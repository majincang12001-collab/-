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
[exit status 0]