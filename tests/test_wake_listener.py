import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np
from PyQt5.QtCore import Qt

from _support import qt_app
import wake_listener
from wake_listener import FRAME, WakeListener


class FakeStream:
    """Explicit-lifecycle stand-in for sd.InputStream: start() / abort() / close()."""

    def __init__(self, frames=0, active=True, **kwargs):
        self.kwargs = kwargs
        self.frames = frames
        self.active = active
        self.aborted = False
        self.closed = False
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._pump, daemon=True).start()

    def abort(self, ignore_errors=False):
        self.aborted = True
        self._stop.set()

    def close(self, ignore_errors=False):
        self.closed = True
        self._stop.set()

    def _pump(self):
        frame = np.zeros((FRAME, 1), dtype=np.int16)
        for _ in range(self.frames):
            if self._stop.is_set():
                return
            self.kwargs['callback'](frame, FRAME, None, None)
            time.sleep(0.01)


class AlwaysFires:
    def __init__(self):
        self.calls = 0

    def process(self, frame):
        self.calls += 1
        return 0.9 if self.calls == 2 else None

    def reset(self):
        pass


class FlakyFactory:
    """Raises on the first `fail_times` opens, then returns a FakeStream."""

    def __init__(self, fail_times, frames=0, error=None):
        self.fail_times = fail_times
        self.frames = frames
        self.error = error or OSError('Unanticipated host error [-9999]')
        self.opens = 0

    def __call__(self, **kw):
        self.opens += 1
        if self.opens <= self.fail_times:
            raise self.error
        return FakeStream(frames=self.frames, **kw)


def listener(stream_factory, detector=None, device=3):
    qt_app()
    wake = WakeListener(detector or AlwaysFires(), device=device, open_stream=stream_factory)
    wakes, errors = [], []
    wake.wakeSignal.connect(lambda at, score: wakes.append(score), Qt.DirectConnection)
    wake.errorSignal.connect(errors.append, Qt.DirectConnection)
    return wake, wakes, errors


class WakeListenerTests(unittest.TestCase):
    def test_frames_reach_the_detector_and_a_detection_is_emitted(self):
        wake, wakes, errors = listener(lambda **kw: FakeStream(frames=5, **kw))
        wake.start()
        time.sleep(0.3)
        self.assertTrue(wake.stop())
        self.assertEqual(wakes, [0.9])
        self.assertEqual(errors, [])

    def test_stop_returns_promptly_when_no_frames_arrive(self):
        wake, _, errors = listener(lambda **kw: FakeStream(frames=0, **kw))
        wake.start()
        time.sleep(0.1)
        started = time.monotonic()
        self.assertTrue(wake.stop())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(errors, [])

    def test_a_transient_open_failure_is_retried_and_then_works(self):
        factory = FlakyFactory(fail_times=1, frames=5)
        with mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02):
            wake, wakes, errors = listener(factory)
            wake.start()
            time.sleep(0.4)
            self.assertTrue(wake.stop())
        self.assertEqual(factory.opens, 2)     # failed once, then reopened
        self.assertEqual(wakes, [0.9])         # detection works after recovery
        self.assertEqual(errors, [])           # one failure < WARN_AFTER_FAILURES: no toast

    def test_persistent_open_failure_warns_once_but_keeps_retrying(self):
        factory = FlakyFactory(fail_times=10_000)  # never succeeds
        with mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02):
            wake, _, errors = listener(factory)
            wake.start()
            time.sleep(0.4)
            running_mid = wake.isRunning()
            self.assertTrue(wake.stop())
        self.assertTrue(running_mid)                 # did NOT die on the failures
        self.assertGreaterEqual(factory.opens, 3)    # kept retrying
        self.assertEqual(len(errors), 1)             # exactly one tray warning, at the threshold

    def test_a_stream_that_opens_then_always_stalls_still_warns(self):
        # Regression: a successful OPEN must not reset the failure counter, or an open-then-stall
        # loop would never reach the warning threshold and the user would never be told wake is dead.
        opens = {'n': 0}

        def factory(**kw):
            opens['n'] += 1
            return FakeStream(frames=0, active=False, **kw)  # opens, delivers nothing, "dies"
        with mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02), \
             mock.patch.object(wake_listener, 'POLL_S', 0.05):
            wake, _, errors = listener(factory)
            wake.start()
            time.sleep(0.6)
            running_mid = wake.isRunning()
            self.assertTrue(wake.stop())
        self.assertTrue(running_mid)
        self.assertGreaterEqual(opens['n'], 3)       # reopened after each dead stream
        self.assertEqual(len(errors), 1)             # and the warning finally fired

    def test_a_stalled_but_active_stream_is_reopened(self):
        opens = {'n': 0}

        def factory(**kw):
            opens['n'] += 1
            return FakeStream(frames=0, active=True, **kw)
        with mock.patch.object(wake_listener, 'STALL_TIMEOUT_S', 0.1), \
             mock.patch.object(wake_listener, 'POLL_S', 0.02), \
             mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02):
            wake, _, _ = listener(factory)
            wake.start()
            time.sleep(0.4)
            self.assertTrue(wake.stop())
        self.assertGreaterEqual(opens['n'], 2)       # the stall forced a reopen

    def test_the_device_is_re_resolved_on_every_open(self):
        # A Bluetooth mic's index changes across reconnects, so each reopen must re-call the
        # resolver, not reuse a stale index.
        indices = iter([3, 4, 5])
        opened = []

        def resolver():
            return next(indices, 5)

        def factory(**kw):
            opened.append(kw['device'])
            return FakeStream(frames=0, active=False, **kw)  # stall immediately -> reopen
        with mock.patch.object(wake_listener, 'POLL_S', 0.05), \
             mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02):
            wake, _, _ = listener(factory, device=resolver)
            wake.start()
            time.sleep(0.5)
            self.assertTrue(wake.stop())
        self.assertEqual(opened[:3], [3, 4, 5])

    def test_a_resolver_returning_none_retries_and_warns_without_opening(self):
        opened = {'n': 0}

        def factory(**kw):
            opened['n'] += 1
            return FakeStream(frames=0, **kw)
        with mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02):
            wake, _, errors = listener(factory, device=lambda: None)
            wake.start()
            time.sleep(0.3)
            running_mid = wake.isRunning()
            self.assertTrue(wake.stop())
        self.assertTrue(running_mid)
        self.assertEqual(opened['n'], 0)             # never tried to open a stream
        self.assertEqual(len(errors), 1)             # warned that there is no mic

    def test_a_resolver_that_raises_is_treated_as_no_mic_not_a_crash(self):
        opened = {'n': 0}

        def factory(**kw):
            opened['n'] += 1
            return FakeStream(frames=0, **kw)

        def boom():
            raise OSError('audio backend unhappy')
        with mock.patch.object(wake_listener, 'RETRY_BACKOFF_S', 0.02):
            wake, _, errors = listener(factory, device=boom)
            wake.start()
            time.sleep(0.3)
            running_mid = wake.isRunning()
            self.assertTrue(wake.stop())
        self.assertTrue(running_mid)         # the raise did NOT kill the supervised thread
        self.assertEqual(opened['n'], 0)     # never tried to open a stream
        self.assertEqual(len(errors), 1)     # warned once it was persistent

    @unittest.skipUnless(sys.platform == 'win32', 'COM init is Windows-only')
    def test_com_is_initialized_on_the_worker_thread_and_released(self):
        fake_ole = mock.Mock()
        fake_ole.CoInitializeEx.return_value = 0
        fake_ctypes = mock.Mock()
        fake_ctypes.windll.ole32 = fake_ole
        with mock.patch.object(wake_listener, 'ctypes', fake_ctypes):
            wake, _, _ = listener(lambda **kw: FakeStream(frames=0, **kw))
            wake.start()
            time.sleep(0.1)
            self.assertTrue(wake.stop())
        self.assertTrue(fake_ole.CoInitializeEx.called)
        self.assertTrue(fake_ole.CoUninitialize.called)

    def test_detection_carries_the_capture_time_not_the_processing_time(self):
        class Slow:
            calls = 0

            def process(self, frame):
                Slow.calls += 1
                time.sleep(0.15)  # detection falling behind real time
                return 0.9 if Slow.calls == 4 else None

            def reset(self):
                pass

        wake, _, _ = listener(lambda **kw: FakeStream(frames=40, **kw), detector=Slow())
        stamps = []
        wake.wakeSignal.connect(lambda at, score: stamps.append((at, time.monotonic())), Qt.DirectConnection)
        wake.start()
        time.sleep(0.9)
        self.assertTrue(wake.stop())
        self.assertEqual(len(stamps), 1)
        captured_at, emitted_at = stamps[0]
        self.assertGreater(emitted_at - captured_at, 0.3)

    def test_a_hung_close_is_unblocked_by_the_abort_that_runs_first(self):
        # close() blocks until abort() is called (a wedged device). The listener aborts BEFORE
        # it closes, so the thread is never pinned in the blocking close.
        class HangsUntilAborted(FakeStream):
            def __init__(self, **kw):
                super().__init__(frames=0, **kw)
                self._abort_called = threading.Event()

            def abort(self, ignore_errors=False):
                self.aborted = True
                self._abort_called.set()
                self._stop.set()

            def close(self, ignore_errors=False):
                self._abort_called.wait(10)  # would hang forever if abort had not run first
                self.closed = True

        streams = []

        def factory(**kw):
            streams.append(HangsUntilAborted(**kw))
            return streams[-1]

        wake, _, _ = listener(factory)
        wake.start()
        time.sleep(0.1)
        started = time.monotonic()
        self.assertTrue(wake.stop())
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertTrue(streams[0].aborted)

    def test_stream_opens_the_resolved_device_with_auto_convert(self):
        opened = {}

        def capture(**kw):
            opened.update(kw)
            return FakeStream(frames=0, **kw)
        wake, _, _ = listener(capture)
        wake.start()
        time.sleep(0.1)
        wake.stop()
        self.assertEqual(opened['device'], 3)
        self.assertIsNotNone(opened['extra_settings'])


if __name__ == '__main__':
    unittest.main()
