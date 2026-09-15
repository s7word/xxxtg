"""Telethon InitConnection 指纹补丁：lang_pack / params。

Telethon 1.44 在构造客户端时把 ``lang_pack`` 写死为空字符串，并注释
「langPacks are for official apps only」。vault 成功 +91 JSON 与官方
initConnection 都要求 ``lang_pack=android``，并可带 ``params.tz_offset``。

公开 ``initConnection.params`` 文档只写 ``tz_offset``。官方 iOS
``BuildConfig.bundleData`` 还会写入 App Store ``bundleId``。iOS 握手提交
这两键；APNS 走 ``CodeSettings.token``，不伪造签名、不把 ``device_token``
再塞进 params。禁止混入 Android ``safety_net`` / ``cert_fingerprint`` /
``device`` / ``signature`` / ``perf_cat``。

必须在 ``client.connect()`` **之前**调用 ``apply_init_connection_overrides``，
否则握手已发出，事后改 ``_init_request`` 无效。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.app.services.device_alignment import (
    init_connection_should_set_lang_pack,
    init_connection_should_set_tz_offset,
    official_lang_pack_for_api_id,
    profile_looks_ios,
)
from backend.app.services.ios_protocol import ANDROID_ONLY_INIT_KEYS, TELEGRAM_IOS_BUNDLE_ID
from telethon.tl import types


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


def build_init_connection_params(
    tz_offset: int,
    profile: Optional[Dict[str, Any]] = None,
    push_token: Optional[str] = None,
) -> types.JsonObject:
    """构造 InitConnection.params。

    全平台都写 tz_offset。iOS 再补官方 App Store bundleId
    （``ph.telegra.Telegraph``，与 Recaptcha packageName 同源）。
    """
    _ = push_token
    values: List[Any] = [
        types.JsonObjectValue(
            key="tz_offset",
            value=types.JsonNumber(value=float(int(tz_offset))),
        )
    ]
    if profile_looks_ios(profile):
        bundle_id = str((profile or {}).get("bundle_id") or TELEGRAM_IOS_BUNDLE_ID).strip()
        if bundle_id:
            values.append(
                types.JsonObjectValue(
                    key="bundleId",
                    value=types.JsonString(value=bundle_id),
                )
            )
    cleaned = [
        item for item in values
        if str(getattr(item, "key", "") or "") not in ANDROID_ONLY_INIT_KEYS
    ]
    return types.JsonObject(value=cleaned)


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
