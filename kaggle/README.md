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

## 已验证的免费双 T4 图片生成配置（2026-11-24）
- Kaggle 双 T4 是两张独立显卡，不能把显存相加使用。
- 实测某次会话 GPU 0 仅余约 4.1 GiB，而 GPU 1 余约 14.8 GiB；默认 `cuda:0` 的横屏生成发生 OOM。
- 将空闲物理卡映射为进程内唯一可见卡后，768×432 单镜头生成通过：
  `CUDA_VISIBLE_DEVICES=1 MANGA_WIDTH=768 MANGA_HEIGHT=432 MANGA_IP_ADAPTER_SCALE=0.45 MANGA_STEPS=18 python batch_generate_from_jobs.py --max-shots 1`
- 实测产物：`manga_keyframes_landscape/shot_0001.png`，768×432，574,067 bytes；worker 报告单镜头约 6.4 秒。
- 这是单镜头显存/尺寸验证，不代表人物一致性或画面内容验收通过。生成前仍须查看 `nvidia-smi`，确认被选中的物理 GPU 有足够空闲显存；不要并发运行 MimicMotion 和图片生成。
- Kaggle 临时会话及 `/kaggle/tmp` 缓存并非永久存储；每次会话结束前需导出并备份关键帧与 manifest。

- 后续横屏构图复核：把提示词强化为远景/环境占主导，并将 IP-Adapter scale 降至 0.30 后，768×432 仍生成明显人物近景；因此远景构图验收未通过。参考图本身是近景，当前 IP-Adapter 对景别控制仍不可靠。已将 Kaggle jobs JSON 恢复为原始“中景”测试配置；该远景测试图仅作为失败样本留档，不进入正式镜头。

## 参考图裁剪模式（2026-11-24）
- 原角色参考图为 512×512 头肩近景，直接作为 IP-Adapter 输入时会把景别偏向人像。
- Worker 新增可选环境变量 `MANGA_REFERENCE_CROP=face`：将输入参考图裁剪到归一化框 (0.20, 0.10, 0.80, 0.72)，保留脸部、发型和头饰，减少肩部/胸像构图特征。
- 默认 `none` 保持旧行为；该模式已通过 Python 语法检查，但仍需在 Kaggle GPU 上与原始参考图做 A/B 生成对照，不能视为人物一致性或远景控制已验证。
