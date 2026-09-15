"""iOS 官方线路隔离：REGHelp / InitConnection / 邮箱，禁止混入 Android 方案。

对照来源（本模块只收录能对上公开文档的字段）：

- REGHelp Key API：https://reghelp.net/en/api-docs/
  Push 官方 iOS 必须 ``appName=tgiOS`` + ``appDevice=iOS``。
  AntiSafety 不在 REGHelp 协议里，也没有 iOS Push；iOS 线路不得走 AID。
- Telegram-iOS ``build-system/verify.sh``：App Store 构建 ``api_id=8`` /
  ``api_hash=7245de8e747a0d6fbe11f7cc14fcc0bb``，``lang_pack=ios``，
  bundle ``ph.telegra.Telegraph``。
- Telegram-iOS ``BuildConfig.bundleData``：InitConnection.appData 只有
  ``bundleId`` / ``tz_offset`` / 可选 ``device_token``（APNS raw 的 base64）
  以及真机代码签名 ``issuerName/name/data/data1``。
  **没有** Android Expert 的 ``safety_net`` / ``cert_fingerprint`` /
  ``device=iphone`` / ``signature=unknown``。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Sequence

from backend.app.services.device_alignment import profile_looks_ios

REGHELP_IOS_PUSH_APP_NAME = "tgiOS"
TELEGRAM_IOS_BUNDLE_ID = "ph.telegra.Telegraph"
OFFICIAL_IOS_API_ID = 8
OFFICIAL_IOS_API_HASH = "7245de8e747a0d6fbe11f7cc14fcc0bb"

# 第三方列表里的「正式版 iOS」候选。公开源码对不上，本轮不启用。
# 94575 在同一篇中文摘录里同时标成 TDLib example 与 Telegram for iOS。
# 10840 是 opentele 的旧社区 TelegramIOS 值。
UNOFFICIAL_IOS_API_CANDIDATES: Dict[int, str] = {
    94575: "a3406de8d171bb422bb6ddf3bbd800e2",
    10840: "33c45224029d59cb3ad0c16134215aeb",
}

ANDROID_ONLY_INIT_KEYS = frozenset({
    "safety_net",
    "cert_fingerprint",
    "signature",
    "device",
})


def is_ios_profile(profile: Optional[Dict[str, Any]] = None) -> bool:
    return profile_looks_ios(profile)


def skip_antisafety_for_profile(profile: Optional[Dict[str, Any]] = None) -> bool:
    """AntiSafety 无 iOS Push / 无 iOS 审计；iOS 线路整段跳过。"""
    return is_ios_profile(profile)


def reghelp_push_app_name(profile: Optional[Dict[str, Any]] = None) -> str:
    """REGHelp /push/getToken 的 appName。iOS 必须是 tgiOS，不是 Android 的 tg。"""
    profile = profile or {}
    if is_ios_profile(profile):
        return REGHELP_IOS_PUSH_APP_NAME
    name = str(profile.get("app_name") or "").strip()
    if name == REGHELP_IOS_PUSH_APP_NAME:
        return "tg"
    return name or "tg"


def reghelp_email_app_name(profile: Optional[Dict[str, Any]] = None) -> str:
    """REGHelp /email/getEmail 的 appName。文档示例是 tg + iOS，不是 Push 的 tgiOS。"""
    profile = profile or {}
    if is_ios_profile(profile):
        return "tg"
    name = str(profile.get("app_name") or "").strip()
    if name == REGHELP_IOS_PUSH_APP_NAME:
        return "tg"
    return name or "tg"


def resolve_login_email_types(
    profile: Optional[Dict[str, Any]] = None,
    config: Any = None,
) -> List[str]:
    """决定向邮箱提供源申请的 type 顺序。

    SMS Bower 只有 Google/gmail.com，跟客户端是不是 iOS 无关。
    旧逻辑「iOS 优先 icloud」会让日志写成 type=icloud，实际却订了 Gmail。
    REGHelp Email 才支持 ``icloud|gmail``；Gmail 目前常 SERVICE_DISABLED。
    """
    mode = str(getattr(config, "email_provider_mode", "smsbower_primary") or "smsbower_primary")
    if mode.startswith("smsbower"):
        return ["gmail"]
    if is_ios_profile(profile):
        return ["icloud", "gmail"]
    return ["gmail"]


def should_migrate_to_nearest_dc(profile: Optional[Dict[str, Any]], this_dc: Any, nearest_dc: Any) -> bool:
    """官方 iOS 会按 GetNearestDc 切到建议 DC。Telethon 默认从 DC2 起连。"""
    if not is_ios_profile(profile):
        return False
    try:
        current = int(this_dc)
        nearest = int(nearest_dc)
    except (TypeError, ValueError):
        return False
    return current > 0 and nearest > 0 and current != nearest


def ios_apns_device_token_b64(token: Optional[str]) -> Optional[str]:
    """官方 bundleData.device_token = APNS raw bytes 的 base64。只接受 64 位 hex。"""
    raw = str(token or "").strip()
    if len(raw) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        return None
    return base64.b64encode(bytes.fromhex(raw)).decode("ascii")


def assert_no_android_init_keys(keys: Sequence[str]) -> List[str]:
    """返回误混入的 Android InitConnection 键，供测试与日志断言。"""
    return [key for key in keys if key in ANDROID_ONLY_INIT_KEYS]
