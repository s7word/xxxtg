"""iOS 备用机型库：只给 telegram_ios 抽，不进 Android 调度。

机型用官方客户端常见的营销名（与当前模板 ``iPhone 15 Pro`` 同一写法），
系统版本落在本仓已在用的 iOS 18.x。语言/时区跟出口国 overlay，不抄 Android 包。
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.config import DEVICE_DBS_DIR
from backend.app.services.device_alignment import OFFICIAL_IOS_API_ID
from backend.app.services.device_db_manager import (
    DeviceDbManager,
    _files_dir,
    assess_quality,
    compute_stats,
    normalize_country,
)
from backend.app.services.device_generator import write_registrator_db
from backend.app.services.device_profile import OFFICIAL_API_CREDENTIALS
from backend.app.services.ios_protocol import (
    OFFICIAL_IOS_API_HASH,
    apply_ios_country_locale,
    canonicalize_ios_system_lang,
)

IOS_APP_VERSION = "12.9.3"
# 列表页自动保底的国家；指定国家生成不限这份名单，跟出口 locale overlay。
IOS_SEED_COUNTRIES = frozenset({"ph", "tr", "pt"})
IOS_PH_PACK_COUNT = 48


@dataclass(frozen=True)
class IosSku:
    model: str
    ios_versions: Tuple[str, ...]
    weight: int


# 比全员 15 Pro 更高一档。17 系列按营销名收录；iOS 版本与本仓现行 18.6.2 对齐，不编造 26/27。
IOS_SKUS: Tuple[IosSku, ...] = (
    IosSku("iPhone 16 Pro", ("18.5.1", "18.6.2", "18.6.2"), 20),
    IosSku("iPhone 16 Pro Max", ("18.5.1", "18.6.2"), 14),
    IosSku("iPhone 16", ("18.5", "18.6.2"), 12),
    IosSku("iPhone 16 Plus", ("18.5", "18.6.2"), 8),
    IosSku("iPhone 17 Pro", ("18.6.2",), 16),
    IosSku("iPhone 17 Pro Max", ("18.6.2",), 10),
    IosSku("iPhone 17", ("18.6.2",), 10),
    IosSku("iPhone 15 Pro", ("18.6.2", "18.5.1"), 6),
)


def _weighted_choice(items: Sequence[Tuple[Any, int]], rng: random.Random) -> Any:
    population = [item for item, _ in items]
    weights = [max(1, int(weight)) for _, weight in items]
    return rng.choices(population, weights=weights, k=1)[0]


def synthesize_ios_rows(
    country: str,
    count: int = IOS_PH_PACK_COUNT,
    *,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    code = normalize_country(country) or str(country or "").strip().lower()
    if not code:
        raise ValueError("未指定 iOS 合成国家")
    locale = apply_ios_country_locale({}, code)
    rng = random.Random(seed)
    sku_choices = [(sku, sku.weight) for sku in IOS_SKUS]
    rows: List[Dict[str, Any]] = []
    for _ in range(max(1, int(count))):
        sku = _weighted_choice(sku_choices, rng)
        ios_ver = rng.choice(sku.ios_versions)
        rows.append({
            "api_id": OFFICIAL_IOS_API_ID,
            "api_hash": OFFICIAL_IOS_API_HASH or OFFICIAL_API_CREDENTIALS.get(OFFICIAL_IOS_API_ID, ""),
            "system_version": ios_ver,
            "device_model": sku.model,
            "app_version": IOS_APP_VERSION,
            "app_version_pure": IOS_APP_VERSION,
            "app_build": "",
            "lang_code": locale.get("lang_code") or "en",
            "system_lang_code": canonicalize_ios_system_lang(locale.get("system_lang_code") or "en-PH"),
            "lang_pack": "ios",
            "tz_offset": int(locale["tz_offset"]) if locale.get("tz_offset") is not None else 0,
            "perf_cat": 3,
        })
    return rows


def generate_ios_country_db(
    country: str,
    count: int = IOS_PH_PACK_COUNT,
    alias: Optional[str] = None,
    enabled: bool = True,
    seed: Optional[int] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    code = normalize_country(country)
    if not code:
        raise ValueError("未指定 iOS 合成国家")
    rows = synthesize_ios_rows(code, count, seed=seed)
    if not all(str(row.get("lang_pack") or "") == "ios" for row in rows):
        raise ValueError("iOS 合成行 lang_pack 必须是 ios")
    if not all(int(row.get("api_id") or 0) == OFFICIAL_IOS_API_ID for row in rows):
        raise ValueError(f"iOS 合成行 api_id 必须是 {OFFICIAL_IOS_API_ID}")
    stats = compute_stats(rows)
    quality = assess_quality(stats, code, platform="ios")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    origin = f"{stamp}_iOS_{code.upper()}.db"
    stored = f"{uuid.uuid4().hex}.db"
    dest = _files_dir(root or DEVICE_DBS_DIR) / stored
    write_registrator_db(rows, dest)
    label = alias or f"iOS 备用 {code.upper()} · {len(rows)}.db"
    item = DeviceDbManager.register_generated(
        db_path=dest,
        filename=origin,
        alias=label,
        country=code,
        stats=stats,
        enabled=enabled,
        root=root,
        platform="ios",
    )
    item["quality"] = quality
    item["platform"] = "ios"
    item["generated"] = {
        "requested": count,
        "written": len(rows),
        "country": code,
        "platform": "ios",
        "seed": seed,
    }
    return item
