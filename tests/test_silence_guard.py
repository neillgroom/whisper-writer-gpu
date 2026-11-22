"""
c2 silence-guard: blank transcripts type nothing, a whole transcript said twice
is cut back to one copy, and every collapse leaves evidence (the audio and a
JSON line in history.log) in a temp dir, never in the real install.
"""
import json
import os
import shutil
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest import mock

import numpy as np

from _support import REPO, set_config
from utils import ConfigManager
import main
import result_thread
from result_thread import ResultThread
from transcription import collapse_repeat, is_hallucination, post_process_transcription

PROMPT = "Hello, welcome to my lecture. How are you today? We will discuss important topics, commas, and more."
DOUBLED = 'Testing, one, two, three. Testing, one, two, three.'


def cfg(test, value, *keys):
    test.addCleanup(set_config, ConfigManager.get_config_value(*keys), *keys)
    set_config(value, *keys)


def text_config(test):
    cfg(test, PROMPT, 'model_options', 'common', 'initial_prompt')
    cfg(test, False, 'post_processing', 'remove_trailing_period')
    cfg(test, True, 'post_processing', 'add_trailing_space')
    cfg(test, False, 'post_processing', 'remove_capitalization')


class CollapseTests(unittest.TestCase):
    def setUp(self):
        text_config(self)
        self.reports = []

    def pp(self, text):
        return post_process_transcription(text, lambda o, k: self.reports.append((o, k)))

    def test_the_reported_doubled_line_collapses(self):
        self.assertEqual(self.pp(DOUBLED), 'Testing, one, two, three. ')
        self.assertEqual(self.reports, [(DOUBLED, 'Testing, one, two, three.')])

    def test_punctuation_drift_collapses_and_keeps_copy_ones_punctuation(self):
        self.assertEqual(self.pp('Testing, one, two, three. testing one two three'), 'Testing, one, two, three. ')
        self.assertEqual(self.pp("We don't ship Friday, ok? We dont ship friday ok"), "We don't ship Friday, ok? ")

    def test_three_copies_of_a_long_unit_collapse_to_one(self):
        unit = 'Please send the quarterly report to the whole finance team today.'
        self.assertEqual(self.pp(' '.join([unit] * 3)), unit + ' ')

    def test_a_three_word_unit_said_twice_is_typed_whole(self):
        self.assertEqual(self.pp("I don't know. I don't know."), "I don't know. I don't know. ")
        self.assertEqual(self.reports, [])

    def test_dash_and_ellipsis_never_count_as_words(self):
        self.assertEqual(self.pp('Wait - one second. Wait - one second.'), 'Wait - one second. Wait - one second. ')
        self.assertEqual(collapse_repeat('Wait ... one second. Wait ... one second.'),
                         'Wait ... one second. Wait ... one second.')
        self.assertEqual(self.reports, [])

    def test_a_short_phrase_loop_is_still_dropped_not_collapsed(self):
        self.assertEqual(self.pp('I am here. I am here. I am here.'), '')
        self.assertEqual(self.pp('I am here now. ' * 3), '')  # 4-word unit, 3x: the loop guard wins
        self.assertEqual(self.reports, [])

    def test_doubled_silence_phrases_are_dropped(self):
        for text in ['Thank you for watching. Thank you for watching.', 'Thank you. Thank you.', 'Bye. Bye.']:
            self.assertTrue(is_hallucination(text, PROMPT), text)
            self.assertEqual(self.pp(text), '', text)
        self.assertEqual(self.reports, [])

    def test_doubled_initial_prompt_sentences_are_dropped(self):
        for text in ['How are you today? How are you today?',
                     'Hello, welcome to my lecture. Hello welcome to my lecture.']:
            self.assertTrue(is_hallucination(text, PROMPT), text)
            self.assertEqual(self.pp(text), '', text)
        self.assertEqual(self.reports, [])

    def test_a_collapse_that_lands_on_a_silence_phrase_is_dropped(self):
        # Reaches the second guard only if the first misses it; prove the second guard alone works.
        with mock.patch('transcription.is_hallucination', side_effect=[False, True]):
            self.assertEqual(self.pp(DOUBLED), '')
        self.assertEqual(self.reports, [])

    def test_non_repeats_are_untouched(self):
        for text in ['Email Eric from my Gmail with the two website addresses.',
                     'Run the tests. Then run the tests again with coverage.',
                     'one two three four one two three five']:
            self.assertEqual(collapse_repeat(text), text)
            self.assertEqual(self.pp(text), text + ' ')
        self.assertEqual(self.reports, [])

    def test_blank_results_are_empty_not_a_lone_space(self):
        for text in ['', '   ', '\t\n', 'um', 'uh, um.']:
            self.assertEqual(self.pp(text), '', repr(text))
        cfg(self, True, 'post_processing', 'remove_trailing_period')
        self.assertEqual(self.pp('.'), '')

    def test_a_failing_report_callback_never_breaks_dictation(self):
        def boom(o, k):
            raise RuntimeError('disk full')
        self.assertEqual(post_process_transcription(DOUBLED, boom), 'Testing, one, two, three. ')

    def test_no_callback_is_fine(self):
        self.assertEqual(post_process_transcription(DOUBLED), 'Testing, one, two, three. ')


class RunTests(unittest.TestCase):
    """ResultThread.run with a fake recorder and transcriber; every file lands in a temp dir."""

    def setUp(self):
        text_config(self)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.object(result_thread, 'INSTALL_ROOT', self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.audio = (np.arange(-4000, 4000, 3) % 30000).astype(np.int16)
        self.rate = 22050

    def run_thread(self, raw_text):
        thread = ResultThread(source='key', device=None)
        emitted = []
        thread.resultSignal.connect(emitted.append)

        def record():
            thread.sample_rate = self.rate
            return self.audio

        def fake_transcribe(audio, model, on_collapse=None):
            return post_process_transcription(raw_text, on_collapse)

        with mock.patch.object(result_thread, 'com_initialize', lambda: False), \
             mock.patch.object(result_thread, 'com_uninitialize', lambda h: None), \
             mock.patch.object(thread, '_record_audio', record), \
             mock.patch.object(result_thread, 'transcribe', fake_transcribe):
            thread.is_running = True
            thread.run()
        return emitted

    def folder(self):
        return os.path.join(self.tmp, 'doubled-audio')

    def history(self):
        path = os.path.join(self.tmp, 'history.log')
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as f:
            return f.read().split('\n')[:-1]

    def collapsed_lines(self):
        return [json.loads(l) for l in self.history() if l.startswith('{')]

    def test_paths_are_redirected_to_the_temp_dir(self):
        self.assertTrue(result_thread.history_path().startswith(self.tmp))
        self.assertTrue(result_thread.doubled_audio_dir().startswith(self.tmp))

    def test_a_collapse_saves_the_audio_and_logs_one_json_line(self):
        raw = 'Say "hi"\tnow please friend.\nSay "hi" now please friend.'
        self.assertEqual(self.run_thread(raw), ['Say "hi"\tnow please friend. '])
        files = os.listdir(self.folder())
        self.assertEqual(len(files), 1)
        with wave.open(os.path.join(self.folder(), files[0]), 'rb') as wf:
            self.assertEqual((wf.getnchannels(), wf.getsampwidth(), wf.getframerate()), (1, 2, self.rate))
            self.assertEqual(wf.readframes(wf.getnframes()), self.audio.tobytes())
        lines = self.history()
        self.assertEqual(len(lines), 2)  # the COLLAPSED line, then the usual typed line
        entry = json.loads(lines[0])
        self.assertEqual(entry['event'], 'COLLAPSED')
        self.assertEqual(entry['original'], raw)
        self.assertEqual(entry['kept'], 'Say "hi"\tnow please friend.')
        self.assertEqual(entry['audio'], files[0])
        self.assertTrue(lines[1].endswith('\tSay "hi"\tnow please friend. '))

    def test_an_eleventh_save_keeps_the_ten_newest(self):
        os.makedirs(self.folder())
        old = [f'doubled-20000101-0000{i:02d}-000.wav' for i in range(10)]
        for name in old + ['notes.txt']:
            open(os.path.join(self.folder(), name), 'wb').close()
        self.run_thread(DOUBLED)
        files = sorted(os.listdir(self.folder()))
        self.assertIn('notes.txt', files)  # only our own files are pruned
        wavs = [f for f in files if f.endswith('.wav')]
        self.assertEqual(len(wavs), 10)
        self.assertNotIn(old[0], wavs)
        self.assertEqual(wavs[:9], old[1:])
        self.assertEqual(wavs[-1], self.collapsed_lines()[0]['audio'])

    def test_two_saves_in_one_millisecond_do_not_collide(self):
        fixed = mock.Mock()
        fixed.now.return_value = __import__('datetime').datetime(2026, 11, 22, 10, 0, 0, 123000)
        with mock.patch('datetime.datetime', fixed):
            a = result_thread.save_doubled_audio(self.audio, self.rate, self.folder())
            b = result_thread.save_doubled_audio(self.audio, self.rate, self.folder())
        self.assertNotEqual(a, b)
        self.assertEqual(sorted(os.listdir(self.folder())), sorted([a, b]))

    def test_a_wave_failure_still_types_and_logs_a_null_file(self):
        with mock.patch.object(result_thread.wave, 'open', side_effect=OSError('nope')):
            self.assertEqual(self.run_thread(DOUBLED), ['Testing, one, two, three. '])
        self.assertEqual(os.listdir(self.folder()), [])  # no half-written file
        entry = self.collapsed_lines()[0]
        self.assertIsNone(entry['audio'])
        self.assertEqual(entry['kept'], 'Testing, one, two, three.')

    def test_an_unwritable_folder_still_types_and_logs_a_null_file(self):
        open(self.folder(), 'w').close()  # a FILE where the folder should be: makedirs fails
        self.assertEqual(self.run_thread(DOUBLED), ['Testing, one, two, three. '])
        self.assertIsNone(self.collapsed_lines()[0]['audio'])

    def test_a_locked_history_file_still_types(self):
        real_open = open

        def guarded(path, *a, **kw):
            if str(path).endswith('history.log'):
                raise PermissionError('locked')
            return real_open(path, *a, **kw)
        with mock.patch('builtins.open', guarded):
            self.assertEqual(self.run_thread(DOUBLED), ['Testing, one, two, three. '])
        self.assertEqual(len(os.listdir(self.folder())), 1)

    def test_no_collapse_means_no_file_and_no_collapsed_line(self):
        self.assertEqual(self.run_thread('Run the tests again.'), ['Run the tests again. '])
        self.assertFalse(os.path.exists(self.folder()))
        self.assertEqual(self.collapsed_lines(), [])
        self.assertEqual(len(self.history()), 1)

    def test_a_blank_result_is_never_logged(self):
        self.assertEqual(self.run_thread('   '), [''])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'history.log')))

    def test_the_worktree_root_is_untouched(self):
        self.run_thread(DOUBLED)
        self.assertFalse(os.path.exists(os.path.join(REPO, 'doubled-audio')))


class CompletionGateTests(unittest.TestCase):
    """on_transcription_complete: a blank result types and beeps nothing but always re-arms."""

    def make_app(self, source='key'):
        app = SimpleNamespace(typed=[], started=[], key_listener=mock.Mock())
        app.sender = lambda: SimpleNamespace(source=source)
        app.input_simulator = SimpleNamespace(typewrite=lambda text, **kw: app.typed.append(text) or '')
        app.start_result_thread = lambda *a, **kw: app.started.append('key')
        app.type_wake_transcript = lambda text: app.typed.append(('wake', text))
        return app

    def setUp(self):
        cfg(self, True, 'misc', 'noise_on_completion')
        p = mock.patch.object(main, 'AudioPlayer')
        self.player = p.start()
        self.addCleanup(p.stop)

    def complete(self, app, result):
        main.WhisperWriterApp.on_transcription_complete(app, result)

    def test_blank_key_result_types_nothing_but_rearms_the_listener(self):
        cfg(self, 'press_to_toggle', 'recording_options', 'recording_mode')
        app = self.make_app('key')
        for blank in ['', ' ', '\t\n']:
            self.complete(app, blank)
        self.assertEqual(app.typed, [])
        self.player.assert_not_called()
        self.assertEqual(app.key_listener.start.call_count, 3)

    def test_blank_key_result_in_continuous_mode_restarts_recording(self):
        cfg(self, 'continuous', 'recording_options', 'recording_mode')
        app = self.make_app('key')
        self.complete(app, ' ')
        self.assertEqual(app.typed, [])
        self.assertEqual(app.started, ['key'])

    def test_blank_wake_result_still_rearms(self):
        cfg(self, 'continuous', 'recording_options', 'recording_mode')
        app = self.make_app('wake')
        self.complete(app, ' ')
        self.assertEqual(app.typed, [])
        app.key_listener.start.assert_called_once()

    def test_real_text_is_typed_and_beeps(self):
        cfg(self, 'press_to_toggle', 'recording_options', 'recording_mode')
        app = self.make_app('key')
        self.complete(app, 'hello ')
        self.assertEqual(app.typed, ['hello '])
        self.player.assert_called_once()
        app.key_listener.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
