# 开源社区资源用法研究与漫剧生产路线（2026-11-28）

## 结论

不要把不同供应商的算力拼成一个“大GPU”。采用 VPS 控制面 + 多个独立 Worker + 统一任务协议：VPS 只做项目状态、队列、权限、成本闸门和文件索引；GPU/CPU Worker 各自运行最适合的框架；任务按阶段分派，产物回传后校验并落盘。

## 可直接借鉴的社区实现

### 1. AMD MI300X / ModelScope：ComfyUI headless HTTP worker

AMD ROCm 官方教程明确支持在 AMD Instinct MI300X/MI325X/MI350X/MI355X 上以 Python 驱动 ComfyUI，不依赖浏览器。流程是启动 ComfyUI HTTP server，`POST /prompt` 提交 API-format workflow，`GET /history/<prompt_id>` 查询，`GET /view` 取回产物。教程实际演示 Wan2.2 5B image-to-video。

魔搭社区的 MI300X 实测文章给出关键避坑：镜像预装 ROCm PyTorch 时不要让 ComfyUI requirements 覆盖 torch/torchvision/torchaudio；先用 `rocm-smi`、`torch.version.hip`、`torch.cuda.is_available()` 验机；模型和工作流放 `/mnt/workspace`；先 warm-up 再测正式任务。ROCm PyTorch 仍使用 `torch.cuda` Python API 是正常现象。

本项目用法：AMD 192GB额度若成功启动，第一阶段只验证 Wan2.2 5B I2V 单镜头；之后才验证角色参考图、首尾帧衔接和长镜头。先使用已验证的5B工作流，不推断14B或FLF2V在当前镜像一定可用。

### 2. Hygon DCU / SCNet：先 Slurm，不要依赖 Notebook

SCNet 官方手册给出的只读核验入口：`whichpartition`、`sinfo`、`df -h ~`、`sacctmgr show assoc -P Format=Account,User,GrpSubmit,GrpJobs,GrpTRES`。批作业使用 `sbatch`；官方示例使用 `--gres=dcu:8`，但示例 partition/account 不能照抄，必须先查用户真实队列和额度。

HYGON-AI 的 inference-cookbook-das 是关键资料：其模型矩阵列出 BW1000 64GB 单卡运行 Qwen-Image 2.1（SGLang Diffusion 0.5.22、BF16、torch_sdpa、eager，关闭 torch.compile 和 BCG）；BW1000 可运行 Qwen3-VL（vLLM）；Qwen3-TTS 0.6B/1.7B 在 BW1000/BW1100 上有 vLLM-Omni 单卡部署示例。Qwen3-TTS 是 Talker + Code2Wav 两阶段流水线，提供 OpenAI 兼容 `/v1/audio/speech`。

注意：资源页面显示“8×64GB”不足以证明设备型号就是 BW1000。先从 SCNet 作业环境读取设备型号、DTK、驱动和 HCU PyTorch 版本，再按 cookbook 的设备/版本矩阵选容器和 wheel。不要在登录节点编译/跑推理，不要自行安装或覆盖系统驱动/DTK。

本项目用法：DCU 第一候选为 Qwen-Image 2.1 角色/场景关键帧；第二候选为 Qwen3-TTS 中文对白；第三候选为 Qwen3-VL 做镜头图像审查。Wan 视频生成暂不列为 DCU 首选，除非有该型号和 DTK 的明确视频算子验证记录。

### 3. ComfyUI-Distributed：可借鉴任务分派，不直接暴露公网

该项目提供 `POST /distributed/queue`，按 worker 健康状态分发 workflow，支持负载均衡、worker 结果回传和队列查询。但文档明确标注实验性质，并且不提供认证/授权，状态仍需通过 ComfyUI history/WebSocket；丢失提交 ACK 时也可能出现任务悬挂或重复执行风险。

本项目只借鉴 worker 注册、健康探测、负载均衡、结果回传概念；不能把未认证 ComfyUI 端口直接暴露公网。统一入口必须由我们的后端鉴权，Worker 只接收短期签名任务。

### 4. OpenX Flow：控制面与 GPU 后端分离

其结构是 VPS orchestrator 负责 Scheduler/Writer/Generator/Post-production，GPU backend 独立部署，通过统一 HTTP API 提交任务。它用上一镜最后一帧作为下一镜首帧，支持逐镜头生成、重试、最后帧回传、FFmpeg 汇总，并将 GPU backend 设计成可替换服务。

借鉴其接口边界和 last-frame chaining 思路，不直接整套搬入：仓库 README 的 GPU backend 示例主要是 Diffusers/CUDA 路径，不能直接视为已支持 Hygon DCU 或 ModelScope AMD；其部分文档声称 FLF2V，但当前查看的示例 server 文件主要展示 T2V/I2V，必须逐接口核实。

### 5. InstaBox：免费 CPU 适合突发任务，不是常驻 VPS

官方资料说明 Free 机器为 4 vCPU/8GB，空闲约30分钟自动暂停；免费计划对最大运行时长、机器数和快照有限制，免费计划不能通过外部定时 ping、忙循环或反复暂停/恢复来规避自动暂停/最长运行限制。按需执行真实任务，结束后自然空闲即可。

本项目用法：只承担短时 FFmpeg、字幕、文件校验和打包；不要把它当 24/7 调度器，也不允许后台定时心跳维持机器运行。先读 Usage/机器状态，再做用户真实任务。

## 建议的资源分工

| 工序 | 首选 | 备用 | 说明 |
| --- | --- | --- | --- |
| 剧本、对白、分镜文本、提示词 | 已接入 DeepSeek / NVIDIA 文本接口 | ModelScope API Inference（Token/额度核验后） | 文本调用与GPU资源分开计量 |
| 角色设定图、场景图、道具图、关键帧 | Hygon DCU + Qwen-Image 2.1（型号/配套验证后） | AMD ComfyUI 图像 workflow | 先做一张验收图，再批量 |
| 图片理解、角色一致性/画面质检 | Hygon DCU + Qwen3-VL（版本核验后） | NVIDIA VLM | 质检任务与生成任务分开 |
| 中文配音、音色设计 | Hygon DCU + Qwen3-TTS | 已批准的其他 TTS | 先测短句、音色和音画同步 |
| 视频镜头 | ModelScope AMD MI300X + ComfyUI Wan2.2 5B I2V | 已有 MiniMax API（调用前检查成本闸门） | 先验证单镜头，不默认批量付费 |
| FFmpeg、字幕、混音、打包 | InstaBox Free 机器按需运行 | VPS CPU 小任务 | 不允许空转保活；任务结束后停止活动 |

## 统一 Worker 协议必须具备

- `GET /health`：报告在线、框架、设备型号、显存/内存、模型已加载情况。
- `POST /jobs`：提交带 `job_id`、`project_id`、`stage`、`input_artifacts`、`parameters`、`idempotency_key` 的任务。
- `GET /jobs/{id}`：queued/running/succeeded/failed/cancelled 状态、进度、错误码、资源用量。
- `GET /jobs/{id}/artifacts`：产物清单、SHA256、MIME、尺寸、时长、存储位置。
- Worker 任务租约、心跳和超时；提交超时后先查 job_id/idempotency_key，不盲目重复提交。
- 每个阶段写 manifest/checkpoint；失败从最近成功阶段续跑；任何付费 provider 都必须通过服务端预算闸门。
- 统一任务状态不是统一硬件栈：AMD ROCm、Hygon DTK/HCU、CPU 容器各自维护独立镜像和锁定依赖。

## 接入顺序（不产生新增付费）

1. SCNet：只读查询真实 partition/account/GrpTRES/设备型号；无额度不提交。
2. ModelScope AMD：等待 Notebook 创建服务恢复；启动后只做只读验机和单个 Wan2.2 5B I2V 测试；不覆盖预装 PyTorch。
3. SCNet DCU：若型号/DTK匹配 cookbook，使用站点提供容器或对应 wheel，先单卡 Qwen-Image 2.1，再 Qwen3-TTS，最后 Qwen3-VL。
4. InstaBox：按需恢复现有免费机器，执行一项真实 CPU 任务，核验费用为0；不设置外部保活。
5. 通过统一任务协议把以上能力接到现有项目工作流；通过模型兼容测试后才标记为 production-ready。

## 主要公开资料

- AMD ROCm ComfyUI Instinct HTTP API tutorial: https://rocm.docs.amd.com/projects/ai-developer-hub/en/latest/notebooks/inference/t2v_comfyui_api_mode_instinct.html
- ModelScope MI300X + ComfyUI + Wan2.2 5B: https://www.modelscope.cn/posts/435985
- ModelScope MI300X + vLLM + Qwen3: https://www.modelscope.cn/posts/437379
- Hygon inference cookbook: https://github.com/HYGON-AI/inference-cookbook-das
- Hygon PyTorch DAS: https://github.com/HYGON-AI/pytorch-das
- Hygon Apex DAS: https://github.com/HYGON-AI/apex-das
- SCNet BW core node Slurm manual: https://www.scnet.cn/help/docs/mainsite/bw-core-node/system-introduction/index.html
- SCNet resource/account inspection: https://www.scnet.cn/help/docs/mainsite/hpc/cmd/basic-information/
- SCNet Slurm tips: https://www.scnet.cn/help/docs/mainsite/hpc/cmd/skills/index.html
- ComfyUI-Distributed API: https://github.com/robertvoy/ComfyUI-Distributed/blob/main/docs/comfyui-distributed-api.md
- OpenX Flow: https://github.com/OpenX-Inc/flow
- InstaBox concepts and plan limits: https://instabox.sh/docs/concepts
- InstaBox terms (free-plan anti-keepalive rules): https://instabox.sh/terms
[exit status 0]