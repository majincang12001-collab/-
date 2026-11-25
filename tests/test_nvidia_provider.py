import json, os, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
import server

class NvidiaProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True); cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls): cls.http.shutdown(); cls.http.server_close()
    def req(self,path,body=None):
        data=None if body is None else json.dumps(body).encode()
        req=urllib.request.Request(self.base+path,data=data,headers={'X-Access-Key':server.ACCESS_KEY,'Content-Type':'application/json'})
        return urllib.request.urlopen(req).read()
    def test_catalog_lists_provider_choices(self):
        data=json.loads(self.req('/api/providers/catalog'))
        ids={x['id'] for x in data['providers']}
        self.assertTrue({'kaggle','colab','local-comfyui','nvidia-nim','runpod'} <= ids)
    def test_nvidia_status_never_returns_key(self):
        data=json.loads(self.req('/api/nvidia/status'))
        self.assertIn('configured',data); self.assertFalse(data['key_value_exposed'])
        self.assertNotIn('key',data)
    def test_nvidia_chat_requires_server_side_key(self):
        old=os.environ.pop('NVIDIA_API_KEY',None)
        old_file=server.PROVIDER_SECRET_FILE
        with tempfile.TemporaryDirectory() as d: server.PROVIDER_SECRET_FILE=Path(d)/'empty-provider-keys.json'
        try:
            req=urllib.request.Request(self.base+'/api/nvidia/chat',data=json.dumps({'messages':[{'role':'user','content':'test'}]}).encode(),headers={'X-Access-Key':server.ACCESS_KEY,'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(req)
            self.assertEqual(e.exception.code,503)
        finally:
            server.PROVIDER_SECRET_FILE=old_file
            if old is not None: os.environ['NVIDIA_API_KEY']=old

if __name__=='__main__': unittest.main()

class ProviderKeyStorageTests(unittest.TestCase):
    def test_provider_key_saved_outside_project_and_masked(self):
        import server
        old=server.PROVIDER_SECRET_FILE
        with tempfile.TemporaryDirectory() as d:
            server.PROVIDER_SECRET_FILE=Path(d)/'provider-keys.json'
            server.save_provider_secrets({'nvidia-nim':{'api_key':'nvapi-test-secret-1234','base_url':'https://integrate.api.nvidia.com/v1','model':'test-model'}})
            saved=json.loads(server.PROVIDER_SECRET_FILE.read_text())
            self.assertEqual(saved['nvidia-nim']['api_key'],'nvapi-test-secret-1234')
            self.assertEqual(server.PROVIDER_SECRET_FILE.stat().st_mode & 0o777,0o600)
            self.assertNotIn(str(server.PROVIDER_SECRET_FILE),str(server.ROOT))
        server.PROVIDER_SECRET_FILE=old
