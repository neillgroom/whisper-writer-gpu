import atexit
import io
import json
import os
import struct
import subprocess
import sys
import numpy as np
import soundfile as sf
import re
from openai import OpenAI

from utils import ConfigManager


class RemoteModel:
    """In-process handle to a transcription_worker.py subprocess.

    Keeps the GPU model loaded in a worker that never imports PyQt5, dodging the
    Qt-event-loop / CUDA-stream deadlock seen on Windows when both run in-process.
    """

    def __init__(self, model, device, compute_type):
        worker_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'transcription_worker.py')
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'worker.log')
        self._stderr_log = open(log_path, 'wb')
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            [sys.executable, '-u', worker_path,
             '--model', model,
             '--device', device,
             '--compute-type', compute_type],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_log,
            creationflags=creationflags,
        )
        atexit.register(self.close)
        ready = self._read_json()
        if not ready or ready.get('status') != 'ready':
            raise RuntimeError(f'Transcription worker failed to start. See worker.log. Got: {ready}')

    def _read_exact(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                if self.proc.poll() is not None:
                    raise RuntimeError(f'Worker exited (code {self.proc.returncode}). See worker.log.')
                raise RuntimeError('Worker stdout closed unexpectedly.')
            buf += chunk
        return buf

    def _read_json(self):
        raw_len = self._read_exact(4)
        n = struct.unpack('!I', raw_len)[0]
        return json.loads(self._read_exact(n).decode('utf-8'))

    def _write_frame(self, data):
        self.proc.stdin.write(struct.pack('!I', len(data)))
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def transcribe(self, audio, **kwargs):
        if not isinstance(audio, np.ndarray) or audio.dtype != np.float32:
            audio = np.asarray(audio, dtype=np.float32)
        header = {'action': 'transcribe', 'dtype': str(audio.dtype), 'shape': list(audio.shape), 'params': kwargs}
        self._write_frame(json.dumps(header).encode('utf-8'))
        self._write_frame(audio.tobytes())
        resp = self._read_json()
        if 'error' in resp:
            raise RuntimeError(f"Worker error: {resp['error']}\n{resp.get('traceback', '')}")
        return resp['text']

    def close(self):
        if self.proc.poll() is None:
            try:
                self._write_frame(json.dumps({'action': 'stop'}).encode('utf-8'))
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self._stderr_log.close()
        except Exception:
            pass


def create_local_model():
    """Load the local model — in-process for CPU, out-of-process for CUDA."""
    ConfigManager.console_print('Creating local model...')
    local_model_options = ConfigManager.get_config_section('model_options')['local']
    compute_type = local_model_options['compute_type']
    device = local_model_options['device']
    model_name = local_model_options.get('model_path') or local_model_options['model']

    if device == 'cuda':
        ConfigManager.console_print(f'Spawning GPU worker for {model_name} ({compute_type})...')
        model = RemoteModel(model_name, device, compute_type)
        ConfigManager.console_print('GPU worker ready.')
        return model

    if compute_type == 'int8' and device != 'cpu':
        device = 'cpu'
        ConfigManager.console_print('Using int8 quantization, forcing CPU usage.')

    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        ConfigManager.console_print(f'Error initializing WhisperModel: {e}. Falling back to CPU.')
        model = WhisperModel(model_name, device='cpu', compute_type=compute_type)

    ConfigManager.console_print('Local model created.')
    return model

def transcribe_local(audio_data, local_model=None):
    """
    Transcribe an audio file using a local model.
    """
    if not local_model:
        local_model = create_local_model()
    model_options = ConfigManager.get_config_section('model_options')

    # Convert int16 to float32
    audio_data_float = audio_data.astype(np.float32) / 32768.0

    transcribe_kwargs = dict(
        language=model_options['common']['language'],
        initial_prompt=model_options['common']['initial_prompt'],
        condition_on_previous_text=model_options['local']['condition_on_previous_text'],
        temperature=model_options['common']['temperature'],
        vad_filter=model_options['local']['vad_filter'],
    )

    if isinstance(local_model, RemoteModel):
        return local_model.transcribe(audio_data_float, **transcribe_kwargs)

    response = local_model.transcribe(audio=audio_data_float, **transcribe_kwargs)
    return ''.join([segment.text for segment in list(response[0])])

def transcribe_api(audio_data):
    """
    Transcribe an audio file using the OpenAI API.
    """
    model_options = ConfigManager.get_config_section('model_options')
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY') or None,
        base_url=model_options['api']['base_url'] or 'https://api.openai.com/v1'
    )

    # Convert numpy array to WAV file
    byte_io = io.BytesIO()
    sample_rate = ConfigManager.get_config_section('recording_options').get('sample_rate') or 16000
    sf.write(byte_io, audio_data, sample_rate, format='wav')
    byte_io.seek(0)

    response = client.audio.transcriptions.create(
        model=model_options['api']['model'],
        file=('audio.wav', byte_io, 'audio/wav'),
        language=model_options['common']['language'],
        prompt=model_options['common']['initial_prompt'],
        temperature=model_options['common']['temperature'],
    )
    return response.text

def post_process_transcription(transcription):
    """
    Apply post-processing to the transcription.
    """
    transcription = transcription.strip()
    
    # Clean up common filler words (uh, um)
    transcription = re.sub(r'\b(uh|um)\b[,.]?', '', transcription, flags=re.IGNORECASE)
    
    # Clean up stutters (e.g. "but but but" -> "but")
    transcription = re.sub(r'\b(\w+)(?:[,\s]+\1\b)+', r'\1', transcription, flags=re.IGNORECASE)
    
    # Clean up any resulting double spaces or orphaned commas
    transcription = re.sub(r'\s{2,}', ' ', transcription)
    transcription = re.sub(r' ,', ',', transcription).strip()

    post_processing = ConfigManager.get_config_section('post_processing')
    if post_processing['remove_trailing_period'] and transcription.endswith('.'):
        transcription = transcription[:-1]
    if post_processing['add_trailing_space']:
        transcription += ' '
    if post_processing['remove_capitalization']:
        transcription = transcription.lower()

    return transcription

def transcribe(audio_data, local_model=None):
    """
    Transcribe audio date using the OpenAI API or a local model, depending on config.
    """
    if audio_data is None:
        return ''

    if ConfigManager.get_config_value('model_options', 'use_api'):
        transcription = transcribe_api(audio_data)
    else:
        transcription = transcribe_local(audio_data, local_model)

    return post_process_transcription(transcription)

