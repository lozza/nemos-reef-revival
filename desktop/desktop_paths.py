"""Locate original assets and writable desktop state in source and frozen builds."""
import os
from pathlib import Path
import sys


def project_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def desktop_directory():
    override = os.environ.get('NEMO_DESKTOP_HOME')
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        if sys.platform == 'win32':
            base = os.environ.get('LOCALAPPDATA')
            return (Path(base) if base else Path.home() / 'AppData/Local') / 'NemosReef'
        base = os.environ.get('XDG_DATA_HOME')
        return (Path(base) if base else Path.home() / '.local/share') / 'nemos-reef'
    return Path(__file__).resolve().parent
