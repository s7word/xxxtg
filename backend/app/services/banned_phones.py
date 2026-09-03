"""本地号码黑名单 (banned_phones_cache)

Telegram 不会在 contacts.ResolvePhone / contacts.ImportContacts 上暴露
PHONE_NUMBER_BANNED：被销毁/注销的账号在通讯录里与「从未注册」无法区分。
权威封禁态只在 auth.sendCode（以及少数 auth.* 入口）以 RPC 错误返回。

本缓存不做协议嗅探，只记住本系统已经确认过的结果：
- Telegram 返回 PHONE_NUMBER_BANNED
- AntiSafety /check 历史库命中 BANNED
- 白号预检确认已注册（PRECHECK_PHONE_ALREADY_REGISTERED）
- auth.sendCode 仅下发 SENT_CODE_TYPE_APP（本次 APP 投递不可用，带 TTL 临时拉黑）
- 控制台手动录入

分类分「永久」与「带 TTL」两种：
- banned / already_registered / manual 是已确认的结论，永久有效；
- app_delivery_unusable 只是「这一次验证码进了站内 App」的观测。白号预检通过后仍走
  App 的号可能是 Push Token / allow_app_hash 造成的，并不等于号码已注册，所以只临时
  拉黑（默认 48h），过期后自动放回可试池，不能把它当 already_registered 永久判死。

接码平台会回收并再次出租同一号码。再次租到时，在 Push Token / sendCode
之前直接退订，避免重复消耗 Attestation 与走多余注册路程。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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
# 「本次 auth.sendCode 把码投进了站内 App」——不可用是事实，已注册只是猜测，故带 TTL
CATEGORY_APP_DELIVERY = "app_delivery_unusable"
CATEGORY_MANUAL = "manual"

# 优先级只决定同一号码被多次收录时保留哪个结论：数值越大越权威。
# app_delivery_unusable 最低——任何一次确凿结论（已注册 / 封禁 / 手动）都应把它顶掉并转永久。
CATEGORY_PRIORITY = {
    CATEGORY_APP_DELIVERY: 0,
    CATEGORY_MANUAL: 1,
    CATEGORY_ALREADY_REGISTERED: 2,
    CATEGORY_BANNED: 3,
}

CATEGORY_LABELS = {
    CATEGORY_BANNED: "已拉黑",
    CATEGORY_ALREADY_REGISTERED: "已注册",
    CATEGORY_APP_DELIVERY: "APP投递不可用",
    CATEGORY_MANUAL: "手动",
}

ALL_CATEGORIES = (
    CATEGORY_BANNED,
    CATEGORY_ALREADY_REGISTERED,
    CATEGORY_APP_DELIVERY,
    CATEGORY_MANUAL,
)

# 带 TTL 的分类及其默认存活小时数；未列出的分类一律永久有效。
DEFAULT_APP_DELIVERY_TTL_HOURS = 48.0
TTL_CATEGORY_DEFAULTS = {
    CATEGORY_APP_DELIVERY: DEFAULT_APP_DELIVERY_TTL_HOURS,
}

_LOCK = threading.RLock()
_MEM: Optional[Dict[str, Any]] = None
_MEM_PATH: Optional[Path] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def resolve_ttl_hours(category: str, ttl_hours: Optional[float] = None) -> Optional[float]:
    """返回该分类实际生效的 TTL 小时数；None 表示永久有效。"""
    if category not in TTL_CATEGORY_DEFAULTS:
        return None
    if ttl_hours is None:
        return TTL_CATEGORY_DEFAULTS[category]
    try:
        hours = float(ttl_hours)
    except (TypeError, ValueError):
        return TTL_CATEGORY_DEFAULTS[category]
    if hours <= 0:
        return TTL_CATEGORY_DEFAULTS[category]
    return hours


def _expiry_for(category: str, ttl_hours: Optional[float], base: Optional[datetime] = None) -> Optional[str]:
    hours = resolve_ttl_hours(category, ttl_hours)
    if hours is None:
        return None
    origin = base or datetime.now(timezone.utc)
    return (origin + timedelta(hours=hours)).replace(microsecond=0).isoformat()


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
    # SENT_CODE_TYPE_APP 只说明这一次码进了站内 App（可能是 Push / allow_app_hash 导致），
    # 不足以判定号码已注册，因此归入带 TTL 的临时分类而不是永久 already_registered
    if r in {
        "SENT_CODE_TYPE_APP",
        "APP_DELIVERY_UNUSABLE",
    }:
        return CATEGORY_APP_DELIVERY
    if r in {
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
    # 仅带 TTL 的分类会写入；None 表示永久有效
    expires_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        deadline = _parse_iso(self.expires_at)
        if deadline is None:
            return False
        return (now or datetime.now(timezone.utc)) >= deadline

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BannedPhoneRecord":
        digits = normalize_digits(data.get("digits") or data.get("phone"))
        reason = str(data.get("reason") or "PHONE_NUMBER_BANNED")
        category = category_for_reason(reason, data.get("category"))
        expires_at = str(data.get("expires_at") or "").strip() or None
        if category not in TTL_CATEGORY_DEFAULTS:
            expires_at = None
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
            expires_at=expires_at,
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


def _migrate_app_delivery_unlocked(payload: Dict[str, Any]) -> int:
    """历史遗留：SentCodeTypeApp 曾被写成永久 already_registered，按 TTL 分类回迁。

    这些号只被观测到「码进了站内 App」，从未被预检或 Telegram 确认已注册，永久拉黑会
    把大量可用号误杀。回迁后以 last_seen 为基准补一个 TTL，多半立刻过期并被自然清理。
    """
    changed = 0
    for digits, raw in (payload.get("phones") or {}).items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("source") or "") != SOURCE_SENT_CODE:
            continue
        if str(raw.get("reason") or "").upper() != "SENT_CODE_TYPE_APP":
            continue
        if str(raw.get("category") or "") == CATEGORY_APP_DELIVERY:
            continue
        raw["category"] = CATEGORY_APP_DELIVERY
        raw["expires_at"] = _expiry_for(
            CATEGORY_APP_DELIVERY,
            None,
            base=_parse_iso(raw.get("last_seen")) or _parse_iso(raw.get("first_seen")),
        )
        changed += 1
    return changed


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
                migrated = _migrate_app_delivery_unlocked(payload)
                if migrated:
                    logger.info(
                        "banned_phones_cache 回迁 %s 条 SENT_CODE_TYPE_APP 记录："
                        "永久 already_registered → 带 TTL 的 %s",
                        migrated,
                        CATEGORY_APP_DELIVERY,
                    )
                    try:
                        _save_unlocked(path, payload)
                    except Exception as exc:
                        logger.warning("banned_phones_cache 回迁结果落盘失败: %s", exc)
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
    """进程内 + JSON 持久化的号码黑名单（封禁 / 已注册 / APP 投递不可用）。"""

    @classmethod
    def reset_memory(cls, path: Optional[Path] = None) -> None:
        global _MEM, _MEM_PATH
        with _LOCK:
            _MEM = None
            _MEM_PATH = None
            if path is not None:
                _load_unlocked(_cache_path(path))

    @classmethod
    def _drop_expired_unlocked(cls, dest: Path, payload: Dict[str, Any], digits: str) -> None:
        global _MEM, _MEM_PATH
        payload["phones"].pop(digits, None)
        payload["updated_at"] = _now_iso()
        try:
            _save_unlocked(dest, payload)
        except Exception as exc:
            logger.warning("清理过期黑名单条目落盘失败: %s", exc)
        _MEM = payload
        _MEM_PATH = dest

    @classmethod
    def _active_records_unlocked(cls, payload: Dict[str, Any]) -> List[BannedPhoneRecord]:
        """只返回未过期的记录：过期条目对外等同于未命中。"""
        now = datetime.now(timezone.utc)
        rows: List[BannedPhoneRecord] = []
        for digits, raw in (payload.get("phones") or {}).items():
            try:
                record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
            except Exception:
                continue
            if record.is_expired(now):
                continue
            rows.append(record)
        return rows

    @classmethod
    def _active(cls, path: Optional[Path] = None) -> List[BannedPhoneRecord]:
        with _LOCK:
            payload = _load_unlocked(_cache_path(path))
            return cls._active_records_unlocked(payload)

    @classmethod
    def lookup(cls, phone: Optional[str], path: Optional[Path] = None) -> Optional[BannedPhoneRecord]:
        digits = normalize_digits(phone)
        if not digits:
            return None
        dest = _cache_path(path)
        with _LOCK:
            payload = _load_unlocked(dest)
            raw = payload["phones"].get(digits)
            if not raw:
                return None
            try:
                record = BannedPhoneRecord.from_dict(raw)
            except Exception as exc:
                logger.debug("解析封禁号记录失败: %s", exc)
                return None
            if record.is_expired():
                # TTL 到点即视为未命中，并顺手清掉，避免临时结论变成事实上的永久拉黑
                logger.info(
                    "banned_phones_cache 条目已过期，放回可试池: %s category=%s expires_at=%s",
                    record.phone,
                    record.category,
                    record.expires_at,
                )
                cls._drop_expired_unlocked(dest, payload, digits)
                return None
            return record

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
            if record.is_expired():
                cls._drop_expired_unlocked(dest, payload, digits)
                return None
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
        ttl_hours: Optional[float] = None,
        path: Optional[Path] = None,
    ) -> Optional[BannedPhoneRecord]:
        """收录一个号码。

        `ttl_hours` 只对带 TTL 的分类（当前是 app_delivery_unusable）生效；省略时用该分类
        的默认存活时长。若同一号码此前已有更权威的永久结论（已注册 / 封禁 / 手动），分类
        与永久性都不会被临时观测降级。
        """
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
                # 过期条目按新记录重开，不继承旧 hits/首见，否则统计会一直虚高
                if record.is_expired():
                    existing = None
            if existing:
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
            record.expires_at = _expiry_for(record.category, ttl_hours)
            payload["phones"][digits] = record.to_dict()
            payload["updated_at"] = now
            _save_unlocked(dest, payload)
            _MEM = payload
            _MEM_PATH = dest
        logger.info(
            "banned_phones_cache 收录 %s category=%s reason=%s source=%s hits=%s expires_at=%s",
            record.phone,
            record.category,
            record.reason,
            record.source,
            record.hits,
            record.expires_at or "永久",
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
    def purge_expired(cls, path: Optional[Path] = None) -> int:
        """清掉所有已过 TTL 的条目（拦截路径也会按需惰性清理）。"""
        global _MEM, _MEM_PATH
        dest = _cache_path(path)
        now = datetime.now(timezone.utc)
        with _LOCK:
            payload = _load_unlocked(dest)
            keep: Dict[str, Any] = {}
            deleted = 0
            for digits, raw in (payload.get("phones") or {}).items():
                record = BannedPhoneRecord.from_dict(raw if isinstance(raw, dict) else {"phone": digits})
                if record.is_expired(now):
                    deleted += 1
                else:
                    keep[digits] = raw
            if deleted:
                payload["phones"] = keep
                payload["updated_at"] = _now_iso()
                _save_unlocked(dest, payload)
                _MEM = payload
                _MEM_PATH = dest
            return deleted

    @classmethod
    def size(cls, path: Optional[Path] = None) -> int:
        return len(cls._active(path))

    @classmethod
    def summary(cls, path: Optional[Path] = None) -> Dict[str, int]:
        out: Dict[str, int] = {"total": 0}
        for key in ALL_CATEGORIES:
            out[key] = 0
        for record in cls._active(path):
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
        rows: List[BannedPhoneRecord] = []
        for record in cls._active(path):
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
        buckets: Dict[str, Dict[str, Any]] = {}
        for record in cls._active(path):
            key = prefix_of(record.digits, prefix_length)
            bucket = buckets.setdefault(
                key,
                {"prefix": key, "country": record.country or infer_country(key), "count": 0, "hits": 0},
            )
            bucket["count"] += 1
            bucket["hits"] += int(record.hits or 1)
        return sorted(buckets.values(), key=lambda item: (-int(item["hits"]), -int(item["count"]), item["prefix"]))

    @classmethod
    def country_stats(cls, path: Optional[Path] = None) -> List[Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for record in cls._active(path):
            key = record.country or infer_country(record.digits) or "unknown"
            bucket = buckets.setdefault(key, {"country": key, "count": 0, "hits": 0})
            bucket["count"] += 1
            bucket["hits"] += int(record.hits or 1)
        return sorted(buckets.values(), key=lambda item: (-int(item["hits"]), item["country"]))

    @classmethod
    def category_stats(cls, path: Optional[Path] = None) -> List[Dict[str, Any]]:
        summary = cls.summary(path=path)
        rows = []
        for key in ALL_CATEGORIES:
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
            message = "本地号码黑名单为空：尚无封禁 / 已注册 / APP 投递不可用 / 手动录入记录"
        else:
            top = prefixes[0]["prefix"] if prefixes else "-"
            banned_n = next((c["count"] for c in categories if c["category"] == CATEGORY_BANNED), 0)
            reg_n = next((c["count"] for c in categories if c["category"] == CATEGORY_ALREADY_REGISTERED), 0)
            app_n = next((c["count"] for c in categories if c["category"] == CATEGORY_APP_DELIVERY), 0)
            message = (
                f"本地号码黑名单已收录 {size} 个"
                f"（拉黑 {banned_n} / 已注册 {reg_n} / APP投递不可用 {app_n} 条带TTL）；"
                f"最高风险号段 {top}"
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
