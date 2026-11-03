"""Assemble a Windows x64 distribution without installing Python on the target."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from email.parser import Parser

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'portable_build'
CACHE = WORK / 'cache'
PACKAGE = WORK / 'ProcurementRegistry'
OUTPUT = ROOT / 'outputs' / 'windows_portable'
PY_VERSION = '3.13.17'
PY_URL = f'https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip'
PY_SHA256 = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def download(url, name, expected=None):
    target = CACHE / name
    if not target.exists():
        with urllib.request.urlopen(url, timeout=60) as response, target.with_suffix('.part').open('wb') as out:
            shutil.copyfileobj(response, out)
        target.with_suffix('.part').replace(target)
    if expected and digest(target) != expected:
        raise ValueError(f'SHA256 mismatch: {name}')
    return target


def main(archive_output=True):
    CACHE.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    index = json.load(urllib.request.urlopen('https://ziglang.org/download/index.json', timeout=30))
    zig_version = '0.17.0'
    host = 'windows' if os.name == 'nt' else 'linux'
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        raise RuntimeError('Build host must be x64 Linux or Windows.')
    zig_info = index[zig_version]['x86_64-' + host]
    print('Downloading official runtime and build tool', flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        py_job = pool.submit(download, PY_URL, 'python-embed.zip', PY_SHA256)
        zig_job = pool.submit(download, zig_info['tarball'], 'zig-windows.zip' if host == 'windows' else 'zig.tar.xz', zig_info['shasum'])
        py_archive, zig_archive = py_job.result(), zig_job.result()
    compiler_dir = WORK / ('compiler-windows' if host == 'windows' else 'compiler')
    if not compiler_dir.exists():
        compiler_dir.mkdir()
        if host == 'windows':
            with zipfile.ZipFile(zig_archive) as archive:
                archive.extractall(compiler_dir)
        else:
            with tarfile.open(zig_archive) as archive:
                archive.extractall(compiler_dir, filter='data')
    zig = next(compiler_dir.glob('*/zig.exe' if host == 'windows' else '*/zig'))
    wheels_dir = CACHE / 'locked-wheels-v2'
    wheels_dir.mkdir(exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--disable-pip-version-check',
        '--index-url', 'https://pypi.org/simple', '--only-binary=:all:', '--platform', 'any',
        '--python-version', '313', '--implementation', 'py', '--abi', 'none',
        '--no-deps', '--require-hashes', '--dest', str(wheels_dir), '-r',
        str(ROOT / 'procurement_app' / 'requirements-portable.lock.txt')], check=True)
    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)
    runtime, app = PACKAGE / 'runtime', PACKAGE / 'app'
    runtime.mkdir(parents=True)
    app.mkdir()
    with zipfile.ZipFile(py_archive) as archive:
        archive.extractall(runtime)
    (runtime / 'python313._pth').write_text('python313.zip\n.\ndependencies.zip\n../app\n', encoding='utf-8')
    dependencies = []
    vendor = runtime / 'dependencies.zip'
    entries = {}
    for file in sorted(wheels_dir.glob('*.whl')):
        if not file.name.endswith('none-any.whl'):
            raise ValueError('Only pure Python wheels are permitted: ' + file.name)
        with zipfile.ZipFile(file) as archive:
            metadata_name = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            metadata = Parser().parsestr(archive.read(metadata_name).decode())
            dependencies.append({'name': metadata['Name'], 'version': metadata['Version'], 'sha256': digest(file)})
            for name in archive.namelist():
                if name.endswith('/'):
                    continue
                if name in entries:
                    raise ValueError('Dependency file collision: ' + name)
                entries[name] = archive.read(name)
    with zipfile.ZipFile(vendor, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.external_attr = 0x20
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    for name in ['app.py', 'app_paths.py', 'launch.py', 'portable_integrity.py', 'diagnostics.py', 'performance.py', 'tender_registry.py', 'cmr_registry.py', 'index.html']:
        shutil.copy2(ROOT / 'procurement_app' / name, app / name)
    shutil.copytree(ROOT / 'procurement_app' / 'fonts', app / 'fonts')
    temporary = WORK / 'tmp'
    temporary.mkdir(exist_ok=True)
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(WORK / 'zig-global-cache'),
               ZIG_LOCAL_CACHE_DIR=str(WORK / 'zig-local-cache'), TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
    print('Building Windows executable launcher', flush=True)
    subprocess.run([str(zig), 'cc', '-target', 'x86_64-windows-gnu', '-Os', '-municode', '-Wl,--subsystem,windows',
        str(ROOT / 'procurement_app' / 'windows_launcher.c'), '-luser32', '-o',
        str(PACKAGE / 'ProcurementRegistry.exe')], check=True, env=env)
    (PACKAGE / 'exports').mkdir()
    (PACKAGE / 'Start_with_diagnostics.cmd').write_text(
        '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n"runtime\\python.exe" -I "app\\launch.py"\r\npause\r\n', encoding='ascii')
    (PACKAGE / 'Check_startup.cmd').write_text(
        '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n"runtime\\python.exe" -I "app\\diagnostics.py"\r\npause\r\n', encoding='ascii')
    manifest = {'build': '2', 'target': 'Windows 10/11 x64', 'python': PY_VERSION, 'runtime_source': PY_URL,
                'dependencies_zip_sha256': digest(vendor),
                'runtime_archive_sha256': digest(py_archive), 'launcher_compiler': f'Zig {zig_version}',
                'dependencies': dependencies, 'windows_execution_test': 'pending — Windows host required'}
    (PACKAGE / 'PORTABLE_BUILD.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    readme = '''შესყიდვების რეესტრი — Windows 10/11 x64

1. ZIP სრულად გაშალეთ ჩვეულებრივ საქაღალდეში, მაგალითად Desktop-ზე.
2. გახსენით ProcurementRegistry.exe. ინტერფეისი გაიხსნება თქვენს ბრაუზერში.
3. შეიყვანეთ ორგანიზაციის კოდი/კოდები და წლები. მიიღებთ ორ საერთო Excel ფაილს.
4. ფაილები ხელმისაწვდომია ინტერფეისიდან. ავტომატური ასლები რჩება exports საქაღალდეში.
5. დასრულების შემდეგ დააჭირეთ „აპლიკაციის დახურვა“, შემდეგ დახურეთ ბრაუზერის ჩანართი.

Python-ის, pip-ის ან სხვა ბიბლიოთეკების დაყენება არ არის საჭირო.
მონაცემების მისაღებად საჭიროა ინტერნეტი და არსებული ბრაუზერი.
აპლიკაცია მუშაობს მხოლოდ თქვენს კომპიუტერზე, 127.0.0.1 მისამართით.
გადაიტანეთ მთელი საქაღალდე — მარტო EXE ფაილის გადატანა საკმარისი არ არის.
არ წაშალოთ app და runtime საქაღალდეები. ესენი აპლიკაციის ნაწილია.
Excel-ების გახსნა შესაძლებელია Excel-ით ან სხვა თავსებადი პროგრამით; შექმნისთვის Excel არ არის საჭირო.

თუ ZIP-ის საქაღალდეში ჩაწერა შეუძლებელია, ექსპორტი ინახება მომხმარებლის LOCALAPPDATA/
ProcurementRegistry/exports-ში. ფაილების ჩამოტვირთვა ბრაუზერიდან კვლავ შესაძლებელია.
ეს ვერსია სრულად გაშალეთ ახალ საქაღალდეში; ძველ საქაღალდეში ფაილებს ნუ გადააწერთ.
ბიბლიოთეკები მოთავსებულია runtime/dependencies.zip-ში; ამ ფაილს ცალკე ნუ გაშლით.
შემოწმებისთვის გაუშვით Check_startup.cmd — შედეგი ჩაიწერება startup_diagnostics.json-ში.
გაშვების პრობლემისას გამოიყენეთ Start_with_diagnostics.cmd ან startup_error.log.

შენიშვნა ტესტირებაზე: პაკეტი აწყობილია Windows x64 ფორმატში. ამ გარემოში Windows არ არის,
ამიტომ რეალურ Windows 10/11-ზე EXE-ის გაშვება ჯერ შესამოწმებელია.
ეს არ არის ARM64 ან Windows 32-bit-ისთვის შექმნილი პაკეტი.
'''
    (PACKAGE / 'წაიკითხეთ.txt').write_text(readme, encoding='utf-8-sig')
    notices = 'Bundled components and licenses\n\nPython: runtime/LICENSE.txt (PSF License)\n'
    notices += 'Noto Sans Georgian: app/fonts/OFL.txt (SIL Open Font License)\n'
    notices += 'Python dependencies: runtime/dependencies.zip, *.dist-info (license files retained)\n'
    (PACKAGE / 'THIRD_PARTY_NOTICES.txt').write_text(notices, encoding='utf-8')
    files = {str(p.relative_to(PACKAGE)).replace('\\', '/'): digest(p)
             for p in sorted(PACKAGE.rglob('*')) if p.is_file()}
    (PACKAGE / 'SHA256SUMS.json').write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding='utf-8')
    if not archive_output:
        return PACKAGE, zig, env
    out = OUTPUT / 'ProcurementRegistry_Windows10_11_x64_v2.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(PACKAGE.rglob('*')):
            if file.is_file():
                archive.write(file, str(Path(PACKAGE.name) / file.relative_to(PACKAGE)))
    print(json.dumps({'output': str(out), 'bytes': out.stat().st_size, 'dependencies': dependencies}, indent=2), flush=True)


if __name__ == '__main__':
    main()
