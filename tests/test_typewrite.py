import unittest

from _support import set_config
from input_simulation import InputSimulator


class FakeKeyboard:
    def __init__(self):
        self.typed = []

    def press(self, char):
        self.typed.append(char)

    def release(self, char):
        pass


class TypewriteTests(unittest.TestCase):
    def setUp(self):
        set_config('pynput', 'post_processing', 'input_method')
        set_config(0.0, 'post_processing', 'writing_key_press_delay')
        self.sim = InputSimulator()
        self.sim.keyboard = FakeKeyboard()

    def test_types_everything_without_a_check(self):
        self.assertEqual(self.sim.typewrite('hello'), '')
        self.assertEqual(''.join(self.sim.keyboard.typed), 'hello')

    def test_stops_at_the_first_failed_check_and_returns_the_rest(self):
        calls = {'n': 0}

        def still_focused():
            calls['n'] += 1
            return calls['n'] <= 3  # focus moves away after three keystrokes

        remainder = self.sim.typewrite('hello world', should_continue=still_focused)
        self.assertEqual(''.join(self.sim.keyboard.typed), 'hel')
        self.assertEqual(remainder, 'lo world')

    def test_check_runs_before_the_first_keystroke(self):
        remainder = self.sim.typewrite('abc', should_continue=lambda: False)
        self.assertEqual(self.sim.keyboard.typed, [])
        self.assertEqual(remainder, 'abc')


if __name__ == '__main__':
    unittest.main()
