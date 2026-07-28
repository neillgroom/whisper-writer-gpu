"""GPU transcription worker — runs as a subprocess, isolated from the Qt event loop.

Protocol on stdin/stdout (length-prefixed; framing only, no untrusted deserialization):
  Request:  [4-byte BE length][JSON header bytes][4-byte BE length][raw audio bytes]
  Response: [4-byte BE length][JSON body bytes]

JSON header:  {"action": "transcribe"|"stop", "dtype": "float32", "shape": [N], "params": {...}}
JSON body:    {"text": "..."} or {"error": "...", "traceback": "..."} or {"status": "ready"}

Logs go to stderr (captured to worker.log by the parent).
"""
import os
import sys
import json
import struct
import argparse
import traceback


def _add_cuda_dll_dirs():
    if sys.platform != 'win32':
        return
    bin_dirs = []
    for pkg in ('cublas', 'cudnn', 'cuda_nvrtc'):
        d = os.path.join(sys.prefix, 'Lib', 'site-packages', 'nvidia', pkg, 'bin')
        if os.path.isdir(d):
            bin_dirs.append(d)
            os.add_dll_directory(d)
    # Prepend to PATH so ctranslate2's bare LoadLibrary("cublas64_12.dll") at inference time resolves.
    if bin_dirs:
        os.environ['PATH'] = os.pathsep.join(bin_dirs) + os.pathsep + os.environ.get('PATH', '')
    # Force-preload every CUDA DLL so subsequent bare-name lookups hit an already-loaded module.
    import ctypes
    for d in bin_dirs:
        for fn in os.listdir(d):
            if fn.lower().endswith('.dll'):
                try:
                    ctypes.CDLL(os.path.join(d, fn))
                except OSError:
                    pass


def _read_exact(n):
    buf = b''
    while len(buf) < n:
        chunk = sys.stdin.buffer.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _read_frame():
    raw_len = _read_exact(4)
    if raw_len is None:
        return None
    n = struct.unpack('!I', raw_len)[0]
    return _read_exact(n)


def _write_frame(data: bytes):
    sys.stdout.buffer.write(struct.pack('!I', len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _write_json(obj):
    _write_frame(json.dumps(obj).encode('utf-8'))


def _read_request():
    header_bytes = _read_frame()
    if header_bytes is None:
        return None
    header = json.loads(header_bytes.decode('utf-8'))
    if header.get('action') == 'stop':
        return header, None
    body_bytes = _read_frame()
    if body_bytes is None:
        return None
    return header, body_bytes


def _log(msg):
    sys.stderr.write(f'[worker] {msg}\n')
    sys.stderr.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--compute-type', default='float16')
    args = parser.parse_args()

    _add_cuda_dll_dirs()
    _log(f'loading {args.model} on {args.device}/{args.compute_type}')

    import numpy as np
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    _log('model ready')
    _write_json({'status': 'ready'})

    while True:
        req = _read_request()
        if req is None:
            _log('stdin closed, exiting')
            return
        header, body = req
        if header.get('action') == 'stop':
            _log('stop requested')
            return
        try:
            audio = np.frombuffer(body, dtype=header['dtype']).reshape(header['shape']).copy()
            params = header.get('params', {})
            segments, _info = model.transcribe(audio, **params)
            text = ''.join(s.text for s in segments)
            _write_json({'text': text})
        except Exception as e:
            _write_json({'error': str(e), 'traceback': traceback.format_exc()})


if __name__ == '__main__':
    main()
