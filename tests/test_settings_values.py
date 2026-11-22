import unittest

import _support  # noqa: F401
from ui.settings_window import combobox_items, display_text, parse_typed_text, stage_values


class ComboboxItemsTests(unittest.TestCase):
    def test_a_known_value_keeps_the_options_unchanged(self):
        self.assertEqual(combobox_items(['base', 'large-v3'], 'base'), ['base', 'large-v3'])

    def test_an_unknown_configured_value_is_appended_not_replaced(self):
        self.assertEqual(combobox_items(['base', 'large-v3'], 'my-finetune'), ['base', 'large-v3', 'my-finetune'])

    def test_none_adds_nothing(self):
        self.assertEqual(combobox_items(['base'], None), ['base'])


class StageValuesTests(unittest.TestCase):
    def test_all_valid_returns_every_value(self):
        readers = [('sound_device', lambda: parse_typed_text('15', 'int')),
                   ('threshold', lambda: parse_typed_text('0.4', 'float')),
                   ('model', lambda: parse_typed_text('base', 'str'))]
        self.assertEqual(stage_values(readers),
                         ([('sound_device', 15), ('threshold', 0.4), ('model', 'base')], None))

    def test_one_invalid_names_the_field_and_returns_nothing_to_apply(self):
        later = []
        readers = [('sound_device', lambda: parse_typed_text('15', 'int')),
                   ('silence_duration', lambda: parse_typed_text('abc', 'int')),
                   ('threshold', lambda: later.append(1) or 0.4)]
        values, bad = stage_values(readers)
        self.assertIsNone(values)
        self.assertEqual(bad, 'silence_duration')
        self.assertEqual(later, [])  # stops at the first bad field


class DisplayTextTests(unittest.TestCase):
    def test_null_shows_as_an_empty_field(self):
        self.assertEqual(display_text(None), '')

    def test_real_values_keep_their_text(self):
        self.assertEqual(display_text(0), '0')
        self.assertEqual(display_text(15), '15')
        self.assertEqual(display_text(0.5), '0.5')


class ParseTypedTextTests(unittest.TestCase):
    def test_empty_numeric_saves_as_none(self):
        for value_type in ('int', 'float'):
            self.assertIsNone(parse_typed_text('', value_type))
            self.assertIsNone(parse_typed_text('  ', value_type))

    def test_numbers_parse_to_their_type(self):
        self.assertEqual(parse_typed_text('15', 'int'), 15)
        self.assertEqual(parse_typed_text('0', 'int'), 0)
        self.assertEqual(parse_typed_text(' 2500 ', 'int'), 2500)
        self.assertEqual(parse_typed_text('0.4', 'float'), 0.4)

    def test_strings_keep_today_s_behaviour(self):
        self.assertIsNone(parse_typed_text('', 'str'))
        self.assertEqual(parse_typed_text('pause', 'str'), 'pause')

    def test_the_null_round_trip_is_stable(self):
        self.assertIsNone(parse_typed_text(display_text(None), 'int'))


class SaveSettingsWindowTests(unittest.TestCase):
    """The real window on the offscreen platform: a bad number changes nothing."""

    def setUp(self):
        from unittest import mock
        from PyQt5.QtWidgets import QComboBox, QLineEdit
        from utils import ConfigManager
        import ui.settings_window as sw
        _support.qt_app()
        self.mock, self.ConfigManager, self.sw = mock, ConfigManager, sw
        self.QComboBox, self.QLineEdit = QComboBox, QLineEdit
        self.window = sw.SettingsWindow()
        self.addCleanup(self.window.deleteLater)

    def field(self, cls, name):
        return self.window.findChild(cls, name)

    def test_an_invalid_number_warns_and_writes_nothing(self):
        before = self.ConfigManager.get_config_value('recording_options', 'sample_rate')
        self.field(self.QLineEdit, 'recording_options_sample_rate_input').setText('fast')
        self.field(self.QLineEdit, 'recording_options_silence_duration_input').setText('1234')
        with self.mock.patch.object(self.sw.QMessageBox, 'warning') as warning, \
             self.mock.patch.object(self.ConfigManager, 'set_config_value') as set_value, \
             self.mock.patch.object(self.ConfigManager, 'save_config') as save:
            self.window.save_settings()
        warning.assert_called_once()
        self.assertIn('Sample rate', warning.call_args[0][2])
        set_value.assert_not_called()
        save.assert_not_called()
        self.assertEqual(self.ConfigManager.get_config_value('recording_options', 'sample_rate'), before)

    def test_the_model_combobox_shows_the_configured_model_not_the_first_option(self):
        keys = ('model_options', 'local', 'model')
        before = self.ConfigManager.get_config_value(*keys)
        self.addCleanup(self.ConfigManager.set_config_value, before, *keys)
        for model in ('large-v3-turbo', 'my-finetune'):
            self.ConfigManager.set_config_value(model, *keys)
            window = self.sw.SettingsWindow()
            self.addCleanup(window.deleteLater)
            combo = window.findChild(self.QComboBox, 'model_options_local_model_input')
            self.assertEqual(combo.currentText(), model)
            self.assertEqual(window.get_widget_value_typed(combo, 'str'), model)


if __name__ == '__main__':
    unittest.main()
