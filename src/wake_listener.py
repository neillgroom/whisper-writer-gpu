"""
Always-on wake-word listener.

Keeps one input stream open for as long as wake is enabled and emits `wakeSignal`
once per spoken wake word. It never closes for a recording: Windows shared-mode
capture lets the ResultThread open a second stream on the same mic. The main
thread decides whether a wake is accepted (see wake_gate.WakeGate).

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
    The audio callback only copies frames into a queue; detection runs on this
    thread. The loop never blocks on the device for longer than POLL_S, so
    `stop()` always returns promptly, even when a Bluetooth mic stalls.
    """
    wakeSignal = pyqtSignal(float, float)   # (detected_at monotonic seconds, score)
    errorSignal = pyqtSignal(str)

    def __init__(self, detector, device, open_stream=sd.InputStream):
        super().__init__()
        self.detector = detector
        self.device = device
        self.open_stream = open_stream
        self._running = True
        self._stream = None

    def stop(self):
        """
        Ask the loop to exit and wait for the thread to finish. If the device hangs
        while closing, abort the stream to unblock it. Returns True once the thread
        has finished.
        """
        self._running = False
        if self.wait(int(POLL_S * 1000) * 8):
            return True
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
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
        # Supervised, always-on: open the stream, detect until stop() or a failure,
        # then (unless stopping) log it, back off, and reopen. A single transient host
        # error or a silent stall must not permanently kill wake-word -- an always-on
        # listener recovers. The thread exits only when stop() clears self._running.
        consecutive_failures = 0
        warned = False
        while self._running:
            # Bounded: if detection ever falls behind, the OLDEST queued frame is dropped
            # to make room for the newest, so the most recent audio (where a live wake word
            # is) is kept. Each frame carries its capture time, and that time (not the
            # processing time) is what the main thread compares against re-arm.
            frames = queue.Queue(maxsize=MAX_BACKLOG)

            def callback(indata, frame_count, time_info, status):
                item = (time.monotonic(), indata[:, 0].copy())
                try:
                    frames.put_nowait(item)
                except queue.Full:
                    try:
                        frames.get_nowait()       # drop the oldest
                        frames.put_nowait(item)   # keep the newest
                    except queue.Empty:
                        pass

            stream = None
            try:
                self.detector.reset()  # drop any audio buffered before this (re)open
                with self.open_stream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=FRAME,
                                      device=self.device, extra_settings=extra_settings(self.device),
                                      callback=callback) as stream:
                    self._stream = stream
                    ConfigManager.console_print('Wake listener: microphone stream open.')
                    if warned:
                        ConfigManager.console_print('Wake listener: recovered.')
                        warned = False
                    consecutive_failures = 0
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
                        score = self.detector.process(frame)
                        if score is not None:
                            self.wakeSignal.emit(captured_at, score)
            except Exception as error:  # open failed, or the stream died / stalled mid-run
                # Abort from this thread so a wedged device cannot keep the context
                # manager from returning.
                if stream is not None:
                    try:
                        stream.abort(ignore_errors=True)
                    except Exception:
                        pass
                if not self._running:
                    break
                consecutive_failures += 1
                ConfigManager.console_print(
                    f'Wake listener: stream error ({error}); reopening in {RETRY_BACKOFF_S:.0f}s '
                    f'(attempt {consecutive_failures}).')
                # One tray warning once the trouble is persistent -- not for a blip that self-heals.
                if consecutive_failures == WARN_AFTER_FAILURES:
                    warned = True
                    self.errorSignal.emit(
                        f"Wake word can't reach the microphone ({error}). Still retrying.")
                # Back off, but stay responsive to stop().
                waited = 0.0
                while self._running and waited < RETRY_BACKOFF_S:
                    time.sleep(0.1)
                    waited += 0.1
            finally:
                self._stream = None
        ConfigManager.console_print('Wake listener: stopped.')
