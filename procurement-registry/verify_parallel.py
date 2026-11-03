import importlib.util
import sys
import threading
import time
import types

class Session:
    def __init__(self):
        self.headers, self.cookies = {}, {}
        self.closed = False
    def close(self):
        self.closed = True

sys.modules['requests'] = types.SimpleNamespace(Session=Session)
spec = importlib.util.spec_from_file_location('performance', 'procurement_app/performance.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.make_session = lambda **kwargs: Session()
lock = threading.Lock()
active = peak = 0
sessions = set()
def task(session, value):
    global active, peak
    with lock:
        active += 1
        peak = max(peak, active)
        sessions.add(session)
    time.sleep(0.03)
    with lock:
        active -= 1
    return value * 2
source = Session()
source.headers['Referer'] = 'test'
assert list(module.parallel_map(task, range(8), source)) == list(range(0, 16, 2))
assert peak == 2 and len(sessions) == 2
assert all(s.closed and s.headers['Referer'] == 'test' for s in sessions)
print('Parallel checks passed: two concurrent workers, independent reused sessions, ordered results, cleanup.')

# Repeated tender batches must retain their two sessions and close them on exit.
sessions.clear()
with module.ParallelRequests(source) as pool:
    assert list(pool.map(task, range(4))) == [0, 2, 4, 6]
    first = set(sessions)
    assert len(first) == 2
    assert list(pool.map(task, range(4, 8))) == [8, 10, 12, 14]
    assert sessions == first
    assert not any(s.closed for s in sessions)
assert all(s.closed for s in sessions)
print('Repeated batches reuse exactly two worker sessions.')
