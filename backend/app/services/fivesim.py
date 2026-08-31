"""5SIM (5sim.net) 一手接码客户端。

官方协议 (https://5sim.net/docs/):
  Base URL: https://5sim.net/v1
  鉴权:     Authorization: Bearer <JWT> + Accept: application/json
  余额:     GET /user/profile
  价格库存: GET /guest/prices?product=telegram[&country=indonesia]
  租号:     GET /user/buy/activation/{country}/{operator}/{product}
  查码:     GET /user/check/{id}
  完结:     GET /user/finish/{id}
  取消退款: GET /user/cancel/{id}
  坏号退款: GET /user/ban/{id}

国家参数使用英文全名小写 slug（indonesia / usa / england），
本模块负责 ISO-2 ↔ 5SIM slug 双向转换。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

import httpx

from backend.app.models.schemas import format_sms_max_price, normalize_sms_max_price
from backend.app.services.vaksms import NoNumberAvailableError, is_no_number_error

logger = logging.getLogger("FiveSimService")

BASE_URL = "https://5sim.net/v1"
PROVIDER_NAME = "fivesim"
PROVIDER_LABEL = "5SIM (5sim.net)"
DEFAULT_SERVICE = "tg"
DEFAULT_PRODUCT = "telegram"
DEFAULT_OPERATOR = "any"

# 5SIM 官方 guest/countries 实测 slug + 业务常用别名。
# 优先覆盖「英文全名去空格」无法命中的特殊写法（usa / england / bih / tit ...）。
ISO2_TO_FIVESIM_SPECIAL: Dict[str, str] = {
    "us": "usa",
    "gb": "england",
    "uk": "england",
    "hk": "hongkong",
    "za": "southafrica",
    "ci": "ivorycoast",
    "cz": "czech",
    "ae": "uae",
    "tl": "easttimor",
    "do": "dominicana",
    "ba": "bih",
    "bt": "bhutane",
    "tt": "tit",
    "sv": "salvador",
    "mo": "macau",
    "sz": "swaziland",
    "mk": "northmacedonia",
    "pg": "papuanewguinea",
    "ag": "antiguaandbarbuda",
    "kn": "saintkittsandnevis",
    "lc": "saintlucia",
    "vc": "saintvincentandgrenadines",
    "cv": "capeverde",
    "gq": "equatorialguinea",
    "gf": "frenchguiana",
    "gw": "guineabissau",
    "sl": "sierraleone",
    "lk": "srilanka",
    "bf": "burkinafaso",
    "sa": "saudiarabia",
    "cr": "costarica",
    "pr": "puertorico",
    "nc": "newcaledonia",
    "sb": "solomonislands",
    "cd": "congo",
    "cg": "congo",
    "kr": "southkorea",
    "ss": "southsudan",
    "mm": "myanmar",
    "kp": "northkorea",
}

# 5sim 实测 guest/countries 全量 slug → ISO-2（含特殊拼写）。
FIVESIM_SLUG_TO_ISO2: Dict[str, str] = {
    "afghanistan": "af",
    "albania": "al",
    "algeria": "dz",
    "angola": "ao",
    "antiguaandbarbuda": "ag",
    "argentina": "ar",
    "armenia": "am",
    "aruba": "aw",
    "australia": "au",
    "austria": "at",
    "azerbaijan": "az",
    "bahamas": "bs",
    "bahrain": "bh",
    "bangladesh": "bd",
    "barbados": "bb",
    "belgium": "be",
    "belize": "bz",
    "benin": "bj",
    "bhutane": "bt",
    "bhutan": "bt",
    "bih": "ba",
    "bosnia": "ba",
    "bolivia": "bo",
    "botswana": "bw",
    "brazil": "br",
    "bulgaria": "bg",
    "burkinafaso": "bf",
    "burundi": "bi",
    "cambodia": "kh",
    "cameroon": "cm",
    "canada": "ca",
    "capeverde": "cv",
    "chad": "td",
    "chile": "cl",
    "colombia": "co",
    "comoros": "km",
    "congo": "cd",
    "costarica": "cr",
    "croatia": "hr",
    "cyprus": "cy",
    "czech": "cz",
    "czechia": "cz",
    "denmark": "dk",
    "djibouti": "dj",
    "dominicana": "do",
    "dominican": "do",
    "easttimor": "tl",
    "timorleste": "tl",
    "ecuador": "ec",
    "egypt": "eg",
    "england": "gb",
    "unitedkingdom": "gb",
    "equatorialguinea": "gq",
    "estonia": "ee",
    "ethiopia": "et",
    "finland": "fi",
    "france": "fr",
    "frenchguiana": "gf",
    "gabon": "ga",
    "gambia": "gm",
    "georgia": "ge",
    "germany": "de",
    "ghana": "gh",
    "greece": "gr",
    "guadeloupe": "gp",
    "guatemala": "gt",
    "guinea": "gn",
    "guineabissau": "gw",
    "guyana": "gy",
    "haiti": "ht",
    "honduras": "hn",
    "hongkong": "hk",
    "hungary": "hu",
    "india": "in",
    "indonesia": "id",
    "ireland": "ie",
    "israel": "il",
    "italy": "it",
    "ivorycoast": "ci",
    "ivory": "ci",
    "jamaica": "jm",
    "jordan": "jo",
    "kazakhstan": "kz",
    "kenya": "ke",
    "kuwait": "kw",
    "kyrgyzstan": "kg",
    "laos": "la",
    "latvia": "lv",
    "lesotho": "ls",
    "liberia": "lr",
    "lithuania": "lt",
    "luxembourg": "lu",
    "macau": "mo",
    "macao": "mo",
    "madagascar": "mg",
    "malawi": "mw",
    "malaysia": "my",
    "maldives": "mv",
    "mauritania": "mr",
    "mauritius": "mu",
    "mexico": "mx",
    "moldova": "md",
    "mongolia": "mn",
    "montenegro": "me",
    "morocco": "ma",
    "mozambique": "mz",
    "namibia": "na",
    "nepal": "np",
    "netherlands": "nl",
    "newcaledonia": "nc",
    "nicaragua": "ni",
    "nigeria": "ng",
    "northmacedonia": "mk",
    "macedonia": "mk",
    "norway": "no",
    "oman": "om",
    "pakistan": "pk",
    "panama": "pa",
    "papuanewguinea": "pg",
    "papua": "pg",
    "paraguay": "py",
    "peru": "pe",
    "philippines": "ph",
    "poland": "pl",
    "portugal": "pt",
    "puertorico": "pr",
    "reunion": "re",
    "romania": "ro",
    "russia": "ru",
    "rwanda": "rw",
    "saintkittsandnevis": "kn",
    "saintlucia": "lc",
    "saintvincentandgrenadines": "vc",
    "salvador": "sv",
    "elsalvador": "sv",
    "samoa": "ws",
    "saudiarabia": "sa",
    "senegal": "sn",
    "serbia": "rs",
    "seychelles": "sc",
    "sierraleone": "sl",
    "slovakia": "sk",
    "slovenia": "si",
    "solomonislands": "sb",
    "southafrica": "za",
    "southkorea": "kr",
    "korea": "kr",
    "southsudan": "ss",
    "spain": "es",
    "srilanka": "lk",
    "suriname": "sr",
    "swaziland": "sz",
    "sweden": "se",
    "taiwan": "tw",
    "tajikistan": "tj",
    "tanzania": "tz",
    "thailand": "th",
    "tit": "tt",
    "trinidad": "tt",
    "togo": "tg",
    "tunisia": "tn",
    "turkey": "tr",
    "turkmenistan": "tm",
    "uganda": "ug",
    "ukraine": "ua",
    "uruguay": "uy",
    "usa": "us",
    "unitedstates": "us",
    "uzbekistan": "uz",
    "venezuela": "ve",
    "vietnam": "vn",
    "zambia": "zm",
    "china": "cn",
    "japan": "jp",
    "iraq": "iq",
    "iran": "ir",
    "uae": "ae",
    "unitedarabemirates": "ae",
    "myanmar": "mm",
    "northkorea": "kp",
}

SERVICE_TO_PRODUCT: Dict[str, str] = {
    "tg": "telegram",
    "telegram": "telegram",
    "wa": "whatsapp",
    "whatsapp": "whatsapp",
    "fb": "facebook",
    "facebook": "facebook",
    "ig": "instagram",
    "instagram": "instagram",
    "go": "google",
    "google": "google",
    "tw": "twitter",
    "twitter": "twitter",
    "ds": "discord",
    "discord": "discord",
}

NO_NUMBER_TOKENS = frozenset({
    "nofreephones",
    "no free phones",
    "no_free_phones",
    "no phones",
    "nophones",
    "no numbers",
    "nonumber",
    "no_numbers",
    "no product",
    "noproduct",
    "bad country",
    "badcountry",
    "select country",
})
NO_BALANCE_TOKENS = frozenset({
    "notenoughuserbalance",
    "not enough user balance",
    "not enough balance",
    "no balance",
    "nobalance",
    "no money",
    "nomoney",
})
BAD_KEY_TOKENS = frozenset({
    "unauthorized",
    "unauthorised",
    "bad token",
    "badtoken",
    "invalid token",
    "jwt",
})
TERMINAL_FAIL_STATUSES = frozenset({
    "CANCELED", "CANCELLED", "TIMEOUT", "BANNED",
})
SUCCESS_ORDER_STATUSES = frozenset({
    "PENDING", "RECEIVED", "FINISHED", "READY",
})


class InsufficientBalanceError(RuntimeError):
    """5SIM 返回 not enough user balance：账户余额不足以租号。"""

    def __init__(self, raw: Any = None):
        self.raw = raw
        super().__init__(f"5SIM 账户余额不足 (not enough user balance): {raw}")


class FiveSimError(RuntimeError):
    """5SIM 协议层通用异常。"""


def _normalize_iso2(value: str) -> str:
    token = (value or "").strip().lower()
    return "gb" if token == "uk" else token


def _compact_token(text: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "")).lower()


def _slugify(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _build_iso2_to_fivesim() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    try:
        from backend.app.services.geo_catalog import iter_catalog

        for item in iter_catalog():
            iso = str(item.get("code") or "").strip().lower()
            name = str(item.get("name") or "")
            if not iso:
                continue
            mapping[iso] = _slugify(name)
    except Exception:
        pass
    for slug, iso in FIVESIM_SLUG_TO_ISO2.items():
        mapping.setdefault(iso, slug)
    mapping.update(ISO2_TO_FIVESIM_SPECIAL)
    mapping["uk"] = "england"
    return mapping


ISO2_TO_FIVESIM: Dict[str, str] = _build_iso2_to_fivesim()


def _build_fivesim_to_iso() -> Dict[str, str]:
    reverse = dict(FIVESIM_SLUG_TO_ISO2)
    for iso, slug in ISO2_TO_FIVESIM.items():
        if iso == "uk":
            continue
        reverse.setdefault(slug, iso)
    reverse["england"] = "gb"
    reverse["usa"] = "us"
    return reverse


FIVESIM_TO_ISO2: Dict[str, str] = _build_fivesim_to_iso()


def resolve_country_iso2(country: Union[str, int, None]) -> str:
    """任意国家输入 → ISO-2。无法识别时原样小写返回。"""
    if country is None:
        return ""
    token = str(country).strip()
    if not token:
        return ""
    lower = token.lower()
    compact = _slugify(lower)
    if compact and compact in FIVESIM_TO_ISO2:
        return FIVESIM_TO_ISO2[compact]
    if lower in FIVESIM_TO_ISO2:
        return FIVESIM_TO_ISO2[lower]
    if len(lower) == 2 and lower.isascii() and lower.isalpha():
        return "gb" if lower == "uk" else lower
    try:
        from backend.app.services.geo_catalog import resolve_iso2

        inferred = resolve_iso2(token)
        if inferred:
            return inferred
    except Exception:
        pass
    return lower


def resolve_fivesim_country(country: Union[str, int, None]) -> str:
    """ISO-2 / ISO-3 / 国家名 / 5sim slug → 官方英文小写 country 参数。"""
    if country is None or str(country).strip() == "":
        raise FiveSimError("未指定租号国家")
    token = str(country).strip().lower()
    compact = _slugify(token)
    if compact and compact in FIVESIM_TO_ISO2:
        # 已是官方 slug（或别名），归一到权威写法
        iso = FIVESIM_TO_ISO2[compact]
        return ISO2_TO_FIVESIM.get(iso, compact)
    if token in FIVESIM_TO_ISO2:
        iso = FIVESIM_TO_ISO2[token]
        return ISO2_TO_FIVESIM.get(iso, token)
    iso = resolve_country_iso2(country)
    if iso in ISO2_TO_FIVESIM:
        return ISO2_TO_FIVESIM[iso]
    if compact and not compact.isdigit():
        return compact
    raise FiveSimError(f"无法将国家 '{country}' 映射到 5SIM country slug")


def fivesim_country_to_iso(country: Union[str, int, None]) -> Optional[str]:
    if country is None:
        return None
    token = str(country).strip().lower()
    if not token:
        return None
    compact = _slugify(token)
    if compact and compact in FIVESIM_TO_ISO2:
        return FIVESIM_TO_ISO2[compact]
    if token in FIVESIM_TO_ISO2:
        return FIVESIM_TO_ISO2[token]
    if len(token) == 2 and token.isascii() and token.isalpha():
        return "gb" if token == "uk" else token
    try:
        from backend.app.services.geo_catalog import resolve_iso2

        return resolve_iso2(token)
    except Exception:
        return None


def resolve_product(service: Optional[str]) -> str:
    token = str(service or DEFAULT_SERVICE).strip().lower()
    return SERVICE_TO_PRODUCT.get(token, token or DEFAULT_PRODUCT)


def _is_no_numbers(text: Any) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if is_no_number_error(raw):
        return True
    compact = _compact_token(raw)
    tokens = {_compact_token(t) for t in NO_NUMBER_TOKENS}
    if compact in tokens:
        return True
    return (
        "nofreephone" in compact
        or "nonumber" in compact
        or "nophones" in compact
        or compact.startswith("badcountry")
        or compact.startswith("noproduct")
    )


def _is_no_balance(text: Any) -> bool:
    compact = _compact_token(text)
    tokens = {_compact_token(t) for t in NO_BALANCE_TOKENS}
    return compact in tokens or "notenough" in compact and "balance" in compact or compact.startswith("nobalance")


def _is_bad_key(text: Any) -> bool:
    compact = _compact_token(text)
    return any(tok in compact for tok in {_compact_token(t) for t in BAD_KEY_TOKENS})


def _parse_maybe_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return stripped
    if stripped[:1] in "{[\"" or stripped in {"null", "true", "false"}:
        try:
            return json.loads(stripped)
        except (TypeError, ValueError):
            return stripped
    return stripped


def _error_text(payload: Any, raw: str = "") -> str:
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("message", "error", "msg", "detail", "reason"):
            value = payload.get(key)
            if value:
                return str(value)
    return (raw or str(payload or "")).strip()


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(phone or "").strip())
    if not digits:
        return str(phone or "").strip()
    if digits.startswith("+"):
        return digits
    return "+" + digits.lstrip("00")


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


def extract_sms_code(payload: Any) -> Optional[str]:
    """从 5SIM check 响应提取最新一条验证码。"""
    if isinstance(payload, str):
        parsed = _parse_maybe_json(payload)
        if parsed is not payload:
            payload = parsed
        elif payload.strip():
            match = re.search(r"\b(\d{4,8})\b", payload)
            return match.group(1) if match else None

    if not isinstance(payload, dict):
        return None

    sms_items = payload.get("sms")
    candidates: List[Dict[str, Any]] = []
    if isinstance(sms_items, list):
        candidates = [item for item in sms_items if isinstance(item, dict)]
    elif isinstance(sms_items, dict):
        candidates = [sms_items]

    for item in reversed(candidates):
        code = item.get("code") or item.get("smsCode") or item.get("sms")
        if code not in (None, ""):
            return str(code).strip()
        text = str(item.get("text") or "")
        match = re.search(r"\b(\d{4,8})\b", text)
        if match:
            return match.group(1)

    direct = payload.get("code") or payload.get("smsCode")
    if direct not in (None, ""):
        return str(direct).strip()
    return None


def _iter_operator_nodes(product_node: Any) -> Iterable[Tuple[str, Dict[str, Any]]]:
    if not isinstance(product_node, dict):
        return
    if "cost" in product_node or "count" in product_node:
        yield DEFAULT_OPERATOR, product_node
        return
    for operator, node in product_node.items():
        if isinstance(node, dict):
            yield str(operator), node


def parse_fivesim_price_payload(
    data: Any,
    product: str = DEFAULT_PRODUCT,
    country: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """解析 5SIM guest/prices 响应，按国家聚合库存与最低价。

    兼容两种官方形态：
      {country: {product: {operator: {cost, count}}}}
      {product: {country: {operator: {cost, count}}}}
    """
    if not isinstance(data, dict) or not data:
        return []

    rows: List[Dict[str, Any]] = []
    country_filter = _slugify(country) if country else ""

    def _consume(slug: str, operators: Any) -> None:
        if country_filter and _slugify(slug) != country_filter:
            return
        best_cost: Optional[float] = None
        total = 0
        best_operator = DEFAULT_OPERATOR
        for op_name, node in _iter_operator_nodes(operators):
            count = _as_int(node.get("count") or node.get("Qty") or node.get("qty") or 0)
            cost = _as_float(node.get("cost") if node.get("cost") is not None else node.get("Price", node.get("price")))
            if count <= 0:
                continue
            total += count
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_operator = op_name
        if total <= 0:
            return
        rows.append({
            "provider_country_id": str(slug),
            "stock": total,
            "cost": best_cost or 0.0,
            "operator": best_operator,
        })

    product_map = data.get(product)
    looks_product_outer = (
        isinstance(product_map, dict)
        and product_map
        and not any(k in product_map for k in ("cost", "count", "Price", "Qty"))
        and any(isinstance(v, dict) for v in product_map.values())
    )
    if looks_product_outer:
        sample = next((v for v in product_map.values() if isinstance(v, dict)), {})
        sample_has_ops = any(
            isinstance(v, dict) and ("cost" in v or "count" in v)
            for v in sample.values()
        ) or ("cost" in sample or "count" in sample)
        if sample_has_ops:
            for slug, operators in product_map.items():
                _consume(str(slug), operators)
            if rows:
                return rows

    for slug, bucket in data.items():
        if str(slug).lower() in {product, "status", "error", "msg", "message"}:
            continue
        if not isinstance(bucket, dict):
            continue
        operators = bucket.get(product) if isinstance(bucket.get(product), dict) else bucket
        _consume(str(slug), operators)
    return rows


def pick_operator_from_prices(
    data: Any,
    country_slug: str,
    product: str,
    max_price: Optional[float] = None,
) -> Optional[str]:
    """在价格表中挑选库存>0 且不超过 max_price 的最便宜运营商。"""
    rows = parse_fivesim_price_payload(data, product=product, country=country_slug)
    if not rows:
        # 再按原始结构扫描该国全部运营商
        operators: Dict[str, Dict[str, Any]] = {}
        if isinstance(data, dict):
            country_node = data.get(country_slug) or data.get(product, {}).get(country_slug) if isinstance(data.get(product), dict) else None
            if isinstance(country_node, dict):
                product_node = country_node.get(product) if isinstance(country_node.get(product), dict) else country_node
                operators = dict(_iter_operator_nodes(product_node))
        candidates = []
        for op_name, node in operators.items():
            count = _as_int(node.get("count") or 0)
            cost = _as_float(node.get("cost") if node.get("cost") is not None else 0)
            if count <= 0:
                continue
            if max_price is not None and cost > max_price:
                continue
            candidates.append((cost, -count, op_name))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][2]

    row = rows[0]
    if max_price is not None and _as_float(row.get("cost")) > max_price:
        return None
    return str(row.get("operator") or DEFAULT_OPERATOR)


def _stock_from_payload(data: Any, country_slug: str, product: str) -> int:
    rows = parse_fivesim_price_payload(data, product=product, country=country_slug)
    return int(rows[0]["stock"]) if rows else 0


class FiveSimService:
    """5SIM 异步接码客户端，接口对齐 VakSmsService / GrizzlySmsService 契约。"""

    BASE_URL = BASE_URL
    PROVIDER_NAME = PROVIDER_NAME
    PROVIDER_LABEL = PROVIDER_LABEL

    def __init__(self, api_key: str, timeout: float = 30.0, client: Optional[httpx.AsyncClient] = None):
        self.api_key = (api_key or "").strip()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "FiveSimService":
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
            logger.warning("释放 5SIM httpx 客户端失败: %s", exc)

    def _headers(self, authed: bool = True) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if authed:
            if not self.api_key:
                raise FiveSimError("未配置 5SIM API Token")
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _raise_protocol_error(self, raw: str, payload: Any, status_code: int, country: Optional[str] = None) -> None:
        text = _error_text(payload, raw)
        if _is_bad_key(text) or status_code in {401, 403}:
            raise FiveSimError(f"5SIM API Token 无效: {text or status_code}")
        if _is_no_balance(text):
            raise InsufficientBalanceError(text or payload or raw)
        if _is_no_numbers(text) or status_code in {400, 404} and _is_no_numbers(text or raw):
            iso = resolve_country_iso2(country) if country else ""
            raise NoNumberAvailableError(iso or (country or "?"), text or payload or raw)
        if status_code >= 400:
            raise FiveSimError(f"5SIM HTTP {status_code}: {(text or raw)[:300]}")

    async def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        authed: bool = True,
        country: Optional[str] = None,
        allow_error_status: bool = False,
    ) -> Any:
        url = path if str(path).startswith("http") else f"{self.BASE_URL}{path}"
        resp = await self.client.get(
            url,
            headers=self._headers(authed=authed),
            params={k: v for k, v in (params or {}).items() if v is not None} or None,
        )
        raw = (resp.text or "").strip()
        payload = _parse_maybe_json(raw)
        if resp.status_code >= 400 or (isinstance(payload, str) and payload and not allow_error_status):
            # 5sim 错误常常是 400 + 纯文本 / JSON 字符串
            if resp.status_code >= 400 or _is_no_numbers(payload) or _is_no_balance(payload) or _is_bad_key(payload):
                self._raise_protocol_error(raw, payload, resp.status_code, country=country)
        return payload

    async def get_profile(self) -> Dict[str, Any]:
        data = await self._get("/user/profile")
        if isinstance(data, dict) and "balance" in data:
            return data
        raise FiveSimError(f"解析 5SIM 用户资料失败: {data}")

    async def get_balance(self) -> float:
        profile = await self.get_profile()
        return float(profile["balance"])

    query_telemetry_quota = get_balance

    async def get_prices(
        self,
        country: Union[str, int, None] = None,
        product: str = DEFAULT_PRODUCT,
        service: Optional[str] = None,
    ) -> Any:
        resolved_product = resolve_product(service or product)
        params: Dict[str, Any] = {"product": resolved_product}
        country_slug = None
        if country is not None and str(country).strip() != "":
            country_slug = resolve_fivesim_country(country)
            params["country"] = country_slug
        try:
            data = await self._get("/guest/prices", params=params, authed=bool(self.api_key), country=country_slug)
        except FiveSimError:
            # 无 Token 时 guest 接口仍可用
            data = await self._get("/guest/prices", params=params, authed=False, country=country_slug)
        if data in (None, "null"):
            return {}
        if isinstance(data, (dict, list)):
            return data
        raise FiveSimError(f"获取 5SIM 价格/库存失败: {data}")

    async def get_all_prices(self, product: str = DEFAULT_PRODUCT, service: Optional[str] = None) -> Any:
        return await self.get_prices(country=None, product=product, service=service)

    async def get_stock_count(
        self,
        country: Union[str, int] = "id",
        service: str = DEFAULT_SERVICE,
    ) -> int:
        product = resolve_product(service)
        try:
            country_slug = resolve_fivesim_country(country)
            data = await self.get_prices(country=country_slug, product=product)
        except Exception as exc:
            logger.warning("查询 5SIM 库存失败: %s", exc)
            return 0
        return _stock_from_payload(data, resolve_fivesim_country(country), product)

    query_channel_capacity = get_stock_count

    async def _choose_operator(
        self,
        country_slug: str,
        product: str,
        operator: Optional[str],
        max_price: Optional[float],
    ) -> str:
        requested = (operator or "").strip() or DEFAULT_OPERATOR
        if max_price is None and requested:
            return requested
        try:
            prices = await self.get_prices(country=country_slug, product=product)
        except Exception as exc:
            logger.warning("5SIM 价格筛选失败，回落 operator=%s: %s", requested, exc)
            return requested
        picked = None
        operators: List[Tuple[float, int, str]] = []
        rows_source: Any = prices
        country_node = None
        if isinstance(prices, dict):
            if isinstance(prices.get(country_slug), dict):
                country_node = prices[country_slug]
                if isinstance(country_node.get(product), dict):
                    country_node = country_node[product]
            elif isinstance(prices.get(product), dict) and isinstance(prices[product].get(country_slug), dict):
                country_node = prices[product][country_slug]
        if isinstance(country_node, dict):
            for op_name, node in _iter_operator_nodes(country_node):
                count = _as_int(node.get("count") or 0)
                cost = _as_float(node.get("cost") if node.get("cost") is not None else 0)
                if count <= 0:
                    continue
                if max_price is not None and cost > max_price:
                    continue
                operators.append((cost, -count, op_name))
        if operators:
            operators.sort()
            if requested and requested != DEFAULT_OPERATOR:
                for cost, _neg, op_name in operators:
                    if op_name == requested:
                        picked = op_name
                        break
            if picked is None:
                picked = operators[0][2]
        if picked:
            return picked
        if max_price is not None and not operators:
            raise NoNumberAvailableError(
                resolve_country_iso2(country_slug) or country_slug,
                f"no free phones under max_price={max_price}",
            )
        return requested

    async def get_number(
        self,
        country: Union[str, int] = "id",
        service: str = DEFAULT_SERVICE,
        operator: Optional[str] = DEFAULT_OPERATOR,
        max_price: Optional[float] = None,
        provider_ids: Optional[Union[str, List[str]]] = None,
    ) -> Tuple[str, str]:
        # 5SIM 无 providerIds 概念；参数仅与 Grizzly/SMS Bower 调用签名对齐，避免编排层 TypeError
        if provider_ids:
            logger.info("5SIM 忽略 providerIds=%s（该平台不支持按供应商精确取号）", provider_ids)
        country_slug = resolve_fivesim_country(country)
        product = resolve_product(service)
        bid = normalize_sms_max_price(max_price)
        bid_str = format_sms_max_price(bid)
        iso = (resolve_country_iso2(country) or str(country) or "").upper()
        chosen_operator = await self._choose_operator(country_slug, product, operator, bid)
        params: Dict[str, Any] = {}
        if bid_str is not None:
            params["maxPrice"] = bid_str
            params["max_price"] = bid_str
        logger.info(
            "向 5SIM 申请租号 (country=%s/%s, operator=%s, product=%s, maxPrice=%s)...",
            country_slug,
            iso or "?",
            chosen_operator,
            product,
            bid_str if bid_str is not None else "未设置",
        )
        path = f"/user/buy/activation/{country_slug}/{chosen_operator}/{product}"
        try:
            data = await self._get(path, params=params or None, country=country_slug)
        except NoNumberAvailableError:
            raise
        except InsufficientBalanceError:
            raise
        except FiveSimError:
            raise
        except Exception as exc:
            raise FiveSimError(f"5SIM 租号请求失败: {exc}") from exc

        if isinstance(data, str):
            self._raise_protocol_error(data, data, 400, country=country_slug)
        if not isinstance(data, dict):
            raise FiveSimError(f"租号返回非预期格式: {data}")

        error = data.get("error") or data.get("message") or data.get("msg")
        if error:
            self._raise_protocol_error(str(error), data, 400, country=country_slug)

        act_id = data.get("id") or data.get("activationId") or data.get("activation_id")
        phone = data.get("phone") or data.get("number") or data.get("tel")
        if act_id in (None, "") or not phone:
            raise FiveSimError(f"租号返回非预期格式: {data}")
        return str(act_id), _normalize_phone(str(phone))

    lease_channel_handle = get_number

    async def check(self, act_id: str) -> Dict[str, Any]:
        if not act_id:
            raise FiveSimError("缺少 order_id，无法查询 5SIM 订单")
        data = await self._get(f"/user/check/{act_id}")
        if isinstance(data, dict):
            return data
        raise FiveSimError(f"查询 5SIM 订单失败: {data}")

    async def wait_for_code(
        self,
        act_id: str,
        max_attempts: int = 30,
        interval: float = 4.0,
        log_callback: Optional[Callable] = None,
    ) -> str:
        if not act_id:
            raise FiveSimError("缺少 order_id，无法轮询验证码")
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval)
            if log_callback:
                await log_callback(f"正在异步轮询 5SIM 挑战凭证 (第 {attempt}/{max_attempts} 次)...")
            try:
                data = await self.check(act_id)
            except Exception as exc:
                logger.warning("轮询 5SIM 验证码异常: %s", exc)
                continue
            status = str(data.get("status") or "").upper()
            code = extract_sms_code(data)
            if code and (status in {"RECEIVED", "FINISHED", "PENDING", ""} or data.get("sms")):
                return code
            if status == "RECEIVED" and code:
                return code
            if status in TERMINAL_FAIL_STATUSES:
                raise FiveSimError(f"5SIM 订单已终止 ({status}): {data}")
        raise TimeoutError("等待 5SIM 带外挑战证明超时 (已达最大重试轮次)")

    poll_ephemeral_challenge_proof = wait_for_code

    async def _order_action(self, action: str, act_id: str) -> Dict[str, Any]:
        if not act_id:
            return {"success": False, "skipped": True, "reason": "missing_act_id", "status": action}
        try:
            data = await self._get(f"/user/{action}/{act_id}")
            status = ""
            success = False
            if isinstance(data, dict):
                status = str(data.get("status") or action).upper()
                success = status in SUCCESS_ORDER_STATUSES or status in {
                    "CANCELED", "CANCELLED", "BANNED", "FINISHED", "OK",
                } or bool(data.get("id"))
            elif isinstance(data, str):
                status = data.strip().upper()
                success = "OK" in status or "CANCEL" in status or "FINISH" in status or "BAN" in status
                data = {"raw": data}
            result = {
                "success": bool(success),
                "skipped": False,
                "act_id": act_id,
                "status": status or action,
                "action": action,
                "data": data,
                "error": None if success else _error_text(data),
            }
            if result["success"]:
                logger.info("[5SIM %s] act_id=%s status=%s", action, act_id, result["status"])
            else:
                logger.warning("5SIM %s 未成功: act_id=%s resp=%s", action, act_id, data)
            return result
        except Exception as exc:
            logger.warning("5SIM %s 失败 act_id=%s: %s", action, act_id, exc)
            return {
                "success": False,
                "skipped": False,
                "act_id": act_id,
                "status": action,
                "action": action,
                "error": str(exc),
            }

    async def finish(self, act_id: str) -> Dict[str, Any]:
        return await self._order_action("finish", act_id)

    finalize_channel_binding = finish

    async def ban(self, act_id: str) -> Dict[str, Any]:
        return await self._order_action("ban", act_id)

    async def cancel(self, act_id: str) -> Dict[str, Any]:
        """优先 /user/cancel；失败再尝试 /user/ban，保证失败路径可退款。"""
        result = await self._order_action("cancel", act_id)
        if result.get("skipped") or result.get("success"):
            return result
        ban_result = await self._order_action("ban", act_id)
        if ban_result.get("success"):
            ban_result["fallback"] = "ban"
            return ban_result
        result["ban"] = ban_result
        return result

    revoke_channel_binding = cancel


# 学术规范别名
OOBTelemetryProvider = FiveSimService
