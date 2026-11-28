"""Instabox control-plane adapter for the manga console.

Stores only a restricted Instabox API key in the existing 0600 provider vault.
Never returns credentials or raw provider error bodies to the browser.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.instabox.sh/v1"
PROVIDER_ID = "instabox-worker"


class InstaboxError(RuntimeError):
    pass


def _api_key(secret_file: Path) -> str:
    try:
        rows = json.loads(Path(secret_file).read_text("utf-8"))
        key = str(rows.get(PROVIDER_ID, {}).get("api_key", "")).strip()
    except Exception:
        key = ""
    if not key:
        raise InstaboxError("Instabox API Key 尚未配置")
    if not key.startswith("ibk_"):
        raise InstaboxError("Instabox API Key 格式无效")
    return key


def request(secret_file: Path, path: str, method: str = "GET", payload=None):
    key = _api_key(secret_file)
    if not path.startswith("/") or ".." in path:
        raise InstaboxError("Instabox API 路径无效")
    headers = {"X-API-Key": key, "Accept": "application/json"}
    if method in ("GET", "POST", "PATCH", "DELETE"):
        # Idempotency-Key is required by Instabox for list/lifecycle operations.
        import uuid
        headers["Idempotency-Key"] = "manga-console-" + uuid.uuid4().hex
    body = None
    if payload is not None:
        import json as _json
        body = _json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read(2 * 1024 * 1024)
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        # Do not echo provider response bodies: they may contain account details.
        code = exc.code
        if code == 401:
            raise InstaboxError("Instabox API Key 无效或已过期") from None
        if code == 403:
            raise InstaboxError("Instabox API Key 权限不足") from None
        if code == 404:
            raise InstaboxError("Instabox 资源不存在") from None
        raise InstaboxError(f"Instabox API 返回 HTTP {code}") from None
    except Exception as exc:
        raise InstaboxError("Instabox API 暂时无法连接：" + type(exc).__name__) from None


def status(secret_file: Path):
    machines_data = request(secret_file, "/machines")
    usage = request(secret_file, "/usage")
    machines = machines_data.get("machines", machines_data if isinstance(machines_data, list) else [])
    if isinstance(machines, dict):
        machines = machines.get("items", [])
    safe_machines = []
    for row in machines:
        if not isinstance(row, dict):
            continue
        spec = row.get("spec") or {}
        safe_machines.append({
            "id": row.get("id"), "name": row.get("name"), "state": row.get("state"),
            "created_at": row.get("createdAt"), "updated_at": row.get("updatedAt"),
            "vcpus": spec.get("vcpus"), "memory_gib": round((spec.get("memMiB") or 0) / 1024, 1),
            "disk_gib": spec.get("persistentDiskGiB"), "max_run_minutes": row.get("maxRunMinutes"),
        })
    cost = usage.get("cost") or {}
    limits = usage.get("limits") or {}
    return {
        "configured": True, "provider": "Instabox", "machines": safe_machines,
        "usage": {
            "period_start": cost.get("periodStart"), "period_end": cost.get("periodEnd"),
            "free_sandbox_hours": cost.get("freeSandboxHours"),
            "running": cost.get("running"), "cost_usd": cost.get("costUSD"),
            "overage_usd": cost.get("overageUSD"), "remaining_usd": cost.get("remainingUSD"),
            "snapshots": usage.get("snapshots"), "stored_bytes": usage.get("storedBytes"),
            "allocated_disk_gib": usage.get("allocatedDiskGiB"),
            "max_machines": limits.get("maxMachines"), "max_snapshots": limits.get("maxSnapshots"),
            "max_stored_gib": limits.get("maxStoredGiB"),
        },
    }


def machine_action(secret_file: Path, machine_id: str, action: str):
    if not isinstance(machine_id, str) or not machine_id.startswith("m_") or len(machine_id) > 80:
        raise InstaboxError("机器编号无效")
    if action not in ("resume", "suspend"):
        raise InstaboxError("只允许启动或暂停机器")
    result = request(secret_file, f"/machines/{machine_id}/{action}", method="POST", payload={})
    return {"ok": True, "action": action, "operation": result}
