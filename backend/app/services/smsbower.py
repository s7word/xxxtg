"""SMS Bower (smsbower.app) 接码 + 临时邮箱客户端。

接码协议与 SMS-Activate / Grizzly SMS 兼容：
  GET https://smsbower.page/stubs/handler_api.php
    ?api_key={key}&action={getBalance|getPrices|getNumber|getStatus|setStatus}

临时邮箱（SetUpEmailRequired 候补）：
  GET https://smsbower.page/api/mail/getActivation
  GET https://smsbower.page/api/mail/getCode
  GET https://smsbower.page/api/mail/setStatus
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from backend.app.services.grizzlysms import GrizzlySmsError, GrizzlySmsService
from backend.app.services.reghelp import EmailInboxResult

logger = logging.getLogger("SmsBowerService")

MAIL_API_BASE = "https://smsbower.page/api/mail"
MAIL_SERVICE_TG = "tg"
EMAIL_TYPE_TO_DOMAIN: Dict[str, str] = {
    "gmail": "gmail.com",
    "icloud": "gmail.com",
}


class SmsBowerEmailError(RuntimeError):
    """SMS Bower 临时邮箱 API 错误。"""


class SmsBowerService(GrizzlySmsService):
    BASE_URL = "https://smsbower.page/stubs/handler_api.php"
    PROVIDER_NAME = "smsbower"
    PROVIDER_LABEL = "SMS Bower (smsbower.app)"

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise GrizzlySmsError(f"未配置 {self.PROVIDER_LABEL} API Key")

    @staticmethod
    def _resolve_mail_domain(email_type: str) -> tuple[str, str]:
        normalized = str(email_type or "gmail").strip().lower()
        if normalized not in EMAIL_TYPE_TO_DOMAIN:
            normalized = "gmail"
        return normalized, EMAIL_TYPE_TO_DOMAIN[normalized]

    async def _mail_get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_api_key()
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        clean.setdefault("api_key", self.api_key)
        resp = await self.client.get(f"{MAIL_API_BASE}/{path}", params=clean)
        try:
            data = resp.json()
        except Exception as exc:
            raise SmsBowerEmailError(
                f"SMS Bower Email {path} 返回非 JSON (http={resp.status_code}): {resp.text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise SmsBowerEmailError(f"SMS Bower Email {path} 返回异常: {data!r}")
        return data

    async def get_login_email(
        self,
        profile: Dict[str, Any],
        phone: str,
        email_type: str = "gmail",
        log_callback=None,
        ref: Optional[str] = None,
        max_price: Optional[float] = None,
    ) -> EmailInboxResult:
        """订购临时邮箱，返回与 REGHelp 兼容的 EmailInboxResult。"""
        _ = profile, phone
        normalized_type, domain = self._resolve_mail_domain(email_type)
        params: Dict[str, Any] = {
            "service": MAIL_SERVICE_TG,
            "domain": domain,
            "ref": ref,
        }
        if max_price is not None:
            params["maxPrice"] = max_price
        if log_callback:
            await log_callback(
                f"向 SMS Bower 申请临时邮箱 (service={MAIL_SERVICE_TG}, domain={domain})..."
            )

        data = await self._mail_get("getActivation", params)
        if int(data.get("status") or 0) != 1:
            error = str(data.get("error") or data.get("message") or data)
            raise SmsBowerEmailError(f"SMS Bower Email 订购失败: {error}")

        mail = str(data.get("mail") or "").strip()
        mail_id = data.get("mailId")
        if not mail or mail_id is None:
            raise SmsBowerEmailError(f"SMS Bower Email 订购返回异常: {data}")

        task_id = str(mail_id)
        if log_callback:
            price_hint = ""
            if data.get("price") is not None:
                price_hint = f" (计费: {data.get('price')})"
            await log_callback(f"SMS Bower Email 已分配 (mailId={task_id}){price_hint}: {mail}")

        return EmailInboxResult(
            email=mail,
            task_id=task_id,
            code=data.get("code"),
            email_type=normalized_type,
        )

    async def poll_email_code(
        self,
        task_id: str,
        log_callback=None,
        max_attempts: int = 45,
        interval_sec: float = 2.0,
        confirm_on_success: bool = True,
    ) -> Optional[str]:
        """轮询 getCode 直到返回验证码；成功后可选 setStatus=3 确认扣费。"""
        if not task_id:
            raise SmsBowerEmailError("SMS Bower Email 轮询缺少 mailId")

        pending_markers = (
            "code has not been received",
            "not been received yet",
            "wait",
        )
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval_sec)
            if log_callback and attempt % 4 == 0:
                await log_callback(
                    f"等待 SMS Bower Email 验证码 ({attempt * interval_sec:.0f}s)..."
                )
            data = await self._mail_get("getCode", {"mailId": task_id})
            if int(data.get("status") or 0) == 1:
                code = str(data.get("code") or "").strip()
                if code:
                    if confirm_on_success:
                        try:
                            await self.confirm_email_activation(task_id)
                        except Exception as exc:
                            logger.warning("SMS Bower Email setStatus=3 失败 mailId=%s: %s", task_id, exc)
                    return code
            error = str(data.get("error") or "").strip()
            lowered = error.lower()
            if "canceled" in lowered or "cancelled" in lowered:
                raise SmsBowerEmailError(f"SMS Bower Email 激活已取消: {error or data}")
            if error and not any(marker in lowered for marker in pending_markers):
                raise SmsBowerEmailError(f"SMS Bower Email 取码失败: {error or data}")

        raise TimeoutError("SMS Bower Email 验证码超时 (超过最大轮询阈值)")

    async def confirm_email_activation(self, mail_id: str) -> bool:
        """收到验证码后 setStatus=3，确认激活并从保留余额扣费。"""
        data = await self._mail_get("setStatus", {"id": mail_id, "status": 3})
        return int(data.get("status") or 0) == 1

    async def cancel_email_activation(self, mail_id: str) -> bool:
        data = await self._mail_get("setStatus", {"id": mail_id, "status": 2})
        return int(data.get("status") or 0) == 1

    async def get_mail_prices(
        self,
        service: str = MAIL_SERVICE_TG,
        domain: str = "gmail.com",
    ) -> Dict[str, Any]:
        """查询邮箱库存与价格（探针/诊断用）。"""
        return await self._mail_get("getPriceRests", {"service": service, "domain": domain})


PROVIDER_NAME = SmsBowerService.PROVIDER_NAME
PROVIDER_LABEL = SmsBowerService.PROVIDER_LABEL
