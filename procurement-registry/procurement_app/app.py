"""Local procurement export application. Binds only to 127.0.0.1."""
import json
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zipfile import ZipFile, ZIP_DEFLATED
import requests
from performance import make_session
import tender_registry
import cmr_registry
from app_paths import resource_root, export_root

ROOT = resource_root()
OUTPUTS = export_root()
TOKEN = secrets.token_urlsafe(32)
STATE = {'status': 'idle', 'logs': [], 'files': [], 'errors': [], 'progress': None, 'summary': []}
LOCK = threading.Lock()
BUSY = False


def log(message):
    with LOCK:
        STATE['logs'][:] = [str(message)]  # private diagnostic; no scrolling UI log


def set_progress(code, name, label, stage, current=0, total=None):
    with LOCK:
        STATE['progress'] = dict(code=code, name=name, module=label,
                                 stage=stage, current=current, total=total)


def resolve(code):
    with make_session(report=log) as session:
        r = session.get('https://tenders.procurement.gov.ge/public/library/list_org.php',
                        params={'q': code, 'limit': 50, 'timestamp': int(datetime.now().timestamp()*1000), 'orgtype': 3}, timeout=60)
    r.raise_for_status()
    r.encoding = 'utf-8'
    matches = []
    for line in r.text.splitlines():
        fields = line.split('|')
        if len(fields) >= 3 and fields[2].strip() == code and fields[0].strip().isdigit():
            matches.append((fields[0].strip(), fields[1].strip()))
    matches = list(dict.fromkeys(matches))
    if not matches:
        raise ValueError(f'{code}: შემსყიდველის ჩანაწერი ვერ მოიძებნა')
    if len(r.text.splitlines()) >= 50:
        raise ValueError(f'{code}: ორგანიზაციის ძიება ლიმიტს აღწევს; სრული სია გადასამოწმებელია')
    log(f'{code}: ნაპოვნია {len(matches)} დასახელება / სისტემური ჩანაწერი')
    return matches


def export_aliases(module, aliases, code, start, end, folder, collect_only=False):
    """Collect before writing so each identification code gets one workbook per module."""
    merged, owners, alias_report, conflicts = {}, {}, [], []
    actual_writer = module.write_xlsx
    # The same system ID can appear with multiple names; search it once.
    searches = list(dict((org_id, name) for org_id, name in aliases).items())
    try:
        for org_id, name in searches:
            module.ORG, module.ORG_ID, module.ORG_CODE = name, org_id, code
            module.DATE_FROM, module.DATE_TO = f'01.01.{start}', f'31.12.{end}'
            module.PERIOD, module.ROOT = f'{start}–{end}', folder
            if module is cmr_registry:
                module.YEARS = set(range(start, end + 1))
            label = 'CMR' if module is cmr_registry else 'ტენდერები'
            module.on_progress = lambda stage, current=0, total=None: set_progress(code, name, label, stage, current, total)
            set_progress(code, name, label, 'ძებნა')
            captured = []
            module.write_xlsx = lambda path, sheets: captured.extend(sheets)
            log(f'{code}: სისტემური ID {org_id} / {name}')
            module.main()
            if not captured:
                raise ValueError(f'{code}/{org_id}: მოდულმა მონაცემები ვერ დააბრუნა')
            primary = captured[0]
            primary_rows = primary[2]
            alias_report.append([org_id, name, len(primary_rows)])
            with LOCK:
                STATE['summary'].append(dict(code=code, name=name, module=label, total=len(primary_rows)))
            for row in primary_rows:
                number = row[0]
                if number in owners:
                    # A differing snapshot must be visible, not silently overwritten.
                    old = owners[number][1]
                    headers = primary[1]
                    differing = [headers[i] for i, (a, b) in enumerate(zip(old, row))
                                 if a != b and headers[i] not in ('შემსყიდველის დასახელება', 'შემსყიდველის სისტემური ID')]
                    conflicts.append([number, owners[number][0], org_id,
                                      'იდენტური ჩანაწერი' if not differing else 'განსხვავდება: ' + ', '.join(differing)])
                else:
                    owners[number] = (org_id, row)
            names = {r[0]: r[primary[1].index('შემსყიდველის დასახელება')]
                     for r in primary_rows} if 'შემსყიდველის დასახელება' in primary[1] else {}
            for sheet_name, headers, rows in captured:
                if sheet_name == 'პარამეტრები':
                    continue
                if headers and headers[0] in ('ტენდერის ნომერი', 'CMR ნომერი'):
                    extra = [(h, value) for h, value in (
                        ('შემსყიდველის კოდი', code),
                        ('შემსყიდველის დასახელება', name),
                        ('შემსყიდველის სისტემური ID', org_id)) if h not in headers]
                    rows = [list(r) + [names.get(r[0], value) if h == 'შემსყიდველის დასახელება' else value
                                      for h, value in extra] for r in rows]
                    headers = list(headers) + [h for h, value in extra]
                if sheet_name not in merged:
                    merged[sheet_name] = (headers, [])
                if merged[sheet_name][0] != headers:
                    raise ValueError(f'{sheet_name}: სვეტები არ ემთხვევა')
                procurement_sheet = headers[0] in ('ტენდერის ნომერი', 'CMR ნომერი')
                if procurement_sheet:
                    # Preserve every source record, including unexpected repeated numbers.
                    merged[sheet_name][1].extend(rows)
                else:
                    # Organization/contact tables can legitimately overlap between searches.
                    known = {tuple(r) for r in merged[sheet_name][1]}
                    for row in rows:
                        if tuple(row) not in known:
                            merged[sheet_name][1].append(row)
                            known.add(tuple(row))
    finally:
        module.write_xlsx = actual_writer
    sheets = [(name, headers, rows) for name, (headers, rows) in merged.items()]
    sheets.append(('შემსყიდველები', ['საიდენტიფიკაციო კოდი', 'სისტემური ID', 'დასახელება', 'შესყიდვების რაოდენობა'],
                   [[code, org_id, name, count] for org_id, name, count in alias_report]))
    if conflicts:
        sheets.append(('დუბლირების შემოწმება', ['შესყიდვის ნომერი', 'პირველი წყაროს ID', 'მეორე წყაროს ID', 'შედარება'], conflicts))
    if collect_only:
        return sheets
    prefix = 'cmr' if module is cmr_registry else 'tender'
    output = folder / f'{prefix}_{code}_{start}_{end}_{datetime.now():%Y%m%d_%H%M%S_%f}.xlsx'
    set_progress(code, '', 'CMR' if module is cmr_registry else 'ტენდერები', 'Excel-ის შექმნა')
    actual_writer(output, sheets)
    if conflicts:
        log(f'{code}: განმეორებული ნომრები შენარჩუნებულია; იხილეთ დუბლირების შემოწმების ფურცელი')
    return output


def validate(data):
    start, end = int(data['start']), int(data['end'])
    if not 2000 <= start <= end <= datetime.now().year:
        raise ValueError('წლების დიაპაზონი არასწორია')
    organizations = data.get('organizations', [])
    if not 1 <= len(organizations) <= 50:
        raise ValueError('დაამატეთ 1–50 ორგანიზაცია')
    cleaned, seen = [], set()
    for org in organizations:
        code = str(org.get('code', '')).strip()
        if not code.isascii() or not code.isdigit() or len(code) != 9:
            raise ValueError('მიუთითეთ 9-ციფრიანი საიდენტიფიკაციო კოდი')
        if code not in seen:
            cleaned.append({'code': code})
            seen.add(code)
    return start, end, cleaned


def run(data):
    global BUSY
    start, end, organizations = validate(data)
    job_id = uuid.uuid4().hex
    folder = OUTPUTS / job_id
    folder.mkdir(parents=True)
    began = time.perf_counter()
    manifest = {'start_year': start, 'end_year': end, 'organizations': organizations,
                'files': [], 'errors': [], 'created_at': datetime.now().isoformat()}
    manifest['module_seconds'] = []
    combined = {tender_registry: {}, cmr_registry: {}}
    try:
        for organization in organizations:
            code = organization['code']
            try:
                set_progress(code, '', '', 'ორგანიზაციის ძებნა')
                log(f'{code}: ორგანიზაციის შემოწმება')
                aliases = resolve(code)
                organization['resolved_aliases'] = [{'id': i, 'name': n} for i, n in aliases]
            except Exception as error:
                message = f'{code}: {error}'
                log(message)
                manifest['errors'].append(message)
                continue
            org_folder = folder
            for label, module in [('ტენდერები', tender_registry), ('CMR', cmr_registry)]:
                module.print = lambda *args, **kwargs: log(' '.join(str(v) for v in args))
                log(f'{code}: {label} — იწყება')
                module_started = time.perf_counter()
                try:
                    sheets = export_aliases(module, aliases, code, start, end, org_folder, collect_only=True)
                    target = combined[module]
                    for sheet_name, headers, rows in sheets:
                        if sheet_name not in target:
                            target[sheet_name] = (headers, [])
                        if target[sheet_name][0] != headers:
                            raise ValueError(f'{sheet_name}: სვეტები არ ემთხვევა')
                    for sheet_name, headers, rows in sheets:
                        target[sheet_name][1].extend(rows)
                except Exception as error:
                    message = f'{code} / {label}: {error}'
                    log(message)
                    manifest['errors'].append(message)
                finally:
                    seconds = round(time.perf_counter() - module_started, 1)
                    manifest['module_seconds'].append({'code': code, 'module': label, 'seconds': seconds})
                    log(f'{code}: {label} — დრო {seconds} წამი')
        for label, module, prefix in [('ტენდერები', tender_registry, 'tenders'), ('CMR', cmr_registry, 'cmr')]:
            if not combined[module]:
                continue
            sheets = []
            for sheet_name, (headers, rows) in combined[module].items():
                if headers[0] not in ('ტენდერის ნომერი', 'CMR ნომერი'):
                    rows = list(dict.fromkeys(tuple(r) for r in rows))
                sheets.append((sheet_name, headers, rows))
            path = folder / f'{prefix}_{start}_{end}.xlsx'
            set_progress('', '', label, 'საერთო Excel-ის შექმნა')
            module.write_xlsx(path, sheets)
            relative = path.name
            manifest['files'].append(relative)
            with LOCK:
                STATE['files'].append({'name': path.name, 'path': f'{job_id}/{relative}'})
        manifest['total_seconds'] = round(time.perf_counter() - began, 1)
        log(f'სრული ამოღების დრო: {manifest["total_seconds"]} წამი')
        (folder / 'run_report.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        bundle = folder / 'all_exports.zip'
        with ZipFile(bundle, 'w', ZIP_DEFLATED) as archive:
            for relative in manifest['files']:
                archive.write(folder / relative, relative)
            archive.write(folder / 'run_report.json', 'run_report.json')
        with LOCK:
            STATE['files'].append({'name': bundle.name, 'path': f'{job_id}/{bundle.name}'})
            STATE['errors'] = manifest['errors']
            STATE['status'] = 'partial' if manifest['errors'] else 'done'
    except Exception as error:
        with LOCK:
            STATE['status'] = 'failed'
            STATE['errors'].append(str(error))
    finally:
        with LOCK:
            BUSY = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, mime='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return self.headers.get('X-App-Token') == TOKEN or parse_qs(urlparse(self.path).query).get('token', [''])[0] == TOKEN

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == '/':
            html = (ROOT / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', TOKEN)
            return self.send(200, html.encode(), 'text/html; charset=utf-8')
        if route.path == '/fonts/NotoSansGeorgian.ttf':
            return self.send(200, (ROOT / 'fonts' / 'NotoSansGeorgian.ttf').read_bytes(), 'font/ttf')
        if not self.authorized():
            return self.send(403, b'{}')
        if route.path == '/api/state':
            with LOCK:
                body = json.dumps(STATE, ensure_ascii=False).encode()
            return self.send(200, body)
        if route.path == '/download':
            relative = parse_qs(route.query).get('path', [''])[0]
            path = (OUTPUTS / relative).resolve()
            if not path.is_relative_to(OUTPUTS.resolve()) or not path.is_file() or path.suffix not in ('.xlsx', '.zip'):
                return self.send(404, b'{}')
            return self.send(200, path.read_bytes(), 'application/zip' if path.suffix == '.zip' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.send(404, b'{}')

    def do_POST(self):
        global BUSY
        route = urlparse(self.path).path
        if route == '/api/shutdown' and self.authorized():
            with LOCK:
                if BUSY:
                    return self.send(409, json.dumps({'error': 'დაელოდეთ მონაცემების ამოღების დასრულებას.'}, ensure_ascii=False).encode())
                STATE['status'] = 'closing'
            self.send(200, b'{}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if route != '/api/run' or not self.authorized():
            return self.send(403, b'{}')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 100000:
                raise ValueError('მოთხოვნის ზომა არასწორია')
            data = json.loads(self.rfile.read(length))
            validate(data)
            with LOCK:
                if BUSY or STATE['status'] == 'closing':
                    return self.send(409, b'{}')
                BUSY = True
                STATE.update(status='running', logs=[], files=[], errors=[], progress=None, summary=[])
            threading.Thread(target=run, args=(data,), daemon=True).start()
            self.send(202, b'{}')
        except (ValueError, KeyError, TypeError) as error:
            self.send(400, json.dumps({'error': str(error)}, ensure_ascii=False).encode())


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    if sys.stdout is not None:
        print('აპლიკაცია მზადაა. დასახურად გამოიყენეთ აპლიკაციის დახურვის ღილაკი.')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
