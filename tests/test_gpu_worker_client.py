import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gpu_worker_client import ComfyUIWorker, GPUWorkerError

class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/system_stats': obj={'devices':[{'name':'MOCK GPU','vram_total':123}]}
        elif self.path == '/queue': obj={'queue_running':[], 'queue_pending':[1,2]}
        elif self.path == '/history/abcdef0123456789abcdef0123456789': obj={'abcdef0123456789abcdef0123456789':{'status':{'completed':True}}}
        else: self.send_error(404); return
        raw=json.dumps(obj).encode(); self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(n))
        if self.path != '/prompt' or 'prompt' not in payload: self.send_error(400); return
        raw=json.dumps({'prompt_id':'abcdef0123456789abcdef0123456789'}).encode(); self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self,*a): pass

class WorkerClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),MockHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close()
    def test_health_and_queue(self):
        result=ComfyUIWorker(self.url).health()
        self.assertTrue(result['online']); self.assertEqual(result['devices'][0]['name'],'MOCK GPU'); self.assertEqual(result['queue_pending'],2)
    def test_submit_and_history(self):
        worker=ComfyUIWorker(self.url); submitted=worker.submit({'1':{'class_type':'EmptyLatentImage'}})
        self.assertEqual(submitted['prompt_id'],'abcdef0123456789abcdef0123456789'); self.assertTrue(worker.history('abcdef0123456789abcdef0123456789')['abcdef0123456789abcdef0123456789']['status']['completed'])
    def test_unconfigured_is_safe(self):
        result=ComfyUIWorker('').health(); self.assertFalse(result['online'])
        with self.assertRaises(GPUWorkerError): ComfyUIWorker('').submit({'x':{}})

if __name__=='__main__': unittest.main()
