import inspect
import unittest

import numpy
import sounddevice


class PinnedStackTests(unittest.TestCase):
    def test_numpy_stays_below_2(self):
        # ctranslate2 4.7.1 + faster-whisper in this venv are built against numpy 1.x.
        self.assertLess(int(numpy.__version__.split('.')[0]), 2)

    def test_openwakeword_imports(self):
        import openwakeword  # noqa: F401
        from openwakeword.model import Model  # noqa: F401

    def test_sounddevice_supports_wasapi_auto_convert(self):
        # 0.4.6 lacked it; the wake path depends on it to open Bluetooth mics at 16 kHz.
        self.assertIn('auto_convert', inspect.signature(sounddevice.WasapiSettings).parameters)


if __name__ == '__main__':
    unittest.main()
