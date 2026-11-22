import contextlib
import unittest
from unittest import mock

from pynput.keyboard import Key, KeyCode

from _support import set_config
import input_simulation
from input_simulation import InputSimulator

SHIFTS = (Key.shift, Key.shift_l, Key.shift_r)

# A fixed US-style layout so no test depends on the machine's real keyboard layout.
# char -> (vk, modifier byte); missing chars -> None (the layout has no key for them).
LAYOUT = {
    'h': (0x48, 0), 'e': (0x45, 0), 'l': (0x4C, 0), 'o': (0x4F, 0), 'w': (0x57, 0),
    'r': (0x52, 0), 'd': (0x44, 0), 'a': (0x41, 0), 'b': (0x42, 0), 'c': (0x43, 0),
    ' ': (0x20, 0),
    'H': (0x48, 1), 'A': (0x41, 1),
    '?': (0xBF, 1), '!': (0x31, 1), ':': (0xBA, 1),
    '@': (0x51, 6),  # AltGr (Ctrl+Alt) on some layouts
}


class FakeKeyboard:
    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(('down', key))

    def release(self, key):
        self.events.append(('up', key))

    @contextlib.contextmanager
    def pressed(self, *keys):
        for key in keys:
            self.press(key)
        try:
            yield
        finally:
            for key in reversed(keys):
                self.release(key)

    @property
    def typed(self):
        """Non-Shift key-downs, in order."""
        return [k for kind, k in self.events if kind == 'down' and k not in SHIFTS]


class TypewriteTests(unittest.TestCase):
    def setUp(self):
        set_config('pynput', 'post_processing', 'input_method')
        set_config(0.0, 'post_processing', 'writing_key_press_delay')
        patcher = mock.patch.object(input_simulation, 'layout_key', lambda ch: LAYOUT.get(ch))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sim = InputSimulator()
        self.kb = self.sim.keyboard = FakeKeyboard()

    def assert_every_shift_press_released(self):
        for shift in SHIFTS:
            held = 0
            for kind, key in self.kb.events:
                if key == shift:
                    held = held + 1 if kind == 'down' else max(held - 1, 0)
            self.assertEqual(held, 0, f'{shift} left held')

    def test_types_everything_without_a_check(self):
        self.assertEqual(self.sim.typewrite('hello'), '')
        self.assertEqual(''.join(self.kb.typed), 'hello')

    def test_stops_at_the_first_failed_check_and_returns_the_rest(self):
        calls = {'n': 0}

        def still_focused():
            calls['n'] += 1
            return calls['n'] <= 3  # focus moves away after three keystrokes

        remainder = self.sim.typewrite('hello world', should_continue=still_focused)
        self.assertEqual(''.join(self.kb.typed), 'hel')
        self.assertEqual(remainder, 'lo world')

    def test_check_failing_up_front_sends_nothing_at_all(self):
        remainder = self.sim.typewrite('abc', should_continue=lambda: False)
        self.assertEqual(self.kb.events, [])  # not even the defensive Shift release
        self.assertEqual(remainder, 'abc')

    def test_empty_text_sends_nothing(self):
        self.assertEqual(self.sim.typewrite(''), '')
        self.assertEqual(self.kb.events, [])

    def test_all_three_shift_keys_are_released_first(self):
        self.sim.typewrite('a')
        self.assertEqual(self.kb.events[:3], [('up', Key.shift), ('up', Key.shift_l), ('up', Key.shift_r)])

    def test_lowercase_is_pressed_as_the_character(self):
        self.sim.typewrite('ab')
        self.assertEqual(self.kb.events[3:], [('down', 'a'), ('up', 'a'), ('down', 'b'), ('up', 'b')])

    def test_uppercase_is_shift_plus_the_layout_vk(self):
        self.sim.typewrite('H')
        vk = KeyCode.from_vk(0x48)
        self.assertEqual(self.kb.events[3:], [('down', Key.shift), ('down', vk), ('up', vk), ('up', Key.shift)])

    def test_shifted_punctuation_is_shift_plus_the_layout_vk(self):
        for char in '?!:':
            self.kb.events.clear()
            self.sim.typewrite(char)
            vk = KeyCode.from_vk(LAYOUT[char][0])
            self.assertEqual(self.kb.events[3:],
                             [('down', Key.shift), ('down', vk), ('up', vk), ('up', Key.shift)], char)

    def test_unmapped_or_altgr_characters_are_pressed_as_themselves(self):
        self.sim.typewrite('é@')  # no key on the layout; a Ctrl+Alt combo
        self.assertEqual(self.kb.events[3:], [('down', 'é'), ('up', 'é'), ('down', '@'), ('up', '@')])

    def test_dotted_capital_i_does_not_raise(self):
        # U+0130 lowercases to two code points; the old char.lower() path crashed on it.
        self.assertEqual(self.sim.typewrite('İ'), '')
        self.assertEqual(self.kb.typed, ['İ'])

    def test_stopping_mid_text_leaves_no_shift_held(self):
        calls = {'n': 0}

        def still_focused():
            calls['n'] += 1
            return calls['n'] <= 2

        remainder = self.sim.typewrite('HAH', should_continue=still_focused)
        self.assertEqual(remainder, 'H')
        self.assertEqual(self.kb.typed, [KeyCode.from_vk(0x48), KeyCode.from_vk(0x41)])
        self.assertEqual(self.kb.events[-1], ('up', Key.shift))
        self.assert_every_shift_press_released()

    def test_a_raising_press_still_releases_shift(self):
        def boom(key):
            if key not in SHIFTS:
                raise RuntimeError('injection failed')
            self.kb.events.append(('down', key))
        self.kb.press = boom
        with self.assertRaises(RuntimeError):
            self.sim.typewrite('H')
        self.assert_every_shift_press_released()


class LayoutKeyTests(unittest.TestCase):
    def test_no_binding_or_non_bmp_returns_none(self):
        with mock.patch.object(input_simulation, '_vk_key_scan', None):
            self.assertIsNone(input_simulation.layout_key('A'))
        with mock.patch.object(input_simulation, '_vk_key_scan', lambda c: 0x0141):
            self.assertIsNone(input_simulation.layout_key('\U0001F600'))

    def test_splits_vk_and_modifiers_and_maps_failure_to_none(self):
        with mock.patch.object(input_simulation, '_vk_key_scan', lambda c: 0x0141):
            self.assertEqual(input_simulation.layout_key('A'), (0x41, 1))
        with mock.patch.object(input_simulation, '_vk_key_scan', lambda c: -1):
            self.assertIsNone(input_simulation.layout_key('é'))


if __name__ == '__main__':
    unittest.main()
