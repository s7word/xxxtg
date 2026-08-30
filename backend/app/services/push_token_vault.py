"""REGHelp Push Token 本地库存：签发后落盘，未成功消耗的可按开关复用。

排序策略（开启复用时）：use_count 升序 → created_at 升序，即优先未使用，其次用过 1 次。
成功注册 / 已 setStatus 退款的令牌不再参与复用。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.config import DATA_DIR

logger = logging.getLogger("PushTokenVault")

VAULT_FILE = DATA_DIR / "push_token_vault.json"
VAULT_TMP = DATA_DIR / "push_token_vault.json.tmp"

STATUS_AVAILABLE = "available"
STATUS_CONSUMED = "consumed"
STATUS_REFUNDED = "refunded"
STATUS_RETIRED = "retired"

REUSE_PROVIDER = "reghelp_reuse"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token_preview(token: Optional[str]) -> str:
    raw = str(token or "")
    if len(raw) <= 12:
        return raw[:4] + "…" if raw else ""
    return f"{raw[:6]}…{raw[-4:]}"


class PushTokenVault:
    """进程内单例 + 文件持久化。"""

    _instance: Optional["PushTokenVault"] = None
    _instance_lock = threading.Lock()

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else VAULT_FILE
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self._load()

    @classmethod
    def get_instance(cls) -> "PushTokenVault":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = PushTokenVault()
        return cls._instance

    @classmethod
    def reset_for_tests(cls, path: Optional[Path] = None) -> "PushTokenVault":
        with cls._instance_lock:
            cls._instance = PushTokenVault(path=path)
            return cls._instance

    def _load(self) -> None:
        try:
            if not self.path.exists():
                self._items = []
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else data
            self._items = [dict(row) for row in (items or []) if isinstance(row, dict)]
        except Exception as exc:
            logger.warning("读取 Push Token 库存失败，将使用空库存: %s", exc)
            self._items = []

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _utc_now(),
            "items": self._items,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def store_issued(
        self,
        *,
        token: str,
        reghelp_task_id: Optional[str],
        provider: str = "reghelp",
        app_name: Optional[str] = None,
        app_device: Optional[str] = None,
        app_type: Optional[str] = None,
        source_task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """REGHelp 新签发成功后入库；同 reghelp_task_id 幂等更新。"""
        token = str(token or "").strip()
        if not token:
            raise ValueError("empty push token")
        with self._lock:
            existing = None
            if reghelp_task_id:
                for row in self._items:
                    if row.get("reghelp_task_id") == reghelp_task_id:
                        existing = row
                        break
            now = _utc_now()
            if existing:
                existing["token"] = token
                existing["provider"] = provider or existing.get("provider") or "reghelp"
                existing["app_name"] = app_name or existing.get("app_name")
                existing["app_device"] = app_device or existing.get("app_device")
                existing["app_type"] = app_type or existing.get("app_type")
                existing["updated_at"] = now
                if existing.get("status") == STATUS_REFUNDED:
                    pass
                elif existing.get("status") != STATUS_CONSUMED:
                    existing["status"] = STATUS_AVAILABLE
                self._save_unlocked()
                return dict(existing)

            row = {
                "id": uuid.uuid4().hex[:12],
                "token": token,
                "token_preview": _token_preview(token),
                "reghelp_task_id": reghelp_task_id,
                "provider": provider or "reghelp",
                "app_name": app_name,
                "app_device": app_device,
                "app_type": app_type,
                "source_task_id": source_task_id,
                "use_count": 0,
                "status": STATUS_AVAILABLE,
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
                "last_outcome": None,
                "last_lease_task_id": None,
            }
            self._items.append(row)
            self._save_unlocked()
            return dict(row)

    def acquire_for_reuse(
        self,
        *,
        max_uses: int = 2,
        app_type: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 use_count 升序取可用令牌；命中后 use_count+1。"""
        limit = max(1, int(max_uses or 1))
        with self._lock:
            candidates = []
            for row in self._items:
                if row.get("status") != STATUS_AVAILABLE:
                    continue
                if int(row.get("use_count") or 0) >= limit:
                    continue
                if app_type and row.get("app_type") and row.get("app_type") != app_type:
                    continue
                if not row.get("token"):
                    continue
                candidates.append(row)
            if not candidates:
                return None
            candidates.sort(
                key=lambda r: (
                    int(r.get("use_count") or 0),
                    str(r.get("created_at") or ""),
                )
            )
            picked = candidates[0]
            picked["use_count"] = int(picked.get("use_count") or 0) + 1
            picked["last_used_at"] = _utc_now()
            picked["updated_at"] = picked["last_used_at"]
            picked["last_lease_task_id"] = lease_task_id
            picked["last_outcome"] = "leased"
            self._save_unlocked()
            return dict(picked)

    def mark_attempt(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """新签发令牌在本任务首次 auth.sendCode 前调用：把 use_count 从 0 记到 1。"""
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if int(row.get("use_count") or 0) <= 0:
                row["use_count"] = 1
            row["last_used_at"] = _utc_now()
            row["updated_at"] = row["last_used_at"]
            if lease_task_id:
                row["last_lease_task_id"] = lease_task_id
            row["last_outcome"] = "attempted"
            self._save_unlocked()
            return dict(row)

    def mark_success(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            row["status"] = STATUS_CONSUMED
            row["last_outcome"] = "success"
            row["updated_at"] = _utc_now()
            self._save_unlocked()
            return dict(row)

    def mark_refunded(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            row["status"] = STATUS_REFUNDED
            row["last_outcome"] = "refunded"
            row["updated_at"] = _utc_now()
            self._save_unlocked()
            return dict(row)

    def mark_retired(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """主动作废：猎号轮换设备/窗口后不希望立刻把同一枚 Token 租回来。

        已 consumed / refunded 的行保持原状态，其余一律置为 retired，退出复用候选。
        """
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if row.get("status") not in {STATUS_REFUNDED, STATUS_CONSUMED}:
                row["status"] = STATUS_RETIRED
            row["last_outcome"] = reason or "retired"
            row["updated_at"] = _utc_now()
            self._save_unlocked()
            return dict(row)

    def mark_failed_keep(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """失败且未退款：保留为 available，供二次利用。"""
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if row.get("status") not in {STATUS_REFUNDED, STATUS_CONSUMED}:
                row["status"] = STATUS_AVAILABLE
            row["last_outcome"] = reason or "failed"
            row["updated_at"] = _utc_now()
            self._save_unlocked()
            return dict(row)

    def _find_unlocked(
        self,
        vault_id: Optional[str],
        reghelp_task_id: Optional[str],
        token: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if vault_id:
            for row in self._items:
                if row.get("id") == vault_id:
                    return row
        if reghelp_task_id:
            for row in self._items:
                if row.get("reghelp_task_id") == reghelp_task_id:
                    return row
        if token:
            for row in self._items:
                if row.get("token") == token:
                    return row
        return None

    def list_items(self, *, include_token: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            rows = []
            for row in self._items:
                view = dict(row)
                view["token_preview"] = view.get("token_preview") or _token_preview(view.get("token"))
                if not include_token:
                    view.pop("token", None)
                rows.append(view)
            rows.sort(key=lambda r: (str(r.get("created_at") or ""),), reverse=True)
            return rows

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._items)
            available = 0
            unused = 0
            used_once = 0
            reusable = 0
            consumed = 0
            refunded = 0
            retired = 0
            for row in self._items:
                status = row.get("status")
                uses = int(row.get("use_count") or 0)
                if status == STATUS_AVAILABLE:
                    available += 1
                    if uses == 0:
                        unused += 1
                    elif uses == 1:
                        used_once += 1
                    reusable += 1
                elif status == STATUS_CONSUMED:
                    consumed += 1
                elif status == STATUS_REFUNDED:
                    refunded += 1
                elif status == STATUS_RETIRED:
                    retired += 1
            return {
                "total": total,
                "available": available,
                "unused": unused,
                "used_once": used_once,
                "reusable": reusable,
                "consumed": consumed,
                "refunded": refunded,
                "retired": retired,
            }

    def delete(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [row for row in self._items if row.get("id") != item_id]
            if len(self._items) == before:
                return False
            self._save_unlocked()
            return True

    def purge(
        self,
        *,
        refunded: bool = True,
        consumed: bool = True,
        retired: bool = True,
        exhausted_max_uses: Optional[int] = None,
    ) -> int:
        with self._lock:
            kept: List[Dict[str, Any]] = []
            removed = 0
            for row in self._items:
                status = row.get("status")
                uses = int(row.get("use_count") or 0)
                drop = False
                if refunded and status == STATUS_REFUNDED:
                    drop = True
                if consumed and status == STATUS_CONSUMED:
                    drop = True
                if retired and status == STATUS_RETIRED:
                    drop = True
                if exhausted_max_uses is not None and status == STATUS_AVAILABLE and uses >= int(exhausted_max_uses):
                    drop = True
                if drop:
                    removed += 1
                else:
                    kept.append(row)
            self._items = kept
            self._save_unlocked()
            return removed
