"""Shared test setup: put src/ on the path and initialize ConfigManager once."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, 'src')
FIXTURES = os.path.join(REPO, 'tests', 'fixtures')
if SRC not in sys.path:
    sys.path.insert(0, SRC)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from utils import ConfigManager  # noqa: E402

ConfigManager.initialize()


def set_config(value, *keys):
    ConfigManager.set_config_value(value, *keys)


_qt_app = None


def qt_app():
    """One QApplication for every Qt-using test, on the offscreen platform."""
    global _qt_app
    from PyQt5.QtWidgets import QApplication
    _qt_app = QApplication.instance() or QApplication([])
    return _qt_app
