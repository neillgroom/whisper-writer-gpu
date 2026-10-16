import unittest

import _support  # noqa: F401
from wake_gate import WakeGate


class WakeGateTests(unittest.TestCase):
    def test_idle_gate_accepts_a_new_wake(self):
        self.assertTrue(WakeGate().accept_wake(detected_at=10.0))

    def test_wake_dropped_while_any_recording_owns_the_recorder(self):
        for source in ('key', 'wake'):
            gate = WakeGate()
            gate.begin(source, object())
            self.assertFalse(gate.accept_wake(10.0), source)

    def test_wake_detected_before_the_last_finish_is_dropped(self):
        # A wake heard while text was being typed queues behind the typing and is
        # delivered after `finished`; its timestamp is older than rearm_at.
        gate = WakeGate()
        thread = object()
        gate.begin('wake', thread, hwnd=7)
        gate.finish(thread, now=20.0)
        self.assertFalse(gate.accept_wake(detected_at=19.5))
        self.assertFalse(gate.accept_wake(detected_at=20.0))
        self.assertTrue(gate.accept_wake(detected_at=20.1))

    def test_keys_ignored_only_during_a_wake_recording(self):
        gate = WakeGate()
        self.assertFalse(gate.keys_ignored())
        gate.begin('key', object())
        self.assertFalse(gate.keys_ignored())
        gate = WakeGate()
        gate.begin('wake', object())
        self.assertTrue(gate.keys_ignored())

    def test_finish_from_a_replaced_thread_is_ignored(self):
        gate = WakeGate()
        old, new = object(), object()
        gate.begin('key', old)
        gate.begin('key', new)
        self.assertFalse(gate.finish(old, now=5.0))
        self.assertEqual(gate.source, 'key')
        self.assertIs(gate.thread, new)
        self.assertEqual(gate.rearm_at, 0.0)

    def test_finish_releases_everything(self):
        gate = WakeGate()
        thread = object()
        gate.begin('wake', thread, hwnd=42)
        self.assertTrue(gate.finish(thread, now=9.0))
        self.assertIsNone(gate.source)
        self.assertIsNone(gate.thread)
        self.assertIsNone(gate.hwnd)
        self.assertEqual(gate.rearm_at, 9.0)
        self.assertFalse(gate.keys_ignored())


if __name__ == '__main__':
    unittest.main()
