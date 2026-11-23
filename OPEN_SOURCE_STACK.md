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