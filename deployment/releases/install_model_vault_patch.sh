#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MANGA_APP_DIR:-/opt/manga-console}"
PATCH_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE="${MANGA_SERVICE:-manga-console}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${APP_DIR}/backups/model-vault-${STAMP}"

if [[ ! -d "$APP_DIR" || ! -f "$APP_DIR/server.py" ]]; then
  echo "ERROR: application directory not found: $APP_DIR" >&2; exit 2
fi
if [[ ! -f "$PATCH_DIR/model_vault.py" || ! -f "$PATCH_DIR/server.py" ]]; then
  echo "ERROR: run this script from the extracted release tree" >&2; exit 2
fi

mkdir -p "$BACKUP_DIR"
cp -a "$APP_DIR/server.py" "$BACKUP_DIR/server.py"
[[ ! -f "$APP_DIR/model_vault.py" ]] || cp -a "$APP_DIR/model_vault.py" "$BACKUP_DIR/model_vault.py"
[[ ! -d "$APP_DIR/tests" ]] || cp -a "$APP_DIR/tests" "$BACKUP_DIR/tests"

rollback() {
  echo "Deployment failed; restoring previous application files..." >&2
  cp -a "$BACKUP_DIR/server.py" "$APP_DIR/server.py"
  if [[ -f "$BACKUP_DIR/model_vault.py" ]]; then cp -a "$BACKUP_DIR/model_vault.py" "$APP_DIR/model_vault.py"; else rm -f "$APP_DIR/model_vault.py"; fi
  if [[ -d "$BACKUP_DIR/tests" ]]; then rm -rf "$APP_DIR/tests"; cp -a "$BACKUP_DIR/tests" "$APP_DIR/tests"; fi
  systemctl restart "$SERVICE" || true
}
trap rollback ERR

install -m 0644 "$PATCH_DIR/server.py" "$APP_DIR/server.py"
install -m 0644 "$PATCH_DIR/model_vault.py" "$APP_DIR/model_vault.py"
mkdir -p "$APP_DIR/tests" "$APP_DIR/data/model-vault"
install -m 0644 "$PATCH_DIR/tests/test_model_vault.py" "$APP_DIR/tests/test_model_vault.py"
install -m 0644 "$PATCH_DIR/tests/test_prompt_builder.py" "$APP_DIR/tests/test_prompt_builder.py"
install -m 0644 "$PATCH_DIR/data/model-vault/README.md" "$APP_DIR/data/model-vault/README.md"

cd "$APP_DIR"
PY="${APP_DIR}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
"$PY" -m py_compile server.py model_vault.py
"$PY" -m unittest discover -s tests -v
systemctl restart "$SERVICE"
for _ in $(seq 1 25); do
  if curl -fsS --max-time 2 http://127.0.0.1:8766/api/health >/dev/null; then break; fi
  sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:8766/api/health >/dev/null
trap - ERR
echo "PATCH_DEPLOYED"
echo "Backup: $BACKUP_DIR"
echo "Next: authenticate GET /api/models/catalog using the workbench client; do not expose model-vault as a public static directory."
