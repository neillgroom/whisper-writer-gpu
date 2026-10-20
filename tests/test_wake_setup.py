import os
import tempfile
import unittest

from _support import SRC  # noqa: F401  puts src/ on sys.path
import wake_setup


class RepairIncompletePairsTests(unittest.TestCase):
    """
    The one-time setup wedges when openWakeWord downloads a model's .tflite but
    not its paired .onnx: the retry sees the .tflite and skips both. The repair
    deletes the surviving half so the next download completes the pair.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.names = wake_setup.required_onnx_names()

    def tearDown(self):
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def _write(self, name, content=b'data'):
        with open(os.path.join(self.dir, name), 'wb') as f:
            f.write(content)

    def _exists(self, name):
        return os.path.isfile(os.path.join(self.dir, name))

    def test_lone_tflite_is_removed_so_the_retry_refetches(self):
        self._write('melspectrogram.tflite')  # the exact wedge CodeRabbit flagged
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names),
                         ['melspectrogram.tflite'])
        self.assertFalse(self._exists('melspectrogram.tflite'))

    def test_a_complete_pair_is_left_alone(self):
        self._write('melspectrogram.tflite')
        self._write('melspectrogram.onnx')
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names), [])
        self.assertTrue(self._exists('melspectrogram.tflite'))
        self.assertTrue(self._exists('melspectrogram.onnx'))

    def test_lone_onnx_is_also_removed(self):
        self._write('hey_jarvis_v0.1.onnx')
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names),
                         ['hey_jarvis_v0.1.onnx'])
        self.assertFalse(self._exists('hey_jarvis_v0.1.onnx'))

    def test_a_zero_byte_half_takes_the_whole_incomplete_pair(self):
        # an interrupted write leaves a 0-byte file; the pair is incomplete, so BOTH existing
        # files go (the 0-byte one too -- openWakeWord would still skip on its mere existence).
        self._write('embedding_model.onnx', b'')
        self._write('embedding_model.tflite', b'data')
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names),
                         ['embedding_model.onnx', 'embedding_model.tflite'])
        self.assertFalse(self._exists('embedding_model.onnx'))
        self.assertFalse(self._exists('embedding_model.tflite'))

    def test_a_zero_byte_tflite_with_no_onnx_is_removed(self):
        # the exact interrupted-download case: a 0-byte .tflite, no .onnx. A size-only check
        # would see "both absent" and remove nothing, but openWakeWord skips on the .tflite's
        # existence, so it must be removed for the retry to re-fetch the pair.
        self._write('melspectrogram.tflite', b'')
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names),
                         ['melspectrogram.tflite'])
        self.assertFalse(self._exists('melspectrogram.tflite'))

    def test_unrelated_vad_tflite_is_not_touched(self):
        self._write('silero_vad.tflite')  # VAD model has no .onnx sibling by design
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names), [])
        self.assertTrue(self._exists('silero_vad.tflite'))

    def test_nothing_to_do_on_an_empty_dir(self):
        self.assertEqual(wake_setup.repair_incomplete_pairs(self.dir, self.names), [])


if __name__ == '__main__':
    unittest.main()
