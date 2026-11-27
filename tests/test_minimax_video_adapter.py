import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import minimax_video_adapter as mm

class MiniMaxAdapterTests(unittest.TestCase):
    def setUp(self):
        self.old=mm.SECRET_FILE
        self.tmp=tempfile.TemporaryDirectory()
        mm.SECRET_FILE=Path(self.tmp.name)/'provider-keys.json'
    def tearDown(self):
        mm.SECRET_FILE=self.old; self.tmp.cleanup()
    def test_status_does_not_expose_key(self):
        mm.SECRET_FILE.write_text(json.dumps({'minimax-video':{'api_key':'secret-test-key'}}))
        result=mm.status()
        self.assertTrue(result['configured']); self.assertNotIn('secret-test-key',json.dumps(result))
    def test_submit_uses_official_h3_endpoint_and_payload(self):
        mm.SECRET_FILE.write_text(json.dumps({'minimax-video':{'api_key':'secret-test-key'}}))
        class Response:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self): return b'{"task_id":"task_12345678","request_id":"req-1"}'
        with patch.object(mm.urllib.request,'urlopen',return_value=Response()) as call:
            result=mm.submit('cinematic scene',duration=6,resolution='768P',ratio='16:9')
        self.assertEqual(result['task_id'],'task_12345678')
        req=call.call_args.args[0]
        self.assertEqual(req.full_url,'https://api.minimax.io/v2/video_generation')
        self.assertEqual(req.get_header('Authorization'),'Bearer secret-test-key')
        body=json.loads(req.data)
        self.assertEqual(body['model'],'MiniMax-H3'); self.assertEqual(body['content'][0]['text'],'cinematic scene')
    def test_submit_validates_before_network(self):
        with self.assertRaises(ValueError): mm.submit('',duration=6)
        with self.assertRaises(ValueError): mm.submit('x',duration=30)
    def test_query_rejects_unsafe_task_id(self):
        with self.assertRaises(ValueError): mm.query('../etc/passwd')

if __name__=='__main__': unittest.main()
