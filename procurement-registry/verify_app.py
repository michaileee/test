"""Offline checks for orchestration; no procurement website calls."""
import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from urllib.request import Request, urlopen
import json
import threading

for name in ('requests', 'tender_registry', 'cmr_registry'):
    sys.modules[name] = types.ModuleType(name)
sys.path.insert(0, str(Path('procurement_app').resolve()))
spec = importlib.util.spec_from_file_location('local_app', 'procurement_app/app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
assert app.validate({'start': 2024, 'end': 2025, 'organizations': [
    {'code': '405384542'}, {'code': '405384542'}]})[2] == [{'code': '405384542'}]
try:
    app.validate({'start': 2025, 'end': 2024, 'organizations': []})
except ValueError:
    pass
else:
    raise AssertionError('Invalid range accepted')

# Exact tax-code match includes deactivated identities and excludes other organizations.
class Response:
    text = '80466|ახალი|405384542|\n90001|ძველი (დეაქტივირებული)|405384542|\n7|სხვა|400348664|'
    def raise_for_status(self):
        pass
class LookupSession:
    def __init__(self,**kwargs): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def get(self,*args,**kwargs): return Response()
app.make_session = LookupSession
assert app.resolve('405384542') == [('80466', 'ახალი'), ('90001', 'ძველი (დეაქტივირებული)')]

with tempfile.TemporaryDirectory() as tmp:
    app.OUTPUTS = Path(tmp)
    app.resolve = lambda code: [('80466', 'ახალი'), ('90001', 'ძველი (დეაქტივირებული)')]
    def export(module, prefix):
        path = module.ROOT / (prefix + '.xlsx')
        rows = [['SAME', 10], [module.ORG_ID, 20]]
        module.write_xlsx(path, [('რეესტრი', ['CMR ნომერი', 'თანხა'], rows),
                                ('გადახდები', ['CMR ნომერი', 'თანხა'], [['SAME', 5], ['SAME', 5], [module.ORG_ID, 20]])])
        return path
    exported = []
    def writer(path, sheets):
        exported.append(sheets)
        path.write_bytes(b'offline test fixture')
    app.tender_registry.write_xlsx = writer
    app.cmr_registry.write_xlsx = writer
    app.tender_registry.main = lambda: export(app.tender_registry, 'tenders')
    app.cmr_registry.main = lambda: export(app.cmr_registry, 'cmr')
    data = {'start': 2024, 'end': 2025, 'organizations': [
        {'code': '405384542'}, {'code': '400348664'}]}
    app.run(data)
    assert app.STATE['status'] == 'done'
    assert len(app.STATE['files']) == 3
    assert app.cmr_registry.YEARS == {2024, 2025}
    assert len(exported) == 2
    assert len(app.STATE['summary']) == 8
    app.set_progress('405384542', 'სახელი', 'CMR', 'მონაცემების მიღება', 1, 15)
    app.set_progress('405384542', 'სახელი', 'CMR', 'მონაცემების მიღება', 2, 15)
    assert app.STATE['progress']['current'] == 2
    assert app.STATE['progress']['total'] == 15
    for sheets in exported:
        assert len(sheets[0][2]) == 8  # unexpected repeated numbers remain in the registry
        assert len(sheets[1][2]) == 12  # all payments from both source IDs are preserved
        aliases = next(s for s in sheets if s[0] == 'შემსყიდველები')
        assert len(aliases[2]) == 4
        assert aliases[2][1][2].endswith('(დეაქტივირებული)')
        assert next(s for s in sheets if s[0] == 'დუბლირების შემოწმება')[2]
        headers=sheets[1][1]
        assert headers.count('შემსყიდველის კოდი')==1
        assert {r[headers.index('შემსყიდველის კოდი')] for r in sheets[1][2]}=={'405384542','400348664'}
        assert {r[headers.index('შემსყიდველის სისტემური ID')] for r in sheets[1][2]}=={'80466','90001'}
        assert not any(s[0] in ('პარამეტრები','ძიების შედეგები','ორგანიზაციის დასახელებები') for s in sheets)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    assert b'__TOKEN__' not in urlopen(base).read()
    font = urlopen(base + '/fonts/NotoSansGeorgian.ttf')
    assert font.headers['Content-Type'] == 'font/ttf'
    assert len(font.read()) > 10000
    state = json.load(urlopen(Request(base + '/api/state', headers={'X-App-Token': app.TOKEN})))
    assert state['status'] == 'done'
    server.shutdown()
    server.server_close()
print('Offline checks passed: validation, two organizations, two consolidated exports, ZIP, local HTTP UI and API.')
