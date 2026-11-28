import unittest
from production_pipeline import build_asset_bible, build_shot_plan, build_generation_brief, audit_shots

class CharacterConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'characters': [
                {'name':'韩','age':'17岁','appearance':'黑色蓬松碎发，清秀少年脸','costume':'深灰黑色破旧交领衣袍','visual_lock':'17岁少年感，黑发固定','negative_constraints':'不得变成成年人，不得换衣服','role':'主角'},
                {'name':'爷爷','age':'约70岁','appearance':'消瘦老人，白灰头发','costume':'浅灰色破败道袍','visual_lock':'老人皱纹和白灰头发固定','role':'配角'},
                {'name':'黑色乌鸦','appearance':'黑羽、猩红眼睛','visual_lock':'黑羽红眼','negative_constraints':'不是人类角色','role':'动物角色'},
            ],
            'storyboard': []
        }

    def brief(self, shots):
        data = dict(self.data, storyboard=shots)
        return build_generation_brief(build_shot_plan(data), build_asset_bible(data))

    def test_han_solo_shot_does_not_receive_grandfather(self):
        job = self.brief([{'id':1,'characters':['韩'],'visual':'韩独自站立','prompt':'韩站在墓前'}])['jobs'][0]
        self.assertEqual([x['name'] for x in job['character_anchors']], ['韩'])
        self.assertIn('深灰黑色破旧交领衣袍', job['prompt'])
        self.assertNotIn('浅灰色破败道袍', job['prompt'])

    def test_grandfather_solo_shot_does_not_receive_han(self):
        job = self.brief([{'id':1,'characters':['爷爷'],'visual':'爷爷独自站立','prompt':'爷爷站在墓前'}])['jobs'][0]
        self.assertEqual([x['name'] for x in job['character_anchors']], ['爷爷'])
        self.assertIn('浅灰色破败道袍', job['prompt'])
        self.assertNotIn('深灰黑色破旧交领衣袍', job['prompt'])

    def test_crow_shot_is_animal_only_and_forbids_people(self):
        job = self.brief([{'id':1,'characters':'黑色乌鸦','visual':'乌鸦啄食柿子','prompt':'黑色乌鸦啄食柿子'}])['jobs'][0]
        self.assertEqual([x['name'] for x in job['character_anchors']], ['黑色乌鸦'])
        self.assertEqual(job['character_anchors'][0]['entity_type'], 'animal')
        self.assertIn('不得生成任何人类角色', job['prompt'])
        self.assertIn('人物面孔', job['negative_prompt'])
        self.assertNotIn('爷爷', job['prompt'])

    def test_legacy_singular_character_field_is_preserved_in_shot_plan(self):
        data={'characters':[{'name':'黑色乌鸦','role':'动物角色'}],
              'storyboard':[{'id':1,'character':'黑色乌鸦','visual':'乌鸦啄食柿子','prompt':'乌鸦特写'}]}
        plan=build_shot_plan(data)
        self.assertEqual(plan['shots'][0]['character_names'],['黑色乌鸦'])

    def test_approved_base_image_is_linked_to_protagonist_only(self):
        data={'approved_base_image':True,'base_image_file':'project_hero.png',
              'characters':[{'name':'韩','role':'主角'},{'name':'爷爷','role':'配角'}],
              'storyboard':[]}
        files=[{'name':'project_hero.png','stored':'project_hero.png'}]
        bible=build_asset_bible(data,files)
        han=next(c for c in bible['characters'] if c['name']=='韩')
        grandpa=next(c for c in bible['characters'] if c['name']=='爷爷')
        self.assertEqual(han['reference_images'],['project_hero.png'])
        self.assertEqual(han['reference_status'],'linked')
        self.assertEqual(grandpa['reference_images'],[])
        self.assertEqual(grandpa['reference_status'],'missing')

    def test_environment_empty_cast_stays_empty_even_with_one_character(self):
        data = {'characters':[{'name':'韩','appearance':'黑发'}], 'storyboard':[{'id':1,'visual':'空旷坟地','action':'镜头推进','prompt':'空旷坟地，夜雾'}]}
        job = build_generation_brief(build_shot_plan(data),build_asset_bible(data))['jobs'][0]
        self.assertEqual(job['character_anchors'], [])
        self.assertEqual(job['reference_images'], [])
        self.assertNotIn('韩', job['prompt'])

    def test_prop_inscription_does_not_trigger_empty_cast_during_generation_or_qc(self):
        data = {'characters':[{'name':'许清月','appearance':'长发女子','costume':'白衣'}],
                'storyboard':[{'id':1,'characters':[], 'visual':'玉佩刻着许清月的名字',
                               'action':'只拍摄玉佩，不出现人物','prompt':'玉佩铭文：许清月'}]}
        bible=build_asset_bible(data); plan=build_shot_plan(data)
        job=build_generation_brief(plan,bible)['jobs'][0]
        report=audit_shots(plan,bible,project_files=[])
        self.assertEqual(job['character_anchors'],[])
        self.assertEqual(job['reference_images'],[])
        self.assertNotIn('长发女子',job['prompt'])
        self.assertFalse(any(x.startswith('character_anchor_not_in_prompt:') for x in report['shots'][0]['issues']))
        self.assertFalse(any(x.startswith('character_reference_missing:') for x in report['shots'][0]['warnings']))

    def test_empty_cast_is_not_inferred_from_single_episode_character_during_qc(self):
        data = {'characters':[{'name':'韩','appearance':'黑发'}],
                'storyboard':[{'id':1,'characters':[],'visual':'空坟地夜景','action':'镜头缓慢推进','prompt':'空坟地夜景'}]}
        report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[])
        self.assertNotIn('character_anchor_not_in_prompt:character_1',report['shots'][0]['issues'])
        self.assertNotIn('character_reference_missing:character_1',report['shots'][0]['warnings'])

    def test_animal_cast_not_present_in_visual_is_flagged_for_review(self):
        data = {'characters':[{'name':'黑色乌鸦','entity_type':'animal','appearance':'黑羽红眼'}],
                'storyboard':[{'id':1,'characters':['黑色乌鸦'],'visual':'腐烂柿子挂在枯枝上','action':'静止','prompt':'柿子特写'}]}
        report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[])
        self.assertIn('cast_may_not_match_shot_subject:character_1',report['shots'][0]['warnings'])

    def test_scene_and_prop_continuity_are_shared_across_related_shots(self):
        shots=[{'id':1,'visual':'枯萎柿子树挂着腐烂柿子','action':'夜风吹动枝条'},
               {'id':2,'visual':'黑色乌鸦站在柿子树枝头','action':'乌鸦啄食柿子'},
               {'id':3,'visual':'爷爷在乱坟岗墓碑前插上三炷香','action':'香头尚未燃起'},
               {'id':4,'visual':'暗青色龙形玉佩出现在墓碑前','action':'龙眼微微泛红'}]
        data=dict(self.data,storyboard=shots)
        bible=build_asset_bible(data); plan=build_shot_plan(data)
        self.assertEqual(len(bible['scenes']),2)
        self.assertEqual(plan['shots'][0]['scene_key'],plan['shots'][1]['scene_key'])
        self.assertEqual(plan['shots'][2]['scene_key'],'graveyard_night')
        self.assertIn('three_incense_sticks',plan['shots'][2]['prop_keys'])
        self.assertIn('dragon_jade_pendant',plan['shots'][3]['prop_keys'])
        brief=build_generation_brief(plan,bible)
        self.assertIn('场景连续性锚点',brief['jobs'][0]['prompt'])
        self.assertIn('道具连续性锚点',brief['jobs'][2]['prompt'])

    def test_memorial_only_character_does_not_trigger_missing_reference_warning(self):
        data = {'characters':[{'name':'许清月（仅墓碑信息）','entity_type':'memorial_only',
                               'visual_lock':'仅作为墓碑姓名出现，不生成实体','reference_file':'accidental_face.png',
                               'negative_constraints':'禁止实体人物、女鬼或幽灵'}],
                'storyboard':[{'id':1,'characters':['许清月（仅墓碑信息）'],
                               'visual':'旧墓碑上刻着许清月三个字','action':'镜头缓慢推近',
                               'prompt':'旧墓碑上刻着许清月三个字'}]}
        bible=build_asset_bible(data, project_files=[])
        self.assertEqual(bible['characters'][0]['entity_type'],'memorial_only')
        self.assertEqual(bible['characters'][0]['reference_images'],[])
        report=audit_shots(build_shot_plan(data),bible,project_files=[])
        self.assertNotIn('character_reference_missing:character_1',report['shots'][0]['warnings'])
        brief=build_generation_brief(build_shot_plan(data),bible)['jobs'][0]
        self.assertIn('绝对不得生成该姓名对应的人物',brief['prompt'])

    def test_missing_reference_is_reported_without_claiming_it_is_linked(self):
        data = {'characters':[{'name':'韩','appearance':'黑发'}], 'storyboard':[{'id':1,'characters':['韩'],'visual':'韩站立','action':'站立','prompt':'韩站立'}]}
        bible=build_asset_bible(data, project_files=[])
        self.assertEqual(bible['characters'][0]['reference_status'],'missing')
        self.assertEqual(bible['characters'][0]['reference_images'],[])
        report=audit_shots(build_shot_plan(data),bible,project_files=[])
        self.assertIn('character_reference_missing:character_1',report['shots'][0]['warnings'])
        self.assertNotIn('character_reference_missing:character_1',report['shots'][0]['issues'])

    def test_character_id_name_mismatch_is_qc_issue(self):
        data = {'characters':[{'name':'韩','character_id':'han','appearance':'黑发'},{'name':'爷爷','character_id':'grandpa','appearance':'白发'}],
                'storyboard':[{'id':1,'character_ids':['han'],'characters':['爷爷'],'visual':'爷爷站立','action':'站立','prompt':'爷爷站立'}]}
        report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[])
        self.assertIn('character_id_name_mismatch',report['shots'][0]['issues'])

    def test_unknown_cast_name_is_qc_issue(self):
        data = {'characters':[{'name':'韩','appearance':'黑发'}], 'storyboard':[{'id':1,'characters':['陌生人'],'visual':'陌生人站立','action':'站立','prompt':'陌生人站立'}]}
        report=audit_shots(build_shot_plan(data),build_asset_bible(data),project_files=[])
        self.assertIn('unknown_character_name:陌生人',report['shots'][0]['issues'])

if __name__ == '__main__':
    unittest.main()
