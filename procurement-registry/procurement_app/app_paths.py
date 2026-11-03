"""Keep application assets and persistent user exports separate."""
import os
import sys
from pathlib import Path
import tempfile
import json


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def export_root():
    resources = resource_root()
    marker = resources.parent / 'PORTABLE_BUILD.json'
    if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('single_file'):
        base = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ProcurementRegistry'
        preferred = base / 'exports'
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
    elif (resources.parent / 'PORTABLE_BUILD.json').is_file():
        base = resources.parent
    else:
        base = resources
    preferred = base / 'exports'
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=preferred):
            pass
        return preferred
    except OSError:
        fallback = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ProcurementRegistry' / 'exports'
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
