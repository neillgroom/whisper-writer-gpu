"""
Which window has the keyboard focus, and which process owns it.

Used by the wake-word path to decide whether a wake may record and whether its
transcript may still be typed. Every failure returns None, and None is never an
allowed window: the gate fails closed.
"""
import os
import sys

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL('user32', use_last_error=True)
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def foreground_window():
    """Return the HWND of the foreground window as an int, or None."""
    if sys.platform != 'win32':
        return None
    hwnd = _user32.GetForegroundWindow()
    return int(hwnd) if hwnd else None


def process_name(hwnd):
    """Return the executable basename owning `hwnd` (e.g. 'WindowsTerminal.exe'), or None."""
    if sys.platform != 'win32' or not hwnd:
        return None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.basename(buffer.value) or None
    finally:
        _kernel32.CloseHandle(handle)


def parse_allowed(allowed):
    """Turn the comma-separated config string into a set of lower-cased executable names."""
    return {name.strip().lower() for name in (allowed or '').split(',') if name.strip()}


def is_allowed(hwnd, allowed_names, name_of=process_name):
    """True only when `hwnd` resolves to a process whose name is in `allowed_names`."""
    if not hwnd:
        return False
    name = name_of(hwnd)
    return name is not None and name.lower() in allowed_names
