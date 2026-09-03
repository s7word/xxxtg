"""Proxy-Seller 多径中继网关客户端。

官方 API 规范 (https://docs.proxy-seller.com/):
    Base: https://proxy-seller.com/personal/api/v1/{api_key}/

    列表:
        GET  /proxy/list            返回账户下全部类型的活跃代理
        GET  /proxy/list/{type}     ipv4 / ipv6 / mobile / isp / mix / mix_isp / resident
        Query: country=Alpha3 (USA/CHL/...), latest=Y/N, orderId, ends=Y
    住宅流量包列表 (xxxtg 专用 {ISO2}_tg，禁止改动 bot_*):
        GET  /resident/lists        账户下全部住宅列表
        POST /resident/list/add     创建列表（不另扣「买 IP」费，单测必须 mock）
        GET  /resident/geo
        GET  /resident/package
        连接: {login}:{password}@res.proxy-seller.com:10000–10999

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
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import httpx

from backend.app.services.net_utils import format_httpx_proxy_url

logger = logging.getLogger("MultipathRelayGatewayService")

# ISO-2 -> (alpha3, 英文名, 其它别名...)
# 用于精准 / 模糊匹配 Proxy-Seller 返回的 country / country_alpha3。
COUNTRY_PROFILES: Dict[str, Tuple[str, ...]] = {
    "ae": ("are", "united arab emirates", "uae", "dubai"),
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
    "eg": ("egy", "egypt"),
    "es": ("esp", "spain"),
    "fr": ("fra", "france"),
    "gb": ("gbr", "united kingdom", "uk", "great britain", "england"),
    "id": ("idn", "indonesia"),
    "in": ("ind", "india"),
    "it": ("ita", "italy"),
    "jp": ("jpn", "japan"),
    "ke": ("ken", "kenya"),
    "kr": ("kor", "south korea", "korea", "republic of korea"),
    "kz": ("kaz", "kazakhstan"),
    "ma": ("mar", "morocco", "maroc"),
    "mx": ("mex", "mexico"),
    "ng": ("nga", "nigeria"),
    "nl": ("nld", "netherlands", "holland"),
    "pe": ("per", "peru"),
    "ph": ("phl", "philippines"),
    "pk": ("pak", "pakistan"),
    "pl": ("pol", "poland"),
    "ro": ("rou", "romania"),
    "ru": ("rus", "russia", "russian federation"),
    "sa": ("sau", "saudi arabia", "ksa"),
    "sg": ("sgp", "singapore"),
    "th": ("tha", "thailand"),
    "tr": ("tur", "turkey", "turkiye"),
    "ua": ("ukr", "ukraine"),
    "us": ("usa", "united states", "united states of america", "america"),
    "uz": ("uzb", "uzbekistan"),
    "vn": ("vnm", "vietnam", "viet nam"),
    "za": ("zaf", "south africa"),
}

PROXY_TYPE_BUCKETS = ("ipv4", "ipv6", "mobile", "isp", "mix", "mix_isp", "resident")
CACHE_TTL_SECONDS = 90.0
DEFAULT_PROBE_TIMEOUT = 8.0

# 云主机出口 IP 无法固定加入 Proxy-Seller API 白名单时，直接使用这批带账密的
# 专属住宅/动态节点作为内置候选池，保证自动分配与测活不依赖 API。
# 同一 host 上不同账密对应不同区域隧道，因此按 region 分组，identity 必须带 username。
STATIC_RESIDENTIAL_HOST = "res.proxy-seller.com"
STATIC_CATALOG_TYPE = "resident_static"
RESIDENT_TG_SOURCE = "resident_tg"
RESIDENT_TG_CATALOG = "resident_tg"
RESIDENT_TG_PORT_START = 10000
RESIDENT_TG_PORT_CAP = 20
RESIDENT_TG_TITLE_RE = re.compile(r"^([A-Za-z]{2})_tg$", re.IGNORECASE)
API_TOOLS_TITLES = frozenset({"api-tools", "apitools"})
STATIC_REGIONAL_POOLS: Dict[str, Dict[str, Any]] = {
    "cl": {
        "iso2": "cl",
        "country": "Chile",
        "country_alpha3": "CHL",
        "username": "2c131619348a4a7c",
        "password": "aU9dcl6IekEYLmtv",
        "ports": (10000, 10001, 10002, 10003, 10004),
    },
    "in": {
        "iso2": "in",
        "country": "India",
        "country_alpha3": "IND",
        "username": "2f11184ffd63ed46",
        "password": "hUWcsFGSugR5CtrD",
        "ports": (10000, 10001, 10002, 10003, 10004, 10005, 10006, 10007, 10008, 10009),
    },
}
# 向后兼容：历史代码 / 测试默认指向智利静态池
STATIC_RESIDENTIAL_USERNAME = STATIC_REGIONAL_POOLS["cl"]["username"]
STATIC_RESIDENTIAL_PASSWORD = STATIC_REGIONAL_POOLS["cl"]["password"]
STATIC_RESIDENTIAL_PORTS = STATIC_REGIONAL_POOLS["cl"]["ports"]
STATIC_INDIA_USERNAME = STATIC_REGIONAL_POOLS["in"]["username"]
STATIC_INDIA_PASSWORD = STATIC_REGIONAL_POOLS["in"]["password"]
STATIC_INDIA_PORTS = STATIC_REGIONAL_POOLS["in"]["ports"]
IP_PROBE_ENDPOINTS = (
    "https://api.ip.sb/geoip",
    "https://api.ip.sb/ip",
    "https://ipapi.co/json/",
    "https://ipinfo.io/json",
)

# NANP (+1) 加拿大区号。未命中时 +1 回落美国。
NANP_CA_AREA_CODES = frozenset({
    "204", "226", "236", "249", "250", "257", "263", "289",
    "306", "343", "354", "365", "367", "368", "382", "387",
    "403", "416", "418", "428", "431", "437", "438", "450",
    "468", "474", "506", "514", "519", "548", "579", "581",
    "584", "587", "604", "613", "639", "647", "672", "683",
    "705", "709", "742", "753", "778", "780", "782", "807",
    "819", "825", "867", "873", "879", "902", "905",
})

# E.164 国际字冠 -> ISO-2。按最长前缀匹配，避免 1 / 7 这类共享字冠误伤。
# +1 默认 us，加拿大区号由 infer_country_from_phone 二次判定；
# +7 默认 ru，哈萨克 6x/7x 由 infer_country_from_phone 二次判定。
PHONE_DIAL_TO_ISO2: Dict[str, str] = {
    "1": "us",
    "7": "ru",
    "20": "eg",
    "27": "za",
    "31": "nl",
    "32": "be",
    "33": "fr",
    "34": "es",
    "39": "it",
    "44": "gb",
    "48": "pl",
    "49": "de",
    "51": "pe",
    "52": "mx",
    "54": "ar",
    "55": "br",
    "56": "cl",
    "57": "co",
    "61": "au",
    "62": "id",
    "63": "ph",
    "65": "sg",
    "66": "th",
    "81": "jp",
    "82": "kr",
    "84": "vn",
    "86": "cn",
    "90": "tr",
    "91": "in",
    "92": "pk",
    "93": "af",
    "98": "ir",
    "212": "ma",
    "234": "ng",
    "254": "ke",
    "380": "ua",
    "420": "cz",
    "880": "bd",
    "966": "sa",
    "971": "ae",
    "998": "uz",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def infer_country_from_phone(phone: Optional[str]) -> Optional[str]:
    """从 +E.164 / 裸数字手机号推断 ISO-2。

    +91 → in，+56 → cl；+1 按 NANP 区号智能区分 us/ca；
    +7 按 6x/7x 字冠智能区分 ru/kz。
    """
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return None
    if digits.startswith("1") and len(digits) >= 4 and digits[1:4] in NANP_CA_AREA_CODES:
        return "ca"
    if digits.startswith("7") and len(digits) >= 2 and digits[1] in {"6", "7"}:
        return "kz"
    best_iso: Optional[str] = None
    best_len = 0
    for prefix, iso2 in PHONE_DIAL_TO_ISO2.items():
        if digits.startswith(prefix) and len(prefix) > best_len:
            best_iso = iso2
            best_len = len(prefix)
    return best_iso


def _geo_aliases_for_iso2(iso2: str) -> Set[str]:
    """用全球地理目录补全 COUNTRY_PROFILES 未收录国家的别名（如 MA/摩洛哥）。"""
    code = _norm(iso2)
    if not code or len(code) != 2 or not code.isalpha():
        return set()
    family: Set[str] = {code}
    try:
        from backend.app.services.geo_catalog import lookup_country

        row = lookup_country(code)
    except Exception:
        row = None
    if not row:
        return family
    for key in ("code", "iso2", "iso3", "name", "name_en", "name_zh"):
        val = _norm(row.get(key))
        if val:
            family.add(val)
    return {item for item in family if item}


def expand_country_aliases(query: Optional[str]) -> Set[str]:
    """把 ISO-2 / ISO-3 / 国家名展开为可互相比对的别名集合。

    两位码只做精确命中，避免 `id` 误匹配 India / `in` 误匹配 Indonesia。
    国家全称才启用子串模糊匹配。未在 COUNTRY_PROFILES 的国家回落到 geo_catalog。
    """
    token = _norm(query)
    if not token:
        return set()
    aliases: Set[str] = {token}
    hit_profile = False
    for iso2, extras in COUNTRY_PROFILES.items():
        names = {_norm(item) for item in extras}
        alpha3 = _norm(extras[0]) if extras else ""
        family = {iso2, alpha3, *names}
        if token == iso2 or (alpha3 and token == alpha3) or token in names:
            aliases.update(family)
            hit_profile = True
            continue
        if len(token) >= 4 and any(len(name) >= 4 and (token in name or name in token) for name in names):
            aliases.update(family)
            hit_profile = True
    if not hit_profile:
        try:
            from backend.app.services.geo_catalog import resolve_iso2

            resolved = resolve_iso2(token)
        except Exception:
            resolved = None
        if resolved:
            aliases.update(_geo_aliases_for_iso2(resolved))
        elif len(token) == 2 and token.isalpha():
            aliases.update(_geo_aliases_for_iso2(token))
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
    try:
        from backend.app.services.geo_catalog import resolve_iso2, lookup_country

        iso2 = resolve_iso2(token)
        row = lookup_country(iso2) if iso2 else None
        if row and row.get("iso3"):
            return str(row["iso3"]).upper()
    except Exception:
        pass
    return None


def _parse_ip_probe_payload(data: Any) -> Optional[Dict[str, Any]]:
    """兼容 api.ip.sb / ipapi.co / ipinfo.io 等出口探测响应（JSON 或纯文本 IP）。"""
    if isinstance(data, str):
        token = data.strip()
        # 纯文本 IP（如 https://api.ip.sb/ip）
        if token and " " not in token and 3 <= len(token) <= 45:
            return {
                "ip": token,
                "country": None,
                "country_code": None,
                "city": None,
                "region": None,
                "org": None,
            }
        return None
    if not isinstance(data, dict):
        return None
    ip = data.get("ip") or data.get("query")
    if not ip:
        return None
    country_code = data.get("country_code") or data.get("countryCode")
    country = data.get("country_name")
    raw_country = data.get("country")
    if not country_code and isinstance(raw_country, str) and len(raw_country.strip()) == 2:
        country_code = raw_country.strip().upper()
    if not country:
        country = raw_country if raw_country and len(str(raw_country)) > 2 else country_code
    return {
        "ip": ip,
        "country": country,
        "country_code": str(country_code).upper() if country_code else None,
        "city": data.get("city"),
        "region": data.get("region") or data.get("region_name"),
        "org": data.get("org") or data.get("org_name") or data.get("isp"),
    }


class EgressIpRegistry:
    """进程级真实出口 IP 占用账本：发现多任务复用同一公网 IP 时告警。

    槽位 1:1 只按 host:port:user 互斥，住宅网关背后的真实 egress 仍可能撞车。
    """

    _lock = threading.Lock()
    _holders: Dict[str, Set[str]] = {}

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._holders = {}

    @classmethod
    def note(cls, egress_ip: Optional[str], task_id: str) -> List[str]:
        """登记 task 正在使用该出口 IP，返回其它仍占用同一 IP 的 task_id。"""
        ip = str(egress_ip or "").strip()
        tid = str(task_id or "").strip()
        if not ip or not tid:
            return []
        with cls._lock:
            holders = cls._holders.setdefault(ip, set())
            others = sorted(holders - {tid})
            holders.add(tid)
            return others

    @classmethod
    def release(cls, egress_ip: Optional[str], task_id: str) -> None:
        ip = str(egress_ip or "").strip()
        tid = str(task_id or "").strip()
        if not ip or not tid:
            return
        with cls._lock:
            holders = cls._holders.get(ip)
            if not holders:
                return
            holders.discard(tid)
            if not holders:
                cls._holders.pop(ip, None)


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
    """同一 host:port 上不同账密是不同区域隧道，必须带 username 去重。"""
    user = _norm(proxy.get("username"))
    return f"{proxy.get('addr')}:{proxy.get('port')}:{user}" if user else f"{proxy.get('addr')}:{proxy.get('port')}"


def format_proxy_endpoint(proxy: Dict[str, Any]) -> str:
    protocol = _norm(proxy.get("proxy_type") or "socks5") or "socks5"
    return f"{protocol}://{proxy.get('addr')}:{proxy.get('port')}"


def _resolve_static_regions(region: Optional[str] = None) -> List[str]:
    token = _norm(region)
    if not token:
        return list(STATIC_REGIONAL_POOLS.keys())
    aliases = expand_country_aliases(token)
    matched = [iso2 for iso2 in STATIC_REGIONAL_POOLS if iso2 in aliases]
    if matched:
        return matched
    if token in STATIC_REGIONAL_POOLS:
        return [token]
    return []


def static_residential_count(region: Optional[str] = None) -> int:
    return len(builtin_static_residential_items(region))


def builtin_static_residential_items(region: Optional[str] = None) -> List[Dict[str, Any]]:
    """内置 Proxy-Seller 多区域专属住宅节点。

    - cl: 端口 10000-10004，智利账密，出口 Chile
    - in: 端口 10000-10009，印度账密，出口 India
    target_country=in / +91 会命中印度池；target_country=cl 仍命中智利池。
    """
    collected: List[Dict[str, Any]] = []
    for iso2 in _resolve_static_regions(region):
        spec = STATIC_REGIONAL_POOLS[iso2]
        for port in spec["ports"]:
            normalized = normalize_proxy_item(
                {
                    "id": f"static-res-{iso2}-{port}",
                    "ip": STATIC_RESIDENTIAL_HOST,
                    "protocol": "socks5",
                    "port": int(port),
                    "login": spec["username"],
                    "password": spec["password"],
                    "country": spec["country"],
                    "country_alpha3": spec["country_alpha3"],
                    "country_code": iso2,
                    "status": "ACTIVE",
                    "status_type": "ACTIVE",
                    "type": STATIC_CATALOG_TYPE,
                },
                bucket=STATIC_CATALOG_TYPE,
            )
            if not normalized:
                continue
            normalized["source"] = "static_residential"
            normalized["region"] = iso2
            collected.append(normalized)
    return collected


def merge_proxy_pools(*pools: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 addr:port 去重合并多个候选池，靠前的池优先保留。"""
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for pool in pools:
        for item in pool or []:
            if not isinstance(item, dict):
                continue
            ident = proxy_identity(item)
            if not ident or ident in seen:
                continue
            seen.add(ident)
            merged.append(dict(item))
    return merged


def is_static_residential(proxy: Optional[Dict[str, Any]]) -> bool:
    if not proxy:
        return False
    return (
        _norm(proxy.get("catalog_type")) == STATIC_CATALOG_TYPE
        or _norm(proxy.get("source")) == "static_residential"
    )


def is_custom_proxy(proxy: Optional[Dict[str, Any]]) -> bool:
    if not proxy:
        return False
    return (
        _norm(proxy.get("source")) == "custom"
        or _norm(proxy.get("catalog_type")) == "custom"
        or str(proxy.get("id") or "").startswith("custom-")
    )


def is_resident_tg(proxy: Optional[Dict[str, Any]]) -> bool:
    if not proxy:
        return False
    return (
        _norm(proxy.get("source")) == RESIDENT_TG_SOURCE
        or _norm(proxy.get("catalog_type")) == RESIDENT_TG_CATALOG
    )


def is_bot_list_title(title: Any) -> bool:
    """bot_* / bot_api* / 含 _bot token 的列表一律视为 autoc_tg 资产，禁止读写改。"""
    text = _norm(title)
    if not text:
        return False
    if text.startswith("bot_") or text.startswith("bot_api") or text.startswith("botapi"):
        return True
    if "_bot" in text:
        return True
    return False


def is_xxxtg_list_title(title: Any) -> bool:
    """大小写不敏感，以 _tg 结尾，且不是 bot 列表。"""
    text = _norm(title)
    if not text.endswith("_tg"):
        return False
    return not is_bot_list_title(title)


def find_api_tools_list(lists: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Api-Tools 是住宅流量包的通用连接账密；新建 {CC}_tg 列表自带 login 实测无法 SOCKS 鉴权。"""
    for row in lists or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        if is_bot_list_title(title):
            continue
        login = str(row.get("login") or "").strip()
        password = str(row.get("password") or "").strip()
        if not login or not password:
            continue
        token = title.strip().lower()
        if token in API_TOOLS_TITLES or login.lower().startswith("api"):
            return row
    return None


def tools_auth_from_lists(lists: Iterable[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    tools = find_api_tools_list(lists)
    if not tools:
        return None, None
    login = str(tools.get("login") or "").strip() or None
    password = str(tools.get("password") or "").strip() or None
    return login, password


def build_resident_tg_username(
    base_login: str,
    *,
    country: Optional[str] = None,
    port: Optional[int] = None,
) -> str:
    """login_c_CL_s_tg10000_ttl_24h — 用 Api-Tools 账密按国家钉死出口。"""
    base = (base_login or "").strip()
    if not base:
        return ""
    parts = [base]
    cc = (country or "").strip().upper()
    if len(cc) == 2 and cc.isalpha():
        parts.append(f"c_{cc}")
    if port:
        sid = f"tg{(cc or 'xx').lower()}{int(port)}"
        parts.append(f"s_{sid[:48]}")
        parts.append("ttl_24h")
    return "_".join(parts)


def resolve_iso2_country(query: Optional[str]) -> Optional[str]:
    """把 ISO-2 / ISO-3 / 国家名解析为两位大写 ISO-2。

    优先 COUNTRY_PROFILES；未收录时回落 geo_catalog（支持 MA 等全球国家）。
    任意两位字母若不在地理目录中则拒绝，避免 invent 出 XX_tg。
    """
    token = _norm(query)
    if not token:
        return None
    aliases = expand_country_aliases(token)
    for iso2 in COUNTRY_PROFILES:
        if iso2 in aliases:
            return iso2.upper()
    try:
        from backend.app.services.geo_catalog import lookup_country, resolve_iso2

        resolved = resolve_iso2(token)
        if resolved and lookup_country(resolved):
            return resolved.upper()
    except Exception:
        pass
    return None


def tg_list_title_for_country(country: str) -> str:
    return f"{str(country).strip().upper()}_tg"


def country_code_from_tg_title(title: Any) -> Optional[str]:
    text = str(title or "").strip()
    match = RESIDENT_TG_TITLE_RE.match(text)
    if not match:
        return None
    return match.group(1).lower()


def parse_resident_geo(geo: Any) -> Dict[str, str]:
    """官方文档写成 object，实际常见为 list[{country, region, city, isp}]。"""
    if isinstance(geo, list):
        geo = geo[0] if geo else {}
    if not isinstance(geo, dict):
        return {"country": "", "region": "", "city": "", "isp": ""}
    raw = geo.get("country") or geo.get("code") or geo.get("iso") or geo.get("iso2") or ""
    country = str(raw).strip().upper()
    return {
        "country": country,
        "region": str(geo.get("region") or ""),
        "city": str(geo.get("city") or ""),
        "isp": str(geo.get("isp") or ""),
    }


def parse_export_ports(export: Any, default: int = 10) -> int:
    """export.ports 经常是字符串 '50'，表示条数而不是起始端口。"""
    raw = export.get("ports") if isinstance(export, dict) else export
    parsed = _as_int(raw, default)
    if parsed is None or parsed <= 0:
        return default
    return parsed


def _looks_like_resident_list(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    title = row.get("title")
    if not isinstance(title, str) or not title.strip():
        return False
    if row.get("ip") or row.get("ip_only") or row.get("port_socks5") or row.get("port_socks"):
        return False
    return True


def _resident_lists_payload_recognized(payload: Any) -> bool:
    """区分真正的 /resident/lists 与被 mock 成 /proxy/list 的载荷，避免误 POST list/add。"""
    if isinstance(payload, list):
        if not payload:
            return True
        return any(_looks_like_resident_list(item) for item in payload)
    if not isinstance(payload, dict):
        return False
    if _looks_like_resident_list(payload):
        return True
    nested = payload.get("items")
    if not isinstance(nested, list):
        nested = payload.get("lists")
    if isinstance(nested, list):
        if not nested:
            return True
        return any(_looks_like_resident_list(item) for item in nested)
    return False


def _extract_resident_list_rows(payload: Any) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    rows: List[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if any(key in payload for key in PROXY_TYPE_BUCKETS) and "title" not in payload:
            return []
        nested = payload.get("items")
        if not isinstance(nested, list):
            nested = payload.get("lists")
        if isinstance(nested, list):
            rows = nested
        elif _looks_like_resident_list(payload):
            rows = [payload]
    return [item for item in rows if _looks_like_resident_list(item)]


def parse_resident_lists_payload(data: Any) -> Tuple[List[Dict[str, Any]], bool]:
    if isinstance(data, dict) and data.get("data") is not None:
        payload = data.get("data")
    else:
        payload = data
    return _extract_resident_list_rows(payload), _resident_lists_payload_recognized(payload)


def find_tg_resident_list(lists: Iterable[Dict[str, Any]], iso2: str) -> Optional[Dict[str, Any]]:
    """优先 title 精确等于 {CC}_tg；其次 *_tg 且 geo.country 匹配。跳过全部 bot 列表。"""
    wanted = tg_list_title_for_country(iso2)
    wanted_cc = str(iso2).strip().upper()
    geo_hit: Optional[Dict[str, Any]] = None
    for row in lists or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        if is_bot_list_title(title) or not is_xxxtg_list_title(title):
            continue
        if title.strip().lower() == wanted.lower():
            return row
        geo_cc = parse_resident_geo(row.get("geo")).get("country") or ""
        if geo_cc == wanted_cc and geo_hit is None:
            geo_hit = row
    return geo_hit


def resident_list_to_proxies(
    list_row: Optional[Dict[str, Any]],
    *,
    max_ports: int = 10,
    tools_login: Optional[str] = None,
    tools_password: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """把 xxxtg *_tg 列表归一化为 res.proxy-seller.com:10000.. 节点。绝不转换 bot 列表。

    连接账密优先用 Api-Tools（加 _c_{CC} 钉国家）；列表自身 login 仅作后备。
    """
    if not isinstance(list_row, dict):
        return []
    title = str(list_row.get("title") or "")
    if is_bot_list_title(title) or not is_xxxtg_list_title(title):
        return []
    list_login = str(list_row.get("login") or list_row.get("username") or "").strip()
    list_password = str(list_row.get("password") or "").strip()
    login = (tools_login or "").strip() or list_login
    password = (tools_password or "").strip() or list_password
    if not login:
        return []

    geo = parse_resident_geo(list_row.get("geo"))
    iso2 = None
    if geo.get("country"):
        iso2 = resolve_iso2_country(geo["country"]) or (
            geo["country"].lower() if len(geo["country"]) == 2 else None
        )
    title_iso = country_code_from_tg_title(title)
    if title_iso and (not iso2 or title.strip().lower() == tg_list_title_for_country(title_iso).lower()):
        # 精确 {CC}_tg 标题优先用于国家推断
        if title_iso:
            iso2 = title_iso
    elif not iso2 and title_iso:
        iso2 = title_iso
    if iso2:
        iso2 = iso2.lower()

    cap = _as_int(max_ports, 10) or 10
    cap = max(1, min(cap, RESIDENT_TG_PORT_CAP))
    parsed_ports = parse_export_ports(list_row.get("export"), default=10)
    n = min(parsed_ports or 10, cap, RESIDENT_TG_PORT_CAP)
    n = max(1, n)

    list_id = list_row.get("id")
    rotation = list_row.get("rotation")
    country_name = None
    alpha3 = None
    if iso2:
        extras = COUNTRY_PROFILES.get(iso2)
        if extras:
            country_name = extras[1] if len(extras) > 1 else iso2.upper()
            alpha3 = extras[0].upper()
        else:
            country_name = iso2.upper()

    collected: List[Dict[str, Any]] = []
    for offset in range(n):
        port = RESIDENT_TG_PORT_START + offset
        conn_user = build_resident_tg_username(login, country=iso2, port=port)
        normalized = normalize_proxy_item(
            {
                "id": f"resident-tg-{list_id or title}-{port}",
                "ip": STATIC_RESIDENTIAL_HOST,
                "protocol": "socks5",
                "port": int(port),
                "login": conn_user or login,
                "password": password,
                "country": country_name or (iso2.upper() if iso2 else None),
                "country_alpha3": alpha3,
                "country_code": iso2,
                "status": "ACTIVE",
                "status_type": "ACTIVE",
                "type": RESIDENT_TG_CATALOG,
                "rotation": rotation,
            },
            bucket=RESIDENT_TG_CATALOG,
        )
        if not normalized:
            continue
        normalized["source"] = RESIDENT_TG_SOURCE
        normalized["catalog_type"] = RESIDENT_TG_CATALOG
        normalized["list_id"] = list_id
        normalized["list_title"] = title
        normalized["region"] = iso2
        normalized.pop("raw", None)
        collected.append(normalized)
    return collected


def _package_is_active(pkg: Optional[Dict[str, Any]]) -> Optional[bool]:
    if not isinstance(pkg, dict) or not pkg:
        return None
    if "is_active" in pkg:
        return bool(pkg.get("is_active"))
    if "active" in pkg:
        return bool(pkg.get("active"))
    status = _norm(pkg.get("status") or pkg.get("state"))
    if status in {"active", "ok", "enabled", "valid"}:
        return True
    if status in {"inactive", "expired", "disabled"}:
        return False
    return None


def _source_rank(item: Dict[str, Any]) -> int:
    if is_custom_proxy(item):
        return 0
    if is_resident_tg(item):
        return 1
    if is_static_residential(item):
        return 2
    return 3


def normalize_custom_proxy_item(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把用户粘贴/持久化的自定义代理归一化为调度结构。"""
    if not isinstance(item, dict):
        return None
    addr = item.get("addr") or item.get("ip") or item.get("host")
    port = _as_int(item.get("port"))
    if not addr or not port:
        return None
    username = item.get("username") or item.get("login") or item.get("user")
    password = item.get("password") or item.get("pass")
    country_name = item.get("country") or item.get("egress_country") or item.get("country_name")
    alpha2 = item.get("country_code") or item.get("egress_country_code") or item.get("iso2")
    alpha3 = item.get("country_alpha3") or item.get("alpha3")
    if not alpha2 and country_name:
        aliases = expand_country_aliases(str(country_name))
        for iso2 in COUNTRY_PROFILES:
            if iso2 in aliases:
                alpha2 = iso2
                break
    normalized = normalize_proxy_item(
        {
            "id": item.get("id"),
            "ip": addr,
            "protocol": item.get("proxy_type") or item.get("protocol") or "socks5",
            "port": port,
            "login": username,
            "password": password,
            "country": country_name,
            "country_alpha3": alpha3,
            "country_code": _norm(alpha2) or None,
            "status": item.get("status") or ("ACTIVE" if item.get("healthy") else "custom"),
            "status_type": item.get("status_type") or ("ACTIVE" if item.get("healthy") else "CUSTOM"),
            "type": "custom",
        },
        bucket="custom",
    )
    if not normalized:
        return None
    if not normalized.get("id"):
        user = _norm(username)
        ident = f"{addr}:{port}:{user}" if user else f"{addr}:{port}"
        normalized["id"] = f"custom-{ident}"
    normalized["source"] = "custom"
    normalized["region"] = _norm(alpha2) or normalized.get("country_code")
    normalized["city"] = item.get("city")
    normalized["healthy"] = item.get("healthy")
    normalized["egress_ip"] = item.get("egress_ip") or item.get("ip")
    normalized["egress_country"] = item.get("egress_country") or country_name
    normalized["egress_country_code"] = _norm(item.get("egress_country_code") or alpha2) or None
    normalized["latency_ms"] = item.get("latency_ms")
    normalized["last_error"] = item.get("last_error") or item.get("error")
    normalized["checked_at"] = item.get("checked_at")
    normalized["raw_line"] = item.get("raw_line")
    role = _norm(item.get("role")) or "all"
    if role not in {"all", "registration", "precheck"}:
        role = "all"
    normalized["role"] = role
    assigned = _norm(item.get("assigned_country")) or None
    normalized["assigned_country"] = assigned
    normalized.pop("raw", None)
    return normalized


def load_custom_proxy_items() -> List[Dict[str, Any]]:
    """从全局配置读取用户自建代理池。配置尚未就绪时返回空列表。"""
    try:
        from backend.app.config import ConfigManager

        raw_items = getattr(ConfigManager.get_instance().config, "custom_proxies", None) or []
    except Exception:
        return []
    collected: List[Dict[str, Any]] = []
    for item in raw_items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        normalized = normalize_custom_proxy_item(data)
        if normalized:
            collected.append(normalized)
    return collected


def custom_residential_count(country: Optional[str] = None) -> int:
    items = load_custom_proxy_items()
    if country:
        items = [item for item in items if match_proxy_country(item, country)]
    return len(items)


class ProxySellerService:
    """多径传输出口中继网关服务 (Multipath Egress Relay Gateway Provider)"""

    BASE_URL = "https://proxy-seller.com/personal/api/v1"

    # 进程内共享: 按 api_key 缓存完整代理池 + 单节点健康状态，便于轮换。
    _pool_cache: Dict[str, Dict[str, Any]] = {}
    _health: Dict[str, Dict[str, Any]] = {}
    _rr_cursor: Dict[str, int] = {}

    def __init__(
        self,
        api_key: str,
        cache_ttl: float = CACHE_TTL_SECONDS,
        include_static: bool = True,
    ):
        self.api_key = (api_key or "").strip()
        self.cache_ttl = cache_ttl
        self.include_static = include_static
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

    @classmethod
    def invalidate_all_caches(cls) -> None:
        cls._pool_cache.clear()

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
        try:
            resp = await self.client.request(method, url, params=params, json=json_body)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else "网络错误"
            raise RuntimeError(f"Proxy-Seller 请求失败 ({detail})") from None
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Proxy-Seller 响应不是合法 JSON (HTTP {resp.status_code})") from exc
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
        api_items: List[Dict[str, Any]] = []
        api_error: Optional[str] = None
        if not self.api_key:
            api_error = "未配置 Proxy-Seller API Key"
        else:
            try:
                api_items = await self._fetch_remote_items(proxy_type=proxy_type)
            except Exception as exc:
                api_error = str(exc)
                if self.include_static:
                    logger.warning(
                        "Proxy-Seller API 不可用（%s），回退到内置静态住宅代理池",
                        api_error,
                    )
                else:
                    logger.warning("Proxy-Seller API 不可用（%s）", api_error)

        static_items = builtin_static_residential_items() if self.include_static else []
        custom_items = load_custom_proxy_items()
        resident_items: List[Dict[str, Any]] = []
        if self.api_key:
            try:
                raw_lists, recognized = await self._load_resident_lists()
                if recognized or raw_lists:
                    tools_login, tools_password = tools_auth_from_lists(raw_lists)
                    for row in raw_lists:
                        resident_items.extend(
                            resident_list_to_proxies(
                                row,
                                max_ports=10,
                                tools_login=tools_login,
                                tools_password=tools_password,
                            )
                        )
            except Exception as exc:
                logger.warning("Proxy-Seller 住宅 _tg 列表不可用（%s）", exc)
        # 自建 > xxxtg *_tg 住宅列表 > 官方 /proxy/list > 内置静态 CL/IN
        items = merge_proxy_pools(custom_items, resident_items, api_items, static_items)
        if not items:
            if api_error:
                raise RuntimeError(api_error)
            raise RuntimeError("Proxy-Seller 账户下没有检索到活跃代理，且未启用内置静态住宅池/自建代理池")

        parts = []
        if custom_items:
            parts.append("custom")
        if resident_items:
            parts.append("resident_tg")
        if api_items:
            parts.append("api")
        if static_items:
            parts.append("static")
        source = "+".join(parts) if parts else "empty"
        if source == "custom":
            source = "custom_pool"
        elif source == "static":
            source = "static_residential"
        elif source == "resident_tg":
            source = "resident_tg"
        entry = self._cache_entry()
        entry["items"] = items
        entry["fetched_at"] = time.time()
        entry["api_error"] = api_error
        entry["api_count"] = len(api_items)
        entry["static_count"] = len(static_items)
        entry["custom_count"] = len(custom_items)
        entry["resident_count"] = len(resident_items)
        entry["source"] = source
        logger.info(
            "已刷新出口中继池: 合计 %s 个节点 (自建=%s, _tg住宅=%s, API=%s, 静态住宅=%s, source=%s)",
            len(items),
            len(custom_items),
            len(resident_items),
            len(api_items),
            len(static_items),
            source,
        )
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
        if health:
            view["healthy"] = health.get("healthy")
            view["egress_ip"] = health.get("egress_ip") or view.get("egress_ip")
            view["egress_country"] = health.get("egress_country") or view.get("egress_country")
            view["egress_country_code"] = health.get("country_code") or view.get("egress_country_code")
            if health.get("country_code") and not view.get("country_code"):
                view["country_code"] = health.get("country_code")
            view["city"] = health.get("city") or view.get("city")
            view["latency_ms"] = health.get("latency_ms") if health.get("latency_ms") is not None else view.get("latency_ms")
            view["last_error"] = health.get("error")
            view["checked_at"] = health.get("checked_at") or view.get("checked_at")
        else:
            view.setdefault("healthy", proxy.get("healthy"))
            view.setdefault("egress_ip", proxy.get("egress_ip"))
            view.setdefault("egress_country", proxy.get("egress_country"))
            view.setdefault("egress_country_code", proxy.get("egress_country_code") or proxy.get("country_code"))
            view.setdefault("city", proxy.get("city"))
            view.setdefault("latency_ms", proxy.get("latency_ms"))
            view.setdefault("last_error", proxy.get("last_error"))
            view.setdefault("checked_at", proxy.get("checked_at"))
        return view

    def record_health(self, proxy: Dict[str, Any], result: Dict[str, Any]) -> None:
        self._health[proxy_identity(proxy)] = {
            "healthy": bool(result.get("success")),
            "checked_at": time.time(),
            "egress_ip": result.get("ip"),
            "egress_country": result.get("country"),
            "country_code": _norm(result.get("country_code")) or None,
            "city": result.get("city"),
            "latency_ms": result.get("latency_ms"),
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
            "source": entry.get("source"),
            "api_error": entry.get("api_error"),
            "api_count": entry.get("api_count"),
            "static_count": entry.get("static_count"),
            "custom_count": entry.get("custom_count"),
            "resident_count": entry.get("resident_count"),
        }

    def _sort_candidates(self, proxies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def score(item: Dict[str, Any]) -> Tuple[int, int, int, str]:
            health = self._health.get(proxy_identity(item)) or {}
            persisted_healthy = item.get("healthy")
            healthy = health.get("healthy") if health else persisted_healthy
            if healthy is True:
                health_rank = 0
            elif healthy is False:
                health_rank = 2
            else:
                health_rank = 1
            source_rank = _source_rank(item)
            status = _norm(item.get("status_type") or item.get("status"))
            active_rank = 0 if (not status or "active" in status or "custom" in status) else 1
            return (health_rank, source_rank, active_rank, proxy_identity(item))

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
        phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按目标国家严格挑选区域代理；禁止把智利/印度等互不相干大区隐式交叉分配。

        target_country 为空时可根据 phone（如 +91）推断区域，从而命中印度住宅池。
        指定区域后若全军覆没，不跨大区强行选节点；由调用方优雅降级到 fallback_proxy。
        allow_fallback 仅表示「允许调用方使用配置后备」，不再从其它区域池里抽节点。
        """
        country = _norm(target_country) or infer_country_from_phone(phone)
        if country and self.api_key:
            try:
                ensured = await self.ensure_tg_resident_list(country, create=True)
                if ensured.get("created") or ensured.get("proxies"):
                    cached = self._cache_entry().get("items") or []
                    have_tg = any(
                        is_resident_tg(item) and match_proxy_country(item, country)
                        for item in cached
                    )
                    if ensured.get("created") or not have_tg:
                        self.invalidate_cache()
                        refresh = True
            except Exception as exc:
                logger.warning("自主确保 xxxtg 住宅列表失败（%s）", exc)
        regional = await self.get_proxy_list(country=country or None, refresh=refresh, include_health=False)
        fallback_used = False
        source = "regional"
        hint = None

        if regional:
            candidates = self._rotate(country or "*", self._sort_candidates(regional))
            custom_hit = any(is_custom_proxy(item) for item in regional)
            resident_hit = any(is_resident_tg(item) for item in regional)
            static_hit = any(is_static_residential(item) for item in regional)
            if custom_hit and all(is_custom_proxy(item) for item in regional):
                source = "custom_pool"
            elif resident_hit and all(is_resident_tg(item) for item in regional):
                source = "resident_tg"
            elif static_hit and all(is_static_residential(item) for item in regional):
                source = "static_residential"
            else:
                source = "regional"
            message = f"已匹配到 {len(regional)} 个 {(country or 'ALL').upper()} 区域代理"
            extras = []
            if custom_hit:
                extras.append("用户自建代理池")
            if resident_hit:
                extras.append("xxxtg 专用住宅列表")
            if static_hit:
                extras.append("内置静态住宅节点")
            if extras:
                message += f"（含{' / '.join(extras)}）"
        else:
            candidates = []
            available: List[str] = []
            if country:
                all_items = await self.get_proxy_list(country=None, refresh=False, include_health=False)
                available = sorted({
                    (item.get("country_code") or item.get("country") or "?").upper()
                    for item in all_items
                })
                hint = (
                    f"目标区域 {country.upper()} 暂无可用区域代理"
                    + (f"；账户当前其它区域: {', '.join(available)}" if available else "")
                    + "。已禁止跨大区隐式兜底，请使用配置的 fallback_proxy。"
                )
                source = "config_fallback_required" if allow_fallback else "strict_region_miss"
            else:
                hint = "Proxy-Seller 账户下没有检索到活跃代理。"
                source = "empty_pool"
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

        if selected and is_custom_proxy(selected):
            source = "custom_pool"
            if "自建" not in (message or ""):
                message = f"{message}（用户自建代理池）"
        elif selected and is_resident_tg(selected):
            source = "resident_tg"
            title = selected.get("list_title") or ""
            extra = f"xxxtg 专用住宅列表 {title}".strip()
            if "xxxtg" not in (message or ""):
                message = f"{message}（{extra}）"
        elif selected and is_static_residential(selected):
            source = "static_residential"
            if "内置静态住宅" not in (message or ""):
                message = f"{message}（内置静态住宅节点）"

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

        proxy_url = format_httpx_proxy_url({
            "proxy_type": proxy_type,
            "addr": addr,
            "port": port,
            "username": username,
            "password": password,
        })
        if not proxy_url:
            return {"success": False, "error": "未配置有效的中继跳点主机地址与端口"}

        client_kwargs = {"verify": False, "timeout": timeout}
        started = time.perf_counter()
        try:
            try:
                client = httpx.AsyncClient(proxy=proxy_url, **client_kwargs)
            except TypeError:
                client = httpx.AsyncClient(proxies=proxy_url, **client_kwargs)

            async with client:
                last_error = None
                for endpoint in IP_PROBE_ENDPOINTS:
                    probe_started = time.perf_counter()
                    try:
                        ip_resp = await client.get(endpoint)
                        try:
                            ip_data: Any = ip_resp.json()
                        except Exception:
                            ip_data = (ip_resp.text or "").strip()
                    except Exception as exc:
                        last_error = f"{endpoint}: {exc}"
                        continue
                    parsed = _parse_ip_probe_payload(ip_data)
                    if not parsed:
                        last_error = f"出口探测响应异常: {ip_data!r}"
                        continue
                    latency_ms = round((time.perf_counter() - probe_started) * 1000, 1)
                    parsed.update({
                        "success": True,
                        "latency_ms": latency_ms,
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                        "probe_url": endpoint,
                    })
                    return parsed
                return {
                    "success": False,
                    "error": last_error or "出口探测全部失败",
                    "total_ms": round((time.perf_counter() - started) * 1000, 1),
                }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            }

    probe_relay_path_connectivity = test_proxy_connectivity

    async def _load_resident_lists(self) -> Tuple[List[Dict[str, Any]], bool]:
        data = await self._request("GET", "resident/lists")
        return parse_resident_lists_payload(data)

    async def fetch_resident_lists(self) -> List[Dict[str, Any]]:
        rows, _recognized = await self._load_resident_lists()
        return rows

    async def fetch_resident_package(self) -> Dict[str, Any]:
        data = await self._request("GET", "resident/package")
        payload = data.get("data", data) if isinstance(data, dict) else data
        return payload if isinstance(payload, dict) else {}

    def _created_list_row(self, payload: Any, *, title: str, iso2: str, ports: int, rotation: int) -> Dict[str, Any]:
        row = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(row, dict):
            row = {}
        nested = row.get("item") or row.get("list")
        if isinstance(nested, dict):
            row = nested
        merged = dict(row)
        merged.setdefault("title", title)
        merged.setdefault("geo", {"country": iso2})
        export = merged.get("export") if isinstance(merged.get("export"), dict) else {}
        export.setdefault("ports", ports)
        export.setdefault("ext", "txt")
        merged["export"] = export
        merged.setdefault("rotation", rotation)
        return merged

    async def ensure_tg_resident_list(
        self,
        country: Optional[str],
        *,
        create: bool = False,
        ports: int = 10,
        rotation: int = 3600,
    ) -> Dict[str, Any]:
        """确保目标国家存在 xxxtg 专用 {CC}_tg 列表。绝不改动 bot_* 列表。"""
        iso2 = resolve_iso2_country(country)
        if not iso2:
            hint = f"无法识别国家 {country!s}，请使用 COUNTRY_PROFILES 中的两位 ISO-2（如 CL / ZA）"
            return {
                "success": False,
                "created": False,
                "title": None,
                "proxies": [],
                "hint": hint,
                "message": hint,
            }
        title = tg_list_title_for_country(iso2)
        port_count = max(1, min(_as_int(ports, 10) or 10, RESIDENT_TG_PORT_CAP))
        rotation_val = _as_int(rotation, 3600) or 3600

        try:
            rows, recognized = await self._load_resident_lists()
        except Exception as exc:
            hint = f"读取住宅列表失败: {exc}"
            return {
                "success": False,
                "created": False,
                "title": title,
                "proxies": [],
                "hint": hint,
                "message": hint,
            }

        found = find_tg_resident_list(rows, iso2)
        if found:
            tools_login, tools_password = tools_auth_from_lists(rows)
            proxies = resident_list_to_proxies(
                found,
                max_ports=port_count,
                tools_login=tools_login,
                tools_password=tools_password,
            )
            existing_title = str(found.get("title") or title)
            return {
                "success": True,
                "created": False,
                "title": existing_title,
                "list_id": found.get("id"),
                "proxies": proxies,
                "hint": None,
                "message": f"已存在 xxxtg 专用列表 {existing_title}，导出 {len(proxies)} 个节点",
            }

        if not create:
            hint = (
                f"账户中没有 {title} 列表。设置 create=true 可自主创建"
                "（不会读取或改动 bot_* 列表）"
            )
            return {
                "success": False,
                "created": False,
                "title": title,
                "proxies": [],
                "hint": hint,
                "message": hint,
            }

        if not recognized:
            hint = "住宅列表接口未返回可识别数据，已跳过创建以免误伤已有 bot 列表"
            return {
                "success": False,
                "created": False,
                "title": title,
                "proxies": [],
                "hint": hint,
                "message": hint,
            }

        body = {
            "title": title,
            "whitelist": "",
            "geo": {"country": iso2},
            "export": {"ports": port_count, "ext": "txt"},
            "rotation": rotation_val,
        }
        try:
            created_payload = await self._request("POST", "resident/list/add", json_body=body)
        except Exception as exc:
            hint = f"创建 {title} 失败: {exc}"
            logger.warning("创建 xxxtg 住宅列表失败（title=%s）: %s", title, exc)
            return {
                "success": False,
                "created": False,
                "title": title,
                "proxies": [],
                "hint": hint,
                "message": hint,
            }

        created_row = self._created_list_row(
            created_payload, title=title, iso2=iso2, ports=port_count, rotation=rotation_val
        )
        refreshed_rows = rows
        if not created_row.get("login"):
            try:
                refreshed, _ = await self._load_resident_lists()
                found = find_tg_resident_list(refreshed, iso2)
                if found:
                    created_row = found
                refreshed_rows = refreshed
            except Exception:
                pass

        tools_login, tools_password = tools_auth_from_lists(refreshed_rows)
        proxies = resident_list_to_proxies(
            created_row,
            max_ports=port_count,
            tools_login=tools_login,
            tools_password=tools_password,
        )
        self.invalidate_cache()
        logger.info("已创建 xxxtg 住宅列表 %s（%s 个节点）", title, len(proxies))
        return {
            "success": True,
            "created": True,
            "title": title,
            "list_id": created_row.get("id"),
            "proxies": proxies,
            "hint": None,
            "message": f"已创建 xxxtg 专用列表 {title}，导出 {len(proxies)} 个节点",
        }

    async def summarize_resident_tg_lists(self) -> Dict[str, Any]:
        if not self.api_key:
            return {
                "success": False,
                "message": "未配置 Proxy-Seller API Key",
                "lists": [],
                "bot_skipped": 0,
                "package_active": None,
            }
        try:
            rows, _recognized = await self._load_resident_lists()
        except Exception as exc:
            return {
                "success": False,
                "message": f"读取住宅列表失败: {exc}",
                "lists": [],
                "bot_skipped": 0,
                "package_active": None,
            }

        bot_skipped = 0
        summaries: List[Dict[str, Any]] = []
        for row in rows:
            title = str(row.get("title") or "")
            if is_bot_list_title(title):
                bot_skipped += 1
                continue
            if not is_xxxtg_list_title(title):
                continue
            geo = parse_resident_geo(row.get("geo"))
            country = geo.get("country") or (country_code_from_tg_title(title) or "").upper() or None
            summaries.append({
                "id": row.get("id"),
                "title": title,
                "country": country,
                "ports": parse_export_ports(row.get("export")),
                "rotation": row.get("rotation"),
            })

        package_active: Optional[bool] = None
        try:
            package_active = _package_is_active(await self.fetch_resident_package())
        except Exception:
            package_active = None

        return {
            "success": True,
            "message": f"xxxtg 列表 {len(summaries)} 条，已忽略 {bot_skipped} 条 bot_* 列表",
            "lists": summaries,
            "bot_skipped": bot_skipped,
            "package_active": package_active,
        }

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
