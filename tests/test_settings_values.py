import unittest

import _support  # noqa: F401
from ui.settings_window import display_text, parse_typed_text


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


if __name__ == '__main__':
    unittest.main()
