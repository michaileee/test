"""Validate the dependency archive before importing third-party modules."""
import hashlib
import json
from pathlib import Path


def verify_dependencies():
    root = Path(__file__).resolve().parent.parent
    manifest_file = root / 'PORTABLE_BUILD.json'
    if not manifest_file.is_file():
        return  # Source-code development layout.
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    expected = manifest.get('dependencies_zip_sha256')
    if not expected:
        raise RuntimeError('Portable build metadata is incomplete. Extract the new ZIP into a new folder.')
    archive = root / 'runtime' / 'dependencies.zip'
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError('runtime/dependencies.zip is missing or damaged. Extract the complete new ZIP into a new folder.')
