import sys, threading, socket
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0,str(Path('procurement_app').resolve()))
from performance import make_session
class Handler(BaseHTTPRequestHandler):
    counts={}
    def log_message(self,*args): pass
    def handle_request(self):
        key=(self.command,self.path)
        self.counts[key]=self.counts.get(key,0)+1
        if self.counts[key]<=6 and self.path=='/disconnect':
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        status=503 if self.path=='/status' and self.counts[key]==1 else 200
        self.send_response(status);self.end_headers();self.wfile.write(b'ok')
    do_GET=handle_request
    do_POST=handle_request
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with make_session(retry_interval=0.01) as session:
        for method in ('get','post'):
            for path in ('/disconnect','/status'):
                response=getattr(session,method)(f'http://127.0.0.1:{server.server_port}{path}',timeout=3)
                assert response.status_code==200
                assert Handler.counts[(method.upper(),path)]==(7 if path=='/disconnect' else 2)
finally: server.shutdown();server.server_close()
print('Passed: GET and read-only POST recover after six disconnects and HTTP 503.')
