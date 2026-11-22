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
import ctypes
import sys

import sounddevice as sd

# Bound once at import on a private WinDLL, so argtypes/restype never touch the shared
# windll.ole32 object other code (WakeListener) uses.
_co_initialize_ex = _co_uninitialize = None
if sys.platform == 'win32':
    _ole32 = ctypes.WinDLL('ole32')
    _co_initialize_ex = _ole32.CoInitializeEx
    _co_initialize_ex.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    _co_initialize_ex.restype = ctypes.c_long  # HRESULT
    _co_uninitialize = _ole32.CoUninitialize
    _co_uninitialize.argtypes = []
    _co_uninitialize.restype = None

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
    WASAPI resamples to our 16 kHz only when asked. An index on the WASAPI host
    API gets auto_convert. device=None (the system default via MME), a non-WASAPI
    index (WasapiSettings there makes the stream open raise PortAudioError), and a
    stale, out-of-range or negative index get nothing. Never raises.
    """
    if device is None:
        return None
    try:
        devices = sd.query_devices()
        if not 0 <= device < len(devices):
            return None
        hostapi = sd.query_hostapis()[devices[device]['hostapi']]
        if 'WASAPI' not in hostapi['name']:
            return None
        return sd.WasapiSettings(auto_convert=True)
    except Exception as error:
        ConfigManager.console_print(f'Host API lookup for device {device} failed: {error}')
        return None


def com_initialize():
    """
    Initialize COM (multithreaded) on the calling worker thread, the way
    WakeListener does: a Qt app leaves the main thread's apartment in STA, and a
    worker that opens a WASAPI stream without its own COM init fails with
    PortAudio -9999. Returns True when the caller holds a reference that
    com_uninitialize() must release. Non-Windows: False, nothing called.
    """
    if _co_initialize_ex is None:
        return False
    COINIT_MULTITHREADED = 0x0
    hr = _co_initialize_ex(None, COINIT_MULTITHREADED)
    # S_OK (0) and S_FALSE (1, already initialized here) both need a CoUninitialize;
    # RPC_E_CHANGED_MODE (negative) means another apartment owns this thread -- do not.
    return hr in (0, 1)


def com_uninitialize(held):
    """Release the reference com_initialize() returned True for."""
    if held and _co_uninitialize is not None:
        _co_uninitialize()
