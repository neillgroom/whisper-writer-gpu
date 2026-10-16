import os
import time
import traceback
import numpy as np
import sounddevice as sd
import tempfile
import wave
import webrtcvad
from PyQt5.QtCore import QThread, QMutex, pyqtSignal
from collections import deque
from threading import Event

from audio_devices import extra_settings
from transcription import transcribe
from utils import ConfigManager

WAKE_TAIL_GUARD_S = 0.4  # start of a wake recording ignored as the tail of the wake word


class ResultThread(QThread):
    """
    A thread class for handling audio recording, transcription, and result processing.

    This class manages the entire process of:
    1. Recording audio from the microphone
    2. Detecting speech and silence
    3. Saving the recorded audio as numpy array
    4. Transcribing the audio
    5. Emitting the transcription result

    Signals:
        statusSignal: Emits the current status of the thread (e.g., 'recording', 'transcribing', 'idle')
        resultSignal: Emits the transcription result
    """

    statusSignal = pyqtSignal(str)
    resultSignal = pyqtSignal(str)

    def __init__(self, local_model=None, source='key', device=None):
        """
        Initialize the ResultThread.

        :param local_model: Local transcription model (if applicable)
        :param source: 'key' for the activation key, 'wake' for the wake word
        :param device: input device index for a wake recording; key recordings use the configured device
        """
        super().__init__()
        self.local_model = local_model
        self.source = source
        self.device = device
        self.is_recording = False
        self.is_running = True
        self.sample_rate = None
        self.mutex = QMutex()

    def stop_recording(self):
        """Stop the current recording session."""
        self.mutex.lock()
        self.is_recording = False
        self.mutex.unlock()

    def stop(self):
        """Stop the entire thread execution."""
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.statusSignal.emit('idle')
        self.wait()

    def run(self):
        """Main execution method for the thread."""
        try:
            if not self.is_running:
                return

            self.mutex.lock()
            self.is_recording = True
            self.mutex.unlock()

            self.statusSignal.emit('recording')
            ConfigManager.console_print('Recording...')
            audio_data = self._record_audio()

            if not self.is_running:
                return

            if audio_data is None:
                self.statusSignal.emit('idle')
                return

            self.statusSignal.emit('transcribing')
            ConfigManager.console_print('Transcribing...')

            # Time the transcription process
            start_time = time.time()
            result = transcribe(audio_data, self.local_model)
            end_time = time.time()

            transcription_time = end_time - start_time
            ConfigManager.console_print(f'Transcription completed in {transcription_time:.2f} seconds. Post-processed line: {result}')

            # Persist every transcription: launched via pythonw.exe, console output
            # is discarded, so without this file a lost paste is unrecoverable.
            if result:
                try:
                    from datetime import datetime
                    history_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'history.log')
                    with open(history_path, 'a', encoding='utf-8') as hf:
                        hf.write(f'{datetime.now():%Y-%m-%d %H:%M:%S}\t{result}\n')
                except Exception:
                    pass  # history is best-effort; never block the paste

            if not self.is_running:
                return

            self.statusSignal.emit('idle')
            self.resultSignal.emit(result)

        except Exception as e:
            traceback.print_exc()
            self.statusSignal.emit('error')
            self.resultSignal.emit('')
        finally:
            self.stop_recording()

    def _record_audio(self):
        """
        Record audio from the microphone and save it to a temporary file.

        :return: numpy array of audio data, or None if the recording is too short
        """
        recording_options = ConfigManager.get_config_section('recording_options')
        self.sample_rate = recording_options.get('sample_rate') or 16000
        frame_duration_ms = 30  # 30ms frame duration for WebRTC VAD
        frame_size = int(self.sample_rate * (frame_duration_ms / 1000.0))
        silence_duration_ms = recording_options.get('silence_duration') or 900
        silence_frames = int(silence_duration_ms / frame_duration_ms)

        # 150ms delay before starting VAD to avoid mistaking the sound of key pressing for voice
        initial_frames_to_skip = int(0.15 * self.sample_rate / frame_size)

        # A wake recording is always one-shot VAD with its own deadlines, whatever the
        # configured mode: nobody is holding a key to end it.
        is_wake = self.source == 'wake'
        if is_wake:
            wake_options = ConfigManager.get_config_section('wake_options')
            recording_mode = 'voice_activity_detection'
            device = self.device
            no_speech_ms = wake_options.get('no_speech_timeout_ms')
            hard_deadline = wake_options.get('max_recording_s')
            # `is None`, not `or`: a configured 0 is a real (if strict) value.
            # The no-speech window opens when the wake-word guard below closes.
            no_speech_deadline = WAKE_TAIL_GUARD_S + (4000 if no_speech_ms is None else no_speech_ms) / 1000.0
            hard_deadline = 60 if hard_deadline is None else hard_deadline
            # The detector fires as "Jarvis" ends, so the first moments of a wake recording
            # are the tail of the wake word. Neither record them nor let them count as speech;
            # otherwise a short pause after the wake word would type the tail.
            initial_frames_to_skip = int(WAKE_TAIL_GUARD_S * self.sample_rate / frame_size)
        else:
            recording_mode = recording_options.get('recording_mode') or 'continuous'
            device = recording_options.get('sound_device')

        # Create VAD only for recording modes that use it
        vad = None
        speech_detected = False
        if recording_mode in ('voice_activity_detection', 'continuous'):
            vad = webrtcvad.Vad(2)  # VAD aggressiveness: 0 to 3, 3 being the most aggressive
            silent_frame_count = 0

        audio_buffer = deque(maxlen=frame_size)
        recording = []

        data_ready = Event()

        def audio_callback(indata, frames, time, status):
            if status:
                ConfigManager.console_print(f"Audio callback status: {status}")
            audio_buffer.extend(indata[:, 0])
            data_ready.set()

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16',
                            blocksize=frame_size, device=device,
                            extra_settings=extra_settings(device) if is_wake else None,
                            callback=audio_callback):
            # Deadlines count from when the mic is actually open; opening a Bluetooth
            # input can take a noticeable part of a second.
            started = time.monotonic()
            while self.is_running and self.is_recording:
                # Bounded wait: a stalled callback (Bluetooth drop) must not hang the loop
                # past the wake deadlines.
                got_data = data_ready.wait(timeout=0.25)
                if is_wake:
                    elapsed = time.monotonic() - started
                    if not speech_detected and elapsed > no_speech_deadline:
                        ConfigManager.console_print('Wake recording: no speech before the deadline.')
                        return None
                    if elapsed > hard_deadline:
                        if not speech_detected:
                            return None
                        ConfigManager.console_print('Wake recording: hit the maximum length.')
                        break
                if not got_data:
                    continue
                data_ready.clear()

                if len(audio_buffer) < frame_size:
                    continue

                # Save frame
                frame = np.array(list(audio_buffer), dtype=np.int16)
                audio_buffer.clear()

                # Avoid trying to detect voice in initial frames
                if initial_frames_to_skip > 0:
                    initial_frames_to_skip -= 1
                    if not is_wake:
                        recording.extend(frame)
                    continue
                recording.extend(frame)

                if vad:
                    if vad.is_speech(frame.tobytes(), self.sample_rate):
                        silent_frame_count = 0
                        if not speech_detected:
                            ConfigManager.console_print("Speech detected.")
                            speech_detected = True
                    else:
                        silent_frame_count += 1

                    if speech_detected and silent_frame_count > silence_frames:
                        break

        audio_data = np.array(recording, dtype=np.int16)
        duration = len(audio_data) / self.sample_rate

        ConfigManager.console_print(f'Recording finished. Size: {audio_data.size} samples, Duration: {duration:.2f} seconds')

        min_duration_ms = recording_options.get('min_duration') or 100

        if (duration * 1000) < min_duration_ms:
            ConfigManager.console_print(f'Discarded due to being too short.')
            return None

        return audio_data
