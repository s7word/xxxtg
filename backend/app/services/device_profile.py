import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.config import ConfigManager, DATA_DIR, LOD_USER_DIR

logger = logging.getLogger("NodeTelemetryProfileManager")

COUNTRY_LANG_MAP = {
    # 美洲
    "ca": {"lang_code": "en", "system_lang_code": "en-ca", "tz_offset": -18000, "dial": "1",
           "alt_system_lang_codes": ("fr-ca",), "tz_offset_range": (-28800, -14400)},
    "us": {"lang_code": "en", "system_lang_code": "en-us", "tz_offset": -18000, "dial": "1",
           "tz_offset_range": (-28800, -14400)},
    "mx": {"lang_code": "es", "system_lang_code": "es-mx", "tz_offset": -21600, "dial": "52",
           "tz_offset_range": (-25200, -18000)},
    "cl": {"lang_code": "es", "system_lang_code": "es-cl", "tz_offset": -14400, "dial": "56"},
    "br": {"lang_code": "pt", "system_lang_code": "pt-br", "tz_offset": -10800, "dial": "55"},
    "co": {"lang_code": "es", "system_lang_code": "es-co", "tz_offset": -18000, "dial": "57"},
    "pe": {"lang_code": "es", "system_lang_code": "es-pe", "tz_offset": -18000, "dial": "51"},
    "ar": {"lang_code": "es", "system_lang_code": "es-ar", "tz_offset": -10800, "dial": "54"},
    # 西欧
    "gb": {"lang_code": "en", "system_lang_code": "en-gb", "tz_offset": 0, "dial": "44"},
    "de": {"lang_code": "de", "system_lang_code": "de-de", "tz_offset": 3600, "dial": "49"},
    "fr": {"lang_code": "fr", "system_lang_code": "fr-fr", "tz_offset": 3600, "dial": "33"},
    # 东欧 / CIS
    "ru": {"lang_code": "ru", "system_lang_code": "ru-ru", "tz_offset": 10800, "dial": "7"},
    "ua": {"lang_code": "uk", "system_lang_code": "uk-ua", "tz_offset": 7200, "dial": "380"},
    "kz": {"lang_code": "ru", "system_lang_code": "ru-kz", "tz_offset": 18000, "dial": "7"},
    "uz": {"lang_code": "uz", "system_lang_code": "uz-uz", "tz_offset": 18000, "dial": "998"},
    # 中东
    "tr": {"lang_code": "tr", "system_lang_code": "tr-tr", "tz_offset": 10800, "dial": "90"},
    "ae": {"lang_code": "ar", "system_lang_code": "ar-ae", "tz_offset": 14400, "dial": "971"},
    "sa": {"lang_code": "ar", "system_lang_code": "ar-sa", "tz_offset": 10800, "dial": "966"},
    "eg": {"lang_code": "ar", "system_lang_code": "ar-eg", "tz_offset": 7200, "dial": "20"},
    "iq": {"lang_code": "ar", "system_lang_code": "ar-iq", "tz_offset": 10800, "dial": "964"},
    "jo": {"lang_code": "ar", "system_lang_code": "ar-jo", "tz_offset": 10800, "dial": "962"},
    "ma": {"lang_code": "ar", "system_lang_code": "ar-ma", "tz_offset": 3600, "dial": "212"},
    "af": {"lang_code": "en", "system_lang_code": "en-af", "tz_offset": 16200, "dial": "93"},
    # 非洲
    "za": {"lang_code": "en", "system_lang_code": "en-za", "tz_offset": 7200, "dial": "27"},
    "ng": {"lang_code": "en", "system_lang_code": "en-ng", "tz_offset": 3600, "dial": "234"},
    "ke": {"lang_code": "en", "system_lang_code": "en-ke", "tz_offset": 10800, "dial": "254"},
    # 亚太
    "in": {"lang_code": "en", "system_lang_code": "en-in", "tz_offset": 19800, "dial": "91"},
    "pk": {"lang_code": "en", "system_lang_code": "en-pk", "tz_offset": 18000, "dial": "92"},
    "id": {"lang_code": "id", "system_lang_code": "id-id", "tz_offset": 25200, "dial": "62"},
    "jp": {"lang_code": "ja", "system_lang_code": "ja-jp", "tz_offset": 32400, "dial": "81"},
    "kr": {"lang_code": "ko", "system_lang_code": "ko-kr", "tz_offset": 32400, "dial": "82"},
    "th": {"lang_code": "th", "system_lang_code": "th-th", "tz_offset": 25200, "dial": "66"},
    "vn": {"lang_code": "vi", "system_lang_code": "vi-vn", "tz_offset": 25200, "dial": "84"},
    "ph": {"lang_code": "en", "system_lang_code": "en-ph", "tz_offset": 28800, "dial": "63"},
    "au": {"lang_code": "en", "system_lang_code": "en-au", "tz_offset": 36000, "dial": "61",
           "tz_offset_range": (28800, 39600)},
}
TOPOLOGY_LANG_MAP = COUNTRY_LANG_MAP

# 控制台 / 指纹合成共用的全球拓扑国家全集（ISO-2，小写）
GLOBAL_TOPOLOGY_COUNTRIES = tuple(COUNTRY_LANG_MAP.keys())

# 已知被公开泄露/广泛传播的官方 api_id 黑名单。
# 这些 ID 早年随官方 APK/开源客户端反编译泄露，被大量第三方工具复用，
# Telegram 服务端针对这些 ID 的 auth.sendCode 请求执行近乎无差别的风控：
# 若请求未附带合法的平台级 Push/Play-Integrity 签署凭证 (Signed Push Token)，
# 会直接返回 API_ID_PUBLISHED_FLOOD，与账号、IP、地区历史无关。
PUBLISHED_API_ID_BLOCKLIST = {4, 6, 8, 10, 2040, 2100, 17349, 21724}

# 官方客户端 api_id → api_hash 固定配对（反编译 / opentele 共识值）。
# api_id=4 必须配 014b35…5103；混用 api_id=6 的 eb06d4…581e 会触发 SendCodeRequest invalid。
OFFICIAL_API_CREDENTIALS: Dict[int, str] = {
    4: "014b35b6184100b085b0d0572f9b5103",
    6: "eb06d4abfb49dc3eeb1aeb98ae0f581e",
    21724: "3e0cb5efcd52300aec5994fdfc5bdc16",
}


def apply_official_api_id(profile: Dict[str, Any], api_id: int) -> Dict[str, Any]:
    """把 profile 切到指定官方 api_id，并写入与之配对的官方 api_hash。"""
    resolved = dict(profile)
    resolved["api_id"] = int(api_id)
    expected = OFFICIAL_API_CREDENTIALS.get(int(api_id))
    if expected:
        current = str(resolved.get("api_hash") or "").strip().lower()
        if current and current != expected.lower():
            resolved["api_hash_was"] = current
            resolved["api_hash_corrected"] = True
        resolved["api_hash"] = expected
    return normalize_official_api_credentials(resolved)


def normalize_official_api_credentials(profile: Dict[str, Any]) -> Dict[str, Any]:
    """若 profile 使用了已知官方 api_id 但 hash 不匹配，自动纠正并标注。"""
    resolved = dict(profile)
    try:
        api_id = int(resolved.get("api_id") or 0)
    except (TypeError, ValueError):
        return resolved
    expected = OFFICIAL_API_CREDENTIALS.get(api_id)
    if not expected:
        return resolved
    current = str(resolved.get("api_hash") or "").strip().lower()
    if current and current != expected.lower():
        resolved["api_hash"] = expected
        resolved["api_hash_corrected"] = True
        resolved["api_hash_was"] = current
    elif not current:
        resolved["api_hash"] = expected
    return resolved

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
    # 早期 Android 公开泄露凭证 (api_id=4)，vault 成功样本与严格对齐默认身份
    "telegram_android_public": {
        "key": "telegram_android_public",
        "name": "MTProto Android Legacy Public (api_id=4)",
        "default_aid": "308aba4e-5680-466b-81a5-477ac6befa95",
        "api_id": 4,
        "api_hash": "014b35b6184100b085b0d0572f9b5103",
        "app_name": "tg",
        "app_device": "Android",
        "device_model": "Samsung Galaxy S23 Ultra",
        "system_version": "SDK 33",
        "app_version": "12.7.3 (67509)",
        "app_version_pure": "12.7.3",
        "app_build": "67509",
        "lang_pack": "android"
    },
    "telegram_x": {
        "key": "telegram_x",
        "name": "MTProto TDLib Fast Endpoint (TDLib Engine)",
        "default_aid": "47f7d612-fe1a-4167-a450-db8a52048e9c",
        "api_id": 21724,
        "api_hash": "3e0cb5efcd52300aec5994fdfc5bdc16",
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

_VAULT_FP_INDEX = 0
_APP_VERSION_RE = re.compile(r"^(?P<pure>.+?)\s*\((?P<build>\d+)\)\s*$")


def split_app_version(raw: Any) -> Dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {"app_version": "", "app_version_pure": "", "app_build": ""}
    matched = _APP_VERSION_RE.match(text)
    if matched:
        return {
            "app_version": text,
            "app_version_pure": matched.group("pure").strip(),
            "app_build": matched.group("build"),
        }
    return {"app_version": text, "app_version_pure": text.split()[0], "app_build": ""}


def load_vault_android_fingerprints(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """只读 lod_user 成功 JSON 的机型字段，绝不返回 token/secret。"""
    rows: List[Dict[str, Any]] = []
    base = Path(root) if root is not None else Path(LOD_USER_DIR)
    if not base.exists():
        return rows
    for path in sorted(base.rglob("91*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            app_id = int(data.get("app_id") or data.get("api_id") or 0)
        except (TypeError, ValueError):
            continue
        if app_id != 4:
            continue
        version = split_app_version(data.get("app_version"))
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            rel = path.name
        rows.append({
            "file": rel,
            "api_id": 4,
            "device_model": str(data.get("device") or "").strip(),
            "system_version": str(data.get("sdk") or "").strip(),
            "app_version": version["app_version"],
            "app_version_pure": version["app_version_pure"],
            "app_build": version["app_build"],
            "lang_pack": str(data.get("lang_pack") or "android"),
            "system_lang_code": str(
                data.get("system_lang_pack") or data.get("system_lang_code") or ""
            ).lower(),
            "tz_offset": data.get("tz_offset"),
            "has_device_secret": bool(data.get("device_secret")),
            "has_device_token": bool(data.get("device_token")),
        })
    return rows


def pick_vault_fingerprint(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    global _VAULT_FP_INDEX
    rows = load_vault_android_fingerprints(root)
    if not rows:
        return None
    row = rows[_VAULT_FP_INDEX % len(rows)]
    _VAULT_FP_INDEX += 1
    return dict(row)


class DeviceProfileManager:
    """边缘节点硬件拓扑与环境指纹管理器 (Node Telemetry Profile Manager)"""
    _cached_db_devices: List[Dict[str, Any]] = []
    _db_loaded = False

    @classmethod
    def _manager(cls):
        from backend.app.services.device_db_manager import DeviceDbManager
        return DeviceDbManager

    @classmethod
    def load_sqlite_devices(cls, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """从已激活的国家指纹包（或显式路径）解析硬件遥测样本。"""
        if db_path:
            from backend.app.services.device_db_manager import parse_registrator_db
            try:
                return parse_registrator_db(Path(db_path))
            except Exception as exc:
                logger.warning("解析指定硬件指纹库失败: %s", exc)
                return []

        if cls._db_loaded and cls._cached_db_devices:
            return cls._cached_db_devices

        manager = cls._manager()
        manager.ensure_ready()
        pooled: List[Dict[str, Any]] = []
        for pack in manager.enabled_packs():
            pooled.extend(manager.load_rows(str(pack["id"])))
        if pooled:
            cls._cached_db_devices = pooled
            cls._db_loaded = True
            logger.info("已从 %s 个已激活指纹包载入 %s 组终端遥测特征", len(manager.enabled_packs()), len(pooled))
            return pooled

        # 兼容尚未迁入 catalog 的遗留单文件 Base.db
        search_paths = [
            Path("./2026-08-23_07-06-02_Base.db"),
            Path("/Users/mac/Downloads/tg_auto/2026-08-23_07-06-02_Base.db"),
            DATA_DIR / "Base.db",
        ]
        for candidate in search_paths:
            if candidate.exists():
                try:
                    from backend.app.services.device_db_manager import parse_registrator_db
                    parsed = parse_registrator_db(candidate)
                except Exception as exc:
                    logger.warning("解析遗留硬件指纹库 %s 失败: %s", candidate, exc)
                    continue
                cls._cached_db_devices = parsed
                cls._db_loaded = True
                logger.info("成功从遗留硬件数据库 %s 载入 %s 组特征", candidate.name, len(parsed))
                return parsed
        return []

    @classmethod
    def invalidate_device_cache(cls) -> None:
        cls._cached_db_devices = []
        cls._db_loaded = False
        cls._manager().invalidate_cache()

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
        official_emu = bool(getattr(config, "official_client_emulation", False))
        mode = "official" if official_emu else (getattr(config, "api_credential_mode", "auto") or "auto")
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
            return cls._finalize_credentials(resolved, config)

        if mode == "official":
            if is_published and not has_push_token:
                resolved["credential_risk"] = "published_id_without_push_token"
            return cls._finalize_credentials(resolved, config)

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

        return cls._finalize_credentials(resolved, config)

    @classmethod
    def _finalize_credentials(cls, resolved: Dict[str, Any], config: Any) -> Dict[str, Any]:
        """官方 hash 纠偏；vault 严格模式钉死 api_id=4，禁止漂到 6。"""
        from backend.app.services.device_alignment import (
            VAULT_STRICT_API_ID,
            is_strict_alignment,
        )

        out = normalize_official_api_credentials(resolved)
        if not is_strict_alignment(config):
            return out
        try:
            current_id = int(out.get("api_id") or 0)
        except (TypeError, ValueError):
            current_id = 0
        if current_id != VAULT_STRICT_API_ID:
            out = apply_official_api_id(out, VAULT_STRICT_API_ID)
            out["credential_source"] = "vault_strict_api4"
            out["is_published_api_id"] = True
            out["api_id_pinned_from"] = current_id
        else:
            out = apply_official_api_id(out, VAULT_STRICT_API_ID)
        return out

    @classmethod
    def get_db_stats(cls) -> Dict[str, Any]:
        return cls._manager().aggregate_stats()

    @classmethod
    def infer_locale(cls, country: str) -> Dict[str, Any]:
        """任意 ISO-2 → 语言 / 时区 / 区号。预设表优先，其余走全球推断引擎。"""
        code = (country or "").strip().lower()
        if code == "uk":
            code = "gb"
        if code in COUNTRY_LANG_MAP:
            spec = dict(COUNTRY_LANG_MAP[code])
            spec["code"] = code
            spec["locale_inferred"] = False
            return spec
        from backend.app.services.geo_catalog import infer_locale as infer_iso_locale
        return infer_iso_locale(code or country)

    @classmethod
    def _apply_locale(cls, profile: Dict[str, Any], country: str, sampled: Optional[Dict[str, Any]], match: str) -> None:
        fallback = cls.infer_locale(country)
        sampled = sampled or {}
        force_country = False
        try:
            force_country = bool(
                getattr(ConfigManager.get_instance().config, "force_country_locale", False)
            )
        except Exception:
            force_country = False
        keep_sampled_locale = (
            (not force_country)
            and match == "country"
            and sampled.get("lang_code")
            and sampled.get("system_lang_code")
        )
        if keep_sampled_locale:
            sampled_lang = str(sampled.get("lang_code") or "").lower()
            primary_lang = str(fallback.get("lang_code") or "").lower()
            if primary_lang and sampled_lang != primary_lang:
                keep_sampled_locale = False
        if keep_sampled_locale:
            profile["lang_code"] = str(sampled["lang_code"]).lower()
            profile["system_lang_code"] = str(sampled["system_lang_code"]).lower()
            profile["tz_offset"] = int(sampled.get("tz_offset") or fallback.get("tz_offset") or 0)
            profile["locale_source"] = "pack"
            return
        profile["lang_code"] = fallback.get("lang_code") or "en"
        profile["system_lang_code"] = fallback.get("system_lang_code") or "en-us"
        profile["tz_offset"] = int(fallback.get("tz_offset") or 0)
        profile["locale_source"] = "country_overlay"
        if fallback.get("locale_inferred"):
            profile["locale_source"] = "iso_inferred"

    @classmethod
    def get_resolved_profile(cls, app_type: str = "telegram_android", country: str = "cl") -> Dict[str, Any]:
        from backend.app.services.device_alignment import (
            VAULT_STRICT_API_ID,
            VAULT_STRICT_LANG_PACK,
            is_strict_alignment,
            strict_app_version_pin,
        )
        from backend.app.services.vault_attestation import attach_attestation_metadata

        config = ConfigManager.get_instance().config
        strict = is_strict_alignment(config)
        if strict and app_type == "telegram_android":
            app_type = "telegram_android_public"
        base = DEFAULT_PROFILES.get(app_type, DEFAULT_PROFILES["telegram_android"])
        aid = config.antisafety_aids.get(app_type) or config.antisafety_aids.get(
            "telegram_android", base["default_aid"]
        )

        profile = dict(base)
        profile["aid"] = aid
        profile["device_pack_id"] = None
        profile["device_pack_alias"] = None
        profile["device_pack_country"] = None
        profile["device_pack_match"] = "none"
        profile["device_pack_auto"] = False
        profile["device_alignment_mode"] = "strict" if strict else "loose"

        selection = cls._manager().select_sample(country)
        pin = strict_app_version_pin(config)
        if pin and selection:
            ver0 = str((selection.get("row") or {}).get("app_version") or "")
            if pin in ver0:
                profile["app_version_pinned"] = True
            else:
                matched = None
                for _ in range(16):
                    cand = cls._manager().select_sample(country)
                    if not cand:
                        break
                    ver = str((cand.get("row") or {}).get("app_version") or "")
                    if pin in ver:
                        matched = cand
                        break
                if matched:
                    selection = matched
                    profile["app_version_pinned"] = True
                else:
                    profile["app_version_pinned"] = False
        sampled_dev = None
        match = "none"
        if selection:
            sampled_dev = selection["row"]
            pack = selection["pack"]
            match = selection.get("match") or "none"
            profile["device_model"] = sampled_dev["device_model"]
            profile["system_version"] = sampled_dev["system_version"]
            profile["perf_cat"] = sampled_dev.get("perf_cat", 2)
            # 指纹包来自 Android Registrator，lang_pack 几乎总是 android。
            # telegram_x 模板是 android_x，不能被包里的 android 覆盖，否则握手与 api_id=21724 自相矛盾。
            sampled_lp = str(sampled_dev.get("lang_pack") or "").strip()
            template_lp = str(profile.get("lang_pack") or "android").strip()
            if sampled_lp and sampled_lp.lower() == template_lp.lower():
                profile["lang_pack"] = sampled_lp
            elif sampled_lp and template_lp.lower() in {"", "android"}:
                profile["lang_pack"] = sampled_lp
            profile["device_pack_id"] = pack.get("id")
            profile["device_pack_alias"] = pack.get("alias")
            profile["device_pack_country"] = pack.get("country")
            profile["device_pack_match"] = match
            profile["device_pack_auto"] = bool(selection.get("created")) or match == "auto"
            if app_type in ("telegram_android", "telegram_android_public"):
                profile["app_version"] = sampled_dev["app_version"]
                profile["app_version_pure"] = sampled_dev["app_version_pure"]
                profile["app_build"] = sampled_dev["app_build"]
                sampled_id = sampled_dev.get("api_id")
                if app_type == "telegram_android" and not strict:
                    profile["api_id"] = sampled_id if sampled_id is not None else base["api_id"]
                    profile["api_hash"] = sampled_dev.get("api_hash", base["api_hash"])
                elif int(sampled_id or base["api_id"]) == int(base["api_id"]):
                    profile["api_id"] = int(base["api_id"])
                    profile["api_hash"] = sampled_dev.get("api_hash", base["api_hash"])

        force_country = bool(getattr(config, "force_country_locale", False)) or strict
        if force_country:
            cls._apply_locale(profile, country, None, "none")
        else:
            cls._apply_locale(profile, country, sampled_dev, match)

        want_vault = bool(getattr(config, "vault_fingerprint_replay", False)) or strict
        if want_vault:
            vault_fp = pick_vault_fingerprint()
            if vault_fp:
                if vault_fp.get("device_model"):
                    profile["device_model"] = vault_fp["device_model"]
                if vault_fp.get("system_version"):
                    profile["system_version"] = vault_fp["system_version"]
                if vault_fp.get("app_version"):
                    profile["app_version"] = vault_fp["app_version"]
                    profile["app_version_pure"] = vault_fp.get("app_version_pure") or profile.get("app_version_pure")
                    profile["app_build"] = vault_fp.get("app_build") or profile.get("app_build")
                if vault_fp.get("lang_pack"):
                    profile["lang_pack"] = vault_fp["lang_pack"]
                profile["vault_fingerprint_source"] = vault_fp.get("file")
                profile["vault_fingerprint_replay"] = True
                if not force_country and str(country or "").lower() == "in":
                    if vault_fp.get("system_lang_code"):
                        sys_lang = vault_fp["system_lang_code"]
                        profile["system_lang_code"] = sys_lang
                        profile["lang_code"] = sys_lang.split("-")[0] if sys_lang else profile.get("lang_code")
                        profile["locale_source"] = "vault_json"
                    if vault_fp.get("tz_offset") is not None:
                        profile["tz_offset"] = int(vault_fp["tz_offset"])
            else:
                profile["vault_fingerprint_replay"] = False
                profile["vault_fingerprint_source"] = None

        if pin and pin not in str(profile.get("app_version") or ""):
            # 指纹包没钉上时，严格模式仍用 vault 成功版本字符串，避免 12.9.x 漂移
            pinned = split_app_version(f"{pin} (67509)" if pin == "12.7.3" else pin)
            if pinned.get("app_version"):
                profile["app_version"] = pinned["app_version"] if "(" in pinned["app_version"] else (
                    f"{pin} (67509)" if pin == "12.7.3" else pin
                )
                profile["app_version_pure"] = pin
                if pin == "12.7.3":
                    profile["app_build"] = "67509"
                profile["app_version_pinned"] = True

        if strict:
            profile["lang_pack"] = VAULT_STRICT_LANG_PACK
            profile = apply_official_api_id(profile, VAULT_STRICT_API_ID)
            profile = attach_attestation_metadata(
                profile, config, source_file=profile.get("vault_fingerprint_source")
            )

        try:
            official_id = int(profile.get("api_id") or 0)
        except (TypeError, ValueError):
            official_id = 0
        if official_id in OFFICIAL_API_CREDENTIALS:
            profile = apply_official_api_id(profile, official_id)
        return normalize_official_api_credentials(profile)

    @classmethod
    def describe_pack_match(cls, match: str, auto_created: bool = False) -> str:
        if auto_created or match == "auto":
            return "国家自动适配（即时合成）"
        if match == "country":
            return "国家精确匹配"
        if match == "fallback":
            return "跨库回退采样"
        return "目录为空，回退端点模板默认机型"

# 学术规范别名
NodeTelemetryProfileManager = DeviceProfileManager
