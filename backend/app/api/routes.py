import asyncio
import os
from pathlib import Path
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, BackgroundTasks

from backend.app.config import ConfigManager, SESSIONS_DIR
from backend.app.models.schemas import (
    AppConfigModel,
    TestApiResponse,
    RegisterTaskRequest,
    RegisterTaskResponse,
    TaskStatusResponse,
    EgressRelayConfig,
    VaultAccountListResponse,
    ApplyVaultCredentialsRequest,
    ApplyVaultCredentialsResponse,
    TelegramAppsStartRequest,
    TelegramAppsSubmitCodeRequest,
    TelegramAppsApplyRequest,
    TelegramAppsJobResponse,
    TelegramAppsJobListResponse,
)
from backend.app.services.device_profile import DeviceProfileManager
from backend.app.services.vaksms import VakSmsService
from backend.app.services.antisafety import AntiSafetyService
from backend.app.services.reghelp import RegHelpService
from backend.app.services.attestation_urls import sanitize_provider_urls
from backend.app.services.proxyseller import ProxySellerService
from backend.app.services.registrar import RegistrationTaskManager, RegistrationOrchestrator
from backend.app.services.account_vault import AccountVaultService
from backend.app.services.telegram_apps import TelegramAppsHelper, TelegramAppsJobManager

router = APIRouter(prefix="/api")

# ==================== 1. 系统配置与硬件拓扑库 ====================
@router.get("/config", response_model=AppConfigModel, summary="获取全局仿真配置")
async def get_config():
    return ConfigManager.get_instance().config

@router.post("/config", response_model=AppConfigModel, summary="更新并持久化全局仿真配置")
async def update_config(new_config: AppConfigModel):
    return ConfigManager.get_instance().save_config(new_config)

@router.get("/device-profiles", summary="获取所有端点环境与特征模板")
async def list_device_profiles():
    return DeviceProfileManager.get_all_profiles()

@router.get("/device-db-stats", summary="获取真机硬件拓扑指纹数据库统计")
async def get_device_db_stats():
    return DeviceProfileManager.get_db_stats()

# ==================== 2. 服务探针与连通性审计 ====================
@router.post("/test/vaksms", response_model=TestApiResponse, summary="带外遥测与挑战响应通道诊断探针")
async def test_vaksms(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.vak_sms_api_key
    country = (payload or {}).get("country") or config.target_country

    svc = VakSmsService(api_key)
    try:
        balance = await svc.get_balance()
        stock = await svc.get_stock_count(country=country, service="tg")
        return TestApiResponse(
            success=True,
            service="OOB-Telemetry",
            message="带外遥测通道鉴权与通信正常",
            data={"balance": balance, "country": country, "telegram_stock": stock}
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="OOB-Telemetry",
            message=f"带外遥测通道探针异常: {str(e)}"
        )
    finally:
        await svc.close()

@router.post("/test/antisafety", response_model=TestApiResponse, summary="Attestation 凭证网关诊断探针")
async def test_antisafety(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.antisafety_api_key
    aid = (payload or {}).get("aid") or config.antisafety_aids.get(config.active_app_type)

    svc = AntiSafetyService(
        api_key,
        api_bases=sanitize_provider_urls(
            (payload or {}).get("base_urls") or config.antisafety_base_urls,
            "antisafety",
        ),
        reporting_bases=sanitize_provider_urls(
            (payload or {}).get("reporting_base_urls") or config.antisafety_reporting_base_urls,
            "antisafety_reporting",
        ),
        connect_timeout=config.antisafety_connect_timeout,
        total_timeout=config.antisafety_total_timeout
    )
    try:
        res = await svc.check_phone_history("12025550123", aid=aid)
        if res and res.get("status") == "ok":
            return TestApiResponse(
                success=True,
                service="Attestation-Gateway",
                message="Attestation 网关鉴权成功，对应 AID 实例已激活",
                data=res
            )
        return TestApiResponse(
            success=False,
            service="Attestation-Gateway",
            message=f"Attestation 网关响应状态异常: {res}"
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="Attestation-Gateway",
            message=f"Attestation 凭证网关测试失败: {str(e)}"
        )
    finally:
        await svc.close()

@router.post("/test/reghelp", response_model=TestApiResponse, summary="REGHelp 高可用 Attestation/Push 凭证网关诊断探针")
async def test_reghelp(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.reghelp_api_key
    base_urls = sanitize_provider_urls(
        (payload or {}).get("base_urls") or config.reghelp_base_urls,
        "reghelp",
    )

    svc = RegHelpService(
        api_key,
        api_bases=base_urls,
        connect_timeout=config.reghelp_connect_timeout,
        total_timeout=config.reghelp_total_timeout
    )
    try:
        data = await svc.get_balance()
        if data.get("status") == "success" or "balance" in data:
            return TestApiResponse(
                success=True,
                service="REGHelp-Gateway",
                message=f"REGHelp 网关鉴权成功! 当前账户余额: {data.get('balance')} {data.get('currency', '')}".strip(),
                data=data
            )
        return TestApiResponse(
            success=False,
            service="REGHelp-Gateway",
            message=f"REGHelp 网关响应状态异常: {data}"
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="REGHelp-Gateway",
            message=f"REGHelp 网关探针测试失败: {str(e)}"
        )
    finally:
        await svc.close()

@router.post("/test/proxyseller", response_model=TestApiResponse, summary="多径中继出口网关池诊断")
async def test_proxyseller(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.proxy_seller_key
    country = (payload or {}).get("country") or config.target_country

    svc = ProxySellerService(api_key)
    try:
        proxies = await svc.get_proxy_list(country=country)
        return TestApiResponse(
            success=True,
            service="Multipath-Relay",
            message=f"成功检索到 {len(proxies)} 个活跃出口中继跳点",
            data={"proxies": proxies}
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="Multipath-Relay",
            message=f"多径中继网关检索失败: {str(e)}"
        )
    finally:
        await svc.close()

@router.post("/test/proxy-connectivity", response_model=TestApiResponse, summary="中继链路公网拓扑连通性探测")
async def test_proxy_connectivity(proxy_data: Dict[str, Any]):
    res = await ProxySellerService.test_proxy_connectivity(proxy_data)
    if res.get("success"):
        return TestApiResponse(
            success=True,
            service="Relay-Connectivity",
            message=f"中继链路握手成功! 出口 IP: {res.get('ip')} ({res.get('country')})",
            data=res
        )
    return TestApiResponse(
        success=False,
        service="Relay-Connectivity",
        message=f"中继链路探测失败: {res.get('error')}"
    )

# ==================== 3. 虚拟节点引导任务调度 ====================
@router.post("/register/start", response_model=RegisterTaskResponse, summary="触发边缘节点引导任务")
@router.post("/provision/start", response_model=RegisterTaskResponse, summary="触发边缘节点引导任务 (学术规范路径)")
async def start_registration(req: RegisterTaskRequest, background_tasks: BackgroundTasks):
    manager = RegistrationTaskManager.get_instance()
    task_id = manager.create_task()

    proxy_dict = req.proxy.model_dump() if req.proxy else None
    background_tasks.add_task(
        RegistrationOrchestrator.run_registration,
        task_id=task_id,
        country=req.country,
        app_type=req.app_type,
        proxy_override=proxy_dict
    )

    return RegisterTaskResponse(
        task_id=task_id,
        status="pending",
        message="虚拟节点引导与协议握手任务已提交后台编排流水线"
    )

@router.get("/register/tasks", summary="获取节点任务队列列表")
@router.get("/provision/tasks", summary="获取节点任务队列列表 (学术规范路径)")
async def list_tasks():
    return RegistrationTaskManager.get_instance().list_tasks()

@router.get("/register/tasks/{task_id}", response_model=TaskStatusResponse, summary="获取指定节点状态机审计详情")
@router.get("/provision/tasks/{task_id}", response_model=TaskStatusResponse, summary="获取指定节点状态机审计详情 (学术规范路径)")
async def get_task_status(task_id: str):
    task = RegistrationTaskManager.get_instance().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

# ==================== 4. 密码学上下文快照与节点资产 ====================
@router.get("/sessions", summary="获取已持久化的密码学上下文快照列表")
@router.get("/artifacts", summary="获取已持久化的密码学上下文快照列表 (学术规范路径)")
async def list_sessions():
    sessions = []
    if SESSIONS_DIR.exists():
        for file in SESSIONS_DIR.glob("*.session"):
            stat = file.stat()
            sessions.append({
                "filename": file.name,
                "size_kb": round(stat.st_size / 1024, 2),
                "created_at": datetime_from_timestamp(stat.st_ctime)
            })
    return sorted(sessions, key=lambda x: x["created_at"], reverse=True)

def datetime_from_timestamp(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# ==================== 5. 已有账号凭证库 (Account Vault) ====================
@router.get(
    "/vault/accounts",
    response_model=VaultAccountListResponse,
    summary="扫描 lod_user/ 与 data/sessions/ 中的已有账号凭证",
)
async def list_vault_accounts():
    return AccountVaultService.list_accounts()


@router.post(
    "/vault/accounts/apply",
    response_model=ApplyVaultCredentialsResponse,
    summary="将某个已有账号的 app_id/app_hash 一键写入全局配置",
)
async def apply_vault_account_credentials(req: ApplyVaultCredentialsRequest):
    result = AccountVaultService.apply_account_credentials(
        req.account_id,
        set_mode_custom=req.set_mode_custom,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return result


# ==================== 6. 开发者凭证申请助手 (my.telegram.org) ====================
@router.post(
    "/vault/apps/start",
    response_model=TelegramAppsJobResponse,
    summary="对指定已有账号发起 my.telegram.org 登录并申请/读取 api_id/api_hash",
)
async def start_telegram_apps_job(req: TelegramAppsStartRequest):
    if not req.account_id and not req.phone:
        raise HTTPException(status_code=400, detail="account_id 或 phone 至少提供一个")
    return await TelegramAppsHelper.start_job(
        account_id=req.account_id,
        phone=req.phone,
        auto_read_code=req.auto_read_code,
        app_title=req.app_title,
        app_shortname=req.app_shortname,
        apply_to_config=req.apply_to_config,
    )


@router.post(
    "/vault/apps/submit-code",
    response_model=TelegramAppsJobResponse,
    summary="手动提交 my.telegram.org 登录验证码并继续申请流程",
)
async def submit_telegram_apps_code(req: TelegramAppsSubmitCodeRequest):
    return await TelegramAppsHelper.submit_code(
        job_id=req.job_id,
        code=req.code,
        apply_to_config=req.apply_to_config,
    )


@router.get(
    "/vault/apps/jobs",
    response_model=TelegramAppsJobListResponse,
    summary="列出开发者凭证申请任务",
)
async def list_telegram_apps_jobs():
    manager = TelegramAppsJobManager.get_instance()
    return TelegramAppsJobListResponse(
        jobs=[manager.to_response(job) for job in manager.list_jobs()]
    )


@router.get(
    "/vault/apps/jobs/{job_id}",
    response_model=TelegramAppsJobResponse,
    summary="查询指定开发者凭证申请任务状态",
)
async def get_telegram_apps_job(job_id: str):
    manager = TelegramAppsJobManager.get_instance()
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Apps job not found")
    return manager.to_response(job)


@router.post(
    "/vault/apps/apply",
    response_model=ApplyVaultCredentialsResponse,
    summary="将某次申请任务得到的专属 api_id/api_hash 写入全局配置",
)
async def apply_telegram_apps_credentials(req: TelegramAppsApplyRequest):
    manager = TelegramAppsJobManager.get_instance()
    job = manager.get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Apps job not found")
    if not job.get("api_id") or not job.get("api_hash"):
        raise HTTPException(status_code=400, detail="Job has no api_id/api_hash yet")
    result = AccountVaultService.apply_raw_credentials(
        int(job["api_id"]),
        str(job["api_hash"]),
        set_mode_custom=req.set_mode_custom,
    )
    manager.update(req.job_id, applied_to_config=True)
    result.account_id = job.get("account_id")
    return result
