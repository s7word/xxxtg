"""SMSBazaar 告警 Webhook（smsall.alert.v1）：接低价新货并决定是否自动开注册。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.config import DATA_DIR

logger = logging.getLogger("SmsallWebhook")

SCHEMA = "smsall.alert.v1"
SECRET_FILENAME = "smsall_webhook_secret"
EVENTS_FILENAME = "smsall_events.json"
MAX_EVENTS = 200
TELEGRAM_SERVICE_KEYS = frozenset({"telegram", "tg", "telegramotp"})
EVENT_TYPES = frozenset({"restock", "new_listing"})
# 「设置 → 程序推送 → 狙击」的判定口径：payload.source / 请求头 / item 三处任一命中即算狙击。
SNIPER_TOKEN = "sniper"
SNIPER_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "sniper", "high", "urgent"})
SNIPER_HEADER_FLAG = "x-smsall-sniper"
SNIPER_HEADER_PRIORITY = "x-smsall-priority"
SNIPER_COOLDOWN_PREFIX = "sniper:"

_LOCK = threading.RLock()
_EVENTS: deque = deque(maxlen=MAX_EVENTS)
_COOLDOWN_UNTIL: Dict[str, float] = {}
_LOADED = False
_PERSIST_ENABLED = True


def secret_file_path():
    return DATA_DIR / SECRET_FILENAME


def events_file_path() -> Path:
    override = (os.getenv("SMSALL_EVENTS_PATH") or "").strip()
    if override:
        return Path(override)
    return DATA_DIR / EVENTS_FILENAME


def _testing() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _should_persist() -> bool:
    return _PERSIST_ENABLED and not _testing()


def _stamp_record(rec: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    out = dict(rec)
    ts = _as_float(out.get("at"))
    if ts is None:
        ts = float(now if now is not None else time.time())
    out["at"] = ts
    if not out.get("id"):
        out["id"] = uuid.uuid4().hex[:12]
    if not out.get("received_at"):
        out["received_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return out


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    if _testing():
        return
    path = events_file_path()
    try:
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("读取 SMSBazaar 通知列表失败")
        return
    rows = raw if isinstance(raw, list) else []
    for rec in reversed(rows[-MAX_EVENTS:]):
        if isinstance(rec, dict):
            _EVENTS.appendleft(_stamp_record(rec))


def _persist_unlocked() -> None:
    if not _should_persist():
        return
    path = events_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [dict(item) for item in list(_EVENTS)[:MAX_EVENTS]]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("无法持久化 SMSBazaar 通知列表")


def ensure_file_secret() -> str:
    path = secret_file_path()
    try:
        if path.exists():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        logger.warning("读取 SMSBazaar webhook secret 失败，将尝试重新生成")
    import secrets

    secret = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        logger.warning("无法持久化 SMSBazaar webhook secret")
    return secret


def resolve_secret(config: Any = None) -> str:
    env = (os.getenv("SMSALL_HOOK_SECRET") or "").strip()
    if env:
        return env
    cfg = ""
    if config is not None:
        cfg = str(getattr(config, "smsall_webhook_secret", "") or "").strip()
    if cfg:
        return cfg
    return ensure_file_secret()


def verify_request(raw_body: bytes, authorization: str, signature: str, secret: str) -> bool:
    """Secret 为空则放行；有 Secret 时校验 HMAC 或 Bearer 之一。"""
    if not secret:
        return True
    sig = (signature or "").strip()
    if sig:
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).hexdigest()
        expect = f"sha256={digest}"
        return hmac.compare_digest(sig, expect)
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return hmac.compare_digest(token, secret)
    return False


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed == float("inf") or parsed == float("-inf"):
        return None
    return parsed


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_iso2(value: Any) -> Optional[str]:
    from backend.app.services.device_db_manager import normalize_country

    return normalize_country(value)


def _normalize_headers(headers: Any) -> Dict[str, str]:
    """把任意 headers 映射压成小写短横线键，方便统一取值。"""
    out: Dict[str, str] = {}
    if not headers:
        return out
    try:
        pairs = list(headers.items())
    except AttributeError:
        return out
    for key, value in pairs:
        norm = str(key or "").strip().lower().replace("_", "-")
        if norm:
            out[norm] = str(value or "").strip()
    return out


def _tags_contain_sniper(value: Any) -> bool:
    if isinstance(value, str):
        return SNIPER_TOKEN in value.strip().lower()
    if isinstance(value, (list, tuple, set)):
        return any(str(tag or "").strip().lower() == SNIPER_TOKEN for tag in value)
    return False


def headers_indicate_sniper(headers: Any) -> bool:
    hdrs = _normalize_headers(headers)
    if hdrs.get(SNIPER_HEADER_FLAG, "").strip().lower() in SNIPER_TRUE_TOKENS:
        return True
    return hdrs.get(SNIPER_HEADER_PRIORITY, "").strip().lower() == SNIPER_TOKEN


def payload_indicates_sniper(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("source", "priority", "channel", "mode", "kind"):
        if str(payload.get(key) or "").strip().lower() == SNIPER_TOKEN:
            return True
    return _tags_contain_sniper(payload.get("tags"))


def item_indicates_sniper(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    flag = raw.get("sniper")
    if isinstance(flag, bool):
        if flag:
            return True
    elif flag is not None and str(flag).strip().lower() in SNIPER_TRUE_TOKENS:
        return True
    for key in ("priority", "source"):
        if str(raw.get(key) or "").strip().lower() == SNIPER_TOKEN:
            return True
    return _tags_contain_sniper(raw.get("tags"))


def _parse_supplier_ids(raw: Any) -> List[str]:
    """解析上游 supplierIds / providerRef 为去重后的供应商 ID 列表。"""
    ids: List[str] = []
    seen = set()
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            token = str(item or "").strip()
            if token and token not in seen:
                seen.add(token)
                ids.append(token)
    elif raw is not None:
        token = str(raw).strip()
        if token and token not in seen:
            ids.append(token)
    return ids


def normalize_webhook_payload(payload: Any) -> Dict[str, Any]:
    """兼容上游直接 POST items 数组或标准 smsall.alert.v1 对象。"""
    if isinstance(payload, list):
        return {"schema": SCHEMA, "serviceKey": "telegram", "items": payload}
    if isinstance(payload, dict):
        return payload
    return {}


def _sniper_launch_key(item: Dict[str, Any]) -> str:
    country = str(item.get("country") or "").lower()
    supplier_ids = item.get("supplier_ids") or []
    provider_ref = str(item.get("provider_ref") or "").strip()
    if provider_ref:
        return f"{country}:{provider_ref}"
    if supplier_ids:
        return f"{country}:{'-'.join(supplier_ids)}"
    return country


def _resolve_sniper_price_cap(country: str, config: Any) -> Tuple[Optional[float], str]:
    """返回 (上限, 来源)：country_caps / global / none。"""
    iso = str(country or "").strip().lower()
    caps = getattr(config, "smsall_sniper_price_caps", None) or []
    if caps:
        for row in caps:
            cap_country = normalize_iso2(getattr(row, "country", None) if not isinstance(row, dict) else row.get("country"))
            if cap_country != iso:
                continue
            cap_val = _as_float(getattr(row, "max_price_usd", None) if not isinstance(row, dict) else row.get("max_price_usd"))
            if cap_val is not None:
                return cap_val, "country_caps"
        return None, "country_not_configured"
    global_cap = _as_float(getattr(config, "smsall_sniper_max_price_usd", None))
    if global_cap is not None:
        return global_cap, "global"
    return None, "none"


def parse_items(payload: Any, headers: Any = None, force_sniper: bool = False) -> List[Dict[str, Any]]:
    body = normalize_webhook_payload(payload)
    if not isinstance(body, dict):
        return []
    raw_items = body.get("items") or []
    if not isinstance(raw_items, list):
        return []
    sniper_all = bool(force_sniper) or payload_indicates_sniper(body) or headers_indicate_sniper(headers)
    parsed: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        country = normalize_iso2(raw.get("country"))
        if not country:
            continue
        sniper = sniper_all or item_indicates_sniper(raw)
        event_type = str(raw.get("type") or "").strip().lower()
        if event_type not in EVENT_TYPES:
            # 狙击推送的 type 上游可能另有取值，不能因为不认识就把抢货信号丢掉
            if not sniper:
                continue
            event_type = event_type or SNIPER_TOKEN
        supplier_ids = _parse_supplier_ids(raw.get("supplierIds"))
        provider_ref = str(raw.get("providerRef") or raw.get("provider_ref") or "").strip()
        if not supplier_ids and provider_ref:
            supplier_ids = [provider_ref]
        parsed.append({
            "type": event_type,
            "sniper": sniper,
            "country": country,
            "country_name": str(raw.get("countryName") or country.upper()),
            "price_usd": _as_float(raw.get("priceUsd")),
            "currency": str(raw.get("currency") or "USD"),
            "stock_from": _as_int(raw.get("stockFrom"), 0),
            "stock_to": _as_int(raw.get("stockTo"), 0),
            "provider": str(raw.get("provider") or ""),
            "provider_code": str(raw.get("providerCode") or ""),
            "provider_ref": provider_ref,
            "supplier_ids": supplier_ids,
            "balance": _as_float(raw.get("balance")),
            "balance_currency": str(raw.get("balanceCurrency") or ""),
            "portal_url": str(raw.get("portalUrl") or ""),
        })
    parsed.sort(key=lambda item: (item["price_usd"] is None, item["price_usd"] or 0.0))
    return parsed


def _service_is_telegram(payload: Dict[str, Any]) -> bool:
    key = str(payload.get("serviceKey") or "").strip().lower().replace("-", "").replace("_", "")
    if not key:
        return True
    return key in TELEGRAM_SERVICE_KEYS


def _cooldown_key(country: str, sniper: bool = False, launch_key: Optional[str] = None) -> str:
    """狙击与普通补货各自独立冷却；狙击按 country+supplier 维度，避免同国多供应商互相挡死。"""
    if sniper:
        token = str(launch_key or country).strip().lower()
        return f"{SNIPER_COOLDOWN_PREFIX}{token}"
    return str(country)


def _on_cooldown(country: str, now: float, cooldown: float, sniper: bool = False, launch_key: Optional[str] = None) -> bool:
    until = _COOLDOWN_UNTIL.get(_cooldown_key(country, sniper, launch_key)) or 0.0
    return until > now and cooldown > 0


def _mark_cooldown(country: str, now: float, cooldown: float, sniper: bool = False, launch_key: Optional[str] = None) -> None:
    if cooldown <= 0:
        return
    _COOLDOWN_UNTIL[_cooldown_key(country, sniper, launch_key)] = now + cooldown


def _busy_task_count() -> int:
    from backend.app.services.registrar import RegistrationTaskManager

    manager = RegistrationTaskManager.get_instance()
    busy = 0
    for task in manager.list_tasks() or []:
        if (task.get("status") or "") in {"pending", "running", "waiting_code", "logging_in"}:
            busy += 1
    return busy


def resolve_sniper_sms_provider(item: Dict[str, Any], config: Any = None) -> str:
    """狙击批次选接码源：有 supplierIds 或上游是 SMS Bower/Grizzly 时，不再死用全局 FiveSim。

    上游告警来自 SMSBazaar 对各接码平台的监控；本地取号必须落到真正有货的那家，
    否则 providerIds 会被 FiveSim 忽略，表现为「平台手动能取、狙击全 NO_NUMBERS」。
    """
    from backend.app.services.registrar import RegistrationOrchestrator

    supplier_ids = item.get("supplier_ids") or []
    upstream = str(item.get("provider") or "").strip().lower()
    compact = upstream.replace(" ", "").replace("-", "").replace("_", "")

    preferred: Optional[str] = None
    if "smsbower" in compact or compact in {"bower", "smsbowerapp"}:
        preferred = "smsbower"
    elif "grizzly" in compact:
        preferred = "grizzlysms"
    elif supplier_ids:
        # providerIds 是 SMS-Activate 系参数；有供应商 ID 时优先走 SMS Bower
        preferred = "smsbower"

    if preferred:
        return RegistrationOrchestrator.normalize_sms_provider(preferred)
    return RegistrationOrchestrator.resolve_sms_provider(config)


def decide_sniper_launches(
    items: List[Dict[str, Any]],
    config: Any,
    now: float,
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """狙击通道：命中即按「10 路 × 每任务 20 次取号」直接开猎号，与普通自动开跑互不影响。"""
    enabled = getattr(config, "smsall_sniper_enabled", True)
    enabled = True if enabled is None else bool(enabled)
    count = max(1, min(10, _as_int(getattr(config, "smsall_sniper_count", 10), 10)))
    concurrency = max(1, min(10, _as_int(getattr(config, "smsall_sniper_concurrency", 10), 10)))
    attempts = max(1, min(500, _as_int(getattr(config, "smsall_sniper_max_number_attempts", 20), 20)))
    cooldown = max(0, _as_int(getattr(config, "smsall_sniper_cooldown_seconds", 60), 60))
    max_countries = max(1, min(10, _as_int(getattr(config, "smsall_sniper_max_countries", 3), 3)))
    use_item_price = getattr(config, "smsall_sniper_use_item_price_as_max", True)
    use_item_price = True if use_item_price is None else bool(use_item_price)
    country_caps = getattr(config, "smsall_sniper_price_caps", None) or []
    has_country_caps = bool(country_caps)

    launches: List[Dict[str, Any]] = []
    seen_launch_keys = set()
    for item in items:
        country = item["country"]
        price = item["price_usd"]
        launch_key = _sniper_launch_key(item)
        price_cap, cap_source = _resolve_sniper_price_cap(country, config)
        reason = None
        action = "logged"
        if not enabled:
            action = "received"
            reason = "sniper_disabled"
        elif launch_key in seen_launch_keys:
            reason = "duplicate_entry"
        elif _on_cooldown(country, now, cooldown, sniper=True, launch_key=launch_key):
            reason = "cooldown"
        elif len(launches) >= max_countries:
            reason = "country_cap"
        elif cap_source == "country_not_configured":
            reason = "country_not_in_caps"
        elif price is None and price_cap is not None:
            reason = "missing_price"
        elif price_cap is not None and price is not None and price > price_cap:
            reason = "price_above_cap"
        elif item.get("balance") is not None and (item.get("balance") or 0.0) <= 0:
            # ASSUMPTION：上游已按「有余额」过滤，这里只兜底挡住明确写 0 的条目
            reason = "upstream_no_balance"
        else:
            # ASSUMPTION：狙击要抢货，故意跳过普通通道的 busy 上限检查
            action = "launch"
            seen_launch_keys.add(launch_key)

        stamped = _stamp_record({
            "at": now,
            "action": action,
            "reason": reason,
            "source": SNIPER_TOKEN,
            "launch_key": launch_key,
            "price_cap_usd": price_cap,
            "price_cap_source": cap_source if has_country_caps or price_cap is not None else None,
            **item,
            "sniper": True,
        }, now)
        if action == "launch":
            stamped["max_number_attempts"] = attempts
            stamped["planned_count"] = count
        records.append(stamped)
        if action != "launch":
            continue

        # ASSUMPTION：本批出价用 item.priceUsd 上浮 10%（抢货时上游价常有抖动），
        # 不得越过按国/全局单价上限；两者都没有时回落全局 sms_max_price。
        batch_max_price: Optional[float] = None
        if use_item_price and price is not None:
            batch_max_price = round(price * 1.1, 4)
            if price_cap is not None:
                batch_max_price = min(batch_max_price, price_cap)
        elif price_cap is not None:
            batch_max_price = price_cap

        launches.append({
            "country": country,
            "count": count,
            "concurrency": min(concurrency, count),
            "price_usd": price,
            "provider": item["provider"],
            "provider_ref": item.get("provider_ref") or "",
            "supplier_ids": list(item.get("supplier_ids") or []),
            "sms_provider": resolve_sniper_sms_provider(item, config),
            "event_type": item["type"],
            "stock_to": item["stock_to"],
            "event_id": stamped["id"],
            "launch_key": launch_key,
            "sniper": True,
            "max_number_attempts": attempts,
            "max_price": batch_max_price,
            "cooldown_seconds": cooldown,
        })
    return launches


def decide_launches(
    payload: Dict[str, Any],
    config: Any,
    headers: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (launches, event_records)。launches 供真正开跑。"""
    body = normalize_webhook_payload(payload)
    now = time.time()
    auto = bool(getattr(config, "smsall_auto_register", False))
    max_price = _as_float(getattr(config, "smsall_auto_max_price_usd", None)) or 0.5
    min_stock = max(0, _as_int(getattr(config, "smsall_auto_min_stock", 1), 1))
    cooldown = max(0, _as_int(getattr(config, "smsall_auto_cooldown_seconds", 600), 600))
    max_countries = max(1, min(10, _as_int(getattr(config, "smsall_auto_max_countries", 2), 2)))
    count = max(1, min(10, _as_int(getattr(config, "smsall_auto_count", 3), 3)))
    concurrency = max(1, min(10, _as_int(getattr(config, "smsall_auto_concurrency", 3), 3)))

    records: List[Dict[str, Any]] = []
    launches: List[Dict[str, Any]] = []
    telegram = _service_is_telegram(body)
    if not telegram:
        records.append(_stamp_record({
            "at": now,
            "action": "ignored",
            "reason": "not_telegram",
            "service_key": body.get("serviceKey"),
        }, now))
        return [], records

    items = parse_items(body, headers=headers)
    sniper_items = [item for item in items if item.get("sniper")]
    normal_items = [item for item in items if not item.get("sniper")]
    # 同一 POST 混装时分别决策：狙击不看 smsall_auto_register，普通条目仍走原规则
    launches.extend(decide_sniper_launches(sniper_items, config, now, records))

    seen_countries = set()
    busy = _busy_task_count()
    normal_launches: List[Dict[str, Any]] = []
    for item in normal_items:
        country = item["country"]
        price = item["price_usd"]
        reason = None
        action = "logged"
        if not auto:
            action = "received"
            reason = "awaiting_confirm"
        elif price is None:
            reason = "missing_price"
        elif price > max_price:
            reason = "price_above_cap"
        elif item["stock_to"] < min_stock:
            reason = "stock_too_low"
        elif country in seen_countries:
            reason = "duplicate_country"
        elif _on_cooldown(country, now, cooldown):
            reason = "cooldown"
        elif len(normal_launches) >= max_countries:
            reason = "country_cap"
        elif busy + (len(normal_launches) + 1) * count >= 24:
            reason = "queue_busy"
        else:
            action = "launch"
            seen_countries.add(country)

        stamped = _stamp_record({
            "at": now,
            "action": action,
            "reason": reason,
            **item,
        }, now)
        records.append(stamped)
        if action == "launch":
            normal_launches.append({
                "country": country,
                "count": count,
                "concurrency": min(concurrency, count),
                "price_usd": price,
                "provider": item["provider"],
                "event_type": item["type"],
                "stock_to": item["stock_to"],
                "event_id": stamped["id"],
                "sniper": False,
                "cooldown_seconds": cooldown,
            })

    launches.extend(normal_launches)
    return launches, records


def remember_events(records: List[Dict[str, Any]]) -> None:
    with _LOCK:
        _ensure_loaded()
        for rec in records:
            _EVENTS.appendleft(_stamp_record(rec))
        _persist_unlocked()


def recent_events(limit: int = 40) -> List[Dict[str, Any]]:
    with _LOCK:
        _ensure_loaded()
        return [dict(item) for item in list(_EVENTS)[: max(1, min(int(limit or 40), MAX_EVENTS))]]


def event_count() -> int:
    with _LOCK:
        _ensure_loaded()
        return len(_EVENTS)


def delete_events(event_ids: Optional[List[str]] = None, clear_all: bool = False) -> int:
    """删除指定通知或清空全部。返回实际删除条数。"""
    with _LOCK:
        _ensure_loaded()
        if clear_all:
            deleted = len(_EVENTS)
            _EVENTS.clear()
            _persist_unlocked()
            return deleted
        wanted = {str(item).strip() for item in (event_ids or []) if str(item).strip()}
        if not wanted:
            return 0
        kept = [rec for rec in _EVENTS if str(rec.get("id") or "") not in wanted]
        deleted = len(_EVENTS) - len(kept)
        if deleted:
            _EVENTS.clear()
            _EVENTS.extend(kept)
            _persist_unlocked()
        return deleted


def get_event(event_id: str) -> Optional[Dict[str, Any]]:
    token = str(event_id or "").strip()
    if not token:
        return None
    with _LOCK:
        _ensure_loaded()
        for item in _EVENTS:
            if str(item.get("id") or "") == token:
                return dict(item)
    return None


def attach_batch(
    event_id: Optional[str],
    country: str,
    batch_id: str,
    task_ids: Optional[List[str]] = None,
    source: str = "trial",
) -> Optional[Dict[str, Any]]:
    """把一键测试 / 自动开跑的 batch 记回对应通知。"""
    token = str(event_id or "").strip()
    iso = str(country or "").strip().lower()
    action = "trial" if source == "trial" else "launch"
    with _LOCK:
        _ensure_loaded()
        target = None
        if token:
            for rec in _EVENTS:
                if str(rec.get("id") or "") == token:
                    target = rec
                    break
        if target is None and iso:
            for rec in _EVENTS:
                if str(rec.get("country") or "") == iso and not rec.get("batch_id"):
                    target = rec
                    break
        if target is None:
            return None
        target["batch_id"] = batch_id
        target["task_ids"] = list(task_ids or [])
        target["trial_source"] = source
        if source == SNIPER_TOKEN:
            target["sniper"] = True
            target["source"] = SNIPER_TOKEN
        if source == "trial":
            target["action"] = "trial"
            target["reason"] = None
        elif target.get("action") != "launch":
            target["action"] = action
        _persist_unlocked()
        return dict(target)


def reset_state() -> None:
    global _LOADED, _PERSIST_ENABLED
    with _LOCK:
        _EVENTS.clear()
        _COOLDOWN_UNTIL.clear()
        _LOADED = True
        _PERSIST_ENABLED = False


def mark_launched(launches: List[Dict[str, Any]], cooldown: float) -> None:
    now = time.time()
    with _LOCK:
        for item in launches:
            country = item.get("country")
            if not country:
                continue
            sniper = bool(item.get("sniper"))
            own = _as_int(item.get("cooldown_seconds"), -1)
            launch_key = item.get("launch_key") if sniper else None
            _mark_cooldown(
                str(country),
                now,
                own if own >= 0 else cooldown,
                sniper=sniper,
                launch_key=str(launch_key) if launch_key else None,
            )


def ingest(payload: Any, config: Any, headers: Any = None) -> Dict[str, Any]:
    body = normalize_webhook_payload(payload)
    launches, records = decide_launches(body, config, headers=headers)
    remember_events(records)
    cooldown = max(0, _as_int(getattr(config, "smsall_auto_cooldown_seconds", 600), 600))
    sniper_launches = [item for item in launches if item.get("sniper")]
    if launches:
        mark_launched(launches, cooldown)
        logger.info(
            "SMSBazaar webhook 将自动开跑: %s",
            ", ".join(f"{x['country']} ${x.get('price_usd')}" for x in launches),
        )
    else:
        logger.info("SMSBazaar webhook 已接收，本轮无自动开跑（%s 条）", len(records))
    for item in sniper_launches:
        # ASSUMPTION：接码源仍用全局 config.sms_provider，不按 item.provider 自动切换；
        # 上游 provider 只打日志供人工核对映射。
        logger.warning(
            "SMSBazaar 狙击命中 %s：%s 路 × 每任务最多取号 %s 次，本批出价 %s，"
            "supplierIds=%s providerRef=%s，上游平台=%s(%s)，本批接码源=%s（全局=%s）",
            str(item.get("country") or "").upper(),
            item.get("count"),
            item.get("max_number_attempts"),
            item.get("max_price"),
            item.get("supplier_ids") or "-",
            item.get("provider_ref") or "-",
            item.get("provider") or "-",
            item.get("price_usd"),
            item.get("sms_provider") or getattr(config, "sms_provider", None),
            getattr(config, "sms_provider", None),
        )
    return {
        "accepted": True,
        "schema": body.get("schema") or SCHEMA,
        "item_count": len(body.get("items") or []) if isinstance(body.get("items"), list) else 0,
        "launches": launches,
        "sniper_launches": len(sniper_launches),
        "events": records,
    }
