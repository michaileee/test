"""Build one Windows x64 EXE with a verified, reusable per-user payload cache."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import zipfile
import cabinet_pack
import build_windows_portable as base

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'single_build'
OUTPUT = ROOT / 'outputs' / 'single_file'
VERSION = '3.0.0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    package, zig, environment = base.main(archive_output=False)
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((package / 'PORTABLE_BUILD.json').read_text(encoding='utf-8'))
    manifest.update(build=VERSION, single_file=True)
    (package / 'PORTABLE_BUILD.json').write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
    files = [(p.relative_to(package).as_posix(), p.read_bytes())
             for folder in ['app', 'runtime'] for p in sorted((package/folder).rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts]
    files += [(name, (package/name).read_bytes()) for name in ['PORTABLE_BUILD.json', 'THIRD_PARTY_NOTICES.txt']]
    checksums = {name: sha(data) for name, data in files}
    files.append(('SHA256SUMS.json', json.dumps(checksums, sort_keys=True, indent=2).encode()))
    files.sort()
    cabinet = WORK / 'payload.cab'
    cabinet_pack.pack(files, cabinet)
    decoded = cabinet_pack.unpack_for_test(cabinet)
    if decoded != dict(files):
        raise RuntimeError('Cabinet round-trip mismatch')
    payload_hash = sha(cabinet.read_bytes())
    header = ['#include <stddef.h>',
              'typedef struct { const wchar_t *name; unsigned char hash[32]; } ExpectedFile;',
              'static const ExpectedFile expected_files[] = {']
    for name, data in files:
        literal = json.dumps(name.replace('/', '\\'))
        digest = ','.join('0x'+sha(data)[i:i+2] for i in range(0,64,2))
        header.append('{L'+literal+',{'+digest+'}},')
    header += ['};', '#define EXPECTED_COUNT (sizeof(expected_files)/sizeof(expected_files[0]))',
               '#define BUILD_ID L"'+payload_hash[:24]+'"']
    (WORK / 'payload_manifest.h').write_text('\n'.join(header), encoding='utf-8')
    (WORK / 'payload.S').write_text('.section .rdata,"dr"\n.p2align 4\n'
        '.global _binary_payload_cab_start\n_binary_payload_cab_start:\n'
        '.incbin '+json.dumps(cabinet.as_posix())+'\n'
        '.global _binary_payload_cab_end\n_binary_payload_cab_end:\n', encoding='utf-8')
    application_manifest = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
<assemblyIdentity version="3.0.0.0" processorArchitecture="amd64" name="ProcurementRegistry" type="win32"/>
<trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
<requestedExecutionLevel level="asInvoker" uiAccess="false"/>
</requestedPrivileges></security></trustInfo>
<compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1"><application>
<supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
</application></compatibility>
<application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings>
<longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
</windowsSettings></application></assembly>'''
    (WORK / 'app.manifest').write_text(application_manifest, encoding='utf-8')
    rc = '''1 24 "app.manifest"
1 VERSIONINFO
FILEVERSION 3,0,0,0
PRODUCTVERSION 3,0,0,0
FILEFLAGSMASK 0x3f
FILEFLAGS 0
FILEOS 0x40004
FILETYPE 1
FILESUBTYPE 0
BEGIN
 BLOCK "StringFileInfo"
 BEGIN
  BLOCK "040904b0"
  BEGIN
   VALUE "CompanyName", "Procurement Registry contributors\\0"
   VALUE "FileDescription", "Public procurement registry export\\0"
   VALUE "FileVersion", "3.0.0.0\\0"
   VALUE "InternalName", "ProcurementRegistry\\0"
   VALUE "OriginalFilename", "ProcurementRegistry.exe\\0"
   VALUE "ProductName", "Procurement Registry\\0"
   VALUE "ProductVersion", "3.0.0.0\\0"
   VALUE "LegalCopyright", "Copyright 2026 Procurement Registry contributors\\0"
  END
 END
 BLOCK "VarFileInfo"
 BEGIN
  VALUE "Translation", 0x409, 1200
 END
END
'''
    (WORK / 'launcher.rc').write_text(rc, encoding='utf-8')
    resource = WORK / 'launcher.obj'
    subprocess.run([str(zig), 'rc', '/:no-preprocess', '/:output-format', 'coff', '/:target', 'X64',
                    '/fo', str(resource), '--', str(WORK/'launcher.rc')], cwd=WORK, check=True, env=environment)
    out = OUTPUT / 'ProcurementRegistry.exe'
    subprocess.run([str(zig), 'cc', '-target', 'x86_64-windows-gnu', '-Os', '-s', '-municode',
                    '-Wl,--subsystem,windows', '-I'+str(WORK),
                    str(ROOT/'procurement_app/single_file_launcher.c'), str(WORK/'payload.S'), str(resource),
                    '-lbcrypt', '-lcabinet', '-lshell32', '-luser32', '-o', str(out)],
                   check=True, env=environment)
    report = {'version': VERSION, 'sha256': sha(out.read_bytes()), 'bytes': out.stat().st_size,
              'cabinet_sha256': payload_hash, 'cache_id': payload_hash[:24], 'payload_files': len(files),
              'platform': 'Windows 10/11 x64', 'signature': 'unsigned; SignPath application pending',
              'windows_execution_test': 'pending'}
    (OUTPUT/'build_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    return out


if __name__ == '__main__':
    main()
