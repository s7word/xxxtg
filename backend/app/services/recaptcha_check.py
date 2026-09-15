"""Telegram RECAPTCHA_CHECK 人机挑战解析与提交辅助。

Telegram 在 auth.sendCode 上可能返回：

    RPCError 403: RECAPTCHA_CHECK_signup__6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt

其中：
    action   = "signup"
    site_key = "6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"

突破方式：向 REGHelp RecaptchaMobile 解出 token，再用
`functions.InvokeWithReCaptchaRequest(token, query=SendCodeRequest)` 重发原请求。
"""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple

RECAPTCHA_CHECK_RE = re.compile(
    r"RECAPTCHA_CHECK_([a-zA-Z0-9_]+)__([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

TELEGRAM_ANDROID_PACKAGE = "org.telegram.messenger"
TELEGRAM_X_PACKAGE = "org.thunderdog.challegram"
TELEGRAM_IOS_PACKAGE = "ph.telegra.Telegraph"
# 官方 Play 安装来源。ConnectionsManager 无安装来源时写空串，Play 包几乎总是这个。
PLAY_STORE_INSTALLER = "com.android.vending"
# AndroidUtilities.getCertificateSHA256Fingerprint：SHA-256(X509.getEncoded()) 小写 hex。
# Play 商店官方签名，不是随机编的。
OFFICIAL_ANDROID_CERT_SHA256 = {
    TELEGRAM_ANDROID_PACKAGE: "49c1522548ebacd46ce322b6fd47f6092bb745d0f88082145caf35e14dcc38e1",
    TELEGRAM_X_PACKAGE: "eb801a303294ba02a84d030ebb1216c438ac985ad4b89208e32d7a7190dcdd33",
}


class RecaptchaChallengeError(Exception):
    """RECAPTCHA_CHECK 未能自动突破时抛出，触发 Vak-SMS 自动退款。"""

    def __init__(
        self,
        message: str,
        action: Optional[str] = None,
        site_key: Optional[str] = None,
    ):
        super().__init__(message)
        self.action = action
        self.site_key = site_key


def parse_recaptcha_check(error: Any) -> Optional[Tuple[str, str]]:
    """从 RPCError / 字符串提取 (action, site_key)。无法识别时返回 None。"""
    if error is None:
        return None
    blobs = []
    message = getattr(error, "message", None)
    if message:
        blobs.append(str(message))
    blobs.append(str(error))
    text = " ".join(blobs)
    match = RECAPTCHA_CHECK_RE.search(text)
    if not match:
        return None
    return match.group(1), match.group(2)


def official_android_package_id(profile: Optional[dict] = None) -> str:
    """官方 Android InitConnection.params.package_id / Recaptcha packageName。"""
    profile = profile or {}
    lang_pack = str(profile.get("lang_pack") or "").strip().lower()
    app_type = str(profile.get("app_type") or profile.get("key") or "").strip().lower()
    try:
        api_id = int(profile.get("api_id") or 0)
    except (TypeError, ValueError):
        api_id = 0
    if lang_pack == "android_x" or app_type == "telegram_x" or api_id == 21724:
        return TELEGRAM_X_PACKAGE
    return TELEGRAM_ANDROID_PACKAGE


def official_android_installer(profile: Optional[dict] = None) -> str:
    """InitConnection.params.installer。profile 可覆盖；默认 Play 商店。"""
    raw = str((profile or {}).get("installer") or "").strip()
    if raw:
        return raw
    return PLAY_STORE_INSTALLER


def official_android_cert_data(profile: Optional[dict] = None) -> str:
    """InitConnection.params.data = 官方 APK 证书 SHA-256。没有已知签名就不写。"""
    profile = profile or {}
    override = str(profile.get("cert_data") or profile.get("cert_fingerprint") or "").strip()
    if override:
        return override.replace(":", "").replace(" ", "").lower()
    package_id = official_android_package_id(profile)
    return OFFICIAL_ANDROID_CERT_SHA256.get(package_id, "")


def official_android_perf_cat(profile: Optional[dict] = None) -> Optional[int]:
    """InitConnection.params.perf_cat。官方值为 performanceClass+1（1/2/3）。"""
    raw = (profile or {}).get("perf_cat")
    if raw is None or raw == "":
        return 2
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 2
    if value in {1, 2, 3}:
        return value
    if value == 0:
        return 1
    return 2


def recaptcha_app_name(profile: Optional[dict] = None) -> str:
    """按端点模板选择 REGHelp RecaptchaMobile 的 appName。"""
    device = str((profile or {}).get("app_device") or "Android").lower()
    if device == "ios":
        return TELEGRAM_IOS_PACKAGE
    return official_android_package_id(profile)


def recaptcha_app_device(profile: Optional[dict] = None) -> str:
    device = str((profile or {}).get("app_device") or "Android")
    return "iOS" if device.lower() == "ios" else "Android"
