# Kaggle worker

Kaggle is an ephemeral GPU worker, not the durable project home. Export notebook versions and generated assets after each verified run. Keep credentials out of notebook output and Git history.

Current target experiment: SD 1.5-compatible anime checkpoint + IP-Adapter SD15. Validate package imports, free disk, per-GPU VRAM, model license, and adapter compatibility before downloading multi-GB weights.

## Notebook archive

`notebook57e8a33be4.ipynb` is an exported source snapshot from Kaggle. Execution outputs, counters, and transient cell metadata were stripped before backup. It preserves the cell source history, including earlier ComfyUI experiments and the later Diffusers/Tiny-SD tests. Some old cells are marked as raw or contain malformed draft fragments; treat this as an audit/archive snapshot, not a clean run-all notebook. The exported Kaggle metadata reported GPU disabled, so GPU availability must be re-verified in the live session before new experiments.


## 当前视频后期边界（2026-11-24）
- `render_episode.py` 只对已存在的真实关键帧做动态剪辑；缺少关键帧会停止任务，不再用文字卡冒充画面。
- 不再调用 eSpeak NG 作为正式旁白；没有真实普通话 TTS 音频时，不伪造机械配音。
- Ken Burns 动效只是静态关键帧剪辑，不等于 AI 图生视频。真实动作视频仍需单独接入视频生成模型。

## Qwen3-TTS 普通话免费 GPU Worker

优先模型：`Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`（Apache-2.0）。支持中文音色 Serena、Vivian、Uncle_Fu、Dylan、Eric，可通过自然语言指令调整语气。模型约 2.5GB，另需 Tokenizer 和运行依赖；仅在 Kaggle 免费 GPU Session 中下载，不安装到 Box 控制端。

### Kaggle 启动
1. 在 Kaggle Notebook 选择免费 GPU（T4 即可），确认 `torch.cuda.is_available()`。
2. 将项目中的 `kaggle/tts_worker.py` 放入 `/kaggle/working/`。
3. 在 Kaggle Secrets 中配置 `MANGA_SERVER_URL`（当前漫剧控制台公网地址）和 `MANGA_WORKER_KEY`（工作进程密钥）；不要把密钥写入 Notebook 输出或代码仓库。
4. 执行 `pip install -U qwen-tts soundfile`。
5. 执行 `python /kaggle/working/tts_worker.py`。首次启动会加载/下载 0.6B 模型，成功后持续轮询控制台语音队列。
6. 回到控制台「制作流程 → 中文语音」，选择音色、输入试听文本，提交试听；Worker 完成后会返回 WAV，页面出现音频播放器。

Worker 使用独立的 `/api/tts/*` 队列，不调用付费 API。若 Kaggle Session 关闭，试听任务会停留在队列中，重新启动 Worker 后继续处理。

**验证边界：** 目前已完成 TTS 队列、Worker 领取、WAV 上传、状态查询和音频回放的 API 集成测试；尚未在真实 Kaggle T4 Session 下载 Qwen3-TTS 权重并验收听感。控制台不会把排队状态冒充为已生成音频。

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
