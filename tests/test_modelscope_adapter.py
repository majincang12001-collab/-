import json, os, tempfile, unittest
from pathlib import Path
import modelscope_adapter as ms

class ModelScopeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.old_file=ms.SECRET_FILE
        self.old_env=os.environ.pop('MODELSCOPE_TOKEN',None)
        self.tmp=tempfile.TemporaryDirectory()
        ms.SECRET_FILE=Path(self.tmp.name)/'provider-keys.json'
    def tearDown(self):
        ms.SECRET_FILE=self.old_file
        if self.old_env is not None: os.environ['MODELSCOPE_TOKEN']=self.old_env
        self.tmp.cleanup()
    def test_token_status_never_exposes_token(self):
        ms.SECRET_FILE.write_text(json.dumps({'modelscope-hub':{'access_token':'secret-token'}}))
        result=ms.status()
        self.assertTrue(result['configured'])
        self.assertFalse(result['token_value_exposed'])
        self.assertNotIn('secret-token',json.dumps(result))
    def test_public_mode_is_not_misreported_as_authenticated(self):
        result=ms.status()
        self.assertFalse(result['configured'])
        self.assertEqual(result['auth_mode'],'public_or_token_pending')
    def test_repo_id_validation(self):
        self.assertEqual(ms.validate_repo_id('Wan-AI/Wan2.2-I2V-A14B'),'Wan-AI/Wan2.2-I2V-A14B')
        for value in ('../secret','Wan-AI','Wan-AI/a/b',''):
            with self.assertRaises(ValueError): ms.validate_repo_id(value)
    def test_download_requires_explicit_opt_in(self):
        with self.assertRaises(PermissionError): ms.snapshot_download('Wan-AI/Wan2.2-I2V-A14B',self.tmp.name)

if __name__=='__main__': unittest.main()
