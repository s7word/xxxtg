"""基于真机品牌参数规则库的硬件指纹合成器。

禁止盲目随机拼装机型字符串。每一行都从真实 SKU 目录中抽取，
并满足 SDK 出厂区间、Telegram 官方 Android 版本矩阵、国家语言/时区联合分布、
以及品牌在目标市场的份额权重。
"""
from __future__ import annotations

import random
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.app.config import DEVICE_DBS_DIR
from backend.app.services.device_db_manager import (
    DeviceDbManager,
    _files_dir,
    assess_quality,
    compute_stats,
    country_display_name,
    country_display_name_zh,
    country_dial_code,
    normalize_country,
)
from backend.app.services.device_profile import DeviceProfileManager, OFFICIAL_API_CREDENTIALS
from backend.app.services.telegram_android_releases import (
    TELEGRAM_9_RELEASE,
    TELEGRAM_X_RELEASE,
    attach_apk_version_code,
    official_release_tuples,
    pick_official_release,
)

GENERIC_BRAND_WEIGHTS = {
    "samsung": 30, "xiaomi": 16, "other": 12, "oppo": 10,
    "vivo": 8, "realme": 8, "huawei": 8, "motorola": 8,
}


# 合成指纹包按 AntiSafety 模板选官方 Android 凭证。
# telegram_android=6、telegram_android_public=4、telegram_x=21724、telegram_9=6。
# custom 模式不得再盖这些 App ID / 设备参数。
OFFICIAL_API_ID = 6
OFFICIAL_API_HASH = OFFICIAL_API_CREDENTIALS[6]

ANDROID_GENERATE_PRESETS = {
    "telegram_android": {
        "app_type": "telegram_android",
        "api_id": 6,
        "lang_pack": "android",
        "aid_key": "telegram_android",
        "pin_version": False,
        "label": "Android 主版",
    },
    "telegram_android_public": {
        "app_type": "telegram_android_public",
        "api_id": 4,
        "lang_pack": "android",
        "aid_key": "telegram_android",
        "pin_version": True,
        "app_version": "12.7.3 (67509)",
        "app_version_pure": "12.7.3",
        "apk_version_code": 67509,
        "label": "Android Public / vault",
    },
    "telegram_x": {
        "app_type": "telegram_x",
        "api_id": 21724,
        "lang_pack": "android_x",
        "aid_key": "telegram_x",
        "pin_version": True,
        "app_version": TELEGRAM_X_RELEASE.app_version,
        "app_version_pure": TELEGRAM_X_RELEASE.app_version_pure,
        "app_build": TELEGRAM_X_RELEASE.app_build,
        "apk_version_code": TELEGRAM_X_RELEASE.apk_version_code,
        "label": "Telegram X / TDLib",
    },
    "telegram_9": {
        "app_type": "telegram_9",
        "api_id": 6,
        "lang_pack": "android",
        "aid_key": "telegram_9",
        "pin_version": True,
        "app_version": TELEGRAM_9_RELEASE.app_version,
        "app_version_pure": TELEGRAM_9_RELEASE.app_version_pure,
        "app_build": TELEGRAM_9_RELEASE.app_build,
        "apk_version_code": TELEGRAM_9_RELEASE.apk_version_code,
        "label": "Telegram 9 Legacy",
    },
}


def resolve_android_generate_preset(app_type: Optional[str] = None) -> Dict[str, Any]:
    key = str(app_type or "telegram_android").strip() or "telegram_android"
    if key not in ANDROID_GENERATE_PRESETS:
        raise ValueError(
            "Android 合成 app_type 必须是 telegram_android / telegram_android_public / "
            "telegram_x / telegram_9（与 AntiSafety AID 模板对齐）"
        )
    return dict(ANDROID_GENERATE_PRESETS[key])

# 兼容旧导入：真实矩阵在 telegram_android_releases.py。
TELEGRAM_ANDROID_RELEASES = official_release_tuples()


@dataclass(frozen=True)
class DeviceSku:
    brand: str
    prefix: str
    model: str
    sdk_min: int
    sdk_max: int
    perf_cat: int
    weight: int = 10

    @property
    def device_model(self) -> str:
        return f"{self.prefix}{self.model}"


# 真机 SKU：manufacturer 前缀沿用官方 Android Telegram 的 Build.MANUFACTURER+MODEL 拼接习惯。
DEVICE_SKUS: Tuple[DeviceSku, ...] = (
    # Samsung
    DeviceSku("samsung", "samsung", "SM-S938B", 34, 35, 3, 8),
    DeviceSku("samsung", "samsung", "SM-S928B", 34, 35, 3, 12),
    DeviceSku("samsung", "samsung", "SM-S926B", 34, 35, 3, 8),
    DeviceSku("samsung", "samsung", "SM-S921B", 34, 35, 3, 8),
    DeviceSku("samsung", "samsung", "SM-S918B", 33, 35, 3, 14),
    DeviceSku("samsung", "samsung", "SM-S916B", 33, 34, 3, 8),
    DeviceSku("samsung", "samsung", "SM-S911B", 33, 34, 3, 10),
    DeviceSku("samsung", "samsung", "SM-S908B", 32, 34, 3, 10),
    DeviceSku("samsung", "samsung", "SM-S906B", 32, 34, 3, 7),
    DeviceSku("samsung", "samsung", "SM-S901B", 32, 34, 3, 7),
    DeviceSku("samsung", "samsung", "SM-G998B", 31, 33, 3, 6),
    DeviceSku("samsung", "samsung", "SM-G996B", 31, 33, 3, 6),
    DeviceSku("samsung", "samsung", "SM-G991B", 31, 33, 3, 8),
    DeviceSku("samsung", "samsung", "SM-G988B", 29, 32, 3, 4),
    DeviceSku("samsung", "samsung", "SM-G986B", 29, 32, 3, 4),
    DeviceSku("samsung", "samsung", "SM-G981B", 29, 32, 3, 6),
    DeviceSku("samsung", "samsung", "SM-A556B", 34, 35, 2, 12),
    DeviceSku("samsung", "samsung", "SM-A546B", 33, 35, 2, 14),
    DeviceSku("samsung", "samsung", "SM-A536B", 32, 34, 2, 10),
    DeviceSku("samsung", "samsung", "SM-A356B", 34, 35, 2, 10),
    DeviceSku("samsung", "samsung", "SM-A256B", 34, 35, 2, 9),
    DeviceSku("samsung", "samsung", "SM-A156B", 34, 35, 1, 10),
    DeviceSku("samsung", "samsung", "SM-A155F", 33, 34, 1, 8),
    DeviceSku("samsung", "samsung", "SM-A325F", 30, 33, 1, 6),
    DeviceSku("samsung", "samsung", "SM-A528B", 31, 33, 2, 6),
    DeviceSku("samsung", "samsung", "SM-M346B", 33, 34, 2, 5),
    DeviceSku("samsung", "samsung", "SM-M156B", 34, 35, 1, 5),
    # Xiaomi / Redmi / POCO
    DeviceSku("xiaomi", "Xiaomi", "22101316G", 33, 35, 3, 10),
    DeviceSku("xiaomi", "Xiaomi", "22101316UG", 33, 35, 3, 8),
    DeviceSku("xiaomi", "Xiaomi", "2201123G", 31, 34, 3, 7),
    DeviceSku("xiaomi", "Xiaomi", "21091116UG", 31, 33, 3, 5),
    DeviceSku("xiaomi", "Xiaomi", "23013PC75G", 33, 35, 3, 7),
    DeviceSku("xiaomi", "Xiaomi", "23078PND5G", 33, 34, 2, 9),
    DeviceSku("xiaomi", "Xiaomi", "2312DRA50G", 34, 35, 2, 10),
    DeviceSku("xiaomi", "Xiaomi", "2201117TG", 31, 33, 2, 8),
    DeviceSku("xiaomi", "Xiaomi", "2201117TY", 31, 33, 2, 6),
    DeviceSku("xiaomi", "Xiaomi", "M2101K6G", 30, 33, 2, 5),
    DeviceSku("xiaomi", "Xiaomi", "24069PC21G", 34, 35, 3, 6),
    DeviceSku("xiaomi", "Xiaomi", "2311DRK48G", 34, 35, 2, 6),
    DeviceSku("xiaomi", "Xiaomi", "22071219CG", 32, 34, 2, 5),
    DeviceSku("xiaomi", "POCO", "22021211RG", 31, 34, 2, 5),
    DeviceSku("xiaomi", "POCO", "2302EPCC4G", 33, 34, 1, 4),
    # Huawei
    DeviceSku("huawei", "HUAWEI", "NOH-NX9", 30, 32, 3, 4),
    DeviceSku("huawei", "HUAWEI", "ANA-NX9", 29, 31, 3, 3),
    DeviceSku("huawei", "HUAWEI", "JAD-AL50", 31, 33, 3, 3),
    DeviceSku("huawei", "HUAWEI", "ALN-AL00", 33, 34, 3, 3),
    DeviceSku("huawei", "HUAWEI", "BRA-AL00", 31, 33, 2, 3),
    DeviceSku("huawei", "HUAWEI", "NAM-LX9", 29, 31, 2, 3),
    DeviceSku("huawei", "HUAWEI", "ANY-LX3", 29, 31, 1, 3),
    # Motorola
    DeviceSku("motorola", "motorola", "XT2347-2", 33, 35, 2, 10),
    DeviceSku("motorola", "motorola", "XT2343-1", 33, 35, 2, 8),
    DeviceSku("motorola", "motorola", "XT2335-3", 33, 34, 1, 7),
    DeviceSku("motorola", "motorola", "XT2321-2", 33, 34, 3, 5),
    DeviceSku("motorola", "motorola", "XT2201-2", 31, 33, 3, 4),
    DeviceSku("motorola", "motorola", "XT2427-4", 34, 35, 2, 6),
    DeviceSku("motorola", "motorola", "XT2345-3", 33, 34, 1, 5),
    DeviceSku("motorola", "motorola", "moto g54 5G", 33, 34, 2, 4),
    # Realme
    DeviceSku("realme", "realme", "RMX3741", 33, 34, 1, 8),
    DeviceSku("realme", "realme", "RMX3630", 33, 34, 2, 7),
    DeviceSku("realme", "realme", "RMX3785", 33, 35, 1, 7),
    DeviceSku("realme", "realme", "RMX3842", 34, 35, 2, 6),
    DeviceSku("realme", "realme", "RMX3998", 34, 35, 1, 6),
    DeviceSku("realme", "realme", "RMX3941", 33, 34, 2, 5),
    DeviceSku("realme", "realme", "RMX3511", 31, 33, 1, 4),
    # Vivo
    DeviceSku("vivo", "vivo", "V2050", 29, 31, 2, 5),
    DeviceSku("vivo", "vivo", "V2111", 30, 32, 2, 6),
    DeviceSku("vivo", "vivo", "V2202", 31, 33, 2, 6),
    DeviceSku("vivo", "vivo", "V2250", 32, 34, 2, 7),
    DeviceSku("vivo", "vivo", "V2307", 33, 34, 2, 6),
    DeviceSku("vivo", "vivo", "V2320", 33, 35, 2, 6),
    DeviceSku("vivo", "vivo", "V2336", 34, 35, 2, 5),
    DeviceSku("vivo", "vivo", "V2413", 34, 35, 2, 5),
    # OPPO
    DeviceSku("oppo", "OPPO", "CPH2035", 29, 31, 2, 4),
    DeviceSku("oppo", "OPPO", "CPH2211", 30, 32, 2, 5),
    DeviceSku("oppo", "OPPO", "CPH2371", 32, 34, 2, 6),
    DeviceSku("oppo", "OPPO", "CPH2411", 33, 34, 2, 7),
    DeviceSku("oppo", "OPPO", "CPH2477", 33, 34, 2, 6),
    DeviceSku("oppo", "OPPO", "CPH2577", 34, 35, 2, 5),
    DeviceSku("oppo", "OPPO", "CPH2585", 34, 35, 3, 5),
    DeviceSku("oppo", "OPPO", "CPH2609", 34, 35, 2, 5),
    # Other / OnePlus / Infinix — 作为市场长尾
    DeviceSku("other", "OnePlus", "HD1903", 29, 31, 3, 3),
    DeviceSku("other", "OnePlus", "CPH2449", 33, 35, 3, 4),
    DeviceSku("other", "OnePlus", "CPH2581", 34, 35, 3, 3),
    DeviceSku("other", "Infinix", "X6833B", 33, 34, 1, 4),
    DeviceSku("other", "TECNO", "KJ6", 33, 34, 1, 3),
    DeviceSku("other", "google", "Pixel 7", 33, 35, 3, 2),
)


COUNTRY_SYNTH: Dict[str, Dict[str, Any]] = {
    "cl": {
        "name": "Chile",
        "locales": [
            ("es", "es-cl", 72),
            ("es", "es", 12),
            ("en", "en-us", 8),
            ("en", "en-gb", 8),
        ],
        "tz_offsets": [(-14400, 96), (-10800, 4)],
        "brands": {
            "samsung": 34, "motorola": 18, "xiaomi": 16, "huawei": 8,
            "oppo": 6, "realme": 5, "vivo": 5, "other": 8,
        },
    },
    "id": {
        "name": "Indonesia",
        "locales": [
            ("id", "id-id", 70),
            ("en", "en-us", 14),
            ("en", "en-gb", 10),
            ("id", "in-id", 6),
        ],
        "tz_offsets": [(25200, 70), (28800, 28), (32400, 2)],
        "brands": {
            "samsung": 22, "xiaomi": 18, "oppo": 16, "vivo": 15,
            "realme": 12, "motorola": 4, "huawei": 3, "other": 10,
        },
    },
    "in": {
        "name": "India",
        "locales": [
            ("en", "en-in", 48),
            ("hi", "hi-in", 28),
            ("en", "en-gb", 14),
            ("en", "en-us", 10),
        ],
        "tz_offsets": [(19800, 100)],
        "brands": {
            "xiaomi": 18, "samsung": 17, "vivo": 16, "realme": 14,
            "oppo": 12, "motorola": 8, "other": 10, "huawei": 5,
        },
    },
    "ru": {
        "name": "Russia",
        "locales": [
            ("ru", "ru-ru", 86),
            ("en", "en-us", 8),
            ("en", "en-gb", 6),
        ],
        "tz_offsets": [(10800, 70), (18000, 20), (25200, 10)],
        "brands": {
            "samsung": 28, "xiaomi": 24, "huawei": 12, "realme": 10,
            "vivo": 6, "oppo": 5, "motorola": 5, "other": 10,
        },
    },
    "kz": {
        "name": "Kazakhstan",
        "locales": [
            ("ru", "ru-kz", 62),
            ("ru", "ru-ru", 18),
            ("kk", "kk-kz", 12),
            ("en", "en-us", 8),
        ],
        "tz_offsets": [(18000, 85), (21600, 15)],
        "brands": {
            "samsung": 30, "xiaomi": 22, "huawei": 14, "realme": 8,
            "oppo": 6, "vivo": 6, "motorola": 4, "other": 10,
        },
    },
    "af": {
        "name": "Afghanistan",
        "locales": [
            ("en", "en-af", 40),
            ("fa", "fa-af", 28),
            ("ps", "ps-af", 18),
            ("en", "en-us", 14),
        ],
        "tz_offsets": [(16200, 100)],
        "brands": {
            "samsung": 28, "xiaomi": 22, "huawei": 12, "oppo": 10,
            "vivo": 8, "realme": 8, "other": 8, "motorola": 4,
        },
    },
    "us": {
        "name": "United States",
        "locales": [
            ("en", "en-us", 86),
            ("es", "es-us", 8),
            ("en", "en-gb", 6),
        ],
        "tz_offsets": [(-18000, 35), (-14400, 30), (-21600, 20), (-25200, 15)],
        "brands": {
            "samsung": 32, "other": 22, "motorola": 16, "xiaomi": 8,
            "google": 0, "oppo": 4, "vivo": 4, "realme": 4, "huawei": 2,
        },
    },
    "gb": {
        "name": "United Kingdom",
        "locales": [
            ("en", "en-gb", 88),
            ("en", "en-us", 12),
        ],
        "tz_offsets": [(0, 92), (3600, 8)],
        "brands": {
            "samsung": 34, "xiaomi": 14, "other": 16, "motorola": 10,
            "oppo": 8, "vivo": 6, "realme": 6, "huawei": 6,
        },
    },
    "br": {
        "name": "Brazil",
        "locales": [
            ("pt", "pt-br", 86),
            ("en", "en-us", 8),
            ("es", "es", 6),
        ],
        "tz_offsets": [(-10800, 88), (-14400, 12)],
        "brands": {
            "samsung": 32, "motorola": 22, "xiaomi": 16, "realme": 6,
            "oppo": 5, "vivo": 5, "huawei": 4, "other": 10,
        },
    },
    "tr": {
        "name": "Turkey",
        "locales": [
            ("tr", "tr-tr", 82),
            ("en", "en-us", 10),
            ("en", "en-gb", 8),
        ],
        "tz_offsets": [(10800, 100)],
        "brands": {
            "samsung": 30, "xiaomi": 22, "oppo": 10, "realme": 8,
            "vivo": 8, "huawei": 8, "motorola": 4, "other": 10,
        },
    },
    "pt": {
        "name": "Portugal",
        "locales": [
            ("pt", "pt-pt", 86),
            ("en", "en-gb", 8),
            ("en", "en-us", 6),
        ],
        "tz_offsets": [(0, 100)],
        "brands": {
            "samsung": 32, "xiaomi": 16, "oppo": 10, "huawei": 8,
            "motorola": 8, "realme": 6, "vivo": 6, "other": 14,
        },
    },
    "ca": {
        "name": "Canada",
        "locales": [
            ("en", "en-ca", 70),
            ("fr", "fr-ca", 22),
            ("en", "en-us", 8),
        ],
        "tz_offsets": [(-18000, 50), (-28800, 17), (-21600, 15), (-25200, 10), (-14400, 8)],
        "brands": {
            "samsung": 30, "other": 16, "motorola": 14, "xiaomi": 10,
            "google": 8, "oppo": 6, "vivo": 6, "realme": 5, "huawei": 5,
        },
    },
    "de": {
        "name": "Germany",
        "locales": [
            ("de", "de-de", 82),
            ("en", "en-gb", 10),
            ("en", "en-us", 8),
        ],
        "tz_offsets": [(3600, 92), (7200, 8)],
        "brands": {
            "samsung": 32, "xiaomi": 16, "other": 14, "oppo": 8,
            "huawei": 8, "motorola": 6, "vivo": 8, "realme": 8,
        },
    },
    "fr": {
        "name": "France",
        "locales": [
            ("fr", "fr-fr", 84),
            ("en", "en-gb", 10),
            ("en", "en-us", 6),
        ],
        "tz_offsets": [(3600, 92), (7200, 8)],
        "brands": {
            "samsung": 30, "xiaomi": 16, "other": 14, "oppo": 10,
            "huawei": 8, "vivo": 8, "realme": 8, "motorola": 6,
        },
    },
    "au": {
        "name": "Australia",
        "locales": [
            ("en", "en-au", 86),
            ("en", "en-gb", 8),
            ("en", "en-us", 6),
        ],
        "tz_offsets": [(36000, 70), (28800, 18), (34200, 12)],
        "brands": {
            "samsung": 32, "other": 18, "xiaomi": 12, "oppo": 10,
            "google": 8, "vivo": 6, "realme": 6, "motorola": 4, "huawei": 4,
        },
    },
    "jp": {
        "name": "Japan",
        "locales": [
            ("ja", "ja-jp", 86),
            ("en", "en-us", 8),
            ("en", "en-gb", 6),
        ],
        "tz_offsets": [(32400, 100)],
        "brands": {
            "samsung": 26, "other": 22, "xiaomi": 14, "oppo": 10,
            "google": 8, "vivo": 8, "realme": 6, "huawei": 6,
        },
    },
    "kr": {
        "name": "South Korea",
        "locales": [
            ("ko", "ko-kr", 88),
            ("en", "en-us", 8),
            ("en", "en-gb", 4),
        ],
        "tz_offsets": [(32400, 100)],
        "brands": {
            "samsung": 48, "other": 14, "xiaomi": 10, "oppo": 8,
            "vivo": 6, "realme": 6, "huawei": 4, "motorola": 4,
        },
    },
    "th": {
        "name": "Thailand",
        "locales": [
            ("th", "th-th", 78),
            ("en", "en-us", 12),
            ("en", "en-gb", 10),
        ],
        "tz_offsets": [(25200, 100)],
        "brands": {
            "samsung": 24, "oppo": 16, "vivo": 14, "xiaomi": 14,
            "realme": 12, "huawei": 6, "motorola": 4, "other": 10,
        },
    },
    "vn": {
        "name": "Vietnam",
        "locales": [
            ("vi", "vi-vn", 80),
            ("en", "en-us", 12),
            ("en", "en-gb", 8),
        ],
        "tz_offsets": [(25200, 100)],
        "brands": {
            "samsung": 24, "xiaomi": 16, "oppo": 16, "vivo": 14,
            "realme": 12, "huawei": 6, "motorola": 4, "other": 8,
        },
    },
    "ph": {
        "name": "Philippines",
        "locales": [
            ("en", "en-ph", 62),
            ("en", "en-us", 20),
            ("en", "en-gb", 10),
            ("tl", "tl-ph", 8),
        ],
        "tz_offsets": [(28800, 100)],
        "brands": {
            "samsung": 26, "xiaomi": 16, "oppo": 14, "vivo": 14,
            "realme": 12, "huawei": 6, "motorola": 4, "other": 8,
        },
    },
    "mx": {
        "name": "Mexico",
        "locales": [
            ("es", "es-mx", 82),
            ("es", "es", 10),
            ("en", "en-us", 8),
        ],
        "tz_offsets": [(-21600, 70), (-18000, 18), (-25200, 12)],
        "brands": {
            "samsung": 32, "motorola": 20, "xiaomi": 14, "oppo": 8,
            "huawei": 6, "vivo": 6, "realme": 6, "other": 8,
        },
    },
    "co": {
        "name": "Colombia",
        "locales": [
            ("es", "es-co", 84),
            ("es", "es", 10),
            ("en", "en-us", 6),
        ],
        "tz_offsets": [(-18000, 100)],
        "brands": {
            "samsung": 30, "motorola": 20, "xiaomi": 16, "huawei": 8,
            "oppo": 6, "vivo": 6, "realme": 6, "other": 8,
        },
    },
    "pe": {
        "name": "Peru",
        "locales": [
            ("es", "es-pe", 84),
            ("es", "es", 10),
            ("en", "en-us", 6),
        ],
        "tz_offsets": [(-18000, 100)],
        "brands": {
            "samsung": 30, "xiaomi": 18, "motorola": 16, "huawei": 8,
            "oppo": 8, "vivo": 6, "realme": 6, "other": 8,
        },
    },
    "ar": {
        "name": "Argentina",
        "locales": [
            ("es", "es-ar", 84),
            ("es", "es", 10),
            ("en", "en-us", 6),
        ],
        "tz_offsets": [(-10800, 100)],
        "brands": {
            "samsung": 32, "motorola": 22, "xiaomi": 14, "huawei": 8,
            "oppo": 6, "vivo": 6, "realme": 4, "other": 8,
        },
    },
    "eg": {
        "name": "Egypt",
        "locales": [
            ("ar", "ar-eg", 72),
            ("en", "en-us", 16),
            ("en", "en-gb", 12),
        ],
        "tz_offsets": [(7200, 100)],
        "brands": {
            "samsung": 28, "xiaomi": 18, "oppo": 12, "realme": 10,
            "huawei": 10, "vivo": 8, "motorola": 4, "other": 10,
        },
    },
    "za": {
        "name": "South Africa",
        "locales": [
            ("en", "en-za", 70),
            ("en", "en-gb", 16),
            ("en", "en-us", 14),
        ],
        "tz_offsets": [(7200, 100)],
        "brands": {
            "samsung": 32, "xiaomi": 16, "huawei": 12, "oppo": 8,
            "vivo": 8, "realme": 8, "motorola": 6, "other": 10,
        },
    },
    "ng": {
        "name": "Nigeria",
        "locales": [
            ("en", "en-ng", 72),
            ("en", "en-gb", 16),
            ("en", "en-us", 12),
        ],
        "tz_offsets": [(3600, 100)],
        "brands": {
            "samsung": 26, "xiaomi": 14, "oppo": 10, "vivo": 8,
            "realme": 8, "huawei": 8, "motorola": 8, "other": 18,
        },
    },
    "ke": {
        "name": "Kenya",
        "locales": [
            ("en", "en-ke", 70),
            ("en", "en-gb", 18),
            ("en", "en-us", 12),
        ],
        "tz_offsets": [(10800, 100)],
        "brands": {
            "samsung": 26, "xiaomi": 16, "oppo": 12, "huawei": 10,
            "vivo": 8, "realme": 8, "motorola": 6, "other": 14,
        },
    },
    "ua": {
        "name": "Ukraine",
        "locales": [
            ("uk", "uk-ua", 62),
            ("ru", "ru-ua", 20),
            ("en", "en-us", 10),
            ("en", "en-gb", 8),
        ],
        "tz_offsets": [(7200, 88), (10800, 12)],
        "brands": {
            "samsung": 28, "xiaomi": 22, "huawei": 12, "realme": 10,
            "oppo": 8, "vivo": 6, "motorola": 4, "other": 10,
        },
    },
    "uz": {
        "name": "Uzbekistan",
        "locales": [
            ("uz", "uz-uz", 52),
            ("ru", "ru-uz", 30),
            ("en", "en-us", 18),
        ],
        "tz_offsets": [(18000, 100)],
        "brands": {
            "samsung": 28, "xiaomi": 22, "huawei": 14, "realme": 10,
            "oppo": 8, "vivo": 6, "motorola": 4, "other": 8,
        },
    },
    "ae": {
        "name": "United Arab Emirates",
        "locales": [
            ("ar", "ar-ae", 48),
            ("en", "en-us", 28),
            ("en", "en-gb", 24),
        ],
        "tz_offsets": [(14400, 100)],
        "brands": {
            "samsung": 32, "other": 16, "xiaomi": 14, "huawei": 10,
            "oppo": 8, "vivo": 6, "realme": 6, "google": 4, "motorola": 4,
        },
    },
    "sa": {
        "name": "Saudi Arabia",
        "locales": [
            ("ar", "ar-sa", 72),
            ("en", "en-us", 16),
            ("en", "en-gb", 12),
        ],
        "tz_offsets": [(10800, 100)],
        "brands": {
            "samsung": 32, "huawei": 14, "xiaomi": 14, "oppo": 10,
            "vivo": 8, "realme": 8, "other": 10, "motorola": 4,
        },
    },
}


def resolve_synth_spec(country: str) -> Dict[str, Any]:
    """已注册合成规格优先；否则按全球 ISO 推断引擎即时生成自洽规格。"""
    code = normalize_country(country) or str(country or "").strip().lower()
    if code == "uk":
        code = "gb"
    if code in COUNTRY_SYNTH:
        spec = dict(COUNTRY_SYNTH[code])
        spec["inferred"] = False
        return spec
    locale = DeviceProfileManager.infer_locale(code or country)
    alts = []
    for alt in locale.get("alt_system_lang_codes") or ():
        lang = str(alt).split("-", 1)[0]
        alts.append((lang, str(alt).lower(), 12))
    locales = [
        (locale.get("lang_code") or "en", locale.get("system_lang_code") or "en-us", 76),
        *alts,
        ("en", "en-us", 12),
    ]
    tz = int(locale.get("tz_offset") or 0)
    tz_offsets = [(tz, 100)]
    tz_range = locale.get("tz_offset_range")
    if tz_range and len(tz_range) == 2 and tz_range[0] != tz_range[1]:
        tz_offsets = [(int(tz_range[0]), 20), (tz, 60), (int(tz_range[1]), 20)]
    name = locale.get("name") or country_display_name(code) or (code or "Unknown").upper()
    return {
        "name": name,
        "locales": locales,
        "tz_offsets": tz_offsets,
        "brands": dict(GENERIC_BRAND_WEIGHTS),
        "inferred": True,
    }


def list_supported_countries() -> List[Dict[str, str]]:
    listed: Dict[str, Dict[str, str]] = {}
    for code, spec in COUNTRY_SYNTH.items():
        listed[code] = {
            "code": code,
            "name": spec["name"],
            "name_zh": country_display_name_zh(code) or country_display_name(code),
            "dial": country_dial_code(code) or "",
            "group": "",
        }
    try:
        from backend.app.services.geo_catalog import iter_catalog
        for meta in iter_catalog():
            code = meta["code"]
            if code in listed:
                listed[code]["group"] = meta.get("region") or listed[code].get("group") or ""
                if not listed[code].get("name_zh"):
                    listed[code]["name_zh"] = meta.get("name_zh") or ""
                if not listed[code].get("dial"):
                    listed[code]["dial"] = meta.get("dial") or ""
                continue
            listed[code] = {
                "code": code,
                "name": meta["name"],
                "name_zh": meta.get("name_zh") or "",
                "dial": meta.get("dial") or "",
                "group": meta.get("region") or "",
            }
    except Exception:
        pass
    return list(listed.values())

# 设备指纹合成引擎已注册的国家全集
SUPPORTED_COUNTRIES = tuple(COUNTRY_SYNTH.keys())


def _weighted_choice(pairs: Sequence[Tuple[Any, int]], rng: random.Random) -> Any:
    values = [item[0] for item in pairs]
    weights = [max(0, int(item[1])) for item in pairs]
    if not values or sum(weights) <= 0:
        raise ValueError("加权抽样集合为空")
    return rng.choices(values, weights=weights, k=1)[0]


def _skus_by_brand() -> Dict[str, List[DeviceSku]]:
    grouped: Dict[str, List[DeviceSku]] = {}
    for sku in DEVICE_SKUS:
        grouped.setdefault(sku.brand, []).append(sku)
    return grouped


def pick_sku(brand_weights: Dict[str, int], rng: random.Random) -> DeviceSku:
    grouped = _skus_by_brand()
    available = {brand: skus for brand, skus in grouped.items() if brand in brand_weights and skus}
    if not available:
        available = grouped
    brands = [(brand, int(brand_weights.get(brand, 1))) for brand in available]
    brand = _weighted_choice(brands, rng)
    skus = available[brand]
    return rng.choices(skus, weights=[max(1, sku.weight) for sku in skus], k=1)[0]


def pick_sdk(sku: DeviceSku, rng: random.Random) -> int:
    # 偏近期：较高 SDK 权重更大，但仍落在该机型真实出厂区间。
    options = list(range(sku.sdk_min, sku.sdk_max + 1))
    weights = [idx + 2 for idx in range(len(options))]
    return rng.choices(options, weights=weights, k=1)[0]


def pick_app_version(sdk: int, rng: random.Random) -> Tuple[str, str, str, int]:
    release = pick_official_release(sdk, rng)
    return release.app_version, release.app_version_pure, release.app_build, release.apk_version_code


def locale_matches_country(lang_code: str, system_lang_code: str, country: str) -> bool:
    spec = COUNTRY_SYNTH.get(country) or {}
    allowed = {(item[0], item[1]) for item in spec.get("locales") or []}
    if not allowed:
        return True
    return (str(lang_code).lower(), str(system_lang_code).lower()) in allowed


def tz_matches_country(tz_offset: int, country: str) -> bool:
    spec = COUNTRY_SYNTH.get(country) or {}
    allowed = {int(item[0]) for item in spec.get("tz_offsets") or []}
    if not allowed:
        return True
    return int(tz_offset) in allowed


def sku_sdk_consistent(device_model: str, system_version: str) -> bool:
    match = None
    for sku in DEVICE_SKUS:
        if sku.device_model == device_model:
            match = sku
            break
    if match is None:
        return False
    try:
        sdk = int(str(system_version).replace("SDK", "").strip())
    except ValueError:
        return False
    return match.sdk_min <= sdk <= match.sdk_max


def synthesize_rows(
    country: str,
    count: int,
    brand_weights: Optional[Dict[str, int]] = None,
    seed: Optional[int] = None,
    app_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    code = normalize_country(country) or str(country or "").strip().lower()
    if not code:
        raise ValueError("未指定合成国家")
    spec = resolve_synth_spec(code)
    if count < 10 or count > 5000:
        raise ValueError("合成数量需在 10~5000 之间")
    weights = dict(spec["brands"])
    if brand_weights:
        for key, value in brand_weights.items():
            token = str(key).strip().lower()
            if token in weights and value is not None:
                weights[token] = int(value)
    rng = random.Random(seed)
    locales = [( (lang, sys_lang), weight) for lang, sys_lang, weight in spec["locales"]]
    tzs = [(tz, weight) for tz, weight in spec["tz_offsets"]]
    preset = resolve_android_generate_preset(app_type)
    api_id = int(preset["api_id"])
    api_hash = OFFICIAL_API_CREDENTIALS[api_id]
    lang_pack = str(preset["lang_pack"])
    rows: List[Dict[str, Any]] = []
    for _ in range(count):
        sku = pick_sku(weights, rng)
        sdk = pick_sdk(sku, rng)
        if preset.get("pin_version"):
            app_version = str(preset["app_version"])
            pure = str(preset["app_version_pure"])
            if "(" in app_version:
                app_build = app_version.split("(")[-1].rstrip(")")
            else:
                app_build = str(preset.get("app_build") or "")
                if not app_build and app_version.count(".") >= 3:
                    app_build = app_version.rsplit(".", 1)[-1]
            apk_version_code = int(preset.get("apk_version_code") or 0)
            if not apk_version_code:
                apk_version_code = int(attach_apk_version_code({
                    "app_type": preset["app_type"],
                    "app_version": app_version,
                    "app_version_pure": pure,
                    "app_build": app_build,
                    "lang_pack": lang_pack,
                }).get("apk_version_code") or 0)
        else:
            app_version, pure, app_build, apk_version_code = pick_app_version(sdk, rng)
        lang_code, system_lang_code = _weighted_choice(locales, rng)
        tz_offset = _weighted_choice(tzs, rng)
        rows.append({
            "api_id": api_id,
            "api_hash": api_hash,
            "system_version": f"SDK {sdk}",
            "device_model": sku.device_model,
            "app_version": app_version,
            "app_version_pure": pure,
            "app_build": app_build,
            "apk_version_code": int(apk_version_code),
            "lang_code": lang_code,
            "system_lang_code": system_lang_code,
            "lang_pack": lang_pack,
            "tz_offset": int(tz_offset),
            "perf_cat": int(sku.perf_cat),
            "app_type": preset["app_type"],
        })
    return rows


def write_registrator_db(rows: Sequence[Dict[str, Any]], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(dest)
    try:
        conn.execute(
            """
            CREATE TABLE REGISTRATOR (
                APP_ID INTEGER,
                APP_HASH TEXT,
                SDK TEXT,
                DEVICE TEXT,
                APP_VERSION TEXT,
                LANG_CODE TEXT,
                SYSTEM_LANG_CODE TEXT,
                LANG_PACK TEXT,
                TZ_OFFSET INTEGER,
                PERF_CAT INTEGER,
                APK_VERSION_CODE INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO REGISTRATOR VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    int(row["api_id"]),
                    str(row["api_hash"]),
                    str(row["system_version"]),
                    str(row["device_model"]),
                    str(row["app_version"]),
                    str(row["lang_code"]),
                    str(row["system_lang_code"]),
                    str(row["lang_pack"]),
                    int(row["tz_offset"]),
                    int(row["perf_cat"]),
                    int(row.get("apk_version_code") or 0),
                )
                for row in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return dest


def validate_android_rows(rows: Sequence[Dict[str, Any]], country: str) -> None:
    """合成后自检：Android 包不能混 iOS、locale/SKU 必须自洽。"""
    code = normalize_country(country) or str(country or "").strip().lower()
    if not rows:
        raise ValueError("Android 合成结果为空")
    for row in rows:
        model = str(row.get("device_model") or "")
        if model.lower().startswith("iphone") or str(row.get("lang_pack") or "").lower() == "ios":
            raise ValueError("Android 合成混入了 iOS 行")
        lang_pack = str(row.get("lang_pack") or "")
        if lang_pack not in {"android", "android_x"}:
            raise ValueError(f"Android lang_pack 必须是 android / android_x，得到 {row.get('lang_pack')}")
        try:
            api_id = int(row.get("api_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Android 行缺少 api_id") from exc
        expected = OFFICIAL_API_CREDENTIALS.get(api_id)
        if api_id not in {4, 6, 21724} or not expected:
            raise ValueError(f"Android 合成 api_id={api_id} 不是本仓允许的官方配对（4/6/21724）")
        if api_id == 21724 and lang_pack != "android_x":
            raise ValueError("api_id=21724 必须配 lang_pack=android_x")
        if api_id in {4, 6} and lang_pack != "android":
            raise ValueError(f"api_id={api_id} 必须配 lang_pack=android")
        if str(row.get("api_hash") or "").strip().lower() != expected:
            raise ValueError(f"Android api_id={api_id} 与官方 api_hash 不配对")
        if not sku_sdk_consistent(model, str(row.get("system_version") or "")):
            raise ValueError(f"机型 {model} 与 SDK {row.get('system_version')} 不在真机区间")
        if not locale_matches_country(row.get("lang_code"), row.get("system_lang_code"), code):
            raise ValueError(
                f"locale {row.get('lang_code')}/{row.get('system_lang_code')} 对不上 {code}"
            )
        if not tz_matches_country(int(row.get("tz_offset") or 0), code):
            raise ValueError(f"tz {row.get('tz_offset')} 对不上 {code}")
        resolved = attach_apk_version_code({
            **row,
            "app_type": row.get("app_type") or ("telegram_x" if lang_pack == "android_x" else "telegram_android"),
        })
        apk_version_code = int(resolved.get("apk_version_code") or 0)
        if apk_version_code <= 0:
            raise ValueError(
                f"Android 行缺少真实 APK versionCode: {row.get('app_version')} / {row.get('app_build')}"
            )
        row["apk_version_code"] = apk_version_code


def generate_country_db(
    country: str,
    count: int = 300,
    alias: Optional[str] = None,
    enabled: bool = True,
    brand_weights: Optional[Dict[str, int]] = None,
    seed: Optional[int] = None,
    root: Optional[Path] = None,
    app_type: Optional[str] = None,
) -> Dict[str, Any]:
    code = normalize_country(country)
    preset = resolve_android_generate_preset(app_type)
    rows = synthesize_rows(
        code or country,
        count,
        brand_weights=brand_weights,
        seed=seed,
        app_type=preset["app_type"],
    )
    validate_android_rows(rows, code or country)
    stats = compute_stats(rows)
    quality = assess_quality(stats, code)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    spec = resolve_synth_spec(code or country)
    origin = f"{stamp}_{spec['name']}.db"
    stored = f"{uuid.uuid4().hex}.db"
    dest = _files_dir(root or DEVICE_DBS_DIR) / stored
    write_registrator_db(rows, dest)
    label = alias or DeviceDbManager._default_alias(origin, code, len(rows))
    item = DeviceDbManager.register_generated(
        db_path=dest,
        filename=origin,
        alias=label,
        country=code or country,
        stats=stats,
        enabled=enabled,
        root=root,
        app_type=preset["app_type"],
    )
    item["quality"] = quality
    item["generated"] = {
        "requested": count,
        "written": len(rows),
        "country": code,
        "seed": seed,
        "app_type": preset["app_type"],
        "api_id": preset["api_id"],
    }
    return item


# 学术化别名
NodeTelemetrySynthesizer = generate_country_db
