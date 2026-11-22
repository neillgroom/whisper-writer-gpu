import sys
import types
import unittest
from unittest import mock

import _support  # noqa: F401
import key_listener
from key_listener import InputEvent, KeyCode, KeyListener, PynputBackend
from pynput import keyboard as real_keyboard, mouse as real_mouse

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_PAUSE, VK_A, VK_F5, VK_SPACE, VK_LCONTROL = 0x13, 0x41, 0x74, 0x20, 0xA2
VK_OEM_3 = 0xC0  # backquote on a US layout
FAKE_LAYOUT = {'`': VK_OEM_3, 'a': VK_A}


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


def make_listener(activation_key, layout=FAKE_LAYOUT):
    """Build a KeyListener and start its PynputBackend through the real start() path, as on win32."""
    config = {'activation_key': activation_key, 'input_backend': 'auto'}
    with mock.patch.object(key_listener.ConfigManager, 'get_config_value',
                           side_effect=lambda section, key: config[key]):
        listener = KeyListener()
    backend = PynputBackend()
    listener.backends = [backend]
    listener.select_active_backend()
    with mock.patch.dict(sys.modules, {'pynput': fake_pynput()}), \
            mock.patch.object(key_listener.sys, 'platform', 'win32'), \
            mock.patch.object(key_listener, 'resolve_char_vk', side_effect=layout.get):
        backend.start()
    return listener, backend


def reload_keys(listener, activation_key):
    with mock.patch.object(key_listener.ConfigManager, 'get_config_value',
                           side_effect=lambda section, key: activation_key):
        listener.update_activation_keys()


def send(backend, msg, vk, listener=None):
    """Run the filter the fake keyboard Listener received; return (suppressed, return value)."""
    listener = listener or backend.keyboard_listener
    data = types.SimpleNamespace(vkCode=vk)
    try:
        return False, listener.kwargs['win32_event_filter'](msg, data)
    except Suppressed:
        return True, None


class KeySwallowTests(unittest.TestCase):
    def test_swallowed_key_down_is_dispatched_once_and_suppressed(self):
        listener, backend = make_listener('pause')
        events = []
        backend.on_input_event = events.append
        suppressed, _ = send(backend, WM_KEYDOWN, VK_PAUSE)
        self.assertTrue(suppressed)
        self.assertEqual(events, [(KeyCode.PAUSE, InputEvent.KEY_PRESS)])
        self.assertEqual(backend.keyboard_listener.suppressed, 1)

    def test_held_key_repeats_give_one_activate_and_one_deactivate(self):
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
        events = []
        backend.on_input_event = events.append
        self.assertTrue(send(backend, WM_SYSKEYDOWN, VK_PAUSE)[0])
        self.assertTrue(send(backend, WM_SYSKEYUP, VK_PAUSE)[0])
        self.assertEqual(events, [(KeyCode.PAUSE, InputEvent.KEY_PRESS), (KeyCode.PAUSE, InputEvent.KEY_RELEASE)])

    def test_other_keys_pass_through_untouched(self):
        listener, backend = make_listener('pause')
        events = []
        backend.on_input_event = events.append
        for vk in (VK_A, VK_F5):
            suppressed, result = send(backend, WM_KEYDOWN, vk)
            self.assertFalse(suppressed)
            self.assertIsNot(result, False)
        self.assertEqual(events, [])
        self.assertEqual(backend.keyboard_listener.suppressed, 0)

    def test_lone_character_key_is_swallowed(self):
        for name, vk, code in (('backquote', VK_OEM_3, KeyCode.BACKQUOTE), ('a', VK_A, KeyCode.A)):
            listener, backend = make_listener(name)
            events = []
            backend.on_input_event = events.append
            self.assertTrue(send(backend, WM_KEYDOWN, vk)[0], name)
            self.assertEqual(events, [(code, InputEvent.KEY_PRESS)])

    def test_unresolvable_character_key_swallows_nothing(self):
        listener, backend = make_listener('backquote', layout={})
        self.assertEqual(listener.get_swallow_keys(), frozenset({KeyCode.BACKQUOTE}))
        self.assertFalse(send(backend, WM_KEYDOWN, VK_OEM_3)[0])

    def test_modifier_chord_swallows_nothing(self):
        listener, backend = make_listener('ctrl+shift+space')
        self.assertEqual(listener.get_swallow_keys(), frozenset())
        self.assertFalse(send(backend, WM_KEYDOWN, VK_SPACE)[0])

    def test_lone_modifier_chord_swallows_nothing(self):
        listener, backend = make_listener('ctrl')
        self.assertEqual(listener.get_swallow_keys(), frozenset())
        self.assertFalse(send(backend, WM_KEYDOWN, VK_LCONTROL)[0])

    def test_unknown_or_empty_token_swallows_nothing(self):
        for chord in ('win+space', 'space+', 'garbage+pause'):
            listener, backend = make_listener(chord)
            self.assertEqual(listener.get_swallow_keys(), frozenset(), chord)
            self.assertFalse(send(backend, WM_KEYDOWN, VK_SPACE)[0], chord)
            self.assertFalse(send(backend, WM_KEYDOWN, VK_PAUSE)[0], chord)

    def test_reloading_activation_keys_changes_what_is_swallowed(self):
        listener, backend = make_listener('pause')
        reload_keys(listener, 'f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        self.assertTrue(send(backend, WM_KEYDOWN, VK_F5)[0])
        reload_keys(listener, 'ctrl+f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_F5)[0])
        reload_keys(listener, 'win+f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_F5)[0])

    def test_a_raising_callback_still_suppresses_the_key(self):
        listener, backend = make_listener('pause')

        def boom():
            raise RuntimeError('callback failed')
        listener.add_callback('on_activate', boom)
        suppressed, _ = send(backend, WM_KEYDOWN, VK_PAUSE)
        self.assertTrue(suppressed)
        self.assertEqual(backend.keyboard_listener.suppressed, 1)

    def test_late_event_after_stop_suppresses_via_its_own_listener(self):
        listener, backend = make_listener('pause')
        old = backend.keyboard_listener
        backend.stop()
        self.assertIsNone(backend.keyboard_listener)
        suppressed, _ = send(backend, WM_KEYDOWN, VK_PAUSE, listener=old)
        self.assertTrue(suppressed)
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

    def test_resolver_is_none_off_windows(self):
        with mock.patch.object(key_listener.sys, 'platform', 'linux'):
            self.assertIsNone(key_listener.resolve_char_vk('a'))


if __name__ == '__main__':
    unittest.main()
