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
        self.caps = False
        self.hkls = []
        for patcher in (
                mock.patch.object(input_simulation, 'layout_key', self.fake_layout_key),
                mock.patch.object(input_simulation, 'caps_lock_on', lambda: self.caps),
                mock.patch.object(input_simulation, 'target_layout', lambda: 0x4090409)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sim = InputSimulator()
        self.kb = self.sim.keyboard = FakeKeyboard()

    def fake_layout_key(self, char, hkl=None):
        self.hkls.append(hkl)
        return LAYOUT.get(char)

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

    def shifted(self, vk):
        key = KeyCode.from_vk(vk)
        return [('down', Key.shift), ('down', key), ('up', key), ('up', Key.shift)]

    def bare(self, vk):
        key = KeyCode.from_vk(vk)
        return [('down', key), ('up', key)]

    def test_caps_lock_on_uppercase_is_the_vk_without_shift(self):
        self.caps = True
        self.sim.typewrite('H')
        self.assertEqual(self.kb.events[3:], self.bare(0x48))

    def test_caps_lock_on_lowercase_is_the_vk_with_shift(self):
        self.caps = True
        self.sim.typewrite('h')
        self.assertEqual(self.kb.events[3:], self.shifted(0x48))

    def test_caps_lock_on_punctuation_and_space_are_unchanged(self):
        self.caps = True
        self.sim.typewrite('? ')
        self.assertEqual(self.kb.events[3:], self.shifted(0xBF) + [('down', ' '), ('up', ' ')])

    def test_caps_lock_and_layout_are_read_once_per_call(self):
        reads = []
        with mock.patch.object(input_simulation, 'caps_lock_on', lambda: reads.append('caps') or False), \
             mock.patch.object(input_simulation, 'target_layout', lambda: reads.append('hkl') or None):
            self.sim.typewrite('Hello')
        self.assertEqual(sorted(reads), ['caps', 'hkl'])

    def test_the_target_layout_is_passed_to_every_lookup(self):
        self.sim.typewrite('Ha')
        self.assertEqual(self.hkls, [0x4090409, 0x4090409])

    def test_no_target_layout_falls_back_to_the_default_lookup(self):
        with mock.patch.object(input_simulation, 'target_layout', lambda: None):
            self.sim.typewrite('H')
        self.assertEqual(self.hkls, [None])
        self.assertEqual(self.kb.events[3:], self.shifted(0x48))

    def test_a_character_the_controller_refuses_is_skipped_and_the_rest_typed(self):
        real_press = self.kb.press

        def press(key):
            if key == '\U0001F600':
                raise TypeError('wrong type')
            real_press(key)
        self.kb.press = press
        with mock.patch.object(input_simulation.ConfigManager, 'console_print') as said:
            self.assertEqual(self.sim.typewrite('a\U0001F600\U0001F600H'), '')
        self.assertEqual(self.kb.typed, ['a', KeyCode.from_vk(0x48)])
        said.assert_called_once()
        self.assert_every_shift_press_released()

    def test_a_shifted_press_that_raises_is_skipped_with_shift_released(self):
        real_press = self.kb.press

        def press(key):
            if key == KeyCode.from_vk(0x48):
                raise OSError('SendInput failed')
            real_press(key)
        self.kb.press = press
        with mock.patch.object(input_simulation.ConfigManager, 'console_print'):
            self.assertEqual(self.sim.typewrite('Ha'), '')
        self.assertEqual(self.kb.typed, ['a'])
        self.assert_every_shift_press_released()


class LayoutKeyTests(unittest.TestCase):
    def test_a_layout_handle_uses_the_ex_lookup(self):
        calls = []
        with mock.patch.object(input_simulation, '_vk_key_scan', lambda c: calls.append('plain') or 0x0141), \
             mock.patch.object(input_simulation, '_vk_key_scan_ex',
                               lambda c, h: calls.append(('ex', h)) or 0x0141):
            self.assertEqual(input_simulation.layout_key('A', 0x4070407), (0x41, 1))
            self.assertEqual(input_simulation.layout_key('A', None), (0x41, 1))
        self.assertEqual(calls, [('ex', 0x4070407), 'plain'])

    def test_target_layout_failures_return_none(self):
        with mock.patch.object(input_simulation, '_get_foreground_window', lambda: None):
            self.assertIsNone(input_simulation.target_layout())
        with mock.patch.object(input_simulation, '_get_foreground_window', lambda: 1), \
             mock.patch.object(input_simulation, '_get_window_thread_process_id', lambda h, p: 0):
            self.assertIsNone(input_simulation.target_layout())
        with mock.patch.object(input_simulation, '_get_foreground_window', lambda: 1), \
             mock.patch.object(input_simulation, '_get_window_thread_process_id', lambda h, p: 7), \
             mock.patch.object(input_simulation, '_get_keyboard_layout', lambda t: 0):
            self.assertIsNone(input_simulation.target_layout())
        with mock.patch.object(input_simulation, '_get_foreground_window', side_effect=OSError):
            self.assertIsNone(input_simulation.target_layout())
        with mock.patch.object(input_simulation, '_get_foreground_window', lambda: 1), \
             mock.patch.object(input_simulation, '_get_window_thread_process_id', lambda h, p: 7), \
             mock.patch.object(input_simulation, '_get_keyboard_layout', lambda t: 0x4090409):
            self.assertEqual(input_simulation.target_layout(), 0x4090409)

    def test_caps_lock_reads_the_toggle_bit(self):
        with mock.patch.object(input_simulation, '_get_key_state', lambda vk: 1 if vk == 0x14 else 0):
            self.assertTrue(input_simulation.caps_lock_on())
        with mock.patch.object(input_simulation, '_get_key_state', lambda vk: -128):  # held, not toggled
            self.assertFalse(input_simulation.caps_lock_on())
        with mock.patch.object(input_simulation, '_get_key_state', None):
            self.assertFalse(input_simulation.caps_lock_on())

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
