# AnimateDiff 视频生成实验报告（2026-11-24）

## 目标
在 Kaggle 免费双 T4 上验证：使用现有 SD1.5 动漫底模和角色参考图，生成真正具有连续帧的 AI 动作片段，并评估能否进入漫剧渲染流程。

## 环境与模型
- Kaggle Draft Session：双 Tesla T4；使用免费 GPU，不启动付费资源。
- Diffusers 0.35.2、Transformers 4.57.1、Accelerate。
- 底模：`Yntec/AnythingV5`（本地 fp16 Diffusers snapshot）。
- 动态模块：`guoyww/animatediff-motion-adapter-v1-5-2`（fp16，已下载约 1.82GB）。
- IP-Adapter：`h94/IP-Adapter` SD15 Light。
- 角色参考图：`heroine_reference_seed20261123.png`。

## 实测结果
1. AnimateDiff + AnythingV5 + IP-Adapter：Pipeline 初始化成功（约 10.9 秒）；16 帧、512×512、12 steps 推理完成（约 46.8 秒），导出 H.264 MP4（2 秒，155,255 bytes）。
2. 逐帧检查后发现输出为紫灰色噪声/纹理，人物不可辨识，**不符合可用视频标准**。不能将“推理成功/MP4 存在”视为生成成功。
3. 去除 IP-Adapter、保留 CPU offload，20 steps 仍输出噪声；改为正确 DDIM 参数（clip_sample=False、timestep_spacing=linspace、beta_schedule=linear、steps_offset=1），25 steps 仍未修复。
4. 改为直接 `.to('cuda')`、256×256、8 帧、16 steps，推理约 6.7 秒，输出仍为抽象紫色纹理。
5. 尝试旧版 `animatediff-motion-adapter-v1-5`，在 Diffusers 0.35.2 下出现配置 ValueError，未能加载。
6. 尝试 `frankjoshua/toonyou_beta6`，下载到的仓库结构为单体 checkpoint，不含 Diffusers UNet 权重路径，不能直接用当前 `AnimateDiffPipeline.from_pretrained` 加载；已清理该试验缓存。

## 结论
- AnimateDiff 模型下载、Pipeline 组装、GPU 推理和 MP4 导出流程都能执行，但当前组合输出质量失败；此实验**不算完成 AI 动态视频能力**。
- 失败视频仅用于调试，不导入正式项目，不用于成片。
- 下一步优先检查 motion adapter 与 Diffusers 版本兼容性，或转向真正的 image-to-video 模型进行单帧驱动测试。

## 下一候选路线：Stable Video Diffusion（SVD）
- 官方 Diffusers 文档提供 `StableVideoDiffusionPipeline` 的图片驱动视频流程。
- `stabilityai/stable-video-diffusion-img2vid` 模型卡说明它以静态图为条件帧生成短视频，训练目标为 14 帧、576×1024；模型可能输出静止画面或缓慢镜头移动，人物/脸部也可能不稳定。
- 官方模型权重约 9.56GB（另有 image decoder 权重），Kaggle 盘空间和 T4 显存可能成为瓶颈；需要先核算下载体积和 offload 方案，不能保证一次成功。
- 商业使用须先核对 Stability AI license，不把研究测试等同于可商业部署。

## 质量门槛
只有当抽帧检查确认主体清晰、帧间存在合理动作、无大面积噪声/崩坏，并且 MP4 可解码时，才标记为“AI 动态视频测试通过”。

## 2026-11-24：Stable Video Diffusion（SVD）首轮实测补记
- Kaggle Draft Session 中已存在并成功导出的 `svd_heroine_test.mp4`；Box 下载副本 `/home/user/Downloads/svd_heroine_test.mp4`，大小 114,628 bytes。
- ffprobe 实测：H.264、1024×576、7 fps、8 帧、时长 1.142857 秒。逐帧 MD5 均不同，说明并非完全重复帧。
- 对输出帧联系表进行视觉检查：主角清晰可辨，未出现 AnimateDiff 那种整幅紫灰噪声；但人物基本保持原姿势，主要是头发/局部细节轻微变化，动作幅度不足，尚不能作为剧情动作镜头验收。
- 因此 SVD 当前判定为“技术链路初步可用、动作质量未通过”，只算比 AnimateDiff 噪声结果有进步，不算正式图生视频能力完成。
- SVD 官方 Diffusers 文档提供 image-to-video pipeline，并说明可通过 model CPU offload、UNet forward chunking、较小 decode_chunk_size 降低显存，文档称可降至 8GB 以下；但 `decode_chunk_size=1` 可能增加闪烁。参考：https://huggingface.co/docs/diffusers/main/en/api/pipelines/stable_diffusion/svd
- 官方模型卡注明模型用于 research purposes；商业用途需另行查看 Stability AI license；模型本身也可能只生成静止画面或缓慢镜头移动，人物面部可能异常。参考：https://huggingface.co/stabilityai/stable-video-diffusion-img2vid
- 下一轮重点：提升 motion_bucket_id 与 noise_aug_strength 的小范围网格测试，逐帧检查人物面部/服饰漂移，并把 8 帧短片延长为可用镜头；每次只保留少量测试输出，避免占满 Kaggle 19.5GiB 输出盘。

## 2026-11-24：SVD 动态参数第二轮（已生成，待回传验片）
- 复用当前 Kaggle Session 中已加载的 `StableVideoDiffusionPipeline` 和 1024×576 主角输入图，没有重新下载模型。
- 运行参数：8 帧、10 inference steps、`decode_chunk_size=2`、`motion_bucket_id=180`、`noise_aug_strength=0.1`、7fps、seed=20261124。
- 控制台推理结束后，Kaggle `/kaggle/working` 输出列表出现 `svd_motion180_test.mp4`、`svd_heroine_motion2.mp4` 和 `svd2_frame_00.png` 至 `svd2_frame_07.png`。
- 目前只确认文件已生成；尚未下载第二轮 MP4 到 Box，也未对逐帧动作/人物漂移做视觉验收，因此**不标记为质量通过**。下一步必须先回传并验片，再决定参数是否有效。
- 当时 Kaggle `/kaggle/working` 可用空间约 6.33GiB，CUDA 可用，首张卡识别为 Tesla T4；本轮只使用 cuda:0。

## SVD image-to-video 实测进展（2026-11-24）

### Pipeline 与显存
- 使用 `stabilityai/stable-video-diffusion-img2vid` 的 Diffusers fp16 组件加载 `StableVideoDiffusionPipeline` 成功。
- 首次 14 帧推理遇到 CUDA OOM；启用 `unet.enable_forward_chunking()` 并将测试缩到 8 帧后成功，`decode_chunk_size=1`。
- Kaggle 仍为免费双 T4 Draft Session；没有启动付费资源。

### 已生成结果
- SVD 8 帧、1024×576、7 fps、10 steps，视频时长 1.142857 秒，H.264，文件 161,689 bytes。
- 第一组低噪声（0.02）输出人物基本静止，主要是背景轻微变化，不通过动作验收。
- 第二组 noise_aug_strength=0.10、motion_bucket_id=180，背景运动更明显，但人物主体动作仍较弱。
- 第三组使用 768×768 输入、noise_aug_strength=0.20、motion_bucket_id=220，8 帧输出；相邻帧平均像素差约 9.96–21.70，人物头发/局部姿态有轻微变化，仍不是明显的走路、转身等完整动作。
- 对另一张批量生成角色图也完成 8 帧推理，逐帧平均像素差约 12.3–23.66；需要继续做抽帧人工验收。
- 已将一段 8 帧、1.14 秒 SVD 动态测试视频和抽帧对比图导入正式项目文件列表，明确标注为“动态测试”，没有冒充成片镜头。

### 当前判断
- **SVD 图片转视频的模型加载、T4 推理、视频导出已打通；但人物动作幅度和角色稳定性还不够，暂不接入正式成片渲染。**
- 下一步尝试针对完整剧情关键帧进行动作提示/输入构图优化，评估人物主体运动幅度，再决定是否扩展成多镜头。

### 许可提醒
- 当前 SVD 模型使用 Stability AI Community License：年收入低于 100 万美元的有限商业使用可免版税，但商业用途须向 Stability AI 注册；分发模型材料或衍生作品还需保留许可和署名要求。正式商业部署前需逐项核对许可条款。

## 2026-11-24：SVD motion_bucket_id=180 回传验片结果
- 已从 Kaggle Output 下载 `/kaggle/working/svd_heroine_motion2.mp4` 到 Box：`/home/user/Downloads/svd_heroine_motion2 (1).mp4`。
- ffprobe：H.264、1024×576、7fps、8帧、1.142857秒，113,909 bytes。
- 逐帧联系表视觉检查：大部分帧保持同一姿态，部分帧出现明显模糊/拖影；没有形成清晰连续的人物动作。判定：**未通过剧情动作镜头验收**。
- 另一个已有输出 `svd_square_motion.mp4` 为同样 8帧、1024×576、7fps、161,689 bytes；对应 `svd_square_contact.png` 的 8帧联系表几乎完全静止，仅有微小形变，亦不通过。
- 结论：当前 SVD 在本角色图与免费 T4 设置下能生成视频容器和少量帧变化，但无法提供可用动作。暂不接入正式成片链路，不再用放大 motion_bucket 的方式盲目堆参数。
- 下一步改用动作可控、以姿态/骨架/参考帧驱动的开源视频方案做小样验证；优先在现有 T4 session 可承受范围内测试，不新增付费资源。

## 下一阶段方案：转向姿态驱动的 MimicMotion
- 依据官方仓库说明，MimicMotion 以参考人物图 + 姿态视频作为输入，能通过置信度姿态引导控制动作，比当前 SVD 单图条件更贴合漫剧角色表演需求。官方仓库：https://github.com/Tencent/MimicMotion
- 官方资源说明：72帧 v1.1 需要约16GB显存；16帧 U-Net 最低约8GB，但 VAE 解码器需要16GB，可将 VAE 解码放到 CPU。当前 Kaggle 单张 T4 约14.56GB，因此先尝试16帧配置 + CPU VAE，不直接尝试72帧配置。
- 后续验证顺序：先准备一个简单、清晰的姿态参考视频；16帧、低分辨率、FP16 小样；验收人物身份、服装、肢体结构、动作连贯性和显存峰值。确认后才考虑加帧和分镜接入。
- 版权/用途：MimicMotion 仓库有公开代码与模型权重；部署前仍需核对仓库 LICENSE 和权重许可，不默认认定可商用。
[exit status 0]