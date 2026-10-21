from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from queue_store import Queue
from server import make_handler

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(Queue(Path(self.temp.name)/'api.db'),'test-token'))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()
    def request(self,path,data=None,authorized=True,extra=None):
        headers={'Content-Type':'application/json'}
        if authorized: headers['Authorization']='Bearer test-token'
        headers.update(extra or {})
        request=Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with urlopen(request,timeout=5) as response: return response.status,json.load(response)
        except HTTPError as error: return error.code,json.load(error)
    def test_auth(self):
        self.assertEqual(self.request('/api/jobs',authorized=False)[0],401)
        self.assertEqual(self.request('/health')[0],200)
    def test_submit_replay_conflict_events(self):
        body={'kind':'echo','payload':{'x':1}}
        headers={'Idempotency-Key':'same'}
        code,job=self.request('/api/jobs',body,extra=headers)
        self.assertEqual(code,201)
        self.assertEqual(self.request('/api/jobs',body,extra=headers)[0],200)
        self.assertEqual(self.request('/api/jobs',{'kind':'fail'},extra=headers)[0],409)
        self.assertEqual(self.request(f"/api/jobs/{job['id']}/events")[1]['events'][0]['event'],'submitted')
        self.assertNotIn('lease_token',job)
    def test_validation(self):
        for path,body,code in [('/api/jobs',{'kind':'shell'},400),('/api/jobs/missing',None,404),('/api/jobs/missing/retry',{},404),('/api/jobs',[1],400)]:
            self.assertEqual(self.request(path,body)[0],code)
    def test_cross_origin(self):
        self.assertEqual(self.request('/api/jobs',{'kind':'echo'},extra={'Origin':'https://example.test'})[0],403)
    def test_same_origin_browser_submit(self):
        self.assertEqual(self.request('/api/jobs',{'kind':'echo'},extra={'Origin':self.base})[0],201)
    def test_assets(self):
        for path in ('/','/style.css','/dashboard.js'):
            with urlopen(self.base+path) as response:
                self.assertEqual(response.status,200)
                self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
