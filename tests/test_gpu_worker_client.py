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


class WorkerReferenceRoutingTests(unittest.TestCase):
    def test_unreferenced_characters_are_text_only_and_do_not_borrow_other_reference(self):
        import ast
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'kaggle' / 'box_gpu_worker.py').read_text('utf-8')
        tree = ast.parse(source)
        names = {'characters_with_references', 'mark_shots_without_character_references'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<worker-reference-routing>', 'exec'), scope)
        job = {
            'characters': [
                {'name': '林月', 'reference_file': 'p1_hero.png'},
                {'name': '黑衣老者'},
            ],
            'storyboard': [
                {'id': 1, 'character': '林月'},
                {'id': 2, 'character': '黑衣老者'},
                {'id': 3, 'visual': '古城远景'},
            ],
        }
        referenced = scope['characters_with_references'](job)
        self.assertEqual([x['name'] for x in referenced], ['林月'])
        names = scope['mark_shots_without_character_references'](job)
        self.assertEqual(names, {'林月'})
        self.assertNotIn('text_only_character', job['storyboard'][0])
        self.assertTrue(job['storyboard'][1]['text_only_character'])
        self.assertTrue(job['storyboard'][2]['text_only_character'])


    def test_memorial_only_character_reference_is_never_downloaded(self):
        import ast
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'kaggle'/'box_gpu_worker.py').read_text('utf-8')
        tree=ast.parse(source); names={'characters_with_references'}
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        scope={}; exec(compile(ast.Module(body=nodes,type_ignores=[]),'<worker-memorial-reference>','exec'),scope)
        job={'characters':[{'name':'韩','reference_file':'han.png'},
                           {'name':'许清月','reference_file':'xuqingyue.png','visual_lock':'只作为墓碑姓名出现，不生成实体'}]}
        self.assertEqual([x['name'] for x in scope['characters_with_references'](job)],['韩'])

    def test_multi_character_shot_uses_independent_references_when_all_exist(self):
        import ast
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'kaggle' / 'box_gpu_worker.py').read_text('utf-8')
        tree = ast.parse(source)
        names = {'characters_with_references', 'mark_shots_without_character_references'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<worker-multi-reference>', 'exec'), scope)
        job = {'characters': [{'name':'林月','reference_file':'lin.png'}, {'name':'韩渐离','reference_file':'han.png'}],
               'storyboard':[{'id':1,'character_names':['林月','韩渐离']}]}
        scope['mark_shots_without_character_references'](job)
        shot = job['storyboard'][0]
        self.assertTrue(shot['multi_character_reference'])
        self.assertNotIn('text_only_character', shot)

    def test_worker_clears_stale_text_only_flag_when_all_multi_refs_exist(self):
        import ast
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'kaggle' / 'box_gpu_worker.py').read_text('utf-8')
        tree = ast.parse(source)
        names = {'characters_with_references', 'mark_shots_without_character_references'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<worker-stale-flag>', 'exec'), scope)
        job = {'characters':[{'name':'林月','reference_file':'lin.png'},{'name':'韩渐离','reference_file':'han.png'}],
               'storyboard':[{'id':1,'character_names':['林月','韩渐离'],'text_only_character':True}]}
        scope['mark_shots_without_character_references'](job)
        shot=job['storyboard'][0]
        self.assertTrue(shot['multi_character_reference'])
        self.assertNotIn('text_only_character',shot)

    def test_multi_character_shot_missing_one_reference_stays_text_only(self):
        import ast
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'kaggle' / 'box_gpu_worker.py').read_text('utf-8')
        tree = ast.parse(source)
        names = {'characters_with_references', 'mark_shots_without_character_references'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<worker-multi-reference-missing>', 'exec'), scope)
        job = {'characters': [{'name':'林月','reference_file':'lin.png'}, {'name':'韩渐离'}],
               'storyboard':[{'id':1,'character_names':['林月','韩渐离']}]}
        scope['mark_shots_without_character_references'](job)
        shot = job['storyboard'][0]
        self.assertTrue(shot['text_only_character'])
        self.assertNotIn('multi_character_reference', shot)
