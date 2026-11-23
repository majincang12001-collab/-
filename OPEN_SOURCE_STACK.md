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
[exit status 0]

## 2026-11-24：结构化分镜与批量出图任务交接
- 工作台分镜文本改为五栏结构：景别、画面内容、人物动作/表情、对白/旁白、图片提示词；保存时拆成独立 JSON 字段，并支持重新加载。
- 增加“导出图片生成任务 JSON”，可导出角色资料与分镜任务清单。
- 新增 `kaggle/batch_generate_from_jobs.py`，复用缓存 AnythingV5 fp16 + IP-Adapter SD15 Light，逐镜生成 512×512 关键帧并写出 manifest。
- 通过临时测试资料验证结构化分镜 POST 保存和 GET 重新读取成功；测试资料已恢复，没有覆盖正式项目。
- 这仍是 Kaggle 手动交接，不是工作台自动排队执行；未完成 AI 自动编剧、智能语义分镜、视频生成、配音与 MP4 合成。
