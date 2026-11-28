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
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('build_prompt', 'camera_for_shot', 'camera_prompt_for_shot', 'shot_cast_names', 'shot_uses_character_adapter', 'renderable_cast_names', 'shot_needs_full_reference', 'adapter_scale_for_shot', 'character_region_boxes', 'should_use_text_only_for_missing_reference')]
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

    def test_missing_named_character_reference_fails_closed_instead_of_borrowing_hero(self):
        self.assertTrue(should_use_text_only_for_missing_reference(['爷爷'], False))
        self.assertTrue(should_use_text_only_for_missing_reference(['黑色乌鸦'], False))
        self.assertTrue(should_use_text_only_for_missing_reference([], True))
        self.assertFalse(should_use_text_only_for_missing_reference([], False))

    def test_scene_and_prop_continuity_are_prioritized_before_scene_detail_and_style(self):
        prompt = build_prompt(FakeTokenizer(), ['fixed character face and costume', 'wide cinematic framing', 'same graveyard layout and fog', 'same three incense sticks, only light when specified', 'detailed scene prose ' * 20, 'donghua style'], 24)
        self.assertIn('fixed character face', prompt)
        self.assertIn('same graveyard', prompt)
        self.assertIn('same three incense', prompt)
        self.assertNotIn('donghua style', prompt)
        self.assertLessEqual(len(prompt.split()), 24)

    def test_candidate_prompt_keeps_identity_and_each_view_angle_inside_budget(self):
        identity = '17岁少年；清秀略显冷峻的少年面容；黑色蓬松碎发；深灰黑色破旧交领衣袍'
        view = '身体轻微向左转约15度，面部仍清晰朝向镜头'
        style = 'Chinese 2D hand-painted donghua character concept art, semi-realistic anatomy, full body, neutral dark gray studio background'
        prompt = build_prompt(FakeTokenizer(), [identity, view, style], 75)
        self.assertIn('17岁少年', prompt)
        self.assertIn('向左转', prompt)
        self.assertIn('Chinese 2D', prompt)
        self.assertLessEqual(len(prompt.split()), 75)

    def test_bird_closeup_does_not_invent_subject_or_human_portrait(self):
        shot = {'shot_type': '特写', 'visual': '乌鸦抬起头，一双赤红眼睛反光'}
        prompt = camera_for_shot(shot)
        self.assertIn('close-up framing', prompt)
        self.assertNotIn('human portrait', prompt)
        self.assertNotIn('black crow', prompt)

    def test_authored_camera_direction_is_preserved_and_not_overwritten(self):
        shot={'shot_type':'近景','camera_prompt':'侧面拍摄，缓慢向左摇镜头','visual':'乌鸦望向树下'}
        prompt=camera_prompt_for_shot(shot)
        self.assertIn('侧面拍摄，缓慢向左摇镜头',prompt)
        self.assertIn('close-up framing',prompt)
        self.assertNotIn('pecking',prompt)

    def test_medium_framing_does_not_override_specific_camera_direction(self):
        shot = {'shot_type': '中景', 'character': '韩渐离', 'visual': '站在墓碑旁', 'camera_prompt':'侧面中景，人物在画面右侧'}
        prompt = camera_for_shot(shot)
        self.assertIn('medium framing', prompt)
        self.assertNotIn('waist', prompt)
        self.assertTrue(shot_uses_character_adapter(shot))

    def test_wide_framing_preserves_environment_without_inventing_character_count(self):
        shot = {'shot_type': '远景', 'character': '韩渐离', 'visual': '坟场'}
        prompt = camera_for_shot(shot)
        self.assertIn('wide cinematic framing', prompt)
        self.assertNotIn('one character', prompt)
        self.assertTrue(shot_uses_character_adapter(shot))

    def test_bird_wing_shot_does_not_invent_three_crows(self):
        shot = {'shot_type': '中景', 'visual': '一只乌鸦振翅，翅膀张开'}
        self.assertIn('medium framing', camera_for_shot(shot))
        self.assertNotIn('three black crows', camera_for_shot(shot))
        self.assertFalse(shot_uses_character_adapter(shot))

    def test_downward_reveal_does_not_invent_boy_if_cast_is_not_declared(self):
        shot = {'shot_type': '下摇见人', 'visual': '镜头向下摇，韩渐离独自站在树下'}
        self.assertIn('cinematic framing faithful to the described scene', camera_for_shot(shot))
        self.assertNotIn('teenage boy', camera_for_shot(shot))
        self.assertFalse(shot_uses_character_adapter(shot))

    def test_memorial_only_name_is_removed_from_renderable_cast(self):
        shot={'characters':['韩','许清月']}
        chars=[{'name':'韩','entity_type':'human'},{'name':'许清月','visual_lock':'只作为墓碑姓名出现，不生成实体'}]
        self.assertEqual(renderable_cast_names(shot,chars),['韩'])

    def test_prop_inscription_name_does_not_activate_character_adapter(self):
        shot = {'shot_type':'特写','visual':'a jade pendant engraved with Han Jianli, no person visible'}
        self.assertFalse(shot_uses_character_adapter(shot))
        self.assertEqual(adapter_scale_for_shot(shot,0.45),0.0)

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


class MultiCharacterMaskLayoutTests(unittest.TestCase):
    def test_two_characters_get_separate_left_right_regions(self):
        boxes = character_region_boxes(2)
        self.assertEqual(len(boxes), 2)
        self.assertLess(boxes[0][0], boxes[0][2])
        self.assertLess(boxes[1][0], boxes[1][2])
        self.assertLessEqual(boxes[0][2], boxes[1][0])

    def test_three_and_four_character_layouts_are_supported(self):
        self.assertEqual(len(character_region_boxes(3)), 3)
        self.assertEqual(len(character_region_boxes(4)), 4)

    def test_more_than_four_characters_is_rejected(self):
        with self.assertRaises(ValueError):
            character_region_boxes(5)
