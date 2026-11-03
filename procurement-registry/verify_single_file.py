"""Check the final cabinet, PE metadata, and the extracted application without Windows."""
from pathlib import Path
import ctypes
import ctypes.util
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import cabinet_pack

ROOT = Path(__file__).resolve().parent
cabinet = ROOT / 'single_build' / 'payload.cab'
exe = ROOT / 'outputs' / 'single_file' / 'ProcurementRegistry.exe'
report = json.loads(exe.with_name('build_report.json').read_text())
blob = exe.read_bytes()
assert report['sha256'] == hashlib.sha256(blob).hexdigest()
assert cabinet.read_bytes() in blob
pe = struct.unpack_from('<I', blob, 0x3c)[0]
assert blob[pe:pe+4] == b'PE\0\0'
assert struct.unpack_from('<H', blob, pe+4)[0] == 0x8664
assert struct.unpack_from('<H', blob, pe+24+68)[0] == 2
assert 'Procurement Registry'.encode('utf-16le') in blob
assert '3.0.0.0'.encode('utf-16le') in blob
assert b'requestedExecutionLevel level="asInvoker"' in blob
expected = cabinet_pack.unpack_for_test(cabinet)
assert len(expected) == report['payload_files']
assert not any(n.endswith(('.xlsx', '.log', '.pyc')) or n.startswith('exports/') for n in expected)

# Independent libarchive decoder confirms compatibility of the MSZIP archive.
library = ctypes.util.find_library('archive') if os.name != 'nt' else None
if library:
    lib = ctypes.CDLL(library)
    lib.archive_read_new.restype = ctypes.c_void_p
    for name in ['archive_read_support_format_cab', 'archive_read_free']:
        getattr(lib, name).argtypes = [ctypes.c_void_p]
    lib.archive_read_open_filename.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.archive_read_next_header.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.archive_entry_pathname.argtypes = [ctypes.c_void_p]
    lib.archive_entry_pathname.restype = ctypes.c_char_p
    lib.archive_read_data.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    lib.archive_read_data.restype = ctypes.c_ssize_t
    lib.archive_error_string.argtypes = [ctypes.c_void_p]
    lib.archive_error_string.restype = ctypes.c_char_p
    archive = lib.archive_read_new()
    decoded = {}
    try:
        assert lib.archive_read_support_format_cab(archive) == 0
        assert lib.archive_read_open_filename(archive, os.fsencode(cabinet), 10240) == 0
        while True:
            entry = ctypes.c_void_p()
            status = lib.archive_read_next_header(archive, ctypes.byref(entry))
            if status == 1:
                break
            assert status == 0, lib.archive_error_string(archive)
            name = lib.archive_entry_pathname(entry).decode().replace('\\', '/')
            parts, buffer = [], ctypes.create_string_buffer(65536)
            while True:
                length = lib.archive_read_data(archive, buffer, len(buffer))
                assert length >= 0, lib.archive_error_string(archive)
                if not length:
                    break
                parts.append(buffer.raw[:length])
            decoded[name] = b''.join(parts)
        assert decoded == expected
    finally:
        lib.archive_read_free(archive)
    print('PASS: independently decoded every MSZIP cabinet member using libarchive.')

with tempfile.TemporaryDirectory(dir=ROOT, prefix='single-smoke-') as directory:
    directory = Path(directory)
    for name, data in expected.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    app, vendor = directory/'app', directory/'runtime/dependencies.zip'
    env = dict(os.environ, LOCALAPPDATA=str(directory/'user-data'))
    command = [sys.executable, '-I', '-B', '-c',
        "import sys,runpy;sys.path[:0]="+repr([str(app), str(vendor)])+";runpy.run_path("+
        repr(str(app/'diagnostics.py'))+",run_name='__main__')"]
    subprocess.run(command, env=env, check=True)
    diagnostic = json.loads((directory/'user-data/ProcurementRegistry/startup_diagnostics.json').read_text())
    assert diagnostic['status'] == 'passed'
    assert diagnostic['packages']['urllib3']['version'] == '2.8.0'
    assert (directory/'user-data/ProcurementRegistry/exports').is_dir()
print('PASS: x64 GUI PE, product metadata, unsigned payload, data separation, certificates, imports and local HTTP.')
print('Native Windows extraction and execution require the Windows CI smoke test.')
