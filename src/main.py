import os
import sys
import time

if sys.platform == 'win32':
    for _pkg in ('cublas', 'cudnn', 'cuda_nvrtc'):
        _dll_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'nvidia', _pkg, 'bin')
        if os.path.isdir(_dll_dir):
            os.add_dll_directory(_dll_dir)

import ctranslate2  # noqa: E402  initialize CUDA before PyQt/pynput poison the loader
import faster_whisper  # noqa: E402

from audioplayer import AudioPlayer
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from audio_devices import resolve_input
from foreground import foreground_window, is_allowed, parse_allowed
from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager
from wake_gate import WakeGate
from wake_listener import WakeListener, load_detector


class KeyBridge(QObject):
    """
    KeyListener calls back on the keyboard-hook thread. Re-emitting as signals runs
    the handlers on the Qt main thread, where all recorder state lives, and keeps
    the low-level hook from ever waiting on app work.
    """
    activated = pyqtSignal()
    deactivated = pyqtSignal()


class WhisperWriterApp(QObject):
    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        ConfigManager.initialize()

        # Set before anything can call cleanup(): on a first run the settings window
        # can restart the app before initialize_components() ever runs.
        self.key_listener = None
        self.input_simulator = None
        self.result_thread = None
        self.wake_listener = None

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.restart_app)

        if ConfigManager.config_file_exists():
            self.initialize_components()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        self.input_simulator = InputSimulator()

        self.key_bridge = KeyBridge()
        self.key_bridge.activated.connect(self.on_activation)
        self.key_bridge.deactivated.connect(self.on_deactivation)

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.key_bridge.activated.emit)
        self.key_listener.add_callback("on_deactivate", self.key_bridge.deactivated.emit)

        self.gate = WakeGate()
        self.wake_listener = None
        self.wake_device = None
        self.wake_allowed = set()

        model_options = ConfigManager.get_config_section('model_options')
        model_path = model_options.get('local', {}).get('model_path')
        self.local_model = create_local_model() if not model_options.get('use_api') else None

        self.result_thread = None

        self.main_window = MainWindow()
        self.main_window.openSettings.connect(self.settings_window.show)
        self.main_window.startListening.connect(self.key_listener.start)
        self.main_window.closeApp.connect(self.exit_app)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()
            self.status_window.closeSignal.connect(self.stop_result_thread)

        self.create_tray_icon()
        # Tray-only launch: skip the main-window popup and auto-arm the hotkey listener.
        # Summon the main window any time via the tray icon → "WhisperWriter Main Menu".
        self.key_listener.start()
        self.start_wake_listener()

    def start_wake_listener(self):
        """
        Start the always-on wake listener when enabled. Any failure leaves wake off
        with a tray message; key dictation is unaffected.
        """
        wake_options = ConfigManager.get_config_section('wake_options')
        if not wake_options.get('enabled'):
            return
        try:
            # Device enumeration itself can throw if the audio backend is unhappy; keep it
            # inside the guard so any failure disables wake rather than taking down dictation.
            device = resolve_input(wake_options.get('laptop_match'))
            if device is None:
                self.notify('Wake word off', f'No connected mic matches "{wake_options.get("laptop_match")}".', warning=True)
                return
            detector = load_detector(wake_options.get('wake_model'), float(wake_options.get('threshold')))
        except Exception as error:  # missing files, an unloadable model, or an audio-backend failure
            self.notify('Wake word off', str(error), warning=True)
            return
        self.wake_device = device
        self.wake_allowed = parse_allowed(wake_options.get('allowed_processes'))
        self.wake_listener = WakeListener(detector, device)
        self.wake_listener.wakeSignal.connect(self.on_wake)
        self.wake_listener.errorSignal.connect(self.on_wake_error)
        self.wake_listener.start()
        ConfigManager.console_print(f'Wake listener started on device {device}.')

    def notify(self, title, message, warning=False):
        icon = QSystemTrayIcon.Warning if warning else QSystemTrayIcon.Information
        self.tray_icon.showMessage(title, message, icon, 8000)

    def on_wake(self, detected_at, score):
        """
        Called on the main thread for each detected wake word. Starts a one-shot
        recording only when nothing owns the recorder, the wake is newer than the
        last recording, and a terminal is the foreground window.
        """
        if not self.gate.accept_wake(detected_at):
            return
        hwnd = foreground_window()
        if not is_allowed(hwnd, self.wake_allowed):
            return
        ConfigManager.console_print(f'Wake word detected (score {score:.2f}).')
        self.start_result_thread(source='wake', device=self.wake_device, hwnd=hwnd)

    def on_wake_error(self, message):
        # The listener thread is exiting. Drop the reference only once it has finished:
        # destroying a QThread that is still running aborts the whole process.
        if self.wake_listener and self.wake_listener.wait(2000):
            self.wake_listener = None
        self.notify('Wake word stopped', message, warning=True)

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo.png')), self.app)

        tray_menu = QMenu()

        show_action = QAction('WhisperWriter Main Menu', self.app)
        show_action.triggered.connect(self.main_window.show)
        tray_menu.addAction(show_action)

        settings_action = QAction('Open Settings', self.app)
        settings_action.triggered.connect(self.settings_window.show)
        tray_menu.addAction(settings_action)

        exit_action = QAction('Exit', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def cleanup(self):
        if self.wake_listener:
            if not self.wake_listener.stop():
                # The process is exiting and the audio device would not let go even after
                # abort. A QThread still running when Qt destroys it aborts the process,
                # so end it here as the last resort.
                self.wake_listener.terminate()
                self.wake_listener.wait(1000)
            self.wake_listener = None
        self.stop_result_thread()
        if self.key_listener:
            self.key_listener.stop()
        if self.input_simulator:
            self.input_simulator.cleanup()

    def exit_app(self):
        """
        Exit the application.
        """
        self.cleanup()
        QApplication.quit()

    def restart_app(self):
        """Restart the application to apply the new settings."""
        self.cleanup()
        QApplication.quit()
        QProcess.startDetached(sys.executable, sys.argv)

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not os.path.exists(os.path.join('src', 'config.yaml')):
            QMessageBox.information(
                self.settings_window,
                'Using Default Values',
                'Settings closed without saving. Default values are being used.'
            )
            self.initialize_components()

    def on_activation(self):
        """
        Called when the activation key combination is pressed.
        """
        if self.gate.keys_ignored():
            return
        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        self.start_result_thread()

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if self.gate.keys_ignored():
            return
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self, source='key', device=None, hwnd=None):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self.result_thread = ResultThread(self.local_model, source=source, device=device)
        self.gate.begin(source, self.result_thread, hwnd)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        # finished fires on every exit path (result, too short, no speech, error, stop),
        # so the recorder is always released and the wake path re-armed.
        self.result_thread.finished.connect(self.on_result_finished)
        self.result_thread.start()

    def on_result_finished(self):
        self.gate.finish(self.sender(), time.monotonic())

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, type the result and start listening for the activation key again.
        """
        source = getattr(self.sender(), 'source', 'key')
        if source == 'wake':
            self.type_wake_transcript(result)
        else:
            self.input_simulator.typewrite(result)

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

        # Only a key recording re-arms continuous mode; a wake recording is always one-shot.
        if source == 'key' and ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def type_wake_transcript(self, result):
        """
        Type a wake transcript only into the window that was in front when the wake
        word was heard, re-checking before every keystroke. Whatever could not be
        typed is shown in a tray message (it is also in history.log).
        """
        if not result.strip():
            return
        hwnd = self.gate.hwnd
        remainder = self.input_simulator.typewrite(result, should_continue=lambda: foreground_window() == hwnd)
        if remainder.strip():
            self.notify('Wake dictation not typed', f'The window changed. Untyped: {remainder.strip()}', warning=True)

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    app = WhisperWriterApp()
    app.run()
