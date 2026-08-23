"""Proxy-Seller 多径中继网关客户端。

官方 API 规范 (https://docs.proxy-seller.com/):
    Base: https://proxy-seller.com/personal/api/v1/{api_key}/

    列表:
        GET  /proxy/list            返回账户下全部类型的活跃代理
        GET  /proxy/list/{type}     ipv4 / ipv6 / mobile / isp / mix / mix_isp / resident
        Query: country=Alpha3 (USA/CHL/...), latest=Y/N, orderId, ends=Y

    购买/租用 (预留，不会被注册流水线自动调用):
        GET  /reference/list/{type} 查询 countryId / periodId / 支付方式
        POST /order/calc            下单计价 (不扣费)
        POST /order/make            正式下单租用
        GET  /balance               查询余额

    续费:
        POST /prolong/calc/{type}   续费计价
        POST /prolong/make/{type}   正式续费
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import httpx

logger = logging.getLogger("MultipathRelayGatewayService")

# ISO-2 -> (alpha3, 英文名, 其它别名...)
# 用于精准 / 模糊匹配 Proxy-Seller 返回的 country / country_alpha3。
COUNTRY_PROFILES: Dict[str, Tuple[str, ...]] = {
    "af": ("afg", "afghanistan"),
    "ar": ("arg", "argentina"),
    "au": ("aus", "australia"),
    "be": ("bel", "belgium"),
    "br": ("bra", "brazil", "brasil"),
    "ca": ("can", "canada"),
    "cl": ("chl", "chile"),
    "cn": ("chn", "china"),
    "co": ("col", "colombia"),
    "cz": ("cze", "czechia", "czech republic"),
    "de": ("deu", "germany"),
    "es": ("esp", "spain"),
    "fr": ("fra", "france"),
    "gb": ("gbr", "united kingdom", "uk", "great britain", "england"),
    "id": ("idn", "indonesia"),
    "in": ("ind", "india"),
    "it": ("ita", "italy"),
    "jp": ("jpn", "japan"),
    "kz": ("kaz", "kazakhstan"),
    "mx": ("mex", "mexico"),
    "nl": ("nld", "netherlands", "holland"),
    "pl": ("pol", "poland"),
    "ru": ("rus", "russia", "russian federation"),
    "sg": ("sgp", "singapore"),
    "tr": ("tur", "turkey", "turkiye"),
    "ua": ("ukr", "ukraine"),
    "us": ("usa", "united states", "united states of america", "america"),
    "vn": ("vnm", "vietnam", "viet nam"),
}

PROXY_TYPE_BUCKETS = ("ipv4", "ipv6", "mobile", "isp", "mix", "mix_isp", "resident")
CACHE_TTL_SECONDS = 90.0
DEFAULT_PROBE_TIMEOUT = 8.0


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def expand_country_aliases(query: Optional[str]) -> Set[str]:
    """把 ISO-2 / ISO-3 / 国家名展开为可互相比对的别名集合。

    两位两个码只做精确命中，避免 `id` 误匹配 India / `in` 误匹配 Indonesia。
    国家全称才启用子串模糊匹配。
    """
    token = _norm(query)
    if not token:
        return set()
    aliases: Set[str] = {token}
    for iso2, extras in COUNTRY_PROFILES.items():
        names = {_norm(item) for item in extras}
        alpha3 = _norm(extras[0]) if extras else ""
        family = {iso2, alpha3, *names}
        if token == iso2 or (alpha3 and token == alpha3) or token in names:
            aliases.update(family)
            continue
        if len(token) >= 4 and any(len(name) >= 4 and (token in name or name in token) for name in names):
            aliases.update(family)
    return {item for item in aliases if item}


def country_alpha3(query: Optional[str]) -> Optional[str]:
    """尽量把用户输入转成官方列表接口使用的 Alpha3 码。"""
    token = _norm(query)
    if not token:
        return None
    if len(token) == 3 and token.isalpha():
        return token.upper()
    for iso2, extras in COUNTRY_PROFILES.items():
        family = {iso2, *(_norm(item) for item in extras)}
        if token in family:
            return extras[0].upper()
    return None


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value in (None, "", False):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_raw_items(payload: Any) -> List[Dict[str, Any]]:
    """兼容官方两种列表形态:

    1) 指定 type: data.items = [{...}]
    2) 全量 /proxy/list: data = {ipv4: [...], ipv6: [...], ...}
       其中桶值可能是 list，或 {items: [...]}。
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    collected: List[Dict[str, Any]] = []
    items = payload.get("items")
    if isinstance(items, list):
        collected.extend(item for item in items if isinstance(item, dict))

    for key, value in payload.items():
        if key == "items":
            continue
        if isinstance(value, list):
            collected.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            nested = value.get("items")
            if isinstance(nested, list):
                collected.extend(item for item in nested if isinstance(item, dict))
            elif any(field in value for field in ("ip", "ip_only", "port_socks", "port_socks5", "login")):
                collected.append(value)
    return collected


def _pick_protocol_and_port(item: Dict[str, Any]) -> Tuple[str, int]:
    """优先 SOCKS5 (port_socks5 > port_socks)，否则回落到 HTTP / 通用 port。"""
    raw_protocol = _norm(item.get("protocol") or item.get("proto") or "socks5")
    if raw_protocol in {"socks", "socks5", "socks4"}:
        protocol = "socks5" if raw_protocol != "socks4" else "socks4"
    elif raw_protocol in {"http", "https"}:
        protocol = "http"
    else:
        protocol = "socks5"

    socks_port = _as_int(item.get("port_socks5")) or _as_int(item.get("port_socks"))
    http_port = _as_int(item.get("port_http")) or _as_int(item.get("port_https"))
    generic_port = _as_int(item.get("port"))

    if socks_port:
        return "socks5", socks_port
    if protocol.startswith("http") and http_port:
        return "http", http_port
    if generic_port:
        return protocol if protocol in {"socks5", "socks4", "http"} else "socks5", generic_port
    if http_port:
        return "http", http_port
    return protocol if protocol in {"socks5", "http"} else "socks5", 1080


def normalize_proxy_item(item: Dict[str, Any], bucket: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """把官方字段归一化为注册流水线使用的统一结构。"""
    if not isinstance(item, dict):
        return None
    addr = item.get("addr") or item.get("ip_only") or item.get("ip")
    if not addr:
        return None
    protocol, port = _pick_protocol_and_port(item)
    country_name = item.get("country") or item.get("country_name")
    alpha3 = item.get("country_alpha3") or item.get("alpha3")
    alpha2 = item.get("country_code") or item.get("country_alpha2") or item.get("iso2")
    if not alpha2 and country_name:
        aliases = expand_country_aliases(str(country_name))
        for iso2 in COUNTRY_PROFILES:
            if iso2 in aliases:
                alpha2 = iso2
                break
    if not alpha2 and alpha3:
        aliases = expand_country_aliases(str(alpha3))
        for iso2 in COUNTRY_PROFILES:
            if iso2 in aliases:
                alpha2 = iso2
                break

    status = item.get("status") or item.get("status_type") or "unknown"
    active_until = item.get("active_until") or item.get("date_end") or item.get("expired_at")
    username = item.get("username") or item.get("login")
    password = item.get("password")

    return {
        "id": item.get("id"),
        "order_id": item.get("order_id") or item.get("order_number"),
        "proxy_type": protocol,
        "addr": str(addr).strip(),
        "port": int(port),
        "username": username,
        "password": password,
        "country": country_name or alpha2 or alpha3,
        "country_code": _norm(alpha2) or None,
        "country_alpha3": str(alpha3).upper() if alpha3 else country_alpha3(alpha2 or country_name),
        "active_until": active_until,
        "status": status,
        "status_type": item.get("status_type") or status,
        "can_prolong": bool(item.get("can_prolong")),
        "catalog_type": bucket or item.get("type") or item.get("proxy_kind"),
        "rotation": item.get("rotation"),
        "raw": item,
    }


def match_proxy_country(proxy: Dict[str, Any], query: Optional[str]) -> bool:
    """精准优先、别名/子串兜底的国家匹配。"""
    if not query:
        return True
    wanted = expand_country_aliases(query)
    if not wanted:
        return True
    fields = [
        proxy.get("country_code"),
        proxy.get("country_alpha3"),
        proxy.get("country"),
        (proxy.get("raw") or {}).get("country"),
        (proxy.get("raw") or {}).get("country_alpha3"),
        (proxy.get("raw") or {}).get("country_code"),
    ]
    haystack: Set[str] = set()
    for field in fields:
        token = _norm(field)
        if token:
            haystack.add(token)
            haystack.update(expand_country_aliases(token))
    if wanted & haystack:
        return True
    # 仅对较长名称做子串兜底，覆盖官方偶发的 "Chile (CL)" 这类组合字段
    joined = " ".join(sorted(haystack))
    return any(len(token) >= 4 and token in joined for token in wanted)


def proxy_identity(proxy: Dict[str, Any]) -> str:
    return f"{proxy.get('addr')}:{proxy.get('port')}"


def format_proxy_endpoint(proxy: Dict[str, Any]) -> str:
    protocol = _norm(proxy.get("proxy_type") or "socks5") or "socks5"
    return f"{protocol}://{proxy.get('addr')}:{proxy.get('port')}"


class ProxySellerService:
    """多径传输出口中继网关服务 (Multipath Egress Relay Gateway Provider)"""

    BASE_URL = "https://proxy-seller.com/personal/api/v1"

    # 进程内共享: 按 api_key 缓存完整代理池 + 单节点健康状态，便于轮换。
    _pool_cache: Dict[str, Dict[str, Any]] = {}
    _health: Dict[str, Dict[str, Any]] = {}
    _rr_cursor: Dict[str, int] = {}

    def __init__(self, api_key: str, cache_ttl: float = CACHE_TTL_SECONDS):
        self.api_key = (api_key or "").strip()
        self.cache_ttl = cache_ttl
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    async def close(self):
        try:
            await self.client.aclose()
        except Exception:
            pass

    def _cache_entry(self) -> Dict[str, Any]:
        return self._pool_cache.setdefault(self.api_key, {"items": [], "fetched_at": 0.0})

    def invalidate_cache(self) -> None:
        self._pool_cache.pop(self.api_key, None)

    def _raise_api_error(self, data: Dict[str, Any]) -> None:
        errors = data.get("errors") or []
        if errors and isinstance(errors[0], dict):
            err_msg = errors[0].get("message") or "API Error"
        elif errors:
            err_msg = str(errors[0])
        else:
            err_msg = data.get("message") or "API Error"
        raise RuntimeError(err_msg)

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("未配置 Proxy-Seller API Key")
        url = f"{self.BASE_URL}/{self.api_key}/{path.lstrip('/')}"
        resp = await self.client.request(method, url, params=params, json=json_body)
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Proxy-Seller 响应不是合法 JSON (HTTP {resp.status_code}): {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Proxy-Seller 响应结构异常")
        if data.get("status") == "error" or (data.get("errors") and data.get("status") != "success"):
            self._raise_api_error(data)
        return data

    async def _fetch_remote_items(self, proxy_type: Optional[str] = None) -> List[Dict[str, Any]]:
        path = f"proxy/list/{proxy_type}" if proxy_type else "proxy/list"
        data = await self._request("GET", path)
        payload = data.get("data", data)
        collected: List[Dict[str, Any]] = []

        if isinstance(payload, dict) and any(key in payload for key in PROXY_TYPE_BUCKETS) and "items" not in payload:
            for bucket in PROXY_TYPE_BUCKETS:
                raw_bucket = payload.get(bucket)
                for item in _extract_raw_items(raw_bucket):
                    normalized = normalize_proxy_item(item, bucket=bucket)
                    if normalized:
                        collected.append(normalized)
            # 兼容官方偶发把其它键也放进 data
            leftover = _extract_raw_items({k: v for k, v in payload.items() if k not in PROXY_TYPE_BUCKETS})
            for item in leftover:
                normalized = normalize_proxy_item(item)
                if normalized and all(proxy_identity(normalized) != proxy_identity(existing) for existing in collected):
                    collected.append(normalized)
            return collected

        for item in _extract_raw_items(payload):
            normalized = normalize_proxy_item(item, bucket=proxy_type)
            if normalized:
                collected.append(normalized)
        return collected

    async def refresh_pool(self, proxy_type: Optional[str] = None) -> List[Dict[str, Any]]:
        items = await self._fetch_remote_items(proxy_type=proxy_type)
        entry = self._cache_entry()
        entry["items"] = items
        entry["fetched_at"] = time.time()
        logger.info("已从 Proxy-Seller API 刷新出口中继池: %s 个节点", len(items))
        return [dict(item) for item in items]

    async def _ensure_pool(self, refresh: bool = False, proxy_type: Optional[str] = None) -> List[Dict[str, Any]]:
        entry = self._cache_entry()
        age = time.time() - float(entry.get("fetched_at") or 0)
        if refresh or not entry.get("items") or age > self.cache_ttl:
            try:
                return await self.refresh_pool(proxy_type=proxy_type)
            except Exception:
                if entry.get("items"):
                    logger.warning("刷新 Proxy-Seller 代理池失败，继续使用 %ss 前的本地缓存", int(age))
                    return [dict(item) for item in entry["items"]]
                raise
        return [dict(item) for item in entry["items"]]

    def attach_health(self, proxy: Dict[str, Any]) -> Dict[str, Any]:
        view = dict(proxy)
        view.pop("raw", None)
        health = self._health.get(proxy_identity(proxy)) or {}
        view["healthy"] = health.get("healthy")
        view["egress_ip"] = health.get("egress_ip")
        view["egress_country"] = health.get("egress_country")
        view["egress_country_code"] = health.get("country_code")
        view["last_error"] = health.get("error")
        view["checked_at"] = health.get("checked_at")
        return view

    def record_health(self, proxy: Dict[str, Any], result: Dict[str, Any]) -> None:
        self._health[proxy_identity(proxy)] = {
            "healthy": bool(result.get("success")),
            "checked_at": time.time(),
            "egress_ip": result.get("ip"),
            "egress_country": result.get("country"),
            "country_code": _norm(result.get("country_code")) or None,
            "error": result.get("error"),
        }

    async def get_proxy_list(
        self,
        country: Optional[str] = None,
        refresh: bool = False,
        proxy_type: Optional[str] = None,
        include_health: bool = True,
    ) -> List[Dict[str, Any]]:
        """检索账户下全部或指定区域的活跃代理。

        country 为空时返回全部；传入 ISO-2 / ISO-3 / 国家名时做精准+模糊过滤。
        """
        items = await self._ensure_pool(refresh=refresh, proxy_type=proxy_type)
        if country:
            items = [item for item in items if match_proxy_country(item, country)]
        if include_health:
            return [self.attach_health(item) for item in items]
        return items

    discover_relay_nodes = get_proxy_list

    def cache_meta(self) -> Dict[str, Any]:
        entry = self._cache_entry()
        fetched_at = float(entry.get("fetched_at") or 0)
        age = (time.time() - fetched_at) if fetched_at else None
        return {
            "cached": bool(entry.get("items") and fetched_at),
            "cache_age_seconds": round(age, 2) if age is not None else None,
            "cache_ttl_seconds": self.cache_ttl,
            "total_cached": len(entry.get("items") or []),
        }

    def _sort_candidates(self, proxies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def score(item: Dict[str, Any]) -> Tuple[int, int, str]:
            health = self._health.get(proxy_identity(item)) or {}
            if health.get("healthy") is True:
                health_rank = 0
            elif health.get("healthy") is False:
                health_rank = 2
            else:
                health_rank = 1
            status = _norm(item.get("status_type") or item.get("status"))
            active_rank = 0 if (not status or "active" in status) else 1
            return (health_rank, active_rank, proxy_identity(item))

        return sorted(proxies, key=score)

    def _rotate(self, country_key: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        cursor_key = f"{self.api_key}:{country_key}"
        start = self._rr_cursor.get(cursor_key, 0) % len(candidates)
        rotated = candidates[start:] + candidates[:start]
        self._rr_cursor[cursor_key] = (start + 1) % len(candidates)
        return rotated

    async def select_best_proxy(
        self,
        target_country: Optional[str] = None,
        probe: bool = False,
        allow_fallback: bool = True,
        refresh: bool = False,
        max_probes: int = 3,
    ) -> Dict[str, Any]:
        """按目标国家自动挑选最佳/可用代理，支持测活与跨区域兜底。"""
        country = _norm(target_country)
        regional = await self.get_proxy_list(country=country or None, refresh=refresh, include_health=False)
        fallback_used = False
        source = "regional"
        hint = None

        if regional:
            candidates = self._rotate(country or "*", self._sort_candidates(regional))
            message = f"已匹配到 {len(regional)} 个 {(country or 'ALL').upper()} 区域代理"
        else:
            all_items = await self.get_proxy_list(country=None, refresh=False, include_health=False)
            if country and all_items and allow_fallback:
                fallback_used = True
                source = "smart_fallback"
                candidates = self._rotate(f"fallback:{country}", self._sort_candidates(all_items))
                available = sorted({
                    (item.get("country_code") or item.get("country") or "?").upper()
                    for item in all_items
                })
                hint = (
                    f"目标区域 {country.upper()} 暂无可用 Proxy-Seller 代理；"
                    f"账户当前区域: {', '.join(available) or '未知'}。已启用智能兜底。"
                )
                message = hint
            elif country:
                candidates = []
                hint = (
                    f"目标区域 {country.upper()} 暂无可用 Proxy-Seller 代理，"
                    "且账户下也没有其它活跃节点可兜底。"
                )
                message = hint
            else:
                candidates = []
                hint = "Proxy-Seller 账户下没有检索到活跃代理。"
                message = hint

        selected = None
        probe_results: List[Dict[str, Any]] = []
        if candidates and probe:
            for item in candidates[: max(1, max_probes)]:
                result = await self.test_proxy_connectivity(item)
                self.record_health(item, result)
                probe_results.append({"proxy": self.attach_health(item), "result": result})
                if result.get("success"):
                    selected = item
                    message = (
                        f"测活通过，选定 {(item.get('country_code') or country or 'ALL').upper()} "
                        f"区域代理 {format_proxy_endpoint(item)}"
                    )
                    break
            if selected is None:
                selected = candidates[0]
                message = (
                    f"前 {len(probe_results)} 个节点测活未全部成功，"
                    f"按健康轮换顺序回退至 {format_proxy_endpoint(selected)}"
                )
        elif candidates:
            selected = candidates[0]
            message = (
                f"已自动分配 {(selected.get('country_code') or country or 'ALL').upper()} "
                f"区域代理 {format_proxy_endpoint(selected)}"
            )

        return {
            "success": selected is not None,
            "matched": bool(regional and selected and not fallback_used),
            "fallback_used": fallback_used,
            "source": source,
            "message": message,
            "hint": hint,
            "target_country": country or None,
            "candidates": len(regional if regional else candidates),
            "proxy": self.attach_health(selected) if selected else None,
            "probe_results": probe_results,
        }

    auto_select = select_best_proxy

    async def test_all(
        self,
        country: Optional[str] = None,
        refresh: bool = False,
        limit: int = 20,
        concurrency: int = 4,
    ) -> Dict[str, Any]:
        """批量探测账户代理的连通性与出口 IP / 国家。"""
        import asyncio

        proxies = await self.get_proxy_list(country=country, refresh=refresh, include_health=False)
        if limit and limit > 0:
            proxies = proxies[:limit]
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _probe(item: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                result = await self.test_proxy_connectivity(item)
                self.record_health(item, result)
                return {
                    **self.attach_health(item),
                    "probe": result,
                }

        results = await asyncio.gather(*[_probe(item) for item in proxies], return_exceptions=False)
        healthy = sum(1 for item in results if item.get("healthy"))
        return {
            "success": True,
            "tested": len(results),
            "healthy": healthy,
            "country": country,
            "results": results,
            "message": f"已完成 {len(results)} 个节点测活，{healthy} 个连通",
        }

    @staticmethod
    async def test_proxy_connectivity(
        proxy_dict: Dict[str, Any],
        timeout: float = DEFAULT_PROBE_TIMEOUT,
    ) -> Dict[str, Any]:
        """对指定出口中继路径进行主动连通性与公网拓扑寻址探测。"""
        proxy_type = _norm(proxy_dict.get("proxy_type") or "socks5")
        if proxy_type in {"socks", "socks5"}:
            proxy_type = "socks5"
        elif proxy_type in {"http", "https"}:
            proxy_type = "http"
        addr = proxy_dict.get("addr")
        port = proxy_dict.get("port")
        username = proxy_dict.get("username")
        password = proxy_dict.get("password")

        if not addr or not port:
            return {"success": False, "error": "未配置有效的中继跳点主机地址与端口"}

        proxy_url = f"{proxy_type}://"
        if username and password:
            proxy_url += f"{username}:{password}@"
        proxy_url += f"{addr}:{port}"

        client_kwargs = {"verify": False, "timeout": timeout}
        try:
            try:
                client = httpx.AsyncClient(proxy=proxy_url, **client_kwargs)
            except TypeError:
                client = httpx.AsyncClient(proxies=proxy_url, **client_kwargs)

            async with client:
                ip_resp = await client.get("https://ipapi.co/json/")
                ip_data = ip_resp.json()
                if not isinstance(ip_data, dict) or not ip_data.get("ip"):
                    return {"success": False, "error": f"出口探测响应异常: {ip_data}"}
                return {
                    "success": True,
                    "ip": ip_data.get("ip"),
                    "country": ip_data.get("country_name"),
                    "country_code": ip_data.get("country_code"),
                    "city": ip_data.get("city"),
                    "org": ip_data.get("org"),
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    probe_relay_path_connectivity = test_proxy_connectivity

    # ------------------------------------------------------------------
    # 购买 / 续费 / 租用 — 官方规范封装 (预留，注册流水线不会自动扣费)
    # ------------------------------------------------------------------
    async def get_balance(self) -> Dict[str, Any]:
        """GET /balance — 查询账户余额，下单前建议先调用。"""
        data = await self._request("GET", "balance")
        return data.get("data", data)

    async def get_reference_list(self, proxy_type: Optional[str] = None) -> Dict[str, Any]:
        """GET /reference/list[/{type}] — 获取 countryId / periodId / 支付方式。"""
        path = f"reference/list/{proxy_type}" if proxy_type else "reference/list"
        data = await self._request("GET", path)
        return data.get("data", data)

    async def calculate_order(self, payload: Dict[str, Any], proxy_type: Optional[str] = None) -> Dict[str, Any]:
        """POST /order/calc — 下单计价，不扣费。

        payload 需包含官方字段: countryId, periodId, quantity, paymentId(=1 余额),
        以及可选 coupon / authorization / protocol (HTTPS|SOCKS5)。
        """
        path = f"order/calc/{proxy_type}" if proxy_type else "order/calc"
        data = await self._request("POST", path, json_body=payload)
        return data.get("data", data)

    async def place_order(self, payload: Dict[str, Any], proxy_type: Optional[str] = None) -> Dict[str, Any]:
        """POST /order/make — 正式租用/购买。调用后会扣费并刷新本地缓存。"""
        path = f"order/make/{proxy_type}" if proxy_type else "order/make"
        data = await self._request("POST", path, json_body=payload)
        self.invalidate_cache()
        return data.get("data", data)

    async def calculate_renewal(
        self,
        proxy_ids: Iterable[Any],
        period_id: str,
        proxy_type: str = "ipv4",
        payment_id: str = "1",
        coupon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /prolong/calc/{type} — 续费计价。"""
        body: Dict[str, Any] = {
            "ids": list(proxy_ids),
            "periodId": period_id,
            "paymentId": payment_id,
        }
        if coupon:
            body["coupon"] = coupon
        data = await self._request("POST", f"prolong/calc/{proxy_type}", json_body=body)
        return data.get("data", data)

    async def renew_proxies(
        self,
        proxy_ids: Iterable[Any],
        period_id: str,
        proxy_type: str = "ipv4",
        payment_id: str = "1",
        coupon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /prolong/make/{type} — 正式续费。"""
        body: Dict[str, Any] = {
            "ids": list(proxy_ids),
            "periodId": period_id,
            "paymentId": payment_id,
        }
        if coupon:
            body["coupon"] = coupon
        data = await self._request("POST", f"prolong/make/{proxy_type}", json_body=body)
        self.invalidate_cache()
        return data.get("data", data)

    # 语义化别名，便于调用方按「购买 / 续费 / 租用」检索
    purchase_proxies = place_order
    rent_proxies = place_order
    prolong_proxies = renew_proxies


MultipathRelayGateway = ProxySellerService
