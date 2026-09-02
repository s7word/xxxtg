"""Vault ``device_secret`` / Integrity 旁路存储（默认不注入 sendCode）。

官方 Android 的 Play Integrity 走 ``auth.requestFirebaseSms`` 的
``play_integrity_token``，绑定当次 ``play_integrity_nonce``。vault JSON 里的
``device_secret`` 是历史成功样本上的 attestation 块，**不能**当作 CodeSettings
字段，也没有公开的 InitConnection 槽位。

因此本模块只做三件事：
1. 扫描 lod_user 成功 JSON，记录是否存在 secret（不把原文写进指纹/日志）。
2. 可选把 secret 落到 ``data/vault_attestation.json``（gitignore），供日后对照。
3. 若显式打开 ``inject_vault_device_secret``，把 secret 挂到 profile 供
   FirebaseSms 路径尝试注入——默认关闭，因为 nonce 几乎必然不匹配。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.config import DATA_DIR, LOD_USER_DIR

logger = logging.getLogger("VaultAttestationStore")

SIDECAR_FILE = DATA_DIR / "vault_attestation.json"
SIDECAR_TMP = DATA_DIR / "vault_attestation.json.tmp"


def _preview_len(value: Any) -> int:
    if value is None:
        return 0
    return len(str(value))


def scan_vault_attestation(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """只读扫描：返回元数据，绝不包含 secret/token 原文。"""
    rows: List[Dict[str, Any]] = []
    base = Path(root) if root is not None else Path(LOD_USER_DIR)
    if not base.exists():
        return rows
    for path in sorted(base.rglob("91*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            app_id = int(data.get("app_id") or data.get("api_id") or 0)
        except (TypeError, ValueError):
            continue
        if app_id != 4:
            continue
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            rel = path.name
        rows.append({
            "file": rel,
            "has_device_secret": bool(data.get("device_secret")),
            "device_secret_len": _preview_len(data.get("device_secret")),
            "has_device_token": bool(data.get("device_token")),
            "device_token_len": _preview_len(data.get("device_token")),
        })
    return rows


def _read_secret_from_source(rel: str, root: Optional[Path] = None) -> Optional[str]:
    base = Path(root) if root is not None else Path(LOD_USER_DIR)
    path = (base / rel).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    secret = data.get("device_secret")
    text = str(secret or "").strip()
    return text or None


def persist_attestation_index(
    root: Optional[Path] = None,
    *,
    copy_secrets: bool = False,
) -> Dict[str, Any]:
    """把扫描结果写入 sidecar。默认不落 secret 原文。"""
    meta = scan_vault_attestation(root)
    items: List[Dict[str, Any]] = []
    for row in meta:
        item = dict(row)
        if copy_secrets:
            secret = _read_secret_from_source(row["file"], root=root)
            if secret:
                item["device_secret"] = secret
        items.append(item)
    payload = {
        "copy_secrets": bool(copy_secrets),
        "count": len(items),
        "with_secret": sum(1 for item in items if item.get("has_device_secret")),
        "items": items,
    }
    SIDECAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR_TMP.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    SIDECAR_TMP.replace(SIDECAR_FILE)
    return {
        "path": str(SIDECAR_FILE),
        "count": payload["count"],
        "with_secret": payload["with_secret"],
        "copy_secrets": payload["copy_secrets"],
    }


def attach_attestation_metadata(
    profile: Dict[str, Any],
    config: Any = None,
    *,
    source_file: Optional[str] = None,
) -> Dict[str, Any]:
    """给 profile 打上 has_device_secret 等元数据；按开关可选注入原文。"""
    resolved = dict(profile)
    rows = scan_vault_attestation()
    match = None
    if source_file:
        for row in rows:
            if row.get("file") == source_file:
                match = row
                break
    if match is None and rows:
        match = rows[0]
    if not match:
        resolved["has_device_secret"] = False
        resolved["device_secret_len"] = 0
        resolved["vault_attestation_source"] = None
        return resolved

    resolved["has_device_secret"] = bool(match.get("has_device_secret"))
    resolved["device_secret_len"] = int(match.get("device_secret_len") or 0)
    resolved["has_device_token"] = bool(match.get("has_device_token"))
    resolved["device_token_len"] = int(match.get("device_token_len") or 0)
    resolved["vault_attestation_source"] = match.get("file")

    persist = bool(getattr(config, "vault_attestation_persist_secrets", False)) if config is not None else False
    inject = bool(getattr(config, "inject_vault_device_secret", False)) if config is not None else False
    if persist:
        try:
            persist_attestation_index(copy_secrets=True)
        except Exception as exc:
            logger.warning("写入 vault attestation sidecar 失败: %s", exc)
    if inject and match.get("file"):
        secret = _read_secret_from_source(str(match["file"]))
        if secret:
            resolved["device_secret"] = secret
            resolved["device_secret_injected"] = True
        else:
            resolved["device_secret_injected"] = False
    else:
        resolved["device_secret_injected"] = False
    return resolved


def take_injected_device_secret(profile: Optional[Dict[str, Any]]) -> Optional[str]:
    """取出注入的 secret；调用方禁止把返回值写入任务日志。"""
    profile = profile or {}
    if not profile.get("device_secret_injected"):
        return None
    text = str(profile.get("device_secret") or "").strip()
    return text or None
