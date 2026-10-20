"""
Resolve microphones by name rather than by index.

Bluetooth inputs get a new index every time they reconnect, so the wake path
always looks a device up by a substring of its WASAPI name. WASAPI lists an
input only while it is connected; a miss is the normal disconnected state. On a
tie (several inputs match) an exact name match wins, else the lowest index.

resolve_input never raises: a backend that cannot be queried returns None, the
same as a disconnected mic, so the always-on listener and the wake callback can
treat it as "no mic right now" and retry rather than die.
"""
import sounddevice as sd

from utils import ConfigManager


def resolve_input(substring, devices=None, hostapis=None):
    """
    Return the index of a WASAPI input whose name contains `substring`
    (case-insensitive), or None. On a tie, prefer an exact name match, else the
    lowest index. Output-only endpoints never match. Never raises: a backend that
    cannot be queried returns None, the same as a disconnected mic.
    """
    if not substring:
        return None
    try:
        devices = sd.query_devices() if devices is None else devices
        hostapis = sd.query_hostapis() if hostapis is None else hostapis
        wasapi = next((i for i, h in enumerate(hostapis) if 'WASAPI' in h['name']), None)
        if wasapi is None:
            return None
        needle = substring.lower()
        matches = [i for i, d in enumerate(devices)
                   if d['hostapi'] == wasapi and d['max_input_channels'] > 0 and needle in d['name'].lower()]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        # Ambiguous: prefer an exact (case-insensitive) name match; else the lowest index, so the
        # choice is deterministic rather than silently first-wins. console_print, not print: launched
        # via pythonw.exe, bare stdout is discarded.
        exact = [i for i in matches if devices[i]['name'].lower() == needle]
        chosen = exact[0] if exact else matches[0]
        ConfigManager.console_print(
            f'Mic "{substring}" matched {len(matches)} inputs {matches}; using {chosen}: '
            f'{devices[chosen]["name"]}. Set wake_options.laptop_match to a more specific name to disambiguate.')
        return chosen
    except Exception as error:
        # A flaky audio backend must look like "no mic right now" -- callers retry -- and must
        # never let an enumeration error escape into the listener thread or the Qt wake callback.
        ConfigManager.console_print(f'Mic lookup for "{substring}" failed: {error}')
        return None


def extra_settings(device):
    """
    WASAPI resamples to our 16 kHz only when asked. A resolved index always gets
    auto_convert; device=None (the system default via MME) gets nothing, as before.
    """
    if device is None:
        return None
    return sd.WasapiSettings(auto_convert=True)
