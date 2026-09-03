import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import httpx

from backend.app.services.attestation_urls import (
    DEFAULT_REGHELP_BASES,
    describe_auth_error,
    is_auth_error_payload,
    sanitize_provider_urls,
)
from backend.app.services.net_utils import create_httpx_client

logger = logging.getLogger("RegHelpService")

# REGHelp /push/setStatus 允许的枚举值。仅在对应 getToken 请求携带了有效 ref 时生效，
# 平台会在窗口期内（官方文档描述约 60~180 秒）对已标记为无效的 Push Token 触发自动退款审计。
PUSH_STATUS_VALUES = frozenset({"NOSMS", "FLOOD", "BANNED", "2FA"})
PUSH_REFUND_MIN_SECONDS = 60.0
PUSH_REFUND_WINDOW_SECONDS = 180.0
PUSH_REF_MAX_LENGTH = 50
PUSH_REFUND_REJECT_DETAILS = frozenset({
    "NOT_FOUND",
    "TASK_NOT_FOUND",
    "INVALID_PARAM",
    "MISSING_PARAM",
    "SERVICE_DISABLED",
    "RATE_LIMIT",
})

# 内部失败原因 -> REGHelp setStatus 枚举映射表 (与 registrar.py 中 _refund_and_revoke_channel /
# 各异常分支使用的同一套内部原因标识保持一致，避免退款审计与接码平台自动退订语义割裂)：
#
#   内部失败原因 (reason)                          REGHelp setStatus
#   ----------------------------------------------  -----------------
#   PHONE_NUMBER_BANNED (Telegram RPC 封禁)          BANNED
#   PHONE_PREAUDIT_BANNED (AntiSafety 历史审计封禁)   BANNED
#   LOCAL_BANNED_PHONE_CACHE (本地封禁库命中)         BANNED
#   FLOOD_WAIT (PhoneNumberFloodError/FloodWaitError) FLOOD
#   NO_CODE (等待带外验证码超时)                       NOSMS
#   SENT_CODE_TYPE_APP (验证码下发到已登录客户端)      NOSMS
#   API_ID_PUBLISHED_FLOOD (sendCode 因 Token 无效失败) NOSMS
#   RECAPTCHA_CHECK (人机挑战未突破，未收到短信)         NOSMS
#   EXCEPTION (引导异常且未完成短信验证)                 NOSMS
#   existing_2fa (旧号已启用 2FA，SignIn 即完成)       2FA
#
# WRONG_CODE 表示带外短信已到达，Push Token 本身有效，不触发 setStatus。
PUSH_REFUND_REASON_MAP: Dict[str, str] = {
    "PHONE_NUMBER_BANNED": "BANNED",
    "PHONE_PREAUDIT_BANNED": "BANNED",
    "LOCAL_BANNED_PHONE_CACHE": "BANNED",
    "FLOOD_WAIT": "FLOOD",
    "NO_CODE": "NOSMS",
    "SENT_CODE_TYPE_APP": "NOSMS",
    "PAYMENT_REQUIRED_OFFICIAL_ONLY": "NOSMS",
    "EMAIL_SETUP_FAILED": "NOSMS",
    "EMAIL_CODE_UNAVAILABLE": "NOSMS",
    "FIREBASE_SMS_FAILED": "NOSMS",
    "API_ID_PUBLISHED_FLOOD": "NOSMS",
    "RECAPTCHA_CHECK": "NOSMS",
    "EXCEPTION": "NOSMS",
    "existing_2fa": "2FA",
}


@dataclass
class PushTokenResult:
    """`get_push_token` 成功时的返回结构，携带退款闭环所需的 task_id/provider/时间戳。"""

    token: Optional[str]
    task_id: Optional[str]
    provider: str = "reghelp"
    obtained_at: Optional[float] = None

    def __bool__(self) -> bool:
        return bool(self.token)


@dataclass
class EmailInboxResult:
    """REGHelp `/email/getEmail` + `/email/getStatus` 的临时邮箱结果。"""

    email: Optional[str]
    task_id: Optional[str]
    code: Optional[str] = None
    email_type: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.email)


class RegHelpService:
    """REGHelp (reghelp.net) 高可用 Attestation / Push 凭证生成客户端

    对接 REGHelp 官方 Key API (参考开源客户端 https://github.com/REGHELPNET/reghelp_client
    与官方接口文档 https://reghelp.net/en/api-docs/)。协议规范如下 —— 全部为 GET 请求，
    鉴权与业务参数均通过查询字符串传递：

        GET /balance              {apiKey}
            -> {"balance": 123.25, "currency": "RUB", "status": "success"}

        GET /push/getToken        {apiKey, appName, appDevice(Android/iOS),
                                    [appVersion], [appBuild], [ref], [webHook]}
            -> {"id": "...", "service": "tg", "product": "push",
                "price": 0.75, "balance": 122.5, "status": "success"}

        GET /push/getStatus       {apiKey, id}
            -> {"id": "...", "status": "wait|pending|done|error",
                "token": "...", "message": "..."}

        GET /push/setStatus       {apiKey, id, number, status(NOSMS/FLOOD/BANNED/2FA)}
            开发者专用：标记任务无效并触发自动退款审计 (仅在 getToken 时携带了有效 ref 才可用)

        GET /email/getEmail       {apiKey, appName, appDevice, phone, type(icloud/gmail),
                                    [ref], [webHook]}
        GET /email/getStatus      {apiKey, id}
            -> {"id": "...", "status": "wait|pending|done|error",
                "email": "...", "code": "..."}

        GET /integrity/getToken   {apiKey, appName, appDevice, nonce,
                                    appVersionCode, [type=std|classic], [ref], [webHook]}
        GET /integrity/getStatus  {apiKey, id}

        GET /RecaptchaMobile/getToken
            {apiKey, appName, appKey(site_key), appAction, appDevice,
             [proxyType], [proxyAddress], [proxyPort], [proxyLogin], [proxyPassword]}
        GET /RecaptchaMobile/getStatus  {apiKey, id}

    与 `AntiSafetyService` 保持一致的多候选网关容灾风格，但使用 REGHelp 独立的 API Key 与
    协议字段 —— 无需 `aid`，`appName`/`appDevice` 与项目内置的 `DeviceProfileManager` 模板
    天然对齐 (telegram_android/telegram_9 -> appName=tg, telegram_x -> appName=tg_x)。
    RecaptchaMobile 对 Telegram Android 使用 appName=org.telegram.messenger。

    注：REGHelp 官方接口目前未提供与 AntiSafety `/check` 等价的号码历史安全审计能力，
    该职责仍由 `AttestationGatewayService` 路由至 AntiSafety 处理。
    """

    DEFAULT_API_BASES = list(DEFAULT_REGHELP_BASES)

    # REGHelp appName 与项目内置端点模板的 app_device 大小写对齐 (Android / iOS)
    _DEVICE_ALIASES = {"android": "Android", "ios": "iOS"}

    def __init__(
        self,
        api_key: str,
        proxy: Optional[Dict[str, Any]] = None,
        api_bases: Optional[List[str]] = None,
        connect_timeout: float = 6.0,
        total_timeout: float = 20.0
    ):
        self.api_key = api_key
        self.api_bases = sanitize_provider_urls(api_bases or self.DEFAULT_API_BASES, "reghelp", self.DEFAULT_API_BASES)
        self.client = create_httpx_client(proxy=proxy, connect_timeout=connect_timeout, total_timeout=total_timeout)
        self._owns_client = True
        self._last_good_api_base: Optional[str] = None

    async def __aenter__(self) -> "RegHelpService":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        try:
            if self._owns_client and self.client:
                await self.client.aclose()
        except Exception:
            pass

    def _normalize_device(self, app_device: str) -> str:
        return self._DEVICE_ALIASES.get(str(app_device or "Android").lower(), app_device or "Android")

    async def _get_with_fallback(
        self,
        path: str,
        params: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None
    ) -> Tuple[str, Any, int]:
        """按序尝试候选网关地址，任一成功即返回 (base, payload, http_status)，全部失败则汇总错误抛出"""
        clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
        errors = []
        ordered = self.api_bases
        if self._last_good_api_base and self._last_good_api_base in self.api_bases:
            ordered = [self._last_good_api_base] + [b for b in self.api_bases if b != self._last_good_api_base]

        for base in ordered:
            try:
                resp = await self.client.get(f"{base}{path}", params=clean_params, headers=headers)
                data = resp.json()
                http_status = int(getattr(resp, "status_code", 0) or 0)
                if is_auth_error_payload(data, http_status or None):
                    errors.append(f"{base} -> {describe_auth_error('reghelp', data)}")
                    logger.warning("REGHelp 候选网关 %s%s 鉴权失败，尝试下一候选: %s", base, path, data)
                    continue
                self._last_good_api_base = base
                return base, data, http_status
            except Exception as e:
                errors.append(f"{base} -> {e}")
                logger.warning(f"REGHelp 候选网关 {base}{path} 请求失败，尝试下一候选: {e}")

        raise RuntimeError("所有 REGHelp 候选网关均不可达 (" + "; ".join(errors) + ")")

    async def get_balance(self) -> Dict[str, Any]:
        """查询 REGHelp 账户当前计费余额，同时用作鉴权与连通性诊断探针"""
        _, data, _ = await self._get_with_fallback("/balance", {"apiKey": self.api_key})
        return data

    query_account_balance = get_balance

    async def get_push_token(
        self,
        profile: Dict[str, Any],
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Optional[PushTokenResult]:
        """向 REGHelp Key API 请求平台推送握手凭证 (Push Token)

        `ref` 应传入调用方稳定标识 (推荐使用注册任务 task_id，≤50 字符)：仅当创建任务时
        携带了有效 ref，之后才能通过 `set_push_status`/`refund_push_token` 触发自动退款审计，
        否则平台会静默忽略 setStatus 请求。未提供 ref 时仍会正常发起任务 (不影响本次取号)，
        但会记录一条日志提示退款闭环不可用。

        成功时返回 `PushTokenResult(token, task_id, provider="reghelp", obtained_at=<monotonic>)`；
        `task_id` 与 `obtained_at` 供调用方在失败/退订分支据此调用 `set_push_status`/
        `refund_push_token`（`obtained_at` 用于自行判断是否已超出退款窗口）。
        """
        if ref is not None:
            ref = str(ref).strip()[:PUSH_REF_MAX_LENGTH] or None
        if not ref:
            logger.warning(
                "REGHelp Push Token 请求未携带 ref，本次任务事后无法通过 setStatus 触发自动退款审计"
            )

        app_device = self._normalize_device(profile.get("app_device", "Android"))
        params = {
            "apiKey": self.api_key,
            "appName": profile.get("app_name", "tg"),
            "appDevice": app_device,
            "appVersion": profile.get("app_version_pure"),
            "appBuild": profile.get("app_build"),
            "ref": ref,
            "webHook": webhook
        }
        headers = {"Idempotency-Key": request_id} if request_id else None
        if log_callback:
            await log_callback(
                f"向 REGHelp 网关发起 Push Token 生成任务 (App: {params['appName']}/{app_device}, "
                f"候选网关: {', '.join(self.api_bases)}, ref={ref or '未提供'})..."
            )

        try:
            used_base, data, _ = await self._get_with_fallback("/push/getToken", params, headers=headers)
        except Exception as req_err:
            raise RuntimeError(f"连接 REGHelp 网关失败 (已尝试 {', '.join(self.api_bases)}): {req_err}")

        if is_auth_error_payload(data):
            raise RuntimeError(describe_auth_error("reghelp", data))

        if data.get("status") == "error":
            raise RuntimeError(f"REGHelp Push Token 任务创建失败: {data.get('detail') or data.get('message') or data}")

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"REGHelp Push Token 任务创建返回异常: {data}")

        if log_callback:
            price_info = f" (计费: {data.get('price')}, 余额: {data.get('balance')})" if data.get("price") is not None else ""
            await log_callback(f"REGHelp 任务已创建 (id={task_id}){price_info}")
            if used_base != self.api_bases[0]:
                await log_callback(f"REGHelp 主用网关不可达，已自动切换至备用地址: {used_base}")

        for attempt in range(1, 61):
            await asyncio.sleep(2.0)
            if log_callback and attempt % 3 == 0:
                await log_callback(f"等待 REGHelp 云端签发 Push Token ({attempt * 2}s)...")

            check_resp = await self.client.get(f"{used_base}/push/getStatus", params={
                "apiKey": self.api_key,
                "id": task_id
            })
            res = check_resp.json()
            status = res.get("status")
            if status == "done":
                return PushTokenResult(
                    token=res.get("token"),
                    task_id=task_id,
                    provider="reghelp",
                    obtained_at=time.monotonic(),
                )
            if status == "error":
                raise RuntimeError(f"REGHelp Push Token 签署失败: {res.get('message')}")

        raise TimeoutError("REGHelp Push Token 获取超时 (超过最大轮询阈值)")

    request_push_token = get_push_token

    async def set_push_status(self, task_id: str, number: str, status: str) -> Tuple[bool, Any]:
        """标记一次已获取的 Push Token 为无效 (NOSMS/FLOOD/BANNED/2FA)，触发平台自动退款审计

        仅在对应 getToken 请求携带了有效且已启用的 ref 时可用，否则平台会静默忽略。
        返回 (accepted, payload)：accepted 仅在平台明确受理时为 True；
        `{'detail': 'NOT_FOUND'}` 等拒绝回包不再被当成成功。
        网络/平台异常只记录 warning，绝不向上抛出。
        """
        if not task_id:
            logger.warning("REGHelp Push 状态回写跳过：缺少 task_id")
            return False, None
        normalized_status = str(status or "").strip().upper()
        if normalized_status not in PUSH_STATUS_VALUES:
            logger.warning(
                "REGHelp Push 状态回写收到未知 status=%s（允许值: %s），仍按原样提交",
                status, ", ".join(sorted(PUSH_STATUS_VALUES)),
            )
        try:
            _, data, http_status = await self._get_with_fallback("/push/setStatus", {
                "apiKey": self.api_key,
                "id": task_id,
                "number": number,
                "status": status
            })
            accepted, verdict = self.interpret_set_status_response(data, http_status)
            if accepted:
                logger.info(
                    "REGHelp setStatus 提交成功 id=%s status=%s http=%s payload=%s",
                    task_id, status, http_status, data,
                )
            else:
                logger.warning(
                    "REGHelp setStatus 平台拒绝 id=%s status=%s http=%s verdict=%s payload=%s",
                    task_id, status, http_status, verdict, data,
                )
            return accepted, data
        except Exception as e:
            logger.warning(f"REGHelp Push 状态回写异常 (id={task_id}, status={status}): {e}")
            return False, None

    @staticmethod
    def interpret_set_status_response(data: Any, http_status: Optional[int] = None) -> Tuple[bool, str]:
        """区分 setStatus 提交成功与平台拒绝。官方成功回包为 status=success；
        带 balance 的 status=error 视为已退过（官方客户端同样当作成功）。
        """
        payload = data if isinstance(data, dict) else {}
        raw_detail = payload.get("detail") or payload.get("id") or payload.get("message") or ""
        detail = str(raw_detail).strip()
        code = detail.upper().replace(" ", "_")
        status = str(payload.get("status") or "").strip().lower()
        http_status = int(http_status or 0)

        if http_status in {401, 403}:
            return False, f"鉴权失败 http={http_status}"
        if code in PUSH_REFUND_REJECT_DETAILS or http_status == 404:
            return False, f"平台拒绝: {detail or code or f'http={http_status}'}"
        if status == "success":
            return True, "平台已受理"
        if status == "error" and "balance" in payload:
            return True, f"平台已受理(error+balance id={payload.get('id') or '-'})"
        if status == "error":
            return False, f"平台拒绝: {detail or payload}"
        if http_status >= 400:
            return False, f"平台拒绝: http={http_status} {payload or detail}"
        if payload:
            return False, f"平台拒绝: 无法确认回包 {payload}"
        return False, "平台拒绝: 空回包"

    @staticmethod
    def resolve_refund_status(reason: str) -> Optional[str]:
        """将内部失败原因标识映射为 REGHelp setStatus 枚举，未收录的原因返回 None。"""
        return PUSH_REFUND_REASON_MAP.get(str(reason or "").strip())

    async def refund_push_token(
        self,
        task_id: Optional[str],
        phone: Optional[str],
        reason: str,
        log_callback=None,
    ) -> Optional[str]:
        """按内部失败原因自动映射并回写 setStatus，返回实际提交的状态；未匹配/无 task_id 则跳过并返回 None。

        本方法自身不做退款窗口 (60~180s) 判断。调用方会等到满 60s 再提交；若已超窗仍会尝试
        setStatus，由平台决定是否受理。任务日志区分「提交成功」与「平台拒绝」。
        """
        if not task_id:
            return None
        status = self.resolve_refund_status(reason)
        if not status:
            if log_callback:
                await log_callback(
                    f"⚠️ [REGHelp 退款] 原因 {reason or '-'} 未映射到 NOSMS/FLOOD/BANNED/2FA，"
                    f"跳过 setStatus id={task_id}"
                )
            return None
        accepted, payload = await self.set_push_status(task_id, phone or "", status)
        extra = f" resp={payload}" if payload is not None else ""
        if log_callback:
            if accepted:
                await log_callback(
                    f"[REGHelp 退款] 提交成功 setStatus id={task_id} status={status} "
                    f"act_id={phone or '-'}{extra}"
                )
            else:
                _, verdict = self.interpret_set_status_response(payload)
                await log_callback(
                    f"⚠️ [REGHelp 退款] 平台拒绝 setStatus id={task_id} status={status} "
                    f"act_id={phone or '-'} {verdict}{extra}"
                )
        return status if accepted else None

    async def get_integrity_token(
        self,
        profile: Dict[str, Any],
        nonce: str,
        app_version_code: int,
        token_type: str = "classic",
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Optional[str]:
        """向 REGHelp Key API 请求 Google Play Integrity 凭证 (Classic / Standard 两种流程)"""
        app_device = self._normalize_device(profile.get("app_device", "Android"))
        normalized_type = str(token_type or "classic").lower()
        params = {
            "apiKey": self.api_key,
            "appName": profile.get("app_name", "tg"),
            "appDevice": app_device,
            "nonce": nonce,
            "appVersionCode": app_version_code,
            "type": "std" if normalized_type in ("std", "standard", "express") else None,
            "ref": ref,
            "webHook": webhook
        }
        headers = {"Idempotency-Key": request_id} if request_id else None
        if log_callback:
            await log_callback(f"向 REGHelp 网关发起 Play Integrity 凭证生成任务 (App: {params['appName']}/{app_device})...")

        used_base, data, _ = await self._get_with_fallback("/integrity/getToken", params, headers=headers)
        if data.get("status") == "error":
            raise RuntimeError(f"REGHelp Integrity 任务创建失败: {data.get('detail') or data.get('message') or data}")

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"REGHelp Integrity 任务创建返回异常: {data}")

        for attempt in range(1, 31):
            await asyncio.sleep(1.5)
            if log_callback and attempt % 4 == 0:
                await log_callback(f"等待 REGHelp 签发 Play Integrity 凭证 ({attempt * 1.5:.0f}s)...")

            check_resp = await self.client.get(f"{used_base}/integrity/getStatus", params={
                "apiKey": self.api_key,
                "id": task_id
            })
            res = check_resp.json()
            status = res.get("status")
            if status == "done":
                return res.get("token")
            if status == "error":
                raise RuntimeError(f"REGHelp Integrity 凭证签署失败: {res.get('message')}")

        raise TimeoutError("REGHelp Integrity 凭证获取超时 (超过最大轮询阈值)")

    request_integrity_token = get_integrity_token

    async def get_login_email(
        self,
        profile: Dict[str, Any],
        phone: str,
        email_type: str = "gmail",
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> EmailInboxResult:
        """向 REGHelp 申请临时邮箱（SetUpEmailRequired 登录邮箱）。

        对应官方 Key API：
            GET /email/getEmail
            GET /email/getStatus
        成功时至少返回 `email`；验证码可能稍后才出现在 getStatus 的 `code` 字段。
        """
        normalized_type = str(email_type or "gmail").strip().lower()
        if normalized_type not in {"icloud", "gmail"}:
            normalized_type = "gmail"
        app_device = self._normalize_device(profile.get("app_device", "Android"))
        e164 = str(phone or "").strip()
        if e164 and not e164.startswith("+"):
            e164 = f"+{e164}"
        params = {
            "apiKey": self.api_key,
            "appName": profile.get("app_name", "tg"),
            "appDevice": app_device,
            "phone": e164,
            "type": normalized_type,
            "ref": ref,
            "webHook": webhook,
        }
        headers = {"Idempotency-Key": request_id} if request_id else None
        if log_callback:
            await log_callback(
                f"向 REGHelp 申请临时邮箱 (type={normalized_type}, "
                f"App: {params['appName']}/{app_device})..."
            )

        used_base, data, _ = await self._get_with_fallback("/email/getEmail", params, headers=headers)
        if is_auth_error_payload(data):
            raise RuntimeError(describe_auth_error("reghelp", data))
        if data.get("status") == "error":
            raise RuntimeError(
                f"REGHelp Email 任务创建失败: {data.get('detail') or data.get('message') or data}"
            )
        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"REGHelp Email 任务创建返回异常: {data}")

        email = data.get("email")
        if log_callback:
            price_info = ""
            if data.get("price") is not None:
                price_info = f" (计费: {data.get('price')}, 余额: {data.get('balance')})"
            await log_callback(f"REGHelp Email 任务已创建 (id={task_id}){price_info}")

        if email:
            return EmailInboxResult(
                email=email, task_id=task_id, code=data.get("code"), email_type=normalized_type
            )

        for attempt in range(1, 31):
            await asyncio.sleep(2.0)
            if log_callback and attempt % 3 == 0:
                await log_callback(f"等待 REGHelp 分配临时邮箱 ({attempt * 2}s)...")
            check_resp = await self.client.get(
                f"{used_base}/email/getStatus",
                params={"apiKey": self.api_key, "id": task_id},
            )
            res = check_resp.json()
            status = str(res.get("status") or "").lower()
            if res.get("email"):
                return EmailInboxResult(
                    email=res.get("email"),
                    task_id=task_id,
                    code=res.get("code"),
                    email_type=normalized_type,
                )
            if status == "error":
                raise RuntimeError(
                    f"REGHelp Email 分配失败: {res.get('message') or res.get('detail') or res}"
                )

        raise TimeoutError("REGHelp Email 分配超时 (超过最大轮询阈值)")

    get_email = get_login_email

    async def poll_email_code(
        self,
        task_id: str,
        log_callback=None,
        max_attempts: int = 45,
        interval_sec: float = 2.0,
    ) -> Optional[str]:
        """轮询 `/email/getStatus` 直到返回验证码 `code`。"""
        if not task_id:
            raise RuntimeError("REGHelp Email 轮询缺少 task_id")
        used_base = self._last_good_api_base or (self.api_bases[0] if self.api_bases else None)
        if not used_base:
            raise RuntimeError("REGHelp Email 轮询缺少可用网关")

        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval_sec)
            if log_callback and attempt % 4 == 0:
                await log_callback(
                    f"等待 REGHelp Email 验证码 ({attempt * interval_sec:.0f}s)..."
                )
            check_resp = await self.client.get(
                f"{used_base}/email/getStatus",
                params={"apiKey": self.api_key, "id": task_id},
            )
            res = check_resp.json()
            status = str(res.get("status") or "").lower()
            code = res.get("code") or res.get("token")
            if code:
                return str(code).strip()
            if status == "error":
                raise RuntimeError(
                    f"REGHelp Email 取码失败: {res.get('message') or res.get('detail') or res}"
                )
        raise TimeoutError("REGHelp Email 验证码超时 (超过最大轮询阈值)")

    async def get_recaptcha_mobile_token(
        self,
        app_key: str,
        app_action: str = "signup",
        app_name: str = "org.telegram.messenger",
        app_device: str = "Android",
        proxy: Optional[Dict[str, Any]] = None,
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Optional[str]:
        """向 REGHelp RecaptchaMobile 申请人机验证 token，并轮询 getStatus 直到完成。

        对应官方 Key API：
            GET /RecaptchaMobile/getToken
            GET /RecaptchaMobile/getStatus
        """
        site_key = str(app_key or "").strip()
        action = str(app_action or "signup").strip() or "signup"
        if not site_key:
            raise RuntimeError("RecaptchaMobile 缺少 appKey / site_key")

        device = self._normalize_device(app_device)
        params: Dict[str, Any] = {
            "apiKey": self.api_key,
            "appName": app_name or "org.telegram.messenger",
            "appKey": site_key,
            "appAction": action,
            "appDevice": device,
            "ref": ref,
            "webHook": webhook,
        }
        if proxy and proxy.get("addr") and proxy.get("port"):
            proxy_type = str(proxy.get("proxy_type") or "socks5").lower()
            if proxy_type not in {"direct", "none", ""}:
                params["proxyType"] = "http" if proxy_type.startswith("http") else "socks5"
                params["proxyAddress"] = proxy.get("addr")
                params["proxyPort"] = int(proxy.get("port"))
                if proxy.get("username"):
                    params["proxyLogin"] = proxy.get("username")
                if proxy.get("password"):
                    params["proxyPassword"] = proxy.get("password")

        headers = {"Idempotency-Key": request_id} if request_id else None
        if log_callback:
            await log_callback(
                f"向 REGHelp RecaptchaMobile 发起解题任务 "
                f"(appName={params['appName']}, action={action}, "
                f"site_key={site_key[:12]}..., 候选网关: {', '.join(self.api_bases)})..."
            )

        try:
            used_base, data, _ = await self._get_with_fallback(
                "/RecaptchaMobile/getToken", params, headers=headers
            )
        except Exception as req_err:
            raise RuntimeError(
                f"连接 REGHelp RecaptchaMobile 失败 (已尝试 {', '.join(self.api_bases)}): {req_err}"
            )

        if is_auth_error_payload(data):
            raise RuntimeError(describe_auth_error("reghelp", data))

        if data.get("status") == "error":
            raise RuntimeError(
                f"REGHelp RecaptchaMobile 任务创建失败: {data.get('detail') or data.get('message') or data}"
            )

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"REGHelp RecaptchaMobile 任务创建返回异常: {data}")

        if log_callback:
            price_info = ""
            if data.get("price") is not None:
                price_info = f" (计费: {data.get('price')}, 余额: {data.get('balance')})"
            await log_callback(f"REGHelp RecaptchaMobile 任务已创建 (id={task_id}){price_info}")

        for attempt in range(1, 91):
            await asyncio.sleep(2.0)
            if log_callback and attempt % 4 == 0:
                await log_callback(f"等待 REGHelp RecaptchaMobile 解题 ({attempt * 2}s)...")

            check_resp = await self.client.get(
                f"{used_base}/RecaptchaMobile/getStatus",
                params={"apiKey": self.api_key, "id": task_id},
            )
            res = check_resp.json()
            status = str(res.get("status") or "").lower()
            if status == "done":
                token = res.get("token")
                if not token:
                    raise RuntimeError(f"REGHelp RecaptchaMobile 完成但未返回 token: {res}")
                return token
            if status == "error":
                raise RuntimeError(
                    f"REGHelp RecaptchaMobile 解题失败: {res.get('message') or res.get('detail') or res}"
                )

        raise TimeoutError("REGHelp RecaptchaMobile 解题超时 (超过最大轮询阈值)")

    # 官方客户端 / 任务书对齐别名
    RecaptchaMobile = get_recaptcha_mobile_token
    solve_recaptcha_mobile = get_recaptcha_mobile_token


# 学术规范别名，与 AntiSafetyService / AttestationProofService 命名风格保持一致
RegHelpGatewayService = RegHelpService
