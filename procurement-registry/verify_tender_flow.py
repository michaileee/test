"""Verify shared workers preserve tender/bid counts, ordering, and profile de-duplication."""
from pathlib import Path
from datetime import datetime
import sys
import threading
import time

sys.path.insert(0, str(Path('procurement_app').resolve()))
import tender_registry as t
from performance import ParallelRequests
calls, lock = [], threading.Lock()
workers = set()
class Session:
    headers, cookies = {}, {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, *args, **kwargs): return self
    def raise_for_status(self): pass
    def close(self): pass
import performance
performance.make_session = lambda **kwargs: Session()
t.make_session = lambda **kwargs: Session()
t.collect_search_rows = lambda *args: [{'id': f'A{i}', 'onclick': f"ShowApp({i},'',0,'key')"} for i in [1,2]]
def get_soup(session, params):
    with lock:
        workers.add(threading.get_ident())
        calls.append(dict(params))
    time.sleep(.01)
    return dict(params)
t.get_soup = get_soup
def parse(row, detail, agreement):
    record = [''] * 26
    record[0] = 'NAT-TEST-' + row['id']
    record[4] = datetime(2025,1,1)
    return record, [], [], [], []
t.parse_tender = parse
t.parse_conditions = lambda *args: ([], [])
t.parse_bids = lambda soup, number: [[number, 'shared-org', 'bid-'+number, 'Supplier']]
t.parse_technical = lambda *args: []
t.parse_results = lambda *args: []
t.parse_history = lambda soup, bid: [[bid[0], bid[1], bid[2], 100, datetime(2025,1,1)]]
t.parse_profile = lambda soup, org_id: ([org_id] + ['']*10, [], [])
captured = []
t.write_xlsx = lambda path, sheets: captured.extend(sheets)
t.print = lambda *args, **kwargs: None
t.main()
sheets = {name: rows for name, _, rows in captured}
assert len(sheets['რეესტრი']) == 2
assert len(sheets['შეთავაზებები']) == len(sheets['შეთავაზებების ისტორია']) == 2
assert len(sheets['პრეტენდენტები']) == 1
assert len([c for c in calls if c['action'] == 'profile']) == 1
assert len([c for c in calls if c['action'] == 'view_bid']) == 2
assert len(workers) == 2
assert [r[0] for r in sheets['შეთავაზებების ისტორია']] == ['NAT-TEST-A1','NAT-TEST-A2']
print('PASS: repeated tender batches share two workers; profile fetched once; both bid histories preserved.')
