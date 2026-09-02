"""Vault / Telegram Expert 设备对齐：严格模式校验与 Push token 形态。

严格模式对照 lod_user 成功样本 + 俄语农场手册（api_id=4、lang_pack=android、
号国 tz/lang、非模拟器、Push attach）。缺字段时拒绝发码，避免用半套指纹烧号。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

VAULT_STRICT_APP_VERSION_PIN = "12.7.3"
VAULT_STRICT_API_ID = 4
VAULT_STRICT_LANG_PACK = "android"

# Expert / vault 成功样本要求在发码前齐套的字段。
STRICT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "api_id",
    "api_hash",
    "device_model",
    "system_version",
    "app_version",
    "lang_code",
    "system_lang_code",
    "lang_pack",
    "tz_offset",
)

EMU_DEVICE_MARKERS = (
    "sdk_gphone",
    "generic",
    "emulator",
    "android sdk built",
    "goldfish",
    "ranchu",
    "unknown",
    "desktop",
)

PUSH_SLOT_IOS_CODESETTINGS = "CodeSettings.token(iOS-semantic)"
PUSH_SLOT_NONE = "none"


class DeviceAlignmentError(Exception):
    """严格对齐校验失败：缺字段 / 模拟器 / api_id 漂移。不应继续 auth.sendCode。"""

    def __init__(self, message: str, missing: Optional[List[str]] = None):
        super().__init__(message)
        self.missing = list(missing or [])
        self.reason = "DEVICE_ALIGNMENT_REJECTED"


def coerce_alignment_mode(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in {"strict", "vault", "expert"}:
        return "strict"
    if token in {"loose", "off", "none", "legacy"}:
        return "loose"
    return ""


def is_strict_alignment(config: Any) -> bool:
    """``device_alignment_mode=strict`` 或 ``strict_vault_device_alignment=true``。

    SimpleNamespace / 缺字段时默认 **loose**（单测不误开）。
    AppConfigModel 生产默认是 strict。
    """
    if config is None:
        return False
    explicit = getattr(config, "strict_vault_device_alignment", None)
    mode = coerce_alignment_mode(getattr(config, "device_alignment_mode", None))
    if mode == "strict":
        return True
    if mode == "loose":
        return False if explicit is not True else True
    if explicit is True:
        return True
    if explicit is False:
        return False
    return False


def init_connection_should_set_lang_pack(config: Any, profile: Optional[Dict[str, Any]] = None) -> bool:
    if config is None:
        return False
    if bool(getattr(config, "init_connection_set_lang_pack", False)):
        return True
    if is_strict_alignment(config):
        return True
    try:
        return int((profile or {}).get("api_id") or 0) == VAULT_STRICT_API_ID
    except (TypeError, ValueError):
        return False


def init_connection_should_set_tz_offset(config: Any, profile: Optional[Dict[str, Any]] = None) -> bool:
    if config is None:
        return False
    if bool(getattr(config, "init_connection_set_tz_offset", False)):
        return True
    if is_strict_alignment(config):
        return True
    try:
        return int((profile or {}).get("api_id") or 0) == VAULT_STRICT_API_ID
    except (TypeError, ValueError):
        return False


def is_emulator_device(profile: Optional[Dict[str, Any]]) -> bool:
    profile = profile or {}
    blob = " ".join(
        str(profile.get(key) or "")
        for key in ("device_model", "system_version", "app_device")
    ).lower()
    return any(marker in blob for marker in EMU_DEVICE_MARKERS)


def classify_push_token(token: Optional[str]) -> Dict[str, Any]:
    """REGHelp FCM 形态启发式。不把 token 原文写入返回值。"""
    raw = str(token or "").strip()
    length = len(raw)
    if not raw:
        return {"ok": False, "kind": "empty", "length": 0, "suspicious": True}
    kind = "opaque"
    if ":APA91" in raw:
        kind = "fcm_legacy"
    elif raw.count(":") == 1 and length >= 80:
        kind = "fcm_colon"
    elif length == 64 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
        kind = "apns_hex"
    elif length >= 100:
        kind = "long_opaque"
    ok = length >= 32
    suspicious = (not ok) or kind == "apns_hex" or length < 48
    return {
        "ok": ok,
        "kind": kind,
        "length": length,
        "suspicious": suspicious,
    }


def describe_push_slot(attached: bool) -> str:
    return PUSH_SLOT_IOS_CODESETTINGS if attached else PUSH_SLOT_NONE


def strict_app_version_pin(config: Any) -> str:
    pin = str(getattr(config, "pin_app_version_substr", "") or "").strip()
    if pin:
        return pin
    if is_strict_alignment(config):
        return VAULT_STRICT_APP_VERSION_PIN
    return ""


def missing_strict_fields(profile: Optional[Dict[str, Any]]) -> List[str]:
    profile = profile or {}
    missing: List[str] = []
    for key in STRICT_REQUIRED_FIELDS:
        val = profile.get(key)
        if val is None or (isinstance(val, str) and not str(val).strip()):
            missing.append(key)
    return missing


def validate_strict_device_profile(
    profile: Optional[Dict[str, Any]],
    config: Any = None,
    *,
    has_push_token: Optional[bool] = None,
) -> Dict[str, Any]:
    """严格模式发码前校验。通过则返回摘要；失败抛 DeviceAlignmentError。"""
    if not is_strict_alignment(config):
        return {"ok": True, "strict": False, "missing": []}

    profile = profile or {}
    missing = missing_strict_fields(profile)
    problems: List[str] = []
    if missing:
        problems.append("缺字段: " + ",".join(missing))

    try:
        api_id = int(profile.get("api_id") or 0)
    except (TypeError, ValueError):
        api_id = 0
    if api_id != VAULT_STRICT_API_ID:
        problems.append(f"api_id={api_id}（严格模式禁止漂到 6/其它，须为 {VAULT_STRICT_API_ID}）")
        if "api_id" not in missing:
            missing.append("api_id")

    lang_pack = str(profile.get("lang_pack") or "").strip().lower()
    if lang_pack != VAULT_STRICT_LANG_PACK:
        problems.append(f"lang_pack={lang_pack or '(empty)'}（须为 {VAULT_STRICT_LANG_PACK}）")
        if "lang_pack" not in missing:
            missing.append("lang_pack")

    pin = strict_app_version_pin(config)
    app_version = str(profile.get("app_version") or "")
    if pin and pin not in app_version:
        problems.append(f"app_version={app_version!r} 未钉 {pin}")
        if "app_version" not in missing:
            missing.append("app_version")

    try:
        tz = int(profile.get("tz_offset"))
    except (TypeError, ValueError):
        tz = None
        problems.append("tz_offset 无效")
        if "tz_offset" not in missing:
            missing.append("tz_offset")

    if is_emulator_device(profile):
        problems.append(
            f"机型疑似模拟器: {profile.get('device_model')}（Expert：非 emu 才 attach Push）"
        )
        missing.append("device_model")

    if has_push_token is False and not is_emulator_device(profile):
        problems.append("非 emu 严格模式要求 Push Token")
        missing.append("push_token")

    if problems:
        raise DeviceAlignmentError(
            "严格设备对齐拒绝发码: " + "; ".join(problems),
            missing=missing,
        )
    return {
        "ok": True,
        "strict": True,
        "api_id": api_id,
        "lang_pack": lang_pack,
        "tz_offset": tz,
        "app_version": app_version,
        "device_model": profile.get("device_model"),
        "missing": [],
    }


def alignment_summary_for_log(profile: Optional[Dict[str, Any]], config: Any = None) -> str:
    profile = profile or {}
    mode = "strict" if is_strict_alignment(config) else "loose"
    return (
        f"设备对齐模式={mode} api_id={profile.get('api_id')} "
        f"model={profile.get('device_model')} sdk={profile.get('system_version')} "
        f"app={profile.get('app_version')} lang_pack={profile.get('lang_pack') or '(empty)'} "
        f"lang={profile.get('system_lang_code')} tz={profile.get('tz_offset')} "
        f"emu={'是' if is_emulator_device(profile) else '否'}"
    )
