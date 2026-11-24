# AI 漫剧工作台：当前可运行流程

这份说明把目前已实现的部分和仍需手动操作的部分分开。

## A. 故事与分镜
1. 打开工作台并登录，创建项目。
2. 上传小说/剧本文件（单文件不超过 40MB），或者把内容粘贴到“故事原文”。
3. 在人物设定栏填写角色：`姓名｜年龄｜外貌｜服装｜性格`，一行一个人物。
4. 点击“快速生成分镜草稿（规则辅助）”。它会按句子拆分，尝试提取引号对白，并按关键词建议景别、动作和提示词。它不是大语言模型自动编剧，必须审核。
5. 修改五栏分镜：`景别｜画面内容｜人物动作/表情｜对白/旁白｜图片生成提示词`，然后保存。

## B. 生成每个镜头的动漫关键帧（Kaggle 免费 GPU，当前手动交接）
1. 点击“导出图片生成任务 JSON”，下载 `manga-generation-jobs.json`。
2. 在 Kaggle Notebook 的 `/kaggle/working` 上传任务 JSON、角色参考图（命名 `character_reference.png`）和 `kaggle/batch_generate_from_jobs.py`。
3. 模型缓存必须已经存在：AnythingV5 fp16、IP-Adapter SD15 Light 和 image encoder。当前默认缓存位置 `/kaggle/tmp/hf_cache`，这是临时盘，不保证跨 Session 保留。
4. 选择 Kaggle 免费 GPU，运行前先用 `nvidia-smi` 确认设备。Worker 会在导入 PyTorch 前自动选择空闲显存最多的物理 GPU；也可用 `MANGA_GPU_INDEX=0` 指定。
5. 首次建议只跑一镜：`MANGA_REFERENCE_CROP=face MANGA_WIDTH=768 MANGA_HEIGHT=432 MANGA_IP_ADAPTER_SCALE=0.15 MANGA_STEPS=18 python batch_generate_from_jobs.py --max-shots 1`。
6. 输出位于 `manga_keyframes/`，包括 `shot_0001.png` 和 `manifest.json`；确认图片尺寸、文件大小和画面内容后，再下载并上传到工作台对应项目。

### 当前实测边界
- 768×432 横屏单镜头生成已实测成功；脸部参考裁剪 + IP-Adapter scale 0.15 + 优先级提示词控制，使人物从近景改善为全身远景，建筑背景可见。
- 发现并修复 SD1.5 CLIP 77-token 截断：提示词按景别、场景、动作、风格和人物信息顺序填充，超长段落会被截短而非整段丢弃。新增 3 项纯单元测试。
- 场景细节仍未完全遵循描述；人物跨镜头身份一致性仍未通过系统验收。768×432 也不是 1080P。
- Kaggle Session 关闭后，`/kaggle/tmp` 缓存可能清空；模型持久化 Dataset 尚未完成。不要假设模型已永久保存。
- 不要使用 Run All；不要启动付费 GPU、充值或付费 API。每次只运行指定 Worker cell/Console 命令。

## C. 合成动态分镜预览 MP4（已完成真实测试）
1. 确认分镜已经保存。
2. 确认上传了 `shot_0001.png` 等关键帧；若缺失，系统会用文字占位卡，不会假装那是动漫原画。
3. 点击“生成动态分镜预览 MP4”。
4. 系统会添加轻微推近、普通话 eSpeak NG 旁白、中文字幕并合成为 MP4，任务完成后浏览器自动下载。

这是静态关键帧动态化的 animatic，不是 AI 模型生成的角色动作视频。已验证两镜头混合输入（一个真实 AI 关键帧 + 一个占位卡）输出 1280×720 MP4。中文 TTS 为离线合成测试音色，音质较机械。

## D. 目前未完成
- 真正的大模型自动编剧与语义分镜。
- 工作台与 Kaggle 的自动队列/自动文件回传。
- 可用质量的图生视频镜头：SVD 已有 8 帧短片技术验证，但动作幅度小，尚未达到剧情镜头标准。
- 多角色自然配音、背景音乐、音效、1080P 成片。

全流程默认零预算；不要启用收费 GPU、付费 API 或任何自动充值。

## 2026-11-24 双关键帧链路复测
- Kaggle 免费 Draft Session 保持运行，双 T4 可用；未重新下载模型、未启动付费 GPU。
- 排查到本轮 `from_pretrained` 失败根因：AnythingV5 缓存只有 `diffusion_pytorch_model.fp16.safetensors`，加载时漏传 `variant='fp16'`，并非模型缓存整体丢失。补上 `variant='fp16'` 后加载和推理恢复正常。
- 使用缓存的 AnythingV5 fp16 + IP-Adapter SD15 Light + 主角参考图，额外生成 `shot_0002_batch_test.png`，512x512，424,813 bytes，18 steps，约3.3秒。
- 通过 Kaggle Output 文件面板下载至 Box，登记为项目 `shot_0002.png`。第一张 `shot_0001.png` 原有项目文件保留。
- 将第二张作为同场景不同随机种子的备选构图加入分镜，运行两镜头渲染 `two-keyframes-20261124`：14.8秒，1280x720 H.264 + AAC，5,156,946 bytes，状态 done/100%。旁白和字幕均已合成。
- 视频已登记为项目文件 `双关键帧动态分镜预览_14.8秒.mp4`，可在工作台文件区访问。
- 限制：这次第二镜是同场景随机种子备选图，不代表已具备跨场景人物一致性；本流程仍为关键帧 Ken Burns 动态分镜，不是 AI 角色动作视频。批量 worker 脚本本体尚未完整导入 Kaggle 作为脚本运行，本次实机验证的是同一模型/参考图/参数的第二次单镜头推理。

## 2026-11-24 批量 worker 稳定性修订
- `kaggle/batch_generate_from_jobs.py` 已修订为可断点续跑：支持 `--resume`、`MAX_SHOTS`/`--max-shots`，每张图完成后原子写入 manifest，清理显存缓存。
- 明确加载 AnythingV5 时必须传 `variant='fp16'`，并在推理前验证本地模型关键权重文件，禁止自动联网下载模型。
- 角色提示词加入年龄字段；每镜固定 seed；输出记录 seed、分辨率、大小、耗时和状态。
- 已通过 `py_compile`，并提交 GitHub commit `3693114590d1529c61f9151cdc44fed52bd781ee`。
- 重要：这只是 worker 代码可靠性修订，尚未在 Kaggle 实机运行该脚本。当前 notebook 仍需完成 jobs JSON 输入和脚本执行；现有参考图仍在 Kaggle working 目录，可通过 `HEROINE_REFERENCE` 指定。

## 2026-11-24 批量 Worker 首次成功（两条不同剧情镜头）
- 修复 Kaggle Worker 源码尾部被污染的语法问题；重新从 GitHub 拉取干净版，Kaggle `py_compile` 通过。
- 通过 Kaggle Notebook Python Console 执行脚本，避免失效的 cell toolbar 入口；复用 AnythingV5 fp16 与 IP-Adapter 缓存、角色参考图。
- Worker 实际完成 2/2 镜头：`shot_0001.png` 374,358 bytes，3.34s；`shot_0002.png` 397,073 bytes，3.38s；两张均 512x512，manifest status=done。
- 两镜头为不同剧情构图：古城城门抵达、灯笼街道内部行走；注意参考图条件只能辅助角色一致性，不能保证跨镜头完全一致。
- Kaggle 输出压缩包 `manga_keyframes.zip` 772,902 bytes，已下载并解包导入项目正式 uploads。
- 用两张本轮批量关键帧渲染 MP4：14.8 秒，1280x720 H.264 + AAC，4,961,148 bytes；ffprobe 验证通过。
- 仍是静态关键帧 + Ken Burns 推近 + 普通话旁白/字幕，不是 AI 角色动作视频；目前输出分辨率不是 1080p。

## 2026-11-24：AnimateDiff 首轮视频实验（未通过质量验收）
- 在免费 Kaggle 双 T4 中下载 AnimateDiff motion adapter v1-5-2，并组合 AnythingV5、IP-Adapter。
- 16 帧 512x512 的 MP4 确实生成，但逐帧检查为紫灰色噪声纹理，人物不可辨识；去除 IP-Adapter、切换 DDIM 参数、直接放入 GPU 后仍未修复。
- 旧版 v1-5 motion adapter 在当前 Diffusers 0.35.2 下配置不兼容；ToonYou checkpoint 不是当前 Diffusers pipeline 所需目录结构。
- 该实验判定失败，不导入正式成片。详细记录见 `ANIMATEDIFF_TEST_REPORT.md`。
- 下一步改查兼容版本/真正 image-to-video 模型；SVD 可用静态图作条件帧，但需注意 9.56GB 权重、T4 显存与商业许可限制。


## 2026-11-24 SVD 首轮实测补记
- Stable Video Diffusion 已实际导出 1024×576、7fps、8 帧、1.14 秒 H.264 短片，角色主体清晰，逐帧哈希不同。
- 视觉检查显示角色基本静止，仅头发/细节轻微变化，动作幅度不足，暂不进入正式成片。详见 `ANIMATEDIFF_TEST_REPORT.md`。
- 商业使用前必须核对 Stability AI license；模型卡说明当前模型 intended for research purposes。

## 2026-11-24 SVD 图片转视频实测（已跑通推理，动作质量待提升）
- `StableVideoDiffusionPipeline` fp16 组件在 Kaggle 免费双 T4 成功加载；14 帧首次 OOM，启用 `unet.enable_forward_chunking()` 后改为 8 帧成功。
- 输出 1024×576、7 fps、8 帧、1.14 秒 H.264 MP4；低 noise 版本人物近乎静止，提高到 noise_aug_strength=0.20、motion_bucket_id=220 后出现轻微头发/姿态变化，但还没有明显走路或转身动作。
- 已将 8 帧动态测试视频和抽帧对比图登记到正式项目文件列表，标记为测试素材，尚未进入正式成片。
- 详细记录见 `ANIMATEDIFF_TEST_REPORT.md`。SVD 使用 Stability AI Community License，商业用途需注册并遵守收入门槛及署名条款。


## 2026-11-24 提示词预算二次修订
- 远景单镜头已验证成功，但场景细节仍有偏差。复查 Worker 后发现旧逻辑对每个提示词片段采用“整段放入或整段丢弃”，长场景描述可能被整个丢弃。
- 新增 `build_prompt()`：按景别 → 场景 → 镜头提示 → 动作 → 风格 → 人物信息顺序填充，超长片段按 tokenizer 可用预算截短，目标不超过75 tokens。
- 新增 `tests/test_prompt_builder.py`，覆盖超长场景截断、优先级顺序和空片段；当前全套6项测试通过。
- 这次只完成 Box 本地代码与单元测试，尚未在 Kaggle GPU 上复测，不能宣称场景遵循度已改善。