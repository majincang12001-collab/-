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
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('build_prompt', 'camera_for_shot', 'shot_uses_character_adapter', 'shot_needs_full_reference', 'adapter_scale_for_shot')]
mini = ast.Module(body=nodes, type_ignores=[])
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

    def test_costume_identity_is_prioritized_before_long_scene(self):
        prompt = build_prompt(FakeTokenizer(), ['full body shot', 'black robe cobalt blue shirt vivid blue trousers black cloth shoes', 'autumn graveyard with dry grass and many weathered gravestones ' * 5], 18)
        self.assertIn('vivid blue trousers', prompt)
        self.assertLess(prompt.index('vivid blue trousers'), prompt.index('autumn graveyard'))

    def test_empty_segments_are_ignored(self):
        self.assertEqual(build_prompt(FakeTokenizer(), ['', None, 'wide shot'], 10), 'wide shot')

    def test_bird_closeup_does_not_use_human_portrait_framing(self):
        shot = {'shot_type': '特写', 'visual': '乌鸦抬起头，一双赤红眼睛反光'}
        self.assertIn('black crow', camera_for_shot(shot))

    def test_character_medium_shot_is_strict_waist_up(self):
        shot = {'shot_type': '中景', 'character': '韩渐离', 'visual': '站在墓碑旁'}
        prompt = camera_for_shot(shot)
        self.assertIn('waist', prompt)
        self.assertIn('do not show knees or feet', prompt)

    def test_character_wide_shot_keeps_character_and_environment(self):
        shot = {'shot_type': '远景', 'character': '韩渐离', 'visual': '坟场'}
        prompt = camera_for_shot(shot)
        self.assertIn('one character', prompt)
        self.assertIn('full environment', prompt)
        self.assertTrue(shot_uses_character_adapter(shot))

    def test_bird_wing_shot_uses_medium_bird_framing(self):
        shot = {'shot_type': '中景', 'visual': '另两只乌鸦振翅，翅膀张开'}
        self.assertIn('three black crows', camera_for_shot(shot))
        self.assertFalse(shot_uses_character_adapter(shot))

    def test_downward_reveal_shows_boy_and_uses_character_anchor(self):
        shot = {'shot_type': '下摇见人', 'visual': '镜头向下摇，韩渐离独自站在树下'}
        self.assertIn('teenage boy', camera_for_shot(shot))
        self.assertTrue(shot_uses_character_adapter(shot))

    def test_full_body_shot_uses_complete_costume_reference(self):
        shot = {'shot_type': '全身镜头', 'camera_prompt': 'full body shot, head to toe'}
        self.assertTrue(shot_needs_full_reference(shot))

    def test_medium_shot_uses_face_reference(self):
        shot = {'shot_type': '中景', 'camera_prompt': 'medium shot, waist up'}
        self.assertFalse(shot_needs_full_reference(shot))

    def test_full_body_reference_gets_stronger_costume_weight(self):
        shot = {'character': '韩渐离', 'shot_type': '全身镜头'}
        self.assertEqual(adapter_scale_for_shot(shot, 0.15), 0.68)

    def test_medium_reference_uses_moderate_face_weight(self):
        shot = {'character': '韩渐离', 'shot_type': '中景'}
        self.assertEqual(adapter_scale_for_shot(shot, 0.15), 0.38)

    def test_environment_only_shot_disables_adapter(self):
        shot = {'shot_type': '远景', 'visual': '秋日坟场'}
        self.assertEqual(adapter_scale_for_shot(shot, 0.15), 0.0)

    def test_unreferenced_supporting_character_uses_text_only(self):
        shot = {'character': '黑衣老者', 'text_only_character': True, 'shot_type': '中景'}
        self.assertEqual(adapter_scale_for_shot(shot, 0.45), 0.0)

    def test_full_body_scene_reduces_adapter_to_preserve_background(self):
        shot = {'character': '韩渐离', 'scene_background': True, 'shot_type': '全身镜头'}
        self.assertEqual(adapter_scale_for_shot(shot, 0.68), 0.25)

if __name__ == '__main__':
    unittest.main()
