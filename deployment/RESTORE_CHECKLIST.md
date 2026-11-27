# Restore acceptance checklist
- [ ] Extract archive and inspect SHA256.
- [ ] Create fresh access/worker secrets (do not reuse leaked credentials).
- [ ] Start control plane and verify health endpoint.
- [ ] Confirm project list, production JSON, uploaded reference images and render assets.
- [ ] Mount private model bundle and verify every SHA256 from manifest.
- [ ] Start one GPU worker on the selected provider; verify auth and heartbeat.
- [ ] Run one 896x512 landscape smoke image; verify dimensions and result upload.
- [ ] Run tests before opening production queue.
