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

## 模型缓存持久化（新增，尚待 Kaggle UI 实测）
Kaggle 官方 Notebook 文档说明：Notebook 版本最多可保存 20 GB 的 `/kaggle/working` 输出，并可在后续 Notebook 中通过 Add Input 复用。`/kaggle/tmp` 是临时 scratch，不能作为持久缓存。

1. 免费 GPU/CPU 会话中先完成模型下载和校验，确保 `AnythingV5` 与 `IP-Adapter` 两个 snapshot 都存在于 `/kaggle/tmp/hf_cache`。
2. 执行 `python /kaggle/working/persist_model_cache.py pack`，将完整 Hugging Face cache 打包为 `/kaggle/working/manga-model-cache.tar.gz`。脚本会检查两个必需模型目录、归档目录结构和 18 GiB 安全上限，不覆盖旧归档。
3. 在 Kaggle 的 Save Version → Quick Save → Advanced Settings 中启用保存输出文件；不要选 Save & Run All，以免整本历史 Notebook 重跑并消耗 GPU。
4. 后续会话在 Input → Add Input 中挂载该 Notebook 的已保存输出，将归档复制/读取到工作目录，再运行 `python persist_model_cache.py restore --archive <输入目录>/manga-model-cache.tar.gz --dest /kaggle/tmp/hf_cache`。
5. 恢复后运行 worker；`MANGA_MODEL_CACHE=/kaggle/tmp/hf_cache`。模型归档只保存模型权重，不包含 API key、访问口令或项目私人资料。

该方案利用 Kaggle 官方支持的 Notebook 输出复用能力；本项目尚未在真实 Kaggle UI 完成 Quick Save、Add Input 和恢复验证，因此目前是待验证的持久化方案，不应宣称已完成。