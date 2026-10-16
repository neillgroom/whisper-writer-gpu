"""
Always-on wake-word listener.

Keeps one input stream open for as long as wake is enabled and emits `wakeSignal`
once per spoken wake word. It never closes for a recording: Windows shared-mode
capture lets the ResultThread open a second stream on the same mic. The main
thread decides whether a wake is accepted (see wake_gate.WakeGate).

The model files are fetched once by `python src/wake_setup.py` into models/wake/
and loaded here by path; this module never touches the network.
"""
import os
import time

import numpy as np
import sounddevice as sd
from PyQt5.QtCore import QThread, pyqtSignal

from audio_devices import extra_settings

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, openWakeWord's native frame
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


def load_detector(wake_model, threshold, model_dir=MODEL_DIR):
    from openwakeword.model import Model

    wake_path, melspec_path, embedding_path = model_paths(wake_model, model_dir)
    model = Model(wakeword_models=[wake_path], inference_framework='onnx',
                  melspec_model_path=melspec_path, embedding_model_path=embedding_path)
    return WakeDetector(model, os.path.splitext(os.path.basename(wake_path))[0], threshold)


class WakeListener(QThread):
    wakeSignal = pyqtSignal(float, float)   # (detected_at monotonic seconds, score)
    errorSignal = pyqtSignal(str)

    def __init__(self, detector, device):
        super().__init__()
        self.detector = detector
        self.device = device
        self._running = True

    def stop(self):
        self._running = False
        self.wait(2000)

    def run(self):
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=FRAME,
                                device=self.device, extra_settings=extra_settings(self.device)) as stream:
                while self._running:
                    frame, _ = stream.read(FRAME)
                    score = self.detector.process(np.squeeze(frame, axis=1))
                    if score is not None:
                        self.wakeSignal.emit(time.monotonic(), score)
        except Exception as error:  # the stream died (device unplugged, Bluetooth dropped)
            if self._running:
                self.errorSignal.emit(str(error))
