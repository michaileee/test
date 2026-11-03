"""Offline startup diagnostics using the same bundled interpreter as the app."""
from pathlib import Path
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    report = {'python': sys.version, 'platform': sys.platform, 'executable': sys.executable,
              'sys_path': sys.path, 'status': 'failed'}
    try:
        from portable_integrity import verify_dependencies
        verify_dependencies()
        import urllib3
        import requests
        import bs4
        import certifi
        import ssl
        report['packages'] = {m.__name__: {'version': m.__version__, 'file': m.__file__}
                              for m in [urllib3, requests, bs4]}
        ssl.create_default_context(cafile=certifi.where())
        report['certificate_bundle'] = 'loaded'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'portable-ok')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(f'http://127.0.0.1:{server.server_port}', timeout=5)
                assert response.status_code == 200 and response.text == 'portable-ok'
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        import app
        assert (app.ROOT / 'index.html').is_file()
        report['status'] = 'passed'
    except Exception:
        report['traceback'] = traceback.format_exc()
    from app_paths import export_root
    destination = export_root().parent / 'startup_diagnostics.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Startup check: ' + report['status'])
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
