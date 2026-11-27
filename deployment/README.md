# 漫剧工作台：可迁移部署手册

## 设计原则
- 控制台与GPU执行器分离。控制台只负责项目、角色、分镜、队列和素材；GPU平台可随时替换。
- 所有GPU任务通过统一任务JSON进入队列，Worker只需实现领取任务、下载素材、执行、回传结果。
- 平台目录在 `providers.json`；当前已验证平台与候选平台明确区分。
- 默认只启用免费/本地平台。任何付费GPU或付费API都必须先取得用户明确批准。
- 密钥只放宿主机环境变量或平台Secrets；严禁写入Git、备份包、Notebook输出或前端存储。

## 最小恢复（Ubuntu 22.04/24.04）
1. 解压完整备份，进入 `manga-console` 目录。
2. 安装 Python 3.10+；控制面核心只依赖Python标准库。
3. `python3 -m venv .venv && . .venv/bin/activate`
4. `pip install -r requirements.txt`
5. 从密码管理器/私密记录重新设置 `data/access.key` 和 `data/test_access.key`（测试密钥可不创建）。不要从公开备份恢复旧密钥。
6. `python3 server.py`，检查 `curl http://127.0.0.1:8766/api/health`。
7. 在反向代理或平台配置中启用HTTPS；不要直接把8766端口裸露到公网。

## GPU平台切换
- Kaggle：上传/打开 `kaggle/notebook57e8a33be4.ipynb`，挂载私有模型数据集 `jincangma/ai-manga-model-bundle`，在Notebook Secrets设置 `MANGA_WORKER_KEY`，并配置 `MANGA_BOX_URL`（填入工作台 HTTPS 公网地址，不要带末尾路径）。模型目录从数据集复制到工作盘后再加载。
- Colab：复制Notebook，按其GPU和磁盘容量调整batch大小/分辨率；设置同名Secrets；先跑 `python kaggle/prepare_kaggle_runtime.py`（按脚本参数）并验证单图，再启动worker。
- 本地ComfyUI：启动ComfyUI API，设置 `COMFYUI_BASE_URL`；导入版本化JSON workflow；验证模型/节点依赖后再接任务队列。
- NVIDIA NIM/API Catalog：设置 `NVIDIA_API_KEY`，用于支持的托管LLM/VLM/语音模型调用。它不提供一张可供本项目任意运行Stable Diffusion的GPU，也不等于GPU租赁。
- RunPod等付费平台：默认关闭。必须用户明确批准预算、实例规格和启动操作后才可启用。

## 版本与资产
- 代码：整个项目目录 + `deployment/`。
- 用户数据：`data/projects.json`、`data/production/`、`data/uploads/`、`data/project_workspaces/`、`data/gpu_jobs/`、`data/tts_jobs/`、`data/renders/`、`data/usage/`。
- 模型：模型大文件单独保存在私有模型数据集/模型仓库；备份 `manifest.json`、文件名、版本、SHA256、许可证和下载来源。不要把模型文件误当作源代码备份。
- Notebook：保留 `.ipynb`、Worker脚本、依赖版本、GPU规格和实测报告。

## 备份验证
对每个归档运行 `tar -tzf archive.tar.gz >/dev/null` 并保存 `sha256sum`。至少保留一份不在当前云主机的副本。当前Box内备份不是异地备份。

## NVIDIA API Catalog接口（本版本已预留服务端接口）
- 在宿主机Secret中设置 `NVIDIA_API_KEY`，可选设置 `NVIDIA_BASE_URL`、`NVIDIA_MODEL`，重启控制台进程。
- `GET /api/nvidia/status`（需访问口令）只返回是否已配置，不返回密钥。
- `POST /api/nvidia/chat`（需访问口令）接受 `{ "messages": [{"role":"user","content":"..."}] }`，由服务端调用NVIDIA Chat Completions。
- `GET /api/providers/catalog` 返回平台适配目录。
- 此接口可供后续故事分析、剧本/分镜辅助、提示词优化使用；当前尚未接入前端自动编剧按钮，也不负责Stable Diffusion关键帧推理。


## API密钥管理中心
- 后台「平台额度与用量 → API密钥管理中心」可保存 NVIDIA API Catalog Key 或其他 OpenAI 兼容服务的 Key。
- Key保存在 `/workspace/manga-secrets/provider-keys.json`，目录权限0700、文件权限0600；不在项目目录、不进入项目备份、不返回前端。当前为文件权限隔离，尚未做静态加密；生产部署建议使用云平台Secret Manager或磁盘加密。
- 保存后点「测试连接」，服务端向所选模型发出一个很短的测试请求。该请求可能产生少量Token费用，需先确认平台余额/免费额度。
- NVIDIA API Catalog默认地址 `https://integrate.api.nvidia.com/v1`。其他兼容服务填写其HTTPS Base URL和模型ID。
- NVIDIA Key必须是API Catalog/NIM推理API使用的Key；NGC个人访问令牌不一定能直接调用Chat Completions。


## 后台登录安全设置
- 后台初始登录口令由部署管理员设定，登录后可从页面右上角「后台设置」修改。
- 修改口令会立即更新 `data/access.key`，文件权限为0600，旧后台口令立即失效；新口令长度8—128位。
- GPU Worker凭据与后台登录口令分离，改后台口令不应影响Kaggle Worker认证。Worker凭据只通过独立校验路径验证，不写入项目归档。


## 多模型路由（2026-11-25）
- `/api/llm/chat` 是统一的服务端文本模型路由，可选 `nvidia-nim` 或 `generic-openai-compatible`，Key 不会发到浏览器。
- NVIDIA API Catalog 默认模型：`nvidia/nemotron-3.5-bolt-e4b`；DeepSeek 官方兼容地址：`https://api.deepseek.com`，默认模型 `deepseek-flash`。
- DeepSeek Flash 官方 API 支持文本与图像理解；V4-Pro 为文本模型。模型与费用会调整，实际计费以平台控制台为准。
- API Key 只负责调用托管模型，不提供 GPU 显存；图像/视频生成仍走 Kaggle、ComfyUI 或未来GPU Worker。
- 大陆云GPU适配采用统一 Worker HTTP 契约：健康检查、领取任务、上传结果；优先让 GPU 节点主动向控制台拉任务，不要求公网暴露GPU管理端口。后续可增加阿里云PAI、腾讯云TI平台、AutoDL等具体适配器，先核验API和费用，不默认开机。


## 轻量服务器建议（2核2G / 40GB）
- 这类实例只运行控制台、项目元数据、任务队列和少量上传；不要在控制台服务器安装或运行视频生成模型。
- GPU推理仍由Kaggle等独立Worker执行，Worker主动通过HTTPS拉取任务并回传结果。
- 2GB内存实例建议只运行一个控制台进程；不要同时在服务器运行ComfyUI、Torch、Diffusers或大型文本模型。
- 40GB磁盘可用于程序和短期素材，但本项目当前数据目录约2.3GB，随着视频素材增加必须启用异地备份/对象存储，不能把40GB当成长期视频仓库。
- 生产公网访问应通过Nginx/Caddy反向代理启用HTTPS；防火墙只开放SSH和80/443，不直接公开8766。
- 腾讯云试用实例需核对试用到期策略、自动续费、地域、带宽和月流量；部署前先备份现有数据。

## 当前制作规格与验收
- 本项目《命劫：阴婚玉佩》第一集输出规格为横屏16:9，关键帧工作流实测输出896×512。
- 关键帧验收应先做少量镜头试样；不要把结构检查通过等同于画面质量通过。
- 视觉质量仍需检查角色身份、动作、服装、构图和场景连续性；结构QC不宣称已经完成视觉判定。


## Box/云电脑重启后的工作台恢复
- 项目文件保存在持久工作区；Box重启/休眠恢复后，后台Python进程可能不会自动恢复。
- 在项目目录运行 `./start_workbench.sh`，脚本先检查健康接口，再安全启动单个控制台进程；若进程存在但健康检查失败，会停止并提示检查日志，不会盲目再启动一个实例。
- 启动后验证 `curl http://127.0.0.1:8766/api/health`。
- GPU Worker与控制台生命周期分开；控制台恢复不代表Kaggle Worker在线，也不会自动执行队列任务。
- 备份脚本排除本地备份目录和GPU任务队列快照，避免备份包递归膨胀；GPU任务记录应通过项目数据备份策略单独纳入，不把排队任务当作可安全重放的生成指令。


## SCNet Hygon DCU（预检适配阶段）
- 独立适配目录：`scnet/`；不复用 NVIDIA/Kaggle Worker，不安装 CUDA。
- `scnet/submit_preflight.sbatch` 是单节点、单张 `dcu`、10分钟的 Slurm 预检模板；当前未提交作业。
- `scnet/dcu_preflight.py` 只在计算节点内读取 `hy-smi`/`rocm-smi`、Python、PyTorch与Slurm环境，并可向 `/api/worker/scnet/heartbeat` 回传脱敏状态。
- `GET /api/worker/scnet/status` 提供Worker-key保护的节点状态查询。
- 目前只完成接口与预检适配，不代表关键帧生成已适配海光DCU。需实测DTK、PyTorch、模型算子和图像输出后，才能开放生产任务队列。
- 首次提交前需把更新后的 `server.py` 部署到工作台，并由用户确认卡时额度后再提交单卡10分钟预检。


## ModelScope仓库与在线Studio接口（2026-11-27）
- `modelscope-hub` 是模型仓库适配器；`modelscope-studios` 是网页托管应用入口，两者不是同一种服务。
- `GET /api/modelscope/status`（需工作台登录）只返回Token是否已配置，不返回Token内容。
- `MODELSCOPE_TOKEN` 可在后续认证阶段放入宿主机Secret；也可由 `/var/lib/manga-console/provider-keys.json` 中 `modelscope-hub.access_token` 提供。当前未配置Token不影响公开模型卡浏览。
- `modelscope_adapter.py` 提供repo_id校验和显式下载函数；默认拒绝下载。只有确认模型体积、许可、磁盘和DTK兼容性后，才允许显式调用下载。
- ModelScope Studio网页显示Running仅代表应用实例页面可访问，不代表推理成功、免费额度充足或已获得API调用权限。在线Studio认证、免费额度、队列和输出能力需逐个核验。
- SCNet通过独立Slurm DCU Worker适配器接入：`scnet/submit_preflight.sbatch`、`scnet/dcu_preflight.py`；心跳 `POST /api/worker/scnet/heartbeat`，状态 `GET /api/worker/scnet/status`。Worker Key认证和单卡预检仍待完成，未提交GPU作业。
- 迁移时必须带上项目源代码、`deployment/providers.json`、`scnet/`、`modelscope_adapter.py` 和依赖清单；密钥仍通过新环境Secret重新注入，不随项目目录打包。
