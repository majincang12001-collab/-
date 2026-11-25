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
from urllib.parse import quote
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
        server.WORKSPACES = server.DATA / 'project_workspaces'
        server.USAGE_FILE = server.DATA / 'usage' / 'ledger.json'
        server.TTS_JOBS = server.DATA / 'tts_jobs'
        server.GPU_JOBS = server.DATA / 'gpu_jobs'
        for d in (server.DATA, server.UPLOADS, server.PRODUCTION, server.RENDERS, server.WORKSPACES, server.USAGE_FILE.parent, server.TTS_JOBS, server.GPU_JOBS): d.mkdir(parents=True, exist_ok=True)
        server.PROJECTS.write_text('[]', encoding='utf-8')
        (root / 'index.html').write_text('<h1>test shell</h1>', encoding='utf-8')
        server.ACCESS_KEY = 'test-only-key'
        server.VALID_ACCESS_KEYS = {server.ACCESS_KEY}
        server.VALID_WORKER_KEYS = {server.ACCESS_KEY}
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(timeout=2); cls.tmp.cleanup()

    def call(self, path, method='GET', data=None, key=True, content_type='application/json'):
        headers = {}
        if key: headers['X-Access-Key'] = 'test-only-key'
        if data is not None: headers['Content-Type'] = content_type
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as r: return r.status, r.read(), r.headers
        except HTTPError as e: return e.code, e.read(), e.headers

    def test_health_and_public_shell_only(self):
        status, body, _ = self.call('/api/health', key=False)
        self.assertEqual(status, 200); self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(self.call('/')[0], 200)
        self.assertEqual(self.call('/server.py')[0], 404)
        self.assertEqual(self.call('/data/access.key')[0], 404)

    def test_auth_required_for_private_api(self):
        self.assertEqual(self.call('/api/projects', key=False)[0], 401)
        self.assertEqual(self.call('/api/production?project_id=missing', key=False)[0], 401)

    def test_admin_login_key_can_be_changed_and_old_key_is_rejected(self):
        old_key=server.ACCESS_KEY; old_valid=set(server.VALID_ACCESS_KEYS)
        status, body, _ = self.call('/api/settings/access-key','POST',json.dumps({'new_key':'new-test-login-key-2026'}).encode())
        self.assertEqual(status,200); self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(server.ACCESS_KEY,'new-test-login-key-2026')
        self.assertEqual(server.VALID_ACCESS_KEYS,{'new-test-login-key-2026'})
        self.assertEqual((server.DATA/'access.key').read_text(), 'new-test-login-key-2026')
        server.ACCESS_KEY=old_key; server.VALID_ACCESS_KEYS=old_valid

    def test_episode_data_is_isolated_and_project_folders_exist(self):
        status, body, _ = self.call('/api/projects', 'POST', json.dumps({'name':'分集隔离测试'}).encode())
        self.assertEqual(status, 201); pid = json.loads(body)['project']['id']
        status, body, _ = self.call('/api/episodes?project_id='+pid)
        self.assertEqual(status, 200); self.assertEqual(json.loads(body)['episodes'][0]['id'], 'episode-01')
        status, body, _ = self.call('/api/episodes', 'POST', json.dumps({'project_id':pid,'name':'第二集'},ensure_ascii=False).encode())
        self.assertEqual(status, 201); ep2 = json.loads(body)['episode']['id']
        for ep, story, shot in [('episode-01','第一集故事',1),(ep2,'第二集故事',2)]:
            payload={'project_id':pid,'episode_id':ep,'story':story,'characters':[],'storyboard':[{'id':shot}]}
            self.assertEqual(self.call('/api/production','POST',json.dumps(payload,ensure_ascii=False).encode())[0],200)
        a=json.loads(self.call(f'/api/production?project_id={pid}&episode_id=episode-01')[1])['production']
        b=json.loads(self.call(f'/api/production?project_id={pid}&episode_id={ep2}')[1])['production']
        self.assertEqual(a['story'],'第一集故事'); self.assertEqual(b['story'],'第二集故事')
        self.assertTrue((server.WORKSPACES/pid/'episodes'/'episode-01').is_dir())

    def test_gpu_keyframe_queue_accepts_three_to_five_selected_shots(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'关键帧选择测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        shots=[{'id':i,'visual':f'镜头{i}','prompt':f'prompt{i}'} for i in range(1,7)]
        self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'测试','characters':[],'storyboard':shots}).encode())
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[2,3]}).encode())
        self.assertEqual(status,400)
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[2,3,5,6]}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        job=json.loads((server.GPU_JOBS/(jid+'.json')).read_text('utf-8'))
        self.assertEqual(job['selected_shot_ids'],[2,3,5,6]); self.assertEqual(job['max_shots'],4)

    def test_per_character_reference_is_bound_to_matching_shots(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'角色参考绑定测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        status, body, _ = self.call('/api/upload?project_id='+pid+'&filename='+quote('角色_黑衣老者.png'), 'POST', b'fake-image-bytes', content_type='application/octet-stream')
        self.assertEqual(status,201); stored=json.loads(body)['file']['stored']
        characters=[{'name':'韩渐离','appearance':'少年黑发','reference_file':'','costume':'蓝黑衣服'}, {'name':'黑衣老者','appearance':'白须老人','reference_file':stored,'costume':'黑袍'}]
        shots=[{'id':1,'character':'韩渐离','shot_type':'中景'},{'id':2,'character':'黑衣老者','shot_type':'中景'},{'id':3,'character':'黑衣老者','shot_type':'特写'}]
        self.assertEqual(self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'测试','characters':characters,'storyboard':shots},ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3]}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        job=json.loads((server.GPU_JOBS/(jid+'.json')).read_text('utf-8'))
        self.assertNotIn('character_reference_file',job['storyboard'][0])
        self.assertEqual(job['storyboard'][1]['character_reference_file'],stored)
        req=Request(self.base+'/api/worker/reference?project_id='+pid+'&job_id='+jid+'&character_name='+quote('黑衣老者'),headers={'X-Access-Key':'test-only-key','X-Worker-Key':'test-only-key'})
        with urlopen(req,timeout=5) as r: self.assertEqual(r.read(),b'fake-image-bytes')

    def test_qwen_tts_queue_worker_result_and_audio_roundtrip(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'TTS试听测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'试听','characters':[],'storyboard':[]}).encode())
        payload={'project_id':pid,'episode_id':'episode-01','text':'说了让你申时来。','speaker':'Uncle_Fu','instruct':'自然的中国大陆普通话'}
        status, body, _ = self.call('/api/tts/queue','POST',json.dumps(payload,ensure_ascii=False).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        req=Request(self.base+'/api/tts/next',headers={'X-Access-Key':'test-only-key','X-Worker-Key':'test-only-key'})
        with urlopen(req,timeout=5) as r: claimed=json.loads(r.read())['job']
        self.assertEqual(claimed['job_id'],jid); self.assertEqual(claimed['status'],'running')
        wav=b'RIFF'+(36).to_bytes(4,'little')+b'WAVE'+b'fmt '+(16).to_bytes(4,'little')+b'\x01\x00\x01\x00'+(24000).to_bytes(4,'little')+(48000).to_bytes(4,'little')+b'\x02\x00\x10\x00'+b'data'+(0).to_bytes(4,'little')
        req=Request(self.base+'/api/tts/result?job_id='+jid,data=wav,headers={'X-Access-Key':'test-only-key','X-Worker-Key':'test-only-key','Content-Type':'audio/wav'},method='POST')
        with urlopen(req,timeout=5) as r: self.assertEqual(r.status,200)
        status, body, _ = self.call('/api/tts/status?job_id='+jid)
        self.assertEqual(json.loads(body)['job']['status'],'completed')
        status, body, headers = self.call('/api/tts/audio?job_id='+jid)
        self.assertEqual(status,200); self.assertEqual(body[:4],b'RIFF'); self.assertEqual(headers.get_content_type(),'audio/wav')

    def test_usage_quota_configuration_and_event_ledger(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'用量测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        provider={'name':'测试GPU','model':'T4','quota_limit':30,'unit':'镜头','quota_source':'手动登记'}
        self.assertEqual(self.call('/api/usage/provider','POST',json.dumps(provider,ensure_ascii=False).encode())[0],200)
        event={'project_id':pid,'provider':'测试GPU','task':'关键帧','units':3,'unit':'镜头','status':'completed'}
        self.assertEqual(self.call('/api/usage/event','POST',json.dumps(event,ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/usage?project_id='+pid)
        self.assertEqual(status,200); data=json.loads(body)
        self.assertEqual(data['summary']['测试GPU']['units'],3)
        self.assertEqual(next(x for x in data['providers'] if x['name']=='测试GPU')['quota_limit'],30)

    def test_project_production_upload_download_roundtrip(self):
        status, body, _ = self.call('/api/projects', 'POST', json.dumps({'name':'临时集成测试项目'}).encode())
        self.assertEqual(status, 201)
        project = json.loads(body)['project']; pid = project['id']
        production = {'project_id':pid,'story':'测试故事','characters':[{'name':'主角'}], 'storyboard':[{'id':1,'visual':'测试画面','prompt':'anime'}]}
        status, body, _ = self.call('/api/production', 'POST', json.dumps(production, ensure_ascii=False).encode())
        self.assertEqual(status, 200); self.assertEqual(json.loads(body)['production']['story'], '测试故事')
        status, body, _ = self.call('/api/production?project_id='+pid)
        self.assertEqual(status, 200); self.assertEqual(json.loads(body)['production']['storyboard'][0]['id'], 1)
        status, body, _ = self.call('/api/upload?project_id='+pid+'&filename=frame.png', 'POST', b'fake-png-bytes', content_type='application/octet-stream')
        self.assertEqual(status, 201); stored = json.loads(body)['file']['stored']
        status, body, _ = self.call('/api/files/'+stored)
        self.assertEqual(status, 200); self.assertEqual(body, b'fake-png-bytes')
        self.assertEqual(self.call('/api/files/../../server.py')[0], 404)
        status, body, _ = self.call('/api/pipeline?project_id='+pid)
        self.assertEqual(status, 200)
        pipeline = json.loads(body)
        self.assertTrue(pipeline['ok'])
        self.assertEqual(pipeline['summary']['shot_count'], 1)
        self.assertIn('asset_bible', pipeline['artifacts'])
        self.assertTrue((server.PRODUCTION/(pid+'_pipeline')/'qc_report.json').is_file())

if __name__ == '__main__': unittest.main(verbosity=2)
