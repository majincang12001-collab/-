import tempfile, unittest
from pathlib import Path
from model_vault import catalog, inventory, CATALOG

class ModelVaultTests(unittest.TestCase):
    def test_empty_vault_reports_storage_not_inference(self):
        with tempfile.TemporaryDirectory() as d:
            result = catalog(Path(d))
            self.assertEqual(result['storage']['asset_count'], 0)
            self.assertEqual(result['storage']['storage_role'], 'model_storage_only_not_inference')
            self.assertIn('GPU worker', result['inference_note'])

    def test_inventory_only_lists_supported_weight_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'checkpoints').mkdir()
            (root/'checkpoints'/'demo.safetensors').write_bytes(b'1234')
            (root/'checkpoints'/'notes.txt').write_text('ignore')
            result=inventory(root)
            self.assertEqual(result['asset_count'],1)
            self.assertEqual(result['total_bytes'],4)
            self.assertEqual(result['assets'][0]['relative_path'],'checkpoints/demo.safetensors')

    def test_restricted_license_is_explicit(self):
        row=next(x for x in CATALOG if x['id']=='guofeng41_xl')
        self.assertFalse(row['commercial_use'])
        self.assertIn('CC-BY-NC-SA-4.0',row['license'])

if __name__=='__main__': unittest.main()
