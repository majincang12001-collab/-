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
[exit status 0]