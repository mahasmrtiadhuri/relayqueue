"""Loopback HTTP API and operations dashboard for RelayQueue."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from queue_store import Conflict, Queue


MAX_BODY = 65536


def make_handler(queue, token=''):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, code, body, content_type='application/json'):
            data = json.dumps(body, allow_nan=False).encode() if content_type == 'application/json' else body.encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            expected = f'Bearer {token}'
            return not token or hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/dashboard.js': ('dashboard.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in assets:
                name, mime = assets[path]
                self.reply(200, Path(__file__).with_name(name).read_text(), mime)
                return
            if not self.authorized():
                self.reply(401, {'error': 'Bearer token required'})
                return
            try:
                if path == '/health':
                    self.reply(200, {'status': 'ok', 'counts': queue.metrics()})
                elif path == '/api/jobs':
                    self.reply(200, {'jobs': queue.list()})
                elif path.startswith('/api/jobs/'):
                    parts = path.strip('/').split('/')
                    job = queue.get(parts[2])
                    if job is None or len(parts) > 4 or (len(parts) == 4 and parts[3] != 'events'):
                        self.reply(404, {'error': 'Not found'})
                    else:
                        self.reply(200, {'events': queue.events(parts[2])} if len(parts) == 4 else job)
                else:
                    self.reply(404, {'error': 'Not found'})
            except sqlite3.OperationalError:
                self.reply(503, {'error': 'Storage temporarily unavailable'})

        def do_POST(self):
            # Reject cross-origin browser writes even in local unauthenticated mode.
            origin = self.headers.get('Origin')
            same_origin = origin in (None, f"http://{self.headers.get('Host')}", f"https://{self.headers.get('Host')}")
            if not same_origin or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.reply(403, {'error': 'Cross-origin writes disabled'})
                return
            if not self.authorized():
                self.reply(401, {'error': 'Bearer token required'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    self.reply(413, {'error': 'Body must contain 1..65536 bytes'})
                    return
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    self.reply(415, {'error': 'Use application/json'})
                    return
                body = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')))
                if not isinstance(body, dict):
                    raise ValueError('Body must be an object')
                path = urlsplit(self.path).path
                if path == '/api/jobs':
                    job, created = queue.submit(body.get('kind'), body.get('payload', {}), self.headers.get('Idempotency-Key'), body.get('max_attempts', 3))
                    self.reply(201 if created else 200, job)
                elif path.startswith('/api/jobs/') and path.endswith('/retry') and len(path.strip('/').split('/')) == 4:
                    self.reply(200, queue.retry(path.strip('/').split('/')[2]))
                else:
                    self.reply(404, {'error': 'Not found'})
            except Conflict as error:
                self.reply(409, {'error': str(error)})
            except KeyError:
                self.reply(404, {'error': 'Not found'})
            except (ValueError, TypeError, UnicodeError):
                self.reply(400, {'error': 'Invalid JSON or job parameters'})
            except sqlite3.OperationalError:
                self.reply(503, {'error': 'Storage temporarily unavailable'})

        def log_message(self, *_):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='relayqueue.db')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    token = os.getenv('RELAYQUEUE_TOKEN', '')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not token:
        parser.error('Set RELAYQUEUE_TOKEN when binding outside loopback')
    server = ThreadingHTTPServer((args.host, args.port), make_handler(Queue(args.db), token))
    print(f'RelayQueue listening on http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
