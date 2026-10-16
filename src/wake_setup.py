"""
One-time download of the wake-word model files into models/wake/.

openWakeWord's code is Apache-2.0, but its pre-trained models are licensed
CC BY-NC-SA 4.0 (non-commercial). They are downloaded to this machine only and
are never committed to this repository.

    python src\\wake_setup.py
"""
import os
import sys

import openwakeword.utils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wake_listener import MODEL_DIR, model_paths  # noqa: E402

DEFAULT_MODEL = 'hey_jarvis'


def main():
    print('openWakeWord pre-trained models are licensed CC BY-NC-SA 4.0 (non-commercial use).')
    print(f'Downloading into {MODEL_DIR}')
    openwakeword.utils.download_models(model_names=[DEFAULT_MODEL], target_directory=MODEL_DIR)
    paths = model_paths(f'{DEFAULT_MODEL}_v0.1')
    for path in paths:
        print('  ok', os.path.basename(path))


if __name__ == '__main__':
    main()
