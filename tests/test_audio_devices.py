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
    def setUp(self):
        patches = [mock.patch.object(audio_devices.sd, 'query_devices', return_value=DEVICES),
                   mock.patch.object(audio_devices.sd, 'query_hostapis', return_value=HOSTAPIS)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_none_gets_nothing(self):
        self.assertIsNone(extra_settings(None))

    def test_wasapi_index_gets_auto_convert(self):
        settings = extra_settings(2)
        self.assertIsInstance(settings, sd.WasapiSettings)
        # sounddevice keeps no public attribute; auto_convert sets paWinWasapiAutoConvert (64).
        self.assertTrue(settings._streaminfo.flags & 64)
        self.assertFalse(sd.WasapiSettings()._streaminfo.flags & 64)  # the check can fail

    def test_non_wasapi_index_gets_nothing(self):
        self.assertIsNone(extra_settings(0))  # MME
        self.assertIsNone(extra_settings(4))  # WDM-KS

    def test_stale_out_of_range_or_negative_index_gets_nothing(self):
        self.assertIsNone(extra_settings(len(DEVICES)))
        self.assertIsNone(extra_settings(99))
        self.assertIsNone(extra_settings(-1))  # would wrap to the last (WASAPI) device
        with mock.patch.object(audio_devices.sd, 'query_devices', return_value=[]):
            self.assertIsNone(extra_settings(0))

    def test_a_lookup_error_returns_none_instead_of_raising(self):
        with mock.patch.object(audio_devices.sd, 'query_devices', side_effect=OSError('backend')):
            self.assertIsNone(extra_settings(2))
        with mock.patch.object(audio_devices.sd, 'query_hostapis', side_effect=OSError('backend')):
            self.assertIsNone(extra_settings(2))


class ComTests(unittest.TestCase):
    def fake_ole(self, hr):
        ole = mock.Mock()
        ole.CoInitializeEx.return_value = hr
        for name, fake in (('_co_initialize_ex', ole.CoInitializeEx), ('_co_uninitialize', ole.CoUninitialize)):
            patcher = mock.patch.object(audio_devices, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        return ole

    def test_windows_inits_multithreaded_and_releases_when_held(self):
        ole = self.fake_ole(0)
        held = audio_devices.com_initialize()
        audio_devices.com_uninitialize(held)
        ole.CoInitializeEx.assert_called_once_with(None, 0)
        self.assertTrue(held)
        ole.CoUninitialize.assert_called_once_with()

    def test_already_initialized_still_needs_release(self):
        self.fake_ole(1)
        self.assertTrue(audio_devices.com_initialize())

    def test_changed_mode_is_not_released(self):
        ole = self.fake_ole(-2147417850)  # RPC_E_CHANGED_MODE
        held = audio_devices.com_initialize()
        audio_devices.com_uninitialize(held)
        self.assertFalse(held)
        ole.CoUninitialize.assert_not_called()

    def test_non_windows_calls_neither(self):
        # Off Windows nothing is bound at import.
        with mock.patch.object(audio_devices, '_co_initialize_ex', None), \
             mock.patch.object(audio_devices, '_co_uninitialize', None):
            held = audio_devices.com_initialize()
            audio_devices.com_uninitialize(held)
            audio_devices.com_uninitialize(True)  # even a stray True cannot call anything
        self.assertFalse(held)

    def test_the_binding_is_private_with_types(self):
        if audio_devices.sys.platform != 'win32':
            self.skipTest('Windows binding')
        import ctypes
        self.assertIsNot(audio_devices._co_initialize_ex, ctypes.windll.ole32.CoInitializeEx)
        self.assertEqual(audio_devices._co_initialize_ex.restype, ctypes.c_long)
        # Real round trip on this test thread: init, then release only if held.
        audio_devices.com_uninitialize(audio_devices.com_initialize())


if __name__ == '__main__':
    unittest.main()
