import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple
import httpx

from backend.app.services.net_utils import create_httpx_client

logger = logging.getLogger("RegHelpService")


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

        GET /integrity/getToken   {apiKey, appName, appDevice, nonce,
                                    appVersionCode, [type=std|classic], [ref], [webHook]}
        GET /integrity/getStatus  {apiKey, id}

        GET /email/getEmail      {apiKey, appName, appDevice, phone, type(icloud|gmail),
                                    [ref], [webHook]}
            -> {"id": "...", "status": "success", "price": 0.5, "balance": 122.0}
            设备基础设施层：申请一枚与 `phone` 配对的临时 iCloud Hide My Email / Gmail OAuth
            邮箱，与 Push Token / Play Integrity 同属"让设备看起来像真机已登录 Apple/Google 账号"
            的基础设施，用于增强 Attestation/设备画像一致性。**不是** Telegram 账号找回邮箱，
            不应写入 account.updatePasswordSettings 等账号安全层接口。
        GET /email/getStatus     {apiKey, id}
            -> {"id": "...", "status": "wait|pending|done|error",
                "email": "...", "code": "...", "message": "..."}
            done 时按 worker 类型返回 email（及可能的 code）。

    与 `AntiSafetyService` 保持一致的多候选网关容灾风格，但使用 REGHelp 独立的 API Key 与
    协议字段 —— 无需 `aid`，`appName`/`appDevice` 与项目内置的 `DeviceProfileManager` 模板
    天然对齐 (telegram_android/telegram_9 -> appName=tg, telegram_x -> appName=tg_x)。

    注：REGHelp 官方接口目前未提供与 AntiSafety `/check` 等价的号码历史安全审计能力，
    该职责仍由 `AttestationGatewayService` 路由至 AntiSafety 处理。
    """

    DEFAULT_API_BASES = ["https://api.reghelp.net"]

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
        self.api_bases = [b.rstrip("/") for b in (api_bases or self.DEFAULT_API_BASES) if b]
        if not self.api_bases:
            self.api_bases = list(self.DEFAULT_API_BASES)
        self.client = create_httpx_client(proxy=proxy, connect_timeout=connect_timeout, total_timeout=total_timeout)
        self._last_good_api_base: Optional[str] = None

    async def __aenter__(self) -> "RegHelpService":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        try:
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
    ) -> Tuple[str, Any]:
        """按序尝试候选网关地址，任一成功即返回，全部失败则汇总错误抛出"""
        clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
        errors = []
        ordered = self.api_bases
        if self._last_good_api_base and self._last_good_api_base in self.api_bases:
            ordered = [self._last_good_api_base] + [b for b in self.api_bases if b != self._last_good_api_base]

        for base in ordered:
            try:
                resp = await self.client.get(f"{base}{path}", params=clean_params, headers=headers)
                data = resp.json()
                self._last_good_api_base = base
                return base, data
            except Exception as e:
                errors.append(f"{base} -> {e}")
                logger.warning(f"REGHelp 候选网关 {base}{path} 请求失败，尝试下一候选: {e}")

        raise RuntimeError("所有 REGHelp 候选网关均不可达 (" + "; ".join(errors) + ")")

    async def get_balance(self) -> Dict[str, Any]:
        """查询 REGHelp 账户当前计费余额，同时用作鉴权与连通性诊断探针"""
        _, data = await self._get_with_fallback("/balance", {"apiKey": self.api_key})
        return data

    query_account_balance = get_balance

    async def get_push_token(
        self,
        profile: Dict[str, Any],
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None
    ) -> Optional[str]:
        """向 REGHelp Key API 请求平台推送握手凭证 (Push Token)"""
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
        if log_callback:
            await log_callback(
                f"向 REGHelp 网关发起 Push Token 生成任务 (App: {params['appName']}/{app_device}, "
                f"候选网关: {', '.join(self.api_bases)})..."
            )

        try:
            used_base, data = await self._get_with_fallback("/push/getToken", params)
        except Exception as req_err:
            raise RuntimeError(f"连接 REGHelp 网关失败 (已尝试 {', '.join(self.api_bases)}): {req_err}")

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
                return res.get("token")
            if status == "error":
                raise RuntimeError(f"REGHelp Push Token 签署失败: {res.get('message')}")

        raise TimeoutError("REGHelp Push Token 获取超时 (超过最大轮询阈值)")

    request_push_token = get_push_token

    async def set_push_status(self, task_id: str, number: str, status: str):
        """标记一次已获取的 Push Token 为无效 (NOSMS/FLOOD/BANNED/2FA)，触发平台自动退款审计

        仅在对应 getToken 请求携带了有效且已启用的 ref 时可用，否则平台会静默忽略。
        """
        try:
            await self._get_with_fallback("/push/setStatus", {
                "apiKey": self.api_key,
                "id": task_id,
                "number": number,
                "status": status
            })
        except Exception as e:
            logger.debug(f"REGHelp Push 状态回写异常: {e}")

    async def get_integrity_token(
        self,
        profile: Dict[str, Any],
        nonce: str,
        app_version_code: int,
        token_type: str = "classic",
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None
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
        if log_callback:
            await log_callback(f"向 REGHelp 网关发起 Play Integrity 凭证生成任务 (App: {params['appName']}/{app_device})...")

        used_base, data = await self._get_with_fallback("/integrity/getToken", params)
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

    async def get_email(
        self,
        phone: str,
        app_name: str = "tg",
        app_device: str = "Android",
        email_type: str = "icloud",
        ref: Optional[str] = None,
        webhook: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """向 REGHelp Key API 创建设备配对邮箱任务 (`/email/getEmail`)

        **设备基础设施层**：为 `phone` 申请一枚临时的 iCloud Hide My Email / Gmail OAuth 邮箱，
        与 Push Token / Play Integrity 同属"让设备看起来像真机已登录 Apple/Google 账号"的基础
        设施，用于增强 Attestation/设备画像一致性 —— **不是** Telegram 账号找回邮箱，调用方不应
        将返回的 email 写入 `account.updatePasswordSettings` 等账号安全层接口。

        `phone` 必须为 E.164 格式 (如 `+8613800000000`)。返回创建响应原始字典 (含 `id`/`price`/
        `balance` 等)，需配合 `get_email_status` / `wait_for_email` 轮询直到 `done`。
        """
        normalized_type = str(email_type or "icloud").lower()
        if normalized_type not in ("icloud", "gmail"):
            raise RuntimeError(f"不支持的 REGHelp 设备邮箱类型: {email_type} (仅支持 icloud/gmail)")

        device = self._normalize_device(app_device)
        params = {
            "apiKey": self.api_key,
            "appName": app_name or "tg",
            "appDevice": device,
            "phone": phone,
            "type": normalized_type,
            "ref": ref,
            "webHook": webhook
        }
        headers = {"Idempotency-Key": request_id} if request_id else None

        try:
            used_base, data = await self._get_with_fallback("/email/getEmail", params, headers=headers)
        except Exception as req_err:
            raise RuntimeError(f"连接 REGHelp 设备邮箱网关失败 (已尝试 {', '.join(self.api_bases)}): {req_err}")

        if data.get("status") == "error":
            raise RuntimeError(f"REGHelp 设备邮箱任务创建失败: {data.get('detail') or data.get('message') or data}")

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"REGHelp 设备邮箱任务创建返回异常: {data}")

        result = dict(data)
        result["_used_base"] = used_base
        return result

    request_device_email_task = get_email

    async def get_email_status(self, task_id: str, api_base: Optional[str] = None) -> Dict[str, Any]:
        """查询设备配对邮箱任务当前状态 (`/email/getStatus`)"""
        base = api_base or self._last_good_api_base or self.api_bases[0]
        resp = await self.client.get(f"{base}/email/getStatus", params={
            "apiKey": self.api_key,
            "id": task_id
        })
        return resp.json()

    async def wait_for_email(
        self,
        task_id: str,
        api_base: Optional[str] = None,
        log_callback=None,
        max_attempts: int = 60,
        interval: float = 2.0
    ) -> Dict[str, Any]:
        """轮询设备配对邮箱任务直到 `done`/`error`，成功时返回 `{"email", "code", "raw"}`"""
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval)
            if log_callback and attempt % 5 == 0:
                await log_callback(f"等待 REGHelp 签发设备配对邮箱 ({attempt * interval:.0f}s)...")

            res = await self.get_email_status(task_id, api_base=api_base)
            status = str(res.get("status") or "").lower()
            if status == "done":
                email = res.get("email")
                if not email:
                    raise RuntimeError(f"REGHelp 设备邮箱完成但未返回 email: {res}")
                return {"email": email, "code": res.get("code"), "raw": res}
            if status == "error":
                raise RuntimeError(f"REGHelp 设备邮箱签发失败: {res.get('message') or res.get('detail') or res}")

        raise TimeoutError("REGHelp 设备邮箱获取超时 (超过最大轮询阈值)")

    async def get_device_email(
        self,
        phone: str,
        app_name: str = "tg",
        app_device: str = "Android",
        email_type: str = "icloud",
        log_callback=None,
        ref: Optional[str] = None,
        webhook: Optional[str] = None,
        request_id: Optional[str] = None,
        max_attempts: int = 60,
        interval: float = 2.0
    ) -> Dict[str, Any]:
        """创建设备配对邮箱任务并轮询直到完成，返回 `{"email", "code", "task_id", "raw"}`

        组合封装 `get_email` + `wait_for_email`，与 `get_push_token` / `get_integrity_token`
        的调用风格保持一致。
        """
        if log_callback:
            await log_callback(
                f"向 REGHelp 网关发起设备配对邮箱申请 (App: {app_name}/{self._normalize_device(app_device)}, "
                f"type={email_type}, 候选网关: {', '.join(self.api_bases)})..."
            )

        data = await self.get_email(
            phone,
            app_name=app_name,
            app_device=app_device,
            email_type=email_type,
            ref=ref,
            webhook=webhook,
            request_id=request_id
        )
        task_id = data.get("id")
        used_base = data.get("_used_base")

        if log_callback:
            price_info = f" (计费: {data.get('price')}, 余额: {data.get('balance')})" if data.get("price") is not None else ""
            await log_callback(f"REGHelp 设备邮箱任务已创建 (id={task_id}){price_info}")

        result = await self.wait_for_email(
            task_id,
            api_base=used_base,
            log_callback=log_callback,
            max_attempts=max_attempts,
            interval=interval
        )
        result["task_id"] = task_id
        return result

    request_device_email = get_device_email


# 学术规范别名，与 AntiSafetyService / AttestationProofService 命名风格保持一致
RegHelpGatewayService = RegHelpService
