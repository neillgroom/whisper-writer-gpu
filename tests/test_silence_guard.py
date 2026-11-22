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

    def test_separate_punctuation_ending_copy_one_is_kept(self):
        self.assertEqual(collapse_repeat('Is this feature ready ? Is this feature ready ?'), 'Is this feature ready ?')
        self.assertEqual(collapse_repeat('A b c d ... A b c d ...'), 'A b c d ...')
        self.assertEqual(collapse_repeat('A b c d ... ! A b c d'), 'A b c d ... !')
        # Attached punctuation behaves as before.
        self.assertEqual(collapse_repeat('Is this feature ready? Is this feature ready?'), 'Is this feature ready?')
        self.assertEqual(collapse_repeat(DOUBLED), 'Testing, one, two, three.')

    def test_punctuation_before_the_first_or_after_the_last_copy_never_breaks_detection(self):
        self.assertEqual(collapse_repeat('... A b c d A b c d'), '... A b c d')
        self.assertEqual(collapse_repeat('A b c d A b c d ... ?'), 'A b c d')
        self.assertEqual(collapse_repeat('- A b c d . A b c d . A b c d - -'), '- A b c d .')
        self.assertEqual(collapse_repeat('A b c d ... A b c e ...'), 'A b c d ... A b c e ...')  # not a repeat

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
        with mock.patch('transcription.is_hallucination', side_effect=[False, False, True]):
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

    def test_a_filler_masked_doubled_line_collapses(self):
        raw = 'Testing one two three. Um, testing one two three.'
        self.assertEqual(self.pp(raw), 'Testing one two three. ')
        self.assertEqual(self.reports, [(raw, 'Testing one two three.')])

    def test_a_filler_masked_silence_phrase_is_dropped(self):
        for text in ['Thank you, uh.', 'Um, thank you.', 'Thank you. Uh, bye.']:
            self.assertFalse(is_hallucination(text, PROMPT), text)  # the raw text alone passes
            self.assertEqual(self.pp(text), '', text)
        self.assertEqual(self.reports, [])

    def test_a_filler_masked_short_loop_is_dropped(self):
        text = 'I am here. Um, I am here. I am here.'
        self.assertFalse(is_hallucination(text, PROMPT))
        self.assertEqual(self.pp(text), '')
        self.assertEqual(self.reports, [])

    def test_a_collapse_emptied_by_later_cleanup_fires_no_callback(self):
        cfg(self, True, 'post_processing', 'remove_trailing_period')
        with mock.patch('transcription.collapse_repeat', return_value='.'):
            self.assertEqual(self.pp(DOUBLED), '')
        self.assertEqual(self.reports, [])

    def test_kept_is_exactly_the_typed_text_without_the_trailing_space(self):
        cfg(self, True, 'post_processing', 'remove_trailing_period')
        cfg(self, True, 'post_processing', 'remove_capitalization')
        typed = self.pp(DOUBLED)
        self.assertEqual(typed, 'testing, one, two, three ')
        self.assertEqual(self.reports, [(DOUBLED, typed[:-1])])

    def test_any_mix_of_silence_phrases_and_prompt_sentences_is_dropped(self):
        for text in ['Hello, welcome to my lecture. How are you today?',
                     'How are you today? Hello, welcome to my lecture. How are you today?',
                     'Thank you. Bye.', 'Thanks for watching! Thank you. Bye. Thanks.',
                     'Thank you. How are you today?']:
            self.assertTrue(is_hallucination(text, PROMPT), text)
            self.assertEqual(self.pp(text), '', text)

    def test_a_phrase_among_real_words_is_typed(self):
        for text in ['thank you for the update', 'How are you today? I need the report.',
                     'Bye for now, see you Monday.']:
            self.assertFalse(is_hallucination(text, PROMPT), text)
            self.assertEqual(self.pp(text), text + ' ', text)

    def test_a_stutter_join_across_the_copy_boundary_still_collapses(self):
        # Copy 1 ends with the word copy 2 starts with; the stutter cleanup would merge
        # "go go" and break the exact repeat, so the double must be caught before it.
        raw = 'go to the store go go to the store go'
        self.assertEqual(self.pp(raw), 'go to the store go ')
        self.assertEqual(self.reports, [(raw, 'go to the store go')])

    def test_a_filler_and_a_stutter_join_report_one_collapse_with_the_typed_text(self):
        raw = 'Um, go to the store go go to the store go'
        self.assertEqual(self.pp(raw), 'go to the store go ')
        self.assertEqual(self.reports, [(raw, 'go to the store go')])

    def test_wordless_output_is_never_typed(self):
        for text in ['.', '...', '?', '♪', '♪ ♪', 'Uh?', 'Um...', '- -']:
            self.assertEqual(self.pp(text), '', repr(text))
        self.assertEqual(self.reports, [])

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
        for i, name in enumerate(old + ['notes.txt']):
            open(os.path.join(self.folder(), name), 'wb').close()
            os.utime(os.path.join(self.folder(), name), (1_000_000 + i, 1_000_000 + i))
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

    def test_a_clock_step_back_never_prunes_the_new_file(self):
        os.makedirs(self.folder())
        future = [f'doubled-29990101-0000{i:02d}-000-00.wav' for i in range(10)]
        for i, name in enumerate(future):
            path = os.path.join(self.folder(), name)
            open(path, 'wb').close()
            os.utime(path, (32_000_000_000 + i, 32_000_000_000 + i))  # far-future mtimes and names
        new = result_thread.save_doubled_audio(self.audio, self.rate, self.folder())
        files = sorted(os.listdir(self.folder()))
        self.assertIn(new, files)
        self.assertEqual(len(files), 10)
        self.assertNotIn(future[0], files)  # the oldest of the others goes

    def test_twelve_saves_in_one_millisecond_keep_ten_including_the_newest(self):
        fixed = mock.Mock()
        fixed.now.return_value = __import__('datetime').datetime(2026, 11, 22, 10, 0, 0, 123000)
        names = []
        with mock.patch('datetime.datetime', fixed):
            for _ in range(12):
                names.append(result_thread.save_doubled_audio(self.audio, self.rate, self.folder()))
        self.assertEqual(names, sorted(names))  # zero-padded counters sort in save order
        self.assertEqual(names[-1], 'doubled-20261122-100000-123-11.wav')
        files = sorted(os.listdir(self.folder()))
        self.assertEqual(len(files), 10)
        self.assertIn(names[-1], files)

    def test_a_non_ascii_digit_counter_is_ignored(self):
        fixed = mock.Mock()
        fixed.now.return_value = __import__('datetime').datetime(2026, 11, 22, 10, 0, 0, 123000)
        os.makedirs(self.folder())
        odd = 'doubled-20261122-100000-123-²³.wav'  # superscript digits: isdigit() but not int()
        open(os.path.join(self.folder(), odd), 'wb').close()
        with mock.patch('datetime.datetime', fixed):
            name = result_thread.save_doubled_audio(self.audio, self.rate, self.folder())
        self.assertEqual(name, 'doubled-20261122-100000-123-00.wav')

    def test_keep_below_one_is_treated_as_one(self):
        os.makedirs(self.folder())
        for i in range(3):
            open(os.path.join(self.folder(), f'doubled-20000101-000000-000-0{i}.wav'), 'wb').close()
        for keep in (0, -5):
            new = result_thread.save_doubled_audio(self.audio, self.rate, self.folder(), keep=keep)
            self.assertEqual(os.listdir(self.folder()), [new])

    def test_a_blank_result_is_never_logged(self):
        self.assertEqual(self.run_thread('   '), [''])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'history.log')))

    def test_the_worktree_root_is_untouched(self):
        # The checkout may also be a live install with real saves in it: compare, never create or delete.
        def snapshot():
            real = os.path.join(REPO, 'doubled-audio')
            history = os.path.join(REPO, 'history.log')
            files = None
            if os.path.isdir(real):
                files = sorted((f, os.stat(os.path.join(real, f)).st_size, os.stat(os.path.join(real, f)).st_mtime_ns)
                               for f in os.listdir(real))
            hist = (os.stat(history).st_size, os.stat(history).st_mtime_ns) if os.path.exists(history) else None
            return os.path.exists(real), files, hist
        before = snapshot()
        self.run_thread(DOUBLED)
        self.assertEqual(snapshot(), before)


class CompletionGateTests(unittest.TestCase):
    """on_transcription_complete: a blank result types and beeps nothing but always re-arms."""

    def make_app(self, source='key'):
        app = SimpleNamespace(typed=[], started=[], key_listener=mock.Mock(), result_thread=None)
        app.sender = lambda: SimpleNamespace(source=source)
        app.input_simulator = SimpleNamespace(typewrite=lambda text, **kw: app.typed.append(text) or '')
        app.start_result_thread = lambda *a, **kw: app.started.append('key')
        app.type_wake_transcript = lambda text: app.typed.append(('wake', text))
        app._restart_continuous_when_finished = \
            lambda t: main.WhisperWriterApp._restart_continuous_when_finished(app, t)
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
        """Real start_result_thread: the finishing thread still reports running until waited on."""
        cfg(self, 'continuous', 'recording_options', 'recording_mode')
        cfg(self, True, 'misc', 'hide_status_window')

        class FinishingThread:
            source = 'key'

            def __init__(self):
                self.running = True
                self.waits = []

            def isRunning(self):
                return self.running

            def wait(self, ms):
                self.waits.append(ms)
                self.running = False
                return True

        created = []

        def fake_result_thread(model, source='key', device=None):
            t = mock.Mock(source=source)
            created.append(t)
            return t

        app = self.make_app('key')
        finishing = FinishingThread()
        app.sender = lambda: finishing
        app.result_thread = finishing
        app.local_model = None
        app.gate = mock.Mock()
        app.on_transcription_complete = lambda r: None
        app.on_result_finished = lambda: None
        app.start_result_thread = lambda *a, **kw: main.WhisperWriterApp.start_result_thread(app, *a, **kw)
        with mock.patch.object(main, 'ResultThread', fake_result_thread):
            self.complete(app, ' ')
        self.assertEqual(app.typed, [])
        self.assertEqual(finishing.waits, [2000])
        self.assertEqual(len(created), 1)
        self.assertIs(app.result_thread, created[0])
        created[0].start.assert_called_once()

    def test_a_thread_that_will_not_exit_is_reported_and_the_restart_skipped(self):
        cfg(self, 'continuous', 'recording_options', 'recording_mode')
        app = self.make_app('key')
        stuck = mock.Mock(source='key', isRunning=mock.Mock(return_value=True), wait=mock.Mock(return_value=False))
        app.sender = lambda: stuck
        app.result_thread = stuck
        app.start_result_thread = lambda *a, **kw: main.WhisperWriterApp.start_result_thread(app, *a, **kw)
        with mock.patch.object(main, 'ResultThread') as rt, \
             mock.patch.object(main.ConfigManager, 'console_print') as cp:
            self.complete(app, ' ')
        stuck.wait.assert_called_once_with(2000)
        rt.assert_not_called()
        self.assertTrue(any('continuous restart skipped' in c.args[0] for c in cp.call_args_list))

    # --- one-shot retry after a timed-out wait ---

    class FakeSignal:
        def __init__(self, disconnect_works=True):
            self.slots = []
            self.disconnect_works = disconnect_works

        def connect(self, slot):
            self.slots.append(slot)

        def disconnect(self, slot):
            if not self.disconnect_works:
                raise TypeError('disconnect failed')
            self.slots.remove(slot)

        def emit(self):
            for slot in list(self.slots):
                slot()

    class StuckThread:
        source = 'key'

        def __init__(self, signal):
            self.finished = signal
            self.running = True
            self.is_running = True  # ResultThread's own flag; stop() clears it

        def isRunning(self):
            return self.running

        def wait(self, ms):
            return False

        def end(self):
            self.running = False
            self.finished.emit()

    def stuck_app(self, disconnect_works=True):
        from wake_gate import WakeGate
        cfg(self, 'continuous', 'recording_options', 'recording_mode')
        cfg(self, True, 'misc', 'hide_status_window')
        app = self.make_app('key')
        stuck = self.StuckThread(self.FakeSignal(disconnect_works))
        app.sender = lambda: stuck
        app.result_thread = stuck
        app.local_model = None
        app.gate = WakeGate()  # free: on_result_finished has already released it
        app.on_transcription_complete = lambda r: None
        app.on_result_finished = lambda: None
        app._restart_continuous_when_finished = \
            lambda t: main.WhisperWriterApp._restart_continuous_when_finished(app, t)
        app.start_result_thread = lambda *a, **kw: main.WhisperWriterApp.start_result_thread(app, *a, **kw)
        self.created = []
        p = mock.patch.object(main, 'ResultThread',
                              lambda model, source='key', device=None: self.created.append(mock.Mock(source=source))
                              or self.created[-1])
        p.start()
        self.addCleanup(p.stop)
        self.complete(app, ' ')
        self.assertEqual(self.created, [])  # nothing started while the old thread is stuck
        return app, stuck

    def test_a_timed_out_wait_restarts_once_when_the_thread_finally_finishes(self):
        app, stuck = self.stuck_app()
        stuck.end()
        self.assertEqual(len(self.created), 1)
        self.created[0].start.assert_called_once()
        self.assertEqual(stuck.finished.slots, [])  # the handler disconnected itself

    def test_finished_firing_twice_still_restarts_once(self):
        app, stuck = self.stuck_app(disconnect_works=False)  # only the fired flag stops a second run
        stuck.end()
        app.result_thread = stuck  # even if the state looks eligible again
        stuck.finished.emit()
        self.assertEqual(len(self.created), 1)

    def test_no_retry_after_the_mode_changes(self):
        app, stuck = self.stuck_app()
        set_config('hold_to_record', 'recording_options', 'recording_mode')
        stuck.end()
        self.assertEqual(self.created, [])

    def test_no_retry_when_another_thread_is_running(self):
        app, stuck = self.stuck_app()
        app.result_thread = mock.Mock(isRunning=mock.Mock(return_value=True))
        stuck.end()
        self.assertEqual(self.created, [])

    def test_no_retry_after_a_cancel(self):
        app, stuck = self.stuck_app()
        stuck.is_running = False  # what ResultThread.stop() does on the key / close / cleanup
        stuck.end()
        self.assertEqual(self.created, [])

    def test_no_retry_after_cleanup(self):
        app, stuck = self.stuck_app()
        app.closing = True
        stuck.end()
        self.assertEqual(self.created, [])

    def test_no_retry_while_the_gate_is_busy(self):
        app, stuck = self.stuck_app()
        app.gate.begin('wake', object())
        stuck.end()
        self.assertEqual(self.created, [])

    def test_without_the_wait_the_restart_would_be_skipped(self):
        # Guards the test above: the real start_result_thread does refuse while the old thread runs.
        app = self.make_app('key')
        app.result_thread = mock.Mock(isRunning=mock.Mock(return_value=True))
        with mock.patch.object(main, 'ResultThread') as rt:
            main.WhisperWriterApp.start_result_thread(app)
        rt.assert_not_called()

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
