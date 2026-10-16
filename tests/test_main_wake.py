"""
WhisperWriterApp's wake handlers, run unbound against a stand-in `self` so no
Qt app, model, or keyboard hook is started.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from _support import set_config
import main
from main import WhisperWriterApp
from wake_gate import WakeGate


class FakeApp:
    on_wake = WhisperWriterApp.on_wake
    on_activation = WhisperWriterApp.on_activation
    on_deactivation = WhisperWriterApp.on_deactivation
    on_transcription_complete = WhisperWriterApp.on_transcription_complete
    type_wake_transcript = WhisperWriterApp.type_wake_transcript

    def __init__(self, sender_source='key'):
        self.gate = WakeGate()
        self.wake_allowed = {'windowsterminal.exe'}
        self.wake_device = 7
        self.result_thread = None
        self.started = []
        self.notices = []
        self.typed = []
        self.remainder = ''
        self._sender = SimpleNamespace(source=sender_source)
        self.input_simulator = SimpleNamespace(typewrite=self._typewrite)
        self.key_listener = mock.Mock()

    def _typewrite(self, text, should_continue=None):
        self.typed.append((text, should_continue))
        return self.remainder

    def sender(self):
        return self._sender

    def start_result_thread(self, source='key', device=None, hwnd=None):
        self.started.append((source, device, hwnd))

    def stop_result_thread(self):
        pass

    def notify(self, title, message, warning=False):
        self.notices.append((title, message))


class OnWakeTests(unittest.TestCase):
    def test_terminal_in_front_starts_a_wake_recording_on_the_wake_mic(self):
        app = FakeApp()
        with mock.patch.object(main, 'foreground_window', return_value=55), \
             mock.patch.object(main, 'is_allowed', return_value=True):
            app.on_wake(10.0, 0.9)
        self.assertEqual(app.started, [('wake', 7, 55)])

    def test_other_window_does_nothing(self):
        app = FakeApp()
        with mock.patch.object(main, 'foreground_window', return_value=55), \
             mock.patch.object(main, 'is_allowed', return_value=False):
            app.on_wake(10.0, 0.9)
        self.assertEqual(app.started, [])

    def test_wake_during_a_recording_is_dropped_before_any_window_check(self):
        app = FakeApp()
        app.gate.begin('key', object())
        with mock.patch.object(main, 'foreground_window', side_effect=AssertionError('checked')):
            app.on_wake(10.0, 0.9)
        self.assertEqual(app.started, [])


class KeyIsolationTests(unittest.TestCase):
    def test_pause_press_and_release_ignored_during_a_wake_recording(self):
        app = FakeApp()
        thread = mock.Mock()
        thread.isRunning.return_value = True
        app.result_thread = thread
        app.gate.begin('wake', thread, hwnd=1)
        set_config('hold_to_record', 'recording_options', 'recording_mode')
        app.on_activation()
        app.on_deactivation()
        thread.stop_recording.assert_not_called()
        self.assertEqual(app.started, [])


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        set_config(False, 'misc', 'noise_on_completion')
        set_config('continuous', 'recording_options', 'recording_mode')

    def test_wake_transcript_is_typed_with_a_focus_check_and_never_rearms(self):
        app = FakeApp(sender_source='wake')
        app.gate.begin('wake', object(), hwnd=55)
        app.on_transcription_complete('run the tests ')
        self.assertEqual(len(app.typed), 1)
        self.assertIsNotNone(app.typed[0][1])
        self.assertEqual(app.started, [])  # continuous mode does not restart after a wake

    def test_key_transcript_in_continuous_mode_still_rearms(self):
        app = FakeApp(sender_source='key')
        app.on_transcription_complete('hello ')
        self.assertEqual(app.typed, [('hello ', None)])
        self.assertEqual(app.started, [('key', None, None)])

    def test_blank_wake_transcript_types_nothing(self):
        app = FakeApp(sender_source='wake')
        app.on_transcription_complete(' ')
        self.assertEqual(app.typed, [])
        self.assertEqual(app.notices, [])

    def test_focus_change_mid_typing_reports_the_untyped_rest(self):
        app = FakeApp(sender_source='wake')
        app.gate.begin('wake', object(), hwnd=55)
        app.remainder = 'the tests '
        app.on_transcription_complete('run the tests ')
        self.assertEqual(len(app.notices), 1)
        self.assertIn('the tests', app.notices[0][1])

    def test_focus_check_compares_against_the_wake_window(self):
        app = FakeApp(sender_source='wake')
        app.gate.begin('wake', object(), hwnd=55)
        app.on_transcription_complete('x')
        check = app.typed[0][1]
        with mock.patch.object(main, 'foreground_window', return_value=55):
            self.assertTrue(check())
        with mock.patch.object(main, 'foreground_window', return_value=99):
            self.assertFalse(check())


if __name__ == '__main__':
    unittest.main()
