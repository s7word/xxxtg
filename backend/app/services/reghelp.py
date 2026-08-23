import asyncio
import logging
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

    与 `AntiSafetyService` 保持一致的多候选网关容灾风格，但使用 REGHelp 独立的 API Key 与
    协议字段 —— 无需 `aid`，`appName`/`appDevice` 与项目内置的 `DeviceProfileManager` 模板
    天然对齐 (telegram_android/telegram_9 -> appName=tg, telegram_x -> appName=tg_x)。

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
                if is_auth_error_payload(data, getattr(resp, "status_code", None)):
                    errors.append(f"{base} -> {describe_auth_error('reghelp', data)}")
                    logger.warning("REGHelp 候选网关 %s%s 鉴权失败，尝试下一候选: %s", base, path, data)
                    continue
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
        webhook: Optional[str] = None,
        request_id: Optional[str] = None
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
        headers = {"Idempotency-Key": request_id} if request_id else None
        if log_callback:
            await log_callback(
                f"向 REGHelp 网关发起 Push Token 生成任务 (App: {params['appName']}/{app_device}, "
                f"候选网关: {', '.join(self.api_bases)})..."
            )

        try:
            used_base, data = await self._get_with_fallback("/push/getToken", params, headers=headers)
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

        used_base, data = await self._get_with_fallback("/integrity/getToken", params, headers=headers)
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


# 学术规范别名，与 AntiSafetyService / AttestationProofService 命名风格保持一致
RegHelpGatewayService = RegHelpService
