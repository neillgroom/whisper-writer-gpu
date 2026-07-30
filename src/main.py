import os
import re
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
from PyQt5.QtCore import QObject, QProcess
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from command_client import BrokerError, CommandBrokerClient
from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager


class WhisperWriterApp(QObject):
    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        ConfigManager.initialize()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.restart_app)

        self.command_broker = None
        self.recording_target = 'dictation'

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
        self.command_broker = CommandBrokerClient()

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)

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

        self.create_tray_icon()
        # Tray-only launch: skip the main-window popup and auto-arm the hotkey listener.
        # Summon the main window any time via the tray icon → "WhisperWriter Main Menu".
        self.key_listener.start()

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
        if getattr(self, 'key_listener', None):
            self.key_listener.stop()
        if getattr(self, 'input_simulator', None):
            self.input_simulator.cleanup()
        if self.command_broker:
            self.command_broker.close()
            self.command_broker = None

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
        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        # Ctrl+Pause is the explicit command channel. Pause alone remains dictation.
        self.recording_target = 'command' if self.key_listener.ctrl_is_pressed() else 'dictation'
        print(f'Voice activation: {self.recording_target.upper()} mode')
        self.start_result_thread()

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self.result_thread = ResultThread(self.local_model)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    @staticmethod
    def strip_helene_prefix(result):
        match = re.match(r'^\s*helene[\s,;:!-]*(.*)$', result, flags=re.IGNORECASE)
        return match.group(1).strip() if match else None

    def handle_command(self, command):
        try:
            response = self.command_broker.route(command)
            message = response.get('message', 'Done')
            print(f'Helene command result: {message}')
            self.tray_icon.showMessage(
                'Helene',
                message,
                QSystemTrayIcon.Information,
                3000,
            )
        except (BrokerError, OSError, ValueError) as error:
            print(f'Helene command failed: {error}')
            self.tray_icon.showMessage(
                'Helene command failed',
                str(error),
                QSystemTrayIcon.Critical,
                5000,
            )

    def on_transcription_complete(self, result):
        """
        Route commands to the broker; otherwise type the transcription at the cursor.
        """
        prefixed_command = self.strip_helene_prefix(result)
        if self.recording_target == 'command' or prefixed_command is not None:
            command = prefixed_command if prefixed_command is not None else result.strip()
            if command:
                self.handle_command(command)
        else:
            self.input_simulator.typewrite(result)

        self.recording_target = 'dictation'

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    app = WhisperWriterApp()
    app.run()
