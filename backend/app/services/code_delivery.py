"""auth.sendCode 验证码投递通道策略（Push Token / allow_app_hash / api_id 联动）。

CodeSettings 各字段的官方语义（core.telegram.org/constructor/codeSettings），
决定了哪些开关真的能影响 SMS 概率：

- ``token`` / ``app_sandbox``：「Used only by official iOS apps for Firebase auth：
  device token for apple push」。带上它等于告诉服务端「有一台可收 APNS 推送的官方
  客户端」，服务端就可能把 OTP 走推送而不是运营商短信 —— 这是唯一真正能压低 SMS
  概率的开关，也是 REGHelp Push Token 流程赖以收码的机制。
- ``allow_app_hash``：「required in newer versions of android, to use the android SMS
  receiver APIs」。它描述的是**短信正文里要不要附带 app hash**，属于 SMS 内容协商，
  不参与「App 还是 SMS」的通道选择。官方 Android 客户端在有 Google Play 服务时恒为
  true，所以拿 Android 指纹发码却把它关掉，只会让指纹偏离官方客户端，不会提高 SMS
  概率。因此本模块按**设备平台**而非投递模式来决定该字段。

规律归纳（来自 BATCH_STRESS_REPORT 与线上任务日志）：

1. **号池因素（主因，不可代码消除）**：复用/注销卡预检仍判白号，但 sendCode 返回
   SentCodeTypeApp 且 next_type=None —— 号码在 Telegram 侧仍挂着已授权会话，OTP 被
   投进旧客户端。换 Push/设备救不了，只能换号源或换国。压测 116 张已租号 0% 走 SMS，
   且当时用的是自建 api_id=35762565，佐证主因在号池而不在凭证。
2. **Push Token 因素（次因，但代码可控）**：压测全程强制申请并 attach Push Token，
   等于主动给服务端一条推送通道。非泄露 api_id 下没有任何理由付这个代价，跳过申请
   既省 REGHelp 费用，也移除了「服务端可以走推送」这个变量。
3. **api_id 因素**：公开泄露 ID（4/6/21724 等）无 Push 时几乎必然 API_ID_PUBLISHED_FLOOD；
   自建 api_id 可不带 Push 先发码，FLOOD 再一次性 escalate 到 push_required。
4. **猎号连续 App**：同一 Push+设备组合连续多号 App 说明是系统性失败，应在达到
   ``hunt_sms_first_after_app_streak`` 后强制 sms_first（仍持有 Token 但不 attach）。

模式说明：

- ``sms_first``：优先 SMS；非泄露 api_id 时不申请 Push、不 attach token。
  遇 API_ID_PUBLISHED_FLOOD 可 escalate 一次（申请 Push + attach）。
- ``balanced``（默认）：非泄露 effective api_id → 同 sms_first；泄露/official 路径 →
  申请 Push 并 attach（与旧版 push_required 对该类凭证等价）。
- ``push_required``：legacy；始终申请 Push 并 attach token。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.app.services.device_profile import PUBLISHED_API_ID_BLOCKLIST

CODE_DELIVERY_SMS_FIRST = "sms_first"
CODE_DELIVERY_BALANCED = "balanced"
CODE_DELIVERY_PUSH_REQUIRED = "push_required"

CODE_DELIVERY_MODES = frozenset({
    CODE_DELIVERY_SMS_FIRST,
    CODE_DELIVERY_BALANCED,
    CODE_DELIVERY_PUSH_REQUIRED,
})

DEFAULT_CODE_DELIVERY_MODE = CODE_DELIVERY_BALANCED
DEFAULT_HUNT_SMS_FIRST_AFTER_APP_STREAK = 2


@dataclass(frozen=True)
class CodeDeliveryPlan:
    """单次 sendCode 应使用的通道参数。"""

    mode: str
    effective_mode: str
    should_request_push_token: bool
    attach_push_token: bool
    allow_app_hash: bool
    can_escalate_on_published_flood: bool
    use_published_api_id: bool
    hunt_app_streak: int = 0
    forced_sms: bool = False
    official_client_emulation: bool = False
    emulation_label: str = "balanced"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def summary_for_log(self) -> str:
        parts = [
            f"通道策略={self.effective_mode}",
            f"申请Push={'是' if self.should_request_push_token else '否'}",
            f"attach_token={'是' if self.attach_push_token else '否'}",
            f"allow_app_hash={'是' if self.allow_app_hash else '否'}",
            f"模式标签={self.emulation_label}",
        ]
        if self.official_client_emulation:
            parts.append("官方客户端模拟")
        if self.forced_sms:
            parts.append("猎号强制SMS")
        if self.use_published_api_id:
            parts.append("泄露/official_api_id")
        return "，".join(parts)


def _normalize_mode(raw: Optional[str]) -> str:
    mode = str(raw or DEFAULT_CODE_DELIVERY_MODE).strip().lower()
    if mode not in CODE_DELIVERY_MODES:
        return DEFAULT_CODE_DELIVERY_MODE
    return mode


def _has_usable_custom_credentials(config: Any) -> bool:
    custom_id = getattr(config, "custom_api_id", None)
    custom_hash = getattr(config, "custom_api_hash", None)
    return bool(custom_id and custom_hash)


def is_official_client_emulation(config: Any) -> bool:
    return bool(getattr(config, "official_client_emulation", False))


def emulation_label_for(config: Any, base_mode: Optional[str] = None) -> str:
    """日志用模式标签：official 与 balanced 必须可从任务日志直接读出。"""
    if is_official_client_emulation(config):
        return "official"
    mode = _normalize_mode(base_mode if base_mode is not None else getattr(config, "code_delivery_mode", None))
    return mode


def _predict_effective_api_id(profile: Dict[str, Any], config: Any) -> int:
    """在尚未申请 Push Token 时预测 sendCode 将使用的 api_id。"""
    template_id = int(profile.get("api_id") or 0)
    if is_official_client_emulation(config):
        return template_id
    mode = getattr(config, "api_credential_mode", "auto") or "auto"
    custom_id = getattr(config, "custom_api_id", None)
    custom_hash = getattr(config, "custom_api_hash", None)
    has_custom = bool(custom_id and custom_hash)

    if mode == "custom" and has_custom:
        return int(custom_id)
    if mode == "official":
        return template_id
    # auto: 无 Push 且模板 ID 泄露 → 可能回退自建
    if template_id in PUBLISHED_API_ID_BLOCKLIST and has_custom:
        return int(custom_id)
    return template_id


def profile_allows_app_hash(profile: Dict[str, Any]) -> bool:
    """该设备指纹下官方客户端是否会设置 allow_app_hash。

    allow_app_hash 是 Android SMS Retriever 的短信正文协商位，官方 Android 客户端恒设，
    iOS 客户端从不设。跟着平台走才能保持指纹自洽；它不影响 App/SMS 通道选择。
    """
    markers = " ".join(
        str(profile.get(key) or "")
        for key in ("app_device", "lang_pack", "system_version", "device_model")
    ).lower()
    if "ios" in markers or "iphone" in markers or "ipad" in markers:
        return False
    return True


def is_published_api_id(api_id: Optional[int]) -> bool:
    try:
        return int(api_id or 0) in PUBLISHED_API_ID_BLOCKLIST
    except (TypeError, ValueError):
        return False


def resolve_code_delivery_plan(
    config: Any,
    profile: Dict[str, Any],
    *,
    hunt_app_streak: int = 0,
    force_sms_after_app: bool = False,
) -> CodeDeliveryPlan:
    """根据全局配置、预测 api_id 与猎号状态生成本轮 sendCode 通道计划。"""
    official_emu = is_official_client_emulation(config)
    base_mode = _normalize_mode(getattr(config, "code_delivery_mode", None))
    if official_emu:
        base_mode = CODE_DELIVERY_PUSH_REQUIRED
    predicted_api_id = _predict_effective_api_id(profile, config)
    published = is_published_api_id(predicted_api_id)
    label = emulation_label_for(config, base_mode)

    try:
        streak_threshold = int(
            getattr(config, "hunt_sms_first_after_app_streak", DEFAULT_HUNT_SMS_FIRST_AFTER_APP_STREAK)
            or DEFAULT_HUNT_SMS_FIRST_AFTER_APP_STREAK
        )
    except (TypeError, ValueError):
        streak_threshold = DEFAULT_HUNT_SMS_FIRST_AFTER_APP_STREAK

    # 官方客户端模拟始终 attach Push，不被猎号连续 App 强制 SMS 覆盖
    forced_sms = bool(
        not official_emu
        and (
            force_sms_after_app
            or (hunt_app_streak >= streak_threshold > 0)
        )
    )

    notes: List[str] = []
    if official_emu:
        notes.append(
            f"official_client_emulation：强制官方 api_id={predicted_api_id} + push_required"
        )

    if forced_sms and base_mode != CODE_DELIVERY_PUSH_REQUIRED:
        effective = CODE_DELIVERY_SMS_FIRST
        notes.append(
            f"猎号连续 App {hunt_app_streak} 次"
            + (f"（阈值 {streak_threshold}）" if streak_threshold else "")
            + "，强制 SMS 优先"
        )
    elif base_mode == CODE_DELIVERY_SMS_FIRST:
        effective = CODE_DELIVERY_SMS_FIRST
    elif base_mode == CODE_DELIVERY_PUSH_REQUIRED:
        effective = CODE_DELIVERY_PUSH_REQUIRED
    else:
        # balanced: 非泄露 api_id 走 SMS 优先，泄露 ID 必须 Push
        effective = CODE_DELIVERY_PUSH_REQUIRED if published else CODE_DELIVERY_SMS_FIRST
        if effective == CODE_DELIVERY_SMS_FIRST:
            notes.append(f"balanced + 非泄露 api_id={predicted_api_id} → SMS 优先")
        else:
            notes.append(f"balanced + 泄露/official api_id={predicted_api_id} → 需要 Push")

    # allow_app_hash 只跟设备平台走：它协商短信正文里的 app hash，不选择投递通道
    allow_app_hash = profile_allows_app_hash(profile)

    if effective == CODE_DELIVERY_SMS_FIRST:
        should_request = published and not forced_sms
        attach = False
        can_escalate = True
        if not published:
            notes.append("跳过 Push Token 申请（非泄露 api_id）")
        elif forced_sms:
            notes.append("泄露 api_id 但猎号强制 SMS：暂不申请 Push，FLOOD 时可 escalate")
            should_request = False
    else:
        should_request = True
        attach = True
        can_escalate = False
        notes.append("push_required：申请 Push 并 attach token")

    return CodeDeliveryPlan(
        mode=base_mode,
        effective_mode=effective,
        should_request_push_token=should_request,
        attach_push_token=attach,
        allow_app_hash=allow_app_hash,
        can_escalate_on_published_flood=can_escalate,
        use_published_api_id=published,
        hunt_app_streak=hunt_app_streak,
        forced_sms=forced_sms,
        official_client_emulation=official_emu,
        emulation_label=label,
        notes=tuple(notes),
    )


def escalation_plan_after_published_flood(plan: CodeDeliveryPlan) -> CodeDeliveryPlan:
    """sms_first / 强制 SMS 遇 API_ID_PUBLISHED_FLOOD 后的一次性 Push escalate。"""
    return CodeDeliveryPlan(
        mode=plan.mode,
        effective_mode=CODE_DELIVERY_PUSH_REQUIRED,
        should_request_push_token=True,
        attach_push_token=True,
        allow_app_hash=plan.allow_app_hash,
        can_escalate_on_published_flood=False,
        use_published_api_id=plan.use_published_api_id,
        hunt_app_streak=plan.hunt_app_streak,
        forced_sms=plan.forced_sms,
        official_client_emulation=plan.official_client_emulation,
        emulation_label=plan.emulation_label,
        notes=plan.notes + ("API_ID_PUBLISHED_FLOOD → escalate 至 push_required",),
    )
