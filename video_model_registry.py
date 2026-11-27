"""Video model registry and conservative hardware gating for the manga pipeline.

This module never downloads weights or starts inference. It keeps official
requirements separate from community quantization claims.
"""
from __future__ import annotations

VIDEO_MODELS = {
    "hunyuanvideo_1_5": {
        "name": "HunyuanVideo-1.5",
        "repo": "https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5",
        "weights": "tencent/HunyuanVideo-1.5",
        "tasks": ["text_to_video", "image_to_video"],
        "native_start_end_frames": False,
        "official_min_vram_gb": 14,
        "official_resolution": ["480p", "720p"],
        "official_i2v_step_distill": True,
        "official_license": "Tencent Hunyuan Community License",
        "license_caveat": "Territory excludes the EU; includes additional use/distribution restrictions. Review the current agreement before deployment or commercial use.",
        "recommended_runner": "Wan2GP or official inference",
        "status": "candidate_needs_runtime_test",
    },
    "wan2_2_ti2v_5b": {
        "name": "Wan2.2 TI2V-5B",
        "repo": "https://github.com/Wan-Video/Wan2.2",
        "weights": "Wan-AI/Wan2.2-TI2V-5B",
        "tasks": ["text_to_video", "image_to_video"],
        "native_start_end_frames": False,
        "quantized_main_weight_gb": 5.23,
        "official_min_vram_gb": 24,
        "official_resolution": ["720p"],
        "official_fps": 24,
        "official_license": "Apache-2.0 (verify the exact model card and included files)",
        "license_caveat": "Quantized community builds may have separate conversion notices; do not treat their hardware claims as official.",
        "recommended_runner": "Wan2GP quantized path, experimental on 16GB",
        "status": "quantized_path_experimental",
    },
    "kandinsky6_lite_distill": {
        "name": "Kandinsky 6.0 Video Lite Distill 3B",
        "repo": "https://github.com/kandinskylab/kandinsky-6",
        "weights": "kandinskylab/Kandinsky-6.0-Lite-distill-5s-Diffusers",
        "tasks": ["text_to_video", "image_to_video", "start_end_frame_video", "text_to_audio_video"],
        "native_start_end_frames": True,
        "model_parameters_b": 3,
        "full_model_package_gb": 26.7,
        "main_transformer_gb": 6.38,
        "text_encoder_package_gb": 16.6,
        "tested_vram_gb": [16, 24],
        "official_min_vram_gb": None,
        "tested_gpu_architectures": ["Ada sm_89", "Blackwell sm_120"],
        "unsupported_gpu_architectures": ["Turing sm_75 (Tesla T4)"],
        "official_license": "MIT (official code and Lite Distill model card)",
        "license_caveat": "Check each separately downloaded component and any third-party quantization package before redistribution or commercial deployment.",
        "disk_caveat": "Full Diffusers package is about 26.7GB, so it cannot fit the current Kaggle session's approximately 17.1GiB free disk. Local 512GB SSD may fit if sufficient space is free.",
        "runtime_caveat": "Official 16GB GPU benchmark uses Qwen text encoder NF4; official 24GB RTX 4090 benchmark uses module offload. T4 is excluded by the documented setup architecture matrix. 16GB system RAM has not been validated.",
        "recommended_runner": "Official Kandinsky pipeline or ComfyUI integration; standalone workbench adapter not yet implemented",
        "status": "local_16gb_candidate_t4_unsupported_disk_blocked",
    },
    "ltxv_098_distilled_13b": {
        "name": "LTX Video 0.9.8 Distilled 13B INT8",
        "repo": "https://github.com/hanlest/Wan2GP",
        "weights": "DeepBeepMeep/LTX_Video",
        "tasks": ["text_to_video", "image_to_video", "start_end_frame_video"],
        "native_start_end_frames": True,
        "quantized_main_weight_gb": 13.21,
        "official_min_vram_gb": None,
        "disk_caveat": "Main INT8 checkpoint alone is about 13.21GB; text encoder, VAE, dependencies and cache exceed current Kaggle free space.",
        "recommended_runner": "Wan2GP LTXV handler; not tested on T4",
        "status": "disk_blocked_runtime_unverified",
    },
    "ltx2_3_distilled_1_1_int8": {
        "name": "LTX-2.3 Distilled 1.1 INT8",
        "repo": "https://github.com/hanlest/Wan2GP",
        "weights": "DeepBeepMeep/LTX-2",
        "tasks": ["text_to_video", "image_to_video", "start_end_frame_video"],
        "native_start_end_frames": True,
        "quantized_main_weight_gb": 19.45,
        "official_min_vram_gb": None,
        "disk_caveat": "Main INT8 checkpoint alone exceeds current Kaggle free disk space.",
        "recommended_runner": "Wan2GP LTX-2 handler; not tested on T4",
        "status": "disk_blocked_runtime_unverified",
    },
}


def assess_model(model_id: str, vram_gb: float | None, *, allow_unofficial_quantization: bool = False) -> dict:
    if model_id not in VIDEO_MODELS:
        raise KeyError(f"Unknown video model: {model_id}")
    model = VIDEO_MODELS[model_id]
    if model.get("official_min_vram_gb") is None:
        verdict = "runtime_requirement_unverified"
        reason = "未找到可核验的官方最低显存要求；先核对磁盘、配套权重和实际峰值显存，不下载模型。"
        return {"model_id": model_id, "vram_gb": vram_gb, "verdict": verdict, "reason": reason,
                "official_min_vram_gb": None, "native_start_end_frames": model.get("native_start_end_frames", False),
                "disk_caveat": model.get("disk_caveat", ""), "license_caveat": model.get("license_caveat", "")}
    if vram_gb is None:
        verdict = "unknown_hardware"
        reason = "GPU显存未知，先运行硬件检测，不下载模型。"
    elif vram_gb >= model["official_min_vram_gb"]:
        verdict = "official_requirement_met"
        reason = "达到官方最低显存要求；仍需检查系统内存、磁盘和依赖。"
    elif model_id == "wan2_2_ti2v_5b" and allow_unofficial_quantization and vram_gb >= 12:
        verdict = "experimental_quantized_only"
        reason = "未达到官方24GB要求；仅允许尝试社区量化/卸载方案，不能保证成功。"
    else:
        verdict = "below_official_requirement"
        reason = f"低于官方最低显存 {model['official_min_vram_gb']}GB，不应按官方配置启动。"
    return {"model_id": model_id, "vram_gb": vram_gb, "verdict": verdict, "reason": reason,
            "official_min_vram_gb": model["official_min_vram_gb"],
            "native_start_end_frames": model.get("native_start_end_frames", False),
            "disk_caveat": model.get("disk_caveat", ""),
            "license_caveat": model.get("license_caveat", "")}
