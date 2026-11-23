# Kaggle worker

Kaggle is an ephemeral GPU worker, not the durable project home. Export notebook versions and generated assets after each verified run. Keep credentials out of notebook output and Git history.

Current target experiment: SD 1.5-compatible anime checkpoint + IP-Adapter SD15. Validate package imports, free disk, per-GPU VRAM, model license, and adapter compatibility before downloading multi-GB weights.

## Notebook archive

`notebook57e8a33be4.ipynb` is an exported source snapshot from Kaggle. Execution outputs, counters, and transient cell metadata were stripped before backup. It preserves the cell source history, including earlier ComfyUI experiments and the later Diffusers/Tiny-SD tests. Some old cells are marked as raw or contain malformed draft fragments; treat this as an audit/archive snapshot, not a clean run-all notebook. The exported Kaggle metadata reported GPU disabled, so GPU availability must be re-verified in the live session before new experiments.
[exit status 0]