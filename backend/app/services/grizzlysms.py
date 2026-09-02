"""Grizzly SMS (grizzlysms.com) 接码客户端。

协议为标准 SMS-Activate / Venta 子集：
  GET https://api.grizzlysms.com/stubs/handler_api.php
    ?api_key={key}&action={getBalance|getPrices|getNumber|getStatus|setStatus}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, Optional, Tuple, Union

import httpx

from backend.app.models.schemas import format_sms_max_price, normalize_sms_max_price
from backend.app.services.vaksms import NoNumberAvailableError, is_no_number_error

logger = logging.getLogger("GrizzlySmsService")

BASE_URL = "https://api.grizzlysms.com/stubs/handler_api.php"
PROVIDER_NAME = "grizzlysms"
PROVIDER_LABEL = "Grizzly SMS (grizzlysms.com)"
DEFAULT_SERVICE = "tg"

# 用户指定的权威 ISO-2 ↔ Grizzly country_id 映射。
# US 主 ID 为 187，12 作为 SMS-Activate 虚拟号别名保留在反向表中。
ISO2_TO_GRIZZLY: Dict[str, int] = {
    "ru": 0,
    "kz": 2,
    "ph": 4,
    "id": 6,
    "vn": 10,
    "gb": 16,
    "uk": 16,
    "in": 22,
    "co": 33,
    "ca": 36,
    "th": 52,
    "br": 73,
    "cl": 151,
    "us": 187,
}

# 扩展 SMS-Activate 常用国家，便于智能推断（不覆盖权威表）。
# 动态 getPrices 全量返回的未知 ID 仍可通过 geo_catalog.SMSACTIVATE_ID_TO_ISO2 回落。
_EXTENDED_ISO2_TO_GRIZZLY: Dict[str, int] = {
    "ua": 1,
    "cn": 3,
    "mm": 5,
    "my": 7,
    "ke": 8,
    "tz": 9,
    "kg": 11,
    "il": 13,
    "hk": 14,
    "pl": 15,
    "ie": 23,
    "kh": 24,
    "la": 25,
    "ht": 26,
    "ci": 27,
    "rs": 29,
    "za": 31,
    "ro": 32,
    "ee": 34,
    "az": 35,
    "ma": 37,
    "gh": 38,
    "ar": 39,
    "uz": 40,
    "cm": 41,
    "de": 43,
    "lt": 44,
    "hr": 45,
    "se": 46,
    "iq": 47,
    "nl": 48,
    "lv": 49,
    "at": 50,
    "by": 51,
    "sa": 53,
    "mx": 54,
    "tw": 55,
    "es": 56,
    "ir": 57,
    "dz": 58,
    "si": 59,
    "bd": 60,
    "sn": 61,
    "tr": 62,
    "cz": 63,
    "lk": 64,
    "pe": 65,
    "pk": 66,
    "nz": 67,
    "ve": 70,
    "et": 71,
    "mn": 72,
    "af": 74,
    "ug": 75,
    "ao": 76,
    "cy": 77,
    "fr": 78,
    "np": 81,
    "be": 82,
    "bg": 83,
    "hu": 84,
    "md": 85,
    "it": 86,
    "py": 87,
    "hn": 88,
    "tn": 89,
    "ni": 90,
    "bo": 92,
    "cr": 93,
    "gt": 94,
    "ae": 95,
    "ec": 105,
    "do": 109,
    "jo": 116,
    "pt": 117,
    "ge": 129,
    "gr": 130,
    "is": 133,
    "sk": 143,
    "tj": 145,
    "bh": 147,
    "am": 150,
    "fi": 163,
    "au": 175,
    "jp": 182,
    "kr": 190,
    "ng": 19,
    "eg": 21,
    "mg": 17,
    "cd": 18,
    "mo": 20,
    "gm": 28,
    "ye": 30,
    "td": 42,
    "gn": 68,
    "ml": 69,
    "pg": 79,
    "mz": 80,
    "tl": 91,
    "zw": 96,
    "pr": 97,
    "sd": 98,
    "tg": 99,
    "kw": 100,
    "sv": 101,
    "ly": 102,
    "jm": 103,
    "tt": 104,
    "sz": 106,
    "om": 107,
    "ba": 108,
    "sy": 110,
    "qa": 111,
    "pa": 112,
    "cu": 113,
    "mr": 114,
    "sl": 115,
    "bb": 118,
    "bi": 119,
    "bj": 120,
    "bn": 121,
    "bs": 122,
    "bw": 123,
    "bz": 124,
    "cf": 125,
    "dm": 126,
    "gd": 127,
    "gy": 131,
    "kn": 134,
    "lr": 135,
    "ls": 136,
    "mw": 137,
    "na": 138,
    "ne": 139,
    "rw": 140,
    "sr": 142,
    "mc": 144,
    "re": 146,
    "zm": 148,
    "so": 149,
    "bf": 152,
    "lb": 153,
    "ga": 154,
    "al": 155,
    "uy": 156,
    "mu": 157,
    "bt": 158,
    "mv": 159,
    "gp": 160,
    "tm": 161,
    "gf": 162,
    "lc": 164,
    "lu": 165,
    "vc": 166,
    "gq": 167,
    "dj": 168,
    "ag": 169,
    "ky": 170,
    "me": 171,
    "dk": 172,
    "ch": 173,
    "no": 174,
    "er": 176,
    "ss": 177,
    "st": 178,
    "aw": 179,
    "mk": 183,
    "sc": 184,
    "nc": 185,
    "cv": 186,
    "ps": 188,
    "fj": 189,
    "sg": 196,
}

ISO3_TO_ISO2: Dict[str, str] = {
    "RUS": "ru", "KAZ": "kz", "PHL": "ph", "IDN": "id", "VNM": "vn",
    "GBR": "gb", "IND": "in", "COL": "co", "CAN": "ca", "THA": "th",
    "BRA": "br", "CHL": "cl", "USA": "us", "UKR": "ua", "CHN": "cn",
    "MYS": "my", "KEN": "ke", "TZA": "tz", "KGZ": "kg", "ISR": "il",
    "HKG": "hk", "POL": "pl", "IRL": "ie", "KHM": "kh", "LAO": "la",
    "ZAF": "za", "ROU": "ro", "EST": "ee", "AZE": "az", "MAR": "ma",
    "GHA": "gh", "ARG": "ar", "UZB": "uz", "CMR": "cm", "DEU": "de",
    "LTU": "lt", "HRV": "hr", "SWE": "se", "IRQ": "iq", "NLD": "nl",
    "LVA": "lv", "AUT": "at", "BLR": "by", "SAU": "sa", "MEX": "mx",
    "TWN": "tw", "ESP": "es", "IRN": "ir", "DZA": "dz", "SVN": "si",
    "BGD": "bd", "SEN": "sn", "TUR": "tr", "CZE": "cz", "LKA": "lk",
    "PER": "pe", "PAK": "pk", "NZL": "nz", "VEN": "ve", "ETH": "et",
    "MNG": "mn", "AFG": "af", "UGA": "ug", "AGO": "ao", "CYP": "cy",
    "FRA": "fr", "NPL": "np", "BEL": "be", "BGR": "bg", "HUN": "hu",
    "MDA": "md", "ITA": "it", "PRY": "py", "HND": "hn", "TUN": "tn",
    "NIC": "ni", "BOL": "bo", "CRI": "cr", "GTM": "gt", "ARE": "ae",
    "ECU": "ec", "DOM": "do", "JOR": "jo", "PRT": "pt", "GEO": "ge",
    "GRC": "gr", "ISL": "is", "SVK": "sk", "TJK": "tj", "BHR": "bh",
    "ARM": "am", "FIN": "fi", "AUS": "au", "JPN": "jp", "KOR": "kr",
    "NGA": "ng", "GB": "gb", "UK": "gb",
}

COUNTRY_NAME_TO_ISO2: Dict[str, str] = {
    "russia": "ru", "russian": "ru", "россия": "ru", "俄罗斯": "ru",
    "kazakhstan": "kz", "казахстан": "kz", "哈萨克斯坦": "kz",
    "philippines": "ph", "菲律宾": "ph",
    "indonesia": "id", "印尼": "id", "印度尼西亚": "id",
    "vietnam": "vn", "viet nam": "vn", "越南": "vn",
    "united kingdom": "gb", "great britain": "gb", "england": "gb", "uk": "gb", "英国": "gb",
    "india": "in", "印度": "in",
    "colombia": "co", "哥伦比亚": "co",
    "canada": "ca", "加拿大": "ca",
    "thailand": "th", "泰国": "th",
    "brazil": "br", "brasil": "br", "巴西": "br",
    "chile": "cl", "智利": "cl",
    "united states": "us", "usa": "us", "america": "us", "美国": "us",
    "ukraine": "ua", "乌克兰": "ua",
    "china": "cn", "中国": "cn",
    "malaysia": "my", "马来西亚": "my",
    "kenya": "ke", "肯尼亚": "ke",
    "germany": "de", "德国": "de",
    "france": "fr", "法国": "fr",
    "mexico": "mx", "墨西哥": "mx",
    "turkey": "tr", "土耳其": "tr",
    "japan": "jp", "日本": "jp",
    "australia": "au", "澳大利亚": "au",
    "argentina": "ar", "阿根廷": "ar",
    "peru": "pe", "秘鲁": "pe",
    "egypt": "eg", "埃及": "eg",
    "iraq": "iq", "伊拉克": "iq",
}

US_COUNTRY_ALIASES = frozenset({187, 12})

NO_NUMBER_TOKENS = frozenset({
    "no_numbers",
    "nonumber",
    "no number",
    "no numbers",
    "no_number",
})
NO_BALANCE_TOKENS = frozenset({
    "no_balance",
    "nobalance",
    "no balance",
    "no_money",
    "nomoney",
})
BAD_KEY_TOKENS = frozenset({
    "bad_key",
    "badkey",
    "wrong_key",
    "error_key",
})

_ACCESS_BALANCE_RE = re.compile(r"^ACCESS_BALANCE[:\s]+(-?\d+(?:\.\d+)?)\s*$", re.I)
_ACCESS_NUMBER_RE = re.compile(r"^ACCESS_NUMBER[:\s]+([^:]+):(.+)$", re.I)
_STATUS_OK_RE = re.compile(r"^STATUS_OK[:\s]+(.+)$", re.I)


class InsufficientBalanceError(RuntimeError):
    """Grizzly SMS 返回 NO_BALANCE：账户余额不足以租号。"""

    def __init__(self, raw: Any = None):
        self.raw = raw
        super().__init__(f"Grizzly SMS 账户余额不足 (NO_BALANCE): {raw}")


class GrizzlySmsError(RuntimeError):
    """Grizzly SMS 协议层通用异常。"""


def _normalize_iso2(value: str) -> str:
    return (value or "").strip().lower()


def _merged_iso2_map() -> Dict[str, int]:
    merged = dict(_EXTENDED_ISO2_TO_GRIZZLY)
    merged.update(ISO2_TO_GRIZZLY)
    return merged


def _build_id_to_iso() -> Dict[int, str]:
    reverse: Dict[int, str] = {}
    try:
        from backend.app.services.geo_catalog import SMSACTIVATE_ID_TO_ISO2
        for cid, iso in SMSACTIVATE_ID_TO_ISO2.items():
            reverse.setdefault(int(cid), iso)
    except Exception:
        pass
    for iso, cid in _EXTENDED_ISO2_TO_GRIZZLY.items():
        reverse.setdefault(int(cid), iso)
    for iso, cid in ISO2_TO_GRIZZLY.items():
        if iso == "uk":
            continue
        reverse[int(cid)] = iso
    reverse[12] = "us"
    reverse[187] = "us"
    return reverse


GRIZZLY_ID_TO_ISO2: Dict[int, str] = _build_id_to_iso()


def resolve_country_iso2(country: Union[str, int, None]) -> str:
    """把任意国家输入智能推断为 ISO-2。无法识别时原样小写返回。"""
    if country is None:
        return ""
    if isinstance(country, int):
        return GRIZZLY_ID_TO_ISO2.get(country, str(country))
    token = str(country).strip()
    if not token:
        return ""
    if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
        return GRIZZLY_ID_TO_ISO2.get(int(token), token)
    lower = token.lower()
    if lower in ISO2_TO_GRIZZLY or lower in _EXTENDED_ISO2_TO_GRIZZLY:
        return "gb" if lower == "uk" else lower
    iso3 = token.upper()
    if iso3 in ISO3_TO_ISO2:
        return ISO3_TO_ISO2[iso3]
    name_key = re.sub(r"[_\-]+", " ", lower).strip()
    if name_key in COUNTRY_NAME_TO_ISO2:
        return COUNTRY_NAME_TO_ISO2[name_key]
    compact = name_key.replace(" ", "")
    for alias, iso in COUNTRY_NAME_TO_ISO2.items():
        if compact == alias.replace(" ", ""):
            return iso
    try:
        from backend.app.services.geo_catalog import resolve_iso2
        inferred = resolve_iso2(token)
        if inferred:
            return inferred
    except Exception:
        pass
    return lower


def resolve_grizzly_country_id(country: Union[str, int, None]) -> int:
    """ISO-2 / ISO-3 / 国家名 / 数字 ID → Grizzly country_id。"""
    if country is None or str(country).strip() == "":
        raise GrizzlySmsError("未指定租号国家")
    if isinstance(country, int) or str(country).strip().lstrip("-").isdigit():
        cid = int(country)
        if cid in US_COUNTRY_ALIASES:
            return 187
        return cid
    iso = resolve_country_iso2(country)
    merged = _merged_iso2_map()
    if iso in merged:
        return int(merged[iso])
    try:
        from backend.app.services.geo_catalog import iso2_to_smsactivate_id
        cid = iso2_to_smsactivate_id(iso)
        if cid is not None:
            return int(cid)
    except Exception:
        pass
    raise GrizzlySmsError(f"无法将国家 '{country}' 映射到 Grizzly country_id")


def grizzly_country_id_to_iso(country_id: Union[int, str]) -> Optional[str]:
    try:
        cid = int(country_id)
    except (TypeError, ValueError):
        return None
    return GRIZZLY_ID_TO_ISO2.get(cid)


def _compact_token(text: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "")).lower()


def _is_no_numbers(text: Any) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if is_no_number_error(raw):
        return True
    compact = _compact_token(raw)
    if compact in {_compact_token(t) for t in NO_NUMBER_TOKENS}:
        return True
    return compact.startswith("nonumber") or "nonumber" in compact


def _is_no_balance(text: Any) -> bool:
    compact = _compact_token(text)
    return compact in {_compact_token(t) for t in NO_BALANCE_TOKENS} or compact.startswith("nobalance")


def _is_bad_key(text: Any) -> bool:
    compact = _compact_token(text)
    return compact in {_compact_token(t) for t in BAD_KEY_TOKENS} or "badkey" in compact


def _parse_maybe_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return stripped
    if stripped[:1] in "{[":
        try:
            return json.loads(stripped)
        except (TypeError, ValueError):
            return stripped
    return stripped


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(phone or "").strip())
    if not digits:
        return str(phone or "").strip()
    if digits.startswith("+"):
        return digits
    return "+" + digits.lstrip("00")


def _extract_sms_code(payload: str) -> Optional[str]:
    match = _STATUS_OK_RE.match((payload or "").strip())
    if not match:
        return None
    code = (match.group(1) or "").strip()
    # 部分平台返回 "12345 extra" 或 JSON
    if ":" in code and not code.replace(":", "").isdigit():
        code = code.split(":", 1)[0].strip()
    return code or None


class GrizzlySmsService:
    """Grizzly SMS 异步接码客户端，接口对齐 VakSmsService 契约。"""

    BASE_URL = BASE_URL
    PROVIDER_NAME = PROVIDER_NAME
    PROVIDER_LABEL = PROVIDER_LABEL

    def __init__(self, api_key: str, timeout: float = 30.0, client: Optional[httpx.AsyncClient] = None):
        self.api_key = (api_key or "").strip()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "GrizzlySmsService":
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

    async def _get(self, action: str, extra: Optional[Dict[str, Any]] = None) -> str:
        if not self.api_key:
            raise GrizzlySmsError(f"未配置 {self.PROVIDER_LABEL} API Key")
        params: Dict[str, Any] = {"api_key": self.api_key, "action": action}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        resp = await self.client.get(self.BASE_URL, params=params)
        text = (resp.text or "").strip()
        if resp.status_code >= 400:
            raise GrizzlySmsError(f"{self.PROVIDER_LABEL} HTTP {resp.status_code}: {text[:300]}")
        if _is_bad_key(text):
            raise GrizzlySmsError(f"{self.PROVIDER_LABEL} API Key 无效 (BAD_KEY): {text}")
        return text

    async def get_balance(self) -> float:
        raw = await self._get("getBalance")
        match = _ACCESS_BALANCE_RE.match(raw)
        if match:
            return float(match.group(1))
        parsed = _parse_maybe_json(raw)
        if isinstance(parsed, dict):
            for key in ("balance", "ACCESS_BALANCE", "money"):
                if key in parsed:
                    return float(parsed[key])
        raise GrizzlySmsError(f"解析余额失败: {raw}")

    query_telemetry_quota = get_balance

    async def get_prices(
        self,
        country: Union[str, int, None] = None,
        service: str = DEFAULT_SERVICE,
    ) -> Any:
        extra: Dict[str, Any] = {"service": service}
        if country is not None and str(country).strip() != "":
            extra["country"] = resolve_grizzly_country_id(country)
        raw = await self._get("getPrices", extra)
        parsed = _parse_maybe_json(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
        raise GrizzlySmsError(f"获取价格/库存失败: {raw}")

    async def get_all_prices(self, service: str = DEFAULT_SERVICE) -> Any:
        """不带 country 参数，官方返回全球所有有 Telegram 货的国家。"""
        return await self.get_prices(country=None, service=service)

    def _stock_from_prices(self, data: Any, country_id: int, service: str) -> int:
        if not isinstance(data, dict):
            return 0
        cid = str(country_id)
        # {country: {service: {count, cost}}}
        bucket = data.get(cid) or data.get(country_id)
        if isinstance(bucket, dict):
            svc = bucket.get(service) or bucket
            if isinstance(svc, dict) and ("count" in svc or "cost" in svc):
                try:
                    return int(svc.get("count") or 0)
                except (TypeError, ValueError):
                    return 0
        # {service: {country: {count, cost}}}
        svc_map = data.get(service)
        if isinstance(svc_map, dict):
            node = svc_map.get(cid) or svc_map.get(country_id) or svc_map
            if isinstance(node, dict):
                try:
                    return int(node.get("count") or 0)
                except (TypeError, ValueError):
                    return 0
        return 0

    async def get_stock_count(self, country: Union[str, int] = "in", service: str = DEFAULT_SERVICE) -> int:
        country_id = resolve_grizzly_country_id(country)
        try:
            data = await self.get_prices(country=country_id, service=service)
        except Exception as exc:
            logger.warning("查询 Grizzly 库存失败: %s", exc)
            return 0
        return self._stock_from_prices(data, country_id, service)

    query_channel_capacity = get_stock_count

    async def get_number(
        self,
        country: Union[str, int] = "in",
        service: str = DEFAULT_SERVICE,
        operator: Optional[str] = None,
        provider_ids: Optional[Union[str, List[str]]] = None,
        max_price: Optional[float] = None,
    ) -> Tuple[str, str]:
        country_id = resolve_grizzly_country_id(country)
        extra: Dict[str, Any] = {"service": service or DEFAULT_SERVICE, "country": country_id}
        if operator:
            extra["operator"] = operator
        if provider_ids:
            if isinstance(provider_ids, (list, tuple, set)):
                joined = ",".join(str(x).strip() for x in provider_ids if str(x).strip())
            else:
                joined = str(provider_ids).strip()
            if joined:
                extra["providerIds"] = joined
        bid = normalize_sms_max_price(max_price)
        bid_str = format_sms_max_price(bid)
        iso = (resolve_country_iso2(country) or str(country) or "").upper()
        if bid_str is not None:
            # 同时携带 camelCase / snake_case，兼容 SMS-Activate 与 Grizzly 协议子集。
            # 必须传账户结算币种的真实浮点字符串（美元账户 0.53/0.6，勿传 50/100）。
            extra["maxPrice"] = bid_str
            extra["max_price"] = bid_str
            if bid is not None and bid > 5:
                logger.warning(
                    "Grizzly SMS maxPrice=%s 数值较大。若账户为美元结算 (currency:840，伊拉克约 $0.53)，"
                    "请改填 0.55/0.6/1.0；传入 50/100 会被平台拒绝并返回 NO_NUMBERS。",
                    bid_str,
                )
        logger.info(
            "向 Grizzly SMS 申请租号 (country=%s/%s, maxPrice=%s, providerIds=%s)...",
            country_id,
            iso or "?",
            bid_str if bid_str is not None else "未设置",
            extra.get("providerIds") or "未指定",
        )
        raw = await self._get("getNumber", extra)
        if _is_no_numbers(raw):
            iso = resolve_country_iso2(country) or str(country)
            raise NoNumberAvailableError(iso, raw)
        if _is_no_balance(raw):
            raise InsufficientBalanceError(raw)
        match = _ACCESS_NUMBER_RE.match(raw)
        if match:
            act_id = (match.group(1) or "").strip()
            phone = _normalize_phone(match.group(2) or "")
            if act_id and phone:
                return act_id, phone
        parsed = _parse_maybe_json(raw)
        if isinstance(parsed, dict):
            error = parsed.get("error") or parsed.get("status") or parsed.get("msg")
            if _is_no_numbers(error) or _is_no_numbers(parsed):
                raise NoNumberAvailableError(resolve_country_iso2(country) or str(country), parsed)
            if _is_no_balance(error):
                raise InsufficientBalanceError(parsed)
            act_id = parsed.get("id") or parsed.get("activationId") or parsed.get("activation_id")
            phone = parsed.get("phone") or parsed.get("number") or parsed.get("tel")
            if act_id and phone:
                return str(act_id), _normalize_phone(str(phone))
        raise GrizzlySmsError(f"租号返回非预期格式: {raw}")

    lease_channel_handle = get_number

    async def set_status(self, act_id: str, status: int) -> str:
        if not act_id:
            return ""
        return await self._get("setStatus", {"id": act_id, "status": int(status)})

    async def notify_ready(self, act_id: str) -> str:
        """status=1：通知平台号码已就绪、开始等待短信。"""
        try:
            return await self.set_status(act_id, 1)
        except Exception as exc:
            logger.warning("Grizzly SMS 就绪通知失败 act_id=%s: %s", act_id, exc)
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
            raise GrizzlySmsError("缺少 activation_id，无法轮询验证码")
        if notify_ready:
            await self.notify_ready(act_id)
        last_raw = ""
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval)
            try:
                raw = await self._get("getStatus", {"id": act_id})
            except Exception as exc:
                logger.warning("轮询 Grizzly SMS 验证码异常: %s", exc)
                last_raw = f"EXC:{exc}"
                if log_callback:
                    await log_callback(
                        f"[接码轮询] {self.PROVIDER_LABEL} getStatus 异常={exc} "
                        f"elapsed={attempt * interval:.0f}s attempt={attempt}/{max_attempts}"
                    )
                continue
            last_raw = raw
            upper = (raw or "").strip().upper()
            is_wait = upper.startswith("STATUS_WAIT")
            if log_callback and (
                attempt == 1 or attempt == max_attempts or attempt % 5 == 0 or not is_wait
            ):
                await log_callback(
                    f"[接码轮询] {self.PROVIDER_LABEL} getStatus raw={raw!r} "
                    f"elapsed={attempt * interval:.0f}s attempt={attempt}/{max_attempts}"
                )
            if upper.startswith("STATUS_OK"):
                code = _extract_sms_code(raw)
                if code:
                    return code
            if upper.startswith("STATUS_CANCEL"):
                raise GrizzlySmsError(f"Grizzly SMS 订单已取消 (STATUS_CANCEL): {raw}")
            if upper.startswith("STATUS_WAIT"):
                continue
            parsed = _parse_maybe_json(raw)
            if isinstance(parsed, dict):
                code = parsed.get("code") or parsed.get("smsCode") or parsed.get("sms")
                if code:
                    return str(code)
        raise TimeoutError(
            f"等待 {self.PROVIDER_LABEL} 带外挑战证明超时 "
            f"(已达最大重试轮次, last_getStatus={last_raw!r})"
        )

    poll_ephemeral_challenge_proof = wait_for_code

    async def finish(self, act_id: str) -> Dict[str, Any]:
        """status=6：完成订单。"""
        if not act_id:
            return {"success": False, "skipped": True, "reason": "missing_act_id", "status": 6}
        try:
            raw = await self.set_status(act_id, 6)
            upper = (raw or "").strip().upper()
            success = (
                upper.startswith("ACCESS_")
                or upper in {"OK", "ACCESS_ACTIVATION", "ACCESS_READY"}
                or upper.startswith("STATUS_OK")
            )
            result = {
                "success": success or bool(raw),
                "skipped": False,
                "act_id": act_id,
                "status": 6,
                "data": raw,
            }
            logger.info("[Grizzly SMS 完成订单] act_id=%s status=6 resp=%s", act_id, raw)
            return result
        except Exception as exc:
            logger.warning("Grizzly SMS 完成订单上报失败: %s", exc)
            return {
                "success": False,
                "skipped": False,
                "act_id": act_id,
                "status": 6,
                "error": str(exc),
            }

    finalize_channel_binding = finish

    async def cancel(self, act_id: str) -> Dict[str, Any]:
        """status=8：取消订单并触发自动退款。"""
        if not act_id:
            return {"success": False, "skipped": True, "reason": "missing_act_id", "status": 8}
        try:
            raw = await self.set_status(act_id, 8)
            upper = (raw or "").strip().upper()
            error_text = ""
            success = upper.startswith("ACCESS_CANCEL") or upper in {
                "ACCESS_CANCEL", "ACCESS_CANCEL_ALREADY", "OK",
            } or "CANCEL" in upper
            if upper.startswith("EARLY_CANCEL") or upper.startswith("ERROR"):
                success = False
                error_text = raw
            result = {
                "success": success or (bool(raw) and "ERROR" not in upper and "DENIED" not in upper),
                "skipped": False,
                "act_id": act_id,
                "status": 8,
                "data": raw,
                "error": error_text or None,
            }
            if result["success"]:
                logger.info("[自动退订/撤销信道句柄完成] act_id=%s status=8 resp=%s", act_id, raw)
            else:
                logger.warning("Grizzly SMS 撤销未成功: act_id=%s resp=%s", act_id, raw)
            return result
        except Exception as exc:
            logger.warning("Grizzly SMS 撤销信道句柄失败: %s", exc)
            return {
                "success": False,
                "skipped": False,
                "act_id": act_id,
                "status": 8,
                "error": str(exc),
            }

    revoke_channel_binding = cancel


# 学术规范别名
OOBTelemetryProvider = GrizzlySmsService
