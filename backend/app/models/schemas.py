from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator

from backend.app.services.attestation_urls import (
    DEFAULT_ANTISAFETY_BASES,
    DEFAULT_ANTISAFETY_REPORTING_BASES,
    DEFAULT_REGHELP_BASES,
    sanitize_provider_urls,
)

class EgressRelayConfig(BaseModel):
    """出口中继与网络传输网关配置 (Multipath Egress Relay Gateway)"""
    proxy_type: str = Field(default="socks5", description="传输协议: socks5 / http / direct")
    addr: str = Field(default="127.0.0.1", description="中继节点主机地址或 IP")
    port: int = Field(default=10808, description="中继节点监听端口")
    username: Optional[str] = Field(default=None, description="中继鉴权用户凭证")
    password: Optional[str] = Field(default=None, description="中继鉴权口令凭证")

# 保持向前兼容别名
ProxyConfig = EgressRelayConfig


PROXY_ROLES = ("all", "registration", "precheck")
PROXY_MODES = ("explicit", "custom_pool", "auto", "fallback")


def normalize_proxy_role(value: Any) -> str:
    token = str(value or "all").strip().lower()
    return token if token in PROXY_ROLES else "all"


def normalize_proxy_mode(value: Any) -> str:
    token = str(value or "custom_pool").strip().lower()
    return token if token in PROXY_MODES else "custom_pool"


def normalize_sms_max_price(value: Any) -> Optional[float]:
    """把配置/任务级最高出价规范化为任意正浮点数。

    必须保留精确小数（如 0.53 / 0.6 / 1.0），不得强转为整数，也不得要求 >1。
    Grizzly 账户可能按 USD(840) 或 RUB 结算：出价按账户币种原样传递，不做单位换算。
    空值或非法输入视为未设置。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        token = value.strip().replace(",", "")
        if not token:
            return None
        value = token
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed != parsed:  # NaN
        return None
    if parsed == float("inf"):
        return None
    return parsed


def format_sms_max_price(value: Any) -> Optional[str]:
    """把最高出价格式化为 Grizzly/SMS-Activate 可接受的浮点字符串。

    例如 0.53 → "0.53"，0.6 → "0.6"，1.0 → "1"，50.0 → "50"。
    """
    bid = normalize_sms_max_price(value)
    if bid is None:
        return None
    return f"{bid:.4f}".rstrip("0").rstrip(".")


class CustomProxyItem(BaseModel):
    """用户手动粘贴并持久化的自建代理节点"""
    id: Optional[str] = None
    proxy_type: str = Field(default="socks5", description="socks5 / http / socks4")
    addr: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    country_alpha3: Optional[str] = None
    city: Optional[str] = None
    egress_ip: Optional[str] = None
    latency_ms: Optional[float] = None
    healthy: Optional[bool] = None
    last_error: Optional[str] = None
    checked_at: Optional[float] = None
    source: str = "custom"
    raw_line: Optional[str] = None
    role: str = Field(
        default="all",
        description="用途角色: all 通用 / registration 注册引导与发码 / precheck 号码预检专用",
    )
    assigned_country: Optional[str] = Field(
        default=None,
        description="用户手动绑定的国家代码 (如 cl / in / id)；为空表示全球/全池可用",
    )

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, value):
        return normalize_proxy_role(value)

    @field_validator("assigned_country", mode="before")
    @classmethod
    def _normalize_assigned_country(cls, value):
        if value is None:
            return None
        token = str(value).strip().lower()
        return token or None


class AppConfigModel(BaseModel):
    """系统全局仿真实验与节点编排配置"""
    active_app_type: str = Field(
        default="telegram_android",
        description="当前激活的端点环境模板 (telegram_android / telegram_x / telegram_9)"
    )
    antisafety_api_key: str = Field(
        default="as2b21dc7b71b5ce8166a42c22b54566",
        description="带外 Attestation 凭证生成器 API Key"
    )
    antisafety_aids: Dict[str, str] = Field(
        default={
            "telegram_android": "308aba4e-5680-466b-81a5-477ac6befa95",
            "telegram_x": "47f7d612-fe1a-4167-a450-db8a52048e9c",
            "telegram_9": "59e59906-5177-4f6f-8f7e-ced3fe370997"
        },
        description="各端点环境模板绑定的 Attestation 实例标识 (AID)"
    )
    vak_sms_api_key: str = Field(
        default="16aa4499a3954317aaf002a55e354eed",
        description="异步带外挑战响应 (OOB Challenge) 遥测源 API Key"
    )
    sms_provider: str = Field(
        default="fivesim",
        description="当前接码提供源: fivesim (推荐, 5SIM) / grizzlysms (Grizzly SMS) / smsbower (SMS Bower) / vaksms (Vak-SMS)"
    )
    fivesim_api_key: str = Field(
        default="eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE4MTg5MzAxMzYsImlhdCI6MTc4NzM5NDEzNiwicmF5IjoiMTBiOGU4OTkwMmQzODdkYmUzY2Y2NzE5Mzc2MGJkOGQiLCJzdWIiOjI5NjU0NDJ9.JvnelHQoodRonZ7OWYv-5XJMXfZ0spP2pI1yPETPvD-VGe0cE8VDJGLLsg-teh_vtRhu-QzIEIji4LcztZv0rLQ8h5poAHOxlfJJYWO_Oh077GWn83n7M1Gc1fukEgmWSv--WQify8PuSK_XwLmfttwHuDAqwvLnmq2cEIYnGTdRT2LgdomcNksRYzGk26nE8wsEqJVlbhlUH9tkLjwwWzedMLdj227_b6gjmjRR0IfwTphMatLMm-5I-6i2yfUMPAKY34rpRGSPqKmjoS3jk29xOFKYVVqKBYgTb_XoaNzHMZWqCMnm7de9jU54fjkphiECRvngh4mfI3-oeDvB2A",
        description="5SIM (5sim.net) API JWT Token，请求头 Authorization: Bearer <token>"
    )
    grizzly_sms_api_key: str = Field(
        default="66bd4d8e5f54db073d15c2856c9a1366",
        description="Grizzly SMS (grizzlysms.com) API Key"
    )
    smsbower_api_key: str = Field(
        default="",
        description="SMS Bower (smsbower.app) API Key"
    )
    sms_max_price: Optional[float] = Field(
        default=None,
        description=(
            "单次接码最高出价上限（按接码平台账户结算币种原样填写）。"
            "美元账户填小数（如伊拉克 IQ 网页价 $0.5294，建议 0.55 / 0.6 / 1.0）；"
            "卢布账户填网页显示的卢布价。平台在 [底价, maxPrice] 范围内匹配高优先级现卡。"
            "为空表示使用平台默认底价。勿把美元账户误填 50/100，会被拒绝并返回 NO_NUMBERS。"
        ),
    )
    target_country: str = Field(
        default="cl",
        description="目标地理拓扑与语言区域代码 (如 cl, id, ru, af)"
    )
    proxy_seller_key: str = Field(
        default="q8WKCvB4QNUF",
        description="动态出口中继网关池 API Key"
    )
    use_proxy_seller_auto: bool = Field(
        default=False,
        description="是否根据目标拓扑区域自动分配中继节点"
    )
    fallback_proxy: EgressRelayConfig = Field(
        default_factory=EgressRelayConfig,
        description="静态后备中继网关配置"
    )
    custom_proxies: List[CustomProxyItem] = Field(
        default_factory=list,
        description="用户手动粘贴导入的自建代理池 (Custom Proxy Pool)"
    )
    default_2fa_password: str = Field(
        default="Password@2026!Sec",
        description="二级密码学状态保护凭证 (Secondary State Lock)"
    )
    auto_set_2fa: bool = Field(
        default=True,
        description="节点引导完成后是否自动启用二级密码学状态保护"
    )
    custom_device_profiles: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="自定义扩展节点环境遥测指纹库"
    )

    # ---- 自建开发者 API 凭证 与 Attestation 网关容灾 (API_ID_PUBLISHED_FLOOD 应对方案) ----
    api_credential_mode: str = Field(
        default="auto",
        description=(
            "API 凭证选择策略: "
            "official (始终使用官方内置 api_id/api_hash，需要有效 Push Token 才能规避 API_ID_PUBLISHED_FLOOD) / "
            "custom (始终强制使用下方自建开发者 api_id/api_hash) / "
            "auto (优先使用官方 ID；若本次未获取到有效 Push Token 且官方 ID 属于已知公开泄露 ID，则自动回退到自建开发者 ID)"
        )
    )
    custom_api_id: Optional[int] = Field(
        default=None,
        description="自建开发者 API ID (在 https://my.telegram.org/apps 申请获得，未被公开泄露，可规避 API_ID_PUBLISHED_FLOOD)"
    )
    custom_api_hash: Optional[str] = Field(
        default=None,
        description="自建开发者 API Hash，与 custom_api_id 配套使用"
    )
    antisafety_base_urls: List[str] = Field(
        default_factory=lambda: list(DEFAULT_ANTISAFETY_BASES),
        description="AntiSafety Push Token 网关候选地址列表 (仅 antisafety.net，禁止混入 REGHelp 地址)"
    )
    antisafety_reporting_base_urls: List[str] = Field(
        default_factory=lambda: list(DEFAULT_ANTISAFETY_REPORTING_BASES),
        description="AntiSafety 历史安全审计 / 结果上报网关候选地址列表"
    )
    antisafety_connect_timeout: float = Field(
        default=6.0,
        description="AntiSafety 网关单次连接超时 (秒)，超时后快速切换至下一候选地址，避免长时间卡死"
    )
    antisafety_total_timeout: float = Field(
        default=20.0,
        description="AntiSafety 网关单次请求总超时 (秒)"
    )
    antisafety_enabled: bool = Field(
        default=True,
        description="是否启用 AntiSafety 作为 Attestation / Push 凭证提供源"
    )

    # ---- REGHelp (reghelp.net) 高可用 Attestation / Push 凭证提供源 ----
    reghelp_api_key: str = Field(
        default="w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR",
        description="REGHelp (reghelp.net) 平台 API Key，用于高可用 Push Token / Play Integrity 凭证获取 "
                     "(参考开源客户端 https://github.com/REGHELPNET/reghelp_client)"
    )
    reghelp_base_urls: List[str] = Field(
        default_factory=lambda: list(DEFAULT_REGHELP_BASES),
        description="REGHelp Key API 候选网关地址列表 (仅 reghelp.net，禁止混入 AntiSafety 地址)"
    )
    reghelp_enabled: bool = Field(
        default=True,
        description="是否启用 REGHelp 作为 Attestation / Push 凭证提供源"
    )
    reghelp_connect_timeout: float = Field(
        default=6.0,
        description="REGHelp 网关单次连接超时 (秒)"
    )
    reghelp_total_timeout: float = Field(
        default=20.0,
        description="REGHelp 网关单次请求总超时 (秒)"
    )
    attestation_provider_mode: str = Field(
        default="reghelp_primary",
        description=(
            "Attestation / Push 凭证提供源高可用调度策略: "
            "reghelp_primary (REGHelp 优先，AntiSafety 备选，推荐) / "
            "antisafety_primary (AntiSafety 优先，REGHelp 备选) / "
            "reghelp_only (仅使用 REGHelp) / antisafety_only (仅使用 AntiSafety)"
        )
    )
    push_token_reuse_enabled: bool = Field(
        default=False,
        description=(
            "是否允许复用本地库存中「未成功消耗」的 REGHelp Push Token。"
            "默认关闭；开启后优先取 use_count=0，其次 use_count=1。"
            "已成功注册或已 setStatus 退款的令牌不会复用。"
        ),
    )
    push_token_reuse_max_uses: int = Field(
        default=2,
        ge=1,
        le=5,
        description="复用令牌的最大使用次数上限（含首次）；达到后不再参与排序选取",
    )
    push_token_save_issued: bool = Field(
        default=True,
        description="REGHelp 新签发成功后是否写入本地 Push Token 库存（与是否开启复用无关）",
    )
    phone_precheck_enabled: bool = Field(
        default=True,
        description=(
            "是否启用号码注册状态预检探测器：租号后、申请 Push Token / auth.sendCode 之前，"
            "用 lod_user 已授权 session 查询该号是否已在 Telegram 注册，拦截二手号以免消耗 Push Token"
        )
    )
    active_precheck_probe_ids: List[str] = Field(
        default_factory=list,
        description="被用户激活为号码预检探测源的凭证库 account_id 列表",
    )
    precheck_probes_configured: bool = Field(
        default=False,
        description=(
            "是否已由用户显式配置预检探针名单。"
            "未配置时默认激活所有具备 session 的账号；配置后严格只使用 active_precheck_probe_ids"
        ),
    )
    smsall_webhook_secret: str = Field(
        default="",
        description="SMSBazaar 程序推送 Webhook Secret；校验 Bearer 与 HMAC SHA256。也可设环境变量 SMSALL_HOOK_SECRET"
    )
    smsall_auto_register: bool = Field(
        default=False,
        description="收到 Telegram 低价补货/新上架 Webhook 后是否自动启动注册。默认关：半自动，只记账等你一键测试"
    )
    smsall_auto_max_price_usd: float = Field(
        default=0.5,
        description="自动开跑的最高单价（USD）；条目 priceUsd 超过此值则只记日志不开注册"
    )
    smsall_auto_count: int = Field(
        default=3,
        ge=1,
        le=10,
        description="单次自动实验的任务数"
    )
    smsall_auto_concurrency: int = Field(
        default=3,
        ge=1,
        le=10,
        description="单次自动实验的并发度"
    )
    smsall_auto_cooldown_seconds: int = Field(
        default=600,
        ge=0,
        description="同一国家自动开跑冷却秒数，避免告警抖动连打"
    )
    smsall_auto_min_stock: int = Field(
        default=1,
        ge=0,
        description="stockTo 低于此值不开跑"
    )
    smsall_auto_max_countries: int = Field(
        default=2,
        ge=1,
        le=10,
        description="单次 Webhook 最多自动开跑的国家数（按单价从低到高）"
    )

    @field_validator("antisafety_base_urls", mode="before")
    @classmethod
    def _isolate_antisafety_base_urls(cls, value):
        return sanitize_provider_urls(value, "antisafety", DEFAULT_ANTISAFETY_BASES)

    @field_validator("antisafety_reporting_base_urls", mode="before")
    @classmethod
    def _isolate_antisafety_reporting_urls(cls, value):
        return sanitize_provider_urls(value, "antisafety_reporting", DEFAULT_ANTISAFETY_REPORTING_BASES)

    @field_validator("reghelp_base_urls", mode="before")
    @classmethod
    def _isolate_reghelp_base_urls(cls, value):
        return sanitize_provider_urls(value, "reghelp", DEFAULT_REGHELP_BASES)

    @field_validator("sms_provider", mode="before")
    @classmethod
    def _normalize_sms_provider(cls, value):
        token = str(value or "fivesim").strip().lower().replace("-", "").replace("_", "")
        aliases = {
            "fivesim": "fivesim",
            "5sim": "fivesim",
            "5simnet": "fivesim",
            "fivesimnet": "fivesim",
            "grizzly": "grizzlysms",
            "grizzlysms": "grizzlysms",
            "grizzlysmscom": "grizzlysms",
            "smsbower": "smsbower",
            "smsbowerapp": "smsbower",
            "bower": "smsbower",
            "vak": "vaksms",
            "vaksms": "vaksms",
        }
        return aliases.get(token, "fivesim")

    @field_validator("sms_max_price", mode="before")
    @classmethod
    def _normalize_sms_max_price(cls, value):
        return normalize_sms_max_price(value)


class DeviceProfileSchema(BaseModel):
    """边缘节点硬件拓扑与环境指纹模型"""
    key: str
    name: str
    aid: str
    api_id: int
    api_hash: str
    app_name: str
    app_device: str
    device_model: str
    system_version: str
    app_version: str
    app_version_pure: str
    app_build: str
    lang_pack: str
    lang_code: str
    system_lang_code: str
    is_published_api_id: bool = Field(
        default=True,
        description="该 api_id 是否为已知被公开泄露的官方 ID (缺少合法 Push Token 时几乎必然触发 API_ID_PUBLISHED_FLOOD)"
    )
    credential_source: str = Field(
        default="official",
        description="本次生效凭证来源: official / custom / custom_auto_fallback"
    )


class TestApiResponse(BaseModel):
    """诊断探针与接口连通性响应"""
    success: bool
    service: str
    message: str
    data: Optional[Dict[str, Any]] = None


class SmsStockCountryItem(BaseModel):
    """接码平台实时有货国家条目"""
    code: str = Field(..., description="ISO-2 国家代码，未知 ID 时回落为平台数字 ID")
    name: str = ""
    name_zh: str = ""
    dial: str = ""
    flag: str = ""
    stock: int = 0
    cost: float = 0.0
    provider: str = "grizzlysms"
    provider_country_id: Optional[str] = None
    lang_code: Optional[str] = None
    system_lang_code: Optional[str] = None
    tz_offset: Optional[int] = None


class SmsAvailableCountriesResponse(BaseModel):
    """GET /api/sms/available-countries 动态库存发现响应"""
    success: bool = True
    provider: str = "grizzlysms"
    service: str = "tg"
    items: List[SmsStockCountryItem] = Field(default_factory=list)
    total_countries: int = 0
    total_stock: int = 0
    updated_at: float = 0.0
    cached: bool = False
    cache_age_seconds: float = 0.0
    message: str = ""


class BannedPhonesCacheStatusResponse(BaseModel):
    """本地号码黑名单状态与号段画像"""
    enabled: bool = True
    size: int = 0
    path: str = ""
    message: str = ""
    prefixes: List[Dict[str, Any]] = Field(default_factory=list)
    countries: List[Dict[str, Any]] = Field(default_factory=list)
    categories: List[Dict[str, Any]] = Field(default_factory=list)


class BannedPhoneItem(BaseModel):
    phone: str = ""
    digits: str = ""
    reason: str = ""
    source: str = ""
    category: str = "banned"
    country: Optional[str] = None
    prefix: str = ""
    note: str = ""
    first_seen: str = ""
    last_seen: str = ""
    hits: int = 1


class BannedPhonesSummary(BaseModel):
    total: int = 0
    banned: int = 0
    already_registered: int = 0
    manual: int = 0


class BannedPhonesListResponse(BaseModel):
    success: bool = True
    summary: BannedPhonesSummary = Field(default_factory=BannedPhonesSummary)
    items: List[BannedPhoneItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 200
    offset: int = 0
    path: str = ""
    message: str = ""


class BannedPhoneAddRequest(BaseModel):
    phone: str = Field(..., min_length=5, description="国际号码，可带 +")
    reason: str = Field(default="MANUAL_BLACKLIST", description="入库原因")
    category: Optional[str] = Field(
        default="manual",
        description="banned | already_registered | manual",
    )
    note: str = Field(default="", description="备注")
    country: Optional[str] = None


class BannedPhonesPurgeRequest(BaseModel):
    category: Optional[str] = Field(
        default=None,
        description="仅清理指定分类；省略则清空全部",
    )


class BannedPhonesActionResponse(BaseModel):
    success: bool = True
    message: str = ""
    deleted: int = 0
    summary: Optional[BannedPhonesSummary] = None
    item: Optional[BannedPhoneItem] = None


class PhonePrecheckStatusResponse(BaseModel):
    """号码白号预检探测器就绪状态"""
    enabled: bool
    active: bool
    probe_count: int = 0
    probe_phones: List[str] = Field(default_factory=list)
    degraded: bool = False
    message: str = ""
    active_probes: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="已激活探针明细：账号、健康度、session 状态",
    )
    precheck_proxy: Optional[Dict[str, Any]] = Field(
        default=None,
        description="当前预检通道绑定的专用/通用代理出口信息",
    )


class RegisterTaskRequest(BaseModel):
    """节点引导与握手仿真任务请求"""
    country: Optional[str] = Field(default=None, description="目标拓扑区域代码")
    app_type: Optional[str] = Field(default=None, description="指定端点架构模板")
    session_name: Optional[str] = Field(default=None, description="自定义密码学上下文快照命名")
    proxy: Optional[EgressRelayConfig] = Field(default=None, description="自定义覆盖中继网关")
    set_2fa: Optional[bool] = Field(default=None, description="覆盖二级保护凭证设定")
    proxy_id: Optional[str] = Field(
        default=None,
        description="显式指定使用的自建/静态代理 ID；与 proxy_mode=explicit 配合，100% 遵从用户指定",
    )
    proxy_mode: str = Field(
        default="custom_pool",
        description=(
            "代理配对策略: explicit 显式指定 / custom_pool 自建池优先轮换 / "
            "auto API 动态分配 / fallback 全局后备"
        ),
    )
    sms_provider: Optional[str] = Field(
        default=None,
        description="单次任务覆盖接码提供源: fivesim / grizzlysms / smsbower / vaksms；为空则使用全局配置",
    )
    max_price: Optional[float] = Field(
        default=None,
        description=(
            "单次任务接码最高出价上限（按账户结算币种原样填写，支持 0.53 / 0.6 / 1.0）。"
            "覆盖全局 sms_max_price；为空则回落系统配置，再为空则使用平台默认底价"
        ),
    )
    max_number_attempts: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "循环试号：同一任务内最多换号次数（1=关闭）。"
            "遇 SentCodeTypeApp / 黑名单 / 预检已注册等可换号原因时退订并复用同一 Push Token"
        ),
    )

    @field_validator("proxy_mode", mode="before")
    @classmethod
    def _normalize_proxy_mode(cls, value):
        return normalize_proxy_mode(value)

    @field_validator("sms_provider", mode="before")
    @classmethod
    def _normalize_task_sms_provider(cls, value):
        if value is None or str(value).strip() == "":
            return None
        token = str(value).strip().lower().replace("-", "").replace("_", "")
        aliases = {
            "fivesim": "fivesim",
            "5sim": "fivesim",
            "5simnet": "fivesim",
            "fivesimnet": "fivesim",
            "grizzly": "grizzlysms",
            "grizzlysms": "grizzlysms",
            "smsbower": "smsbower",
            "smsbowerapp": "smsbower",
            "bower": "smsbower",
            "vak": "vaksms",
            "vaksms": "vaksms",
        }
        return aliases.get(token, token)

    @field_validator("max_price", mode="before")
    @classmethod
    def _normalize_task_max_price(cls, value):
        return normalize_sms_max_price(value)

# 学术化别名
NodeProvisioningRequest = RegisterTaskRequest


class BatchRegisterRequest(RegisterTaskRequest):
    """并发批量节点引导请求"""
    count: int = Field(default=3, ge=1, le=10, description="批量任务数 (1~10)")
    concurrency: int = Field(default=3, ge=1, le=10, description="同时运行的最大任务数")


RegisterBatchRequest = BatchRegisterRequest


class RegisterTaskResponse(BaseModel):
    """节点引导任务创建响应"""
    task_id: str
    status: str
    message: str

NodeProvisioningResponse = RegisterTaskResponse


class BatchRegisterResponse(BaseModel):
    """并发批量节点引导创建响应"""
    batch_id: str
    task_ids: List[str]
    count: int
    concurrency: int
    status: str
    message: str
    country: Optional[str] = None
    app_type: Optional[str] = None


class SmsallTrialRequest(BaseModel):
    """对 Webhook 通知中的国家一键测试注册"""
    event_id: Optional[str] = Field(default=None, description="通知列表里的事件 id")
    country: Optional[str] = Field(default=None, description="ISO2 国家码；缺省时按 event_id 回填")
    count: int = Field(default=1, ge=1, le=10, description="测试任务数")
    concurrency: int = Field(default=1, ge=1, le=10, description="测试线程 / 并发")


class SmsallDeleteEventsRequest(BaseModel):
    """删除 Webhook 通知列表"""
    event_ids: List[str] = Field(default_factory=list, description="要删除的通知 id")
    clear_all: bool = Field(default=False, description="为 true 时清空全部通知")


class BatchStatusResponse(BaseModel):
    """批次聚合状态"""
    batch_id: str
    task_ids: List[str] = Field(default_factory=list)
    count: int = 0
    concurrency: int = 0
    country: Optional[str] = None
    app_type: Optional[str] = None
    status: str = "pending"
    success: int = 0
    failed: int = 0
    running: int = 0
    pending: int = 0
    precheck_intercepted: int = 0
    no_number: int = 0
    created_at: str
    updated_at: str


class TaskStatusResponse(BaseModel):
    """节点状态机生命周期与审计追踪响应"""
    task_id: str
    status: str  # pending, running, waiting_code, logging_in, success, failed, filtered, canceled
    phone: Optional[str] = Field(default=None, description="绑定的端点通信句柄")
    user_id: Optional[int] = Field(default=None, description="协商确认的分布式节点 UID")
    error: Optional[str] = None
    logs: List[str] = []
    batch_id: Optional[str] = Field(default=None, description="所属并发批次 ID")
    account_kind: Optional[str] = None
    needs_signup: Optional[bool] = None
    precheck_intercepted: bool = Field(default=False, description="是否被号码注册状态预检拦截")
    precheck_user_id: Optional[int] = Field(default=None, description="预检解析出的已注册 Telegram UID")
    banned_cache_hit: bool = Field(default=False, description="是否被本地封禁号缓存拦截")
    no_number: bool = Field(default=False, description="接码平台对该区域返回 noNumber")
    mode: Optional[str] = Field(default=None, description="任务模式: auto / manual")
    phone_code_hash: Optional[str] = Field(default=None, description="auth.sendCode 返回的 phone_code_hash")
    delivery_type: Optional[str] = Field(default=None, description="验证码分发通道类型")
    session_file: Optional[str] = Field(default=None, description="成功后写入的 .session 文件名")
    expires_at: Optional[str] = Field(default=None, description="手动等待验证码的截止时间")
    push_task_id: Optional[str] = Field(default=None, description="REGHelp Push Token 任务 id，用于 setStatus 退款审计")
    push_provider: Optional[str] = Field(default=None, description="本次生效的 Attestation 提供源: reghelp / antisafety")
    created_at: str
    updated_at: str

NodeTaskStatusResponse = TaskStatusResponse


# ==================== 手动单号注册调试控制台 ====================

class ManualRegisterStartRequest(BaseModel):
    """手动发码：跳过接码平台租号，直接对用户填写的手机号调用 auth.sendCode。"""
    phone: str = Field(..., min_length=8, max_length=32, description="国际格式手机号，支持 + 或纯数字")
    country: Optional[str] = Field(default=None, description="目标拓扑区域；为空则从手机号推断")
    app_type: Optional[str] = Field(default=None, description="指定端点架构模板")
    proxy: Optional[EgressRelayConfig] = Field(default=None, description="自定义覆盖中继网关")
    set_2fa: Optional[bool] = Field(default=None, description="覆盖二级保护凭证设定")
    proxy_id: Optional[str] = Field(default=None, description="显式指定自建/静态代理 ID")
    proxy_mode: str = Field(
        default="custom_pool",
        description="代理配对策略: explicit / custom_pool / auto / fallback",
    )
    first_name: Optional[str] = Field(default=None, description="新号 SignUp 时使用的名")
    last_name: Optional[str] = Field(default=None, description="新号 SignUp 时使用的姓")

    @field_validator("proxy_mode", mode="before")
    @classmethod
    def _normalize_manual_proxy_mode(cls, value):
        return normalize_proxy_mode(value)


class ManualRegisterStartResponse(BaseModel):
    """发码阶段响应：成功后进入 waiting_code 等待人工输入验证码。"""
    task_id: str
    status: str = Field(description='waiting_code / running / failed')
    phone: Optional[str] = None
    phone_code_hash: Optional[str] = None
    delivery_type: Optional[str] = None
    message: str
    logs: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


class ManualRegisterSubmitCodeRequest(BaseModel):
    """提交短信/客户端验证码，完成 auth.signIn / auth.signUp。"""
    task_id: str
    code: str = Field(..., min_length=3, max_length=12, description="短信或客户端验证码")
    password: Optional[str] = Field(default=None, description="已有账号 2FA 口令")
    first_name: Optional[str] = Field(default=None, description="新号 SignUp 覆盖名")
    last_name: Optional[str] = Field(default=None, description="新号 SignUp 覆盖姓")


class ManualRegisterSubmitCodeResponse(BaseModel):
    """验证码提交后的终态响应。"""
    task_id: str
    status: str = Field(description="success / failed / waiting_code")
    phone: Optional[str] = None
    user_id: Optional[int] = None
    message: str
    session_file: Optional[str] = None
    account_kind: Optional[str] = None
    logs: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class ManualRegisterCancelRequest(BaseModel):
    task_id: str


class ManualRegisterCancelResponse(BaseModel):
    task_id: str
    status: str = "canceled"
    message: str
    logs: List[str] = Field(default_factory=list)


# ==================== Account Vault & Telegram Apps Helper ====================

class VaultAccountItem(BaseModel):
    """已有账号凭证库条目（脱敏后的可展示元数据）"""
    account_id: str = Field(..., description="稳定账号标识，用于后续 apply / apps 操作")
    source: str = Field(..., description="来源分区: lod_user / sessions")
    phone: Optional[str] = Field(default=None, description="国际格式手机号")
    phone_raw: Optional[str] = Field(default=None, description="原始文件中记录的手机号")
    user_id: Optional[int] = Field(default=None, description="Telegram 用户 ID")
    register_time: Optional[str] = Field(default=None, description="注册/导入时间 (ISO 或可读字符串)")
    register_time_unix: Optional[int] = Field(default=None, description="原始 Unix 时间戳")
    device_model: Optional[str] = Field(default=None, description="设备型号")
    system_version: Optional[str] = Field(default=None, description="SDK / 系统版本")
    app_version: Optional[str] = Field(default=None, description="客户端版本")
    lang_pack: Optional[str] = Field(default=None)
    system_lang_code: Optional[str] = Field(default=None)
    app_id: Optional[int] = Field(default=None, description="该账号记录的 api_id / app_id")
    app_hash: Optional[str] = Field(default=None, description="该账号记录的 api_hash / app_hash")
    is_published_api_id: bool = Field(default=False, description="记录的 api_id 是否属于已知公开泄露 ID")
    has_usable_custom_credentials: bool = Field(
        default=False,
        description="是否具备可一键应用到全局配置的非公开泄露 api_id/api_hash"
    )
    has_session: bool = Field(default=False, description="是否存在可用的 Telethon .session 快照")
    has_json: bool = Field(default=False, description="是否存在 JSON 元数据")
    has_2fa: bool = Field(default=False, description="元数据是否标记了二级密码")
    can_request_new_api_credentials: bool = Field(
        default=False,
        description="是否具备向 my.telegram.org 申请专属 api_id/api_hash 的基本条件 (至少有手机号)"
    )
    session_missing_for_auto_code: bool = Field(
        default=True,
        description="是否因缺少同名 .session 而无法自动读取 my.telegram.org 登录码"
    )
    session_valid: bool = Field(default=False, description="同名 .session 是否为可用的 Telethon SQLite 快照")
    usable: bool = Field(default=False, description="是否可作为注册号帐户导出/登录（有效 session）")
    useless: bool = Field(default=True, description="无 session 或 session 损坏/占位，属于无用凭证")
    useless_reason: Optional[str] = Field(
        default=None,
        description="json_only / invalid_session / incomplete_session / empty；可用帐户为 null",
    )
    apps_apply_hint: Optional[str] = Field(
        default=None,
        description="针对该账号申请/应用开发者凭证的操作提示"
    )
    json_path: Optional[str] = Field(default=None, description="相对仓库的 JSON 路径")
    session_path: Optional[str] = Field(default=None, description="相对仓库的 .session 路径")
    filename: Optional[str] = Field(default=None)
    is_probe_active: bool = Field(
        default=False,
        description="是否被用户激活为号码预检探测源",
    )


class VaultAccountListResponse(BaseModel):
    """凭证库扫描结果"""
    total: int
    lod_user_dir: str
    sessions_dir: str
    accounts: List[VaultAccountItem]
    applied_api_id: Optional[int] = Field(default=None, description="当前全局配置中的 custom_api_id")
    applied_api_hash: Optional[str] = Field(default=None, description="当前全局配置中的 custom_api_hash")
    api_credential_mode: Optional[str] = None
    published_api_id_count: int = 0
    missing_session_count: int = 0
    usable_count: int = 0
    useless_count: int = 0
    guidance: Optional[str] = Field(
        default=None,
        description="如何用 lod_user 已有账号申请全新 api_id/api_hash 的操作说明"
    )
    active_probe_count: int = Field(default=0, description="当前已激活的预检探针数量")
    precheck_probes_configured: bool = Field(
        default=False,
        description="用户是否已显式配置预检探针名单",
    )


class VaultUploadResponse(BaseModel):
    """浏览器端上传 .zip / .session / .json 后的导入结果"""
    success: bool
    message: str
    filename: str
    kind: str = Field(default="unknown", description="zip / session / json")
    dest_dir: str = ""
    imported_files: List[str] = Field(default_factory=list)
    skipped_files: List[str] = Field(default_factory=list)
    imported_accounts: List[VaultAccountItem] = Field(default_factory=list)
    imported_count: int = 0
    total: int = 0
    paired_count: int = 0


class ToggleVaultProbeRequest(BaseModel):
    """开启或停用某个凭证库账号作为预检探测源"""
    account_id: str
    active: bool = True


class ToggleVaultProbeResponse(BaseModel):
    success: bool
    message: str
    account_id: Optional[str] = None
    active: bool = False
    is_probe_active: bool = False
    active_precheck_probe_ids: List[str] = Field(default_factory=list)
    active_probe_count: int = 0
    precheck_probes_configured: bool = False


class VaultAccountBulkRequest(BaseModel):
    """批量导出 / 删除凭证库账号。"""
    account_ids: List[str] = Field(default_factory=list, description="指定账号 ID；scope=selected 时必填")
    scope: str = Field(
        default="selected",
        description="selected / usable / useless / all；非 selected 时忽略空的 account_ids，按扫描结果筛选",
    )


class VaultDeleteResponse(BaseModel):
    success: bool
    message: str
    deleted: int = 0
    skipped: List[str] = Field(default_factory=list)
    remaining: int = 0
    usable_count: int = 0
    useless_count: int = 0


class ApplyVaultCredentialsRequest(BaseModel):
    """将某个已有账号的 app_id/app_hash 写入全局配置"""
    account_id: str
    set_mode_custom: bool = Field(
        default=True,
        description="写入后是否将 api_credential_mode 设为 custom，确保立即生效"
    )


class ApplyVaultCredentialsResponse(BaseModel):
    success: bool
    message: str
    account_id: Optional[str] = None
    custom_api_id: Optional[int] = None
    custom_api_hash: Optional[str] = None
    api_credential_mode: Optional[str] = None
    is_published_api_id: bool = False
    warning: Optional[str] = None


class TelegramAppsStartRequest(BaseModel):
    """对指定已有账号或手机号发起 my.telegram.org 开发者门户登录"""
    account_id: Optional[str] = Field(
        default=None,
        description="凭证库账号 ID；与 phone 二选一，优先使用 account_id"
    )
    phone: Optional[str] = Field(
        default=None,
        description="已登录 Telegram 客户端的手机号。无 .session 时走 Web 登录码手动提交"
    )
    auto_read_code: bool = Field(
        default=True,
        description="若存在 Telethon session，则自动读取官方登录验证码"
    )
    app_title: Optional[str] = Field(default=None, description="若需创建新应用时使用的标题")
    app_shortname: Optional[str] = Field(default=None, description="若需创建新应用时使用的短名")
    apply_to_config: bool = Field(
        default=False,
        description="成功获取后是否立即写入 custom_api_id / custom_api_hash"
    )


class TelegramAppsSubmitCodeRequest(BaseModel):
    """在无法自动读取验证码时，手动提交 my.telegram.org 登录码"""
    job_id: str
    code: str = Field(..., min_length=3, max_length=24)
    apply_to_config: bool = Field(default=False)


class TelegramAppsApplyRequest(BaseModel):
    """将某次申请任务得到的 api_id/api_hash 写入全局配置"""
    job_id: str
    set_mode_custom: bool = Field(default=True)


class TelegramAppsJobResponse(BaseModel):
    """my.telegram.org 申请任务状态"""
    job_id: str
    account_id: Optional[str] = None
    phone: Optional[str] = None
    status: str = Field(
        default="pending",
        description=(
            "pending / sending_code / waiting_code / logging_in / "
            "fetching_apps / creating_app / success / failed"
        )
    )
    logs: List[str] = []
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    app_title: Optional[str] = None
    created_new_app: bool = False
    applied_to_config: bool = False
    needs_manual_code: bool = False
    error: Optional[str] = None
    created_at: str
    updated_at: str


class TelegramAppsJobListResponse(BaseModel):
    jobs: List[TelegramAppsJobResponse]


# ==================== Proxy-Seller 区域代理池 ====================

class ProxySellerNode(BaseModel):
    """归一化后的 Proxy-Seller 出口节点"""
    id: Optional[Any] = None
    order_id: Optional[Any] = None
    proxy_type: str = "socks5"
    addr: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    country_alpha3: Optional[str] = None
    active_until: Optional[str] = None
    status: Optional[str] = None
    status_type: Optional[str] = None
    can_prolong: bool = False
    catalog_type: Optional[str] = None
    healthy: Optional[bool] = None
    egress_ip: Optional[str] = None
    egress_country: Optional[str] = None
    egress_country_code: Optional[str] = None
    last_error: Optional[str] = None
    checked_at: Optional[float] = None


class ProxySellerListResponse(BaseModel):
    success: bool
    message: str
    country: Optional[str] = None
    total: int = 0
    proxies: List[Dict[str, Any]] = Field(default_factory=list)
    cached: bool = False
    cache_age_seconds: Optional[float] = None
    available_countries: List[str] = Field(default_factory=list)


class ProxySellerAutoSelectRequest(BaseModel):
    target_country: Optional[str] = Field(default=None, description="目标区域 ISO-2 / ISO-3 / 国家名")
    country: Optional[str] = Field(default=None, description="target_country 别名")
    apply_fallback: bool = Field(default=False, description="是否一键写入 config.fallback_proxy")
    probe: bool = Field(default=False, description="是否按顺序测活后挑选")
    allow_fallback: bool = Field(
        default=True,
        description="指定国家无节点时是否允许调用方降级到配置的 fallback_proxy（不再跨大区抽节点）",
    )
    refresh: bool = Field(default=False, description="是否绕过本地缓存强制拉取 API")
    api_key: Optional[str] = None

    def resolved_country(self) -> Optional[str]:
        return (self.target_country or self.country or "").strip() or None


class ProxySellerAutoSelectResponse(BaseModel):
    success: bool
    message: str
    matched: bool = False
    fallback_used: bool = False
    applied: bool = False
    target_country: Optional[str] = None
    source: Optional[str] = None
    hint: Optional[str] = None
    proxy: Optional[Dict[str, Any]] = None
    fallback_proxy: Optional[EgressRelayConfig] = None


class ProxySellerTestAllRequest(BaseModel):
    country: Optional[str] = None
    api_key: Optional[str] = None
    refresh: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    concurrency: int = Field(default=4, ge=1, le=10)


class ProxySellerTestAllResponse(BaseModel):
    success: bool
    message: str
    tested: int = 0
    healthy: int = 0
    country: Optional[str] = None
    results: List[Dict[str, Any]] = Field(default_factory=list)


class ProxySellerResidentListSummary(BaseModel):
    id: Optional[Any] = None
    title: str
    country: Optional[str] = None
    ports: Optional[int] = None
    rotation: Optional[Any] = None


class ProxySellerResidentListsResponse(BaseModel):
    success: bool
    message: str
    lists: List[ProxySellerResidentListSummary] = Field(default_factory=list)
    bot_skipped: int = 0
    package_active: Optional[bool] = None


class ProxySellerEnsureTgRequest(BaseModel):
    target_country: Optional[str] = Field(default=None, description="目标国家 ISO-2 / ISO-3 / 国家名")
    country: Optional[str] = Field(default=None, description="target_country 别名，兼容前端/脚本传 country")
    create: bool = Field(default=True, description="没有 {CC}_tg 时是否 POST resident/list/add")
    ports: int = Field(default=10, ge=1, le=20)
    probe: bool = Field(default=False, description="是否对导出节点测活")
    rotation: int = Field(default=3600, ge=0)
    api_key: Optional[str] = None

    def resolved_country(self) -> Optional[str]:
        return (self.target_country or self.country or "").strip() or None


class ProxySellerEnsureTgResponse(BaseModel):
    success: bool
    message: str
    created: bool = False
    title: Optional[str] = None
    hint: Optional[str] = None
    proxies: List[Dict[str, Any]] = Field(default_factory=list)


# ==================== 自定义代理池 (Custom Proxy Pool) ====================

class CustomProxyListResponse(BaseModel):
    success: bool
    message: str
    total: int = 0
    healthy: int = 0
    country: Optional[str] = None
    countries: List[str] = Field(default_factory=list)
    proxies: List[Dict[str, Any]] = Field(default_factory=list)
    fallback_proxy: Optional[EgressRelayConfig] = None
    role_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="用途角色统计: all / registration / precheck",
    )


class CustomProxyImportRequest(BaseModel):
    text: str = Field(..., description="多行代理文本，支持 host:port:user:pass / host;port;user;pass / user:pass@host:port / scheme://...")
    probe: bool = Field(default=False, description="导入后是否立即并发测活")
    replace: bool = Field(default=False, description="是否用本次解析结果整表替换自建池")
    default_protocol: str = Field(default="socks5", description="无协议前缀时的默认协议")
    default_country: Optional[str] = Field(default=None, description="可选：为本批未测活节点预标注国家 ISO-2")
    default_role: str = Field(default="all", description="无 #role 后缀时的默认用途角色")
    concurrency: int = Field(default=4, ge=1, le=16)

    @field_validator("default_role", mode="before")
    @classmethod
    def _normalize_default_role(cls, value):
        return normalize_proxy_role(value)


class CustomProxyImportResponse(BaseModel):
    success: bool
    message: str
    parsed: int = 0
    imported: int = 0
    updated: int = 0
    skipped: List[str] = Field(default_factory=list)
    skipped_count: int = 0
    total: int = 0
    proxies: List[Dict[str, Any]] = Field(default_factory=list)
    probe: Optional[Dict[str, Any]] = None


class CustomProxyTestAllRequest(BaseModel):
    concurrency: int = Field(default=4, ge=1, le=16)
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class CustomProxyTestAllResponse(BaseModel):
    success: bool
    message: str
    tested: int = 0
    healthy: int = 0
    results: List[Dict[str, Any]] = Field(default_factory=list)
    proxies: List[Dict[str, Any]] = Field(default_factory=list)


class CustomProxySetFallbackRequest(BaseModel):
    proxy_id: Optional[str] = None
    addr: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None


class CustomProxySetFallbackResponse(BaseModel):
    success: bool
    message: str
    proxy: Optional[Dict[str, Any]] = None
    fallback_proxy: Optional[EgressRelayConfig] = None


class CustomProxyUpdateItemRequest(BaseModel):
    """修改单个自建代理的用途角色、绑定国家、协议等属性"""
    proxy_id: Optional[str] = None
    addr: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = Field(default=None, description="all / registration / precheck")
    assigned_country: Optional[str] = Field(default=None, description="绑定国家 ISO-2；空字符串表示清除绑定")
    proxy_type: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    clear_assigned_country: bool = Field(default=False, description="显式清除绑定国家")

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_update_role(cls, value):
        if value is None or str(value).strip() == "":
            return None
        return normalize_proxy_role(value)

    @field_validator("assigned_country", mode="before")
    @classmethod
    def _normalize_update_assigned(cls, value):
        if value is None:
            return None
        token = str(value).strip().lower()
        return token or None


class CustomProxyUpdateItemResponse(BaseModel):
    success: bool
    message: str
    proxy: Optional[Dict[str, Any]] = None
    proxies: List[Dict[str, Any]] = Field(default_factory=list)


class CustomProxyDeleteRequest(BaseModel):
    proxy_id: Optional[str] = None
    addr: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    clear_all: bool = False


class CustomProxyDeleteResponse(BaseModel):
    success: bool
    message: str
    deleted: int = 0
    remaining: int = 0
    cleared: bool = False
    proxy: Optional[Dict[str, Any]] = None


# ==================== Device fingerprint catalog ====================

class DeviceDbPack(BaseModel):
    """单个国家/标签硬件指纹 SQLite 包"""
    id: str
    origin_name: str = ""
    stored_name: str = ""
    alias: str
    country: Optional[str] = None
    country_name: Optional[str] = None
    enabled: bool = True
    source: str = Field(default="upload", description="upload / generated / imported")
    sample_count: int = 0
    stats: Dict[str, Any] = Field(default_factory=dict)
    quality: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    generated: Optional[Dict[str, Any]] = None


class DeviceDbListResponse(BaseModel):
    success: bool = True
    message: str = ""
    total_count: int = 0
    is_loaded: bool = False
    sample_models: List[str] = Field(default_factory=list)
    pack_count: int = 0
    enabled_packs: int = 0
    disabled_packs: int = 0
    active_countries: List[str] = Field(default_factory=list)
    packs: List[DeviceDbPack] = Field(default_factory=list)
    supported_countries: List[Dict[str, str]] = Field(default_factory=list)


class DeviceDbPackResponse(BaseModel):
    success: bool
    message: str = ""
    pack: Optional[DeviceDbPack] = None


class DeviceDbUpdateRequest(BaseModel):
    alias: Optional[str] = Field(default=None, description="展示别名，如 智利安装300.db")
    country: Optional[str] = Field(default=None, description="绑定国家 ISO-2，如 cl / id")
    enabled: Optional[bool] = None


class DeviceDbToggleRequest(BaseModel):
    enabled: bool = True


class DeviceDbGenerateRequest(BaseModel):
    country: str = Field(..., description="目标国家 ISO-2，如 cl / id / in")
    count: int = Field(default=300, ge=10, le=5000, description="合成样本条数")
    alias: Optional[str] = Field(default=None, description="生成后的展示别名")
    enabled: bool = Field(default=True, description="生成后是否立即投入调度")
    brand_weights: Optional[Dict[str, int]] = Field(
        default=None,
        description="可选品牌权重覆盖: samsung/xiaomi/huawei/motorola/realme/vivo/oppo/other",
    )
    seed: Optional[int] = Field(default=None, description="可选随机种子，便于复现实验")


class PushTokenVaultItem(BaseModel):
    id: str
    token_preview: Optional[str] = None
    reghelp_task_id: Optional[str] = None
    provider: Optional[str] = None
    app_name: Optional[str] = None
    app_device: Optional[str] = None
    app_type: Optional[str] = None
    source_task_id: Optional[str] = None
    use_count: int = 0
    status: str = "available"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    last_outcome: Optional[str] = None
    last_lease_task_id: Optional[str] = None


class PushTokenVaultSummary(BaseModel):
    total: int = 0
    available: int = 0
    unused: int = 0
    used_once: int = 0
    reusable: int = 0
    consumed: int = 0
    refunded: int = 0


class PushTokenVaultListResponse(BaseModel):
    success: bool = True
    summary: PushTokenVaultSummary = Field(default_factory=PushTokenVaultSummary)
    items: List[PushTokenVaultItem] = Field(default_factory=list)
    reuse_enabled: bool = False
    reuse_max_uses: int = 2
    save_issued: bool = True


class PushTokenVaultPurgeRequest(BaseModel):
    refunded: bool = True
    consumed: bool = True
    exhausted: bool = False


class PushTokenVaultActionResponse(BaseModel):
    success: bool
    message: str = ""
    deleted: int = 0
    summary: Optional[PushTokenVaultSummary] = None
