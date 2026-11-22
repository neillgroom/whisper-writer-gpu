import subprocess
import os
import signal
import time
from pynput.keyboard import Controller as PynputController

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
        elif self.input_method == 'paste':
            # Paste delivery still needs a controller for the Ctrl+V keystroke.
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
        if self.input_method == 'paste':
            return self._typewrite_paste(text, should_continue)
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

        Capitals and other Shift-requiring characters are sent as a real
        Shift+key press. pynput's default path sends them as VK_PACKET Unicode
        events, which terminals and TUIs frequently drop -- the missing-initial-
        capital "esting" gibberish. Real VK events work everywhere.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
            should_continue (callable): Optional check run before every keystroke.

        Returns:
            str: The untyped remainder.
        """
        from pynput.keyboard import Key
        shift = Key.shift
        # Defensive: if a previous typing pass was interrupted mid-capital, its
        # Shift press may still be latched. A stuck Shift turns every later
        # keystroke into selection-extension, so the next text REPLACES the
        # selection instead of inserting -- "it deletes my text". Releasing an
        # unpressed key is a no-op, so this is always safe.
        self.keyboard.release(shift)
        for index, char in enumerate(text):
            if should_continue is not None and not should_continue():
                return text[index:]
            if char.isupper():
                # pressed() guarantees the Shift release even if the press of
                # the character raises, so a latch can never survive an error.
                with self.keyboard.pressed(shift):
                    self.keyboard.press(char.lower())
                    self.keyboard.release(char.lower())
            else:
                self.keyboard.press(char)
                self.keyboard.release(char)
            time.sleep(interval)
        return ''

    def _typewrite_paste(self, text, should_continue=None):
        """
        Deliver `text` as one clipboard paste: copy, Ctrl+V.

        The previous clipboard contents are NOT restored on a timer: a slow or
        busy target processes the injected Ctrl+V late, and a mid-flight restore
        would make it paste the OLD clipboard instead of the transcript. Instead,
        whatever we borrowed is given back at the START of the next paste, when
        nothing is waiting to read it.

        Per-key injection drops characters on some targets -- pynput sends every
        Shift-requiring character (capitals, punctuation) as a VK_PACKET Unicode
        event, which terminals and TUIs often mishandle -- so the whole line
        arrives as a single paste instead.
        """
        import pyperclip
        from pynput.keyboard import Key

        if should_continue is not None and not should_continue():
            return text
        backup = getattr(self, '_paste_backup', None)
        if backup is not None:
            try:
                pyperclip.copy(backup)
            except Exception:
                pass
            self._paste_backup = None
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        pyperclip.copy(text)
        # A clipboard locked by another process can swallow the write; verify
        # the read-back matches before pasting, retry briefly, else give up
        # with the remainder rather than paste stale content.
        for _ in range(4):
            if pyperclip.paste() == text:
                break
            time.sleep(0.05)
            pyperclip.copy(text)
        else:
            return text
        self._paste_backup = previous
        with self.keyboard.pressed(Key.ctrl):
            self.keyboard.press('v')
            self.keyboard.release('v')
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
