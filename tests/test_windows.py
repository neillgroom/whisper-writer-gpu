import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCheckBox

from _support import qt_app, set_config
from utils import ConfigManager


class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()
        from ui.settings_window import SettingsWindow
        cls.SettingsWindow = SettingsWindow

    def test_saved_false_is_not_replaced_by_the_default(self):
        set_config(False, 'misc', 'print_to_terminal')  # schema default is true
        meta = {'value': True}
        self.assertIs(self.SettingsWindow.get_config_value(None, 'misc', None, 'print_to_terminal', meta), False)

    def test_opening_the_window_rereads_the_live_config(self):
        set_config(False, 'wake_options', 'enabled')
        window = self.SettingsWindow()
        checkbox = window.findChild(QCheckBox, 'wake_options_enabled_input')
        self.assertIsNotNone(checkbox)
        self.assertFalse(checkbox.isChecked())
        set_config(True, 'wake_options', 'enabled')  # e.g. written by the tray while the window existed
        window.show()
        self.assertTrue(checkbox.isChecked())
        window.hide()
        ConfigManager.reload_config()


class StatusWindowTests(unittest.TestCase):
    def test_status_window_never_takes_focus(self):
        qt_app()
        from ui.status_window import StatusWindow
        window = StatusWindow()
        self.assertTrue(window.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(window.testAttribute(Qt.WA_ShowWithoutActivating))


if __name__ == '__main__':
    unittest.main()
