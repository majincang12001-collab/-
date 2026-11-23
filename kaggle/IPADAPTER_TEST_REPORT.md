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
[exit status 0]