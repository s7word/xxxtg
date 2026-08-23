import os
import re
import sqlite3
import random
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.config import ConfigManager, DATA_DIR

logger = logging.getLogger("NodeTelemetryProfileManager")

COUNTRY_LANG_MAP = {
    "cl": {"lang_code": "es", "system_lang_code": "es-cl", "tz_offset": -14400},
    "id": {"lang_code": "id", "system_lang_code": "id-id", "tz_offset": 25200},
    "ru": {"lang_code": "ru", "system_lang_code": "ru-ru", "tz_offset": 10800},
    "kz": {"lang_code": "ru", "system_lang_code": "ru-kz", "tz_offset": 18000},
    "af": {"lang_code": "en", "system_lang_code": "en-af", "tz_offset": 16200},
    "us": {"lang_code": "en", "system_lang_code": "en-us", "tz_offset": -18000},
    "gb": {"lang_code": "en", "system_lang_code": "en-gb", "tz_offset": 0},
    "br": {"lang_code": "pt", "system_lang_code": "pt-br", "tz_offset": -10800},
    "tr": {"lang_code": "tr", "system_lang_code": "tr-tr", "tz_offset": 10800},
    "in": {"lang_code": "en", "system_lang_code": "en-in", "tz_offset": 19800}
}
TOPOLOGY_LANG_MAP = COUNTRY_LANG_MAP

# 已知被公开泄露/广泛传播的官方 api_id 黑名单。
# 这些 ID 早年随官方 APK/开源客户端反编译泄露，被大量第三方工具复用，
# Telegram 服务端针对这些 ID 的 auth.sendCode 请求执行近乎无差别的风控：
# 若请求未附带合法的平台级 Push/Play-Integrity 签署凭证 (Signed Push Token)，
# 会直接返回 API_ID_PUBLISHED_FLOOD，与账号、IP、地区历史无关。
PUBLISHED_API_ID_BLOCKLIST = {4, 6, 8, 10, 2040, 2100, 17349, 21724}

# 官方端点环境规范与特征参数矩阵
DEFAULT_PROFILES = {
    "telegram_android": {
        "key": "telegram_android",
        "name": "MTProto Android Endpoint (Mainstream SDK 33)",
        "default_aid": "308aba4e-5680-466b-81a5-477ac6befa95",
        "api_id": 6,
        "api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
        "app_name": "tg",
        "app_device": "Android",
        "device_model": "Samsung Galaxy S23 Ultra",
        "system_version": "SDK 33",
        "app_version": "12.9.1 (69792)",
        "app_version_pure": "12.9.1",
        "app_build": "69792",
        "lang_pack": "android"
    },
    "telegram_x": {
        "key": "telegram_x",
        "name": "MTProto TDLib Fast Endpoint (TDLib Engine)",
        "default_aid": "47f7d612-fe1a-4167-a450-db8a52048e9c",
        "api_id": 21724,
        "api_hash": "3e0cb5ab2d48077663362339f7c30f45",
        "app_name": "tg_x",
        "app_device": "Android",
        "device_model": "Google Pixel 7 Pro",
        "system_version": "SDK 33",
        "app_version": "0.26.5.1692",
        "app_version_pure": "0.26.5",
        "app_build": "1692",
        "lang_pack": "android_x"
    },
    "telegram_9": {
        "key": "telegram_9",
        "name": "MTProto Legacy Stable Endpoint (SDK 32)",
        "default_aid": "59e59906-5177-4f6f-8f7e-ced3fe370997",
        "api_id": 6,
        "api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
        "app_name": "tg",
        "app_device": "Android",
        "device_model": "Xiaomi 13",
        "system_version": "SDK 32",
        "app_version": "9.6.7 (33219)",
        "app_version_pure": "9.6.7",
        "app_build": "33219",
        "lang_pack": "android"
    }
}


class DeviceProfileManager:
    """边缘节点硬件拓扑与环境指纹管理器 (Node Telemetry Profile Manager)"""
    _cached_db_devices: List[Dict[str, Any]] = []
    _db_loaded = False

    @classmethod
    def load_sqlite_devices(cls, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """从 SQLite 拓扑指纹数据库 (Base.db) 动态解析高保真硬件遥测样本"""
        if cls._db_loaded and cls._cached_db_devices:
            return cls._cached_db_devices

        search_paths = [
            db_path,
            Path("./2026-08-23_07-06-02_Base.db"),
            Path("/Users/mac/Downloads/tg_auto/2026-08-23_07-06-02_Base.db"),
            DATA_DIR / "Base.db"
        ]

        target_file = None
        for p in search_paths:
            if p and Path(p).exists():
                target_file = Path(p)
                break

        if not target_file:
            return []

        try:
            conn = sqlite3.connect(target_file)
            cursor = conn.cursor()
            cursor.execute("SELECT APP_ID, APP_HASH, SDK, DEVICE, APP_VERSION, LANG_CODE, SYSTEM_LANG_CODE, LANG_PACK, TZ_OFFSET, PERF_CAT FROM REGISTRATOR")
            rows = cursor.fetchall()

            parsed = []
            for r in rows:
                app_ver_str = str(r[4])
                pure_ver = app_ver_str
                build_code = "69792"
                m = re.search(r'([\d\.]+)\s*\((\d+)\)', app_ver_str)
                if m:
                    pure_ver = m.group(1)
                    build_code = m.group(2)

                parsed.append({
                    "api_id": int(r[0]),
                    "api_hash": str(r[1]),
                    "system_version": str(r[2]),
                    "device_model": str(r[3]),
                    "app_version": app_ver_str,
                    "app_version_pure": pure_ver,
                    "app_build": build_code,
                    "lang_code": str(r[5]),
                    "system_lang_code": str(r[6]),
                    "lang_pack": str(r[7]),
                    "tz_offset": int(r[8]),
                    "perf_cat": int(r[9])
                })

            cls._cached_db_devices = parsed
            cls._db_loaded = True
            logger.info(f"成功从硬件数据库 {target_file.name} 载入 {len(parsed)} 组高保真终端遥测特征")
            return parsed
        except Exception as e:
            logger.warning(f"解析硬件拓扑指纹数据库失败: {e}")
            return []

    @classmethod
    def get_all_profiles(cls) -> List[Dict[str, Any]]:
        config = ConfigManager.get_instance().config
        result = []
        for key, base in DEFAULT_PROFILES.items():
            aid = config.antisafety_aids.get(key, base["default_aid"])
            item = dict(base)
            item["aid"] = aid
            item["is_published_api_id"] = base["api_id"] in PUBLISHED_API_ID_BLOCKLIST
            item["credential_source"] = "official"
            # custom 模式下，展示层面直接呈现将真正生效的自建凭证，避免界面与实际引导行为不一致
            if config.api_credential_mode == "custom" and config.custom_api_id and config.custom_api_hash:
                item["api_id"] = config.custom_api_id
                item["api_hash"] = config.custom_api_hash
                item["is_published_api_id"] = int(config.custom_api_id) in PUBLISHED_API_ID_BLOCKLIST
                item["credential_source"] = "custom"
            result.append(item)
        return result

    @classmethod
    def resolve_effective_credentials(
        cls,
        profile: Dict[str, Any],
        config: Any,
        has_push_token: bool
    ) -> Dict[str, Any]:
        """
        根据 `api_credential_mode` 策略与本次 Push Token 获取结果，决定最终生效的 api_id/api_hash。

        - official: 始终使用模板内置的官方 api_id/api_hash (依赖 Push Token 规避 API_ID_PUBLISHED_FLOOD)
        - custom:   始终强制使用自建开发者 api_id/api_hash
        - auto:     优先使用官方 ID；若本次未拿到有效 Push Token，且官方 ID 属于已知公开泄露 ID
                    (几乎必然触发 API_ID_PUBLISHED_FLOOD)，则在已配置自建凭证的前提下自动回退
        """
        mode = getattr(config, "api_credential_mode", "auto") or "auto"
        custom_id = getattr(config, "custom_api_id", None)
        custom_hash = getattr(config, "custom_api_hash", None)
        has_custom = bool(custom_id and custom_hash)
        is_published = bool(profile.get("api_id") in PUBLISHED_API_ID_BLOCKLIST)

        resolved = dict(profile)
        resolved["is_published_api_id"] = is_published
        resolved["credential_source"] = "official"
        resolved["credential_risk"] = "none"

        if mode == "custom":
            if has_custom:
                resolved["api_id"] = int(custom_id)
                resolved["api_hash"] = custom_hash
                custom_published = int(custom_id) in PUBLISHED_API_ID_BLOCKLIST
                resolved["is_published_api_id"] = custom_published
                resolved["credential_source"] = "custom"
                if custom_published and not has_push_token:
                    resolved["credential_risk"] = "published_id_without_push_token"
            else:
                # 用户强制指定 custom 模式却未填写凭证，明确标注风险而不是静默回退
                resolved["credential_risk"] = "custom_mode_missing_credentials"
            return resolved

        if mode == "official":
            if is_published and not has_push_token:
                resolved["credential_risk"] = "published_id_without_push_token"
            return resolved

        # mode == "auto" (默认): 有 Push Token 就用官方 ID；没有 Push Token 且 ID 已知泄露，则自动切换自建 ID
        if is_published and not has_push_token:
            if has_custom and int(custom_id) not in PUBLISHED_API_ID_BLOCKLIST:
                resolved["api_id"] = int(custom_id)
                resolved["api_hash"] = custom_hash
                resolved["is_published_api_id"] = False
                resolved["credential_source"] = "custom_auto_fallback"
            elif has_custom and int(custom_id) in PUBLISHED_API_ID_BLOCKLIST:
                # 自建栏位本身也填了公开泄露 ID（常见于误把 lod_user 的 app_id=4 写进 config）
                resolved["api_id"] = int(custom_id)
                resolved["api_hash"] = custom_hash
                resolved["is_published_api_id"] = True
                resolved["credential_source"] = "custom_auto_fallback"
                resolved["credential_risk"] = "published_id_without_push_token"
            else:
                resolved["credential_risk"] = "published_id_without_push_token"

        return resolved

    @classmethod
    def get_db_stats(cls) -> Dict[str, Any]:
        devices = cls.load_sqlite_devices()
        return {
            "total_count": len(devices),
            "is_loaded": len(devices) > 0,
            "sample_models": [d["device_model"] for d in devices[:6]] if devices else []
        }

    @classmethod
    def get_resolved_profile(cls, app_type: str = "telegram_android", country: str = "cl") -> Dict[str, Any]:
        config = ConfigManager.get_instance().config
        base = DEFAULT_PROFILES.get(app_type, DEFAULT_PROFILES["telegram_android"])
        aid = config.antisafety_aids.get(app_type, base["default_aid"])

        profile = dict(base)
        profile["aid"] = aid

        # 采样匹配 Base.db 中的高保真硬件参数
        db_devices = cls.load_sqlite_devices()
        if db_devices:
            sampled_dev = random.choice(db_devices)
            profile["device_model"] = sampled_dev["device_model"]
            profile["system_version"] = sampled_dev["system_version"]
            profile["tz_offset"] = sampled_dev.get("tz_offset", -14400)

            if app_type == "telegram_android":
                profile["app_version"] = sampled_dev["app_version"]
                profile["app_version_pure"] = sampled_dev["app_version_pure"]
                profile["app_build"] = sampled_dev["app_build"]
                profile["api_id"] = sampled_dev.get("api_id", base["api_id"])
                profile["api_hash"] = sampled_dev.get("api_hash", base["api_hash"])

        # 动态对齐地理拓扑与语言环境
        lang_info = COUNTRY_LANG_MAP.get(country.lower(), {"lang_code": "es", "system_lang_code": "es-CL", "tz_offset": -14400})
        profile["lang_code"] = lang_info["lang_code"]
        profile["system_lang_code"] = lang_info["system_lang_code"]

        return profile

# 学术规范别名
NodeTelemetryProfileManager = DeviceProfileManager
