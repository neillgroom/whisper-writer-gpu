import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCheckBox, QLineEdit

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

    def test_wake_options_render_with_existing_widget_types(self):
        window = self.SettingsWindow()
        self.assertIsNotNone(window.findChild(QCheckBox, 'wake_options_enabled_input'))
        for key in ('wake_model', 'threshold', 'allowed_processes', 'laptop_match',
                    'no_speech_timeout_ms', 'max_recording_s'):
            self.assertIsNotNone(window.findChild(QLineEdit, f'wake_options_{key}_input'), key)

    def test_showing_again_keeps_unsaved_edits(self):
        # Re-reading config on every show wiped unsaved edits and the env-held API key.
        window = self.SettingsWindow()
        field = window.findChild(QLineEdit, 'model_options_api_api_key_input')
        self.assertIsNotNone(field)
        field.setText('sk-typed-but-not-saved')
        window.show()
        window.hide()
        window.show()
        self.assertEqual(field.text(), 'sk-typed-but-not-saved')
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
