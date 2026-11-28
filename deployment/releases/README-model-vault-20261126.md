# Model Vault integration patch (2026-11-26)

This release adds an authenticated `GET /api/models/catalog` endpoint and a portable model inventory module.

## Files
- `model_vault.py`: candidate catalog + safe local weight-file inventory.
- `server.py`: route addition; existing authentication middleware is preserved.
- `data/model-vault/README.md`: persistent vault usage notes.
- `tests/test_model_vault.py`: inventory and license-safety tests.
- `tests/test_prompt_builder.py`: aligns three stale assertions with current prompt-builder wording; generation behavior is unchanged.
- `deployment/releases/install_model_vault_patch.sh`: backup, deploy, test, restart and rollback script.

## Deploy
Extract this release, then run `sudo bash deployment/releases/install_model_vault_patch.sh`. Optional environment overrides: `MANGA_APP_DIR` and `MANGA_SERVICE`. The script backs up changed files, compiles Python, runs the test suite, restarts the service, checks health, and attempts rollback if deployment fails.

Set `MANGA_MODEL_VAULT` to a mounted persistent directory when a separate model disk/object-store mount is configured. The endpoint only catalogs files; it does not load models or run inference. Do not expose the vault directory directly through Nginx.

## Safety
- The route is covered by the existing authentication middleware (`X-Access-Key`).
- No secrets or model weights are included in this patch.
- Box is not a permanent network disk for the Tencent server. Use a persistent volume/object store or authenticated transfer for large weights.
- A GPU worker is still required for inference.
