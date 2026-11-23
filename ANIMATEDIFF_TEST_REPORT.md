# AnimateDiff 视频生成实验报告（2026-11-24）

## 目标
在 Kaggle 免费双 T4 上验证：使用现有 SD1.5 动漫底模和角色参考图，生成真正具有连续帧的 AI 动作片段，并评估能否进入漫剧渲染流程。

## 环境与模型
- Kaggle Draft Session：双 Tesla T4；使用免费 GPU，不启动付费资源。
- Diffusers 0.35.2、Transformers 4.57.1、Accelerate。
- 底模：`Yntec/AnythingV5`（本地 fp16 Diffusers snapshot）。
- 动态模块：`guoyww/animatediff-motion-adapter-v1-5-2`（fp16，约 1.82GB）。
- IP-Adapter：`h94/IP-Adapter` SD15 Light。
- 角色参考图：`heroine_reference_seed20261123.png`。

## 实测结果
1. AnimateDiff + AnythingV5 + IP-Adapter：Pipeline 初始化成功（约 10.9 秒）；16 帧、512×512、12 steps 推理完成（约 46.8 秒），导出 H.264 MP4（2 秒，155,255 bytes）。
2. 逐帧检查后发现输出为紫灰色噪声/纹理，人物不可辨识，**不符合可用视频标准**。不能将“推理成功/MP4 存在”视为生成成功。
3. 去除 IP-Adapter、保留 CPU offload，20 steps 仍输出噪声；改为 DDIM 参数（clip_sample=False、timestep_spacing=linspace、beta_schedule=linear、steps_offset=1），25 steps 仍未修复。
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
