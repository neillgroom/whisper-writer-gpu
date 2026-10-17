"""
One-time download of the wake-word model files into models/wake/.

openWakeWord's code is Apache-2.0, but its pre-trained models are licensed
CC BY-NC-SA 4.0 (non-commercial). They are downloaded to this machine only and
are never committed to this repository.

    python src\\wake_setup.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wake_listener import MODEL_DIR, FEATURE_FILES, model_paths  # noqa: E402

DEFAULT_MODEL = 'hey_jarvis'


def required_onnx_names():
    """The .onnx files this project loads (must match wake_listener.model_paths)."""
    return [f'{DEFAULT_MODEL}_v0.1.onnx', *FEATURE_FILES]


def _present(path):
    """A download counts only when the file exists and is non-empty; an interrupted
    write can leave a 0-byte file, which is as unusable as a missing one."""
    return os.path.isfile(path) and os.path.getsize(path) > 0


def repair_incomplete_pairs(model_dir, onnx_names):
    """
    openWakeWord downloads each model as a (.tflite, .onnx) pair but decides
    whether to download by the .tflite alone. A run interrupted between the two
    writes leaves a half-pair, and every retry then skips it, so model_paths()
    keeps failing until the orphan is deleted by hand. Remove the surviving half
    of any incomplete pair here so the next download_models() re-fetches both.

    Only the files we load are paired and checked; openWakeWord's VAD model has
    no .onnx sibling by design and is never in onnx_names, so it is left alone.
    Returns the basenames removed (for logging and tests).
    """
    removed = []
    for onnx_name in onnx_names:
        onnx = os.path.join(model_dir, onnx_name)
        tflite = os.path.join(model_dir, os.path.splitext(onnx_name)[0] + '.tflite')
        halves = [p for p in (onnx, tflite) if _present(p)]
        if len(halves) == 1:  # exactly one half survived an interrupted download
            os.remove(halves[0])
            removed.append(os.path.basename(halves[0]))
    return removed


def main():
    import openwakeword.utils  # heavy import; only needed when actually downloading

    print('openWakeWord pre-trained models are licensed CC BY-NC-SA 4.0 (non-commercial use).')
    print(f'Downloading into {MODEL_DIR}')
    os.makedirs(MODEL_DIR, exist_ok=True)
    for name in repair_incomplete_pairs(MODEL_DIR, required_onnx_names()):
        print('  removed partial', name)
    openwakeword.utils.download_models(model_names=[DEFAULT_MODEL], target_directory=MODEL_DIR)
    paths = model_paths(f'{DEFAULT_MODEL}_v0.1')
    for path in paths:
        print('  ok', os.path.basename(path))


if __name__ == '__main__':
    main()
