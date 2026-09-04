"""SMSCode.gg 接码客户端。

官方文档: https://smscode.gg/docs
本模块走 USD 投影的 /v2 API（与控制台 sms_max_price 美元出价对齐）：

  Base URL: https://api.smscode.gg/v2
  鉴权:     Authorization: Bearer <token>
  余额:     GET  /balance
  国家:     GET  /catalog/countries
  服务:     GET  /catalog/services
  价/库存:  GET  /catalog/products
  租号:     POST /orders/create   (catalog_product_id + max_price)
  查码:     GET  /orders/{id}
  取消退款: POST /orders/cancel
  完结:     POST /orders/finish
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urlparse

import httpx

from backend.app.models.schemas import format_sms_max_price, normalize_sms_max_price
from backend.app.services.geo_catalog import resolve_iso2
from backend.app.services.vaksms import NoNumberAvailableError, is_no_number_error

logger = logging.getLogger("SmsCodeService")

BASE_URL = "https://api.smscode.gg/v2"
PROVIDER_NAME = "smscode"
PROVIDER_LABEL = "SMSCode (smscode.gg)"
DEFAULT_SERVICE = "tg"
TELEGRAM_SERVICE_CODES = frozenset({"tg", "telegram", "tele", "telegramorg"})
NO_OFFER_CODES = frozenset({"NO_OFFER_AVAILABLE", "NO_NUMBERS", "NO_NUMBER"})
NO_BALANCE_CODES = frozenset({"INSUFFICIENT_BALANCE"})
BAD_KEY_CODES = frozenset({"UNAUTHORIZED", "FORBIDDEN"})
CANCEL_TOO_EARLY_CODES = frozenset({"CANCEL_TOO_EARLY"})
# 官方：租号后通常需等待约 120s 才允许 /orders/cancel；过早取消返回 409 CANCEL_TOO_EARLY
DEFAULT_CANCEL_RETRY_AFTER_SECONDS = 120.0
CANCEL_RETRY_AFTER_RE = re.compile(
    r"(?:wait|waiting|请再?等待?|需等待?)\s*(\d+)\s*(?:more\s+)?(?:seconds?|secs?|秒)",
    re.IGNORECASE,
)
TERMINAL_FAIL_STATUSES = frozenset({"CANCELED", "CANCELLED", "EXPIRED", "FAILED"})
PROVIDER_NO_NUMBER_CAUSES = frozenset({
    "no_numbers",
    "no_number",
    "price_rejected",
    "provider_unavailable",
})


class SmsCodeError(RuntimeError):
    """SMSCode 协议层通用异常。"""

    def __init__(self, message: str, code: str = "", status_code: int = 0):
        super().__init__(message)
        self.code = str(code or "")
        self.status_code = int(status_code or 0)


class InsufficientBalanceError(RuntimeError):
    """SMSCode 返回 INSUFFICIENT_BALANCE：账户余额不足以租号。"""

    def __init__(self, raw: Any = None):
        self.raw = raw
        super().__init__(f"SMSCode 账户余额不足 (INSUFFICIENT_BALANCE): {raw}")


def mask_api_key(key: Any) -> str:
    """日志脱敏：只保留前后缀。"""
    raw = str(key or "").strip()
    if not raw:
        return "<empty>"
    if len(raw) <= 12:
        return f"{raw[:2]}***{raw[-2:]}" if len(raw) >= 4 else "***"
    return f"{raw[:6]}...{raw[-4:]}"


def parse_cancel_retry_after(message: Any, default: float = DEFAULT_CANCEL_RETRY_AFTER_SECONDS) -> float:
    """从 CANCEL_TOO_EARLY 文案解析还需等待的秒数。"""
    text = str(message or "")
    match = CANCEL_RETRY_AFTER_RE.search(text)
    if match:
        try:
            return max(1.0, float(match.group(1)))
        except (TypeError, ValueError):
            pass
    # 兜底：文案里单独的「120 seconds / 120s」
    loose = re.search(r"(\d+)\s*(?:more\s+)?(?:seconds?|secs?|秒)", text, re.IGNORECASE)
    if loose:
        try:
            return max(1.0, float(loose.group(1)))
        except (TypeError, ValueError):
            pass
    return float(default)


def parse_money_usd(value: Any) -> Optional[float]:
    """解析 v2 money 对象或裸数字为 USD float。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        amount = value.get("amount")
        if amount is not None and str(amount).strip() != "":
            try:
                return float(amount)
            except (TypeError, ValueError):
                return None
        canonical = value.get("canonical_amount")
        rate = value.get("rate")
        if canonical is not None and rate:
            try:
                return float(canonical) / float(rate)
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(phone or "").strip())
    if not digits:
        return str(phone or "").strip()
    if digits.startswith("+"):
        return digits
    return "+" + digits.lstrip("00")


def _compact(text: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "")).lower()


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _envelope_error(payload: Any) -> Tuple[str, str, Any]:
    if not isinstance(payload, dict):
        return "", str(payload or ""), None
    err = payload.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "").strip()
        message = str(err.get("message") or err.get("msg") or code or "").strip()
        return code, message, err.get("details")
    if payload.get("success") is False:
        code = str(payload.get("code") or "").strip()
        message = str(payload.get("message") or payload.get("msg") or code or "").strip()
        return code, message, payload.get("details")
    return "", "", None


def _provider_error_is_no_number(details: Any) -> bool:
    if not isinstance(details, dict):
        return False
    cause_counts = details.get("cause_counts")
    if isinstance(cause_counts, dict):
        total = sum(_as_int(v, 0) or 0 for v in cause_counts.values())
        no_num = sum(
            _as_int(v, 0) or 0
            for k, v in cause_counts.items()
            if _compact(k) in {_compact(x) for x in PROVIDER_NO_NUMBER_CAUSES}
        )
        if total > 0 and no_num == total:
            return True
        if (_as_int(cause_counts.get("no_numbers"), 0) or 0) > 0 and no_num == total:
            return True
    attempts = details.get("attempts")
    if isinstance(attempts, list) and attempts:
        causes = []
        for item in attempts:
            if isinstance(item, dict):
                causes.append(_compact(item.get("cause") or item.get("result") or ""))
            else:
                causes.append(_compact(item))
        if causes and all(c in {_compact(x) for x in PROVIDER_NO_NUMBER_CAUSES} or c == "nonumber" for c in causes):
            return True
    return False


def _is_no_offer(code: str, message: str, details: Any = None) -> bool:
    token = (code or "").strip().upper()
    if token in NO_OFFER_CODES:
        return True
    if token == "PROVIDER_ERROR" and _provider_error_is_no_number(details):
        return True
    if is_no_number_error(message) or is_no_number_error(code):
        return True
    compact = _compact(message)
    return "nooffer" in compact or "nonumber" in compact or "noavailableoffer" in compact


def parse_smscode_products_payload(
    products: Any,
    countries: Optional[Iterable[Any]] = None,
) -> List[Dict[str, Any]]:
    """把 /catalog/products 聚合成库存行：ISO-2 / stock / cost。仅保留 available>0。"""
    id_to_iso: Dict[str, str] = {}
    for row in countries or []:
        if not isinstance(row, dict):
            continue
        cid = row.get("id")
        code = str(row.get("code") or "").strip().lower()
        if cid is None or not code:
            continue
        id_to_iso[str(cid)] = "gb" if code == "uk" else code

    buckets: Dict[str, Dict[str, Any]] = {}
    if not isinstance(products, list):
        return []
    for item in products:
        if not isinstance(item, dict):
            continue
        if item.get("active") is False:
            continue
        available = _as_int(item.get("available"), 0) or 0
        if available <= 0:
            continue
        country_id = item.get("country_id")
        iso = id_to_iso.get(str(country_id), "")
        if not iso:
            iso = str(country_id or "").strip().lower()
        if not iso:
            continue
        cost = parse_money_usd(item.get("price")) or 0.0
        bucket = buckets.get(iso)
        if bucket is None:
            buckets[iso] = {
                "provider_country_id": iso,
                "country_id": country_id,
                "stock": available,
                "cost": cost,
                "catalog_product_id": item.get("catalog_product_id"),
                "product_id": item.get("id"),
            }
            continue
        bucket["stock"] = int(bucket.get("stock") or 0) + available
        prev = float(bucket.get("cost") or 0)
        if cost > 0 and (prev <= 0 or cost < prev):
            bucket["cost"] = cost
            bucket["catalog_product_id"] = item.get("catalog_product_id")
            bucket["product_id"] = item.get("id")
    return list(buckets.values())


class SmsCodeService:
    """SMSCode.gg 异步接码客户端，接口对齐 VakSms / Grizzly / 5SIM 契约。"""

    BASE_URL = BASE_URL
    PROVIDER_NAME = PROVIDER_NAME
    PROVIDER_LABEL = PROVIDER_LABEL

    def __init__(
        self,
        api_key: str = "",
        timeout: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        token = (api_key or "").strip()
        if not token:
            token = (
                os.environ.get("SMSCODE_API_KEY")
                or os.environ.get("SMSCODE_TOKEN")
                or ""
            ).strip()
        self.api_key = token
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))
        self._countries_cache: Optional[List[Dict[str, Any]]] = None
        self._services_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._telegram_platform_cache: Dict[str, Dict[str, Any]] = {}

    async def __aenter__(self) -> "SmsCodeService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if not self._owns_client:
            return
        client = getattr(self, "client", None)
        if client is None:
            return
        try:
            if not client.is_closed:
                await client.aclose()
        except Exception as exc:
            logger.warning("释放 %s httpx 客户端失败: %s", self.PROVIDER_LABEL, exc)

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise SmsCodeError(f"未配置 {self.PROVIDER_LABEL} API Key（Settings 填 smscode_api_key 或环境变量 SMSCODE_API_KEY）")

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        self._require_api_key()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if extra:
            headers.update(extra)
        return headers

    def _raise_protocol_error(
        self,
        payload: Any,
        status_code: int,
        raw: str,
        country: Optional[str] = None,
    ) -> None:
        code, message, details = _envelope_error(payload)
        text = message or (raw[:300] if raw else str(status_code))
        if code in BAD_KEY_CODES or status_code in {401, 403}:
            raise SmsCodeError(
                f"{self.PROVIDER_LABEL} API Key 无效 ({code or status_code}, key={mask_api_key(self.api_key)}): {text}",
                code=code or "UNAUTHORIZED",
                status_code=status_code,
            )
        if code in NO_BALANCE_CODES:
            raise InsufficientBalanceError(text or payload or raw)
        if _is_no_offer(code, text, details):
            iso = resolve_iso2(country) or str(country or "?")
            raise NoNumberAvailableError(iso, text or payload or raw)
        if status_code == 429 or code == "RATE_LIMIT_EXCEEDED":
            raise SmsCodeError(
                f"{self.PROVIDER_LABEL} 触发限流 ({code or 429}): {text}",
                code=code or "RATE_LIMIT_EXCEEDED",
                status_code=status_code or 429,
            )
        raise SmsCodeError(
            f"{self.PROVIDER_LABEL} HTTP {status_code or '?'} {code or ''}: {text}".strip(),
            code=code,
            status_code=status_code,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        country: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        retry_on_rate_limit: bool = True,
    ) -> Dict[str, Any]:
        self._require_api_key()
        url = path if str(path).startswith("http") else f"{self.BASE_URL}{path}"
        headers = self._headers(extra_headers)
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None} or None
        parsed = urlparse(url)
        log_path = parsed.path or path
        logger.info(
            "SMSCode %s %s key=%s",
            method.upper(),
            log_path,
            mask_api_key(self.api_key),
        )
        try:
            resp = await self.client.request(
                method.upper(),
                url,
                params=clean_params,
                json=json_body,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise SmsCodeError(f"{self.PROVIDER_LABEL} 请求超时: {method.upper()} {log_path}") from exc
        except httpx.HTTPError as exc:
            raise SmsCodeError(f"{self.PROVIDER_LABEL} 网络错误: {method.upper()} {log_path}: {exc}") from exc

        raw = (resp.text or "").strip()
        try:
            payload = resp.json() if raw else {}
        except Exception:
            payload = raw

        if resp.status_code == 429 and retry_on_rate_limit:
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = min(max(float(retry_after), 0.2), 5.0) if retry_after else 1.0
            except (TypeError, ValueError):
                delay = 1.0
            logger.warning("SMSCode 限流，%.1fs 后重试 path=%s", delay, log_path)
            await asyncio.sleep(delay)
            return await self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                extra_headers=extra_headers,
                country=country,
                idempotency_key=idempotency_key,
                retry_on_rate_limit=False,
            )

        if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("success") is False):
            self._raise_protocol_error(payload, resp.status_code, raw, country=country)
        if not isinstance(payload, dict):
            raise SmsCodeError(f"{self.PROVIDER_LABEL} 返回非 JSON: {raw[:300]}")
        return payload

    async def get_balance(self) -> float:
        payload = await self._request("GET", "/balance")
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            money = parse_money_usd(data.get("balance"))
            if money is not None:
                return money
            money = parse_money_usd(data)
            if money is not None:
                return money
        money = parse_money_usd(payload.get("balance") if isinstance(payload, dict) else None)
        if money is not None:
            return money
        raise SmsCodeError(f"解析余额失败: {payload}")

    query_telemetry_quota = get_balance

    async def get_countries(self, force: bool = False) -> List[Dict[str, Any]]:
        if self._countries_cache is not None and not force:
            return self._countries_cache
        payload = await self._request("GET", "/catalog/countries")
        data = payload.get("data")
        rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        self._countries_cache = rows
        return rows

    async def get_services(
        self,
        country_id: Optional[int] = None,
        force: bool = False,
    ) -> List[Dict[str, Any]]:
        cache_key = str(country_id or "all")
        if cache_key in self._services_cache and not force:
            return self._services_cache[cache_key]
        params = {"country_id": country_id} if country_id is not None else None
        payload = await self._request("GET", "/catalog/services", params=params)
        data = payload.get("data")
        rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        self._services_cache[cache_key] = rows
        return rows

    async def resolve_country_id(self, country: Union[str, int, None]) -> int:
        if country is None or str(country).strip() == "":
            raise SmsCodeError("未指定国家")
        if isinstance(country, int) or str(country).strip().isdigit():
            cid = int(country)
            rows = await self.get_countries()
            if any(int(row.get("id") or -1) == cid for row in rows):
                return cid
            if rows:
                raise SmsCodeError(f"无法将国家 '{country}' 映射到 SMSCode country_id")
            return cid

        raw = str(country).strip()
        iso = resolve_iso2(raw) or raw.lower()
        if iso == "uk":
            iso = "gb"
        rows = await self.get_countries()
        for row in rows:
            code = str(row.get("code") or "").strip().lower()
            if code == "uk":
                code = "gb"
            if code == iso:
                return int(row["id"])
        name_key = _compact(raw)
        for row in rows:
            if _compact(row.get("name")) == name_key:
                return int(row["id"])
        raise SmsCodeError(f"无法将国家 '{country}' 映射到 SMSCode country_id")

    async def resolve_country_iso2(self, country: Union[str, int, None]) -> str:
        if country is None:
            return ""
        iso = resolve_iso2(country)
        if iso:
            return "gb" if iso == "uk" else iso
        if str(country).strip().isdigit() or isinstance(country, int):
            cid = int(country)
            for row in await self.get_countries():
                if int(row.get("id") or -1) == cid:
                    code = str(row.get("code") or "").strip().lower()
                    return "gb" if code == "uk" else code
        return str(country or "").strip().lower()

    async def _resolve_telegram_platform(
        self,
        service: str = DEFAULT_SERVICE,
        country_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        wanted = _compact(service or DEFAULT_SERVICE)
        cache_key = f"{wanted}:{country_id or 'all'}"
        cached = self._telegram_platform_cache.get(cache_key)
        if cached:
            return cached
        rows = await self.get_services(country_id=country_id)
        picked = None
        for row in rows:
            if row.get("active") is False:
                continue
            code = _compact(row.get("code"))
            name = _compact(row.get("name"))
            if code == wanted or code in TELEGRAM_SERVICE_CODES or "telegram" in name:
                picked = row
                if code in TELEGRAM_SERVICE_CODES or code == wanted:
                    break
        if picked is None:
            raise SmsCodeError(
                f"{self.PROVIDER_LABEL} 目录中未找到 Telegram 服务 (service={service or DEFAULT_SERVICE})"
            )
        self._telegram_platform_cache[cache_key] = picked
        return picked

    async def list_products(
        self,
        country_id: Optional[int] = None,
        platform_id: Optional[int] = None,
        operator_id: Optional[int] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 1
        page_limit = max(1, min(int(limit or 10000), 10000))
        while page <= 20:
            params: Dict[str, Any] = {
                "sort": "price_asc",
                "limit": page_limit,
                "page": page,
            }
            if country_id is not None:
                params["country_id"] = country_id
            if platform_id is not None:
                params["platform_id"] = platform_id
            if operator_id is not None:
                params["operator_id"] = operator_id
            payload = await self._request("GET", "/catalog/products", params=params)
            data = payload.get("data")
            batch = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
            items.extend(batch)
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            total = _as_int(meta.get("count"), None)
            if len(batch) < page_limit:
                break
            if total is not None and len(items) >= total:
                break
            page += 1
        return items

    async def get_prices(
        self,
        country: Union[str, int, None] = None,
        service: str = DEFAULT_SERVICE,
    ) -> Dict[str, Any]:
        country_id = None
        if country is not None and str(country).strip() != "":
            country_id = await self.resolve_country_id(country)
        platform = await self._resolve_telegram_platform(service, country_id=country_id)
        products = await self.list_products(
            country_id=country_id,
            platform_id=_as_int(platform.get("id")),
        )
        countries = await self.get_countries()
        return {
            "products": products,
            "countries": countries,
            "platform": platform,
            "country_id": country_id,
        }

    async def get_all_prices(self, service: str = DEFAULT_SERVICE) -> Dict[str, Any]:
        return await self.get_prices(country=None, service=service)

    async def get_stock_count(
        self,
        country: Union[str, int] = "id",
        service: str = DEFAULT_SERVICE,
    ) -> int:
        try:
            payload = await self.get_prices(country=country, service=service)
            rows = parse_smscode_products_payload(
                payload.get("products"),
                payload.get("countries"),
            )
        except Exception as exc:
            logger.warning("查询 SMSCode 库存失败: %s", exc)
            return 0
        if not rows:
            return 0
        iso = await self.resolve_country_iso2(country)
        total = 0
        for row in rows:
            if iso and str(row.get("provider_country_id") or "") != iso:
                continue
            total += int(row.get("stock") or 0)
        if total == 0 and len(rows) == 1:
            return int(rows[0].get("stock") or 0)
        return total

    query_channel_capacity = get_stock_count

    def _pick_product(
        self,
        products: List[Dict[str, Any]],
        max_price: Optional[float],
        *,
        min_price: Optional[float] = None,
        product_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        want_id = _as_int(product_id)
        candidates: List[Tuple[float, int, Dict[str, Any]]] = []
        for item in products:
            if not isinstance(item, dict) or item.get("active") is False:
                continue
            available = _as_int(item.get("available"), 0) or 0
            if available <= 0:
                continue
            item_id = _as_int(item.get("id"))
            if want_id is not None and item_id != want_id:
                continue
            cost = parse_money_usd(item.get("price"))
            if cost is None:
                cost = 0.0
            if min_price is not None and cost < min_price:
                continue
            if max_price is not None and cost > max_price:
                continue
            candidates.append((cost, -available, item))
        if not candidates:
            raise KeyError("no_product")
        candidates.sort(key=lambda row: (row[0], row[1]))
        return candidates[0][2]

    async def list_priced_products(
        self,
        country: Union[str, int] = "id",
        service: str = DEFAULT_SERVICE,
        *,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        operator: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出某国某服务的可租产品档位（按价格升序），便于切换供应商通道。"""
        country_id = await self.resolve_country_id(country)
        platform = await self._resolve_telegram_platform(service, country_id=country_id)
        platform_id = _as_int(platform.get("id"))
        operator_id = _as_int(operator) if operator and str(operator).strip().isdigit() else None
        products = await self.list_products(
            country_id=country_id,
            platform_id=platform_id,
            operator_id=operator_id,
        )
        rows: List[Dict[str, Any]] = []
        for item in products:
            if not isinstance(item, dict) or item.get("active") is False:
                continue
            available = _as_int(item.get("available"), 0) or 0
            if available <= 0:
                continue
            cost = parse_money_usd(item.get("price"))
            if cost is None:
                continue
            if min_price is not None and cost < min_price:
                continue
            if max_price is not None and cost > max_price:
                continue
            rows.append(
                {
                    "product_id": _as_int(item.get("id")),
                    "catalog_product_id": _as_int(item.get("catalog_product_id")),
                    "price": cost,
                    "available": available,
                    "name": item.get("name") or item.get("title"),
                    "operator_id": item.get("operator_id"),
                    "raw": item,
                }
            )
        rows.sort(key=lambda r: (float(r["price"]), -int(r["available"] or 0)))
        return rows

    async def get_number(
        self,
        country: Union[str, int] = "id",
        service: str = DEFAULT_SERVICE,
        operator: Optional[str] = None,
        provider_ids: Optional[Union[str, List[str]]] = None,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        product_id: Optional[int] = None,
    ) -> Tuple[str, str]:
        iso = await self.resolve_country_iso2(country)
        country_id = await self.resolve_country_id(country)
        platform = await self._resolve_telegram_platform(service, country_id=country_id)
        platform_id = _as_int(platform.get("id"))
        operator_id = _as_int(operator) if operator and str(operator).strip().isdigit() else None
        products = await self.list_products(
            country_id=country_id,
            platform_id=platform_id,
            operator_id=operator_id,
        )
        bid = normalize_sms_max_price(max_price)
        bid_str = format_sms_max_price(bid)
        floor = normalize_sms_max_price(min_price)
        floor_str = format_sms_max_price(floor)
        want_product_id = _as_int(product_id)
        try:
            picked = self._pick_product(
                products,
                bid,
                min_price=floor,
                product_id=want_product_id,
            )
        except KeyError:
            hint = f"max_price={bid_str or '未设置'}"
            if floor_str:
                hint = f"min_price={floor_str},{hint}"
            if want_product_id is not None:
                hint = f"product_id={want_product_id},{hint}"
            raise NoNumberAvailableError(
                iso or str(country),
                f"no free phones under {hint}",
            )
        catalog_product_id = _as_int(picked.get("catalog_product_id"))
        picked_product_id = _as_int(picked.get("id"))
        # 指定 product_id：直接锁通道（API 不允许再附 max_price/prefer_provider）。
        # 否则走 catalog_product_id + 价格带，可轮换不同供应商档位。
        body: Dict[str, Any] = {"quantity": 1}
        if want_product_id is not None and picked_product_id is not None:
            body["product_id"] = picked_product_id
        elif catalog_product_id is not None:
            body["catalog_product_id"] = catalog_product_id
            body["policy"] = "cheapest"
            if floor_str is not None:
                body["min_price"] = floor_str
            if bid_str is not None:
                body["max_price"] = bid_str
        elif picked_product_id is not None:
            body["product_id"] = picked_product_id
        else:
            raise SmsCodeError(f"产品缺少 catalog_product_id/product_id: {picked}")
        if operator_id is not None and "catalog_product_id" in body:
            body["operator_id"] = operator_id
        prefer = None
        if provider_ids and "catalog_product_id" in body:
            if isinstance(provider_ids, (list, tuple, set)):
                prefer = next((str(x).strip() for x in provider_ids if str(x).strip()), None)
            else:
                prefer = str(provider_ids).split(",")[0].strip()
        if prefer:
            body["prefer_provider"] = prefer
        logger.info(
            "向 SMSCode 申请租号 (country=%s/%s, product_id=%s, catalog_product_id=%s, "
            "minPrice=%s, maxPrice=%s, key=%s)...",
            country_id,
            (iso or "?").upper(),
            body.get("product_id"),
            body.get("catalog_product_id"),
            floor_str if floor_str is not None else "未设置",
            bid_str if bid_str is not None else "未设置",
            mask_api_key(self.api_key),
        )
        payload = await self._request(
            "POST",
            "/orders/create",
            json_body=body,
            country=iso or str(country),
            extra_headers={"Content-Type": "application/json"},
            idempotency_key=uuid.uuid4().hex,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        orders = data.get("orders") if isinstance(data, dict) else None
        order = None
        if isinstance(orders, list) and orders:
            order = orders[0]
        elif isinstance(data, dict) and data.get("id") is not None:
            order = data
        if not isinstance(order, dict):
            raise SmsCodeError(f"租号返回非预期格式: {payload}")
        act_id = order.get("id")
        phone = order.get("phone_number") or order.get("phone") or order.get("number")
        if act_id in (None, "") or not phone:
            raise SmsCodeError(f"租号返回非预期格式: {payload}")
        return str(act_id), _normalize_phone(str(phone))

    lease_channel_handle = get_number

    async def get_order(self, act_id: str) -> Dict[str, Any]:
        if not act_id:
            raise SmsCodeError("缺少 order_id，无法查询 SMSCode 订单")
        payload = await self._request("GET", f"/orders/{act_id}")
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        raise SmsCodeError(f"查询 SMSCode 订单失败: {payload}")

    async def get_code(self, act_id: str) -> Optional[str]:
        """单次拉取 OTP（getCode）；尚未到达则返回 None。"""
        data = await self.get_order(act_id)
        code = str(data.get("otp_code") or data.get("code") or "").strip()
        return code or None

    async def notify_ready(self, act_id: str) -> str:
        _ = act_id
        return ""

    async def wait_for_code(
        self,
        act_id: str,
        max_attempts: int = 30,
        interval: float = 4.0,
        log_callback: Optional[Callable] = None,
        notify_ready: bool = True,
    ) -> str:
        if not act_id:
            raise SmsCodeError("缺少 order_id，无法轮询验证码")
        if notify_ready:
            await self.notify_ready(act_id)
        last_status = ""
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval)
            try:
                data = await self.get_order(act_id)
            except Exception as exc:
                logger.warning("轮询 SMSCode 验证码异常: %s", exc)
                last_status = f"EXC:{exc}"
                if log_callback:
                    await log_callback(
                        f"[接码轮询] {self.PROVIDER_LABEL} getOrder 异常={exc} "
                        f"elapsed={attempt * interval:.0f}s attempt={attempt}/{max_attempts}"
                    )
                continue
            status = str(data.get("status") or "").upper()
            last_status = status
            code = str(data.get("otp_code") or data.get("code") or "").strip()
            if log_callback and (
                attempt == 1 or attempt == max_attempts or attempt % 5 == 0 or bool(code)
            ):
                await log_callback(
                    f"[接码轮询] {self.PROVIDER_LABEL} status={status} "
                    f"elapsed={attempt * interval:.0f}s attempt={attempt}/{max_attempts}"
                )
            if code:
                return code
            if status in TERMINAL_FAIL_STATUSES:
                raise SmsCodeError(f"{self.PROVIDER_LABEL} 订单已终止 ({status}): {data}")
        raise TimeoutError(
            f"等待 {self.PROVIDER_LABEL} 带外挑战证明超时 "
            f"(已达最大重试轮次, last_status={last_status})"
        )

    poll_ephemeral_challenge_proof = wait_for_code

    async def _order_action(self, action: str, act_id: str) -> Dict[str, Any]:
        if not act_id:
            return {"success": False, "skipped": True, "reason": "missing_act_id", "status": action}
        order_id = _as_int(act_id, default=None)
        body = {"id": order_id if order_id is not None else act_id}
        try:
            payload = await self._request(
                "POST",
                f"/orders/{action}",
                json_body=body,
                extra_headers={"Content-Type": "application/json"},
            )
            data = payload.get("data") if isinstance(payload, dict) else payload
            status = ""
            if isinstance(data, dict):
                status = str(data.get("status") or "").upper()
            success = True
            if status in TERMINAL_FAIL_STATUSES and action == "finish":
                success = False
            result = {
                "success": success,
                "skipped": False,
                "act_id": str(act_id),
                "status": status or action,
                "action": action,
                "data": data,
                "error": None,
            }
            logger.info("[SMSCode %s] act_id=%s status=%s", action, act_id, result["status"])
            return result
        except SmsCodeError as exc:
            code = str(getattr(exc, "code", "") or "").upper()
            if action == "cancel" and code in CANCEL_TOO_EARLY_CODES:
                retry_after = parse_cancel_retry_after(exc)
                logger.warning(
                    "SMSCode 取消过早 act_id=%s retry_after=%.0fs: %s",
                    act_id,
                    retry_after,
                    exc,
                )
                return {
                    "success": False,
                    "skipped": False,
                    "act_id": str(act_id),
                    "status": code,
                    "action": action,
                    "error": str(exc),
                    "early_cancel": True,
                    "retry_after": retry_after,
                }
            logger.warning("SMSCode %s 失败 act_id=%s: %s", action, act_id, exc)
            return {
                "success": False,
                "skipped": False,
                "act_id": str(act_id),
                "status": action,
                "action": action,
                "error": str(exc),
            }
        except Exception as exc:
            logger.warning("SMSCode %s 失败 act_id=%s: %s", action, act_id, exc)
            return {
                "success": False,
                "skipped": False,
                "act_id": str(act_id),
                "status": action,
                "action": action,
                "error": str(exc),
            }

    async def finish(self, act_id: str) -> Dict[str, Any]:
        return await self._order_action("finish", act_id)

    finalize_channel_binding = finish

    async def cancel(
        self,
        act_id: str,
        *,
        wait_if_too_early: bool = False,
        max_wait: float = 180.0,
    ) -> Dict[str, Any]:
        """取消订单并退款。

        SMSCode 租号后约 120s 内 cancel 会 409 CANCEL_TOO_EARLY。
        wait_if_too_early=True 时按 retry_after 等待后最多再试几次（会阻塞调用方）。
        编排层默认用后台延迟退订，避免猎号被卡住。
        """
        result = await self._order_action("cancel", act_id)
        too_early = bool(result.get("early_cancel"))
        if wait_if_too_early and not result.get("success") and too_early:
            waited = 0.0
            attempts = 0
            while attempts < 4 and waited < max_wait:
                retry_after = float(result.get("retry_after") or DEFAULT_CANCEL_RETRY_AFTER_SECONDS)
                sleep_for = min(max(1.0, retry_after + 1.0), max_wait - waited)
                if sleep_for <= 0:
                    break
                logger.info(
                    "SMSCode cancel 过早，等待 %.0fs 后重试 act_id=%s (attempt=%s)",
                    sleep_for,
                    act_id,
                    attempts + 1,
                )
                await asyncio.sleep(sleep_for)
                waited += sleep_for
                attempts += 1
                result = await self._order_action("cancel", act_id)
                too_early = bool(result.get("early_cancel"))
                if result.get("success") or not too_early:
                    break
            result["deferred_wait_seconds"] = waited
            result["deferred_attempts"] = attempts
        return result

    revoke_channel_binding = cancel


OOBTelemetryProvider = SmsCodeService
