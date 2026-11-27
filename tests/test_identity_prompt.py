import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kaggle"))
from identity_prompt import shot_cast_names, character_anchor_text, camera_guidance

class IdentityPromptTests(unittest.TestCase):
    def test_cast_names_support_arrays_and_delimited_legacy_strings(self):
        self.assertEqual(shot_cast_names({"character":"林月、韩渐离"}), ["林月", "韩渐离"])
        self.assertEqual(shot_cast_names({"characters":["林月", "韩渐离", "林月"]}), ["林月", "韩渐离"])
    def test_structured_costume_and_visual_lock_survive_freeform_anchor(self):
        c={"name":"林月", "costume":"白银长袍", "visual_lock":"银色花形发簪", "appearance":"深紫长发", "prompt_anchor":"仙侠女主，清冷气质"}
        anchor=character_anchor_text(c)
        for item in ("林月", "白银长袍", "银色花形发簪", "深紫长发", "仙侠女主，清冷气质"):
            self.assertIn(item, anchor)

    def test_camera_guidance_never_invents_subject_or_replaces_action(self):
        fruit = camera_guidance({'shot_type':'特写','visual':'腐烂柿子特写','action':'静止'})
        crow = camera_guidance({'shot_type':'近景','visual':'一只乌鸦歪头望向树下'})
        self.assertNotIn('three black crows', fruit)
        self.assertNotIn('pecking', crow)
        self.assertIn('close framing', fruit)

    def test_camera_guidance_respects_hand_detail_and_wide_environment(self):
        hand = camera_guidance({'shot_type':'手部特写'})
        wide = camera_guidance({'shot_type':'远景'})
        self.assertIn('detail framing', hand)
        self.assertIn('wide cinematic framing', wide)

    def test_character_anchor_keeps_visual_lock_separate_from_other_cast(self):
        a={"name":"林月", "appearance":"黑色长发", "costume":"蓝银长袍", "visual_lock":"左眼下小痣"}
        b={"name":"韩渐离", "appearance":"银色短发", "costume":"黑衣", "visual_lock":"右眉疤痕"}
        self.assertIn("左眼下小痣", character_anchor_text(a))
        self.assertNotIn("右眉疤痕", character_anchor_text(a))
        self.assertIn("右眉疤痕", character_anchor_text(b))

if __name__ == "__main__": unittest.main()
