import unittest
from video_model_registry import assess_model, VIDEO_MODELS

class VideoModelRegistryTests(unittest.TestCase):
    def test_hunyuan_t4_meets_published_minimum_only(self):
        result = assess_model('hunyuanvideo_1_5', 15.0)
        self.assertEqual(result['verdict'], 'official_requirement_met')

    def test_wan_official_requirement_not_met_on_16gb(self):
        result = assess_model('wan2_2_ti2v_5b', 16.0)
        self.assertEqual(result['verdict'], 'below_official_requirement')

    def test_wan_quantized_is_explicitly_experimental(self):
        result = assess_model('wan2_2_ti2v_5b', 16.0, allow_unofficial_quantization=True)
        self.assertEqual(result['verdict'], 'experimental_quantized_only')

    def test_wan_ti2v_does_not_claim_native_end_frame(self):
        self.assertFalse(VIDEO_MODELS['wan2_2_ti2v_5b']['native_start_end_frames'])

    def test_ltxv_supports_both_frames_but_disk_is_blocked(self):
        model = VIDEO_MODELS['ltxv_098_distilled_13b']
        self.assertTrue(model['native_start_end_frames'])
        self.assertEqual(model['status'], 'disk_blocked_runtime_unverified')
        result = assess_model('ltxv_098_distilled_13b', 14.56)
        self.assertEqual(result['verdict'], 'runtime_requirement_unverified')

    def test_kandinsky6_lite_has_native_start_and_end_frames(self):
        model = VIDEO_MODELS['kandinsky6_lite_distill']
        self.assertTrue(model['native_start_end_frames'])
        self.assertEqual(model['model_parameters_b'], 3)
        self.assertEqual(model['full_model_package_gb'], 26.7)
        self.assertIn('Turing sm_75 (Tesla T4)', model['unsupported_gpu_architectures'])
        result = assess_model('kandinsky6_lite_distill', 16.0)
        self.assertEqual(result['verdict'], 'runtime_requirement_unverified')

    def test_unknown_vram_does_not_allow_download(self):
        result = assess_model('hunyuanvideo_1_5', None)
        self.assertEqual(result['verdict'], 'unknown_hardware')

if __name__ == '__main__':
    unittest.main()
