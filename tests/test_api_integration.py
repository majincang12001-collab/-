"""Isolated integration tests for the manga-console HTTP API.
Runs against a temporary data directory and never touches the user's projects.
"""
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

MODULE_PATH = Path(__file__).resolve().parents[1] / 'server.py'
spec = importlib.util.spec_from_file_location('manga_server_test', MODULE_PATH)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='manga-api-test-')
        root = Path(cls.tmp.name)
        server.ROOT = root
        server.DATA = root / 'data'
        server.UPLOADS = server.DATA / 'uploads'
        server.PROJECTS = server.DATA / 'projects.json'
        server.PRODUCTION = server.DATA / 'production'
        server.RENDERS = server.DATA / 'renders'
        for d in (server.DATA, server.UPLOADS, server.PRODUCTION, server.RENDERS):
            d.mkdir(parents=True, exist_ok=True)
        server.PROJECTS.write_text('[]', encoding='utf-8')
        (root / 'index.html').write_text('<h1>test shell</h1>', encoding='utf-8')
        server.ACCESS_KEY = 'test-only-key'
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    def call(self, path, method='GET', data=None, key=True, content_type='application/json'):
        headers = {}
        if key:
            headers['X-Access-Key'] = 'test-only-key'
        if data is not None:
            headers['Content-Type'] = content_type
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_health_and_public_shell_only(self):
        status, body, _ = self.call('/api/health', key=False)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(self.call('/')[0], 200)
        self.assertEqual(self.call('/server.py')[0], 404)
        self.assertEqual(self.call('/data/access.key')[0], 404)

    def test_auth_required_for_private_api(self):
        self.assertEqual(self.call('/api/projects', key=False)[0], 401)
        self.assertEqual(self.call('/api/production?project_id=missing', key=False)[0], 401)

    def test_project_production_upload_download_roundtrip(self):
        status, body, _ = self.call(
            '/api/projects', 'POST',
            json.dumps({'name': '临时集成测试项目'}).encode()
        )
        self.assertEqual(status, 201)
        project_id = json.loads(body)['project']['id']
        production = {
            'project_id': project_id,
            'story': '测试故事',
            'characters': [{'name': '主角'}],
            'storyboard': [{'id': 1, 'visual': '测试画面', 'prompt': 'anime'}]
        }
        status, body, _ = self.call(
            '/api/production', 'POST',
            json.dumps(production, ensure_ascii=False).encode()
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['production']['story'], '测试故事')
        status, body, _ = self.call('/api/production?project_id=' + project_id)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['production']['storyboard'][0]['id'], 1)
        status, body, _ = self.call(
            '/api/upload?project_id=' + project_id + '&filename=frame.png',
            'POST', b'fake-png-bytes', content_type='application/octet-stream'
        )
        self.assertEqual(status, 201)
        stored = json.loads(body)['file']['stored']
        status, body, _ = self.call('/api/files/' + stored)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'fake-png-bytes')
        self.assertEqual(self.call('/api/files/../../server.py')[0], 404)

if __name__ == '__main__':
    unittest.main(verbosity=2)
