# AI Manga Production Stack (free-first)

## Integration choices
- Existing workbench: CPU control plane for projects, uploads, story, characters, storyboard metadata and job queue.
- ComfyUI (GPL-3.0): image/video workflow engine, called through its local API and saved JSON workflows. Keep optional partner/API nodes disabled; never expose its port publicly.
- Tencent IP-Adapter: reference-image conditioning for character likeness. Test an SD1.5-compatible adapter on T4 first; identity consistency is not guaranteed. Verify each model checkpoint's own license.
- AnimateDiff (Apache-2.0): first lightweight motion experiment for SD1.5, after still-image reference tests pass.
- adachi0130/comic-pipeline (GPL-3.0): recent outline-to-panels-to-export MVP; study human-review stages, do not copy code unless GPL obligations are accepted.
- tccnnd/Comic-drama (MIT): early prototype describing script parsing, storyboard review, ComfyUI routing, TTS and timeline export; architecture reference only until tested.
- FFmpeg: final image/audio/subtitle assembly on worker, not the small control server.

## Deployment split
- Box main CPU: login, projects, uploads, metadata, job queue, result delivery.
- Kaggle T4 x2: ephemeral GPU worker; load pinned workflow/model, generate batch, save outputs and return artifacts. Do not assume always-on service.
- Mobile browser: control plane only; no inference on phone.

## Guardrails
- No paid compute or paid API calls without explicit approval.
- Pin model and package versions per tested workflow. Track model licenses separately from code licenses.
- Temporary reverse tunnels are temporary, not permanent hosting.
- A stage is complete only after its output file and metadata are verified.

## Current implemented prototype (2026-11-23)
- Project-scoped production metadata API: GET/POST /api/production stores story text, character records and storyboard draft rows as JSON under data/production/.
- Workbench has a basic paragraph-to-shot splitter for manual review. It is deterministic text segmentation, not an LLM screenplay writer; do not label it AI script generation.
- Browser workflow exercised end-to-end: select project, paste sample, split into two draft shots, save successfully. Temporary test content was cleared afterwards.


## 2026-11-23：IP-Adapter 首轮实测
- Kaggle 免费会话检测到 CUDA=True、2×Tesla T4；工作盘当时剩余 12.11GB。
- 本地缓存已包含 `Yntec/AnythingV5` SD1.5 fp16 Diffusers 模型和 `h94/IP-Adapter` 的 SD15 Light 权重及 image encoder。
- `variant="fp16"` 加载底模成功；禁用 attention slicing 后，`load_ip_adapter` 成功。
- 同一参考图已生成正面、侧面、全身三张 512×512 测试图及对照图。初步链路通过，但身份一致性仍需改进，尚不能称为生产级角色锁定。
- 可复用脚本：`kaggle/ipadapter_character_test.py`；清理输出后的实验 Notebook：`kaggle/notebook57e8a33be4.ipynb`。


## 2026-11-24：真实关键帧接入 MP4
- 在 Kaggle 免费 2×Tesla T4 会话中，使用缓存的 AnythingV5 fp16 + IP-Adapter SD15 Light 和主角参考图，单张生成 `shot_0001.png`（512×512，约 5.2 秒）。
- 该真实关键帧归档到 `AI漫剧首次出图测试` 项目，并命名为 `shot_0001.png`。
- 工作台渲染器自动匹配该关键帧，生成 7.24 秒 MP4（2,659,078 bytes），包含普通话旁白和中文字幕；抽帧已确认画面来自真实动漫图，而非占位卡。
- 批量 worker 脚本仍待完整执行验证；当前不是 AI 视频模型动作生成。


## 2026-11-24：项目控制台与普通话语音工作进程
- 每部漫剧使用独立 `data/project_workspaces/<project_id>/` 目录，按 story / characters / references / episodes / audio / exports / logs 归档。每集制作资料独立保存到 `data/production/<project_id>/episode-NN.json`。
- 项目总览、分集切换、文件夹视图、按项目过滤的平台调用统计已接入控制台。第三方平台剩余额度不伪造；未接授权接口时显示未连接或手动登记上限。
- 分镜可勾选3—5个镜头提交关键帧任务；真实AI图生视频阶段单独预留，不把静态图动态剪辑标成AI视频。
- 普通话 TTS 首选 Qwen3-TTS 12Hz 0.6B CustomVoice（Apache-2.0），使用免费 Kaggle GPU Worker；控制台提供音色选择、试听任务、状态轮询与WAV回传。
- API 集成已测试 TTS 队列、Worker领取、WAV上传与回放；真实模型权重下载、T4推理速度和听感验收尚未完成。


## 2026-11-25：角色一致性与连续性方案调研

### 参考项目（学习设计，不直接整仓搬入）
- OpenManga（Apache-2.0）：角色圣经、别名、服装版本（draft/approved/locked）、角色参考图上传/生成、场景/道具版本、角色状态时间线、章节连续性检查。适合借鉴角色资产与故事状态模型。https://github.com/pr0h0/OpenManga
- Comic Pipeline（GPL-3.0-only）：人工审核关卡、逐镜正负提示词/尺寸/种子/LoRA参数、候选图并排比较、采纳后生成新版本、下游失效传播。只借鉴流程；若复制代码须评估GPL传染义务。https://github.com/adachi0130/comic-pipeline
- AID Studio（MIT）：国内团队的AI漫剧/动态漫画端到端工作流，公开Gitee与GitHub源码入口；代码体量大，先审查依赖、模型与服务配置，不整仓替换当前轻量控制台。https://github.com/Dohaluong/aid
- AI-Comic-Generator（MIT未明确，仓库未展示LICENSE文件）：JSON驱动故事、全局画风配置、角色工坊、分镜编辑和一致性检查；可参考数据组织，复用代码前先确认许可证。https://github.com/Dapeng960208/AI-Comic-Generator
- ComfyUI-ZFRNodes：角色/道具多视图资产表生成、同角色参考图串联多镜头；作为后续ComfyUI工作流候选，需核验节点依赖和模型许可。https://github.com/zfrsgtcu/ComfyUI-ZFRNodes

### 一致性技术路线
1. 立即可用：每个角色独立文字设定、独立参考图、镜头显式绑定角色；无参考图的配角只走文字条件，严禁误用主角参考图。
2. 中期：每个角色整理20—40张经人工筛选的多角度/表情/服装样本，训练角色LoRA；单张参考图/IP-Adapter只作为辅助条件。
3. 镜头控制：把景别、主体占画幅比例、取景边界（如中景腰部以上）、相机角度、人物位置、场景锚点拆成独立字段；关键镜头优先采用姿势/深度/线稿控制，而不是只堆文字提示词。
4. 质检：按角色身份、服装、景别、场景/道具、动作、相邻镜头状态逐项评分；低于阈值只重绘单镜头，不整集重跑；保留候选图和版本历史。
5. 技术兼容提醒：PuLID/InstantID的具体权重通常绑定底模架构，不能把SDXL方案直接接到当前AnythingV5 SD1.5；需要选SD1.5兼容FaceID路线，或另开SDXL实验分支。

### 本轮已落地
- 分镜编辑器增加角色资产库：可登记角色文字设定、上传角色独立参考图、保存到项目。
- 每个镜头可指定出场角色；任务队列按角色名匹配独立参考图。
- Worker按角色下载对应参考图；生成器按镜头切换参考图。
- 没有参考图的配角自动使用文字条件，禁用主角IP-Adapter参考，避免串脸。
- 中景强制腰部以上取景，并在负面提示词中排除全身、膝盖、脚部。
