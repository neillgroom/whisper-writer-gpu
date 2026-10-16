import threading
import time
import unittest
from unittest import mock

import numpy as np
import sounddevice as sd

from _support import set_config
import result_thread
from result_thread import ResultThread


class FakeStream:
    """Stands in for sd.InputStream. `feed` controls whether the callback ever runs."""
    instances = []

    def __init__(self, feed=True, **kwargs):
        self.kwargs = kwargs
        self.feed = feed
        self._stop = threading.Event()
        FakeStream.instances.append(self)

    def __enter__(self):
        if self.feed:
            threading.Thread(target=self._pump, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()

    def _pump(self):
        frame = np.ones((self.kwargs['blocksize'], 1), dtype=np.int16)
        while not self._stop.is_set():
            self.kwargs['callback'](frame, len(frame), None, None)
            time.sleep(0.03)


class FakeVad:
    def __init__(self, speech):
        self.speech = speech

    def is_speech(self, frame, rate):
        return self.speech


def run_record(thread, feed=True, speech=False):
    FakeStream.instances.clear()
    with mock.patch.object(result_thread.sd, 'InputStream', lambda **kw: FakeStream(feed=feed, **kw)), \
         mock.patch.object(result_thread.webrtcvad, 'Vad', lambda aggressiveness: FakeVad(speech)):
        thread.is_running = True
        thread.is_recording = True
        started = time.monotonic()
        audio = thread._record_audio()
        return audio, time.monotonic() - started, FakeStream.instances[-1]


class WakeRecordingTests(unittest.TestCase):
    def setUp(self):
        set_config(300, 'wake_options', 'no_speech_timeout_ms')
        set_config(1, 'wake_options', 'max_recording_s')
        set_config('continuous', 'recording_options', 'recording_mode')
        set_config(None, 'recording_options', 'sound_device')

    def test_silence_ends_at_the_no_speech_deadline_with_nothing(self):
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertIsNone(audio)
        self.assertLess(elapsed, 0.9)

    def test_constant_speech_ends_at_the_hard_cap(self):
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), speech=True)
        self.assertIsNotNone(audio)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertLess(elapsed, 1.6)

    def test_stalled_callback_still_ends_by_the_deadline(self):
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), feed=False)
        self.assertIsNone(audio)
        self.assertLess(elapsed, 0.9)

    def test_wake_uses_its_device_with_wasapi_auto_convert(self):
        _, _, stream = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertEqual(stream.kwargs['device'], 5)
        self.assertIsInstance(stream.kwargs['extra_settings'], sd.WasapiSettings)

    def test_key_recording_opens_the_configured_device_exactly_as_before(self):
        set_config('press_to_toggle', 'recording_options', 'recording_mode')
        thread = ResultThread(source='key', device=5)
        threading.Timer(0.2, thread.stop_recording).start()
        _, _, stream = run_record(thread)
        self.assertIsNone(stream.kwargs['device'])
        self.assertIsNone(stream.kwargs['extra_settings'])


if __name__ == '__main__':
    unittest.main()
