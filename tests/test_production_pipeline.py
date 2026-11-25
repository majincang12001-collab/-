import unittest
from production_pipeline import (build_asset_bible, build_shot_plan, build_generation_brief,
    audit_shots, route_regeneration, build_canonical_timeline, build_generation_manifest, compile_production)

class ProductionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.data = {'project_id':'p1','story':'夜色降临。','characters':[{'name':'林月','age':'19岁','appearance':'黑色长发','costume':'蓝银长袍','visual_lock':'左眼下小痣','negative_constraints':'禁止变发色','voice_profile':'清冷女声'}],
          'storyboard':[{'id':1,'shot_type':'远景','visual':'古城门','action':'抬头','dialogue':'旁白','prompt':'林月 黑色长发 蓝银长袍 古城门'}]}
    def test_asset_bible_and_brief_keep_identity_anchors(self):
        bible=build_asset_bible(self.data); plan=build_shot_plan(self.data); brief=build_generation_brief(plan,bible)
        self.assertEqual(bible['characters'][0]['name'],'林月')
        self.assertIn('黑色长发',brief['jobs'][0]['character_anchors'][0]['identity_anchors'])
        self.assertIn('左眼下小痣',brief['jobs'][0]['prompt'])
        self.assertIn('禁止变发色',brief['jobs'][0]['negative_prompt'])
        self.assertEqual(brief['jobs'][0]['character_anchors'][0]['voice_profile'],'清冷女声')
    def test_character_reference_images_match_character_filename(self):
        bible=build_asset_bible(self.data,[{'name':'林月_reference.png','stored':'p1_ab12_林月_reference.png','size':123}])
        self.assertEqual(bible['characters'][0]['reference_images'],['p1_ab12_林月_reference.png'])
    def test_qc_routes_only_failed_shot(self):
        plan=build_shot_plan(self.data); bible=build_asset_bible(self.data)
        report=audit_shots(plan,bible,project_files=[])
        regen=route_regeneration(report)
        self.assertEqual(report['status'],'needs_attention')
        self.assertEqual(regen['jobs'][0]['action'],'regenerate_keyframe')
        self.assertTrue(regen['jobs'][0]['preserve_accepted_shots'])
    def test_manifest_never_invents_unknown_model_provenance(self):
        manifest=build_generation_manifest(build_shot_plan(self.data))
        self.assertIsNone(manifest['shots'][0]['model_id'])
        self.assertEqual(manifest['shots'][0]['provenance_completeness'],'partial')
    def test_canonical_timeline_accumulates_shot_durations(self):
        plan=build_shot_plan(self.data); plan['shots'].append(dict(plan['shots'][0],shot_id='shot_0002',duration_seconds=2))
        timeline=build_canonical_timeline('p1','test',plan)
        self.assertEqual(timeline['duration_seconds'],6.0)
        self.assertEqual(timeline['tracks'][0]['children'][1]['start_seconds'],4.0)
    def test_compile_emits_separate_stage_artifacts(self):
        out=compile_production(self.data)
        self.assertEqual(set(out),{'project_spec','asset_bible','shot_plan','generation_brief','generation_manifest','qc_report','regeneration_brief','canonical_timeline'})
        self.assertTrue(out['qc_report']['structural_only'])

if __name__=='__main__': unittest.main()
