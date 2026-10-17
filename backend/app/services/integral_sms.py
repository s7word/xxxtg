"""Logistica Integral 私人接码：SMS-Activate 子集，仅 Telegram / 阿根廷。

  GET https://api.logisticaintegralsrl.com/stubs/handler_api.php
    ?api_key={key}&action={getBalance|getNumber|getStatus}

文档要点：
  - service=tg，country=39（阿根廷）
  - 也可用请求头 X-Api-Key
  - getNumber 预扣余额；getStatus 收到码后才真正扣费
  - 20 分钟无码 → STATUS_CANCEL，预扣退回，号码仍归账户
  - 不允许取消：setStatus=8 → EARLY_CANCEL_DENIED，预约保持
  - 无 getPrices；库存发现不得调用 getNumber
  - 可选 webhook：POST JSON，X-Signature = HMAC-SHA256(raw body)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import threading
from typing import Any, Callable, Dict, Optional, Tuple, Union

from backend.app.services.grizzlysms import (
    GrizzlySmsError,
    GrizzlySmsService,
    InsufficientBalanceError,
    _ACCESS_NUMBER_RE,
    _extract_sms_code,
    _is_no_balance,
    _is_no_numbers,
    _normalize_phone,
    _parse_maybe_json,
)
from backend.app.services.vaksms import NoNumberAvailableError

logger = logging.getLogger("IntegralSmsService")

BASE_URL = "https://api.logisticaintegralsrl.com/stubs/handler_api.php"
PROVIDER_NAME = "integral"
PROVIDER_LABEL = "Logistica Integral (Argentina TG)"
DEFAULT_SERVICE = "tg"
COUNTRY_ID = 39
COUNTRY_ISO2 = "ar"
HOLD_SECONDS = 20 * 60
EARLY_CANCEL_DENIED = "EARLY_CANCEL_DENIED"

_AR_TOKENS = frozenset({
    "ar",
    "arg",
    "argentina",
    "argentinian",
    "阿根廷",
    str(COUNTRY_ID),
    "+54",
    "54",
})

_pending_lock = threading.Lock()
_pending_ids: set[str] = set()
_pending_codes: Dict[str, str] = {}


def is_argentina(country: Union[str, int, None]) -> bool:
    if country is None or str(country).strip() == "":
        return True
    token = str(country).strip().lower().replace("_", " ").replace("-", " ")
    compact = token.replace(" ", "")
    if compact in _AR_TOKENS or token in _AR_TOKENS:
        return True
    if compact.startswith("+") and compact.lstrip("+") == "54":
        return True
    return False


def remember_activation(act_id: str) -> None:
    token = str(act_id or "").strip()
    if not token:
        return
    with _pending_lock:
        _pending_ids.add(token)


def forget_activation(act_id: str) -> None:
    token = str(act_id or "").strip()
    if not token:
        return
    with _pending_lock:
        _pending_ids.discard(token)
        _pending_codes.pop(token, None)


def pop_webhook_code(act_id: str) -> Optional[str]:
    token = str(act_id or "").strip()
    if not token:
        return None
    with _pending_lock:
        return _pending_codes.pop(token, None)


def store_webhook_code(act_id: str, code: str) -> None:
    token = str(act_id or "").strip()
    sms = str(code or "").strip()
    if not token or not sms:
        return
    with _pending_lock:
        _pending_codes[token] = sms
        _pending_ids.add(token)


def has_pending_activation(act_id: str) -> bool:
    token = str(act_id or "").strip()
    if not token:
        return False
    with _pending_lock:
        return token in _pending_ids or token in _pending_codes


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    key = (secret or "").encode("utf-8")
    if not key:
        return False
    expected = hmac.new(key, raw_body or b"", hashlib.sha256).hexdigest()
    got = str(signature or "").strip()
    if got.lower().startswith("sha256="):
        got = got.split("=", 1)[1].strip()
    if not got:
        return False
    return hmac.compare_digest(expected.lower(), got.lower())


def ingest_webhook_payload(
    payload: Any,
    *,
    raw_body: bytes = b"",
    signature: str = "",
    secret: str = "",
) -> Dict[str, Any]:
    """解析并缓存 webhook 验证码。secret 非空时强制校验 X-Signature。"""
    if secret:
        if not verify_webhook_signature(raw_body, signature, secret):
            raise GrizzlySmsError("Integral webhook 签名无效")
    if not isinstance(payload, dict):
        raise GrizzlySmsError("Integral webhook 体必须是 JSON 对象")
    act_id = payload.get("activation_id")
    if act_id is None:
        act_id = payload.get("id") or payload.get("activationId")
    act_id = str(act_id or "").strip()
    code = str(payload.get("code") or payload.get("sms") or "").strip()
    if not act_id or not code:
        raise GrizzlySmsError("Integral webhook 缺少 activation_id 或 code")
    if not secret and not has_pending_activation(act_id):
        raise GrizzlySmsError("Integral webhook 无签名且 activation_id 不在预约中")
    store_webhook_code(act_id, code)
    return {
        "success": True,
        "activation_id": act_id,
        "phone": str(payload.get("phone") or ""),
        "service": str(payload.get("service") or DEFAULT_SERVICE),
        "code": code,
    }


def reset_webhook_state() -> None:
    with _pending_lock:
        _pending_ids.clear()
        _pending_codes.clear()


class IntegralSmsService(GrizzlySmsService):
    BASE_URL = BASE_URL
    PROVIDER_NAME = PROVIDER_NAME
    PROVIDER_LABEL = PROVIDER_LABEL
    COUNTRY_ID = COUNTRY_ID
    COUNTRY_ISO2 = COUNTRY_ISO2

    def __init__(self, api_key: str = "", timeout: float = 30.0, client=None):
        key = (api_key or "").strip() or (os.environ.get("INTEGRAL_SMS_API_KEY") or "").strip()
        super().__init__(key, timeout=timeout, client=client)

    async def _get(self, action: str, extra: Optional[Dict[str, Any]] = None) -> str:
        if not self.api_key:
            raise GrizzlySmsError(f"未配置 {self.PROVIDER_LABEL} API Key")
        params: Dict[str, Any] = {"api_key": self.api_key, "action": action}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        headers = {"X-Api-Key": self.api_key}
        resp = await self.client.get(self.BASE_URL, params=params, headers=headers)
        text = (resp.text or "").strip()
        if resp.status_code >= 400:
            raise GrizzlySmsError(f"{self.PROVIDER_LABEL} HTTP {resp.status_code}: {text[:300]}")
        from backend.app.services.grizzlysms import _is_bad_key
        if _is_bad_key(text):
            raise GrizzlySmsError(f"{self.PROVIDER_LABEL} API Key 无效 (BAD_KEY): {text}")
        return text

    def _require_argentina(self, country: Union[str, int, None]) -> int:
        if not is_argentina(country):
            raise GrizzlySmsError(
                f"{self.PROVIDER_LABEL} 仅支持阿根廷 Telegram（country=39 / ar），收到: {country}"
            )
        return COUNTRY_ID

    async def get_prices(
        self,
        country: Union[str, int, None] = None,
        service: str = DEFAULT_SERVICE,
    ) -> Any:
        """平台无 getPrices。余额探测成功后仅返回阿根廷占位库存，绝不 getNumber。"""
        if country is not None and str(country).strip() != "" and not is_argentina(country):
            return {}
        balance = await self.get_balance()
        count = 1 if float(balance or 0) > 0 else 0
        svc = service or DEFAULT_SERVICE
        return {str(COUNTRY_ID): {svc: {"cost": 0.0, "count": count}}}

    async def get_all_prices(self, service: str = DEFAULT_SERVICE) -> Any:
        return await self.get_prices(country=COUNTRY_ISO2, service=service)

    async def get_stock_count(
        self,
        country: Union[str, int] = COUNTRY_ISO2,
        service: str = DEFAULT_SERVICE,
    ) -> int:
        if not is_argentina(country):
            return 0
        data = await self.get_prices(country=COUNTRY_ID, service=service)
        return self._stock_from_prices(data, COUNTRY_ID, service or DEFAULT_SERVICE)

    async def get_number(
        self,
        country: Union[str, int] = COUNTRY_ISO2,
        service: str = DEFAULT_SERVICE,
        operator: Optional[str] = None,
        provider_ids: Optional[Union[str, list]] = None,
        max_price: Optional[float] = None,
    ) -> Tuple[str, str]:
        self._require_argentina(country)
        _ = operator, provider_ids, max_price
        extra = {"service": DEFAULT_SERVICE, "country": COUNTRY_ID}
        logger.info(
            "向 %s 申请阿根廷 Telegram 号 (country=%s, service=tg)...",
            self.PROVIDER_LABEL,
            COUNTRY_ID,
        )
        raw = await self._get("getNumber", extra)
        if _is_no_numbers(raw):
            raise NoNumberAvailableError(COUNTRY_ISO2, raw)
        if _is_no_balance(raw):
            raise InsufficientBalanceError(raw)
        match = _ACCESS_NUMBER_RE.match(raw)
        if match:
            act_id = (match.group(1) or "").strip()
            phone = _normalize_phone(match.group(2) or "")
            if act_id and phone:
                remember_activation(act_id)
                return act_id, phone
        parsed = _parse_maybe_json(raw)
        if isinstance(parsed, dict):
            error = parsed.get("error") or parsed.get("status") or parsed.get("msg")
            if _is_no_numbers(error) or _is_no_numbers(parsed):
                raise NoNumberAvailableError(COUNTRY_ISO2, parsed)
            if _is_no_balance(error):
                raise InsufficientBalanceError(parsed)
            act_id = parsed.get("id") or parsed.get("activationId") or parsed.get("activation_id")
            phone = parsed.get("phone") or parsed.get("number") or parsed.get("tel")
            if act_id and phone:
                remember_activation(str(act_id))
                return str(act_id), _normalize_phone(str(phone))
        raise GrizzlySmsError(f"{self.PROVIDER_LABEL} 租号返回非预期格式: {raw}")

    async def notify_ready(self, act_id: str) -> str:
        _ = act_id
        return ""

    async def set_status(self, act_id: str, status: int) -> str:
        if int(status) == 8:
            logger.info(
                "%s 不允许取消，预约保持至 20 分钟无码自动退回余额 act_id=%s",
                self.PROVIDER_LABEL,
                act_id,
            )
            return EARLY_CANCEL_DENIED
        logger.info(
            "%s 跳过 setStatus=%s（文档未提供完成/就绪状态）act_id=%s",
            self.PROVIDER_LABEL,
            status,
            act_id,
        )
        return ""

    async def finish(self, act_id: str) -> Dict[str, Any]:
        forget_activation(act_id)
        return {
            "success": True,
            "skipped": True,
            "reason": "finish_unsupported",
            "act_id": act_id,
            "status": 6,
            "data": "",
        }

    async def cancel(self, act_id: str) -> Dict[str, Any]:
        """失败路径仍会调用 cancel；平台拒绝提前取消，预约保持。"""
        if not act_id:
            return {"success": False, "skipped": True, "reason": "missing_act_id", "status": 8}
        raw = await self.set_status(act_id, 8)
        return {
            "success": False,
            "skipped": False,
            "reason": "early_cancel_denied",
            "act_id": act_id,
            "status": 8,
            "data": raw or EARLY_CANCEL_DENIED,
            "error": EARLY_CANCEL_DENIED,
        }

    async def wait_for_code(
        self,
        act_id: str,
        max_attempts: int = 300,
        interval: float = 4.0,
        log_callback: Optional[Callable] = None,
        notify_ready: bool = False,
    ) -> str:
        if not act_id:
            raise GrizzlySmsError("缺少 activation_id，无法轮询验证码")
        remember_activation(act_id)
        _ = notify_ready
        for attempt in range(1, max_attempts + 1):
            cached = pop_webhook_code(act_id)
            if cached:
                forget_activation(act_id)
                return cached
            await asyncio.sleep(interval)
            if log_callback:
                await log_callback(
                    f"正在轮询 {self.PROVIDER_LABEL} 验证码 (第 {attempt}/{max_attempts} 次)..."
                )
            cached = pop_webhook_code(act_id)
            if cached:
                forget_activation(act_id)
                return cached
            try:
                raw = await self._get("getStatus", {"id": act_id})
            except Exception as exc:
                logger.warning("轮询 %s 验证码异常: %s", self.PROVIDER_LABEL, exc)
                continue
            upper = (raw or "").strip().upper()
            if upper.startswith("STATUS_OK"):
                code = _extract_sms_code(raw)
                if code:
                    forget_activation(act_id)
                    return code
            if upper.startswith("STATUS_CANCEL"):
                forget_activation(act_id)
                raise GrizzlySmsError(
                    f"{self.PROVIDER_LABEL} 20 分钟无码，预扣已退回 (STATUS_CANCEL): {raw}"
                )
            if upper.startswith("STATUS_WAIT"):
                continue
            parsed = _parse_maybe_json(raw)
            if isinstance(parsed, dict):
                code = parsed.get("code") or parsed.get("smsCode") or parsed.get("sms")
                if code:
                    forget_activation(act_id)
                    return str(code)
        raise TimeoutError(f"等待 {self.PROVIDER_LABEL} 验证码超时 (已达最大重试轮次)")
