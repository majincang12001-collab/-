import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'kaggle' / 'batch_generate_from_jobs.py'
spec = importlib.util.spec_from_file_location('manga_worker', MODULE_PATH)
worker = importlib.util.module_from_spec(spec)
# The worker imports torch/diffusers at module scope, so test the pure helper
# from its source AST without importing the GPU runtime.
import ast
source = MODULE_PATH.read_text('utf-8')
tree = ast.parse(source)
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_prompt')
mini = ast.Module(body=[node], type_ignores=[])
exec(compile(mini, str(MODULE_PATH), 'exec'), globals())

class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        return text.split()
    def decode(self, ids, skip_special_tokens=True):
        return ' '.join(ids)

class PromptBuilderTests(unittest.TestCase):
    def test_overlong_scene_is_truncated_not_dropped(self):
        prompt = build_prompt(FakeTokenizer(), ['wide shot', 'ancient city lantern street with gates and moonlight ' * 10, 'walking'], 12)
        self.assertTrue(prompt.startswith('wide shot'))
        self.assertIn('ancient city', prompt)
        self.assertLessEqual(len(prompt.replace(',', '').split()), 12)

    def test_priority_order_is_preserved(self):
        prompt = build_prompt(FakeTokenizer(), ['wide shot', 'city gate', 'heroine walking', 'anime style'], 20)
        self.assertLess(prompt.index('wide shot'), prompt.index('city gate'))
        self.assertLess(prompt.index('city gate'), prompt.index('heroine walking'))

    def test_empty_segments_are_ignored(self):
        self.assertEqual(build_prompt(FakeTokenizer(), ['', None, 'wide shot'], 10), 'wide shot')

if __name__ == '__main__':
    unittest.main()