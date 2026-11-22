import types
import unittest
from unittest import mock

import _support  # noqa: F401
import key_listener
from key_listener import InputEvent, KeyCode, KeyListener, PynputBackend

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_PAUSE, VK_A, VK_F5 = 0x13, 0x41, 0x74


class Suppressed(Exception):
    """Stands in for pynput's SystemHook.SuppressException."""


class FakeListener:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeListener.last_kwargs = kwargs
        self.running = False
        self.suppressed = 0

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def suppress_event(self):
        self.suppressed += 1
        raise Suppressed()


def make_backend():
    from pynput import keyboard, mouse
    backend = PynputBackend()
    backend.keyboard = types.SimpleNamespace(Listener=FakeListener, Key=keyboard.Key, KeyCode=keyboard.KeyCode)
    backend.mouse = types.SimpleNamespace(Listener=FakeListener, Button=mouse.Button)
    backend.key_map = backend._create_key_map()
    backend.vk_map = backend._create_vk_map()
    return backend


def make_listener(activation_key):
    config = {'activation_key': activation_key, 'input_backend': 'auto'}
    with mock.patch.object(key_listener.ConfigManager, 'get_config_value',
                           side_effect=lambda section, key: config[key]):
        listener = KeyListener()
        backend = make_backend()
        listener.backends = [backend]
        listener.select_active_backend()
        backend.start()
    return listener, backend


def reload_keys(listener, activation_key):
    with mock.patch.object(key_listener.ConfigManager, 'get_config_value',
                           side_effect=lambda section, key: activation_key):
        listener.update_activation_keys()


def send(backend, msg, vk):
    """Run the filter like pynput does; return (suppressed, return value)."""
    data = types.SimpleNamespace(vkCode=vk)
    try:
        return False, backend._win32_event_filter(msg, data)
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

    def test_modifier_chord_swallows_nothing(self):
        listener, backend = make_listener('ctrl+shift+space')
        self.assertEqual(listener.get_swallow_keys(), frozenset())
        self.assertFalse(send(backend, WM_KEYDOWN, 0x20)[0])

    def test_lone_modifier_chord_swallows_nothing(self):
        listener, backend = make_listener('ctrl')
        self.assertEqual(listener.get_swallow_keys(), frozenset())
        self.assertFalse(send(backend, WM_KEYDOWN, 0xA2)[0])  # VK_LCONTROL

    def test_reloading_activation_keys_changes_what_is_swallowed(self):
        listener, backend = make_listener('pause')
        reload_keys(listener, 'f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_PAUSE)[0])
        self.assertTrue(send(backend, WM_KEYDOWN, VK_F5)[0])
        reload_keys(listener, 'ctrl+f5')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_F5)[0])

    def test_a_raising_callback_still_suppresses_the_key(self):
        listener, backend = make_listener('pause')

        def boom():
            raise RuntimeError('callback failed')
        listener.add_callback('on_activate', boom)
        suppressed, _ = send(backend, WM_KEYDOWN, VK_PAUSE)
        self.assertTrue(suppressed)
        self.assertEqual(backend.keyboard_listener.suppressed, 1)

    def test_filter_only_passed_on_windows(self):
        backend = make_backend()
        keyboard_kwargs = []
        backend.keyboard = types.SimpleNamespace(
            Listener=lambda **kw: keyboard_kwargs.append(kw) or FakeListener(**kw))
        with mock.patch.object(key_listener.sys, 'platform', 'linux'):
            backend.start()
        self.assertEqual(set(keyboard_kwargs[-1]), {'on_press', 'on_release'})
        backend.stop()
        with mock.patch.object(key_listener.sys, 'platform', 'win32'):
            backend.start()
        self.assertEqual(set(keyboard_kwargs[-1]), {'on_press', 'on_release', 'win32_event_filter'})
        self.assertEqual(keyboard_kwargs[-1]['win32_event_filter'], backend._win32_event_filter)


if __name__ == '__main__':
    unittest.main()
