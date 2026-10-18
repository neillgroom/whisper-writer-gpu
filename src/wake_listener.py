"""
Always-on wake-word listener.

Keeps one input stream open for as long as wake is enabled and emits `wakeSignal`
once per spoken wake word. During a wake recording the ResultThread opens a second
stream on the same mic, relying on Windows shared-mode capture; that is verified on
the target hardware. A driver that refuses the second open surfaces as a failed
(empty) wake recording that simply re-arms -- the AirPods/device-switch chunk is
where serialized capture would land if a shared-mode device is ever used.

The listener is supervised: a transient stream error or a silent stall is logged,
backed off, and the mic is reopened -- re-resolving the device BY NAME each time,
since a Bluetooth mic's index changes across reconnects. The thread exits only when
stop() clears self._running.

The model files are fetched once by `python src/wake_setup.py` into models/wake/
and loaded here by path; this module never touches the network.
"""
import ctypes
import os
import queue
import sys
import time

import numpy as np
import sounddevice as sd
from PyQt5.QtCore import QThread, pyqtSignal

from audio_devices import extra_settings
from utils import ConfigManager

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, openWakeWord's native frame
POLL_S = 0.25  # longest the listener loop goes without checking whether it should stop
MAX_BACKLOG = 25  # frames (2 s) of audio waiting for detection before new frames are dropped
RETRY_BACKOFF_S = 1.0    # wait between reopen attempts after a stream failure
STALL_TIMEOUT_S = 3.0    # no frames for this long while the stream claims "active" => reopen
WARN_AFTER_FAILURES = 3  # consecutive failures before a one-time tray warning
SUSTAINED_FRAMES = 10    # ~0.8 s of real frames before a (re)open counts as a genuine recovery
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'wake')
FEATURE_FILES = ('melspectrogram.onnx', 'embedding_model.onnx')


def model_paths(wake_model, model_dir=MODEL_DIR):
    """Return (wake, melspec, embedding) paths, or raise FileNotFoundError naming the setup command."""
    paths = [os.path.join(model_dir, f'{wake_model}.onnx')] + [os.path.join(model_dir, f) for f in FEATURE_FILES]
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError(
            f'Wake model files missing ({", ".join(os.path.basename(p) for p in missing)}). '
            f'Run: python src\\wake_setup.py')
    return paths


class WakeDetector:
    """
    Turns 80 ms frames into one detection per spoken wake word.

    Emits on the rising edge only: once the score crosses the threshold it fires,
    resets the model's buffers, and stays quiet until a frame scores below the
    threshold again.
    """

    def __init__(self, model, model_name, threshold):
        self.model = model
        self.model_name = model_name
        self.threshold = threshold
        self.armed = True

    def process(self, frame):
        """Feed one int16 frame; return the score when this frame is a new detection, else None."""
        score = float(self.model.predict(frame)[self.model_name])
        if score >= self.threshold:
            if self.armed:
                self.armed = False
                self.model.reset()
                return score
            return None
        self.armed = True
        return None

    def reset(self):
        """Clear any buffered audio context; called on every (re)open of the stream."""
        self.model.reset()
        self.armed = True


def load_detector(wake_model, threshold, model_dir=MODEL_DIR):
    import onnxruntime
    from openwakeword.model import Model

    onnxruntime.set_default_logger_severity(3)  # the bundled models trip harmless "unused initializer" warnings

    wake_path, melspec_path, embedding_path = model_paths(wake_model, model_dir)
    model = Model(wakeword_models=[wake_path], inference_framework='onnx',
                  melspec_model_path=melspec_path, embedding_model_path=embedding_path)
    return WakeDetector(model, os.path.splitext(os.path.basename(wake_path))[0], threshold)


class WakeListener(QThread):
    """
    The audio callback only copies frames into a bounded queue; detection runs on
    this thread. The loop never blocks on the device for longer than POLL_S, and the
    stream is aborted (non-blocking) before it is closed on every exit, so neither a
    stalled Bluetooth mic nor app shutdown can pin the thread in PortAudio's blocking
    stop.
    """
    wakeSignal = pyqtSignal(float, float)   # (detected_at monotonic seconds, score)
    errorSignal = pyqtSignal(str)

    def __init__(self, detector, device, open_stream=sd.InputStream):
        """
        `device` is either a fixed input index or a zero-argument callable returning
        the current index (or None when no matching mic is connected). The callable is
        re-invoked on every (re)open, so a reconnected Bluetooth mic at a new index is
        picked up without restarting the app.
        """
        super().__init__()
        self.detector = detector
        self._device_spec = device
        self.open_stream = open_stream
        self._running = True
        self._stream = None

    def _current_device(self):
        spec = self._device_spec
        return spec() if callable(spec) else spec

    def stop(self):
        """
        Ask the loop to exit and wait for the thread to finish. If the device is
        wedged mid-open, abort the stream to unblock it. Returns True once finished.
        """
        self._running = False
        if self.wait(int(POLL_S * 1000) * 8):
            return True
        stream = self._stream
        if stream is not None:
            try:
                stream.abort(ignore_errors=True)
            except Exception:  # already closed or the device is gone; the wait below decides
                pass
        return self.wait(2000)

    def _com_initialize(self):
        """
        A Qt GUI app puts the main thread's COM apartment in STA. A worker thread that
        opens a WASAPI/PortAudio stream must initialize COM on ITS OWN thread, or
        PortAudio's device scan fails with a WDM-KS host error (-9999) on every open.
        Verified on Neill's laptop 2026-10-17: without this the wake stream never opens
        inside the app, though it opens fine from a main-thread script.

        Returns True when this thread now holds a COM reference that run() must release.
        """
        if sys.platform != 'win32':
            return False
        COINIT_MULTITHREADED = 0x0  # a background audio thread needs no message pump
        hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        # S_OK (0) and S_FALSE (1, already initialized here) both require a CoUninitialize;
        # RPC_E_CHANGED_MODE (negative) means someone else set a different apartment -- do not.
        return hr in (0, 1)

    def run(self):
        com_held = self._com_initialize()
        try:
            self._run()
        finally:
            if com_held:
                ctypes.windll.ole32.CoUninitialize()

    def _run(self):
        # Supervised, always-on. The thread exits only when stop() clears self._running.
        consecutive_failures = 0
        warned = False
        while self._running:
            device = self._current_device()
            if device is None:
                # No matching mic right now (the Bluetooth device is disconnected). Treat it
                # like any other failure so the warning still fires if it never comes back.
                consecutive_failures += 1
                ConfigManager.console_print(
                    f'Wake listener: no matching microphone; retrying in {RETRY_BACKOFF_S:.0f}s '
                    f'(attempt {consecutive_failures}).')
                warned = self._warn_if_persistent(
                    consecutive_failures, warned,
                    'Wake word has no microphone to listen on. Still retrying.')
                self._backoff()
                continue

            # Bounded: if detection falls behind, the OLDEST queued frame is dropped to keep the
            # newest (where a live wake word is). Each frame carries its capture time; that time,
            # not the processing time, is what the main thread compares against re-arm.
            frames = queue.Queue(maxsize=MAX_BACKLOG)

            def callback(indata, frame_count, time_info, status):
                item = (time.monotonic(), indata[:, 0].copy())
                try:
                    frames.put_nowait(item)
                except queue.Full:
                    try:
                        frames.get_nowait()       # drop the oldest to make room
                    except queue.Empty:
                        pass
                    try:
                        frames.put_nowait(item)   # keep the newest; the slot was just freed
                    except queue.Full:
                        pass

            stream = None
            frames_seen = 0
            try:
                self.detector.reset()  # drop any audio buffered before this (re)open
                stream = self.open_stream(samplerate=SAMPLE_RATE, channels=1, dtype='int16',
                                          blocksize=FRAME, device=device,
                                          extra_settings=extra_settings(device), callback=callback)
                self._stream = stream
                stream.start()
                ConfigManager.console_print('Wake listener: microphone stream open.')
                last_frame_at = time.monotonic()
                while self._running:
                    try:
                        captured_at, frame = frames.get(timeout=POLL_S)
                    except queue.Empty:
                        # No frames: a dead or silently stalled stream must trigger a reopen.
                        if not stream.active or (time.monotonic() - last_frame_at) > STALL_TIMEOUT_S:
                            raise RuntimeError('the microphone stream stalled')
                        continue
                    last_frame_at = time.monotonic()
                    frames_seen += 1
                    # Clear the failure/warning state only after SUSTAINED real frames -- a bare
                    # open that immediately stalls must still count toward the warning, or a mic
                    # that opens and dies in a loop would never reach the threshold and never warn.
                    if frames_seen == SUSTAINED_FRAMES:
                        consecutive_failures = 0
                        if warned:
                            ConfigManager.console_print('Wake listener: recovered.')
                            warned = False
                    score = self.detector.process(frame)
                    if score is not None:
                        self.wakeSignal.emit(captured_at, score)
            except Exception as error:  # open failed, or the stream died / stalled mid-run
                if not self._running:
                    break
                consecutive_failures += 1
                ConfigManager.console_print(
                    f'Wake listener: stream error ({error}); reopening in {RETRY_BACKOFF_S:.0f}s '
                    f'(attempt {consecutive_failures}).')
                warned = self._warn_if_persistent(
                    consecutive_failures, warned,
                    f"Wake word can't reach the microphone ({error}). Still retrying.")
                self._backoff()
            finally:
                # Abort FIRST (non-blocking) so a wedged device cannot pin the thread in
                # PortAudio's blocking stop, THEN close. Done on every exit -- stop, stall,
                # or error -- so a hung close can never block the retry or app shutdown.
                self._close_stream(stream)
                self._stream = None
        ConfigManager.console_print('Wake listener: stopped.')

    def _warn_if_persistent(self, failures, warned, message):
        """Emit ONE tray warning the moment failures reach the threshold; return the warned flag."""
        if failures == WARN_AFTER_FAILURES:
            self.errorSignal.emit(message)
            return True
        return warned

    def _backoff(self):
        """Sleep RETRY_BACKOFF_S in small steps so stop() stays responsive."""
        waited = 0.0
        while self._running and waited < RETRY_BACKOFF_S:
            time.sleep(0.1)
            waited += 0.1

    @staticmethod
    def _close_stream(stream):
        if stream is None:
            return
        try:
            stream.abort(ignore_errors=True)   # non-blocking; unwedges a dead device
        except Exception:
            pass
        try:
            stream.close(ignore_errors=True)
        except Exception:
            pass
