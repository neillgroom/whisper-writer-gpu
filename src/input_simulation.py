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
_vk_key_scan = None
if sys.platform == 'win32':
    _vk_key_scan = ctypes.WinDLL('user32').VkKeyScanW
    _vk_key_scan.argtypes = [ctypes.c_wchar]
    _vk_key_scan.restype = ctypes.c_short

SHIFT_ONLY = 1  # VkKeyScanW modifier byte: Shift and nothing else


def layout_key(char):
    """
    Ask the active keyboard layout how to type `char`: returns (vk, modifiers)
    where modifiers is the VkKeyScanW high byte (1 Shift, 2 Ctrl, 4 Alt), or
    None when the layout has no key for it, the char is not a single UTF-16
    unit, or the platform is not Windows.
    """
    if _vk_key_scan is None or len(char) != 1 or ord(char) > 0xFFFF:
        return None
    result = _vk_key_scan(char)
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

        Each character is looked up in the active layout (layout_key). When the
        layout types it with Shift alone (capitals, '?', '!', ':' on US), a real
        Shift down + the layout's virtual key + Shift up is sent: pynput's default
        sends those as VK_PACKET Unicode events, which terminals and TUIs often
        drop. Anything else -- no modifier, a Ctrl/Alt (AltGr) combination, no key
        on the layout, or not Windows -- is pressed as the original character,
        as upstream did.

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
        for index, char in enumerate(text):
            if index and should_continue is not None and not should_continue():
                return text[index:]
            mapped = layout_key(char)
            if mapped is not None and mapped[1] == SHIFT_ONLY:
                key = KeyCode.from_vk(mapped[0])
                # pressed() guarantees the Shift release even if the press raises.
                with self.keyboard.pressed(Key.shift):
                    self.keyboard.press(key)
                    self.keyboard.release(key)
            else:
                self.keyboard.press(char)
                self.keyboard.release(char)
            time.sleep(interval)
        return ''

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
