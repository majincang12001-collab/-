"""Portable model-vault catalog for the manga workbench.

This module catalogs model metadata and locally mounted assets only. It never
fetches weights, executes model code, or assumes that storage implies inference.
"""
from __future__ import annotations
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_VAULT = ROOT / 'data' / 'model-vault'
MODEL_EXTENSIONS = {'.safetensors', '.ckpt', '.gguf', '.pt', '.pth', '.onnx', '.bin'}

CATALOG = [
    {
        'id': 'guofeng41_xl', 'name': 'GuoFeng4.1 XL 2.5D', 'kind': 'image_checkpoint',
        'tasks': ['text_to_image', 'image_to_image'], 'format': 'safetensors',
        'tested_runtime': 'Kaggle T4 / SDXL Diffusers',
        'license': 'CC-BY-NC-SA-4.0', 'commercial_use': False,
        'status': 'tested_candidate_license_restricted',
        'note': '已做技术测试；非商业许可，商业化前必须更换或取得授权。',
    },
    {
        'id': 'sdxl_ip_adapter_plus_vit_h', 'name': 'SDXL IP-Adapter Plus ViT-H', 'kind': 'identity_adapter',
        'tasks': ['character_reference_conditioning'], 'format': 'safetensors',
        'tested_runtime': 'SDXL Diffusers', 'license': 'Apache-2.0',
        'commercial_use': True, 'status': 'tested_component',
        'note': '需配套兼容的 SDXL image encoder；所有 pipeline 调用均需正确传入 image embeds。',
    },
    {
        'id': 'qwen_image_edit_2511', 'name': 'Qwen-Image-Edit-2511', 'kind': 'image_edit_model',
        'tasks': ['image_to_image', 'character_reference_editing', 'multi_reference_editing'],
        'format': 'safetensors/diffusers', 'source': 'https://modelscope.cn/models/qwen/Qwen-Image-Edit-2511',
        'modelscope_id': 'Qwen/Qwen-Image-Edit-2511', 'package_size_gb': 57.72,
        'license': 'Apache-2.0', 'commercial_use': 'license_allows_subject_to_dependencies',
        'status': 'catalogued_not_downloaded_runtime_unverified',
        'note': '官方模型卡强调角色一致性与多图编辑；约57.72GB。需验证海光DTK/PyTorch算子、内存和推理速度后再下载部署。',
    },
    {
        'id': 'qwen_image_2_1', 'name': 'Qwen-Image-2.1', 'kind': 'image_generation_model',
        'tasks': ['text_to_image', 'image_to_image', 'multi_reference_generation'],
        'format': 'safetensors/diffusers', 'source': 'https://modelscope.cn/models/Qwen/Qwen-Image-2.1',
        'modelscope_id': 'Qwen/Qwen-Image-2.1', 'package_size_gb': 33.13,
        'license': 'qwen-research', 'commercial_use': 'review_license_before_use',
        'status': 'catalogued_not_downloaded_runtime_unverified',
        'note': '支持最多10张参考图的生成/编辑方向；许可证不是Apache-2.0，商业使用前必须单独审核。',
    },
    {
        'id': 'wan2_2_first_last_frame', 'name': 'Wan2.2 14B First-Last Frame Video Workflow', 'kind': 'video_model_workflow',
        'tasks': ['image_to_video', 'start_end_frame_video'],
        'format': 'safetensors/comfyui', 'source': 'https://modelscope.cn/models/Comfy-Org/Wan-2.2',
        'modelscope_id': 'Wan-AI/Wan2.2-I2V-A14B',
        'workflow_source': 'https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_flf2v.json',
        'license': 'Apache-2.0 (verify exact component)', 'commercial_use': 'verify_component_licenses',
        'status': 'catalogued_dcu_runtime_unverified',
        'note': '已找到Comfy-Org首尾帧工作流模板；模型/工作流可作为目标接入，但海光DCU算子兼容性尚未验证，暂不下载大权重。',
    },
    {
        'id': 'kandinsky6_lite_distill', 'name': 'Kandinsky 6.0 Video Lite Distill 3B', 'kind': 'video_model',
        'tasks': ['text_to_video', 'image_to_video', 'start_end_frame_video'],
        'license': 'MIT (code/model card; verify each component)', 'commercial_use': 'verify_components',
        'status': 't4_unsupported_disk_blocked',
        'note': 'T4 架构不受当前官方配置支持；完整包约 26.7GB，不适合当前 Kaggle 会话磁盘。',
    },
]


def vault_root() -> Path:
    raw = os.environ.get('MANGA_MODEL_VAULT', '').strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_VAULT.resolve()


def inventory(root: Path | None = None) -> dict:
    root = (root or vault_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    total = 0
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.suffix.lower() not in MODEL_EXTENSIONS:
            continue
        try:
            resolved = p.resolve()
            if root not in resolved.parents:
                continue
            stat = p.stat()
        except OSError:
            continue
        total += stat.st_size
        rows.append({'name': p.name, 'relative_path': str(p.relative_to(root)),
                     'format': p.suffix.lower().lstrip('.'), 'size_bytes': stat.st_size})
    return {'root_configured': True, 'asset_count': len(rows), 'total_bytes': total,
            'assets': rows, 'storage_role': 'model_storage_only_not_inference'}


def catalog(root: Path | None = None) -> dict:
    inv = inventory(root)
    return {'ok': True, 'catalog': CATALOG, 'storage': inv,
            'inference_note': '模型存储与 GPU 推理是两回事；必须配置可用 GPU worker 才能生成。'}
