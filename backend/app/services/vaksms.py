import asyncio
import logging
from typing import Optional, Tuple, Dict, Any
import httpx

logger = logging.getLogger("OOBTelemetryService")

NO_NUMBER_ERROR_ALIASES = frozenset({
    "nonumber",
    "no_number",
    "no number",
    "no numbers",
    "no_numbers",
})

STOCK_HINT_REGIONS = "ID 印尼、KZ 哈萨克斯坦、RU 等"


def format_no_number_message(country: str) -> str:
    """接码平台无库存时的友好告警文案。"""
    code = (country or "?").strip().upper() or "?"
    return (
        f"⚠️ 当前拓扑区域 {code} 在接码平台暂无可分配库存 (noNumber)，"
        f"建议在控制台切换至库存充沛的区域（如 {STOCK_HINT_REGIONS}）"
    )


def is_no_number_error(error: Any) -> bool:
    text = str(error or "").strip().lower()
    if not text:
        return False
    if text in NO_NUMBER_ERROR_ALIASES:
        return True
    compact = text.replace(" ", "").replace("_", "")
    return compact == "nonumber" or "nonumber" in compact


class NoNumberAvailableError(RuntimeError):
    """Vak-SMS 返回 noNumber：目标国家当前无可租号码。"""

    def __init__(self, country: str, raw: Any = None):
        self.country = (country or "").strip().lower()
        self.raw = raw
        super().__init__(format_no_number_message(self.country))


class VakSmsService:
    """异步带外挑战响应遥测提供者 (Out-of-Band Challenge & Telemetry Provider)"""
    BASE_URL = "https://vak-sms.com/api"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def get_balance(self) -> float:
        """查询带外遥测通道当前配额点数"""
        resp = await self.client.get(f"{self.BASE_URL}/getBalance/", params={"apiKey": self.api_key})
        data = resp.json()
        if "balance" in data:
            return float(data["balance"])
        raise RuntimeError(f"获取带外遥测配额失败: {data}")

    query_telemetry_quota = get_balance

    async def get_stock_count(self, country: str = "cl", service: str = "tg") -> int:
        """查询指定地理拓扑区域当前可用的通信信道容量"""
        resp = await self.client.get(f"{self.BASE_URL}/getCountNumber/", params={
            "apiKey": self.api_key,
            "service": service,
            "country": country
        })
        data = resp.json()
        return int(data.get(service, 0))

    query_channel_capacity = get_stock_count

    async def get_number(self, country: str = "cl", service: str = "tg", operator: Optional[str] = None) -> Tuple[str, str]:
        """动态申请租借一个临时带外通信通道句柄"""
        params = {"apiKey": self.api_key, "service": service, "country": country}
        if operator:
            params["operator"] = operator
        resp = await self.client.get(f"{self.BASE_URL}/getNumber/", params=params)
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            error = data.get("error")
            if is_no_number_error(error):
                raise NoNumberAvailableError(country, data)
            raise RuntimeError(f"申请带外通信句柄失败: {error}")
        if isinstance(data, str) and is_no_number_error(data):
            raise NoNumberAvailableError(country, data)
        if "tel" in data and "idNum" in data:
            phone = str(data["tel"])
            if not phone.startswith("+"):
                phone = "+" + phone
            return str(data["idNum"]), phone
        raise RuntimeError(f"带外网关返回非预期格式: {data}")

    lease_channel_handle = get_number

    async def wait_for_code(self, act_id: str, max_attempts: int = 30, interval: float = 4.0, log_callback=None) -> str:
        """异步轮询带外信道下发的瞬时握手挑战证明 (Ephemeral Challenge Proof / OTP)"""
        params = {"apiKey": self.api_key, "idNum": act_id}
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval)
            if log_callback:
                await log_callback(f"正在异步轮询带外挑战凭证 (第 {attempt}/{max_attempts} 次)...")
            try:
                resp = await self.client.get(f"{self.BASE_URL}/getSmsCode/", params=params)
                data = resp.json()
                code = data.get("smsCode")
                if code is not None:
                    return str(code)
            except Exception as e:
                logger.warning(f"轮询带外挑战凭证异常: {e}")
        raise TimeoutError("等待带外挑战证明超时 (已达最大重试轮次)")

    poll_ephemeral_challenge_proof = wait_for_code

    async def finish(self, act_id: str):
        """确认并终结带外挑战通道会话"""
        try:
            await self.client.get(f"{self.BASE_URL}/setStatus/", params={
                "apiKey": self.api_key,
                "status": "end",
                "idNum": act_id
            })
        except Exception as e:
            logger.warning(f"带外挑战会话结束上报失败: {e}")

    finalize_channel_binding = finish

    async def cancel(self, act_id: str) -> Dict[str, Any]:
        """撤销无效或被风控阻断的带外通道句柄，并触发 Vak-SMS 自动退款。

        Vak-SMS 官方语义：`setStatus/?status=bad` 会取消当前号码并退还点数。
        返回结构化结果供编排层打印 `[自动退订/撤销信道句柄完成]`。
        """
        if not act_id:
            return {"success": False, "skipped": True, "reason": "missing_act_id", "status": "bad"}
        try:
            resp = await self.client.get(f"{self.BASE_URL}/setStatus/", params={
                "apiKey": self.api_key,
                "status": "bad",
                "idNum": act_id
            })
            data: Any
            try:
                data = resp.json()
            except Exception:
                data = {"raw": (resp.text or "")[:300]}
            error_text = ""
            if isinstance(data, dict):
                error_text = str(data.get("error") or data.get("detail") or "")
            success = resp.status_code < 400 and not error_text
            result = {
                "success": success,
                "skipped": False,
                "act_id": act_id,
                "status": "bad",
                "http_status": resp.status_code,
                "data": data,
                "error": error_text or None,
            }
            if success:
                logger.info("[自动退订/撤销信道句柄完成] act_id=%s status=bad resp=%s", act_id, data)
            else:
                logger.warning("撤销带外通道句柄未成功: act_id=%s resp=%s", act_id, data)
            return result
        except Exception as e:
            logger.warning(f"撤销带外通道句柄失败: {e}")
            return {
                "success": False,
                "skipped": False,
                "act_id": act_id,
                "status": "bad",
                "error": str(e),
            }

    revoke_channel_binding = cancel

# 学术规范别名
OOBTelemetryProvider = VakSmsService
