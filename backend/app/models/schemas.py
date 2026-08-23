from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class EgressRelayConfig(BaseModel):
    """出口中继与网络传输网关配置 (Multipath Egress Relay Gateway)"""
    proxy_type: str = Field(default="socks5", description="传输协议: socks5 / http / direct")
    addr: str = Field(default="127.0.0.1", description="中继节点主机地址或 IP")
    port: int = Field(default=10808, description="中继节点监听端口")
    username: Optional[str] = Field(default=None, description="中继鉴权用户凭证")
    password: Optional[str] = Field(default=None, description="中继鉴权口令凭证")

# 保持向前兼容别名
ProxyConfig = EgressRelayConfig


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
        default=["https://api.antisafety.net"],
        description="AntiSafety Push Token 网关候选地址列表 (按序尝试并自动容灾切换)"
    )
    antisafety_reporting_base_urls: List[str] = Field(
        default=["https://reporting.antisafety.net"],
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
        default=["https://api.reghelp.net"],
        description="REGHelp Key API 候选网关地址列表 (按序尝试并自动容灾切换)"
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


class RegisterTaskRequest(BaseModel):
    """节点引导与握手仿真任务请求"""
    country: Optional[str] = Field(default=None, description="目标拓扑区域代码")
    app_type: Optional[str] = Field(default=None, description="指定端点架构模板")
    session_name: Optional[str] = Field(default=None, description="自定义密码学上下文快照命名")
    proxy: Optional[EgressRelayConfig] = Field(default=None, description="自定义覆盖中继网关")
    set_2fa: Optional[bool] = Field(default=None, description="覆盖二级保护凭证设定")

# 学术化别名
NodeProvisioningRequest = RegisterTaskRequest


class RegisterTaskResponse(BaseModel):
    """节点引导任务创建响应"""
    task_id: str
    status: str
    message: str

NodeProvisioningResponse = RegisterTaskResponse


class TaskStatusResponse(BaseModel):
    """节点状态机生命周期与审计追踪响应"""
    task_id: str
    status: str  # pending, running, success, failed
    phone: Optional[str] = Field(default=None, description="绑定的端点通信句柄")
    user_id: Optional[int] = Field(default=None, description="协商确认的分布式节点 UID")
    error: Optional[str] = None
    logs: List[str] = []
    created_at: str
    updated_at: str

NodeTaskStatusResponse = TaskStatusResponse
