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

## 模型缓存持久化（UI 跨会话探针已验证，真实模型归档待验证）
Kaggle 的 Notebook Quick Save 可以把当前 `/kaggle/working` 输出保存为该版本的 Output Data，并可通过后续会话的 Input → Add Input 挂载回来。实测过程：创建 40 字节探针文件 `persistence_probe.txt`，选择 Save Version → Quick Save → Advanced Settings → “Save output for this version”；版本成功保存后，在 Add Input → Your Work → Notebook 中添加自己的 Notebook 输出。停止 Draft Session，再重新启动 CPU 会话，读取挂载路径 `/kaggle/input/notebooks/jincangma/notebook57e8a33be4/persistence_probe.txt`，文件存在且内容完全一致（`manga-cache-persistence-probe-2026-11-24`）。整个探针没有启用 GPU。

### 模型缓存归档操作
1. 免费 GPU/CPU 会话中先完成模型下载和校验，确保 `AnythingV5` 与 `IP-Adapter` 两个 snapshot 都存在于 `/kaggle/tmp/hf_cache`。
2. 执行 `python /kaggle/working/persist_model_cache.py pack`，将完整 Hugging Face cache 打包为 `/kaggle/working/manga-model-cache.tar.gz`。脚本会检查两个必需模型目录、生成逐文件 SHA-256 清单、打包后校验归档内容，并限制在 18 GiB 安全上限。
3. 在 Kaggle 的 Save Version → Quick Save → Advanced Settings 中选择 “Save output for this version”。不要选 Save & Run All，以免整本历史 Notebook 重跑并消耗 GPU。
4. 后续会话在 Input → Add Input → Your Work → Notebook 中挂载保存输出；归档路径为 `/kaggle/input/notebooks/<owner>/<notebook-slug>/manga-model-cache.tar.gz`，再运行 `python persist_model_cache.py restore --archive <输入目录>/manga-model-cache.tar.gz --dest /kaggle/tmp/hf_cache`。
5. 恢复后运行 worker；`MANGA_MODEL_CACHE=/kaggle/tmp/hf_cache`。模型归档只保存模型权重，不包含 API key、访问口令或项目私人资料。

**验证边界：** Quick Save、Output Data、Add Input、停止会话、重新启动 CPU 会话、读取挂载文件这一整套持久化路径已经真实通过。真实 AnythingV5 + IP-Adapter 多 GB 权重的打包、保存和恢复尚未执行；待下一次有模型缓存的免费会话中完成。