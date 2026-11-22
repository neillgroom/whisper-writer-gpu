import sys
import types
import unittest
from unittest import mock

import _support  # noqa: F401
import key_listener
from key_listener import InputEvent, KeyCode, KeyListener, PynputBackend
from pynput import keyboard as real_keyboard, mouse as real_mouse

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_PAUSE, VK_SCROLL, VK_F5, VK_A, VK_SPACE, VK_ENTER, VK_TAB, VK_LCONTROL = (
    0x13, 0x91, 0x74, 0x41, 0x20, 0x0D, 0x09, 0xA2)
VK_OEM_3 = 0xC0  # backquote on a US layout


class Suppressed(Exception):
    """Stands in for pynput's SystemHook.SuppressException."""


class FakeKeyboardListener:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.running = False
        self.suppressed = 0

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def suppress_event(self):
        self.suppressed += 1
        raise Suppressed()


class FakeMouseListener:
    def __init__(self, **kwargs):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


def fake_pynput():
    """A pynput stand-in: the real Key/KeyCode/Button enums, fake (hookless) Listener classes."""
    module = types.ModuleType('pynput')
    module.keyboard = types.SimpleNamespace(
        Listener=FakeKeyboardListener, Key=real_keyboard.Key, KeyCode=real_keyboard.KeyCode)
    module.mouse = types.SimpleNamespace(Listener=FakeMouseListener, Button=real_mouse.Button)
    return module


def start_backend(backend):
    with mock.patch.dict(sys.modules, {'pynput': fake_pynput()}), \
            mock.patch.object(key_listener.sys, 'platform', 'win32'):
        backend.start()


def make_listener(activation_key):
    """Build a KeyListener and start its PynputBackend through the real start() path, as on win32."""
    config = {'activation_key': activation_key, 'input_backend': 'auto'}
    with mock.patch.object(key_listener.ConfigManager, 'get_config_value',
                           side_effect=lambda section, key: config[key]):
        listener = KeyListener()
    backend = PynputBackend()
    listener.backends = [backend]
    listener.select_active_backend()
    start_backend(backend)
    return listener, backend


def reload_keys(listener, activation_key):
    with mock.patch.object(key_listener.ConfigManager, 'get_config_value',
                           side_effect=lambda section, key: activation_key):
        listener.update_activation_keys()


def send(backend, msg, vk, listener=None, modifier=False):
    """Run the filter the fake keyboard Listener received; return (suppressed, return value)."""
    listener = listener or backend.keyboard_listener
    data = types.SimpleNamespace(vkCode=vk)
    with mock.patch.object(key_listener, 'any_modifier_down', return_value=modifier):
        try:
            return False, listener.kwargs['win32_event_filter'](msg, data)
        except Suppressed:
            return True, None


def recording(backend):
    events = []
    backend.on_input_event = events.append
    return events


class KeySwallowTests(unittest.TestCase):
    def test_pause_down_is_dispatched_once_and_suppressed(self):
        listener, backend = make_listener('pause')
        events = recording(backend)
        self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        self.assertEqual(events, [(KeyCode.PAUSE, InputEvent.KEY_PRESS)])
        self.assertEqual(backend.keyboard_listener.suppressed, 1)

    def test_held_pause_gives_one_activate_and_one_deactivate(self):
        listener, backend = make_listener('pause')
        calls = []
        listener.add_callback('on_activate', lambda: calls.append('on'))
        listener.add_callback('on_deactivate', lambda: calls.append('off'))
        results = [send(backend, WM_KEYDOWN, VK_PAUSE)[0] for _ in range(20)]
        results.append(send(backend, WM_KEYUP, VK_PAUSE)[0])
        self.assertEqual(calls, ['on', 'off'])
        self.assertTrue(all(results))
        self.assertEqual(backend.keyboard_listener.suppressed, 21)

    def test_sys_key_messages_are_swallowed_too(self):
        listener, backend = make_listener('pause')
        events = recording(backend)
        self.assertTrue(send(backend, WM_SYSKEYDOWN, VK_PAUSE)[0])
        self.assertTrue(send(backend, WM_SYSKEYUP, VK_PAUSE)[0])
        self.assertEqual(events, [(KeyCode.PAUSE, InputEvent.KEY_PRESS), (KeyCode.PAUSE, InputEvent.KEY_RELEASE)])

    def test_function_key_and_scroll_lock_are_swallowed(self):
        for name, vk, code in (('f5', VK_F5, KeyCode.F5), ('scroll_lock', VK_SCROLL, KeyCode.SCROLL_LOCK)):
            listener, backend = make_listener(name)
            events = recording(backend)
            self.assertTrue(send(backend, WM_KEYDOWN, vk)[0], name)
            self.assertTrue(send(backend, WM_KEYUP, vk)[0], name)
            self.assertEqual(events, [(code, InputEvent.KEY_PRESS), (code, InputEvent.KEY_RELEASE)])

    def test_text_keys_are_never_swallowed(self):
        for name, vk in (('a', VK_A), ('backquote', VK_OEM_3), ('space', VK_SPACE),
                         ('enter', VK_ENTER), ('tab', VK_TAB)):
            listener, backend = make_listener(name)
            self.assertEqual(listener.get_swallow_keys(), frozenset(), name)
            events = recording(backend)
            for msg in (WM_KEYDOWN, WM_KEYUP):
                suppressed, result = send(backend, msg, vk)
                self.assertFalse(suppressed, name)
                self.assertIsNot(result, False, name)
            self.assertEqual(events, [], name)
            self.assertEqual(backend.keyboard_listener.suppressed, 0, name)

    def test_other_keys_pass_through_untouched(self):
        listener, backend = make_listener('pause')
        events = recording(backend)
        for vk in (VK_A, VK_F5):
            suppressed, result = send(backend, WM_KEYDOWN, vk)
            self.assertFalse(suppressed)
            self.assertIsNot(result, False)
        self.assertEqual(events, [])

    def test_modifier_held_at_first_down_passes_down_and_up_through(self):
        listener, backend = make_listener('pause')
        events = recording(backend)
        suppressed, result = send(backend, WM_KEYDOWN, VK_PAUSE, modifier=True)
        self.assertFalse(suppressed)
        self.assertIsNot(result, False)
        suppressed, result = send(backend, WM_KEYUP, VK_PAUSE, modifier=False)
        self.assertFalse(suppressed)
        self.assertIsNot(result, False)
        self.assertEqual(events, [])
        self.assertEqual(backend.keyboard_listener.suppressed, 0)

    def test_modifier_pressed_mid_hold_does_not_leak_the_rest(self):
        listener, backend = make_listener('pause')
        self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        for _ in range(5):
            self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE, modifier=True)[0])
        self.assertTrue(send(backend, WM_KEYUP, VK_PAUSE, modifier=True)[0])
        self.assertEqual(backend.keyboard_listener.suppressed, 7)

    def test_orphan_key_up_passes_through(self):
        listener, backend = make_listener('pause')
        events = recording(backend)
        suppressed, result = send(backend, WM_KEYUP, VK_PAUSE)
        self.assertFalse(suppressed)
        self.assertIsNot(result, False)
        self.assertEqual(events, [])

    def test_rearm_clears_swallowed_state(self):
        listener, backend = make_listener('pause')
        self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        backend.stop()
        start_backend(backend)
        self.assertFalse(send(backend, WM_KEYUP, VK_PAUSE)[0])

    def test_modifier_check_raising_still_swallows(self):
        listener, backend = make_listener('pause')
        data = types.SimpleNamespace(vkCode=VK_PAUSE)
        with mock.patch.object(key_listener, 'any_modifier_down', side_effect=OSError('no state')):
            with self.assertRaises(Suppressed):
                backend.keyboard_listener.kwargs['win32_event_filter'](WM_KEYDOWN, data)

    def test_a_raising_callback_still_suppresses_the_key(self):
        listener, backend = make_listener('pause')

        def boom():
            raise RuntimeError('callback failed')
        listener.add_callback('on_activate', boom)
        self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        self.assertEqual(backend.keyboard_listener.suppressed, 1)

    def test_a_raising_print_still_suppresses_the_key(self):
        listener, backend = make_listener('pause')

        def boom(event):
            raise RuntimeError('dispatch failed')
        backend.on_input_event = boom
        with mock.patch('builtins.print', side_effect=OSError('stdout gone')):
            self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        self.assertEqual(backend.keyboard_listener.suppressed, 1)

    def test_modifier_chords_swallow_nothing(self):
        for chord, vk in (('ctrl+shift+space', VK_SPACE), ('ctrl', VK_LCONTROL), ('ctrl+pause', VK_PAUSE)):
            listener, backend = make_listener(chord)
            self.assertEqual(listener.get_swallow_keys(), frozenset(), chord)
            self.assertFalse(send(backend, WM_KEYDOWN, vk)[0], chord)

    def test_unknown_or_empty_token_swallows_nothing(self):
        for chord in ('win+pause', 'pause+', 'garbage+f5'):
            listener, backend = make_listener(chord)
            self.assertEqual(listener.get_swallow_keys(), frozenset(), chord)
            self.assertFalse(send(backend, WM_KEYDOWN, VK_PAUSE)[0], chord)
            self.assertFalse(send(backend, WM_KEYDOWN, VK_F5)[0], chord)

    def test_reloading_activation_keys_changes_what_is_swallowed(self):
        listener, backend = make_listener('pause')
        reload_keys(listener, 'f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        self.assertTrue(send(backend, WM_KEYDOWN, VK_F5)[0])
        self.assertTrue(send(backend, WM_KEYUP, VK_F5)[0])
        reload_keys(listener, 'ctrl+f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_F5)[0])
        reload_keys(listener, 'win+f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_F5)[0])

    def test_late_event_after_stop_suppresses_via_its_own_listener(self):
        listener, backend = make_listener('pause')
        old = backend.keyboard_listener
        backend.stop()
        self.assertIsNone(backend.keyboard_listener)
        self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE, listener=old)[0])
        self.assertEqual(old.suppressed, 1)

    def test_filter_only_passed_on_windows(self):
        with mock.patch.dict(sys.modules, {'pynput': fake_pynput()}):
            backend = PynputBackend()
            with mock.patch.object(key_listener.sys, 'platform', 'linux'):
                backend.start()
            self.assertEqual(set(backend.keyboard_listener.kwargs), {'on_press', 'on_release'})
            backend.stop()
            with mock.patch.object(key_listener.sys, 'platform', 'win32'):
                backend.start()
            self.assertEqual(set(backend.keyboard_listener.kwargs),
                             {'on_press', 'on_release', 'win32_event_filter'})

    def test_any_modifier_down_returns_bool(self):
        self.assertIsInstance(key_listener.any_modifier_down(), bool)
        with mock.patch.object(key_listener.sys, 'platform', 'linux'):
            self.assertIs(key_listener.any_modifier_down(), False)


if __name__ == '__main__':
    unittest.main()
