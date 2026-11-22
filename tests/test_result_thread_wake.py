import threading
import time
import unittest
from unittest import mock

import numpy as np
import sounddevice as sd

from _support import set_config
from utils import ConfigManager
import result_thread
from result_thread import ResultThread


class FakeStream:
    """Stands in for sd.InputStream. `feed` controls whether the callback ever runs."""
    instances = []

    def __init__(self, feed=True, **kwargs):
        self.kwargs = kwargs
        self.feed = feed
        self.aborted = False
        self._stop = threading.Event()
        FakeStream.instances.append(self)

    def start(self):
        if self.feed:
            threading.Thread(target=self._pump, daemon=True).start()

    def close(self, ignore_errors=False):
        self._stop.set()

    def abort(self, ignore_errors=False):
        self.aborted = True
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


# Fake host-API layout so extra_settings never touches the real Windows device list:
# index 5 is a WASAPI input, index 6 an MME input.
HOSTAPIS = [{'name': 'MME'}, {'name': 'Windows WASAPI'}]
DEVICES = [{'name': f'dev{i}', 'hostapi': 1, 'max_input_channels': 1} for i in range(6)] + \
          [{'name': 'dev6', 'hostapi': 0, 'max_input_channels': 1}]


def cfg(test, value, *keys):
    """set_config that restores the previous value when the test ends."""
    test.addCleanup(set_config, ConfigManager.get_config_value(*keys), *keys)
    set_config(value, *keys)


def fake_host_apis():
    """Patch the PortAudio device/host-API lists extra_settings reads."""
    return mock.patch.multiple(sd, query_devices=mock.Mock(return_value=DEVICES),
                               query_hostapis=mock.Mock(return_value=HOSTAPIS))


def run_record(thread, feed=True, speech=False):
    FakeStream.instances.clear()
    with mock.patch.object(result_thread.sd, 'InputStream', lambda **kw: FakeStream(feed=feed, **kw)), \
         fake_host_apis(), \
         mock.patch.object(result_thread.webrtcvad, 'Vad', lambda aggressiveness: FakeVad(speech)):
        thread.is_running = True
        thread.is_recording = True
        started = time.monotonic()
        audio = thread._record_audio()
        return audio, time.monotonic() - started, FakeStream.instances[-1]


class StopAbortTests(unittest.TestCase):
    """ResultThread.stop() must return even if the mic close hangs (dropped Bluetooth)."""

    def setUp(self):
        cfg(self, 'press_to_toggle', 'recording_options', 'recording_mode')
        cfg(self, None, 'recording_options', 'sound_device')

    def test_a_hung_close_is_unblocked_by_the_abort_that_runs_first(self):
        released = threading.Event()
        aborted = {'called': False}

        class HangsOnClose:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                pass  # no frames

            def abort(self, ignore_errors=False):
                aborted['called'] = True
                released.set()

            def close(self, ignore_errors=False):
                released.wait(10)  # would hang forever if abort had not run first in the finally

        thread = ResultThread(source='key', device=None)
        with mock.patch.object(result_thread.sd, 'InputStream', lambda **kw: HangsOnClose(**kw)), \
             mock.patch.object(result_thread.webrtcvad, 'Vad', lambda a: FakeVad(False)):
            thread.start()
            time.sleep(0.3)
            began = time.monotonic()
            thread.stop()               # would block forever without the abort path
            self.assertLess(time.monotonic() - began, 6.0)
            self.assertTrue(aborted['called'])
            self.assertTrue(thread.isFinished())


class WakeRecordingTests(unittest.TestCase):
    def setUp(self):
        cfg(self, 300, 'wake_options', 'no_speech_timeout_ms')
        cfg(self, 1, 'wake_options', 'max_recording_s')
        cfg(self, 'continuous', 'recording_options', 'recording_mode')
        cfg(self, None, 'recording_options', 'sound_device')

    def test_silence_ends_at_the_no_speech_deadline_with_nothing(self):
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertIsNone(audio)
        self.assertLess(elapsed, 0.9)

    def test_a_wake_deadline_aborts_the_stream_before_leaving(self):
        # Abort before the `with` closes, so a dead mic's blocking stop can't pin the thread
        # (which would keep `finished` from firing and wedge WakeGate / the Pause key).
        _, _, stream = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertTrue(stream.aborted)

    def test_constant_speech_ends_at_the_hard_cap(self):
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), speech=True)
        self.assertIsNotNone(audio)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertLess(elapsed, 1.6)

    def test_stalled_callback_still_ends_by_the_deadline(self):
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), feed=False)
        self.assertIsNone(audio)
        self.assertLess(elapsed, 0.9)

    def test_hard_cap_without_any_speech_types_nothing(self):
        cfg(self, 5000, 'wake_options', 'no_speech_timeout_ms')
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertIsNone(audio)
        self.assertLess(elapsed, 1.6)

    def test_wake_word_tail_is_neither_recorded_nor_counted_as_speech(self):
        # Constant "speech" for 1 s: the first 0.4 s is the guard and is dropped.
        audio, _, _ = run_record(ResultThread(source='wake', device=5), speech=True)
        self.assertLess(len(audio), int(0.75 * 16000))

    def test_deadlines_start_after_the_mic_opens(self):
        FakeStream.instances.clear()

        def slow_open(**kw):
            time.sleep(0.6)  # a Bluetooth input taking its time to open
            return FakeStream(**kw)

        thread = ResultThread(source='wake', device=5)
        thread.is_running = thread.is_recording = True
        with mock.patch.object(result_thread.sd, 'InputStream', slow_open), \
             fake_host_apis(), \
             mock.patch.object(result_thread.webrtcvad, 'Vad', lambda a: FakeVad(False)):
            started = time.monotonic()
            self.assertIsNone(thread._record_audio())
        self.assertGreaterEqual(time.monotonic() - started, 0.6 + 0.3)

    def test_configured_zero_is_kept_not_replaced_by_the_default(self):
        cfg(self, 0, 'wake_options', 'no_speech_timeout_ms')
        audio, elapsed, _ = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertIsNone(audio)
        self.assertLess(elapsed, 0.5)  # the 4000 ms default would take 4 s

    def test_wake_uses_its_device_with_wasapi_auto_convert(self):
        _, _, stream = run_record(ResultThread(source='wake', device=5), speech=False)
        self.assertEqual(stream.kwargs['device'], 5)
        self.assertIsInstance(stream.kwargs['extra_settings'], sd.WasapiSettings)

    def test_key_recording_opens_the_configured_device_exactly_as_before(self):
        cfg(self, 'press_to_toggle', 'recording_options', 'recording_mode')
        thread = ResultThread(source='key', device=5)
        threading.Timer(0.2, thread.stop_recording).start()
        _, _, stream = run_record(thread)
        self.assertIsNone(stream.kwargs['device'])
        self.assertIsNone(stream.kwargs['extra_settings'])

    def run_key_on_device(self, index):
        cfg(self, 'press_to_toggle', 'recording_options', 'recording_mode')
        cfg(self, index, 'recording_options', 'sound_device')
        thread = ResultThread(source='key', device=None)
        threading.Timer(0.2, thread.stop_recording).start()
        _, _, stream = run_record(thread)
        return stream

    def test_key_recording_on_a_configured_wasapi_device_gets_auto_convert(self):
        stream = self.run_key_on_device(5)
        self.assertEqual(stream.kwargs['device'], 5)
        self.assertIsInstance(stream.kwargs['extra_settings'], sd.WasapiSettings)

    def test_key_recording_on_a_configured_non_wasapi_device_gets_no_wasapi_settings(self):
        stream = self.run_key_on_device(6)
        self.assertEqual(stream.kwargs['device'], 6)
        self.assertIsNone(stream.kwargs['extra_settings'])


class ComInitTests(unittest.TestCase):
    """run() holds a COM reference on its own thread for the whole body, on every exit path."""

    def run_with(self, body, held=True):
        thread = ResultThread(source='key', device=None)
        order = []
        with mock.patch.object(result_thread, 'com_initialize', lambda: order.append('init') or held), \
             mock.patch.object(result_thread, 'com_uninitialize', lambda h: order.append(('uninit', h))), \
             mock.patch.object(thread, '_record_audio', lambda: body(order)), \
             mock.patch.object(result_thread, 'transcribe', lambda audio, model: 'hello'), \
             mock.patch('builtins.open', mock.mock_open()):
            thread.is_running = True
            thread.run()
        return order

    def test_init_before_the_stream_opens_and_uninit_after_a_result(self):
        order = self.run_with(lambda o: o.append('open') or b'audio')
        self.assertEqual(order, ['init', 'open', ('uninit', True)])

    def test_uninit_after_a_too_short_recording(self):
        order = self.run_with(lambda o: o.append('open') or None)
        self.assertEqual(order, ['init', 'open', ('uninit', True)])

    def test_uninit_after_an_exception(self):
        def boom(o):
            o.append('open')
            raise OSError('PortAudio -9999')
        order = self.run_with(boom)
        self.assertEqual(order, ['init', 'open', ('uninit', True)])

    def test_a_failed_init_is_passed_through_so_nothing_is_released(self):
        order = self.run_with(lambda o: None, held=False)
        self.assertEqual(order, ['init', ('uninit', False)])


if __name__ == '__main__':
    unittest.main()
