"""动态接码库存与价格发现服务。

目标拓扑不再写死国家白名单：直接询问接码平台「哪些国家此刻有 Telegram 货」，
按实时库存量降序返回 ISO-2 / 区号 / 国名 / 单价。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.app.services.geo_catalog import (
    enrich_country,
    infer_locale,
    iso2_flag,
    resolve_iso2,
    smsactivate_id_to_iso2,
)

logger = logging.getLogger("SmsStockService")

DEFAULT_SERVICE = "tg"
CACHE_TTL_SECONDS = 90.0  # 60~120 秒轻量缓存
PROVIDER_FIVESIM = "fivesim"
PROVIDER_GRIZZLY = "grizzlysms"
PROVIDER_SMSBOWER = "smsbower"
PROVIDER_SMSCODE = "smscode"
PROVIDER_VAK = "vaksms"


def normalize_sms_provider(value: Optional[str]) -> str:
    token = str(value or "").strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "fivesim": PROVIDER_FIVESIM,
        "5sim": PROVIDER_FIVESIM,
        "5simnet": PROVIDER_FIVESIM,
        "fivesimnet": PROVIDER_FIVESIM,
        "grizzly": PROVIDER_GRIZZLY,
        "grizzlysms": PROVIDER_GRIZZLY,
        "grizzlysmscom": PROVIDER_GRIZZLY,
        "smsbower": PROVIDER_SMSBOWER,
        "smsbowerapp": PROVIDER_SMSBOWER,
        "bower": PROVIDER_SMSBOWER,
        "smscode": PROVIDER_SMSCODE,
        "smscodegg": PROVIDER_SMSCODE,
        "smscode.gg": PROVIDER_SMSCODE,
        "vak": PROVIDER_VAK,
        "vaksms": PROVIDER_VAK,
    }
    return aliases.get(token, PROVIDER_FIVESIM)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _service_node(bucket: Any, service: str) -> Optional[Dict[str, Any]]:
    if not isinstance(bucket, dict):
        return None
    node = bucket.get(service)
    if isinstance(node, dict):
        return node
    if "count" in bucket or "cost" in bucket or "price" in bucket:
        return bucket
    return None


def parse_grizzly_price_payload(
    data: Any,
    service: str = DEFAULT_SERVICE,
) -> List[Dict[str, Any]]:
    """全量解析 Grizzly / SMS-Activate getPrices 响应，仅保留 count > 0。

    兼容两种官方形态：
      {"151": {"tg": {"cost": 0.26, "count": 23603}}, ...}
      {"tg": {"151": {"cost": 0.26, "count": 23603}}, ...}
    """
    if not isinstance(data, dict) or not data:
        return []

    rows: List[Dict[str, Any]] = []

    svc_map = data.get(service)
    if isinstance(svc_map, dict) and svc_map and not _service_node(svc_map, service):
        # {service: {country: {count, cost}}}
        iterable = svc_map.items()
        for cid, node in iterable:
            if not isinstance(node, dict):
                continue
            count = _as_int(node.get("count") or node.get("phones") or 0)
            if count <= 0:
                continue
            rows.append({
                "provider_country_id": str(cid),
                "stock": count,
                "cost": _as_float(node.get("cost") if node.get("cost") is not None else node.get("price")),
            })
        if rows:
            return rows

    for cid, bucket in data.items():
        node = _service_node(bucket, service)
        if not node:
            continue
        count = _as_int(node.get("count") or node.get("phones") or 0)
        if count <= 0:
            continue
        rows.append({
            "provider_country_id": str(cid),
            "stock": count,
            "cost": _as_float(node.get("cost") if node.get("cost") is not None else node.get("price")),
        })
    return rows


def parse_vak_count_payload(
    data: Any,
    service: str = DEFAULT_SERVICE,
) -> List[Dict[str, Any]]:
    """动态聚合 Vak-SMS getCountNumber / getCountNumbers 有货国家。

    兼容：
      {"cl": {"tg": 1200}, "id": {"tg": 800}}
      {"tg": {"cl": 1200, "id": 800}}
      {"cl": 1200, "id": 800}
      [{"country": "cl", "tg": 1200, "count": 1200}]
    """
    if data is None:
        return []

    rows: List[Dict[str, Any]] = []

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            country = item.get("country") or item.get("code") or item.get("iso")
            count = _as_int(item.get(service) or item.get("count") or item.get("stock") or 0)
            if not country or count <= 0:
                continue
            rows.append({
                "provider_country_id": str(country),
                "stock": count,
                "cost": _as_float(item.get("cost") if item.get("cost") is not None else item.get("price")),
            })
        return rows

    if not isinstance(data, dict):
        return []

    svc_map = data.get(service)
    if isinstance(svc_map, dict):
        # 排除 {tg: {count, cost}} 单国家形态
        looks_multi = any(
            (isinstance(v, (int, float, str)) and str(k).lower() not in {"count", "cost", "price"})
            or isinstance(v, dict)
            for k, v in svc_map.items()
        )
        if looks_multi and not (("count" in svc_map or "cost" in svc_map) and len(svc_map) <= 3):
            for country, node in svc_map.items():
                if isinstance(node, dict):
                    count = _as_int(node.get("count") or node.get(service) or node.get("stock") or 0)
                    cost = _as_float(node.get("cost") if node.get("cost") is not None else node.get("price"))
                else:
                    count = _as_int(node)
                    cost = 0.0
                if count <= 0:
                    continue
                rows.append({
                    "provider_country_id": str(country),
                    "stock": count,
                    "cost": cost,
                })
            if rows:
                return rows

    for country, node in data.items():
        if str(country).lower() in {service, "status", "error", "msg", "message"}:
            continue
        if isinstance(node, dict):
            count = _as_int(node.get(service) or node.get("count") or node.get("stock") or 0)
            cost = _as_float(node.get("cost") if node.get("cost") is not None else node.get("price"))
        else:
            count = _as_int(node)
            cost = 0.0
        if count <= 0:
            continue
        rows.append({
            "provider_country_id": str(country),
            "stock": count,
            "cost": cost,
        })
    return rows


def parse_smscode_price_payload(
    data: Any,
    service: str = DEFAULT_SERVICE,
) -> List[Dict[str, Any]]:
    """解析 SMSCode.gg /catalog/products 聚合结果。"""
    _ = service
    from backend.app.services.smscode import parse_smscode_products_payload as _parse

    if isinstance(data, dict):
        return _parse(data.get("products"), data.get("countries"))
    return _parse(data, None)


def parse_fivesim_price_payload(
    data: Any,
    product: str = "telegram",
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """解析 5SIM guest/prices，仅保留 count > 0 的国家。"""
    from backend.app.services.fivesim import parse_fivesim_price_payload as _parse

    return _parse(data, product=product, country=country)


def _resolve_stock_iso2(provider: str, provider_country_id: Any) -> str:
    token = str(provider_country_id or "").strip()
    if not token:
        return ""
    if provider == PROVIDER_FIVESIM:
        from backend.app.services.fivesim import fivesim_country_to_iso

        iso = fivesim_country_to_iso(token)
        if iso:
            return iso
    if provider in {PROVIDER_GRIZZLY, PROVIDER_SMSBOWER}:
        from backend.app.services.grizzlysms import grizzly_country_id_to_iso

        iso = grizzly_country_id_to_iso(token)
        if iso:
            return iso
        iso = smsactivate_id_to_iso2(token)
        if iso:
            return iso
    if provider == PROVIDER_SMSCODE:
        iso = resolve_iso2(token)
        if iso:
            return iso
    iso = resolve_iso2(token)
    if iso:
        return iso
    if token.isdigit():
        iso = smsactivate_id_to_iso2(token)
        if iso:
            return iso
    return token.lower()


def enrich_stock_rows(
    rows: List[Dict[str, Any]],
    provider: str,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in rows:
        pid = row.get("provider_country_id")
        iso = _resolve_stock_iso2(provider, pid)
        locale = infer_locale(iso)
        code = locale.get("code") or iso or str(pid or "").lower()
        items.append({
            "code": code,
            "name": locale.get("name") or str(code).upper(),
            "name_zh": locale.get("name_zh") or "",
            "dial": locale.get("dial") or "",
            "flag": locale.get("flag") or iso2_flag(code),
            "stock": _as_int(row.get("stock")),
            "cost": _as_float(row.get("cost")),
            "provider": provider,
            "provider_country_id": str(pid) if pid is not None else "",
            "lang_code": locale.get("lang_code"),
            "system_lang_code": locale.get("system_lang_code"),
            "tz_offset": locale.get("tz_offset"),
        })
    items.sort(key=lambda item: (-int(item.get("stock") or 0), str(item.get("code") or "")))
    return items


@dataclass
class SmsStockSnapshot:
    provider: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    total_countries: int = 0
    total_stock: int = 0
    updated_at: float = 0.0
    cached: bool = False
    cache_age_seconds: float = 0.0
    service: str = DEFAULT_SERVICE
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": True,
            "provider": self.provider,
            "service": self.service,
            "items": self.items,
            "total_countries": self.total_countries,
            "total_stock": self.total_stock,
            "updated_at": self.updated_at,
            "cached": self.cached,
            "cache_age_seconds": round(self.cache_age_seconds, 3),
            "message": self.message,
        }


_CACHE: Dict[str, Tuple[float, SmsStockSnapshot]] = {}


def reset_stock_cache() -> None:
    _CACHE.clear()


def _cache_key(provider: str, service: str) -> str:
    return f"{normalize_sms_provider(provider)}:{service or DEFAULT_SERVICE}"


class SmsStockService:
    """接码平台 Telegram 实时有货国家发现器。"""

    CACHE_TTL_SECONDS = CACHE_TTL_SECONDS

    @classmethod
    def peek_cache(
        cls,
        provider: str,
        service: str = DEFAULT_SERVICE,
        allow_stale: bool = False,
    ) -> Optional[SmsStockSnapshot]:
        key = _cache_key(provider, service)
        hit = _CACHE.get(key)
        if not hit:
            return None
        ts, snap = hit
        age = time.time() - ts
        if age > cls.CACHE_TTL_SECONDS and not allow_stale:
            return None
        cached = SmsStockSnapshot(
            provider=snap.provider,
            items=list(snap.items),
            total_countries=snap.total_countries,
            total_stock=snap.total_stock,
            updated_at=snap.updated_at,
            cached=True,
            cache_age_seconds=age,
            service=snap.service,
            message=snap.message,
        )
        return cached

    @classmethod
    async def get_available_countries(
        cls,
        provider: Optional[str] = None,
        refresh: bool = False,
        service: str = DEFAULT_SERVICE,
        api_key: Optional[str] = None,
        config: Any = None,
    ) -> SmsStockSnapshot:
        resolved = normalize_sms_provider(provider)
        key = _cache_key(resolved, service)
        if not refresh:
            cached = cls.peek_cache(resolved, service)
            if cached is not None:
                return cached

        try:
            if resolved == PROVIDER_VAK:
                items = await cls._fetch_vaksms(service=service, api_key=api_key, config=config)
            elif resolved == PROVIDER_FIVESIM:
                items = await cls._fetch_fivesim(service=service, api_key=api_key, config=config)
            elif resolved == PROVIDER_SMSBOWER:
                items = await cls._fetch_smsbower(service=service, api_key=api_key, config=config)
            elif resolved == PROVIDER_SMSCODE:
                items = await cls._fetch_smscode(service=service, api_key=api_key, config=config)
            else:
                items = await cls._fetch_grizzly(service=service, api_key=api_key, config=config)
        except Exception as exc:
            stale = cls.peek_cache(resolved, service, allow_stale=True)
            if stale is not None:
                stale.message = f"接码平台刷新失败，返回上次有货快照: {exc}"
                logger.warning("库存发现失败，回落缓存 provider=%s: %s", resolved, exc)
                return stale
            raise

        now = time.time()
        total_stock = sum(int(item.get("stock") or 0) for item in items)
        snap = SmsStockSnapshot(
            provider=resolved,
            items=items,
            total_countries=len(items),
            total_stock=total_stock,
            updated_at=now,
            cached=False,
            cache_age_seconds=0.0,
            service=service or DEFAULT_SERVICE,
            message=(
                f"{resolved} 实时有货 {len(items)} 国 / {total_stock} 号"
                if items else f"{resolved} 当前无 Telegram 有货国家"
            ),
        )
        _CACHE[key] = (now, snap)
        return snap

    @classmethod
    async def _fetch_grizzly(
        cls,
        service: str,
        api_key: Optional[str],
        config: Any,
    ) -> List[Dict[str, Any]]:
        from backend.app.services.grizzlysms import GrizzlySmsService

        key = (api_key or "").strip()
        if not key and config is not None:
            key = str(getattr(config, "grizzly_sms_api_key", "") or "").strip()
        svc = GrizzlySmsService(key)
        try:
            payload = await svc.get_prices(country=None, service=service)
            rows = parse_grizzly_price_payload(payload, service=service)
            return enrich_stock_rows(rows, PROVIDER_GRIZZLY)
        finally:
            await svc.close()

    @classmethod
    async def _fetch_smsbower(
        cls,
        service: str,
        api_key: Optional[str],
        config: Any,
    ) -> List[Dict[str, Any]]:
        from backend.app.services.smsbower import SmsBowerService

        key = (api_key or "").strip()
        if not key and config is not None:
            key = str(getattr(config, "smsbower_api_key", "") or "").strip()
        svc = SmsBowerService(key)
        try:
            payload = await svc.get_prices(country=None, service=service)
            rows = parse_grizzly_price_payload(payload, service=service)
            return enrich_stock_rows(rows, PROVIDER_SMSBOWER)
        finally:
            await svc.close()

    @classmethod
    async def _fetch_smscode(
        cls,
        service: str,
        api_key: Optional[str],
        config: Any,
    ) -> List[Dict[str, Any]]:
        from backend.app.services.smscode import SmsCodeService

        key = (api_key or "").strip()
        if not key and config is not None:
            key = str(getattr(config, "smscode_api_key", "") or "").strip()
        svc = SmsCodeService(key)
        try:
            payload = await svc.get_prices(country=None, service=service)
            rows = parse_smscode_price_payload(payload, service=service)
            return enrich_stock_rows(rows, PROVIDER_SMSCODE)
        finally:
            await svc.close()

    @classmethod
    async def _fetch_fivesim(
        cls,
        service: str,
        api_key: Optional[str],
        config: Any,
    ) -> List[Dict[str, Any]]:
        from backend.app.services.fivesim import FiveSimService, resolve_product

        key = (api_key or "").strip()
        if not key and config is not None:
            key = str(getattr(config, "fivesim_api_key", "") or "").strip()
        product = resolve_product(service)
        svc = FiveSimService(key)
        try:
            payload = await svc.get_prices(country=None, product=product)
            rows = parse_fivesim_price_payload(payload, product=product)
            return enrich_stock_rows(rows, PROVIDER_FIVESIM)
        finally:
            await svc.close()

    @classmethod
    async def _fetch_vaksms(
        cls,
        service: str,
        api_key: Optional[str],
        config: Any,
    ) -> List[Dict[str, Any]]:
        from backend.app.services.vaksms import VakSmsService

        key = (api_key or "").strip()
        if not key and config is not None:
            key = str(getattr(config, "vak_sms_api_key", "") or "").strip()
        svc = VakSmsService(key)
        try:
            payload = await svc.get_all_stock_counts(service=service)
            if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "stock" in payload[0]:
                rows = payload
            else:
                rows = parse_vak_count_payload(payload, service=service)
            return enrich_stock_rows(rows, PROVIDER_VAK)
        finally:
            await svc.close()


# 保持 enrich_country 可被测试 / 前端预览复用
def preview_country(code: str) -> Dict[str, Any]:
    return enrich_country(code)
