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
        self.assertEqual(bad, ('silence_duration', 'must be a whole number'))
        self.assertEqual(later, [])  # stops at the first bad field

    def test_a_blank_required_number_is_rejected_with_its_name(self):
        readers = [('silence_duration', lambda: parse_typed_text('', 'int', nullable=False))]
        self.assertEqual(stage_values(readers), (None, ('silence_duration', 'cannot be empty')))


class DisplayTextTests(unittest.TestCase):
    def test_null_shows_as_an_empty_field(self):
        self.assertEqual(display_text(None), '')

    def test_real_values_keep_their_text(self):
        self.assertEqual(display_text(0), '0')
        self.assertEqual(display_text(15), '15')
        self.assertEqual(display_text(0.5), '0.5')


class ParseTypedTextTests(unittest.TestCase):
    def test_empty_nullable_numeric_saves_as_none(self):
        for value_type in ('int', 'float'):
            self.assertIsNone(parse_typed_text('', value_type, nullable=True))
            self.assertIsNone(parse_typed_text('  ', value_type, nullable=True))

    def test_empty_non_nullable_numeric_is_rejected(self):
        for value_type in ('int', 'float'):
            for text in ('', '  '):
                with self.assertRaises(ValueError):
                    parse_typed_text(text, value_type, nullable=False)

    def test_nan_and_infinity_are_rejected(self):
        for text in ('nan', 'NaN', 'inf', '-inf', 'Infinity', '1e999'):
            with self.assertRaises(ValueError, msg=text):
                parse_typed_text(text, 'float')

    def test_non_numeric_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_typed_text('abc', 'int')
        with self.assertRaises(ValueError):
            parse_typed_text('1.5', 'int')
        with self.assertRaises(ValueError):
            parse_typed_text('abc', 'float')

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

    def save_patched(self):
        m = self.mock
        return (m.patch.object(self.sw.QMessageBox, 'warning'),
                m.patch.object(self.sw.QMessageBox, 'information'),
                m.patch.object(self.ConfigManager, 'set_config_value'),
                m.patch.object(self.ConfigManager, 'save_config'),
                m.patch.object(self.sw, 'set_key'))

    def test_a_blank_non_nullable_number_warns_and_writes_nothing(self):
        self.field(self.QLineEdit, 'recording_options_sample_rate_input').setText('')
        p = self.save_patched()
        with p[0] as warning, p[1], p[2] as set_value, p[3] as save, p[4]:
            self.window.save_settings()
        self.assertIn("'Sample rate' cannot be empty", warning.call_args[0][2])
        set_value.assert_not_called()
        save.assert_not_called()

    def test_a_blank_nullable_number_saves_as_none(self):
        self.field(self.QLineEdit, 'recording_options_sound_device_input').setText('')
        import os
        env_before = dict(os.environ)
        p = self.save_patched()
        # save_settings writes os.environ['OPENAI_API_KEY']; patch.dict restores the environment.
        with p[0] as warning, p[1], p[2] as set_value, p[3] as save, p[4], \
             self.mock.patch.object(self.window, 'close'), \
             self.mock.patch.dict(os.environ):
            self.window.save_settings()
            self.assertIn('OPENAI_API_KEY', os.environ)  # the write really happens in here
        warning.assert_not_called()
        set_value.assert_any_call(None, 'recording_options', 'sound_device')
        save.assert_called_once()
        self.assertEqual(dict(os.environ), env_before)  # nothing leaks into later tests

    def test_reset_keeps_an_unknown_model_and_tolerates_none(self):
        combo = self.field(self.QComboBox, 'model_options_local_model_input')
        keys = ('model_options', 'local', 'model')
        before = self.ConfigManager.get_config_value(*keys)
        self.addCleanup(self.ConfigManager.set_config_value, before, *keys)
        self.ConfigManager.set_config_value('my-finetune', *keys)
        with self.mock.patch.object(self.ConfigManager, 'reload_config'):
            self.window.reset_settings()
        self.assertEqual(combo.currentText(), 'my-finetune')
        self.window.set_widget_value(combo, None, 'str')  # setCurrentText(None) would raise TypeError
        self.assertEqual(combo.currentText(), 'my-finetune')
        self.window.set_widget_value(combo, 'my-finetune', 'str')
        items = [combo.itemText(i) for i in range(combo.count())]
        self.assertEqual(items.count('my-finetune'), 1)  # not appended twice


if __name__ == '__main__':
    unittest.main()
