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


CHAR_VKS = {VK_A: 'a', VK_OEM_3: '`'}


def pynput_key_for(vk):
    """The key real pynput would hand on_press/on_release for this vk (reverse of vk_map's source)."""
    if vk in CHAR_VKS:
        return real_keyboard.KeyCode.from_char(CHAR_VKS[vk])
    for key in real_keyboard.Key:
        if getattr(key.value, 'vk', None) == vk:
            return key
    return real_keyboard.KeyCode.from_vk(vk)


def send(backend, msg, vk, listener=None, modifier=False):
    """
    Behave like pynput: run the filter; if it suppresses, stop. Otherwise (non-False return) forward
    the key to on_press/on_release. Returns (suppressed, filter return value); counts forwards on
    backend.forwarded so tests can tell the normal path from the filter's direct dispatch.
    """
    listener = listener or backend.keyboard_listener
    data = types.SimpleNamespace(vkCode=vk)
    with mock.patch.object(key_listener, 'any_modifier_down', return_value=modifier):
        try:
            result = listener.kwargs['win32_event_filter'](msg, data)
        except Suppressed:
            return True, None
    if result is not False:
        handler = 'on_press' if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) else 'on_release'
        backend.forwarded = getattr(backend, 'forwarded', 0) + 1
        listener.kwargs[handler](pynput_key_for(vk))
    return False, result


def recording(backend):
    events = []
    backend.on_input_event = events.append
    return events


@unittest.skipUnless(sys.platform == 'win32',
                     'Windows-only feature: these tests send Windows virtual-key codes, which only '
                     "match pynput's enums on its win32 backend")
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
            code = KeyCode[name.upper()]
            listener, backend = make_listener(name)
            self.assertEqual(listener.get_swallow_keys(), frozenset(), name)
            events = recording(backend)
            for msg in (WM_KEYDOWN, WM_KEYUP):
                suppressed, result = send(backend, msg, vk)
                self.assertFalse(suppressed, name)
                self.assertIsNot(result, False, name)
            # Reaches the normal pynput path, once per event: the filter did not direct-dispatch.
            self.assertEqual(backend.forwarded, 2, name)
            self.assertEqual(events, [(code, InputEvent.KEY_PRESS), (code, InputEvent.KEY_RELEASE)], name)
            self.assertEqual(backend.keyboard_listener.suppressed, 0, name)

    def test_other_keys_pass_through_untouched(self):
        listener, backend = make_listener('pause')
        events = recording(backend)
        for vk in (VK_A, VK_F5):
            suppressed, result = send(backend, WM_KEYDOWN, vk)
            self.assertFalse(suppressed)
            self.assertIsNot(result, False)
        self.assertEqual(backend.forwarded, 2)
        self.assertEqual(events, [(KeyCode.A, InputEvent.KEY_PRESS), (KeyCode.F5, InputEvent.KEY_PRESS)])

    def test_modifier_held_at_first_down_passes_down_and_up_through(self):
        listener, backend = make_listener('pause')
        calls = []
        listener.add_callback('on_activate', lambda: calls.append('on'))
        listener.add_callback('on_deactivate', lambda: calls.append('off'))
        dispatched = []
        real_dispatch = backend.on_input_event
        backend.on_input_event = lambda event: (dispatched.append(event), real_dispatch(event))
        suppressed, result = send(backend, WM_KEYDOWN, VK_PAUSE, modifier=True)
        self.assertFalse(suppressed)
        self.assertIsNot(result, False)
        suppressed, result = send(backend, WM_KEYUP, VK_PAUSE, modifier=False)
        self.assertFalse(suppressed)
        self.assertIsNot(result, False)
        self.assertEqual(backend.keyboard_listener.suppressed, 0)
        # Exactly one dispatch per event, both via the normal pynput path (no filter direct-dispatch).
        self.assertEqual(backend.forwarded, 2)
        self.assertEqual(dispatched, [(KeyCode.PAUSE, InputEvent.KEY_PRESS), (KeyCode.PAUSE, InputEvent.KEY_RELEASE)])
        # Pre-existing behaviour, deliberately kept: a stuck modifier must never make dictation dead.
        self.assertEqual(calls, ['on', 'off'])

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
        self.assertEqual(backend.forwarded, 1)
        self.assertEqual(events, [(KeyCode.PAUSE, InputEvent.KEY_RELEASE)])

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

    def test_pass_decision_holds_when_modifier_released_mid_hold(self):
        listener, backend = make_listener('f5')
        events = recording(backend)
        self.assertFalse(send(backend, WM_KEYDOWN, VK_F5, modifier=True)[0])  # Ctrl+F5 down
        for _ in range(5):  # Ctrl released, F5 still auto-repeating
            self.assertFalse(send(backend, WM_KEYDOWN, VK_F5, modifier=False)[0])
        self.assertFalse(send(backend, WM_KEYUP, VK_F5, modifier=False)[0])
        self.assertEqual(backend.keyboard_listener.suppressed, 0)
        self.assertEqual(backend.forwarded, 7)  # each event forwarded exactly once
        self.assertEqual(len(events), 7)  # and dispatched once, via the normal path only
        # A fresh unmodified press after that key-up is swallowed normally.
        self.assertTrue(send(backend, WM_KEYDOWN, VK_F5)[0])
        self.assertTrue(send(backend, WM_KEYUP, VK_F5)[0])
        self.assertEqual(backend.forwarded, 7)

    def test_rearm_clears_both_hold_sets(self):
        listener, backend = make_listener('pause')
        self.assertFalse(send(backend, WM_KEYDOWN, VK_PAUSE, modifier=True)[0])
        backend.stop()
        start_backend(backend)
        self.assertEqual(backend.passed_down, set())
        self.assertEqual(backend.swallowed_down, set())
        self.assertTrue(send(backend, WM_KEYDOWN, VK_PAUSE)[0])  # no stale pass-through decision


class KeySwallowPlatformTests(unittest.TestCase):
    """Runs everywhere: no Windows virtual-key values involved."""

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
