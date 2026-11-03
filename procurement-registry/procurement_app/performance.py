"""Bounded parallel requests with separate reusable sessions per worker."""
from concurrent.futures import ThreadPoolExecutor
from threading import local
import requests
import time


def make_session(report=None, retry_interval=30):
    """Keep retrying transient failures until recovery or process cancellation."""
    class ResilientSession(requests.Session):
        retry_report = staticmethod(report or print)

        def request(self, method, url, **kwargs):
            failures = 0
            while True:
                try:
                    response = super().request(method, url, **kwargs)
                    if response.status_code not in (429, 500, 502, 503, 504):
                        if failures:
                            self.retry_report('კავშირი აღდგა — მონაცემების ამოღება გრძელდება')
                        return response
                    reason = f'HTTP {response.status_code}'
                    response.close()
                except requests.exceptions.SSLError:
                    # A certificate/configuration issue needs correction, not endless retries.
                    raise
                except (requests.ConnectionError, requests.Timeout) as error:
                    reason = type(error).__name__
                failures += 1
                self.retry_report(
                    f'დროებითი კავშირის შეფერხება ({reason}); '
                    f'ხელახალი მცდელობა {retry_interval} წამში — მცდელობა {failures}')
                time.sleep(retry_interval)

    return ResilientSession()


class ParallelRequests:
    """Reuse the same two worker connections across successive detail batches."""
    def __init__(self, source_session, workers=2):
        self.source = source_session
        self.state = local()
        self.sessions = []
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def __enter__(self):
        return self

    def map(self, function, items):
        def worker(item):
            if not hasattr(self.state, 'session'):
                session = make_session(report=getattr(self.source, 'retry_report', None))
                session.headers.update(self.source.headers)
                session.cookies.update(self.source.cookies)
                self.state.session = session
                self.sessions.append(session)
            return function(self.state.session, item)
        yield from self.pool.map(worker, items)

    def __exit__(self, *args):
        try:
            self.pool.shutdown(wait=True)
        finally:
            for session in self.sessions:
                session.close()


def parallel_map(function, items, source_session, workers=2):
    with ParallelRequests(source_session, workers) as pool:
        yield from pool.map(function, items)
