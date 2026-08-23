import asyncio
import logging
import hashlib
from typing import Optional, Dict, Any, List
import httpx

from backend.app.services.attestation_urls import (
    DEFAULT_ANTISAFETY_BASES,
    DEFAULT_ANTISAFETY_REPORTING_BASES,
    describe_auth_error,
    is_auth_error_payload,
    sanitize_provider_urls,
)
from backend.app.services.net_utils import create_httpx_client as _create_httpx_client

logger = logging.getLogger("AttestationProofService")


class AntiSafetyService:
    """平台带外 Attestation 凭证生成与端点风控审计客户端 (Platform Attestation & Challenge Service)

    支持多候选网关地址容灾：当主用地址 (api.antisafety.net) 因 DNS/证书/连通性问题不可达时，
    会按序自动尝试配置中给出的其它候选地址，而不是直接抛出异常并使整条注册流程被迫降级为无 Push Token 模式。

    注：REGHelp (reghelp.net) 现已拆分为独立的高可用提供源 (见 `backend/app/services/reghelp.py`)，
    拥有自己的 API Key 与协议字段，不再作为本服务的候选网关地址混用。两者由
    `backend/app/services/attestation_gateway.py` 统一编排、按优先级策略互为主备。
    """
    API_BASE = "https://api.antisafety.net"
    REPORTING_BASE = "https://reporting.antisafety.net"

    DEFAULT_API_BASES = list(DEFAULT_ANTISAFETY_BASES)
    DEFAULT_REPORTING_BASES = list(DEFAULT_ANTISAFETY_REPORTING_BASES)

    def __init__(
        self,
        api_key: str,
        proxy: Optional[Dict[str, Any]] = None,
        api_bases: Optional[List[str]] = None,
        reporting_bases: Optional[List[str]] = None,
        connect_timeout: float = 6.0,
        total_timeout: float = 20.0
    ):
        self.api_key = api_key
        self.api_bases = sanitize_provider_urls(api_bases or self.DEFAULT_API_BASES, "antisafety", self.DEFAULT_API_BASES)
        self.reporting_bases = sanitize_provider_urls(
            reporting_bases or self.DEFAULT_REPORTING_BASES,
            "antisafety_reporting",
            self.DEFAULT_REPORTING_BASES,
        )
        self.client = _create_httpx_client(proxy=proxy, connect_timeout=connect_timeout, total_timeout=total_timeout)
        self._last_good_api_base: Optional[str] = None

    async def close(self):
        try:
            await self.client.aclose()
        except Exception:
            pass

    async def _get_with_fallback(self, bases: List[str], path: str, params: Dict[str, Any]) -> Any:
        """按序尝试候选网关地址，任一成功即返回，全部失败则汇总错误抛出"""
        errors = []
        ordered = bases
        if self._last_good_api_base and self._last_good_api_base in bases:
            ordered = [self._last_good_api_base] + [b for b in bases if b != self._last_good_api_base]

        for base in ordered:
            try:
                resp = await self.client.get(f"{base}{path}", params=params)
                data = resp.json()
                if is_auth_error_payload(data, getattr(resp, "status_code", None)):
                    errors.append(f"{base} -> {describe_auth_error('antisafety', data)}")
                    logger.warning("AntiSafety 候选网关 %s%s 鉴权失败，尝试下一候选: %s", base, path, data)
                    continue
                self._last_good_api_base = base
                return base, data
            except Exception as e:
                errors.append(f"{base} -> {e}")
                logger.warning(f"AntiSafety 候选网关 {base}{path} 请求失败，尝试下一候选: {e}")

        raise RuntimeError("所有 AntiSafety 候选网关均不可达 (" + "; ".join(errors) + ")")

    async def check_phone_history(self, phone_number: str, aid: str) -> Optional[Dict[str, Any]]:
        """审计端点寻址句柄的历史安全状态与协议合规指标"""
        clean_number = "".join([c for c in phone_number if c.isdigit()])
        num_hash = hashlib.md5(clean_number.encode('utf-8')).hexdigest()
        try:
            _, data = await self._get_with_fallback(self.reporting_bases, "/check", {
                "api_key": self.api_key,
                "aid": aid,
                "hash": num_hash,
                "number": clean_number
            })
            if data.get("status") == "ok":
                return data
            logger.warning(f"端点历史安全审计返回状态: {data}")
        except Exception as e:
            logger.warning(f"端点历史安全审计请求异常: {e}")
        return None

    audit_channel_telemetry_history = check_phone_history

    async def get_push_token(self, profile: Dict[str, Any], log_callback=None) -> Optional[str]:
        """向带外 Attestation 网关请求平台推送握手凭证 (Signed Push Handshake Token)"""
        params = {
            "apiKey": self.api_key,
            "aid": profile.get("aid"),
            "appName": profile.get("app_name", "tg"),
            "appDevice": profile.get("app_device", "Android"),
            "appVersion": profile.get("app_version_pure", "10.9.2"),
            "appBuild": profile.get("app_build", "25345")
        }
        if log_callback:
            await log_callback(
                f"向 AntiSafety Attestation 网关发起凭证生成任务 (App: {params['appName']}, Build: {params['appBuild']}, "
                f"候选网关: {', '.join(self.api_bases)})..."
            )

        try:
            used_base, data = await self._get_with_fallback(self.api_bases, "/push/getToken", params)
        except Exception as req_err:
            raise RuntimeError(f"连接 AntiSafety 网关失败 (已尝试 {', '.join(self.api_bases)}): {req_err}")

        if is_auth_error_payload(data):
            raise RuntimeError(describe_auth_error("antisafety", data))

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"AntiSafety 握手凭证任务创建失败: {data}")

        if log_callback and used_base != self.api_bases[0]:
            await log_callback(f"主用网关不可达，已自动切换至备用 Attestation 网关: {used_base}")

        for attempt in range(1, 35):
            await asyncio.sleep(2.0)
            if log_callback and attempt % 3 == 0:
                await log_callback(f"等待 Attestation 云端签署握手凭证 ({attempt*2}s)...")

            check_resp = await self.client.get(f"{used_base}/push/getStatus", params={
                "apiKey": self.api_key,
                "aid": profile.get("aid"),
                "id": task_id
            })
            res = check_resp.json()
            if res.get("status") == "done":
                return res["token"]
            if res.get("status") == "error":
                raise RuntimeError(f"Attestation 凭证签署错误: {res.get('message')}")

        raise TimeoutError("Attestation 凭证获取超时 (超过最大轮询阈值)")

    request_attestation_proof_token = get_push_token

    async def report_result(self, check_id: str, aid: str, status: str):
        """向审计监控中心上报状态机最终迁移结果"""
        if not check_id:
            return
        try:
            await self._get_with_fallback(self.reporting_bases, "/report", {
                "api_key": self.api_key,
                "aid": aid,
                "id": check_id,
                "status": status
            })
            logger.info(f"状态机迁移结果上报成功: {status}")
        except Exception as e:
            logger.debug(f"状态机迁移上报异常: {e}")

    report_state_transition = report_result

AttestationProofService = AntiSafetyService
