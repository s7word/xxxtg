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


def is_apns_hex_token(token: Optional[str]) -> bool:
    raw = str(token or "").strip()
    return len(raw) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in raw)


def ios_apns_device_token_b64(token: Optional[str]) -> Optional[str]:
    """官方 bundleData.device_token = APNS raw bytes 的 base64。只接受 64 位 hex。"""
    if not is_apns_hex_token(token):
        return None
    return base64.b64encode(bytes.fromhex(str(token).strip())).decode("ascii")


def resolve_ios_app_sandbox(attach_token: bool) -> Optional[bool]:
    """CodeSettings.app_sandbox 的官方语义（core.telegram.org/constructor/codeSettings）：

    「whether a sandbox-certificate will be used during transmission of the push notification」

    这是 **APNS 证书环境**（开发 sandbox / 生产 production），不是 iOS 进程沙盒。
    Telegram-iOS App Store 构建与 REGHelp ``tgiOS`` 签发的都是生产 APNS，
    必须为 False。设 True 会让 Telegram 用 sandbox 证书推生产 token，对不上。
    无 token 时省略（与 token 同 flag）。
    """
    if not attach_token:
        return None
    return False


def resolve_ios_allow_firebase(attach_token: bool) -> bool:
    """官方 iOS 的 token/app_sandbox 专供 Firebase auth。

    有 APNS token 时 allow_firebase 必须为 True，不能跟 Android 的 allow_app_hash 绑死。
    """
    return bool(attach_token)


def describe_device_token_submission(token: Optional[str]) -> Dict[str, Any]:
    """日志用：只描述形态与编码，不回传 token 原文。"""
    raw = str(token or "").strip()
    apns = is_apns_hex_token(raw)
    b64 = ios_apns_device_token_b64(raw) if apns else None
    return {
        "present": bool(raw),
        "length": len(raw),
        "kind": "apns_hex" if apns else ("empty" if not raw else "rejected_non_apns"),
        "init_encoding": "apns_raw_base64" if b64 else "omitted",
        "init_b64_len": len(b64) if b64 else 0,
        "codesettings_encoding": "apns_hex" if apns else ("omitted" if not raw else "rejected"),
        "preview": f"{raw[:6]}…{raw[-4:]}" if apns else "-",
    }


def format_ios_submission_audit(
    *,
    profile: Optional[Dict[str, Any]] = None,
    push_token: Optional[str] = None,
    init_keys: Optional[Sequence[str]] = None,
    app_sandbox: Any = None,
    allow_firebase: Any = None,
    allow_app_hash: Any = None,
    unknown_number: Any = None,
) -> List[str]:
    """多行审计日志：只写实际提交/明确未提交，禁止用占位值冒充字段。"""
    profile = profile or {}
    token_info = describe_device_token_submission(push_token)
    keys = list(init_keys or [])
    leaked = assert_no_android_init_keys(keys)
    sandbox_label = {
        True: "是（APNS 开发证书 / sandbox）",
        False: "否（APNS 生产证书 / production）",
        None: "省略（无 token，与 token 同 flag）",
    }.get(app_sandbox if isinstance(app_sandbox, bool) else None, f"原始={app_sandbox!r}")
    lines = [
        "iOS 提交审计（对照官方 CodeSettings / BuildConfig.bundleData / REGHelp）：",
        f"  REGHelp Push: appName={reghelp_push_app_name(profile)} appDevice=iOS aid=不提交",
        f"  InitConnection.params 键=[{','.join(keys) or '无'}] "
        f"允许键=tz_offset,bundleId,device_token",
        f"  device_token: kind={token_info['kind']} hex_len={token_info['length']} "
        f"CodeSettings={token_info['codesettings_encoding']} "
        f"InitConnection={token_info['init_encoding']} "
        f"b64_len={token_info['init_b64_len']} preview={token_info['preview']}",
        f"  app_sandbox={sandbox_label} "
        "（官方：APNS sandbox-certificate，不是 iOS 进程沙盒）",
        f"  allow_firebase={'是' if allow_firebase else '否'} "
        f"（官方 iOS token/app_sandbox 专供 Firebase auth，有 APNS 时应为是）",
        f"  allow_app_hash={'是' if allow_app_hash else '否'}（Android SMS Retriever，iOS 必须否）",
        f"  unknown_number={'是' if unknown_number else '否'}",
        "  明确未提交: cert_fingerprint（Android APK 签名指纹） / safety_net "
        "/ params.device / params.signature / AID",
    ]
    if leaked:
        lines.append(f"  ❌ 禁止键已混入 InitConnection: {','.join(leaked)}")
    if token_info["kind"] == "rejected_non_apns":
        lines.append("  ❌ iOS device_token 不是 64 位 APNS hex，已拒绝写入 InitConnection 与 CodeSettings")
    return lines


def assert_no_android_init_keys(keys: Sequence[str]) -> List[str]:
    """返回误混入的 Android InitConnection 键，供测试与日志断言。"""
    return [key for key in keys if key in ANDROID_ONLY_INIT_KEYS]
