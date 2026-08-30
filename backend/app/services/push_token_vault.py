"""REGHelp Push Token 本地库存：签发后落盘，未成功消耗的可按开关复用。

排序策略（开启复用时）：use_count 升序 → created_at 升序，即优先未使用，其次用过 1 次。
成功注册 / 已 setStatus 退款的令牌不再参与复用。

租约（lease）：Push Token 与设备指纹绑定，同一枚只能被一个注册任务持有。
`acquire_for_reuse` 只发当前无持有者（或租约已过期）的 available 令牌，并把 `lease_task_id`
登记为申请方；`mark_refunded / mark_retired / mark_failed_keep` 遇到非持有者调用一律 no-op，
避免 A 任务的 retire / setStatus 把 B 任务正在用的令牌打掉。任务收尾调用
`release_task_leases(task_id)` 归还租约；进程崩溃则由 `LEASE_TTL_SECONDS` 兜底回收。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
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

# 租约兜底回收时长：进程被 kill / 任务异常退出未归还时，超过该时长即可被其它任务接管。
# 取值远大于单轮注册（含 180s 退款窗口 + OTP 轮询），正常流程不会踩到。
LEASE_TTL_SECONDS = 900.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_deadline(now: Optional[datetime] = None) -> str:
    return ((now or datetime.now(timezone.utc)) + timedelta(seconds=LEASE_TTL_SECONDS)).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _lease_holder(row: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    """返回当前有效持有者；无持有者或租约已过期则返回 None。"""
    holder = str(row.get("lease_task_id") or "").strip()
    if not holder:
        return None
    deadline = _parse_iso(row.get("leased_until"))
    if deadline is not None and (now or datetime.now(timezone.utc)) >= deadline:
        return None
    return holder


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
                if source_task_id:
                    self._claim_unlocked(existing, source_task_id)
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
                "lease_task_id": None,
                "leased_at": None,
                "leased_until": None,
            }
            # 新签发的令牌立即归属申请方，防止并发任务把它当空闲库存抢走
            if source_task_id:
                self._claim_unlocked(row, source_task_id)
            self._items.append(row)
            self._save_unlocked()
            return dict(row)

    def _claim_unlocked(self, row: Dict[str, Any], lease_task_id: str) -> None:
        row["lease_task_id"] = lease_task_id
        row["last_lease_task_id"] = lease_task_id
        row["leased_at"] = _utc_now()
        row["leased_until"] = _lease_deadline()

    def _release_unlocked(self, row: Dict[str, Any]) -> None:
        row["lease_task_id"] = None
        row["leased_at"] = None
        row["leased_until"] = None

    def _lease_conflict(self, row: Dict[str, Any], lease_task_id: Optional[str]) -> Optional[str]:
        """若该行被别的任务持有则返回持有者，否则返回 None。"""
        holder = _lease_holder(row)
        if holder and holder != (lease_task_id or None):
            return holder
        return None

    def acquire_for_reuse(
        self,
        *,
        max_uses: int = 2,
        app_type: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 use_count 升序取一枚「当前无人持有」的可用令牌；命中后 use_count+1 并登记租约。

        返回值额外带 `use_count_before`，便于调用方如实播报「复用前 → 复用后」。
        """
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
                # 另一任务正在用的令牌绝不能转租：对方 retire / setStatus 会打掉本任务
                if self._lease_conflict(row, lease_task_id):
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
            before = int(picked.get("use_count") or 0)
            picked["use_count"] = before + 1
            picked["last_used_at"] = _utc_now()
            picked["updated_at"] = picked["last_used_at"]
            picked["last_outcome"] = "leased"
            if lease_task_id:
                self._claim_unlocked(picked, lease_task_id)
            self._save_unlocked()
            view = dict(picked)
            view["use_count_before"] = before
            return view

    def mark_attempt(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """新签发令牌在本任务首次 auth.sendCode 前调用：把 use_count 从 0 记到 1 并登记持有者。"""
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            holder = self._lease_conflict(row, lease_task_id)
            if holder:
                logger.warning(
                    "Push Token %s 正被任务 %s 持有，拒绝任务 %s 的 mark_attempt",
                    row.get("id"),
                    holder,
                    lease_task_id,
                )
                return None
            if int(row.get("use_count") or 0) <= 0:
                row["use_count"] = 1
            row["last_used_at"] = _utc_now()
            row["updated_at"] = row["last_used_at"]
            if lease_task_id:
                self._claim_unlocked(row, lease_task_id)
            row["last_outcome"] = "attempted"
            self._save_unlocked()
            return dict(row)

    def mark_success(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if self._reject_foreign_lease(row, lease_task_id, "mark_success"):
                return None
            row["status"] = STATUS_CONSUMED
            row["last_outcome"] = "success"
            row["updated_at"] = _utc_now()
            self._release_unlocked(row)
            self._save_unlocked()
            return dict(row)

    def mark_refunded(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if self._reject_foreign_lease(row, lease_task_id, "mark_refunded"):
                return None
            row["status"] = STATUS_REFUNDED
            row["last_outcome"] = "refunded"
            row["updated_at"] = _utc_now()
            self._release_unlocked(row)
            self._save_unlocked()
            return dict(row)

    def mark_retired(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        reason: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """主动作废：猎号轮换设备/窗口后不希望立刻把同一枚 Token 租回来。

        已 consumed / refunded 的行保持原状态，其余一律置为 retired，退出复用候选。
        非持有者调用一律 no-op：不能把别的任务正在用的令牌 retire 掉。
        """
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if self._reject_foreign_lease(row, lease_task_id, "mark_retired"):
                return None
            if row.get("status") not in {STATUS_REFUNDED, STATUS_CONSUMED}:
                row["status"] = STATUS_RETIRED
            row["last_outcome"] = reason or "retired"
            row["updated_at"] = _utc_now()
            self._release_unlocked(row)
            self._save_unlocked()
            return dict(row)

    def mark_failed_keep(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        reason: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """失败且未退款：保留为 available，供二次利用。非持有者调用一律 no-op。"""
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return None
            if self._reject_foreign_lease(row, lease_task_id, "mark_failed_keep"):
                return None
            if row.get("status") not in {STATUS_REFUNDED, STATUS_CONSUMED}:
                row["status"] = STATUS_AVAILABLE
            row["last_outcome"] = reason or "failed"
            row["updated_at"] = _utc_now()
            self._release_unlocked(row)
            self._save_unlocked()
            return dict(row)

    def _reject_foreign_lease(
        self,
        row: Dict[str, Any],
        lease_task_id: Optional[str],
        action: str,
    ) -> bool:
        holder = self._lease_conflict(row, lease_task_id)
        if not holder:
            return False
        logger.warning(
            "拒绝任务 %s 对 Push Token %s 执行 %s：该令牌由任务 %s 持有",
            lease_task_id or "-",
            row.get("id"),
            action,
            holder,
        )
        return True

    def release_lease(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
        lease_task_id: Optional[str] = None,
    ) -> bool:
        """只解自己的租约；非持有者调用不生效。"""
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            if not row:
                return False
            holder = _lease_holder(row)
            if holder and holder != (lease_task_id or None):
                return False
            self._release_unlocked(row)
            row["updated_at"] = _utc_now()
            self._save_unlocked()
            return True

    def release_task_leases(self, lease_task_id: Optional[str]) -> int:
        """任务结束（成功 / 失败 / 取消）统一归还本任务持有的全部租约。"""
        holder_key = str(lease_task_id or "").strip()
        if not holder_key:
            return 0
        with self._lock:
            released = 0
            for row in self._items:
                if str(row.get("lease_task_id") or "") != holder_key:
                    continue
                self._release_unlocked(row)
                row["updated_at"] = _utc_now()
                released += 1
            if released:
                self._save_unlocked()
            return released

    def lease_holder(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Optional[str]:
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            return _lease_holder(row) if row else None

    def find_view(
        self,
        *,
        vault_id: Optional[str] = None,
        reghelp_task_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """只读快照，供调用方读 created_at 之类的签发时间做老化判断。"""
        with self._lock:
            row = self._find_unlocked(vault_id, reghelp_task_id, token)
            return dict(row) if row else None

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
            leased = 0
            for row in self._items:
                status = row.get("status")
                uses = int(row.get("use_count") or 0)
                held = bool(_lease_holder(row))
                if held:
                    leased += 1
                if status == STATUS_AVAILABLE:
                    available += 1
                    if uses == 0:
                        unused += 1
                    elif uses == 1:
                        used_once += 1
                    # reusable 只算「现在还能被新任务租走的」：已被某任务持有的不计入
                    if not held:
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
                "leased": leased,
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
