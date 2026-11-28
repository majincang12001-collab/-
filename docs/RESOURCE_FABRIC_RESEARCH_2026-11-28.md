# AI 漫剧资源统一调度研究（2026-11-28）

## 结论

不要把所有资源伪装成同一种 GPU。采用“统一任务协议 + 能力标签 + 独立执行器 + 服务端密钥库 + 成本闸门”的资源编织层（Resource Fabric）。控制台负责项目、任务、状态和素材；计算资源只负责领取自己能够执行的任务。资源离线时任务留队列，不静默转到付费服务。

## 资源能力矩阵

| 资源 | 适合承担 | 不应假定 | 当前接入状态/动作 |
|---|---|---|---|
| 腾讯云轻量主机（2C/2G） | 控制台、任务元数据、调度、鉴权、状态、轻量 API | 不跑视频扩散模型、不当素材长期仓库 | 已运行；继续只做控制面 |
| DeepSeek API | 故事、对白、分镜文本、提示词优化 | 不提供 GPU，也不自动生成视频 | 已有兼容 API 路由；核对模型和额度后作为文本节点 |
| NVIDIA API Catalog/NIM | 文本/VLM/支持的语音模型 | 不等于租用 NVIDIA GPU；不能直接跑本地 ComfyUI | Key 已有保存路径；保持服务端调用与费用闸门 |
| MiniMax 视频 API | 生成短视频镜头、图生视频/参考驱动能力（以当前模型接口为准） | 不负责全片剪辑、角色数据库或任务编排 | 已有真实异步任务链路；第一镜已下载，第二镜只确认远端 succeeded，需单独核实下载 |
| ModelScope 模型仓库 | 模型/数据集发现、版本、下载 | 仓库不是推理算力 | 有独立 adapter；下载默认关闭，需核验许可/体积/运行时 |
| ModelScope Notebook CPU | 文本预处理、批量元数据、字幕/提示词整理、轻量 CPU 任务 | 不适合常驻 Worker；Notebook 生命周期会回收 | 账号页面曾显示实例空、创建按钮禁用；未证明可用，需先解决创建权限/资源问题 |
| ModelScope Notebook GPU | 小规模图像/视频实验、模型兼容性试验 | 免费额度不等于稳定生产 SLA | 只在明确额度和可用实例确认后启用 |
| SCNet 华中一区 BW DCU | 大规模图像生成实验、模型推理；兼容后可试视频模型 | 不可直接运行 CUDA 专用依赖；不能仅凭 hy-smi 识别就判定可用 | 已有 Slurm 单卡预检脚本和心跳 API；尚未提交作业、尚未实测出图 |
| Instabox CPU Worker | FFmpeg、字幕、音频拼接、素材校验、打包等 CPU 工序 | 不应默认启动/续费付费机器 | 有 API adapter；先读取 usage/machines，只有已存在可用实例才分派任务 |
| Colab（备用） | 临时验证小模型/工作流 | 免费 GPU 稳定性和在线时间 | 保留备用，不作为主生产调度器 |
| Kaggle | 不纳入当前生产主链路 | 不再自动派发新生产任务 | 旧 Worker 代码仅为迁移/历史兼容，状态应标记为停用 |
| 本地 ComfyUI | 有用户自有 GPU 时的可选节点 | 当前用户不以本地电脑作为生产前提 | 适配接口保留，不作为当前资源假设 |

## 开源项目评估

### 1. ComfyUI + 官方工作流模板：生成执行层首选

- ComfyUI 提供可复用的节点图与 HTTP 队列接口，适合把“镜头”编译成可执行工作流。
- 官方模板仓库存在 Wan 2.2 14B FLF2V（首帧+尾帧）工作流，可作为镜头连续性的目标工作流。
- 但 ComfyUI 的常规安装说明主要按 NVIDIA/AMD 等通用后端给出；海光 DCU 需要单独验证 DTK、PyTorch、算子、自定义节点和模型精度。不能把 JSON 工作流存在仓库就称为 DCU 已支持。

### 2. FlixML Studio：值得借鉴，不建议整体替换

- 值得借鉴：项目/场景/镜头数据结构、GPU 节点 roles（image/video）、按角色过滤任务、统一 REST API、任务耗时统计、角色资产库、API key 分级权限。
- 不建议直接部署：它要求 PostgreSQL、Node 前端和 ComfyUI 节点；当前 2C/2G 控制面不宜增加重型服务。其 RunPod 适配默认可能引入付费路径，必须删掉/禁用。
- 采用“借鉴接口与路由思想”，不复制其整套服务。

### 3. OpenMontage：借鉴制作编排与后期，不整体并入

- 值得借鉴：从脚本到素材到剪辑的多阶段 pipeline、成本预估、阶段状态板、FFmpeg/Remotion 等后期编排。
- 风险：AGPL-3.0；README 中示例依赖多家托管生成 API，部分案例明确有付费成本。若复制代码或合并部署，需要先做许可证审查，并移除默认付费供应商路径。
- 适合提炼流程思想，不适合作为当前项目的直接底座。

### 4. 海光 DCU 软件栈：可行但必须版本锁定

- 海光公开的 HYGON-AI/pytorch-das 提供 HCU 适配 PyTorch。其仓库列出 PyTorch 2.12.0+das.opt1.dtk2604 与 DTK 26.04 的配套，且要求主机已安装 HCU 驱动、DTK、Docker。
- Torch-FL/FlagOS 的 DCU 安装说明和兼容性资料提供另一条适配路线，但其中部分组合仍标记为实验或未覆盖 CI，必须锁定到与 SCNet 镜像匹配的版本。
- DTK 与驱动是第一组版本约束，PyTorch/推理框架与 DTK 是第二组版本约束。必须先采集节点实际驱动、DTK、Python、PyTorch、卡型号和显存，再选择镜像；不要在共享计算节点自行升级驱动。
- 第一个真实工作负载建议不是直接上 14B 视频模型，而是：设备枚举 → 小张量算子/矩阵乘法 → 单张 SD/SDXL 关键帧 → IP-Adapter/参考图 → 低分辨率短视频 → 最后再试 Wan 首尾帧视频。

## 统一任务协议（建议落地）

每个任务至少包含：

```json
{
  "job_id": "stable-id",
  "project_id": "project-id",
  "episode_id": "episode-id",
  "shot_id": "shot-id",
  "task_type": "keyframe|image_to_video|first_last_frame_video|tts|ffmpeg|subtitle|qc",
  "required_capabilities": ["dcu", "image_generation"],
  "inputs": [],
  "workflow_id": "versioned-workflow",
  "priority": 50,
  "max_cost": 0,
  "retry_policy": {"max_attempts": 2},
  "output_contract": {"format": "png|mp4|wav|srt"}
}
```

执行器只领取满足自身能力标签的任务，并回传：任务 ID、节点 ID、开始/结束时间、实际设备/模型版本、输出文件校验和、错误分类。领取必须原子化，失败重试不能重复扣费。

## 调度策略

1. 文本：DeepSeek 优先；NVIDIA NIM 作为可选备份。请求失败时只回退到同样已授权、额度可用的文本服务。
2. 图片关键帧：已验证的 DCU Worker 或 ModelScope 可用 GPU；没有可用免费/预付额度时暂停排队，不自动转付费 API。
3. 视频镜头：已授权 MiniMax API 或通过兼容验证的 DCU ComfyUI 工作流。每镜头单独记录 provider、model、task_id、远端状态、下载状态。
4. TTS/字幕/FFmpeg：优先 Instabox 已有可用 CPU 实例；其次 ModelScope CPU 临时任务。腾讯云主机只做轻量任务，避免挤占控制面。
5. QC：先做文件/时长/分辨率/音轨/字幕结构检查；角色一致性仍需图像相似度或人工复核，不能把文件存在当成视觉验收通过。
6. 任何付费服务在任务提交前必须检查 `allow_paid=false`（默认）和单任务预算；没有显式授权直接拒绝提交。

## DCU 首次验收门槛

- [ ] 确认 SCNet 实际卡型号（BW1000/BW 系列具体型号）、每卡显存、节点 OS/内核、驱动、DTK 版本。
- [ ] 单卡 Slurm 预检成功，记录卡型号/显存/驱动/DTK。
- [ ] Hygon PyTorch 对应 wheel 安装成功，`torch.cuda.is_available()`/设备枚举符合该镜像预期，并通过矩阵乘法与显存分配。
- [ ] ComfyUI 最小启动；仅启用经过验证的核心节点，不装来源不明的 CUDA 自定义节点。
- [ ] 生成一张 512/768 关键帧，下载并核验 PNG。
- [ ] 测试参考图/角色一致性节点。
- [ ] 测试 2–4 秒低分辨率视频；通过后才试 6 秒以上和首尾帧 Wan 工作流。
- [ ] 统计单张图/每秒视频的耗时与资源消耗，再决定 50 DCU 卡时如何分配。

## 当前阻塞与安全边界

- 当前本地工作副本有 SCNet 预检和 ModelScope adapter，但生产服务器目录中未发现 `scnet/`、`modelscope_adapter.py` 或 `deployment/providers.json`；本地文件存在不代表已部署。
- SCNet Slurm 预检模板不再硬编码未经核实的分区名；新增只读 `scnet/inspect_allocation.sh`，用于在登录节点查询 `whichpartition`、`sinfo`、`sacctmgr` 和存储。必须先确认账号实际 DCU 队列与额度，再用 `sbatch --partition=<verified>` 提交。
- SCNet 官方说明登录节点可联网、计算节点通常不能联网；Python wheel 和模型文件应先在登录节点/批准存储准备。未获得/验证 SCNet 作业入口和密钥前，不应假装已经启动算力。
- ModelScope Notebook 当前没有可用实例；CPU 不是 24/7 常驻 Worker。
- 用户已要求停止 Kaggle 生产使用。历史 Kaggle 代码不能继续作为默认路由。
- 不自动启动 RunPod/其他按量计费 GPU，不自动购买/下载大型模型，不在 GitHub、前端或 Notebook 输出中写 API Key。

## 参考资料

- Hygon PyTorch DAS: https://github.com/HYGON-AI/pytorch-das
- FlagOS Torch-FL Hygon DCU install: https://github.com/flagos-ai/Torch-FL/blob/main/docs/vendors/dcu/installation.md
- FlagOS compatibility: https://github.com/flagos-ai/Torch-FL/blob/main/docs/reference/compatibility.md
- ComfyUI Wan 2.2 FLF2V workflow: https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_flf2v.json
- ComfyUI installation reference: https://github.com/Comfy-Org/ComfyUI/blob/master/docs/installation.md
- FlixML Studio: https://github.com/ortegarod/flixml
- OpenMontage: https://github.com/edisplay/openmontage
- SCNet 华中一区 BW 节点资源说明: https://www.scnet.cn/help/docs/mainsite/bw-core-node/system-introduction/index.html
- ModelScope Notebook: https://www.modelscope.cn/docs/notebook/intro
[exit status 0]