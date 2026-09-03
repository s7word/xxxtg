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

    号码历史过滤（reporting `/check`）与 Push 主备路径解耦：只要配置了有效
    `antisafety_api_key` 且 `antisafety_phone_filter_enabled=True`（默认），即使
    Push 走 `reghelp_only` / `reghelp_primary`，仍会调用 AntiSafety 过滤烂号。
    REGHelp 官方无对等 `/check` 接口。

    支持的调度策略 (`attestation_provider_mode`)：
        - reghelp_primary   REGHelp 优先，AntiSafety 备选 (默认推荐)
        - antisafety_primary AntiSafety 优先，REGHelp 备选
        - reghelp_only       仅使用 REGHelp
        - antisafety_only    仅使用 AntiSafety
    """

    PROVIDER_REGHELP = "reghelp"
    PROVIDER_ANTISAFETY = "antisafety"
    DEFAULT_PHONE_FILTER_STATUSES = ("BANNED", "ALREADY_REGISTERED", "FLOOD_WAIT")

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

        self._antisafety_push_enabled = bool(
            getattr(config, "antisafety_enabled", True)
        ) and has_valid_api_key(antisafety_key)
        self._antisafety_phone_filter_enabled = bool(
            getattr(config, "antisafety_phone_filter_enabled", True)
        )
        # Push 关闭 AntiSafety 时仍可为号码过滤单独拉起 reporting 客户端
        if has_valid_api_key(antisafety_key) and (
            self._antisafety_push_enabled or self._antisafety_phone_filter_enabled
        ):
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
        # 号码过滤专用的 AntiSafety 客户端不参与 Push 调度
        antisafety_for_push = self.antisafety if self._antisafety_push_enabled else None
        candidates = {
            self.PROVIDER_REGHELP: self.reghelp,
            self.PROVIDER_ANTISAFETY: antisafety_for_push,
        }

        if mode == "reghelp_only":
            order = [self.PROVIDER_REGHELP]
        elif mode == "antisafety_only":
            order = [self.PROVIDER_ANTISAFETY]
        elif mode == "antisafety_primary":
            order = [self.PROVIDER_ANTISAFETY, self.PROVIDER_REGHELP]
        else:  # reghelp_primary (默认)
            order = [self.PROVIDER_REGHELP, self.PROVIDER_ANTISAFETY]

        return [(name, candidates[name]) for name in order if candidates.get(name)]

    def phone_filter_reject_statuses(self) -> List[str]:
        raw = getattr(self.config, "antisafety_phone_filter_statuses", None)
        if not raw:
            return list(self.DEFAULT_PHONE_FILTER_STATUSES)
        out: List[str] = []
        for item in raw:
            token = str(item or "").strip().upper()
            if token and token not in out:
                out.append(token)
        return out or list(self.DEFAULT_PHONE_FILTER_STATUSES)

    @staticmethod
    def matched_phone_filter_statuses(
        check_data: Optional[Dict[str, Any]],
        reject_statuses: Optional[List[str]] = None,
    ) -> List[str]:
        if not check_data:
            return []
        wanted = {
            str(x).strip().upper()
            for x in (reject_statuses or AttestationGatewayService.DEFAULT_PHONE_FILTER_STATUSES)
            if str(x).strip()
        }
        hit: List[str] = []
        raw_statuses = check_data.get("statuses")
        if raw_statuses is None:
            # 兼容个别响应把列表放在 status 字段的情况
            alt = check_data.get("status")
            raw_statuses = alt if isinstance(alt, list) else None
        for status in raw_statuses or []:
            token = str(status or "").strip().upper()
            if token in wanted and token not in hit:
                hit.append(token)
        return hit

    # 供 MagicMock 网关实例直接绑定；静态方法在 class patch 后会丢失
    def match_phone_filter_statuses(
        self,
        check_data: Optional[Dict[str, Any]],
        reject_statuses: Optional[List[str]] = None,
    ) -> List[str]:
        return AttestationGatewayService.matched_phone_filter_statuses(
            check_data, reject_statuses or self.phone_filter_reject_statuses()
        )

    async def close(self):
        if self.reghelp:
            await self.reghelp.close()
        if self.antisafety:
            await self.antisafety.close()

    async def check_phone_history(self, phone_number: str, aid: Optional[str], log_callback=None) -> Optional[Dict[str, Any]]:
        """端点历史安全审计。目前仅 AntiSafety 提供 `/check` 号码历史审计能力，
        REGHelp 官方接口暂无等价能力，故该职责始终路由至 AntiSafety。

        与 Push 主备无关：`reghelp_primary/only` 下只要配了 AntiSafety Key 且
        `antisafety_phone_filter_enabled`（默认 True）仍会执行。
        """
        if not self._antisafety_phone_filter_enabled:
            if log_callback:
                await log_callback(
                    "[AntiSafety 号码过滤] 已关闭（antisafety_phone_filter_enabled=false），跳过 /check"
                )
            return None
        if not self.antisafety:
            if log_callback:
                await log_callback(
                    "[AntiSafety 号码过滤] ⚠️ 未生效：缺少有效 antisafety_api_key "
                    "（Push 走 REGHelp 时仍需单独配置 AntiSafety Key；REGHelp 无对等 /check）"
                )
            return None
        try:
            if log_callback:
                await log_callback(
                    f"[AntiSafety 号码过滤] 正在请求 reporting `/check`（number={phone_number}）…"
                )
            data = await self.antisafety.check_phone_history(phone_number, aid)
            if log_callback:
                if data:
                    raw = data.get("statuses")
                    if raw is None and isinstance(data.get("status"), list):
                        raw = data.get("status")
                    status_label = (
                        "/".join(str(s) for s in (raw or []) if str(s).strip()) or "空"
                    )
                    await log_callback(
                        f"[AntiSafety 号码过滤] `/check` 响应 ok："
                        f"statuses={status_label} check_id={data.get('id') or '无'}"
                    )
                else:
                    await log_callback(
                        "[AntiSafety 号码过滤] `/check` 无有效 payload（非 ok 或空响应），将放行"
                    )
            return data
        except Exception as e:
            if log_callback:
                await log_callback(f"[AntiSafety 号码过滤] ⚠️ `/check` 请求异常，跳过放行: {e}")
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

    async def get_integrity_token(
        self,
        profile: Dict[str, Any],
        nonce: str,
        app_version_code: int,
        token_type: str = "classic",
        log_callback=None,
        ref: Optional[str] = None,
    ) -> Optional[str]:
        """仅走 REGHelp Play Integrity；AntiSafety 无此能力。"""
        if not self.reghelp:
            raise RuntimeError(
                "REGHelp 未启用或缺少有效 reghelp_api_key，无法申请 Play Integrity 凭证"
            )
        if log_callback:
            await log_callback(
                f"Play Integrity 走独立 REGHelp 网关 "
                f"(bases={', '.join(self.reghelp.api_bases)}, versionCode={app_version_code})"
            )
        return await self.reghelp.get_integrity_token(
            profile,
            nonce=nonce,
            app_version_code=app_version_code,
            token_type=token_type,
            log_callback=log_callback,
            ref=ref,
        )

    async def get_login_email(
        self,
        profile: Dict[str, Any],
        phone: str,
        email_type: str = "gmail",
        log_callback=None,
        ref: Optional[str] = None,
    ):
        """仅走 REGHelp Email 产品。"""
        if not self.reghelp:
            raise RuntimeError(
                "REGHelp 未启用或缺少有效 reghelp_api_key，无法申请临时登录邮箱"
            )
        return await self.reghelp.get_login_email(
            profile,
            phone=phone,
            email_type=email_type,
            log_callback=log_callback,
            ref=ref,
        )

    async def poll_email_code(self, task_id: str, log_callback=None) -> Optional[str]:
        if not self.reghelp:
            raise RuntimeError("REGHelp 未启用，无法轮询 Email 验证码")
        return await self.reghelp.poll_email_code(task_id, log_callback=log_callback)

    async def report_result(self, check_id: Optional[str], aid: Optional[str], status: str):
        """向审计监控中心上报状态机最终迁移结果 (目前仅 AntiSafety 提供上报能力)"""
        if self.antisafety and check_id:
            await self.antisafety.report_result(check_id, aid, status)


AttestationProofGatewayService = AttestationGatewayService
