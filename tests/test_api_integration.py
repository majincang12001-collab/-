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
        server.SECRET_DIR = server.DATA / 'secrets'
        server.PREVIEW_TOKEN_FILE = server.SECRET_DIR / 'preview_tokens.json'
        server.SECRET_DIR.mkdir(parents=True, exist_ok=True)
        server.TTS_JOBS = server.DATA / 'tts_jobs'
        server.GPU_JOBS = server.DATA / 'gpu_jobs'
        server.SCNET_NODES = server.DATA / 'scnet_nodes'
        for d in (server.DATA, server.UPLOADS, server.PRODUCTION, server.RENDERS, server.WORKSPACES, server.USAGE_FILE.parent, server.TTS_JOBS, server.GPU_JOBS, server.SCNET_NODES): d.mkdir(parents=True, exist_ok=True)
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

    def test_ai_storyboard_cast_requires_canonical_user_registered_entity(self):
        canonical = [
            {'name':'韩','role':'主角','entity_type':'human','appearance':'黑发'},
            {'name':'许清月','role':'女主','entity_type':'memorial_only','visual_lock':'只作为墓碑姓名出现，不生成实体'},
        ]
        draft_shots = [
            {'id':1,'visual':'玉佩刻着许清月三个字','characters':['许清月']},
            {'id':2,'visual':'韩站在墓碑前','characters':['韩','陌生女子']},
        ]
        draft_characters = [{'name':'许清月','appearance':'白衣女子'},{'name':'陌生女子','appearance':'长发'}]
        shots, retained, suggestions = server.enforce_canonical_storyboard_cast(draft_shots,draft_characters,canonical)
        self.assertEqual(shots[0]['characters'],[])
        self.assertEqual(shots[0]['unverified_character_names'],['许清月'])
        self.assertEqual(shots[1]['characters'],['韩'])
        self.assertEqual(shots[1]['unverified_character_names'],['陌生女子'])
        self.assertEqual([x['name'] for x in retained],['韩','许清月'])
        self.assertEqual({x['name'] for x in suggestions},{'许清月','陌生女子'})

    def test_health_and_public_shell_only(self):
        status, body, _ = self.call('/api/health', key=False)
        self.assertEqual(status, 200); self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(self.call('/')[0], 200)
        self.assertEqual(self.call('/server.py')[0], 404)
        self.assertEqual(self.call('/data/access.key')[0], 404)

    def test_scnet_worker_heartbeat_is_worker_key_protected_and_status_is_sanitized(self):
        payload=json.dumps({'node_id':'f11r1n03','hostname':'compute-test','partition':'hx1hdtest',
                            'job_id':'12345','gpu_summary':'Hygon DCU test output','python':'3.12',
                            'torch':'torch import test','dtk_detected':True}).encode()
        denied=Request(self.base+'/api/worker/scnet/heartbeat',data=payload,
                       headers={'Content-Type':'application/json','X-Worker-Key':'wrong-key'},method='POST')
        try:
            urlopen(denied,timeout=5)
            self.fail('wrong worker key should be rejected')
        except HTTPError as e:
            self.assertEqual(e.code,401)
        req=Request(self.base+'/api/worker/scnet/heartbeat',data=payload,
                    headers={'Content-Type':'application/json','X-Worker-Key':'test-only-key'},method='POST')
        with urlopen(req,timeout=5) as response:
            self.assertEqual(response.status,200)
            self.assertTrue(json.loads(response.read())['recorded'])
        req=Request(self.base+'/api/worker/scnet/status',headers={'X-Worker-Key':'test-only-key'})
        with urlopen(req,timeout=5) as response:
            result=json.loads(response.read())
        self.assertEqual(result['provider'],'scnet-hygon-dcu')
        self.assertEqual(result['nodes'][0]['node_id'],'f11r1n03')
        self.assertNotIn('worker_key',result['nodes'][0])

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

    def test_one_time_login_bootstrap_changes_human_key_without_changing_worker_key(self):
        old_file=server.ACCESS_KEY_FILE
        old_setup=server.SETUP_TOKEN_FILE
        old_key=server.ACCESS_KEY
        old_valid=set(server.VALID_ACCESS_KEYS)
        old_worker=set(server.VALID_WORKER_KEYS)
        try:
            server.ACCESS_KEY_FILE=Path(self.tmp.name)/'human-login.key'
            server.SETUP_TOKEN_FILE=Path(self.tmp.name)/'bootstrap.json'
            server.SETUP_TOKEN_FILE.write_text(json.dumps({'token':'one-time-test-token','expires_at':9999999999}))
            payload=json.dumps({'new_key':'test-human-login-2026'}).encode()
            req=Request(self.base+'/api/setup/login',data=payload,headers={'Content-Type':'application/json','X-Setup-Token':'one-time-test-token'},method='POST')
            with urlopen(req,timeout=5) as response:
                self.assertEqual(response.status,200)
                self.assertTrue(json.loads(response.read())['ok'])
            self.assertEqual(server.ACCESS_KEY,'test-human-login-2026')
            self.assertEqual(server.VALID_ACCESS_KEYS,{'test-human-login-2026'})
            self.assertEqual(server.VALID_WORKER_KEYS,old_worker)
            self.assertFalse(server.SETUP_TOKEN_FILE.exists())
            self.assertEqual(server.ACCESS_KEY_FILE.read_text(),'test-human-login-2026')
        finally:
            server.ACCESS_KEY_FILE=old_file
            server.SETUP_TOKEN_FILE=old_setup
            server.ACCESS_KEY=old_key
            server.VALID_ACCESS_KEYS=old_valid
            server.VALID_WORKER_KEYS=old_worker

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

    def test_character_candidate_queue_builds_four_story_anchored_variants_without_reference(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'角色候选生成测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        chars=[{'name':'韩','age':'17岁','appearance':'清秀略冷峻，黑色蓬松碎发','costume':'深灰黑色破旧交领衣袍','visual_lock':'同一张脸，少年感固定','negative_constraints':'不得改变年龄、发型、服装','role':'主角'},
               {'name':'许清月','appearance':'本集不设定实体外貌','negative_constraints':'不生成实体人物','role':'女主'}, {'name':'黑色乌鸦','appearance':'黑色羽毛、猩红眼睛','visual_lock':'黑羽红眼','role':'动物角色','entity_type':'animal'}]
        self.assertEqual(self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'剧情角色测试','hero':{'name':'韩','description':'17岁黑发少年'},'characters':chars,'storyboard':[]},ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/character-candidates/queue','POST',json.dumps({'project_id':pid,'character_name':'韩'},ensure_ascii=False).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        job=json.loads((server.GPU_JOBS/(jid+'.json')).read_text('utf-8'))
        self.assertEqual(job['task_type'],'character_candidates'); self.assertEqual(job['max_shots'],4)
        status, body, _ = self.call('/api/character-candidates/queue','POST',json.dumps({'project_id':pid,'character_name':'韩'},ensure_ascii=False).encode())
        self.assertEqual(status,202); self.assertEqual(json.loads(body)['job_id'],jid)
        self.assertTrue(job['text_only_reference']); self.assertEqual(len(job['storyboard']),4)
        self.assertIn('17岁',job['candidate_identity_prompt']); self.assertIn('深灰黑色破旧交领衣袍',job['candidate_identity_prompt'])
        self.assertEqual(len(job['candidate_identity_prompt']), len(job['candidate_identity_prompt'].strip()))
        self.assertTrue(all(x.get('candidate_view_prompt') for x in job['storyboard']))
        self.assertEqual(len({x['candidate_view_prompt'] for x in job['storyboard']}), 4)
        self.assertEqual(self.call('/api/character-candidates/queue','POST',json.dumps({'project_id':pid,'character_name':'许清月'},ensure_ascii=False).encode())[0],409)
        status, body, _ = self.call('/api/character-candidates/queue','POST',json.dumps({'project_id':pid,'character_name':'黑色乌鸦'},ensure_ascii=False).encode())
        self.assertEqual(status,202); crow_job=json.loads((server.GPU_JOBS/(json.loads(body)['job_id']+'.json')).read_text('utf-8'))
        self.assertEqual(crow_job['character_entity_type'],'animal')
        self.assertIn('单只动物',crow_job['storyboard'][0]['prompt'])

    def test_character_candidate_result_and_approval_bind_only_selected_character(self):
        import io, zipfile
        from PIL import Image
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'角色候选验收测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        chars=[{'name':'韩','age':'17岁','appearance':'黑发少年','costume':'深色旧袍','role':'主角'}, {'name':'爷爷','age':'70岁','appearance':'白灰头发老人','costume':'浅灰道袍','role':'配角'}]
        self.call('/api/production','POST',json.dumps({'project_id':pid,'hero':{'name':'韩'},'characters':chars,'storyboard':[]},ensure_ascii=False).encode())
        status, body, _ = self.call('/api/character-candidates/queue','POST',json.dumps({'project_id':pid,'character_name':'韩'}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            for i in (1,2):
                img=io.BytesIO(); Image.new('RGB',(896,512),(i*40,20,30)).save(img,format='PNG'); z.writestr(f'shot_{i:04d}.png',img.getvalue())
        req=Request(self.base+'/api/worker/character-candidates/result?job_id='+jid,data=archive.getvalue(),headers={'X-Worker-Key':'test-only-key','Content-Type':'application/zip'},method='POST')
        with urlopen(req,timeout=5) as r: self.assertEqual(r.status,200); self.assertEqual(json.loads(r.read())['candidate_count'],2)
        status, body, _ = self.call('/api/character-candidates/approve','POST',json.dumps({'project_id':pid,'job_id':jid,'candidate_id':'candidate_2'}).encode())
        self.assertEqual(status,200)
        prod=json.loads(self.call('/api/production?project_id='+pid)[1])['production']
        han=next(c for c in prod['characters'] if c['name']=='韩'); grandpa=next(c for c in prod['characters'] if c['name']=='爷爷')
        self.assertTrue(han['reference_file'].endswith('_2.png')); self.assertEqual(han['reference_status'],'approved')
        self.assertTrue((server.WORKSPACES/pid/'characters'/'episode-01'/'candidates'/'韩'/han['reference_file']).is_file())
        self.assertNotIn('reference_file',grandpa); self.assertEqual(prod['base_image_file'],han['reference_file']); self.assertTrue(prod['approved_base_image'])
        self.assertEqual(self.call('/api/character-candidates?project_id='+pid)[0],200)

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

    def test_gravestone_only_record_never_becomes_a_human_body_when_entity_type_is_omitted(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'墓碑实体隔离测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        characters=[{'name':'许清月','role':'女主','visual_lock':'仅作为墓碑姓名出现，不生成实体','appearance':'长发女子','costume':'白衣'}]
        shots=[{'id':i,'visual':'旧墓碑刻着许清月三个字','action':'只拍摄墓碑','characters':['许清月']} for i in (1,2,3)]
        self.call('/api/production','POST',json.dumps({'project_id':pid,'characters':characters,'storyboard':shots},ensure_ascii=False).encode())
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3]}).encode())
        self.assertEqual(status,202)
        job=json.loads((server.GPU_JOBS/(json.loads(body)['job_id']+'.json')).read_text('utf-8'))
        for shot in job['storyboard']:
            self.assertIn('不得生成对应人物',shot['prompt'])
            self.assertNotIn('长发女子',shot['prompt'])
            self.assertNotIn('白衣',shot['prompt'])
            self.assertEqual(shot['character_names'],[])
            self.assertEqual(shot['memorial_reference_names'],['许清月'])

    def test_gpu_queue_rejects_unregistered_name_even_if_it_appears_on_a_prop(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'铭文不得召唤人物测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        characters=[{'name':'韩','role':'主角','appearance':'黑发'}]
        shots=[{'id':1,'visual':'玉佩上刻着许清月三个字','action':'只拍摄玉佩特写','prompt':'玉佩铭文许清月','characters':['许清月']}]
        self.call('/api/production','POST',json.dumps({'project_id':pid,'characters':characters,'storyboard':shots},ensure_ascii=False).encode())
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,1,1]}).encode())
        self.assertEqual(status,400)  # selection shape is rejected before queue creation
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,1,1],'automatic_batch':True}).encode())
        self.assertEqual(status,400)  # duplicate IDs rejected; no task is created
        # A valid-size batch with the same unknown cast must fail closed.
        shots=[dict(shots[0],id=i) for i in (1,2,3)]
        self.call('/api/production','POST',json.dumps({'project_id':pid,'characters':characters,'storyboard':shots},ensure_ascii=False).encode())
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3]}).encode())
        self.assertEqual(status,409)
        self.assertIn('许清月',json.loads(body)['error'])

    def test_gpu_queue_adds_scene_and_prop_continuity_anchors(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'场景道具连续性测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        shots=[{'id':1,'visual':'枯柿子树挂着腐烂柿子','action':'夜风吹动枝条','characters':[]},
               {'id':2,'visual':'黑色乌鸦站在柿子树枝头','action':'乌鸦啄食柿子','characters':['黑色乌鸦']},
               {'id':3,'visual':'爷爷在乱坟岗墓碑前插上三炷香','action':'香头尚未燃起','characters':['爷爷']},
               {'id':4,'visual':'暗青色龙形玉佩出现在墓碑前','action':'龙眼微微泛红','characters':[]}]
        chars=[{'name':'爷爷','role':'配角','appearance':'白灰头发老人','costume':'浅灰道袍'},{'name':'黑色乌鸦','role':'动物角色','appearance':'黑羽红眼'}]
        self.call('/api/production','POST',json.dumps({'project_id':pid,'characters':chars,'storyboard':shots},ensure_ascii=False).encode())
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3,4],'automatic_batch':True}).encode())
        self.assertEqual(status,202); job=json.loads((server.GPU_JOBS/(json.loads(body)['job_id']+'.json')).read_text('utf-8'))
        tree,crow,grandpa,jade=job['storyboard']
        self.assertEqual(tree['scene_key'],crow['scene_key'])
        self.assertIn('枯萎柿子树',tree['scene_anchor'])
        self.assertIn('乱坟岗',grandpa['scene_anchor'])
        self.assertIn('墓前三炷香',grandpa['prop_anchors'][0])
        self.assertIn('暗青色龙形玉佩',jade['prop_anchors'][0])
        self.assertEqual(tree['prop_states']['dragon_jade_pendant'],'absent')
        self.assertEqual(grandpa['prop_states']['incense'],'unlit')
        self.assertEqual(jade['prop_states']['dragon_jade_pendant'],'revealed_on_ground')
        self.assertIn('identity_guard_prompt',crow)

    def test_gpu_queue_enforces_shot_scoped_cast_and_animal_guards(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'镜头角色约束测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        characters=[{'name':'韩','appearance':'17岁少年黑发','costume':'深灰破旧交领衣袍','role':'主角'},
                    {'name':'爷爷','appearance':'白灰头发老人','costume':'浅灰破败道袍','role':'配角'},
                    {'name':'黑色乌鸦','appearance':'黑羽红眼','role':'动物角色'}]
        shots=[{'id':1,'visual':'空坟地夜景','prompt':'空坟地夜景','characters':[]},
               {'id':2,'visual':'乌鸦啄柿子','prompt':'乌鸦啄柿子','characters':['黑色乌鸦']},
               {'id':3,'visual':'韩独自站立','prompt':'韩独自站立','characters':['韩']}]
        self.assertEqual(self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'测试','characters':characters,'storyboard':shots},ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3],'automatic_batch':True}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        job=json.loads((server.GPU_JOBS/(jid+'.json')).read_text('utf-8'))
        empty,crow,han=job['storyboard']
        self.assertNotIn('角色身份与出场约束',empty['prompt'])
        self.assertIn('不得生成任何人类角色',crow['prompt'])
        self.assertIn('人物面孔',crow['negative_prompt'])
        self.assertNotIn('浅灰破败道袍',han['prompt'])
        self.assertIn('只允许出现已指定角色：韩',han['prompt'])

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

    def test_named_character_reference_never_falls_back_to_hero_image(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'防止角色参考串用'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        status, body, _ = self.call('/api/upload?project_id='+pid+'&filename='+quote('approved-hero.png'), 'POST', b'hero-reference-bytes', content_type='application/octet-stream')
        self.assertEqual(status,201); hero_ref=json.loads(body)['file']['stored']
        production={'project_id':pid,'story':'测试','approved_base_image':True,'base_image_file':hero_ref,
                    'characters':[{'name':'韩','role':'主角','appearance':'少年','costume':'深色衣服'},
                                  {'name':'爷爷','role':'配角','appearance':'老人','costume':'浅灰道袍'}],
                    'storyboard':[{'id':1,'characters':['韩'],'visual':'韩独自站立'},
                                  {'id':2,'characters':['爷爷'],'visual':'爷爷独自站立'},
                                  {'id':3,'characters':['韩','爷爷'],'visual':'两人同框'}]}
        self.assertEqual(self.call('/api/production','POST',json.dumps(production,ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3]}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        headers={'X-Access-Key':'test-only-key','X-Worker-Key':'test-only-key'}
        req=Request(self.base+'/api/worker/reference?project_id='+pid+'&job_id='+jid+'&character_name='+quote('韩'),headers=headers)
        with urlopen(req,timeout=5) as r: self.assertEqual(r.read(),b'hero-reference-bytes')
        req=Request(self.base+'/api/worker/reference?project_id='+pid+'&job_id='+jid+'&character_name='+quote('爷爷'),headers=headers)
        with self.assertRaises(HTTPError) as ctx: urlopen(req,timeout=5)
        self.assertEqual(ctx.exception.code,404)
        self.assertIn('专属参考图',ctx.exception.read().decode('utf-8'))

    def test_multi_character_shot_enables_independent_references_when_all_cast_refs_exist(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'多人双参考图启用测试'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        stored_refs=[]
        for name in ('林月','韩渐离'):
            status, body, _ = self.call('/api/upload?project_id='+pid+'&filename='+quote(name+'.png'), 'POST', b'fake-reference-'+name.encode(), content_type='application/octet-stream')
            self.assertEqual(status,201); stored_refs.append(json.loads(body)['file']['stored'])
        characters=[{'name':'林月','reference_file':stored_refs[0]},{'name':'韩渐离','reference_file':stored_refs[1]}]
        shots=[{'id':i,'characters':['林月','韩渐离'],'visual':'两人并肩'} for i in range(1,4)]
        self.assertEqual(self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'测试','characters':characters,'storyboard':shots},ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3]}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        job=json.loads((server.GPU_JOBS/(jid+'.json')).read_text('utf-8'))
        for shot in job['storyboard']:
            self.assertTrue(shot['multi_character_reference'])
            self.assertNotIn('text_only_character',shot)
            self.assertEqual(shot['character_names'],['林月','韩渐离'])

    def test_multi_character_shot_never_borrows_one_character_reference(self):
        status, body, _ = self.call('/api/projects','POST',json.dumps({'name':'多人同镜头身份保护'}).encode())
        self.assertEqual(status,201); pid=json.loads(body)['project']['id']
        status, body, _ = self.call('/api/upload?project_id='+pid+'&filename='+quote('角色_林月.png'), 'POST', b'fake-reference', content_type='application/octet-stream')
        self.assertEqual(status,201); stored=json.loads(body)['file']['stored']
        characters=[{'name':'林月','reference_file':stored},{'name':'韩渐离','reference_file':''}]
        shots=[{'id':i,'characters':['林月','韩渐离'],'visual':'两人并肩'} for i in range(1,4)]
        self.assertEqual(self.call('/api/production','POST',json.dumps({'project_id':pid,'story':'测试','characters':characters,'storyboard':shots},ensure_ascii=False).encode())[0],200)
        status, body, _ = self.call('/api/gpu/queue','POST',json.dumps({'project_id':pid,'selected_shot_ids':[1,2,3]}).encode())
        self.assertEqual(status,202); jid=json.loads(body)['job_id']
        job=json.loads((server.GPU_JOBS/(jid+'.json')).read_text('utf-8'))
        for shot in job['storyboard']:
            self.assertTrue(shot['text_only_character'])
            self.assertNotIn('character_reference_file',shot)
            self.assertEqual(shot['character_names'],['林月','韩渐离'])

    def test_qwen_tts_worker_result_and_audio_roundtrip(self):
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

    def test_preview_link_is_read_only_and_revocable(self):
        pid='abcdef123456'
        server.PROJECTS.write_text(json.dumps([{'id':pid,'name':'预览测试项目'}],ensure_ascii=False),encoding='utf-8')
        pf=server.PRODUCTION/(pid+'_pipeline-test.json')
        pf.write_text('{}',encoding='utf-8')
        token,expires=server.issue_preview_token()
        headers={'X-Preview-Token':token}
        req=Request(self.base+'/api/preview/data',headers=headers,method='GET')
        with urlopen(req,timeout=5) as r:
            self.assertEqual(r.status,200)
            payload=json.loads(r.read())
        self.assertEqual(payload['project']['id'],pid)
        self.assertNotIn('api_key',json.dumps(payload))
        # The same token cannot call admin/write APIs.
        req=Request(self.base+'/api/settings/access-key',data=b'{"new_key":"new-password-123"}',headers={**headers,'Content-Type':'application/json'},method='POST')
        try:
            urlopen(req,timeout=5)
            self.fail('preview token must not authorize admin writes')
        except HTTPError as e:
            self.assertEqual(e.code,401)
        req=Request(self.base+'/api/preview/end',data=b'{}',headers=headers,method='POST')
        with urlopen(req,timeout=5) as r:
            self.assertEqual(json.loads(r.read())['revoked'],True)
        req=Request(self.base+'/api/preview/data',headers=headers,method='GET')
        try:
            urlopen(req,timeout=5)
            self.fail('revoked preview token must be rejected')
        except HTTPError as e:
            self.assertEqual(e.code,401)

if __name__ == '__main__': unittest.main(verbosity=2)
