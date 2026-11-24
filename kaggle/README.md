# Kaggle worker

Kaggle is an ephemeral GPU worker, not the durable project home. Export notebook versions and generated assets after each verified run. Keep credentials out of notebook output and Git history.

Current target experiment: SD 1.5-compatible anime checkpoint + IP-Adapter SD15. Validate package imports, free disk, per-GPU VRAM, model license, and adapter compatibility before downloading multi-GB weights.

## Notebook archive

`notebook57e8a33be4.ipynb` is an exported source snapshot from Kaggle. Execution outputs, counters, and transient cell metadata were stripped before backup. It preserves the cell source history, including earlier ComfyUI experiments and the later Diffusers/Tiny-SD tests. Some old cells are marked as raw or contain malformed draft fragments; treat this as an audit/archive snapshot, not a clean run-all notebook. The exported Kaggle metadata reported GPU disabled, so GPU availability must be re-verified in the live session before new experiments.


## 当前已验证的视频后期 MVP
- Box 控制端安装了 FFmpeg 6.1.1、eSpeak NG 1.51、Pillow 和 Noto CJK 字体。
- `render_episode.py` 从项目已保存的分镜读取内容，优先匹配项目文件名中的 `shot_0001.png` 等镜头关键帧；缺少原画时生成明确标注的文字占位卡。
- 对画面做轻微 Ken Burns 推近，使用普通话 eSpeak NG 合成旁白，生成 SRT 字幕，最后输出 MP4。
- 工作台的“生成动态分镜预览 MP4”按钮会后台启动任务并在完成后下载视频。
- 这是动态分镜/animatic，不等于 AnimateDiff、SVD 等模型生成的真实人物动作视频。语音目前为离线合成测试音色。

## 模型缓存持久化（私有 Kaggle Dataset 已上传并验证挂载）

**持久化数据集：** https://www.kaggle.com/datasets/jincangma/ai-manga-model-bundle （Private）
- Version 1：5.32 GB，19 个文件；包含 AnythingV5 SD1.5 fp16、IP-Adapter SD15 Light、Image Encoder 和 `manifest.json`。
- 上传来源：Kaggle 免费 Notebook Session 中的 `/kaggle/working/manga_model_bundle`，通过 KaggleHub `dataset_upload` 上传；无需在代码中暴露 API Token。
- 恢复验证：在 Notebook 中执行 `kagglehub.dataset_download('jincangma/ai-manga-model-bundle')`，Kaggle 将 19 个文件挂载到 `/kaggle/input/datasets/jincangma/ai-manga-model-bundle`；实测 `FILES 19`，2,528,373,448 字节的 Image Encoder 权重存在。
- 将数据集完整复制到 `/kaggle/working/manga_model_bundle` 后，对 manifest 中 18 个文件逐个计算 SHA-256，结果 `BAD []`，全部通过。
- 数据集页面确认 Private、Version 1、5.32 GB、19 files。模型包不包含 API key、访问口令或项目私人资料。

### 后续 Notebook 会话启动
1. 将 `prepare_kaggle_runtime.py`、`batch_generate_from_jobs.py` 和 `ipadapter_character_test.py` 放到 `/kaggle/working` 或从项目代码库取用。
2. 执行 `python /kaggle/working/prepare_kaggle_runtime.py`。脚本通过 KaggleHub 挂载私有 Dataset，检查 `manifest.json` 与 `AnythingV5/model_index.json`；为避免 FUSE 上逐张量随机读取非常缓慢，会把 `AnythingV5` 作为只读链接，把 `IP-Adapter`（含 Image Encoder）复制到 `/kaggle/working/manga_model_bundle`。磁盘空间不足时会报出需要和剩余空间，并保留挂载路径。
3. `batch_generate_from_jobs.py` 优先从 `MANGA_MODEL_BUNDLE` 或本地 `/kaggle/working/manga_model_bundle` 读取 `AnythingV5/` 与 `IP-Adapter/`，再回退到旧 HF snapshot 目录。无需再次下载模型权重。
4. 运行前仍须确认免费 GPU Session 已启用、CUDA 可用、参考图和 `manga-generation-jobs.json` 已准备好。

### 真实 GPU 加载与出图验收（2026-11-24）
- Kaggle 免费 GPU T4 x2 已启用，`torch.cuda.is_available() == True`、`device_count() == 2`。
- AnythingV5 从私有 Dataset 挂载路径成功加载到 T4，512×512、8 steps GPU 推理成功；输出 `/kaggle/working/base_model_smoke_test.png`，510,632 bytes。
- 完整复制模型包到 `/kaggle/working/manga_model_bundle` 后，18/18 SHA-256 校验通过。
- AnythingV5 + IP-Adapter SD15 Light + 2.53GB Image Encoder 从本地 staging 路径完整加载成功；8 steps、512×512 参考图条件推理成功，输出 `/kaggle/working/ipadapter_smoke_test.png`，512,464 bytes。
- 当前 smoke test 使用程序绘制的测试参考图，证明模型链路和 GPU 推理通了；还不是用户正式角色参考图的跨镜头一致性验收，也不是正式分镜生成。

**验证边界：** 私有 Dataset 上传、19 文件挂载、18 项 SHA-256、底模加载、IP-Adapter 加载和参考图条件出图均已通过。Kaggle Notebook 仍显示 `Failed to save draft.`，因此本次两个 smoke test PNG 仍在当前临时 Session，尚未保存到长期数据集。下一步要把真实角色参考图和一镜头任务接入 worker，验证角色一致性和正式关键帧输出。
