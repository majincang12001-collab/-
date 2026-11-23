# Kaggle worker

Kaggle is an ephemeral GPU worker, not the durable project home. Export notebook versions and generated assets after each verified run. Keep credentials out of notebook output and Git history.

Current target experiment: SD 1.5-compatible anime checkpoint + IP-Adapter SD15. Validate package imports, free disk, per-GPU VRAM, model license, and adapter compatibility before downloading multi-GB weights.

## Notebook archive

`notebook57e8a33be4.ipynb` is an exported source snapshot from Kaggle. Execution outputs, counters, and transient cell metadata were stripped before backup. It preserves the cell source history, including earlier ComfyUI experiments and the later Diffusers/Tiny-SD tests. Some old cells are marked as raw or contain malformed draft fragments; treat this as an audit/archive snapshot, not a clean run-all notebook. The exported Kaggle metadata reported GPU disabled, so GPU availability must be re-verified in the live session before new experiments.
[exit status 0]

## Workbench batch generation
1. In the workbench, select a project, edit structured storyboard lines, and click **导出图片生成任务 JSON**.
2. Upload `manga-generation-jobs.json` and the chosen character reference image renamed to `character_reference.png` into `/kaggle/working`.
3. Run `batch_generate_from_jobs.py` in the existing Kaggle GPU session. It uses the cached AnythingV5 fp16 and IP-Adapter SD15 Light weights, then writes 512×512 keyframes and `manifest.json` under `/kaggle/working/manga_keyframes`.
4. Download the generated folder and upload the keyframes to the matching workbench project.

This is a manual handoff, not an automatic cloud queue. It requires the free Kaggle session to be active and model caches to exist. Review prompts and output before using them in a finished episode.
