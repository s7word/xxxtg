"""Telethon InitConnection 指纹补丁：lang_pack / params。

Telethon 1.44 在构造客户端时把 ``lang_pack`` 写死为空字符串，并注释
「langPacks are for official apps only」。vault 成功 +91 JSON 与官方
initConnection 都要求 ``lang_pack=android``，并可带 ``params.tz_offset``。

公开 ``initConnection.params`` 文档只写 ``tz_offset``。官方 iOS
``BuildConfig.bundleData`` 还会写入 App Store ``bundleId``。iOS 握手提交
这两键；APNS 走 ``CodeSettings.token``，不伪造签名、不把 ``device_token``
再塞进 params。禁止混入 Android ``safety_net`` / 错名 ``cert_fingerprint`` /
``device`` / ``signature``。官方 Android wrapInLayer 顺序是
``device_token`` / ``data`` / ``installer`` / ``package_id`` / ``tz_offset`` /
``perf_cat``；``data`` 只写已知 Play 签名 SHA-256。

必须在 ``client.connect()`` **之前**调用 ``apply_init_connection_overrides``，
否则握手已发出，事后改 ``_init_request`` 无效。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.app.services.device_alignment import (
    classify_push_token,
    init_connection_should_set_lang_pack,
    init_connection_should_set_tz_offset,
    official_lang_pack_for_api_id,
    profile_looks_android,
    profile_looks_ios,
)
from backend.app.services.ios_protocol import (
    ALLOWED_IOS_INIT_PARAM_KEYS,
    TELEGRAM_IOS_BUNDLE_ID,
)
from backend.app.services.recaptcha_check import (
    official_android_cert_data,
    official_android_installer,
    official_android_package_id,
    official_android_perf_cat,
)
from telethon.tl import types

# 官方 wrapInLayer 键。禁止混入 iOS bundleId / safety_net / 错名 cert_fingerprint。
ALLOWED_ANDROID_INIT_PARAM_KEYS = frozenset({
    "device_token",
    "data",
    "installer",
    "package_id",
    "tz_offset",
    "perf_cat",
})
ANDROID_FCM_TOKEN_KINDS = frozenset({"fcm_legacy", "fcm_colon"})


def _config_flag(config: Any, name: str, default: bool = False) -> bool:
    return bool(getattr(config, name, default))


def inspect_init_param_keys(params: Any) -> List[str]:
    """InitConnection.params 里实际提交的 JSON 键，按写入顺序。"""
    if params is None:
        return []
    values = getattr(params, "value", None)
    if not isinstance(values, (list, tuple)):
        return []
    keys: List[str] = []
    for item in values:
        key = str(getattr(item, "key", "") or "").strip()
        if key:
            keys.append(key)
    return keys


def inspect_tz_offset(params: Any) -> Optional[int]:
    """从 InitConnection.params（TL JsonObject）读出 tz_offset。"""
    if params is None:
        return None
    values = getattr(params, "value", None)
    if not isinstance(values, (list, tuple)):
        return None
    for item in values:
        key = str(getattr(item, "key", "") or "")
        if key != "tz_offset":
            continue
        raw = getattr(getattr(item, "value", None), "value", None)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def build_tz_offset_params(tz_offset: int) -> types.JsonObject:
    return build_init_connection_params(tz_offset)


def _keep_init_keys(values: List[Any], allowed: frozenset) -> List[Any]:
    kept: List[Any] = []
    for item in values:
        key = str(getattr(item, "key", "") or "")
        if key in allowed:
            kept.append(item)
    return kept


def _json_string(key: str, value: str) -> types.JsonObjectValue:
    return types.JsonObjectValue(key=key, value=types.JsonString(value=value))


def _json_number(key: str, value: float) -> types.JsonObjectValue:
    return types.JsonObjectValue(key=key, value=types.JsonNumber(value=float(value)))


def _android_fcm_for_init(push_token: Optional[str]) -> str:
    """Android InitConnection.params.device_token 只收 FCM，拒绝 APNS hex。"""
    raw = str(push_token or "").strip()
    if not raw:
        return ""
    kind = str(classify_push_token(raw).get("kind") or "")
    if kind in ANDROID_FCM_TOKEN_KINDS:
        return raw
    return ""


def build_init_connection_params(
    tz_offset: int,
    profile: Optional[Dict[str, Any]] = None,
    push_token: Optional[str] = None,
) -> types.JsonObject:
    """构造 InitConnection.params。两条线允许名单隔离，禁止交叉。

    共用：tz_offset。
    iOS 独有：``bundleId=ph.telegra.Telegraph``。APNS 只走 CodeSettings.token，
    **禁止**把同名 ``device_token`` 写进 params（官方 iOS 源码里那是 APNS base64，
    本仓合同不提交；Android 同名键是 FCM，更不能混）。
    Android 独有，顺序对齐官方 wrapInLayer：
    ``device_token``（有 FCM）/ ``data``（已知 Play 签名）/ ``installer`` /
    ``package_id`` / ``tz_offset`` / ``perf_cat``。
    不写 iOS ``bundleId``，不编未知证书。
    """
    if profile_looks_ios(profile):
        values: List[Any] = [_json_number("tz_offset", int(tz_offset))]
        bundle_id = str((profile or {}).get("bundle_id") or TELEGRAM_IOS_BUNDLE_ID).strip()
        if bundle_id:
            values.append(_json_string("bundleId", bundle_id))
        return types.JsonObject(value=_keep_init_keys(values, ALLOWED_IOS_INIT_PARAM_KEYS))

    try:
        android_api = int((profile or {}).get("api_id") or 0)
    except (TypeError, ValueError):
        android_api = 0
    if profile_looks_android(profile) or android_api in {4, 6, 21724}:
        values = []
        fcm = _android_fcm_for_init(push_token)
        if fcm:
            values.append(_json_string("device_token", fcm))
        cert = official_android_cert_data(profile)
        if cert:
            values.append(_json_string("data", cert))
        values.append(_json_string("installer", official_android_installer(profile)))
        package_id = str((profile or {}).get("package_id") or official_android_package_id(profile)).strip()
        values.append(_json_string("package_id", package_id or official_android_package_id(profile)))
        values.append(_json_number("tz_offset", int(tz_offset)))
        perf_cat = official_android_perf_cat(profile)
        if perf_cat is not None:
            values.append(_json_number("perf_cat", perf_cat))
        return types.JsonObject(value=_keep_init_keys(values, ALLOWED_ANDROID_INIT_PARAM_KEYS))
    return types.JsonObject(
        value=_keep_init_keys([_json_number("tz_offset", int(tz_offset))], frozenset({"tz_offset"}))
    )


def describe_init_connection(client: Any) -> str:
    req = getattr(client, "_init_request", None)
    if req is None:
        return "InitConnection 不可用（客户端无 _init_request，Telethon 版本或构造方式不支持）"
    lang_pack = getattr(req, "lang_pack", None)
    if not isinstance(lang_pack, str):
        lang_disp = "(empty)"
    else:
        lang_disp = lang_pack if lang_pack else "(empty)"
    params = getattr(req, "params", None)
    tz = inspect_tz_offset(params)
    tz_disp = str(tz) if tz is not None else "未写入"
    keys = inspect_init_param_keys(params)
    keys_disp = ",".join(keys) if keys else "无"
    return (
        f"InitConnection 指纹: lang_pack={lang_disp} tz_offset={tz_disp} "
        f"params=[{keys_disp}]"
    )


def snapshot_init_connection(client: Any) -> Dict[str, Any]:
    req = getattr(client, "_init_request", None)
    if req is None:
        return {"available": False, "blocked": "no_init_request"}
    lang_pack = getattr(req, "lang_pack", "")
    if not isinstance(lang_pack, str):
        lang_pack = ""
    params = getattr(req, "params", None)
    return {
        "available": True,
        "lang_pack": lang_pack if lang_pack else "",
        "lang_pack_empty": not bool(lang_pack),
        "tz_offset": inspect_tz_offset(params),
        "param_keys": inspect_init_param_keys(params),
        "has_params": params is not None,
    }


def apply_init_connection_overrides(
    client: Any,
    profile: Optional[Dict[str, Any]] = None,
    config: Any = None,
    push_token: Optional[str] = None,
) -> Dict[str, Any]:
    """按配置写入 InitConnection.lang_pack / params。返回可进日志的快照。"""
    profile = profile or {}
    req = getattr(client, "_init_request", None)
    out: Dict[str, Any] = {
        "available": req is not None,
        "lang_pack_set": False,
        "tz_offset_set": False,
        "lang_pack": None,
        "tz_offset": None,
        "param_keys": [],
        "blocked": None,
        "before": snapshot_init_connection(client),
    }
    if req is None:
        out["blocked"] = "no_init_request"
        return out

    # MagicMock / 非 Telethon 对象：lang_pack 不是 str，禁止当真写入以免单测误报握手成功
    lang_attr = getattr(req, "lang_pack", None)
    if not isinstance(lang_attr, str):
        out["available"] = False
        out["blocked"] = "init_request_not_writable"
        return out

    if init_connection_should_set_lang_pack(config, profile):
        lang_pack = str(profile.get("lang_pack") or "").strip() or official_lang_pack_for_api_id(
            profile.get("api_id")
        )
        req.lang_pack = lang_pack
        out["lang_pack_set"] = True
        out["lang_pack"] = lang_pack
    else:
        out["lang_pack"] = getattr(req, "lang_pack", "") or ""

    if init_connection_should_set_tz_offset(config, profile):
        override = getattr(config, "init_connection_tz_offset_override", None)
        if override is None or override == "":
            tz = int(profile.get("tz_offset") or 0)
        else:
            tz = int(override)
        req.params = build_init_connection_params(tz, profile, push_token)
        out["tz_offset_set"] = True
        out["tz_offset"] = tz
        out["param_keys"] = inspect_init_param_keys(req.params)
    else:
        out["tz_offset"] = inspect_tz_offset(getattr(req, "params", None))
        out["param_keys"] = inspect_init_param_keys(getattr(req, "params", None))

    out["after"] = snapshot_init_connection(client)
    return out
