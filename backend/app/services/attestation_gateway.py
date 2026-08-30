import logging
from typing import Optional, Dict, Any, List, Tuple

from backend.app.services.antisafety import AntiSafetyService
from backend.app.services.attestation_urls import (
    has_valid_api_key,
    sanitize_provider_urls,
)
from backend.app.services.recaptcha_check import recaptcha_app_device, recaptcha_app_name
from backend.app.services.reghelp import RegHelpService

logger = logging.getLogger("AttestationGatewayService")


class AttestationGatewayService:
    """统一 Attestation / Push 凭证网关高可用调度器 (Multi-Provider Attestation Gateway)

    在 REGHelp 与 AntiSafety 两个相互独立的凭证提供源之间，按 `config.attestation_provider_mode`
    策略自动决定主备顺序；任一提供源未启用、鉴权失败、超时或网络不可达时，自动切换到下一候选提供源。

    密钥与网关地址严格隔离，禁止交叉混用：
        - reghelp    -> config.reghelp_api_key    + config.reghelp_base_urls
        - antisafety -> config.antisafety_api_key + config.antisafety_base_urls

    当 `attestation_provider_mode == "reghelp_primary"` 时，优先使用带有有效
    `reghelp_api_key` 的 REGHelpService；AntiSafety 仅作为备选，且不会把
    api.reghelp.net 放进 AntiSafety 的候选列表。

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

        reghelp_key = getattr(config, "reghelp_api_key", None)
        antisafety_key = getattr(config, "antisafety_api_key", None)
        reghelp_bases = sanitize_provider_urls(
            getattr(config, "reghelp_base_urls", None),
            "reghelp",
        )
        antisafety_bases = sanitize_provider_urls(
            getattr(config, "antisafety_base_urls", None),
            "antisafety",
        )
        reporting_bases = sanitize_provider_urls(
            getattr(config, "antisafety_reporting_base_urls", None),
            "antisafety_reporting",
        )

        if getattr(config, "reghelp_enabled", True) and has_valid_api_key(reghelp_key):
            self.reghelp = RegHelpService(
                reghelp_key,
                proxy=proxy,
                api_bases=reghelp_bases,
                connect_timeout=getattr(config, "reghelp_connect_timeout", 6.0),
                total_timeout=getattr(config, "reghelp_total_timeout", 20.0)
            )

        if getattr(config, "antisafety_enabled", True) and has_valid_api_key(antisafety_key):
            self.antisafety = AntiSafetyService(
                antisafety_key,
                proxy=proxy,
                api_bases=antisafety_bases,
                reporting_bases=reporting_bases,
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
        log_callback=None,
        ref: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """按优先级策略依次尝试各 Attestation 提供源，返回 (token, task_id, 生效提供源名称)

        `ref` 会透传给 REGHelp `push/getToken`（建议传入注册任务 task_id），使后续
        `refund_push_token` 能在失败/退订分支对该任务发起 setStatus 自动退款审计。
        AntiSafety 路径没有等价的 setStatus 能力，`task_id` 恒为 None。

        若 `config.push_token_reuse_enabled`，优先从本地库存按 use_count 升序复用
        （未使用 → 用过 1 次）；命中时 provider 标记为 `reghelp_reuse`，跳过平台退款窗口逻辑。
        复用与新签发都会把 `ref`（注册任务 id）登记为该令牌的租约持有者，其它任务在租约有效
        期内既租不走它，也无法 retire / setStatus 掉它。
        """
        from backend.app.services.push_token_vault import PushTokenVault, REUSE_PROVIDER

        reuse_enabled = bool(getattr(self.config, "push_token_reuse_enabled", False))
        if reuse_enabled:
            max_uses = int(getattr(self.config, "push_token_reuse_max_uses", 2) or 2)
            vault = PushTokenVault.get_instance()
            cached = vault.acquire_for_reuse(
                max_uses=max_uses,
                app_type=profile.get("app_type") or profile.get("key"),
                lease_task_id=ref,
            )
            if cached and cached.get("token"):
                self.last_used_provider = REUSE_PROVIDER
                if log_callback:
                    await log_callback(
                        f"♻️ 复用本地 Push Token 库存: id={cached.get('id')} "
                        f"use_count {cached.get('use_count_before')} → {cached.get('use_count')}"
                        f"（上限 {max_uses}） "
                        f"签发于 {cached.get('created_at') or '-'} "
                        f"reghelp_task={cached.get('reghelp_task_id') or '-'} "
                        f"租约持有者={cached.get('lease_task_id') or '-'} "
                        f"(排序: 未使用优先，其次 1 次使用)"
                    )
                return cached.get("token"), cached.get("reghelp_task_id"), REUSE_PROVIDER

        mode = getattr(self.config, "attestation_provider_mode", "reghelp_primary") or "reghelp_primary"
        order = self._provider_order()
        if not order:
            if log_callback:
                hint = ""
                if mode.startswith("reghelp") and not self.reghelp:
                    hint = "（reghelp_primary/only 已配置，但缺少有效 reghelp_api_key）"
                await log_callback(f"⚠️ 未启用任何 Attestation / Push 凭证提供源{hint}，将直接以标准信道模式继续")
            return None, None, None

        if log_callback:
            provider_labels = {"reghelp": "REGHelp", "antisafety": "AntiSafety"}
            if mode.startswith("reghelp") and not self.reghelp:
                await log_callback(
                    "⚠️ attestation_provider_mode 优先 REGHelp，但未配置有效 reghelp_api_key，"
                    "已跳过 REGHelp，仅尝试其它已启用提供源"
                )
            await log_callback(
                f"Attestation 高可用调度顺序: {' → '.join(provider_labels.get(n, n) for n, _ in order)}"
            )

        errors = []
        for name, svc in order:
            bases = ",".join(getattr(svc, "api_bases", []) or [])
            try:
                if log_callback:
                    await log_callback(f"正在使用独立提供源 {name} (候选网关: {bases}) 申请 Push Token...")
                if name == self.PROVIDER_REGHELP:
                    result = await svc.get_push_token(profile, log_callback=log_callback, ref=ref)
                    token = result.token if result else None
                    task_id = result.task_id if result else None
                else:
                    token = await svc.get_push_token({**profile, "aid": aid}, log_callback=log_callback)
                    task_id = None

                if token:
                    self.last_used_provider = name
                    if name == self.PROVIDER_REGHELP and bool(
                        getattr(self.config, "push_token_save_issued", True)
                    ):
                        try:
                            vault = PushTokenVault.get_instance()
                            stored = vault.store_issued(
                                token=token,
                                reghelp_task_id=task_id,
                                provider="reghelp",
                                app_name=profile.get("app_name"),
                                app_device=profile.get("app_device"),
                                app_type=profile.get("app_type") or profile.get("key"),
                                source_task_id=ref,
                            )
                            vault.mark_attempt(
                                vault_id=stored.get("id"),
                                lease_task_id=ref,
                            )
                            if log_callback:
                                await log_callback(
                                    f"已写入本地 Push Token 库存 id={stored.get('id')} "
                                    f"(未成功消耗前可按开关复用)"
                                )
                        except Exception as store_exc:
                            logger.warning("Push Token 入库失败: %s", store_exc)
                    return token, task_id, name

                if log_callback:
                    await log_callback(f"⚠️ {name} 提供源未返回有效 Push Token，尝试下一候选提供源...")
            except Exception as e:
                errors.append(f"{name}: {e}")
                if log_callback:
                    await log_callback(f"⚠️ {name} 提供源获取 Push Token 失败 ({e})，自动切换至下一候选提供源...")

        if errors:
            logger.warning(f"全部 Attestation 提供源均未成功获取 Push Token: {errors}")
        return None, None, None

    async def refund_push_token(
        self,
        task_id: Optional[str],
        phone: Optional[str],
        reason: str,
        log_callback=None,
    ) -> Optional[str]:
        """把内部失败原因映射为 REGHelp setStatus 并回写，仅在持有 REGHelp 客户端且有 task_id 时生效。

        AntiSafety 无等价能力，直接跳过并返回 None。永远不会向上抛出异常（由
        `RegHelpService.set_push_status` 保证幂等/静默失败），不阻塞调用方的主流程。
        """
        if not self.reghelp or not task_id:
            return None
        try:
            return await self.reghelp.refund_push_token(task_id, phone, reason, log_callback=log_callback)
        except Exception as exc:
            logger.warning(f"REGHelp Push Token 退款回写异常 (id={task_id}, reason={reason}): {exc}")
            return None

    async def get_recaptcha_mobile_token(
        self,
        site_key: str,
        action: str = "signup",
        profile: Optional[Dict[str, Any]] = None,
        proxy: Optional[Dict[str, Any]] = None,
        log_callback=None,
    ) -> Optional[str]:
        """仅走 REGHelp RecaptchaMobile（AntiSafety 无此能力），严格使用 config.reghelp_api_key。"""
        if not self.reghelp:
            raise RuntimeError(
                "REGHelp 未启用或缺少有效 reghelp_api_key，无法自动解 RECAPTCHA_CHECK。"
                "请确认 config.reghelp_api_key 与 config.reghelp_base_urls=https://api.reghelp.net"
            )
        profile = profile or {}
        if log_callback:
            await log_callback(
                f"RECAPTCHA_CHECK 自动解题走独立 REGHelp 网关 "
                f"(bases={', '.join(self.reghelp.api_bases)}, action={action})"
            )
        return await self.reghelp.get_recaptcha_mobile_token(
            app_key=site_key,
            app_action=action,
            app_name=recaptcha_app_name(profile),
            app_device=recaptcha_app_device(profile),
            proxy=proxy,
            log_callback=log_callback,
        )

    async def report_result(self, check_id: Optional[str], aid: Optional[str], status: str):
        """向审计监控中心上报状态机最终迁移结果 (目前仅 AntiSafety 提供上报能力)"""
        if self.antisafety and check_id:
            await self.antisafety.report_result(check_id, aid, status)


AttestationProofGatewayService = AttestationGatewayService
