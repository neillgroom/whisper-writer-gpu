import os
import unittest
import wave

import numpy as np

from _support import FIXTURES
from wake_listener import FRAME, MODEL_DIR, WakeDetector, load_detector, model_paths


class ScriptedModel:
    def __init__(self, scores):
        self.scores = list(scores)
        self.resets = 0

    def predict(self, frame):
        return {'m': self.scores.pop(0)}

    def reset(self):
        self.resets += 1


class RisingEdgeTests(unittest.TestCase):
    def test_one_detection_per_crossing(self):
        model = ScriptedModel([0.1, 0.9, 0.95, 0.9, 0.1, 0.8, 0.2])
        detector = WakeDetector(model, 'm', threshold=0.5)
        fired = [detector.process(None) for _ in range(7)]
        self.assertEqual([i for i, s in enumerate(fired) if s is not None], [1, 5])
        self.assertEqual(model.resets, 2)

    def test_missing_model_files_name_the_setup_command(self):
        with self.assertRaises(FileNotFoundError) as caught:
            model_paths('hey_jarvis_v0.1', model_dir=os.path.join(FIXTURES, 'no-such-dir'))
        self.assertIn('wake_setup.py', str(caught.exception))


def frames_of(path):
    with wave.open(path) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    silence = np.zeros(16000, dtype=np.int16)
    audio = np.concatenate([silence, audio, silence])
    return [audio[i:i + FRAME] for i in range(0, len(audio) - FRAME, FRAME)]


class RealModelTests(unittest.TestCase):
    """Runs the real openWakeWord model on synthetic speech clips. Needs `python src\\wake_setup.py`."""

    @classmethod
    def setUpClass(cls):
        # Missing models fail loudly rather than skip: a skipped detector test proves nothing.
        model_paths('hey_jarvis_v0.1', MODEL_DIR)

    def count(self, name):
        detector = load_detector('hey_jarvis_v0.1', threshold=0.4)
        return sum(detector.process(f) is not None for f in frames_of(os.path.join(FIXTURES, 'wake', name)))

    def test_each_wake_clip_fires_exactly_once(self):
        for name in ('hey_jarvis_david.wav', 'hey_jarvis_run_tests_zira.wav'):
            self.assertEqual(self.count(name), 1, name)

    def test_decoys_never_fire(self):
        for name in ('decoy_jar_travis_zira.wav', 'decoy_okay_claude_david.wav'):
            self.assertEqual(self.count(name), 0, name)


if __name__ == '__main__':
    unittest.main()
