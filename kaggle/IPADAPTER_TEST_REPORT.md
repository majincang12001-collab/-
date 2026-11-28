# IP-Adapter 人物参考图首轮测试报告

日期：2026-11-23  
环境：Kaggle 免费 Draft Session，2×Tesla T4；PyTorch 2.11.0+cu130；Diffusers 0.35.2；Transformers 4.57.1；Hugging Face Hub 0.36.2。

## 已验证

- CUDA 可用，GPU 数量为 2，型号均为 Tesla T4。
- `/kaggle/working/hf_cache` 中存在 `Yntec/AnythingV5` SD1.5 Diffusers fp16 模型，以及 `h94/IP-Adapter` 的 `ip-adapter_sd15_light.safetensors` 和 image encoder。
- AnythingV5 通过 `variant="fp16"` 成功加载到 GPU。
- 禁用 attention slicing 后，`load_ip_adapter(..., subfolder="models", weight_name="ip-adapter_sd15_light.safetensors", image_encoder_folder="image_encoder")` 成功。
- 以同一张主角参考图作为 `ip_adapter_image`，成功生成正面、侧面、全身三个 512×512 图片及联系表。首轮生成代码报告三张图合计约 9.1 秒。
- 生成结果已下载并保存到漫剧工作台项目，另有 ZIP 汇总包。

## 兼容性注意

- 底模缓存使用 fp16 文件名，加载时必须设置 `variant="fp16"`，否则会报找不到默认权重文件。
- 在当前 Diffusers 0.35.2 测试环境中，先执行 `enable_attention_slicing()` 会导致 IP-Adapter 加载时报 `SlicedAttnProcessor` 构造参数错误。当前可行顺序是先加载底模、关闭 attention slicing、再加载 IP-Adapter。

## 质量判断

这次证明了“参考图 → IP-Adapter 条件控制 → 多视角图片”链路可运行，但**尚未达到稳定角色一致性**。首轮联系表中，侧面和全身图出现了重复人物/构图偏差，人物脸部、服装细节仍有变化。不能把固定 seed 或这次成功加载等同于角色锁定。

当前结果是 512×512 原型，不是 1080P，更不是视频成片。

## 下一步

1. 继续用同一参考图测试 IP-Adapter scale 0.75–0.85，并使用 `solo / single character / no duplicate person` 等约束及负面提示词。
2. 建立标准角色设定图，统一发型、眼睛、服装、饰品，再比较正面、侧面、全身的脸部与服装一致性。
3. 若姿势变化导致构图失控，再加入开源 ControlNet OpenPose。
4. 若 IP-Adapter 仍无法锁定身份，再评估小型角色 LoRA 训练；训练前先准备多张同一角色参考图。

全程没有启动付费 GPU、付费 API 或充值。

## 2026-11-24：脸部参考裁剪 + CLIP 提示词优先级修复

### 根因
- 首轮脸部裁剪测试虽成功生成 768×432 图片，但仍然是近景。
- Worker 日志明确提示输入提示词约 300 tokens，SD1.5 CLIP 最大长度 77 tokens，后半段镜头构图提示被截断。

### 修复
- 将镜头景别和场景提示放在 prompt 最前面。
- 使用 pipeline tokenizer 逐段加入提示词，限制总长度不超过 75 tokens。
- 角色参考裁剪从头肩范围收紧为归一化框 (0.25, 0.05, 0.75, 0.55)。
- 将 IP-Adapter scale 从 0.30 降至 0.15，降低近景参考图对构图的牵引。
- Worker 在导入 PyTorch 前自动选择空闲显存最多的 GPU。

### 实测结果
- Kaggle 双 T4 自动选择物理 GPU 1，当时空闲显存 14,912 MiB。
- 768×432，18 steps，IP-Adapter scale 0.15。
- 单镜头推理成功，生成 PNG 528,355 bytes；已下载并通过 Pillow 校验尺寸 768×432。
- 视觉检查：人物已变为全身远景，环境建筑可见；不再是之前的人像近景。场景细节仍未完全符合“古城灯笼街道”描述，需进一步提升背景场景遵循度。
- 输出已登记正式项目：`shot_0001_facecrop_wide_768x432.png`。

### 结论
- 构图控制有实质改善，脸部参考裁剪、提示词预算和较低 IP-Adapter 权重组合值得保留。
- 这仅验证单镜头远景构图，不代表跨镜头角色一致性已通过，也不代表动作视频链路已通过。

## 2026-11-24：提示词预算二次修订（仅本地测试）
- 旧逻辑遇到过长提示词片段时会整段跳过，可能导致具体场景描述完全不进入 CLIP。
- 新增 `build_prompt()`：按景别、场景、镜头提示、动作、风格、人物信息顺序逐段填充；超长片段按 tokenizer 预算截短，不再整段丢弃；目标上限75 tokens。
- 新增3项单元测试，覆盖超长场景截断、优先级顺序、空片段；全项目6项测试通过。
- 当前只在 Box 本地完成代码和单元测试，Kaggle GPU 实测尚未执行。下一次 GPU 会话需要先做单镜头 A/B 对照，不能提前认定背景场景遵循度已提升。

## 2026-11-24：全新 Kaggle Session 恢复验收通过

- 主动停止原 Draft Session，再启动全新 Session；新 Session 的运行计时从 0m 重新开始。
- 两个 Kaggle 私有数据集在新 Session 中自动挂载：模型包位于 `/kaggle/input/datasets/jincangma/ai-manga-model-bundle`，关键帧数据位于 `/kaggle/input/datasets/jincangma/ai-manga-keyframes-20261124`。
- 新 Session 检查到模型清单中的 18 个模型文件，逐一重新计算 SHA256，18/18 匹配，0 失败。
- 新 Session GPU 为 Tesla T4（约 15GiB 显存）。AnythingV5 从只读 Dataset 挂载路径加载成功，IP-Adapter 从本地暂存目录加载成功。
- 真实完成一次 512×512、4 步 GPU 条件生成，输出 `/kaggle/working/fresh_session_smoke.png`，大小约 495KB；日志标记 `FRESH_SESSION_MODEL_AND_GPU_INFERENCE_OK`。
- IP-Adapter 原始挂载路径的 image encoder 权重读取很慢（FUSE 进度长时间不前进）；将 2.57GB 的 IP-Adapter 目录顺序复制到 `/kaggle/working/manga_model_bundle/IP-Adapter` 后，模型加载与推理顺利完成。正式 worker 应沿用此暂存策略。

### 结论

模型私有数据集的跨 Session 恢复、SHA256 完整性、底模/IP-Adapter 加载及真实 GPU 推理全部通过。尚未在新 Session 完成的是正式两镜头 worker 的完整执行及新输出回传；本次 4 步测试只证明恢复与推理链路，不作为画质或人物一致性验收。

## 2026-11-24：新 Session 同参考图双镜头试验

- 从项目私有资料中取出同一张黑发女主脸部参考裁剪，压缩后导入新 Kaggle Session；未使用公开分享链接。
- 在新 Session 已加载的 AnythingV5 + IP-Adapter 上，固定同一参考图、同一 IP-Adapter scale 0.65，分别生成古城门远景和红灯笼街中景；两张 768×432 PNG 均成功落盘，文件大小约 604KB / 592KB。
- 该轮为 12 steps 的快速对照，不是最终画质设置。缩略图预览可见人物风格存在蓝紫色偏移，尚不能判定脸部身份与服装跨镜头锁定通过；需要放大检查，并进一步优化参考图/姿势控制。
- 生成目录：`/kaggle/working/fresh_character_consistency/`。当前新 Session 尚未结束，输出暂存于工作盘，尚未回传到私有结果 Dataset。

## 2026-11-24：低权重身份条件与 MimicMotion 环境恢复

### IP-Adapter A/B
- 同一张女主脸部参考图，在 scale 0.30 下重新生成古城门远景和红灯笼街中景，768×432、20 steps。
- 场景的暖色灯笼、灰色石板路和木质街景表现较上一轮更贴近提示词；但中景缩略预览仍有疑似重复人物，人物身份/单人构图尚未验收。额外尝试 scale 0.25 + 单人/空街约束，生成一张 24 steps 中景图，仍需放大检查。
- 输出暂存 `/kaggle/working/fresh_character_consistency/`，尚未上传私有结果 Dataset。

### MimicMotion 环境
- 新 Session 通过 GitHub 克隆官方 Tencent/MimicMotion 仓库。
- 官方 checkpoint `MimicMotion_1-1.pth` 已从 Hugging Face 下载完成，大小 3,049,867,447 bytes（约 2.84 GiB）。
- 安装 `omegaconf`、`decord`、`einops`、`imageio-ffmpeg`、`onnxruntime` 成功。
- 修补当前 Kaggle torchvision 缺少 `torchvision.io.write_video` 的兼容问题：改为 OpenCV 写 MP4。
- `inference.py --help` 已成功运行，说明官方推理入口及参数解析已通过。
- 尚未完成 MimicMotion 实际推理：配置默认依赖 Stable Video Diffusion XT 1.1 基础模型，以及 DWPose 相关模型/样例素材。当前尚未下载并挂载这些大权重，也尚未运行16帧生成。
