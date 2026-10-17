"""
Resolve microphones by name rather than by index.

Bluetooth inputs get a new index every time they reconnect, so the wake path
always looks a device up by a substring of its WASAPI name. WASAPI lists an
input only while it is connected; a miss is the normal disconnected state.
"""
import sounddevice as sd

from utils import ConfigManager


def resolve_input(substring, devices=None, hostapis=None):
    """
    Return the index of the first WASAPI input whose name contains `substring`
    (case-insensitive), or None. Output-only endpoints never match.
    """
    if not substring:
        return None
    devices = sd.query_devices() if devices is None else devices
    hostapis = sd.query_hostapis() if hostapis is None else hostapis
    wasapi = next((i for i, h in enumerate(hostapis) if 'WASAPI' in h['name']), None)
    if wasapi is None:
        return None
    needle = substring.lower()
    matches = [i for i, d in enumerate(devices)
               if d['hostapi'] == wasapi and d['max_input_channels'] > 0 and needle in d['name'].lower()]
    if len(matches) > 1:
        # console_print, not print: launched via pythonw.exe, bare stdout is discarded.
        ConfigManager.console_print(
            f'Mic "{substring}" matched devices {matches}; using {matches[0]}: {devices[matches[0]]["name"]}')
    return matches[0] if matches else None


def extra_settings(device):
    """
    WASAPI resamples to our 16 kHz only when asked. A resolved index always gets
    auto_convert; device=None (the system default via MME) gets nothing, as before.
    """
    if device is None:
        return None
    return sd.WasapiSettings(auto_convert=True)
