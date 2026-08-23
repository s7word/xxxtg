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
TELEGRAM_IOS_PACKAGE = "ph.telegra.Telegraph"


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


def recaptcha_app_name(profile: Optional[dict] = None) -> str:
    """按端点模板选择 REGHelp RecaptchaMobile 的 appName。"""
    device = str((profile or {}).get("app_device") or "Android").lower()
    if device == "ios":
        return TELEGRAM_IOS_PACKAGE
    return TELEGRAM_ANDROID_PACKAGE


def recaptcha_app_device(profile: Optional[dict] = None) -> str:
    device = str((profile or {}).get("app_device") or "Android")
    return "iOS" if device.lower() == "ios" else "Android"
