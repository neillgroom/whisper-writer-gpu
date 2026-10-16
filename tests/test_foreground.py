import sys
import unittest

import _support  # noqa: F401
import foreground


class ForegroundGateTests(unittest.TestCase):
    ALLOWED = foreground.parse_allowed('WindowsTerminal.exe, Code.exe ,')

    def test_parse_allowed_lowercases_and_drops_blanks(self):
        self.assertEqual(self.ALLOWED, {'windowsterminal.exe', 'code.exe'})
        self.assertEqual(foreground.parse_allowed(None), set())

    def test_allowed_process(self):
        self.assertTrue(foreground.is_allowed(100, self.ALLOWED, name_of=lambda h: 'WindowsTerminal.exe'))

    def test_other_process_denied(self):
        self.assertFalse(foreground.is_allowed(100, self.ALLOWED, name_of=lambda h: 'notepad.exe'))

    def test_null_window_denied_without_lookup(self):
        def must_not_run(hwnd):
            raise AssertionError('name lookup ran for a null window')
        self.assertFalse(foreground.is_allowed(None, self.ALLOWED, name_of=must_not_run))
        self.assertFalse(foreground.is_allowed(0, self.ALLOWED, name_of=must_not_run))

    def test_failed_lookup_denied(self):
        # OpenProcess / QueryFullProcessImageNameW failure surfaces as None: fail closed.
        self.assertFalse(foreground.is_allowed(100, self.ALLOWED, name_of=lambda h: None))

    def test_empty_allow_list_denies_everything(self):
        self.assertFalse(foreground.is_allowed(100, set(), name_of=lambda h: 'WindowsTerminal.exe'))

    @unittest.skipUnless(sys.platform == 'win32', 'Win32 API')
    def test_live_calls_do_not_raise(self):
        hwnd = foreground.foreground_window()
        name = foreground.process_name(hwnd)
        self.assertTrue(name is None or name.lower().endswith('.exe'))
        self.assertIsNone(foreground.process_name(0))


if __name__ == '__main__':
    unittest.main()
