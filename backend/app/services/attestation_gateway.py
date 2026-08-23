import logging
from typing import Optional, Dict, Any, List, Tuple

from backend.app.services.antisafety import AntiSafetyService
from backend.app.services.reghelp import RegHelpService

logger = logging.getLogger("AttestationGatewayService")


class AttestationGatewayService:
    """统一 Attestation / Push 凭证网关高可用调度器 (Multi-Provider Attestation Gateway)

    在 REGHelp 与 AntiSafety 两个相互独立的凭证提供源之间，按 `config.attestation_provider_mode`
    策略自动决定主备顺序；任一提供源未启用、鉴权失败、超时或网络不可达时，自动切换到下一候选提供源，
    从而在不牺牲成功率的前提下获得高可用的 Push Token 获取能力。

    支持的调度策略 (`attestation_provider_mode`)：
        - reghelp_primary   REGHelp 优先，AntiSafety 备选 (默认推荐)
        - antisafety_primary AntiSafety 优先，REGHelp 备选
        - reghelp_only       仅使用 REGHelp
        - antisafety_only    仅使用 AntiSafety
    """

    PROVIDER_REGHELP = "reghelp"
    PROVIDER_ANTISAFETY = "antisafety"

    def __init__(self, config: Any, proxy: Optional[Dict[str, Any]] = None):
        self.config = config
        self.reghelp: Optional[RegHelpService] = None
        self.antisafety: Optional[AntiSafetyService] = None

        if getattr(config, "reghelp_enabled", True) and getattr(config, "reghelp_api_key", None):
            self.reghelp = RegHelpService(
                config.reghelp_api_key,
                proxy=proxy,
                api_bases=getattr(config, "reghelp_base_urls", None),
                connect_timeout=getattr(config, "reghelp_connect_timeout", 6.0),
                total_timeout=getattr(config, "reghelp_total_timeout", 20.0)
            )

        if getattr(config, "antisafety_enabled", True) and getattr(config, "antisafety_api_key", None):
            self.antisafety = AntiSafetyService(
                config.antisafety_api_key,
                proxy=proxy,
                api_bases=getattr(config, "antisafety_base_urls", None),
                reporting_bases=getattr(config, "antisafety_reporting_base_urls", None),
                connect_timeout=getattr(config, "antisafety_connect_timeout", 6.0),
                total_timeout=getattr(config, "antisafety_total_timeout", 20.0)
            )

        self.last_used_provider: Optional[str] = None

    def _provider_order(self) -> List[Tuple[str, Any]]:
        mode = getattr(self.config, "attestation_provider_mode", "reghelp_primary") or "reghelp_primary"
        candidates = {self.PROVIDER_REGHELP: self.reghelp, self.PROVIDER_ANTISAFETY: self.antisafety}

        if mode == "reghelp_only":
            order = [self.PROVIDER_REGHELP]
        elif mode == "antisafety_only":
            order = [self.PROVIDER_ANTISAFETY]
        elif mode == "antisafety_primary":
            order = [self.PROVIDER_ANTISAFETY, self.PROVIDER_REGHELP]
        else:  # reghelp_primary (默认)
            order = [self.PROVIDER_REGHELP, self.PROVIDER_ANTISAFETY]

        return [(name, candidates[name]) for name in order if candidates.get(name)]

    async def close(self):
        if self.reghelp:
            await self.reghelp.close()
        if self.antisafety:
            await self.antisafety.close()

    async def check_phone_history(self, phone_number: str, aid: Optional[str], log_callback=None) -> Optional[Dict[str, Any]]:
        """端点历史安全审计。目前仅 AntiSafety 提供 `/check` 号码历史审计能力，
        REGHelp 官方接口暂无等价能力，故该职责始终路由至 AntiSafety (若已启用)。
        """
        if not self.antisafety:
            return None
        try:
            return await self.antisafety.check_phone_history(phone_number, aid)
        except Exception as e:
            if log_callback:
                await log_callback(f"⚠️ AntiSafety 历史安全审计请求异常，跳过: {e}")
            return None

    async def get_push_token(
        self,
        profile: Dict[str, Any],
        aid: Optional[str] = None,
        log_callback=None
    ) -> Tuple[Optional[str], Optional[str]]:
        """按优先级策略依次尝试各 Attestation 提供源，返回 (token, 生效提供源名称)"""
        order = self._provider_order()
        if not order:
            if log_callback:
                await log_callback("⚠️ 未启用任何 Attestation / Push 凭证提供源，将直接以标准信道模式继续")
            return None, None

        if log_callback:
            provider_labels = {"reghelp": "REGHelp", "antisafety": "AntiSafety"}
            await log_callback(
                f"Attestation 高可用调度顺序: {' → '.join(provider_labels.get(n, n) for n, _ in order)}"
            )

        errors = []
        for name, svc in order:
            try:
                if name == self.PROVIDER_REGHELP:
                    token = await svc.get_push_token(profile, log_callback=log_callback)
                else:
                    token = await svc.get_push_token({**profile, "aid": aid}, log_callback=log_callback)

                if token:
                    self.last_used_provider = name
                    return token, name

                if log_callback:
                    await log_callback(f"⚠️ {name} 提供源未返回有效 Push Token，尝试下一候选提供源...")
            except Exception as e:
                errors.append(f"{name}: {e}")
                if log_callback:
                    await log_callback(f"⚠️ {name} 提供源获取 Push Token 失败 ({e})，自动切换至下一候选提供源...")

        if errors:
            logger.warning(f"全部 Attestation 提供源均未成功获取 Push Token: {errors}")
        return None, None

    async def report_result(self, check_id: Optional[str], aid: Optional[str], status: str):
        """向审计监控中心上报状态机最终迁移结果 (目前仅 AntiSafety 提供上报能力)"""
        if self.antisafety and check_id:
            await self.antisafety.report_result(check_id, aid, status)


AttestationProofGatewayService = AttestationGatewayService
