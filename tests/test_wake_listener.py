import threading
import time
import unittest

import numpy as np
from PyQt5.QtCore import Qt

from _support import qt_app
from wake_listener import FRAME, WakeListener


class FakeStream:
    """Callback-style stand-in for sd.InputStream."""

    def __init__(self, frames=0, active=True, **kwargs):
        self.kwargs = kwargs
        self.frames = frames
        self.active = active
        self._stop = threading.Event()

    def __enter__(self):
        threading.Thread(target=self._pump, daemon=True).start()
        return self

    def __exit__(self, *exc):
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


def listener(stream_factory, detector=None):
    qt_app()
    wake = WakeListener(detector or AlwaysFires(), device=3, open_stream=stream_factory)
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

    def test_stalled_device_still_stops_promptly(self):
        # No frames ever arrive (Bluetooth stall). stop() must not hang on the device.
        wake, _, errors = listener(lambda **kw: FakeStream(frames=0, **kw))
        wake.start()
        time.sleep(0.1)
        started = time.monotonic()
        self.assertTrue(wake.stop())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(errors, [])

    def test_dead_stream_reports_an_error_and_the_thread_ends(self):
        wake, _, errors = listener(lambda **kw: FakeStream(frames=0, active=False, **kw))
        wake.start()
        self.assertTrue(wake.wait(2000))
        self.assertEqual(len(errors), 1)

    def test_open_failure_reports_an_error(self):
        def refuse(**kw):
            raise OSError('device unavailable')
        wake, _, errors = listener(refuse)
        wake.start()
        self.assertTrue(wake.wait(2000))
        self.assertEqual(errors, ['device unavailable'])

    def test_detection_carries_the_capture_time_not_the_processing_time(self):
        class Slow:
            calls = 0

            def process(self, frame):
                Slow.calls += 1
                time.sleep(0.15)  # detection falling behind real time
                return 0.9 if Slow.calls == 4 else None

        wake, _, _ = listener(lambda **kw: FakeStream(frames=40, **kw), detector=Slow())
        stamps = []
        wake.wakeSignal.connect(lambda at, score: stamps.append((at, time.monotonic())), Qt.DirectConnection)
        wake.start()
        time.sleep(0.9)
        self.assertTrue(wake.stop())
        self.assertEqual(len(stamps), 1)
        captured_at, emitted_at = stamps[0]
        self.assertGreater(emitted_at - captured_at, 0.3)

    def test_stop_aborts_a_stream_that_hangs_while_closing(self):
        class HangsOnClose(FakeStream):
            def __init__(self, **kw):
                super().__init__(frames=0, **kw)
                self.released = threading.Event()
                self.aborted = False

            def abort(self):
                self.aborted = True
                self.released.set()

            def __exit__(self, *exc):
                self.released.wait(10)

        streams = []

        def factory(**kw):
            streams.append(HangsOnClose(**kw))
            return streams[-1]

        wake, _, _ = listener(factory)
        wake.start()
        time.sleep(0.1)
        self.assertTrue(wake.stop())
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
