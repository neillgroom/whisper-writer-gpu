import ctypes
import subprocess
import os
import signal
import sys
import time
from pynput.keyboard import Controller as PynputController, Key, KeyCode

from utils import ConfigManager

def run_command_or_exit_on_failure(command):
    """
    Run a shell command and exit if it fails.

    Args:
        command (list): The command to run as a list of strings.
    """
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        exit(1)


# Bound once at import. A private WinDLL instance so setting argtypes/restype does not
# mutate the shared windll.user32 object other code uses.
_vk_key_scan = _vk_key_scan_ex = _get_key_state = None
_get_foreground_window = _get_window_thread_process_id = _get_keyboard_layout = None
if sys.platform == 'win32':
    from ctypes import wintypes
    _user32 = ctypes.WinDLL('user32')
    _vk_key_scan = _user32.VkKeyScanW
    _vk_key_scan.argtypes = [ctypes.c_wchar]
    _vk_key_scan.restype = ctypes.c_short
    _vk_key_scan_ex = _user32.VkKeyScanExW
    _vk_key_scan_ex.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
    _vk_key_scan_ex.restype = ctypes.c_short
    _get_key_state = _user32.GetKeyState
    _get_key_state.argtypes = [ctypes.c_int]
    _get_key_state.restype = ctypes.c_short
    _get_foreground_window = _user32.GetForegroundWindow
    _get_foreground_window.argtypes = []
    _get_foreground_window.restype = wintypes.HWND
    _get_window_thread_process_id = _user32.GetWindowThreadProcessId
    _get_window_thread_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _get_window_thread_process_id.restype = wintypes.DWORD
    _get_keyboard_layout = _user32.GetKeyboardLayout
    _get_keyboard_layout.argtypes = [wintypes.DWORD]
    _get_keyboard_layout.restype = ctypes.c_void_p

SHIFT_ONLY = 1  # VkKeyScan modifier byte: Shift and nothing else
VK_CAPITAL = 0x14
VK_A, VK_Z = 0x41, 0x5A


def caps_lock_on():
    """True when Caps Lock is toggled on (GetKeyState low bit). False off Windows or on error."""
    if _get_key_state is None:
        return False
    try:
        return bool(_get_key_state(VK_CAPITAL) & 1)
    except Exception:
        return False


def target_layout():
    """
    The keyboard layout (HKL) of the foreground window's thread -- the window
    the text lands in -- or None (then layout_key uses this thread's layout).
    """
    if _get_foreground_window is None:
        return None
    try:
        hwnd = _get_foreground_window()
        if not hwnd:
            return None
        thread_id = _get_window_thread_process_id(hwnd, None)
        if not thread_id:
            return None
        return _get_keyboard_layout(thread_id) or None
    except Exception:
        return None


def layout_key(char, hkl=None):
    """
    Ask a keyboard layout how to type `char`: returns (vk, modifiers) where
    modifiers is the VkKeyScan high byte (1 Shift, 2 Ctrl, 4 Alt), or None when
    the layout has no key for it, the char is not a single UTF-16 unit, or the
    platform is not Windows. `hkl` (from target_layout) selects the layout;
    None means this thread's layout.
    """
    if _vk_key_scan is None or len(char) != 1 or ord(char) > 0xFFFF:
        return None
    result = _vk_key_scan_ex(char, hkl) if hkl else _vk_key_scan(char)
    if result == -1:  # 0xFFFF as a signed short: no key on this layout
        return None
    return result & 0xFF, (result >> 8) & 0xFF


class InputSimulator:
    """
    A class to simulate keyboard input using various methods.
    """

    def __init__(self):
        """
        Initialize the InputSimulator with the specified configuration.
        """
        self.input_method = ConfigManager.get_config_value('post_processing', 'input_method')
        self.dotool_process = None

        if self.input_method == 'pynput':
            self.keyboard = PynputController()
        elif self.input_method == 'dotool':
            self._initialize_dotool()

    def _initialize_dotool(self):
        """
        Initialize the dotool process for input simulation.
        """
        self.dotool_process = subprocess.Popen("dotool", stdin=subprocess.PIPE, text=True)
        assert self.dotool_process.stdin is not None

    def _terminate_dotool(self):
        """
        Terminate the dotool process if it's running.
        """
        if self.dotool_process:
            os.kill(self.dotool_process.pid, signal.SIGINT)
            self.dotool_process = None

    def typewrite(self, text, should_continue=None):
        """
        Simulate typing the given text with the specified interval between keystrokes.

        Args:
            text (str): The text to type.
            should_continue (callable): Optional check run before every keystroke; typing
                stops at the first False.

        Returns:
            str: The part of `text` that was not typed ('' when all of it was).
        """
        interval = ConfigManager.get_config_value('post_processing', 'writing_key_press_delay')
        if self.input_method == 'pynput':
            return self._typewrite_pynput(text, interval, should_continue)
        # ydotool/dotool type the whole string in one call, so the check can only run once.
        if should_continue is not None and not should_continue():
            return text
        if self.input_method == 'ydotool':
            self._typewrite_ydotool(text, interval)
        elif self.input_method == 'dotool':
            self._typewrite_dotool(text, interval)
        return ''

    def _typewrite_pynput(self, text, interval, should_continue=None):
        """
        Simulate typing using pynput.

        Empty text returns at once. should_continue() is checked before anything
        is sent -- including the defensive Shift release -- and again before
        every character; at the first False the untyped remainder is returned.

        Then Key.shift, Key.shift_l and Key.shift_r are released: a Shift latched
        by an interrupted earlier pass would turn the next keystrokes into
        selection-extension and replace text. Releasing an unpressed key is a no-op.

        The target window's layout (target_layout) and the Caps Lock state
        (caps_lock_on) are read once per call. Each character is looked up in
        that layout (layout_key). A character typed with no modifier or Shift
        alone goes as the layout's virtual key, with Shift held exactly when
        "needs Shift" XOR "Caps Lock is on and the key is a letter (VK A-Z)" -- so
        the typed case never depends on Caps Lock, and digits/punctuation ignore
        it. When that sends no Shift and the key is not a Caps-Lock-flipped
        letter, the original character is pressed, as upstream did (pynput sends
        a modifier-free character as the same real VK). Anything else -- a
        Ctrl/Alt (AltGr) combination, no key on the layout, or not Windows -- is
        pressed as the original character.

        A character the controller refuses (pynput raises on, e.g., an emoji) is
        skipped -- its Shift, if any, is still released -- and typing continues;
        the first such failure per call is reported on the console.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
            should_continue (callable): Optional check run before every keystroke.

        Returns:
            str: The untyped remainder.
        """
        if not text:
            return ''
        if should_continue is not None and not should_continue():
            return text
        for shift in (Key.shift, Key.shift_l, Key.shift_r):
            self.keyboard.release(shift)
        hkl = target_layout()
        caps = caps_lock_on()
        reported = False
        for index, char in enumerate(text):
            if index and should_continue is not None and not should_continue():
                return text[index:]
            try:
                self._send_char(char, hkl, caps)
            except Exception as error:
                if not reported:
                    ConfigManager.console_print(f'Skipped a character the keyboard could not type: {error!r}')
                    reported = True
            time.sleep(interval)
        return ''

    def _send_char(self, char, hkl, caps):
        """Send one character (see _typewrite_pynput). Raises whatever the controller raises."""
        mapped = layout_key(char, hkl)
        if mapped is not None and mapped[1] in (0, SHIFT_ONLY):
            vk, modifiers = mapped
            flipped = caps and VK_A <= vk <= VK_Z
            if modifiers == SHIFT_ONLY or flipped:
                key = KeyCode.from_vk(vk)
                if (modifiers == SHIFT_ONLY) != flipped:
                    # pressed() guarantees the Shift release even if the press raises.
                    with self.keyboard.pressed(Key.shift):
                        self.keyboard.press(key)
                        self.keyboard.release(key)
                else:
                    self.keyboard.press(key)
                    self.keyboard.release(key)
                return
        self.keyboard.press(char)
        self.keyboard.release(char)

    def _typewrite_ydotool(self, text, interval):
        """
        Simulate typing using ydotool.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        cmd = "ydotool"
        run_command_or_exit_on_failure([
            cmd,
            "type",
            "--key-delay",
            str(interval * 1000),
            "--",
            text,
        ])

    def _typewrite_dotool(self, text, interval):
        """
        Simulate typing using dotool.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        assert self.dotool_process and self.dotool_process.stdin
        self.dotool_process.stdin.write(f"typedelay {interval * 1000}\n")
        self.dotool_process.stdin.write(f"type {text}\n")
        self.dotool_process.stdin.flush()

    def cleanup(self):
        """
        Perform cleanup operations, such as terminating the dotool process.
        """
        if self.input_method == 'dotool':
            self._terminate_dotool()
