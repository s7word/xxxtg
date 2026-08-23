"""硬件指纹 & 拓扑库目录管理。

将多国家 REGISTRATOR SQLite 包持久化到 ``data/device_dbs/``，
负责上传校验、自动解析统计、别名/启停、以及按目标国家挑选已激活样本池。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.app.config import DATA_DIR, DEVICE_DBS_DIR
from backend.app.services.device_profile import COUNTRY_LANG_MAP

logger = logging.getLogger("DeviceDbCatalog")

REGISTRATOR_COLUMNS = (
    "APP_ID",
    "APP_HASH",
    "SDK",
    "DEVICE",
    "APP_VERSION",
    "LANG_CODE",
    "SYSTEM_LANG_CODE",
    "LANG_PACK",
    "TZ_OFFSET",
    "PERF_CAT",
)
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CATALOG_VERSION = 1

COUNTRY_NAME_MAP = {
    "ae": "United Arab Emirates",
    "af": "Afghanistan",
    "ar": "Argentina",
    "au": "Australia",
    "bd": "Bangladesh",
    "br": "Brazil",
    "ca": "Canada",
    "cl": "Chile",
    "cn": "China",
    "co": "Colombia",
    "de": "Germany",
    "eg": "Egypt",
    "es": "Spain",
    "fr": "France",
    "gb": "United Kingdom",
    "id": "Indonesia",
    "in": "India",
    "ir": "Iran",
    "jp": "Japan",
    "ke": "Kenya",
    "kr": "South Korea",
    "kz": "Kazakhstan",
    "mx": "Mexico",
    "my": "Malaysia",
    "ng": "Nigeria",
    "pe": "Peru",
    "ph": "Philippines",
    "pk": "Pakistan",
    "ru": "Russia",
    "sa": "Saudi Arabia",
    "sg": "Singapore",
    "th": "Thailand",
    "tr": "Turkey",
    "ua": "Ukraine",
    "us": "United States",
    "uz": "Uzbekistan",
    "vn": "Vietnam",
    "za": "South Africa",
}

COUNTRY_NAME_ZH_MAP = {
    "ae": "阿联酋",
    "af": "阿富汗",
    "ar": "阿根廷",
    "au": "澳大利亚",
    "bd": "孟加拉",
    "br": "巴西",
    "ca": "加拿大",
    "cl": "智利",
    "cn": "中国",
    "co": "哥伦比亚",
    "de": "德国",
    "eg": "埃及",
    "es": "西班牙",
    "fr": "法国",
    "gb": "英国",
    "id": "印尼",
    "in": "印度",
    "ir": "伊朗",
    "jp": "日本",
    "ke": "肯尼亚",
    "kr": "韩国",
    "kz": "哈萨克斯坦",
    "mx": "墨西哥",
    "my": "马来西亚",
    "ng": "尼日利亚",
    "pe": "秘鲁",
    "ph": "菲律宾",
    "pk": "巴基斯坦",
    "ru": "俄罗斯",
    "sa": "沙特",
    "sg": "新加坡",
    "th": "泰国",
    "tr": "土耳其",
    "ua": "乌克兰",
    "us": "美国",
    "uz": "乌兹别克斯坦",
    "vn": "越南",
    "za": "南非",
}

COUNTRY_ALIAS_TOKENS = {
    "chile": "cl",
    "chilean": "cl",
    "base": "cl",
    "indonesia": "id",
    "indonesian": "id",
    "india": "in",
    "indian": "in",
    "russia": "ru",
    "russian": "ru",
    "kazakhstan": "kz",
    "kazakh": "kz",
    "afghanistan": "af",
    "usa": "us",
    "america": "us",
    "unitedstates": "us",
    "uk": "gb",
    "britain": "gb",
    "england": "gb",
    "unitedkingdom": "gb",
    "brazil": "br",
    "brasil": "br",
    "turkey": "tr",
    "turkiye": "tr",
    "mexico": "mx",
    "argentina": "ar",
    "peru": "pe",
    "colombia": "co",
    "philippines": "ph",
    "vietnam": "vn",
    "thailand": "th",
    "malaysia": "my",
    "singapore": "sg",
    "ukraine": "ua",
    "germany": "de",
    "spain": "es",
    "pakistan": "pk",
    "bangladesh": "bd",
    "nigeria": "ng",
    "egypt": "eg",
    "canada": "ca",
    "canadian": "ca",
    "france": "fr",
    "french": "fr",
    "australia": "au",
    "australian": "au",
    "japan": "jp",
    "japanese": "jp",
    "korea": "kr",
    "southkorea": "kr",
    "korean": "kr",
    "kenya": "ke",
    "uzbekistan": "uz",
    "uzbek": "uz",
    "uae": "ae",
    "emirates": "ae",
    "unitedarabemirates": "ae",
    "dubai": "ae",
    "saudi": "sa",
    "saudiarabia": "sa",
    "ksa": "sa",
    "southafrica": "za",
}

# 时区秒偏置 → 候选国家（用于内容推断，再与语言交叉确认）
TZ_COUNTRY_HINTS = {
    -28800: ("ca", "us"),
    -25200: ("ca", "us", "mx"),
    -21600: ("mx", "ca", "us"),
    -18000: ("us", "ca", "mx", "pe", "co"),
    -14400: ("cl", "ca", "us"),
    -10800: ("br", "ar", "cl"),
    0: ("gb",),
    3600: ("de", "fr", "ng"),
    7200: ("eg", "za", "ua"),
    10800: ("ru", "tr", "ke", "sa"),
    14400: ("ae",),
    16200: ("af",),
    18000: ("kz", "uz", "pk"),
    19800: ("in",),
    21600: ("bd", "kz"),
    25200: ("id", "th", "vn"),
    28800: ("id", "ph", "sg", "my", "cn"),
    32400: ("jp", "kr"),
    34200: ("au",),
    36000: ("au",),
}

LOCALE_COUNTRY_HINTS = {
    "es-cl": "cl",
    "es-mx": "mx",
    "es-ar": "ar",
    "es-pe": "pe",
    "es-co": "co",
    "id-id": "id",
    "in-id": "id",
    "en-in": "in",
    "hi-in": "in",
    "en-gb": "gb",
    "en-us": "us",
    "en-ca": "ca",
    "fr-ca": "ca",
    "pt-br": "br",
    "ru-ru": "ru",
    "ru-kz": "kz",
    "kk-kz": "kz",
    "en-af": "af",
    "tr-tr": "tr",
    "en-ph": "ph",
    "tl-ph": "ph",
    "vi-vn": "vn",
    "th-th": "th",
    "ms-my": "my",
    "en-sg": "sg",
    "de-de": "de",
    "fr-fr": "fr",
    "en-au": "au",
    "ja-jp": "jp",
    "ko-kr": "kr",
    "ar-eg": "eg",
    "en-za": "za",
    "en-ng": "ng",
    "en-ke": "ke",
    "uk-ua": "ua",
    "ru-ua": "ua",
    "uz-uz": "uz",
    "ru-uz": "uz",
    "ar-ae": "ae",
    "ar-sa": "sa",
}

BRAND_PREFIXES = (
    ("samsung", "samsung"),
    ("xiaomi", "xiaomi"),
    ("redmi", "xiaomi"),
    ("poco", "xiaomi"),
    ("huawei", "huawei"),
    ("honor", "huawei"),
    ("motorola", "motorola"),
    ("moto", "motorola"),
    ("realme", "realme"),
    ("vivo", "vivo"),
    ("oppo", "oppo"),
    ("oneplus", "oneplus"),
    ("google", "google"),
    ("pixel", "google"),
    ("infinix", "infinix"),
    ("tecno", "tecno"),
    ("nokia", "nokia"),
    ("sony", "sony"),
    ("lg", "lg"),
)

_LOCK = threading.RLock()
_ROW_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_country(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    token = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    if not token:
        return None
    if token in COUNTRY_NAME_MAP:
        return token
    if token in COUNTRY_ALIAS_TOKENS:
        return COUNTRY_ALIAS_TOKENS[token]
    if len(token) == 2 and token.isalpha():
        return token
    return None


def country_display_name(code: Optional[str]) -> str:
    if not code:
        return "Unknown"
    return COUNTRY_NAME_MAP.get(code, code.upper())


def country_display_name_zh(code: Optional[str]) -> str:
    if not code:
        return ""
    return COUNTRY_NAME_ZH_MAP.get(code, "")


def country_dial_code(code: Optional[str]) -> str:
    """返回不含 + 号的国际区号，如 ca → 1、gb → 44。"""
    if not code:
        return ""
    spec = COUNTRY_LANG_MAP.get(str(code).lower()) or {}
    return str(spec.get("dial") or "")


def infer_country_from_filename(filename: str) -> Optional[str]:
    stem = Path(filename or "").stem
    # 去掉常见时间戳前缀 2026-08-23_14-49-28_
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[_-]\d{2}-\d{2}-\d{2}[_-]?", "", stem)
    cleaned = re.sub(r"[_\-.]+", " ", stem).strip()
    if not cleaned:
        return None
    # 整词匹配别名（Indonesia / Base / Chile）
    for part in re.split(r"\s+", cleaned.lower()):
        hit = normalize_country(part)
        if hit:
            return hit
    collapsed = re.sub(r"\s+", "", cleaned.lower())
    return normalize_country(collapsed)


def infer_country_from_stats(stats: Dict[str, Any]) -> Optional[str]:
    locales = stats.get("system_lang_codes") or {}
    if locales:
        top_locale = max(locales.items(), key=lambda kv: kv[1])[0]
        hinted = LOCALE_COUNTRY_HINTS.get(str(top_locale).lower())
        if hinted:
            return hinted
        # es-cl / id-id 等 BCP47 后半段
        if "-" in str(top_locale):
            region = str(top_locale).split("-")[-1].lower()
            if region in COUNTRY_NAME_MAP:
                return region
    tz_map = stats.get("tz_offsets") or {}
    if tz_map:
        top_tz = max(tz_map.items(), key=lambda kv: kv[1])[0]
        try:
            tz_int = int(top_tz)
        except (TypeError, ValueError):
            tz_int = None
        candidates = TZ_COUNTRY_HINTS.get(tz_int or 0, ())
        langs = {str(k).lower() for k in (stats.get("lang_codes") or {})}
        for cand in candidates:
            lang = (COUNTRY_LANG_MAP.get(cand) or {}).get("lang_code")
            if lang and lang in langs:
                return cand
        if len(candidates) == 1:
            return candidates[0]
    return None


def infer_country(filename: str, stats: Optional[Dict[str, Any]] = None) -> Optional[str]:
    from_name = infer_country_from_filename(filename)
    from_stats = infer_country_from_stats(stats or {})
    return from_name or from_stats


def infer_brand(device_model: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "", str(device_model or "").lower())
    for prefix, brand in BRAND_PREFIXES:
        if token.startswith(prefix):
            return brand
    return "other"


def sanitize_filename(filename: Optional[str]) -> str:
    name = str(filename or "device.db").replace("\\", "/").replace("\x00", "")
    name = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip("._ ")
    if not cleaned.lower().endswith((".db", ".sqlite", ".sqlite3")):
        cleaned = f"{cleaned or 'device'}.db"
    return cleaned[:180]


def _files_dir(root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else DEVICE_DBS_DIR
    dest = base / "files"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _catalog_path(root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else DEVICE_DBS_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / "catalog.json"


def _empty_catalog() -> Dict[str, Any]:
    return {"version": CATALOG_VERSION, "updated_at": _utc_now(), "items": []}


def _read_catalog(root: Optional[Path] = None) -> Dict[str, Any]:
    path = _catalog_path(root)
    if not path.exists():
        return _empty_catalog()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return _empty_catalog()
        data.setdefault("items", [])
        data.setdefault("version", CATALOG_VERSION)
        return data
    except Exception as exc:
        logger.warning("读取硬件指纹目录失败，回退空目录: %s", exc)
        return _empty_catalog()


def _write_catalog(catalog: Dict[str, Any], root: Optional[Path] = None) -> None:
    path = _catalog_path(root)
    catalog["version"] = CATALOG_VERSION
    catalog["updated_at"] = _utc_now()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def parse_app_version(raw: Any) -> Tuple[str, str, str]:
    text = str(raw or "").strip() or "12.9.1 (69792)"
    match = re.search(r"([\d.]+)\s*\((\d+)\)", text)
    if match:
        return text, match.group(1), match.group(2)
    return text, text, "69792"


def row_to_profile(row: Tuple[Any, ...]) -> Dict[str, Any]:
    app_version, pure_ver, build_code = parse_app_version(row[4])
    try:
        tz_offset = int(row[8])
    except (TypeError, ValueError):
        tz_offset = -14400
    try:
        perf_cat = int(row[9])
    except (TypeError, ValueError):
        perf_cat = 2
    try:
        api_id = int(row[0])
    except (TypeError, ValueError):
        api_id = 6
    return {
        "api_id": api_id,
        "api_hash": str(row[1] or ""),
        "system_version": str(row[2] or "SDK 33"),
        "device_model": str(row[3] or "samsungSM-S918B"),
        "app_version": app_version,
        "app_version_pure": pure_ver,
        "app_build": build_code,
        "lang_code": str(row[5] or "en").lower(),
        "system_lang_code": str(row[6] or "en-us").lower(),
        "lang_pack": str(row[7] or "android"),
        "tz_offset": tz_offset,
        "perf_cat": perf_cat,
    }


def _table_columns(conn: sqlite3.Connection, table: str = "REGISTRATOR") -> List[str]:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return [str(item[1]).upper() for item in cursor.fetchall()]


def validate_sqlite_bytes(content: bytes) -> None:
    if not content:
        raise ValueError("上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"上传文件过大（{len(content)} > {MAX_UPLOAD_BYTES} 字节）")
    if not content.startswith(SQLITE_MAGIC):
        raise ValueError("不是合法的 SQLite 数据库（缺少 SQLite format 3 文件头）")


def parse_registrator_db(db_path: Path) -> List[Dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0]).upper()
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "REGISTRATOR" not in tables:
            raise ValueError("SQLite 中缺少 REGISTRATOR 表")
        columns = _table_columns(conn)
        missing = [col for col in REGISTRATOR_COLUMNS if col not in columns]
        if missing:
            raise ValueError(f"REGISTRATOR 缺少必要列: {', '.join(missing)}")
        cursor = conn.execute(
            "SELECT APP_ID, APP_HASH, SDK, DEVICE, APP_VERSION, "
            "LANG_CODE, SYSTEM_LANG_CODE, LANG_PACK, TZ_OFFSET, PERF_CAT "
            "FROM REGISTRATOR"
        )
        return [row_to_profile(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def compute_stats(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = list(rows)
    brands: Counter = Counter()
    models: Counter = Counter()
    sdks: Counter = Counter()
    langs: Counter = Counter()
    sys_langs: Counter = Counter()
    packs: Counter = Counter()
    tzs: Counter = Counter()
    perfs: Counter = Counter()
    versions: Counter = Counter()
    for row in items:
        model = str(row.get("device_model") or "")
        models[model] += 1
        brands[infer_brand(model)] += 1
        sdks[str(row.get("system_version") or "")] += 1
        langs[str(row.get("lang_code") or "")] += 1
        sys_langs[str(row.get("system_lang_code") or "")] += 1
        packs[str(row.get("lang_pack") or "")] += 1
        tzs[str(row.get("tz_offset"))] += 1
        perfs[str(row.get("perf_cat"))] += 1
        versions[str(row.get("app_version") or "")] += 1

    def _top(counter: Counter, limit: int = 12) -> Dict[str, int]:
        return {key: int(val) for key, val in counter.most_common(limit) if key}

    unique_models = len([k for k in models if k])
    total = len(items)
    diversity = round((unique_models / total), 4) if total else 0.0
    return {
        "total": total,
        "unique_models": unique_models,
        "diversity": diversity,
        "brands": _top(brands),
        "models": _top(models, 16),
        "sdks": _top(sdks),
        "lang_codes": _top(langs),
        "system_lang_codes": _top(sys_langs),
        "lang_packs": _top(packs),
        "tz_offsets": _top(tzs),
        "perf_cats": _top(perfs),
        "app_versions": _top(versions),
        "sample_models": [name for name, _ in models.most_common(10) if name],
    }


def assess_quality(stats: Dict[str, Any], country: Optional[str] = None) -> Dict[str, Any]:
    flags: List[str] = []
    score = 100
    total = int(stats.get("total") or 0)
    if total <= 0:
        return {"score": 0, "flags": ["empty"], "notes": "样本为空，无法调度采样"}
    if total < 30:
        flags.append("low_sample")
        score -= 15
    diversity = float(stats.get("diversity") or 0)
    if diversity < 0.08:
        flags.append("low_diversity")
        score -= 12
    brands = stats.get("brands") or {}
    if brands and max(brands.values()) / max(total, 1) > 0.85:
        flags.append("brand_monoculture")
        score -= 8
    lang_packs = stats.get("lang_packs") or {}
    if lang_packs and "android" not in {k.lower() for k in lang_packs}:
        flags.append("unexpected_lang_pack")
        score -= 20
    if country:
        expected = COUNTRY_LANG_MAP.get(country) or {}
        tzs = stats.get("tz_offsets") or {}
        if tzs and expected.get("tz_offset") is not None:
            top_tz = max(tzs.items(), key=lambda kv: kv[1])[0]
            if str(top_tz) != str(expected["tz_offset"]):
                flags.append("tz_country_mismatch")
                score -= 10
        sys_langs = {k.lower() for k in (stats.get("system_lang_codes") or {})}
        expected_lang = str(expected.get("system_lang_code") or "").lower()
        if expected_lang and expected_lang not in sys_langs:
            flags.append("locale_country_mismatch")
            score -= 8
    notes = "真机包结构完整" if not flags else "；".join(flags)
    return {"score": max(0, min(100, score)), "flags": flags, "notes": notes}


class DeviceDbManager:
    """多国家硬件指纹 SQLite 包目录。"""

    @classmethod
    def root(cls) -> Path:
        return DEVICE_DBS_DIR

    @classmethod
    def ensure_ready(cls, root: Optional[Path] = None) -> Dict[str, Any]:
        base = Path(root) if root is not None else DEVICE_DBS_DIR
        _files_dir(base)
        with _LOCK:
            catalog = _read_catalog(base)
            if Path(base).resolve() == Path(DEVICE_DBS_DIR).resolve():
                imported = cls._import_legacy_packs(catalog, base)
                if imported:
                    _write_catalog(catalog, base)
                    cls.invalidate_cache()
            return catalog

    @classmethod
    def _import_legacy_packs(cls, catalog: Dict[str, Any], root: Path) -> int:
        known_stored = {item.get("stored_name") for item in catalog.get("items") or []}
        known_origin = {item.get("origin_name") for item in catalog.get("items") or []}
        candidates = [
            DATA_DIR / "Base.db",
            Path("./2026-08-23_07-06-02_Base.db"),
            Path("/app/2026-08-23_07-06-02_Base.db"),
            Path("/app/Base.db"),
        ]
        for extra in Path(root).glob("*.db"):
            if extra.name == "catalog.json":
                continue
            candidates.append(extra)
        imported = 0
        for src in candidates:
            try:
                path = Path(src)
            except Exception:
                continue
            if not path.exists() or not path.is_file():
                continue
            if path.name in known_origin or path.name in known_stored:
                continue
            if path.resolve().parent == _files_dir(root).resolve():
                continue
            try:
                rows = parse_registrator_db(path)
            except Exception as exc:
                logger.info("跳过无法识别的遗留指纹库 %s: %s", path, exc)
                continue
            if not rows:
                continue
            stored = f"{uuid.uuid4().hex}.db"
            dest = _files_dir(root) / stored
            shutil.copy2(path, dest)
            stats = compute_stats(rows)
            country = infer_country(path.name, stats) or "cl"
            item = cls._new_item(
                origin_name=path.name,
                stored_name=stored,
                alias=cls._default_alias(path.name, country, len(rows)),
                country=country,
                source="imported",
                stats=stats,
                enabled=True,
            )
            catalog.setdefault("items", []).append(item)
            known_origin.add(path.name)
            imported += 1
            logger.info("已导入遗留硬件指纹库 %s → %s (%s, %s 条)", path.name, stored, country, len(rows))
        return imported

    @classmethod
    def _default_alias(cls, filename: str, country: Optional[str], count: int) -> str:
        label = country_display_name(country)
        zh = {
            "cl": "智利",
            "id": "印尼",
            "in": "印度",
            "ru": "俄罗斯",
            "kz": "哈萨克",
            "br": "巴西",
            "tr": "土耳其",
            "us": "美国",
            "gb": "英国",
        }.get(country or "", label)
        return f"{zh}安装{count}.db" if count else Path(filename).name

    @classmethod
    def _new_item(
        cls,
        origin_name: str,
        stored_name: str,
        alias: str,
        country: Optional[str],
        source: str,
        stats: Dict[str, Any],
        enabled: bool = True,
    ) -> Dict[str, Any]:
        now = _utc_now()
        quality = assess_quality(stats, country)
        return {
            "id": uuid.uuid4().hex,
            "origin_name": origin_name,
            "stored_name": stored_name,
            "alias": alias or origin_name,
            "country": country,
            "country_name": country_display_name(country),
            "enabled": bool(enabled),
            "source": source,
            "sample_count": int(stats.get("total") or 0),
            "stats": stats,
            "quality": quality,
            "created_at": now,
            "updated_at": now,
        }

    @classmethod
    def list_packs(cls, root: Optional[Path] = None) -> List[Dict[str, Any]]:
        catalog = cls.ensure_ready(root)
        items = list(catalog.get("items") or [])
        items.sort(key=lambda item: (not item.get("enabled"), item.get("country") or "zz", item.get("updated_at") or ""), reverse=False)
        return items

    @classmethod
    def get_pack(cls, pack_id: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        for item in cls.list_packs(root):
            if item.get("id") == pack_id:
                return item
        return None

    @classmethod
    def resolve_path(cls, pack: Dict[str, Any], root: Optional[Path] = None) -> Path:
        return _files_dir(root) / str(pack.get("stored_name"))

    @classmethod
    def load_rows(cls, pack_id: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
        pack = cls.get_pack(pack_id, root)
        if not pack:
            return []
        cache_key = f"{root or DEVICE_DBS_DIR}:{pack_id}:{pack.get('updated_at')}"
        cached = _ROW_CACHE.get(cache_key)
        if cached is not None:
            return cached
        path = cls.resolve_path(pack, root)
        rows = parse_registrator_db(path)
        _ROW_CACHE[cache_key] = rows
        return rows

    @classmethod
    def invalidate_cache(cls) -> None:
        _ROW_CACHE.clear()
        try:
            from backend.app.services.device_profile import DeviceProfileManager
            DeviceProfileManager._cached_db_devices = []
            DeviceProfileManager._db_loaded = False
        except Exception:
            pass

    @classmethod
    def import_bytes(
        cls,
        filename: str,
        content: bytes,
        alias: Optional[str] = None,
        country: Optional[str] = None,
        enabled: bool = True,
        root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        validate_sqlite_bytes(content)
        safe_name = sanitize_filename(filename)
        stored = f"{uuid.uuid4().hex}.db"
        dest = _files_dir(root) / stored
        dest.write_bytes(content)
        try:
            rows = parse_registrator_db(dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        if not rows:
            dest.unlink(missing_ok=True)
            raise ValueError("REGISTRATOR 表为空，无法作为硬件指纹包导入")
        stats = compute_stats(rows)
        resolved_country = normalize_country(country) or infer_country(safe_name, stats)
        item = cls._new_item(
            origin_name=safe_name,
            stored_name=stored,
            alias=alias or cls._default_alias(safe_name, resolved_country, len(rows)),
            country=resolved_country,
            source="upload",
            stats=stats,
            enabled=enabled,
        )
        with _LOCK:
            catalog = cls.ensure_ready(root)
            catalog.setdefault("items", []).append(item)
            _write_catalog(catalog, root)
        cls.invalidate_cache()
        logger.info("已上传硬件指纹包 %s (%s, %s 条)", item["alias"], item.get("country"), item["sample_count"])
        return item

    @classmethod
    def register_generated(
        cls,
        db_path: Path,
        filename: str,
        alias: str,
        country: str,
        stats: Dict[str, Any],
        enabled: bool = True,
        root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        stored = Path(db_path).name
        item = cls._new_item(
            origin_name=filename,
            stored_name=stored,
            alias=alias,
            country=normalize_country(country),
            source="generated",
            stats=stats,
            enabled=enabled,
        )
        with _LOCK:
            catalog = cls.ensure_ready(root)
            catalog.setdefault("items", []).append(item)
            _write_catalog(catalog, root)
        cls.invalidate_cache()
        return item

    @classmethod
    def update_pack(
        cls,
        pack_id: str,
        alias: Optional[str] = None,
        country: Optional[str] = None,
        enabled: Optional[bool] = None,
        root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        with _LOCK:
            catalog = cls.ensure_ready(root)
            target = None
            for item in catalog.get("items") or []:
                if item.get("id") == pack_id:
                    target = item
                    break
            if not target:
                raise KeyError(pack_id)
            if alias is not None:
                cleaned = str(alias).strip()
                if not cleaned:
                    raise ValueError("别名不能为空")
                target["alias"] = cleaned[:120]
            if country is not None:
                resolved = normalize_country(country)
                target["country"] = resolved
                target["country_name"] = country_display_name(resolved)
                target["quality"] = assess_quality(target.get("stats") or {}, resolved)
            if enabled is not None:
                target["enabled"] = bool(enabled)
            target["updated_at"] = _utc_now()
            _write_catalog(catalog, root)
        cls.invalidate_cache()
        return target

    @classmethod
    def delete_pack(cls, pack_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
        with _LOCK:
            catalog = cls.ensure_ready(root)
            items = list(catalog.get("items") or [])
            target = next((item for item in items if item.get("id") == pack_id), None)
            if not target:
                raise KeyError(pack_id)
            catalog["items"] = [item for item in items if item.get("id") != pack_id]
            _write_catalog(catalog, root)
        path = cls.resolve_path(target, root)
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("删除指纹库文件失败 %s: %s", path, exc)
        cls.invalidate_cache()
        return target

    @classmethod
    def refresh_stats(cls, pack_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
        pack = cls.get_pack(pack_id, root)
        if not pack:
            raise KeyError(pack_id)
        rows = parse_registrator_db(cls.resolve_path(pack, root))
        stats = compute_stats(rows)
        with _LOCK:
            catalog = cls.ensure_ready(root)
            for item in catalog.get("items") or []:
                if item.get("id") == pack_id:
                    item["stats"] = stats
                    item["sample_count"] = int(stats.get("total") or 0)
                    if not item.get("country"):
                        item["country"] = infer_country(item.get("origin_name") or "", stats)
                        item["country_name"] = country_display_name(item.get("country"))
                    item["quality"] = assess_quality(stats, item.get("country"))
                    item["updated_at"] = _utc_now()
                    pack = dict(item)
                    break
            _write_catalog(catalog, root)
        cls.invalidate_cache()
        return pack

    @classmethod
    def enabled_packs(cls, country: Optional[str] = None, root: Optional[Path] = None) -> List[Dict[str, Any]]:
        code = normalize_country(country) if country else None
        packs = [item for item in cls.list_packs(root) if item.get("enabled")]
        if code:
            matched = [item for item in packs if item.get("country") == code]
            return matched
        return packs

    @classmethod
    def select_pack(cls, country: Optional[str], root: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
        """按目标国家挑选已激活指纹包。

        返回 (pack, match_mode)：
        - country: 精确匹配该国家已激活包
        - fallback: 无国家匹配时回退到任一已激活包
        - none: 目录为空
        """
        import random

        matched = cls.enabled_packs(country, root)
        if matched:
            weights = [max(1, int(item.get("sample_count") or 1)) for item in matched]
            return random.choices(matched, weights=weights, k=1)[0], "country"
        any_enabled = cls.enabled_packs(None, root)
        if any_enabled:
            weights = [max(1, int(item.get("sample_count") or 1)) for item in any_enabled]
            return random.choices(any_enabled, weights=weights, k=1)[0], "fallback"
        return None, "none"

    @classmethod
    def select_sample(cls, country: Optional[str], root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        import random

        pack, match = cls.select_pack(country, root)
        if not pack:
            return None
        rows = cls.load_rows(str(pack["id"]), root)
        if not rows:
            return None
        row = dict(random.choice(rows))
        return {"pack": pack, "row": row, "match": match}

    @classmethod
    def aggregate_stats(cls, root: Optional[Path] = None) -> Dict[str, Any]:
        packs = cls.list_packs(root)
        enabled = [item for item in packs if item.get("enabled")]
        models: List[str] = []
        for item in enabled:
            models.extend((item.get("stats") or {}).get("sample_models") or [])
        countries = sorted({item.get("country") for item in enabled if item.get("country")})
        return {
            "total_count": sum(int(item.get("sample_count") or 0) for item in enabled),
            "is_loaded": bool(enabled),
            "sample_models": models[:12],
            "pack_count": len(packs),
            "enabled_packs": len(enabled),
            "disabled_packs": len(packs) - len(enabled),
            "active_countries": countries,
            "packs": packs,
        }
