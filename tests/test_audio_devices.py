import unittest
from unittest import mock

import sounddevice as sd

import _support  # noqa: F401
import audio_devices
from audio_devices import extra_settings, resolve_input

HOSTAPIS = [{'name': 'MME'}, {'name': 'Windows DirectSound'}, {'name': 'Windows WASAPI'}, {'name': 'Windows WDM-KS'}]


def dev(name, hostapi, inputs, outputs=0):
    return {'name': name, 'hostapi': hostapi, 'max_input_channels': inputs, 'max_output_channels': outputs}


DEVICES = [
    dev('Microphone Array (2- Realtek(R)', 0, 2),                     # 0 MME, truncated
    dev('Speakers (KVM USB Audio)', 2, 0, 2),                          # 1 WASAPI output
    dev('Microphone Array (2- Realtek(R) Audio)', 2, 2),               # 2 WASAPI input
    dev('Headphones (AirPods Pro - Find My)', 2, 0, 2),                # 3 WASAPI output-only AirPods
    dev('Headset (AirPods Pro - Find My)', 3, 1),                      # 4 WDM-KS input
    dev('Headset (AirPods Pro - Find My)', 2, 1),                      # 5 WASAPI input
    dev('Microphone Array (Realtek HD Audio Mic Array input)', 2, 2),  # 6 second WASAPI match
]


class ResolveInputTests(unittest.TestCase):
    def resolve(self, needle, devices=DEVICES):
        return resolve_input(needle, devices=devices, hostapis=HOSTAPIS)

    def test_only_wasapi_inputs_match(self):
        self.assertEqual(self.resolve('airpods'), 5)

    def test_output_only_endpoint_never_matches(self):
        self.assertIsNone(self.resolve('AirPods', devices=DEVICES[:5]))

    def test_multiple_matches_take_the_lowest_index(self):
        self.assertEqual(self.resolve('Microphone Array'), 2)

    def test_missing_or_blank_returns_none(self):
        self.assertIsNone(self.resolve('JLab'))
        self.assertIsNone(self.resolve(''))
        self.assertIsNone(self.resolve(None))

    def test_no_wasapi_host_returns_none(self):
        self.assertIsNone(resolve_input('Microphone', devices=DEVICES, hostapis=[{'name': 'MME'}]))

    def test_an_exact_name_match_wins_the_tie(self):
        devices = DEVICES + [dev('microphone array', 2, 2)]  # exact (case-insensitive) WASAPI input
        self.assertEqual(self.resolve('Microphone Array', devices=devices), 7)

    def test_a_backend_error_returns_none_instead_of_raising(self):
        # A flaky backend must look like "no mic" so the listener/callback retry rather than die.
        with mock.patch.object(audio_devices.sd, 'query_devices', side_effect=OSError('backend')):
            self.assertIsNone(resolve_input('Microphone Array'))


class ExtraSettingsTests(unittest.TestCase):
    def test_auto_convert_only_for_a_resolved_index(self):
        self.assertIsNone(extra_settings(None))
        self.assertIsInstance(extra_settings(2), sd.WasapiSettings)


if __name__ == '__main__':
    unittest.main()
