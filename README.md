# AI Manga Studio / 漫剧工坊

中文优先的 AI 漫剧制作工作台。本仓库只存放应用源码、GPU Worker 脚本和自动化测试，不包含用户项目数据。

## 当前制作流程

- 故事与分集项目管理
- 角色、场景参考素材和身份锚点
- 中文配音偏好
- 画风确认与可编辑分镜提示词
- GPU 关键帧任务队列和成片预览
- 模型平台配置与调用记录

网页提供八个流程节点导航：前六个进入现有编辑步骤，后两个跳转到 GPU 队列和成片区域。自由拖拽节点、任意分支连线仍属于后续开发，不应视为已完成能力。

## 启动

需要 Python 3.10 或更高版本：

```bash
python -m pip install -r requirements.txt
python server.py
```

打开 `http://127.0.0.1:8766`。后台口令从本地 `data/access.key` 读取。API Key 和 Worker 凭据必须保存在私密运行目录，不能写进源码。

## 数据与安全边界

公开仓库不包含：

- `data/`：故事、项目记录、上传文件、生成图片、音频和视频
- API Key、后台口令、Worker 凭据、Token、Cookie 和私密配置
- 模型权重、运行日志和本地缓存

部署时请保护 `/workspace/manga-secrets/`。如需旧版 Worker 哈希认证，可通过环境变量 `MANGA_WORKER_ACCESS_HASH` 或私密文件 `worker-access.sha256` 提供；不要将认证材料提交到 Git。

## 测试

```bash
python -m unittest discover -s tests -v
```

自动化测试通过不等于 GPU 出图质量、人物跨镜头一致性或图生视频效果已通过人工验收。
