"""Remote ComfyUI worker client for the Box control plane.

The Box never imports torch or loads model weights. It talks to a remote
ComfyUI HTTP API. Configure MANGA_GPU_BASE_URL and optional MANGA_GPU_API_KEY
in the Box service environment; never put credentials in browser code.
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request

class GPUWorkerError(RuntimeError):
    pass

class ComfyUIWorker:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 8.0):
        self.base_url = (base_url or os.environ.get("MANGA_GPU_BASE_URL", "")).strip().rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("MANGA_GPU_API_KEY", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _request(self, path: str, payload: dict | None = None):
        if not self.configured:
            raise GPUWorkerError("云端 GPU 尚未配置：缺少 MANGA_GPU_BASE_URL")
        url = self.base_url + path
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(8 * 1024 * 1024)
                return json.loads(raw) if raw else {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GPUWorkerError(f"GPU worker 请求失败：{type(exc).__name__}: {exc}") from exc

    def health(self) -> dict:
        if not self.configured:
            return {"configured": False, "online": False, "message": "尚未配置云端 GPU 地址"}
        stats = self._request("/system_stats")
        queue = self._request("/queue")
        devices = stats.get("devices", []) if isinstance(stats, dict) else []
        return {"configured": True, "online": True, "devices": devices,
                "queue_running": len(queue.get("queue_running", [])),
                "queue_pending": len(queue.get("queue_pending", []))}

    def submit(self, workflow: dict, client_id: str = "manga-console") -> dict:
        if not isinstance(workflow, dict) or not workflow:
            raise GPUWorkerError("ComfyUI workflow 必须是非空 JSON 对象")
        result = self._request("/prompt", {"prompt": workflow, "client_id": client_id})
        if not result.get("prompt_id"):
            raise GPUWorkerError(f"GPU worker 未返回 prompt_id：{result}")
        return result

    def history(self, prompt_id: str) -> dict:
        if not prompt_id or any(c not in "0123456789abcdef-" for c in prompt_id.lower()):
            raise GPUWorkerError("prompt_id 格式无效")
        return self._request("/history/" + urllib.parse.quote(prompt_id, safe=""))
