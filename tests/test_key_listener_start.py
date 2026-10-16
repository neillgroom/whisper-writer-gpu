import types
import unittest

import _support  # noqa: F401
from key_listener import PynputBackend


class FakeListener:
    created = 0

    def __init__(self, **kwargs):
        FakeListener.created += 1
        self.running = False
        self.stopped = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.stopped = True


class PynputStartTests(unittest.TestCase):
    def backend(self):
        FakeListener.created = 0
        backend = PynputBackend()
        backend.keyboard = types.SimpleNamespace(Listener=FakeListener)
        backend.mouse = types.SimpleNamespace(Listener=FakeListener)
        backend.key_map = {}
        return backend

    def test_repeated_start_installs_one_hook(self):
        # The app calls start() after every transcription.
        backend = self.backend()
        for _ in range(5):
            backend.start()
        self.assertEqual(FakeListener.created, 2)  # one keyboard + one mouse listener

    def test_a_dead_hook_is_replaced_and_the_old_one_stopped(self):
        backend = self.backend()
        backend.start()
        old = backend.keyboard_listener
        old.running = False  # the hook thread died
        backend.start()
        self.assertTrue(old.stopped)
        self.assertIsNot(backend.keyboard_listener, old)
        self.assertEqual(FakeListener.created, 4)


if __name__ == '__main__':
    unittest.main()
