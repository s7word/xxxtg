"""用户自定义代理池：多格式文本解析、去重持久化、批量测活与按国家匹配。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from backend.app.services.net_utils import format_httpx_proxy_url as _format_httpx_proxy_url

from backend.app.services.proxyseller import (
    ProxySellerService,
    _norm,
    country_alpha3,
    format_proxy_endpoint,
    is_custom_proxy,
    load_custom_proxy_items,
    match_proxy_country,
    merge_proxy_pools,
    normalize_custom_proxy_item,
    proxy_identity,
)

logger = logging.getLogger("CustomProxyPool")

_SCHEME_RE = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*)://(?P<rest>.+)$", re.IGNORECASE)
_IPV6_BRACKET_RE = re.compile(
    r"^\[(?P<host>[^\]]+)\]:(?P<port>\d+)(?::(?P<user>[^:]*))?(?::(?P<password>.*))?$"
)


def _normalize_scheme(value: Optional[str], default: str = "socks5") -> str:
    token = _norm(value) or _norm(default) or "socks5"
    if token in {"socks", "socks5", "socks5h"}:
        return "socks5"
    if token in {"socks4", "socks4a"}:
        return "socks4"
    if token in {"http", "https"}:
        return "http"
    return "socks5"


def _clean_line(raw: str) -> str:
    line = str(raw or "").strip()
    if not line:
        return ""
    if line.startswith("#") or line.startswith("//") or line.startswith(";"):
        return ""
    if set(line) <= set("-_=*~"):
        return ""
    return line


def _as_port(value: Any) -> Optional[int]:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _blank_to_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def make_custom_proxy_id(item: Dict[str, Any]) -> str:
    existing = _blank_to_none(item.get("id"))
    if existing and existing.startswith("custom-"):
        return existing
    digest = hashlib.sha1(proxy_identity(item).encode("utf-8")).hexdigest()[:12]
    return f"custom-{digest}"


def _build_proxy_item(
    *,
    addr: Any,
    port: Any,
    username: Any = None,
    password: Any = None,
    proxy_type: Any = "socks5",
    raw_line: Optional[str] = None,
    country: Any = None,
    country_code: Any = None,
) -> Optional[Dict[str, Any]]:
    host = _blank_to_none(addr)
    parsed_port = _as_port(port)
    if not host or parsed_port is None:
        return None
    item = {
        "proxy_type": _normalize_scheme(proxy_type),
        "addr": host,
        "port": parsed_port,
        "username": _blank_to_none(username),
        "password": _blank_to_none(password),
        "country": _blank_to_none(country),
        "country_code": _norm(country_code) or None,
        "source": "custom",
        "catalog_type": "custom",
        "raw_line": raw_line,
    }
    item["id"] = make_custom_proxy_id(item)
    return item


def format_outbound_proxy_url(proxy: Optional[Dict[str, Any]]) -> Optional[str]:
    """拼接 httpx socks5:// / http:// 出站 URL。

    username / password 经 quote(safe="") 彻底转义 @ : / # 等保留字符，
    IPv6 地址由共享实现加上 [host] 保护。
    """
    return _format_httpx_proxy_url(proxy)


def _parse_authority(authority: str, default_scheme: str, raw_line: str) -> Optional[Dict[str, Any]]:
    """解析 user:pass@host:port / host:port 这类 authority。"""
    token = (authority or "").strip()
    if not token:
        return None
    if token.startswith("["):
        match = _IPV6_BRACKET_RE.match(token)
        if not match:
            return None
        return _build_proxy_item(
            addr=match.group("host"),
            port=match.group("port"),
            username=match.group("user"),
            password=match.group("password"),
            proxy_type=default_scheme,
            raw_line=raw_line,
        )

    user = None
    password = None
    hostport = token
    if "@" in token:
        creds, hostport = token.rsplit("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
        else:
            user = creds
        user = unquote(user) if user is not None else None
        password = unquote(password) if password is not None else None

    if hostport.count(":") == 0:
        return None
    host, port = hostport.rsplit(":", 1)
    return _build_proxy_item(
        addr=unquote(host),
        port=port,
        username=user,
        password=password,
        proxy_type=default_scheme,
        raw_line=raw_line,
    )


def _parse_url_proxy(line: str, default_scheme: str) -> Optional[Dict[str, Any]]:
    match = _SCHEME_RE.match(line)
    if not match:
        return None
    scheme = _normalize_scheme(match.group("scheme"), default_scheme)
    rest = match.group("rest")
    parsed = urlparse(f"{scheme}://{rest}")
    host = parsed.hostname
    port = parsed.port
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    if host and port:
        return _build_proxy_item(
            addr=host,
            port=port,
            username=username,
            password=password,
            proxy_type=scheme,
            raw_line=line,
        )
    return _parse_authority(rest, scheme, line)


def _parse_delimited(line: str, delimiter: str, default_scheme: str) -> Optional[Dict[str, Any]]:
    parts = [part.strip() for part in line.split(delimiter)]
    parts = [part for part in parts if part != "" or len(parts) <= 2]
    if len(parts) < 2:
        return None
    host = parts[0]
    port = parts[1]
    username = parts[2] if len(parts) >= 3 else None
    password = delimiter.join(parts[3:]) if len(parts) >= 4 else None
    return _build_proxy_item(
        addr=host,
        port=port,
        username=username,
        password=password,
        proxy_type=default_scheme,
        raw_line=line,
    )


def _looks_like_hostport(token: str) -> bool:
    value = (token or "").strip()
    if not value:
        return False
    if value.startswith("[") and "]:" in value:
        return _IPV6_BRACKET_RE.match(value) is not None
    if value.count(":") != 1:
        return False
    host, port = value.rsplit(":", 1)
    return bool(host) and _as_port(port) is not None


def parse_proxy_line(
    raw: str,
    default_scheme: str = "socks5",
    default_country: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """解析单行代理文本。无法识别时返回 None。"""
    line = _clean_line(raw)
    if not line:
        return None
    scheme = _normalize_scheme(default_scheme)
    item: Optional[Dict[str, Any]] = None
    if "://" in line:
        item = _parse_url_proxy(line, scheme)
    elif ";" in line:
        item = _parse_delimited(line, ";", scheme)
    elif "@" in line and _looks_like_hostport(line.rsplit("@", 1)[-1]):
        item = _parse_authority(line, scheme, line)
    else:
        item = _parse_delimited(line, ":", scheme)
    if not item:
        return None
    if default_country:
        token = _norm(default_country)
        item["country_code"] = token
        item["country"] = token.upper()
        item["country_alpha3"] = country_alpha3(token)
    return item


def parse_proxy_text(
    text: str,
    default_scheme: str = "socks5",
    default_country: Optional[str] = None,
) -> Dict[str, Any]:
    """批量解析多行代理文本，自动跳过空行、注释，并按 identity 去重。"""
    parsed: List[Dict[str, Any]] = []
    skipped: List[str] = []
    seen = set()
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        original = raw.strip()
        if not _clean_line(original):
            continue
        item = parse_proxy_line(original, default_scheme=default_scheme, default_country=default_country)
        if not item:
            skipped.append(original)
            continue
        ident = proxy_identity(item)
        if ident in seen:
            continue
        seen.add(ident)
        parsed.append(item)
    return {
        "proxies": parsed,
        "parsed": len(parsed),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


def to_persist_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """把调度结构压成可写入 AppConfigModel.custom_proxies 的字段。"""
    normalized = normalize_custom_proxy_item(item) or item
    country_code = _norm(normalized.get("country_code") or normalized.get("egress_country_code")) or None
    return {
        "id": make_custom_proxy_id(normalized),
        "proxy_type": _normalize_scheme(normalized.get("proxy_type")),
        "addr": normalized.get("addr"),
        "port": int(normalized.get("port")),
        "username": _blank_to_none(normalized.get("username")),
        "password": _blank_to_none(normalized.get("password")),
        "country": _blank_to_none(normalized.get("country") or normalized.get("egress_country")),
        "country_code": country_code,
        "country_alpha3": normalized.get("country_alpha3") or country_alpha3(country_code),
        "city": _blank_to_none(normalized.get("city")),
        "egress_ip": _blank_to_none(normalized.get("egress_ip") or normalized.get("ip")),
        "latency_ms": normalized.get("latency_ms"),
        "healthy": normalized.get("healthy"),
        "last_error": _blank_to_none(normalized.get("last_error") or normalized.get("error")),
        "checked_at": normalized.get("checked_at"),
        "source": "custom",
        "raw_line": _blank_to_none(normalized.get("raw_line")),
    }


def apply_probe_result(item: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(item)
    success = bool(result.get("success"))
    view["healthy"] = success
    view["checked_at"] = time.time()
    view["latency_ms"] = result.get("latency_ms")
    view["last_error"] = None if success else (result.get("error") or "测活失败")
    if success:
        country_code = _norm(result.get("country_code")) or view.get("country_code")
        view["egress_ip"] = result.get("ip") or view.get("egress_ip")
        view["country"] = result.get("country") or view.get("country")
        view["country_code"] = country_code
        view["country_alpha3"] = country_alpha3(country_code) or view.get("country_alpha3")
        view["city"] = result.get("city") or view.get("city")
    return view


def merge_imported_proxies(
    existing: Iterable[Dict[str, Any]],
    incoming: Iterable[Dict[str, Any]],
    replace: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    incoming_list = [to_persist_item(item) for item in incoming or [] if item]
    if replace:
        merged = incoming_list
        added = len(incoming_list)
        updated = 0
    else:
        by_id = {proxy_identity(item): to_persist_item(item) for item in existing or [] if item}
        added = 0
        updated = 0
        for item in incoming_list:
            ident = proxy_identity(item)
            if ident in by_id:
                previous = by_id[ident]
                # 新导入覆盖连接字段，但保留已有测活结果（除非新行已带测活）。
                merged_item = dict(previous)
                merged_item.update({
                    "proxy_type": item.get("proxy_type") or previous.get("proxy_type"),
                    "addr": item.get("addr"),
                    "port": item.get("port"),
                    "username": item.get("username"),
                    "password": item.get("password"),
                    "raw_line": item.get("raw_line") or previous.get("raw_line"),
                })
                if item.get("country_code") and not previous.get("country_code"):
                    merged_item["country_code"] = item.get("country_code")
                    merged_item["country"] = item.get("country") or previous.get("country")
                    merged_item["country_alpha3"] = item.get("country_alpha3") or previous.get("country_alpha3")
                if item.get("healthy") is not None:
                    merged_item["healthy"] = item.get("healthy")
                    merged_item["egress_ip"] = item.get("egress_ip")
                    merged_item["latency_ms"] = item.get("latency_ms")
                    merged_item["city"] = item.get("city")
                    merged_item["checked_at"] = item.get("checked_at")
                    merged_item["last_error"] = item.get("last_error")
                by_id[ident] = to_persist_item(merged_item)
                updated += 1
            else:
                by_id[ident] = item
                added += 1
        merged = list(by_id.values())
    return merged, {"added": added, "updated": updated, "total": len(merged)}


def _config_manager():
    from backend.app.config import ConfigManager

    return ConfigManager.get_instance()


def persist_custom_proxies(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from backend.app.models.schemas import AppConfigModel

    mgr = _config_manager()
    payload = mgr.config.model_dump()
    payload["custom_proxies"] = [to_persist_item(item) for item in items or []]
    saved = mgr.save_config(AppConfigModel(**payload))
    ProxySellerService.invalidate_all_caches()
    return [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in saved.custom_proxies]


def list_custom_proxies(country: Optional[str] = None) -> List[Dict[str, Any]]:
    items = [normalize_custom_proxy_item(item) or item for item in load_custom_proxy_items()]
    items = [item for item in items if item]
    if country:
        items = [item for item in items if match_proxy_country(item, country)]
    return items


def find_custom_proxy(
    *,
    proxy_id: Optional[str] = None,
    addr: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    wanted_id = _blank_to_none(proxy_id)
    wanted_ident = None
    if addr and port:
        wanted_ident = proxy_identity({
            "addr": addr,
            "port": int(port),
            "username": username,
        })
    for item in list_custom_proxies():
        if wanted_id and item.get("id") == wanted_id:
            return item
        if wanted_ident and proxy_identity(item) == wanted_ident:
            return item
    return None


def delete_custom_proxies(
    *,
    proxy_id: Optional[str] = None,
    addr: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
    clear_all: bool = False,
) -> Dict[str, Any]:
    current = [to_persist_item(item) for item in load_custom_proxy_items()]
    if clear_all:
        persist_custom_proxies([])
        return {
            "success": True,
            "deleted": len(current),
            "remaining": 0,
            "cleared": True,
            "message": f"已清空自建代理池（{len(current)} 条）",
        }
    target = find_custom_proxy(proxy_id=proxy_id, addr=addr, port=port, username=username)
    if not target:
        return {
            "success": False,
            "deleted": 0,
            "remaining": len(current),
            "cleared": False,
            "message": "未找到指定的自建代理",
        }
    ident = proxy_identity(target)
    remaining = [item for item in current if proxy_identity(item) != ident]
    persist_custom_proxies(remaining)
    return {
        "success": True,
        "deleted": 1,
        "remaining": len(remaining),
        "cleared": False,
        "proxy": target,
        "message": f"已删除 {format_proxy_endpoint(target)}",
    }


def import_proxy_text(
    text: str,
    *,
    probe: bool = False,
    replace: bool = False,
    default_scheme: str = "socks5",
    default_country: Optional[str] = None,
    concurrency: int = 4,
) -> Dict[str, Any]:
    parsed = parse_proxy_text(text, default_scheme=default_scheme, default_country=default_country)
    incoming = parsed["proxies"]
    existing = [] if replace else [to_persist_item(item) for item in load_custom_proxy_items()]
    merged, stats = merge_imported_proxies(existing, incoming, replace=replace)
    persist_custom_proxies(merged)
    return {
        "success": True,
        "parsed": parsed["parsed"],
        "imported": stats["added"],
        "updated": stats["updated"],
        "skipped": parsed["skipped"],
        "skipped_count": parsed["skipped_count"],
        "total": stats["total"],
        "proxies": [normalize_custom_proxy_item(item) or item for item in merged],
        "message": (
            f"已解析 {parsed['parsed']} 条，新增 {stats['added']}，"
            f"更新 {stats['updated']}，当前自建池 {stats['total']} 条"
        ),
    }


async def probe_custom_proxies(
    items: Optional[List[Dict[str, Any]]] = None,
    *,
    persist: bool = True,
    concurrency: int = 4,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    pool = [dict(item) for item in (items if items is not None else load_custom_proxy_items())]
    if limit and limit > 0:
        pool = pool[:limit]
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _probe(item: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            result = await ProxySellerService.test_proxy_connectivity(item)
            updated = apply_probe_result(item, result)
            return {
                **(normalize_custom_proxy_item(updated) or updated),
                "probe": result,
            }

    results = await asyncio.gather(*[_probe(item) for item in pool], return_exceptions=False)
    if persist:
        by_ident = {proxy_identity(item): to_persist_item(item) for item in load_custom_proxy_items()}
        for item in results:
            by_ident[proxy_identity(item)] = to_persist_item(item)
        persist_custom_proxies(list(by_ident.values()))
    healthy = sum(1 for item in results if item.get("healthy"))
    return {
        "success": True,
        "tested": len(results),
        "healthy": healthy,
        "results": results,
        "proxies": list_custom_proxies(),
        "message": f"已完成 {len(results)} 个自建代理测活，{healthy} 个连通",
    }


async def import_proxy_text_async(
    text: str,
    *,
    probe: bool = False,
    replace: bool = False,
    default_scheme: str = "socks5",
    default_country: Optional[str] = None,
    concurrency: int = 4,
) -> Dict[str, Any]:
    parsed = parse_proxy_text(text, default_scheme=default_scheme, default_country=default_country)
    incoming = parsed["proxies"]
    existing = [] if replace else [to_persist_item(item) for item in load_custom_proxy_items()]
    merged, stats = merge_imported_proxies(existing, incoming, replace=replace)
    persist_custom_proxies(merged)

    probe_result = None
    if probe and incoming:
        incoming_idents = {proxy_identity(item) for item in incoming}
        targets = [item for item in merged if proxy_identity(item) in incoming_idents]
        probe_result = await probe_custom_proxies(targets, persist=True, concurrency=concurrency)
        merged = [to_persist_item(item) for item in load_custom_proxy_items()]

    proxies = [normalize_custom_proxy_item(item) or item for item in merged]
    message = (
        f"已解析 {parsed['parsed']} 条，新增 {stats['added']}，"
        f"更新 {stats['updated']}，当前自建池 {len(proxies)} 条"
    )
    if probe_result:
        message += f"；已测活 {probe_result.get('tested', 0)} 个，连通 {probe_result.get('healthy', 0)} 个"
    return {
        "success": True,
        "parsed": parsed["parsed"],
        "imported": stats["added"],
        "updated": stats["updated"],
        "skipped": parsed["skipped"],
        "skipped_count": parsed["skipped_count"],
        "total": len(proxies),
        "proxies": proxies,
        "probe": probe_result,
        "message": message,
    }


def select_custom_proxy(
    country: Optional[str] = None,
    *,
    allow_unlabeled: bool = False,
) -> Optional[Dict[str, Any]]:
    """按国家从自建池挑选节点：健康优先，其次低延迟。"""
    items = list_custom_proxies()
    if not items:
        return None
    regional = [item for item in items if match_proxy_country(item, country)] if country else list(items)
    if not regional and allow_unlabeled:
        regional = [item for item in items if not item.get("country_code") and not item.get("country")]
    if not regional:
        return None

    def _score(item: Dict[str, Any]) -> Tuple[int, float, str]:
        if item.get("healthy") is True:
            health_rank = 0
        elif item.get("healthy") is False:
            health_rank = 2
        else:
            health_rank = 1
        latency = item.get("latency_ms")
        latency_rank = float(latency) if isinstance(latency, (int, float)) else 10_000.0
        return (health_rank, latency_rank, proxy_identity(item))

    chosen = sorted(regional, key=_score)[0]
    return normalize_custom_proxy_item(chosen) or chosen


def custom_pool_summary(country: Optional[str] = None) -> Dict[str, Any]:
    items = list_custom_proxies()
    regional = [item for item in items if match_proxy_country(item, country)] if country else items
    countries = sorted({
        (item.get("country_code") or item.get("country") or "").upper()
        for item in items
        if item.get("country_code") or item.get("country")
    })
    return {
        "total": len(items),
        "regional": len(regional),
        "healthy": sum(1 for item in items if item.get("healthy") is True),
        "unhealthy": sum(1 for item in items if item.get("healthy") is False),
        "pending": sum(1 for item in items if item.get("healthy") is None),
        "countries": [code for code in countries if code],
        "country": country,
    }


__all__ = [
    "apply_probe_result",
    "custom_pool_summary",
    "delete_custom_proxies",
    "find_custom_proxy",
    "import_proxy_text",
    "import_proxy_text_async",
    "is_custom_proxy",
    "list_custom_proxies",
    "make_custom_proxy_id",
    "merge_imported_proxies",
    "merge_proxy_pools",
    "parse_proxy_line",
    "parse_proxy_text",
    "persist_custom_proxies",
    "probe_custom_proxies",
    "select_custom_proxy",
    "to_persist_item",
]
