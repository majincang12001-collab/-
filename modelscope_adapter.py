"""Portable ModelScope connector primitives for the manga workbench.

This module deliberately separates the ModelScope model hub from hosted
Studios. It never starts an inference job or downloads weights implicitly.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path

SECRET_FILE = Path(os.environ.get('MANGA_PROVIDER_SECRET_FILE', '/var/lib/manga-console/provider-keys.json'))
REPO_ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,120}$')


def _secrets() -> dict:
    try:
        value = json.loads(SECRET_FILE.read_text('utf-8'))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def status() -> dict:
    row = _secrets().get('modelscope-hub', {})
    token = str(row.get('access_token') or os.environ.get('MODELSCOPE_TOKEN') or '').strip()
    return {
        'provider_id': 'modelscope-hub',
        'configured': bool(token),
        'auth_mode': 'token' if token else 'public_or_token_pending',
        'token_value_exposed': False,
        'download_enabled': False,
        'note': '公开模型卡可直接浏览；私有或受限仓库需配置Token。下载动作仍须单独授权。'
    }


def validate_repo_id(repo_id: str) -> str:
    value = str(repo_id or '').strip()
    if not REPO_ID.fullmatch(value) or any(part in ('.', '..') for part in value.split('/')):
        raise ValueError('ModelScope repo_id格式无效，应为组织名/模型名')
    return value


def snapshot_download(repo_id: str, cache_dir: str | Path, *, allow_download: bool = False):
    """Explicit opt-in download; importing this module never downloads anything."""
    repo_id = validate_repo_id(repo_id)
    if not allow_download:
        raise PermissionError('模型下载默认关闭；必须显式确认下载后调用')
    try:
        from modelscope import snapshot_download as download
    except ImportError as exc:
        raise RuntimeError('尚未安装ModelScope SDK；请先完成依赖预检') from exc
    row = _secrets().get('modelscope-hub', {})
    token = str(row.get('access_token') or os.environ.get('MODELSCOPE_TOKEN') or '').strip()
    kwargs = {'model_id': repo_id, 'cache_dir': str(Path(cache_dir).expanduser().resolve())}
    if token:
        kwargs['revision'] = 'master'
        os.environ.setdefault('MODELSCOPE_TOKEN', token)
    return download(**kwargs)
