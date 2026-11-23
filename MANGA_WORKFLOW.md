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
2. 在 Kaggle Notebook 的 `/kaggle/working` 上传该 JSON，以及选定的角色参考图并命名为 `character_reference.png`。
3. 确认 Kaggle 免费 GPU Session 已开启，且 `/kaggle/working/hf_cache` 内已有 AnythingV5 fp16 和 IP-Adapter SD15 Light 缓存。
4. 运行 `kaggle/batch_generate_from_jobs.py`。它按分镜逐镜生成 512×512 图片，命名为 `shot_0001.png`、`shot_0002.png`，同时生成 `manga_keyframes.zip`。
5. 下载 ZIP，解压后把 `shot_0001.png` 等图片上传到工作台同一个项目。每张图应按镜头编号命名，才能自动匹配。

注意：已用 Notebook 交互代码真实生成一张 512×512 关键帧（约 5.2 秒）；批量 worker 已增加模型缓存路径检查和 `MAX_SHOTS` 限制，语法检查通过，但尚未在 Kaggle 实机跑通。首次运行只跑 1 条分镜。当前 512×512，不是 1080P。人物参考图条件控制不保证每镜完全一致。

## C. 合成动态分镜预览 MP4（已完成真实测试）
1. 确认分镜已经保存。
2. 确认上传了 `shot_0001.png` 等关键帧；若缺失，系统会用文字占位卡，不会假装那是动漫原画。
3. 点击“生成动态分镜预览 MP4”。
4. 系统会添加轻微推近、普通话 eSpeak NG 旁白、中文字幕并合成为 MP4，任务完成后浏览器自动下载。

这是静态关键帧动态化的 animatic，不是 AI 模型生成的角色动作视频。已验证两镜头混合输入（一个真实 AI 关键帧 + 一个占位卡）输出 1280×720 MP4。中文 TTS 为离线合成测试音色，音质较机械。

## D. 目前未完成
- 真正的大模型自动编剧与语义分镜。
- 工作台与 Kaggle 的自动队列/自动文件回传。
- AnimateDiff / Stable Video Diffusion 等图生视频模型的实机验证。
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
[exit status 0]