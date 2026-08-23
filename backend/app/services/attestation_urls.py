"""Attestation 提供源 URL / 密钥隔离工具。

旧版 AntiSafetyService 曾把 `https://api.reghelp.net` 混进 `antisafety_base_urls`，
导致用 AntiSafety 的 Key 去打 REGHelp（或反过来）时直接返回
`{'detail': 'Invalid API key'}`，整条 Push Token 链路被误判为失败。

REGHelp 与 AntiSafety 是两套独立服务：
    - reghelp    -> config.reghelp_api_key    + config.reghelp_base_urls
    - antisafety -> config.antisafety_api_key + config.antisafety_base_urls
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional
from urllib.parse import urlparse

REGHELP_HOST_MARKERS = ("reghelp.net",)
ANTISAFETY_HOST_MARKERS = ("antisafety.net",)

DEFAULT_REGHELP_BASES = ["https://api.reghelp.net"]
DEFAULT_ANTISAFETY_BASES = ["https://api.antisafety.net"]
DEFAULT_ANTISAFETY_REPORTING_BASES = ["https://reporting.antisafety.net"]

AUTH_ERROR_MARKERS = (
    "invalid api key",
    "invalid apikey",
    "unauthorized",
    "forbidden",
    "api key is invalid",
    "wrong api key",
    "incorrect api key",
)


def _host(url: str) -> str:
    try:
        parsed = urlparse(str(url or "").strip())
        return (parsed.netloc or parsed.path or "").lower()
    except Exception:
        return str(url or "").lower()


def is_reghelp_url(url: str) -> bool:
    host = _host(url)
    return any(marker in host for marker in REGHELP_HOST_MARKERS)


def is_antisafety_url(url: str) -> bool:
    host = _host(url)
    return any(marker in host for marker in ANTISAFETY_HOST_MARKERS)


def _normalize_url_list(urls: Optional[Iterable[Any]]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for raw in urls or []:
        text = str(raw or "").strip().rstrip("/")
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def sanitize_provider_urls(
    urls: Optional[Iterable[Any]],
    provider: str,
    default: Optional[List[str]] = None,
) -> List[str]:
    """剔除交叉污染的候选网关，保证每个提供源只保留自家地址。"""
    provider = (provider or "").strip().lower()
    normalized = _normalize_url_list(urls)
    isolated: List[str] = []

    for url in normalized:
        host = _host(url)
        if provider == "reghelp":
            if is_antisafety_url(url):
                continue
        elif provider == "antisafety":
            if is_reghelp_url(url) or "reporting." in host:
                continue
        elif provider == "antisafety_reporting":
            if is_reghelp_url(url):
                continue
            if is_antisafety_url(url) and "reporting." not in host:
                continue
        isolated.append(url)

    if isolated:
        return isolated

    if default is not None:
        return list(default)
    if provider == "reghelp":
        return list(DEFAULT_REGHELP_BASES)
    if provider == "antisafety_reporting":
        return list(DEFAULT_ANTISAFETY_REPORTING_BASES)
    return list(DEFAULT_ANTISAFETY_BASES)


def has_valid_api_key(api_key: Optional[str]) -> bool:
    text = str(api_key or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in {"none", "null", "your_api_key", "changeme", "placeholder"}:
        return False
    return len(text) >= 8


def is_auth_error_payload(data: Any, status_code: Optional[int] = None) -> bool:
    """识别提供源返回的鉴权失败（含 HTTP 200 + {'detail': 'Invalid API key'}）。"""
    if status_code in {401, 403}:
        return True
    if not isinstance(data, dict):
        return False
    blobs = [
        data.get("detail"),
        data.get("message"),
        data.get("error"),
        data.get("msg"),
    ]
    text = " ".join(str(item) for item in blobs if item is not None).lower()
    if not text:
        return False
    if any(marker in text for marker in AUTH_ERROR_MARKERS):
        return True
    if "api key" in text and "invalid" in text:
        return True
    return False


def describe_auth_error(provider: str, data: Any) -> str:
    peer = "REGHelp" if provider == "antisafety" else "AntiSafety"
    return (
        f"{provider} API Key 无效 ({data})。"
        f"请确认使用的是 config.{provider}_api_key 与 config.{provider}_base_urls，"
        f"不要把 {peer} 的 Key 或网关地址混入本提供源"
    )
