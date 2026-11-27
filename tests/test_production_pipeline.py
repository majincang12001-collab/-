import unittest
from production_pipeline import (build_asset_bible, build_shot_plan, build_generation_brief,
    audit_shots, route_regeneration, build_canonical_timeline, build_generation_manifest, compile_production, derive_prop_states, prop_state_anchors)

class ProductionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.data = {'project_id':'p1','story':'夜色降临。','characters':[{'name':'林月','age':'19岁','appearance':'黑色长发','costume':'蓝银长袍','visual_lock':'左眼下小痣','negative_constraints':'禁止变发色','voice_profile':'清冷女声'}],
          'storyboard':[{'id':1,'characters':['林月'],'shot_type':'远景','visual':'古城门','action':'抬头','dialogue':'旁白','prompt':'林月 黑色长发 蓝银长袍 古城门'}]}
    def test_asset_bible_and_brief_keep_identity_anchors(self):
        bible=build_asset_bible(self.data); plan=build_shot_plan(self.data); brief=build_generation_brief(plan,bible)
        self.assertEqual(bible['characters'][0]['name'],'林月')
        self.assertIn('黑色长发',brief['jobs'][0]['character_anchors'][0]['identity_anchors'])
        self.assertIn('左眼下小痣',brief['jobs'][0]['prompt'])
        self.assertIn('禁止变发色',brief['jobs'][0]['negative_prompt'])
        self.assertEqual(brief['jobs'][0]['character_anchors'][0]['voice_profile'],'清冷女声')
    def test_shot_only_receives_its_named_character_identity_and_reference(self):
        data=dict(self.data)
        data['characters']=[dict(self.data['characters'][0], reference_images=['linyue.png']),
            {'name':'韩渐离','appearance':'银色短发','costume':'黑衣','visual_lock':'右眉疤痕','reference_images':['han.png']}]
        data['storyboard']=[{'id':1,'characters':['林月'],'scene_key':'night_gate','prompt':'林月站在城门前'},
            {'id':2,'characters':['韩渐离'],'scene_key':'night_gate','prompt':'韩渐离拔剑'}]
        bible=build_asset_bible(data); plan=build_shot_plan(data); brief=build_generation_brief(plan,bible)
        self.assertEqual([x['name'] for x in brief['jobs'][0]['character_anchors']],['林月'])
        self.assertEqual(brief['jobs'][0]['reference_images'],['linyue.png'])
        self.assertEqual(brief['jobs'][1]['reference_images'],['han.png'])
        self.assertNotIn('黑衣',brief['jobs'][0]['prompt'])

    def test_scene_scope_and_temporary_role_survive_asset_bible(self):
        data=dict(self.data); data['characters']=[dict(self.data['characters'][0],role='临时角色',scene_key='inn_evening',continuity_note='同一客栈掌柜，灰胡子不变')]
        char=build_asset_bible(data)['characters'][0]
        self.assertEqual(char['role'],'临时角色')
        self.assertEqual(char['scene_scope'],'inn_evening')
        self.assertIn('灰胡子',char['continuity_note'])

    def test_character_reference_is_not_inferred_from_filename_containing_name(self):
        bible=build_asset_bible(self.data,[{'name':'林月_墓碑照片.png','stored':'p1_ab12_林月_墓碑照片.png','size':123}])
        self.assertEqual(bible['characters'][0]['reference_images'],[])
        self.assertEqual(bible['characters'][0]['reference_status'],'missing')

    def test_name_on_prop_never_summons_character_or_reference(self):
        data={'characters':[{'name':'许清月','appearance':'长发女子','costume':'白衣','reference_file':'xuqingyue.png'}],
              'storyboard':[{'id':1,'characters':[],'visual':'暗青色玉佩表面刻着许清月三个字','action':'镜头只拍摄玉佩特写','prompt':'玉佩铭文清晰可见：许清月'}]}
        bible=build_asset_bible(data,project_files=[{'name':'许清月_玉佩特写.png','stored':'prop_xuqingyue.png'}])
        plan=build_shot_plan(data); job=build_generation_brief(plan,bible)['jobs'][0]
        report=audit_shots(plan,bible,project_files=[])
        self.assertEqual(job['character_anchors'],[])
        self.assertEqual(job['reference_images'],[])
        self.assertNotIn('长发女子',job['prompt'])
        self.assertFalse(any(x.startswith('character_anchor_not_in_prompt:') for x in report['shots'][0]['issues']))
        self.assertFalse(any(x.startswith('character_reference_missing:') for x in report['shots'][0]['warnings']))
    def test_missing_keyframe_is_pending_generation_not_a_structural_failure(self):
        plan=build_shot_plan(self.data); bible=build_asset_bible(self.data)
        report=audit_shots(plan,bible,project_files=[])
        regen=route_regeneration(report)
        self.assertEqual(report['status'],'awaiting_generation')
        self.assertEqual(report['summary']['needs_attention'],0)
        self.assertEqual(report['summary']['pending_generation_count'],1)
        self.assertEqual(report['shots'][0]['pending'],['awaiting_keyframe_generation'])
        self.assertEqual(report['shots'][0]['issues'],[])
        self.assertEqual(regen['jobs'],[])
    def test_qc_does_not_require_unseen_characters_in_environment_shot(self):
        data=dict(self.data)
        data['characters']=[dict(self.data['characters'][0]),
            {'name':'爷爷','appearance':'白灰头发','costume':'浅灰道袍','visual_lock':'消瘦老人'}]
        data['storyboard']=[{'id':1,'visual':'空旷的古城街道，没有人物','action':'镜头缓慢推进','prompt':'古城街道，夜色，空镜'}]
        report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[])
        issues=report['shots'][0]['issues']
        self.assertFalse(any(x.startswith('character_anchor_not_in_prompt:') for x in issues))

    def test_qc_uses_effective_prompt_and_only_injects_assigned_character(self):
        data=dict(self.data)
        data['characters']=[dict(self.data['characters'][0]),
            {'name':'韩渐离','appearance':'银色短发','costume':'黑衣','visual_lock':'右眉疤痕'}]
        data['storyboard']=[{'id':1,'characters':['林月'],'visual':'林月站在城门前','action':'抬头','prompt':'林月站在城门前'}]
        plan=build_shot_plan(data); bible=build_asset_bible(data)
        brief=build_generation_brief(plan,bible)
        self.assertIn('黑色长发',brief['jobs'][0]['prompt'])
        self.assertNotIn('黑衣',brief['jobs'][0]['prompt'])
        report=audit_shots(plan,bible,project_files=[])
        anchor_issues=[x for x in report['shots'][0]['issues'] if x.startswith('character_anchor_not_in_prompt:')]
        self.assertEqual(anchor_issues,[])

    def test_qc_reports_keyframe_dimensions_and_separate_visual_review(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        data=dict(self.data); data['storyboard']=[dict(self.data['storyboard'][0], image_file='shot_0001.png')]
        with tempfile.TemporaryDirectory() as td:
            Image.new('RGB',(896,512)).save(Path(td)/'shot_0001.png')
            report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[],keyframe_dir=td)
            row=report['shots'][0]
            self.assertEqual(row['file_status'],'present')
            self.assertEqual(row['image_dimensions']['width'],896)
            self.assertEqual(row['image_dimensions']['height'],512)
            self.assertEqual(row['visual_review']['status'],'pending')
            data['storyboard'][0]['visual_review']={'status':'approved','notes':'人工确认'}
            report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[],keyframe_dir=td)
            self.assertEqual(report['shots'][0]['visual_review']['status'],'approved')

    def test_visual_rejection_routes_only_that_shot_for_targeted_retry(self):
        data=dict(self.data)
        data['storyboard']=[dict(self.data['storyboard'][0], visual_review={'status':'needs_revision','notes':'主体错了'})]
        report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[])
        jobs=route_regeneration(report)['jobs']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['shot_id'],'shot_0001')
        self.assertEqual(jobs[0]['action'],'regenerate_keyframe')


    def test_prop_state_timeline_prevents_incense_and_pendant_appearing_early(self):
        shots=[
            {'id':6,'visual':'爷爷和韩走在乱坟之间，韩手里抱着一叠香'},
            {'id':9,'visual':'爷爷将三炷香插在墓碑前'},
            {'id':15,'visual':'墓前三炷香仍然没有燃起，香头灰暗'},
            {'id':21,'visual':'原本熄灭的三炷香突然自行燃起，火苗跳动'},
            {'id':25,'visual':'地面出现暗青色龙形玉佩','action':'韩拿起玉佩'},
        ]
        states=derive_prop_states(shots)
        self.assertEqual(states[0]['incense'],'carried_unlit')
        self.assertEqual(states[1]['incense'],'placed_unlit')
        self.assertEqual(states[2]['incense'],'unlit')
        self.assertEqual(states[3]['incense'],'self_ignited')
        self.assertEqual(states[0]['dragon_jade_pendant'],'absent')
        self.assertEqual(states[-1]['dragon_jade_pendant'],'revealed_and_held')
        self.assertIn('不得出现玉佩', '；'.join(prop_state_anchors(states[0])))

    def test_prop_states_are_attached_to_shot_plan_and_generation_prompt(self):
        data=dict(self.data, storyboard=[
            {'id':1,'visual':'墓前三炷香仍然没有燃起','action':'香头灰暗'},
            {'id':2,'visual':'原本熄灭的三炷香突然自行燃起','action':'火苗跳动'},
            {'id':3,'visual':'地面出现暗青色龙形玉佩','action':'韩拿起玉佩'},
        ])
        plan=build_shot_plan(data)
        self.assertEqual(plan['shots'][0]['prop_states']['incense'],'unlit')
        self.assertEqual(plan['shots'][1]['prop_states']['incense'],'self_ignited')
        self.assertEqual(plan['shots'][0]['prop_states']['dragon_jade_pendant'],'absent')
        self.assertEqual(plan['shots'][2]['prop_states']['dragon_jade_pendant'],'revealed_and_held')
        brief=build_generation_brief(plan,build_asset_bible(data))
        self.assertIn('龙形玉佩尚未登场',brief['jobs'][0]['prompt'])
        self.assertIn('保持燃烧状态',brief['jobs'][1]['prompt'])


    def test_story_timeline_flags_incense_state_contradiction(self):
        shots=[
            {'id':1,'visual':'爷爷将三炷香插在墓碑前'},
            {'id':2,'visual':'韩低头抱着香，神情疲惫'},
        ]
        states=derive_prop_states(shots)
        self.assertEqual(states[1]['incense'],'placed_unlit')
        self.assertTrue(any('香已在前序镜头插于墓前' in x for x in states[1]['warnings']))
        self.assertTrue(any('保持香在墓前' in x for x in prop_state_anchors(states[1])))

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
    def test_project_spec_preserves_landscape_16_9_delivery_format(self):
        from production_pipeline import build_project_spec
        data=dict(self.data, aspect_ratio='16:9')
        spec=build_project_spec(data)
        self.assertEqual(spec['format']['aspect_ratio'],'16:9')
        self.assertEqual(spec['format']['target_resolution'],{'width':1920,'height':1080})

    def test_vertical_and_portrait_formats_are_respected(self):
        from production_pipeline import build_project_spec
        self.assertEqual(build_project_spec(dict(self.data,aspect_ratio='9:16'))['format']['target_resolution'],{'width':1080,'height':1920})
        self.assertEqual(build_project_spec(dict(self.data,aspect_ratio='3:4'))['format']['target_resolution'],{'width':1440,'height':1920})
    def test_qc_recognizes_keyframes_in_episode_workspace(self):
        import tempfile
        from pathlib import Path
        plan=build_shot_plan(self.data)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'shot_0001.png').write_bytes(b'fake-image-file')
            report=audit_shots(plan,build_asset_bible(self.data),[],None,tmp)
            self.assertNotIn('missing_keyframe',report['shots'][0]['issues'])

if __name__=='__main__': unittest.main()
