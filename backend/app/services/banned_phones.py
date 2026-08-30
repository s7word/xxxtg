"""本地号码黑名单 (banned_phones_cache)

Telegram 不会在 contacts.ResolvePhone / contacts.ImportContacts 上暴露
PHONE_NUMBER_BANNED：被销毁/注销的账号在通讯录里与「从未注册」无法区分。
权威封禁态只在 auth.sendCode（以及少数 auth.* 入口）以 RPC 错误返回。

本缓存不做协议嗅探，只记住本系统已经确认过的结果：
- Telegram 返回 PHONE_NUMBER_BANNED
- AntiSafety /check 历史库命中 BANNED
- 白号预检确认已注册（PRECHECK_PHONE_ALREADY_REGISTERED）
- auth.sendCode 仅下发 SENT_CODE_TYPE_APP（站内推送，号码已占用）
- 控制台手动录入

接码平台会回收并再次出租同一号码。再次租到时，在 Push Token / sendCode
之前直接退订，避免重复消耗 Attestation 与走多余注册路程。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.config import DATA_DIR

logger = logging.getLogger("BannedPhonesCache")

DEFAULT_CACHE_PATH = DATA_DIR / "banned_phones_cache.json"
CACHE_PATH = Path(os.getenv("BANNED_PHONES_CACHE_PATH", str(DEFAULT_CACHE_PATH))).resolve()

LOCAL_BANNED_REASON = "LOCAL_BANNED_PHONE_CACHE"
SOURCE_TELEGRAM_RPC = "telegram_rpc"
SOURCE_ANTISAFETY = "antisafety_check"
SOURCE_PRECHECK = "phone_precheck"
SOURCE_SENT_CODE = "sent_code_app"
SOURCE_MANUAL = "manual"

CATEGORY_BANNED = "banned"
CATEGORY_ALREADY_REGISTERED = "already_registered"
CATEGORY_MANUAL = "manual"

CATEGORY_PRIORITY = {
    CATEGORY_MANUAL: 0,
    CATEGORY_ALREADY_REGISTERED: 1,
    CATEGORY_BANNED: 2,
}

CATEGORY_LABELS = {
    CATEGORY_BANNED: "已拉黑",
    CATEGORY_ALREADY_REGISTERED: "已注册",
    CATEGORY_MANUAL: "手动",
}

_LOCK = threading.RLock()
_MEM: Optional[Dict[str, Any]] = None
_MEM_PATH: Optional[Path] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_digits(phone: Optional[str]) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def format_plus(phone: Optional[str]) -> str:
    digits = normalize_digits(phone)
    return f"+{digits}" if digits else ""


def infer_country(digits: str) -> Optional[str]:
    """按国际字冠做粗粒度国家推断，仅用于号段画像，不作为路由依据。"""
    if digits.startswith("62"):
        return "id"
    if digits.startswith("91"):
        return "in"
    if digits.startswith("56"):
        return "cl"
    if digits.startswith("57"):
        return "co"
    if digits.startswith("27"):
        return "za"
    if digits.startswith("964"):
        return "iq"
    if digits.startswith("7"):
        return "ru"
    if digits.startswith("1"):
        return "us"
    if digits.startswith("44"):
        return "gb"
    if digits.startswith("55"):
        return "br"
    if digits.startswith("380"):
        return "ua"
    if digits.startswith("998"):
        return "uz"
    if digits.startswith("992"):
        return "tj"
    return None


def prefix_of(digits: str, length: int = 6) -> str:
    if not digits:
        return ""
    return digits[: min(length, len(digits))]


def category_for_reason(reason: Optional[str], explicit: Optional[str] = None) -> str:
    if explicit in CATEGORY_PRIORITY:
        return explicit
    r = str(reason or "").upper()
    if r in {
        "SENT_CODE_TYPE_APP",
        "PRECHECK_PHONE_ALREADY_REGISTERED",
        "ALREADY_REGISTERED",
        "PHONE_ALREADY_REGISTERED",
    }:
        return CATEGORY_ALREADY_REGISTERED
    if r.startswith("MANUAL"):
        return CATEGORY_MANUAL
    if r in {
        "PHONE_NUMBER_BANNED",
        "PHONE_PREAUDIT_BANNED",
        "LOCAL_BANNED_PHONE_CACHE",
    }:
        return CATEGORY_BANNED
    # 未知原因默认按封禁处理，宁可多拦一次也不重复烧 Push
    return CATEGORY_BANNED


def _prefer_category(current: Optional[str], incoming: str) -> str:
    cur = current if current in CATEGORY_PRIORITY else CATEGORY_MANUAL
    inc = incoming if incoming in CATEGORY_PRIORITY else CATEGORY_MANUAL
    return inc if CATEGORY_PRIORITY[inc] >= CATEGORY_PRIORITY[cur] else cur


@dataclass
class BannedPhoneRecord:
    phone: str
    digits: str
    reason: str
    source: str
    category: str = CATEGORY_BANNED
    country: Optional[str] = None
    prefix: str = ""
    note: str = ""
    first_seen: str = ""
    last_seen: str = ""
    hits: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BannedPhoneRecord":
        digits = normalize_digits(data.get("digits") or data.get("phone"))
        reason = str(data.get("reason") or "PHONE_NUMBER_BANNED")
        category = category_for_reason(reason, data.get("category"))
        return cls(
            phone=str(data.get("phone") or format_plus(digits)),
            digits=digits,
            reason=reason,
            source=str(data.get("source") or SOURCE_TELEGRAM_RPC),
            category=category,
            country=data.get("country"),
            prefix=str(data.get("prefix") or prefix_of(digits)),
            note=str(data.get("note") or ""),
            first_seen=str(data.get("first_seen") or ""),
            last_seen=str(data.get("last_seen") or ""),
            hits=int(data.get("hits") or 1),
        )


@dataclass
class BannedPhonesStatus:
    enabled: bool
    size: int
    path: str
    message: str
    prefixes: List[Dict[str, Any]] = field(default_factory=list)
    countries: List[Dict[str, Any]] = field(default_factory=list)
    categories: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _empty_payload() -> Dict[str, Any]:
    return {"version": 1, "updated_at": "", "phones": {}}


def _cache_path(path: Optional[Path] = None) -> Path:
    return Path(path).resolve() if path is not None else CACHE_PATH


def _load_unlocked(path: Path) -> Dict[str, Any]:
    global _MEM, _MEM_PATH
    if _MEM is not None and _MEM_PATH == path:
        return _MEM
    payload = _empty_payload()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                phones = raw.get("phones") if isinstance(raw.get("phones"), dict) else {}
                payload = {
                    "version": int(raw.get("version") or 1),
                    "updated_at": str(raw.get("updated_at") or ""),
                    "phones": {normalize_digits(k): v for k, v in phones.items() if normalize_digits(k)},
                }
        except Exception as exc:
            logger.warning("读取 banned_phones_cache 失败，回退空库: %s", exc)
    _MEM = payload
    _MEM_PATH = path
    return payload


def _save_unlocked(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class BannedPhonesCache:
    """进程内 + JSON 持久化的号码黑名单（封禁 / 已注册）。"""

    @classmethod
    def reset_memory(cls, path: Optional[Path] = None) -> None:
        global _MEM, _MEM_PATH
        with _LOCK:
            _MEM = None
            _MEM_PATH = None
            if path is not None:
                _load_unlocked(_cache_path(path))

    @classmethod
    def lookup(cls, phone: Optional[str], path: Optional[Path] = None) -> Optional[BannedPhoneRecord]:
        digits = normalize_digits(phone)
        if not digits:
            return None
        with _LOCK:
            payload = _load_unlocked(_cache_path(path))
            raw = payload["phones"].get(digits)
            if not raw:
                return None
            try:
                return BannedPhoneRecord.from_dict(raw)
            except Exception as exc:
                logger.debug("解析封禁号记录失败: %s", exc)
                return None

    @classmethod
    def touch(
        cls,
        phone: Optional[str],
        path: Optional[Path] = None,
    ) -> Optional[BannedPhoneRecord]:
        """命中拦截时刷新 last_seen / hits，不改原因。"""
        digits = normalize_digits(phone)
        if not digits:
            return None
        now = _now_iso()
        dest = _cache_path(path)
        with _LOCK:
            payload = _load_unlocked(dest)
            existing = payload["phones"].get(digits)
            if not existing:
                return None
            record = BannedPhoneRecord.from_dict(existing)
            record.last_seen = now
            record.hits = int(record.hits or 1) + 1
            payload["phones"][digits] = record.to_dict()
            payload["updated_at"] = now
            _save_unlocked(dest, payload)
            _MEM = payload
            _MEM_PATH = dest
        return record

    @classmethod
    def remember(
        cls,
        phone: Optional[str],
        *,
        reason: str = "PHONE_NUMBER_BANNED",
        source: str = SOURCE_TELEGRAM_RPC,
        country: Optional[str] = None,
        category: Optional[str] = None,
        note: Optional[str] = None,
        path: Optional[Path] = None,
    ) -> Optional[BannedPhoneRecord]:
        digits = normalize_digits(phone)
        if not digits:
            return None
        now = _now_iso()
        dest = _cache_path(path)
        incoming_cat = category_for_reason(reason, category)
        with _LOCK:
            payload = _load_unlocked(dest)
            existing = payload["phones"].get(digits)
            if existing:
                record = BannedPhoneRecord.from_dict(existing)
                record.last_seen = now
                record.hits = int(record.hits or 1) + 1
                if reason:
                    record.reason = reason
                if source:
                    record.source = source
                if country:
                    record.country = country
                if note is not None:
                    record.note = str(note)
                record.category = _prefer_category(record.category, incoming_cat)
            else:
                inferred = country or infer_country(digits)
                record = BannedPhoneRecord(
                    phone=format_plus(digits),
                    digits=digits,
                    reason=reason,
                    source=source,
                    category=incoming_cat,
                    country=inferred,
                    prefix=prefix_of(digits),
                    note=str(note or ""),
                    first_seen=now,
                    last_seen=now,
                    hits=1,
                )
            payload["phones"][digits] = record.to_dict()
            payload["updated_at"] = now
            _save_unlocked(dest, payload)
            _MEM = payload
            _MEM_PATH = dest
        logger.info(
            "banned_phones_cache 收录 %s category=%s reason=%s source=%s hits=%s",
            record.phone,
            record.category,
            record.reason,
            record.source,
            record.hits,
        )
        return record

    @classmethod
    def remove(cls, phone: Optional[str], path: Optional[Path] = None) -> bool:
        digits = normalize_digits(phone)
        if not digits:
            return False
        dest = _cache_path(path)
        with _LOCK:
            payload = _load_unlocked(dest)
            if digits not in payload["phones"]:
                return False
            del payload["phones"][digits]
            payload["updated_at"] = _now_iso()
            _save_unlocked(dest, payload)
            _MEM = payload
            _MEM_PATH = dest
        return True

    @classmethod
    def purge(
        cls,
        *,
        category: Optional[str] = None,
        path: Optional[Path] = None,
    ) -> int:
        dest = _cache_path(path)
        with _LOCK:
            payload = _load_unlocked(dest)
            phones = payload.get("phones") or {}
            if not category:
                deleted = len(phones)
                payload["phones"] = {}
            else:
                keep: Dict[str, Any] = {}
                deleted = 0
                for digits, raw in phones.items():
                    record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
                    if record.category == category:
                        deleted += 1
                    else:
                        keep[digits] = raw
                payload["phones"] = keep
            if deleted:
                payload["updated_at"] = _now_iso()
                _save_unlocked(dest, payload)
                _MEM = payload
                _MEM_PATH = dest
            return deleted

    @classmethod
    def size(cls, path: Optional[Path] = None) -> int:
        with _LOCK:
            return len(_load_unlocked(_cache_path(path)).get("phones") or {})

    @classmethod
    def summary(cls, path: Optional[Path] = None) -> Dict[str, int]:
        with _LOCK:
            phones = _load_unlocked(_cache_path(path)).get("phones") or {}
        out = {
            "total": 0,
            CATEGORY_BANNED: 0,
            CATEGORY_ALREADY_REGISTERED: 0,
            CATEGORY_MANUAL: 0,
        }
        for digits, raw in phones.items():
            record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
            out["total"] += 1
            key = record.category if record.category in out else CATEGORY_BANNED
            out[key] = out.get(key, 0) + 1
        return out

    @classmethod
    def list_items(
        cls,
        *,
        q: Optional[str] = None,
        category: Optional[str] = None,
        country: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
        path: Optional[Path] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        needle = normalize_digits(q) if q else ""
        country_key = (country or "").strip().lower() or None
        category_key = category if category in CATEGORY_PRIORITY else None
        with _LOCK:
            phones = _load_unlocked(_cache_path(path)).get("phones") or {}
        rows: List[BannedPhoneRecord] = []
        for digits, raw in phones.items():
            record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
            if needle and needle not in record.digits:
                continue
            if category_key and record.category != category_key:
                continue
            if country_key and (record.country or "").lower() != country_key:
                continue
            rows.append(record)
        rows.sort(key=lambda r: (r.last_seen or "", r.digits), reverse=True)
        total = len(rows)
        limit = max(1, min(int(limit or 200), 1000))
        offset = max(0, int(offset or 0))
        sliced = rows[offset: offset + limit]
        return [r.to_dict() for r in sliced], total

    @classmethod
    def prefix_stats(cls, prefix_length: int = 6, path: Optional[Path] = None) -> List[Dict[str, Any]]:
        with _LOCK:
            phones = _load_unlocked(_cache_path(path)).get("phones") or {}
        buckets: Dict[str, Dict[str, Any]] = {}
        for digits, raw in phones.items():
            record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
            key = prefix_of(record.digits or digits, prefix_length)
            bucket = buckets.setdefault(
                key,
                {"prefix": key, "country": record.country or infer_country(key), "count": 0, "hits": 0},
            )
            bucket["count"] += 1
            bucket["hits"] += int(record.hits or 1)
        return sorted(buckets.values(), key=lambda item: (-int(item["hits"]), -int(item["count"]), item["prefix"]))

    @classmethod
    def country_stats(cls, path: Optional[Path] = None) -> List[Dict[str, Any]]:
        with _LOCK:
            phones = _load_unlocked(_cache_path(path)).get("phones") or {}
        buckets: Dict[str, Dict[str, Any]] = {}
        for digits, raw in phones.items():
            record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
            key = record.country or infer_country(record.digits or digits) or "unknown"
            bucket = buckets.setdefault(key, {"country": key, "count": 0, "hits": 0})
            bucket["count"] += 1
            bucket["hits"] += int(record.hits or 1)
        return sorted(buckets.values(), key=lambda item: (-int(item["hits"]), item["country"]))

    @classmethod
    def category_stats(cls, path: Optional[Path] = None) -> List[Dict[str, Any]]:
        summary = cls.summary(path=path)
        rows = []
        for key in (CATEGORY_BANNED, CATEGORY_ALREADY_REGISTERED, CATEGORY_MANUAL):
            rows.append({
                "category": key,
                "label": CATEGORY_LABELS.get(key, key),
                "count": int(summary.get(key) or 0),
            })
        return rows

    @classmethod
    def describe_status(cls, path: Optional[Path] = None) -> BannedPhonesStatus:
        dest = _cache_path(path)
        size = cls.size(dest)
        prefixes = cls.prefix_stats(path=dest)[:12]
        countries = cls.country_stats(path=dest)
        categories = cls.category_stats(path=dest)
        if size == 0:
            message = "本地号码黑名单为空：尚无封禁 / 已注册 / 手动录入记录"
        else:
            top = prefixes[0]["prefix"] if prefixes else "-"
            banned_n = next((c["count"] for c in categories if c["category"] == CATEGORY_BANNED), 0)
            reg_n = next((c["count"] for c in categories if c["category"] == CATEGORY_ALREADY_REGISTERED), 0)
            message = (
                f"本地号码黑名单已收录 {size} 个"
                f"（拉黑 {banned_n} / 已注册 {reg_n}）；最高风险号段 {top}"
            )
        return BannedPhonesStatus(
            enabled=True,
            size=size,
            path=str(dest),
            message=message,
            prefixes=prefixes,
            countries=countries,
            categories=categories,
        )
