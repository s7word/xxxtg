import asyncio
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, File, HTTPException, Query, UploadFile, BackgroundTasks

from backend.app.config import ConfigManager, SESSIONS_DIR
from backend.app.models.schemas import (
    AppConfigModel,
    TestApiResponse,
    PhonePrecheckStatusResponse,
    BannedPhonesCacheStatusResponse,
    RegisterTaskRequest,
    RegisterTaskResponse,
    BatchRegisterRequest,
    BatchRegisterResponse,
    BatchStatusResponse,
    TaskStatusResponse,
    EgressRelayConfig,
    VaultAccountListResponse,
    VaultUploadResponse,
    ApplyVaultCredentialsRequest,
    ApplyVaultCredentialsResponse,
    TelegramAppsStartRequest,
    TelegramAppsSubmitCodeRequest,
    TelegramAppsApplyRequest,
    TelegramAppsJobResponse,
    TelegramAppsJobListResponse,
    ProxySellerListResponse,
    ProxySellerAutoSelectRequest,
    ProxySellerAutoSelectResponse,
    ProxySellerTestAllRequest,
    ProxySellerTestAllResponse,
    CustomProxyListResponse,
    CustomProxyImportRequest,
    CustomProxyImportResponse,
    CustomProxyTestAllRequest,
    CustomProxyTestAllResponse,
    CustomProxySetFallbackRequest,
    CustomProxySetFallbackResponse,
    CustomProxyDeleteRequest,
    CustomProxyDeleteResponse,
    CustomProxyUpdateItemRequest,
    CustomProxyUpdateItemResponse,
    ToggleVaultProbeRequest,
    ToggleVaultProbeResponse,
    DeviceDbGenerateRequest,
    DeviceDbListResponse,
    DeviceDbPackResponse,
    DeviceDbToggleRequest,
    DeviceDbUpdateRequest,
    SmsAvailableCountriesResponse,
)
from backend.app.services.device_profile import DeviceProfileManager
from backend.app.services.device_db_manager import DeviceDbManager
from backend.app.services.device_generator import generate_country_db, list_supported_countries
from backend.app.services.vaksms import VakSmsService
from backend.app.services.grizzlysms import (
    GrizzlySmsService,
    PROVIDER_LABEL as GRIZZLY_PROVIDER_LABEL,
    resolve_grizzly_country_id,
)
from backend.app.services.fivesim import (
    FiveSimService,
    PROVIDER_LABEL as FIVESIM_PROVIDER_LABEL,
    resolve_fivesim_country,
    resolve_country_iso2 as fivesim_resolve_iso2,
    parse_fivesim_price_payload,
)
from backend.app.services.sms_stock_service import (
    SmsStockService,
    normalize_sms_provider,
)
from backend.app.services.antisafety import AntiSafetyService
from backend.app.services.reghelp import RegHelpService
from backend.app.services.attestation_urls import sanitize_provider_urls
from backend.app.services.proxyseller import ProxySellerService
from backend.app.services.proxy_manager import (
    custom_pool_summary,
    delete_custom_proxies,
    find_custom_proxy,
    import_proxy_text_async,
    list_custom_proxies,
    probe_custom_proxies,
    update_custom_proxy_item,
)
from backend.app.services.registrar import RegistrationTaskManager, RegistrationOrchestrator
from backend.app.services.phone_precheck import PhonePrecheckService
from backend.app.services.banned_phones import BannedPhonesCache
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


def _device_db_list_payload(message: str = "") -> DeviceDbListResponse:
    stats = DeviceDbManager.aggregate_stats()
    return DeviceDbListResponse(
        success=True,
        message=message,
        supported_countries=list_supported_countries(),
        **stats,
    )


@router.get("/device-dbs", response_model=DeviceDbListResponse, summary="列出已持久化的多国家硬件指纹包")
async def list_device_dbs():
    DeviceDbManager.ensure_ready()
    return _device_db_list_payload("已载入硬件指纹 & 拓扑库目录")


@router.get("/device-dbs/{pack_id}", response_model=DeviceDbPackResponse, summary="获取单个硬件指纹包详情与解析统计")
async def get_device_db(pack_id: str):
    pack = DeviceDbManager.get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="未找到指定的硬件指纹包")
    return DeviceDbPackResponse(success=True, message="ok", pack=pack)


@router.get("/device-dbs/{pack_id}/stats", summary="重新解析并返回机型/SDK/语言/时区分布")
async def get_device_db_pack_stats(pack_id: str):
    try:
        pack = DeviceDbManager.refresh_stats(pack_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的硬件指纹包") from None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析失败: {exc}") from exc
    return {"success": True, "pack": pack, "stats": pack.get("stats") or {}}


@router.post("/device-dbs/upload", response_model=DeviceDbPackResponse, summary="上传 REGISTRATOR SQLite 硬件指纹包")
async def upload_device_db(
    file: UploadFile = File(..., description="REGISTRATOR 结构的 .db / .sqlite"),
    alias: Optional[str] = None,
    country: Optional[str] = None,
    enabled: bool = True,
):
    filename = file.filename or "device.db"
    if not filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(status_code=400, detail="仅接受 .db / .sqlite / .sqlite3")
    try:
        content = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取上传文件失败: {exc}") from exc
    try:
        pack = DeviceDbManager.import_bytes(
            filename,
            content,
            alias=alias,
            country=country,
            enabled=enabled,
        )
    except ValueError as exc:
        status = 413 if "过大" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"导入硬件指纹包失败: {exc}") from exc
    return DeviceDbPackResponse(
        success=True,
        message=f"已导入 {pack.get('alias')}（{pack.get('country_name') or pack.get('country') or '未标注国家'} / {pack.get('sample_count')} 条）",
        pack=pack,
    )


@router.patch("/device-dbs/{pack_id}", response_model=DeviceDbPackResponse, summary="重命名别名、调整国家或启停状态")
async def update_device_db(pack_id: str, req: DeviceDbUpdateRequest):
    try:
        pack = DeviceDbManager.update_pack(
            pack_id,
            alias=req.alias,
            country=req.country,
            enabled=req.enabled,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的硬件指纹包") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DeviceDbPackResponse(success=True, message="已更新硬件指纹包", pack=pack)


@router.post("/device-dbs/{pack_id}/toggle", response_model=DeviceDbPackResponse, summary="启用或停用某个硬件指纹包")
async def toggle_device_db(pack_id: str, req: DeviceDbToggleRequest):
    try:
        pack = DeviceDbManager.update_pack(pack_id, enabled=req.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的硬件指纹包") from None
    state = "启用" if pack.get("enabled") else "停用"
    return DeviceDbPackResponse(success=True, message=f"已{state} {pack.get('alias')}", pack=pack)


@router.delete("/device-dbs/{pack_id}", response_model=DeviceDbPackResponse, summary="删除硬件指纹包及其持久化文件")
async def delete_device_db(pack_id: str):
    try:
        pack = DeviceDbManager.delete_pack(pack_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的硬件指纹包") from None
    return DeviceDbPackResponse(success=True, message=f"已删除 {pack.get('alias')}", pack=pack)


@router.post("/device-dbs/generate", response_model=DeviceDbPackResponse, summary="按目标国家参数化合成一套合规硬件指纹库")
async def generate_device_db(req: DeviceDbGenerateRequest):
    try:
        pack = generate_country_db(
            country=req.country,
            count=req.count,
            alias=req.alias,
            enabled=req.enabled,
            brand_weights=req.brand_weights,
            seed=req.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"合成硬件指纹库失败: {exc}") from exc
    return DeviceDbPackResponse(
        success=True,
        message=f"已合成 {pack.get('alias')}（{pack.get('sample_count')} 条 / {pack.get('country')}）",
        pack=pack,
    )

# ==================== 1b. 接码平台实时有货拓扑 ====================
@router.get(
    "/sms/available-countries",
    response_model=SmsAvailableCountriesResponse,
    summary="动态获取接码平台当前有 Telegram 货的国家（按库存降序）",
)
async def list_sms_available_countries(
    provider: Optional[str] = Query(
        default=None,
        description="fivesim / grizzlysms / vaksms；默认读取系统当前 config.sms_provider",
    ),
    refresh: bool = Query(default=False, description="true 时绕过 90s 缓存强制刷新"),
):
    config = ConfigManager.get_instance().config
    resolved = normalize_sms_provider(provider or getattr(config, "sms_provider", None))
    try:
        snap = await SmsStockService.get_available_countries(
            provider=resolved,
            refresh=refresh,
            service="tg",
            config=config,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"接码平台库存发现失败: {exc}") from exc
    return SmsAvailableCountriesResponse(**snap.to_dict())


# ==================== 2. 服务探针与连通性审计 ====================
@router.post("/test/fivesim", response_model=TestApiResponse, summary="5SIM 接码平台余额与连通性探针")
async def test_fivesim(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.fivesim_api_key
    country = (payload or {}).get("country") or config.target_country

    svc = FiveSimService(api_key)
    try:
        from backend.app.services.vaksms import format_no_number_message

        profile = await svc.get_profile()
        balance = float(profile.get("balance") or 0)
        stock = 0
        prices = None
        country_slug = None
        iso = None
        ref_cost = None
        try:
            country_slug = resolve_fivesim_country(country)
            iso = fivesim_resolve_iso2(country)
            prices = await svc.get_prices(country=country_slug, product="telegram")
            rows = parse_fivesim_price_payload(prices, product="telegram", country=country_slug)
            if rows:
                stock = int(rows[0].get("stock") or 0)
                ref_cost = rows[0].get("cost")
        except Exception as stock_exc:
            prices = {"error": str(stock_exc)}
        message = f"{FIVESIM_PROVIDER_LABEL} 鉴权与通信正常，余额 {balance}（RUB）"
        if profile.get("email"):
            message += f"，账号 {profile.get('email')}"
        if profile.get("rating") is not None:
            message += f"，评分 {profile.get('rating')}"
        if ref_cost is not None:
            message += f"，参考价 {ref_cost}"
        if int(stock or 0) <= 0 and not (isinstance(prices, dict) and prices.get("error")):
            message = format_no_number_message(iso or country)
        return TestApiResponse(
            success=True,
            service="5SIM",
            message=message,
            data={
                "balance": balance,
                "currency": "RUB",
                "email": profile.get("email"),
                "rating": profile.get("rating"),
                "ref_cost": ref_cost,
                "country": country,
                "country_slug": country_slug,
                "iso": iso,
                "telegram_stock": stock,
                "prices": prices,
                "no_number": int(stock or 0) <= 0,
                "provider": "fivesim",
                "endpoint": FiveSimService.BASE_URL,
                "profile": {
                    "id": profile.get("id"),
                    "email": profile.get("email"),
                    "balance": balance,
                    "rating": profile.get("rating"),
                    "frozen_balance": profile.get("frozen_balance"),
                },
            },
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="5SIM",
            message=f"5SIM 探针异常: {str(e)}",
        )
    finally:
        await svc.close()


@router.post("/test/vaksms", response_model=TestApiResponse, summary="带外遥测与挑战响应通道诊断探针")
async def test_vaksms(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.vak_sms_api_key
    country = (payload or {}).get("country") or config.target_country

    svc = VakSmsService(api_key)
    try:
        from backend.app.services.vaksms import format_no_number_message

        balance = await svc.get_balance()
        stock = await svc.get_stock_count(country=country, service="tg")
        message = "带外遥测通道鉴权与通信正常"
        if int(stock or 0) <= 0:
            message = format_no_number_message(country)
        return TestApiResponse(
            success=int(stock or 0) > 0,
            service="OOB-Telemetry",
            message=message,
            data={"balance": balance, "country": country, "telegram_stock": stock, "no_number": int(stock or 0) <= 0}
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="OOB-Telemetry",
            message=f"带外遥测通道探针异常: {str(e)}"
        )
    finally:
        await svc.close()


@router.post("/test/grizzlysms", response_model=TestApiResponse, summary="Grizzly SMS 接码平台余额与连通性探针")
async def test_grizzlysms(payload: Dict[str, Any] = None):
    config = ConfigManager.get_instance().config
    api_key = (payload or {}).get("api_key") or config.grizzly_sms_api_key
    country = (payload or {}).get("country") or config.target_country

    svc = GrizzlySmsService(api_key)
    try:
        from backend.app.services.vaksms import format_no_number_message

        balance = await svc.get_balance()
        stock = 0
        prices = None
        country_id = None
        try:
            country_id = resolve_grizzly_country_id(country)
            prices = await svc.get_prices(country=country_id, service="tg")
            stock = svc._stock_from_prices(prices, country_id, "tg")
        except Exception as stock_exc:
            prices = {"error": str(stock_exc)}
        ref_cost = None
        if isinstance(prices, dict) and country_id is not None:
            bucket = prices.get(str(country_id)) or prices.get(country_id)
            if isinstance(bucket, dict):
                node = bucket.get("tg") or bucket
                if isinstance(node, dict) and node.get("cost") is not None:
                    try:
                        ref_cost = float(node.get("cost"))
                    except (TypeError, ValueError):
                        ref_cost = None
        # Grizzly 账户可能是 USD(840) 或 RUB；小额参考价按美元结算账户处理。
        currency = "USD" if ref_cost is not None and 0 < ref_cost <= 5 else "账户结算币种"
        message = f"{GRIZZLY_PROVIDER_LABEL} 鉴权与通信正常，余额 {balance}（{currency}）"
        if ref_cost is not None:
            message += f"，参考价 {ref_cost}"
        if int(stock or 0) <= 0 and not (isinstance(prices, dict) and prices.get("error")):
            message = format_no_number_message(country)
        return TestApiResponse(
            success=True,
            service="Grizzly-SMS",
            message=message,
            data={
                "balance": balance,
                "currency": currency,
                "ref_cost": ref_cost,
                "country": country,
                "country_id": country_id,
                "telegram_stock": stock,
                "prices": prices,
                "no_number": int(stock or 0) <= 0,
                "provider": "grizzlysms",
                "endpoint": GrizzlySmsService.BASE_URL,
            },
        )
    except Exception as e:
        return TestApiResponse(
            success=False,
            service="Grizzly-SMS",
            message=f"Grizzly SMS 探针异常: {str(e)}",
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


def _proxy_seller_service(api_key: Optional[str] = None) -> ProxySellerService:
    config = ConfigManager.get_instance().config
    key = (api_key or config.proxy_seller_key or "").strip()
    # API Key 缺失或被 IP 白名单拦截时，服务仍可回退到内置静态住宅池。
    return ProxySellerService(key)


def _available_countries(proxies: List[Dict[str, Any]]) -> List[str]:
    codes = []
    seen = set()
    for item in proxies:
        label = (item.get("country_code") or item.get("country_alpha3") or item.get("country") or "").upper()
        if label and label not in seen:
            seen.add(label)
            codes.append(label)
    return codes


@router.get("/proxy-seller/proxies", response_model=ProxySellerListResponse, summary="检索 Proxy-Seller 区域代理池")
async def list_proxy_seller_proxies(country: Optional[str] = None, refresh: bool = False, api_key: Optional[str] = None):
    """列出账户下全部或指定区域的代理，附带缓存健康状态与地理归属。"""
    svc = _proxy_seller_service(api_key)
    try:
        proxies = await svc.get_proxy_list(country=country, refresh=refresh)
        all_items = await svc.get_proxy_list(country=None, refresh=False)
        meta = svc.cache_meta()
        scope = (country or "ALL").upper()
        return ProxySellerListResponse(
            success=True,
            message=f"成功检索到 {len(proxies)} 个 {scope} 区域出口中继跳点",
            country=country,
            total=len(proxies),
            proxies=proxies,
            cached=bool(meta.get("cached")) and not refresh,
            cache_age_seconds=meta.get("cache_age_seconds"),
            available_countries=_available_countries(all_items),
        )
    except Exception as e:
        return ProxySellerListResponse(
            success=False,
            message=f"检索 Proxy-Seller 代理池失败: {e}",
            country=country,
            total=0,
            proxies=[],
        )
    finally:
        await svc.close()


@router.post("/proxy-seller/auto-select", response_model=ProxySellerAutoSelectResponse, summary="按目标国家自动挑选并可选写入后备代理")
async def auto_select_proxy_seller(req: ProxySellerAutoSelectRequest):
    """给定 target_country，自动挑选该区域最佳/可用代理；可一键应用为 fallback_proxy。"""
    config_mgr = ConfigManager.get_instance()
    svc = _proxy_seller_service(req.api_key)
    try:
        target = (req.target_country or config_mgr.config.target_country or "").strip()
        selection = await svc.select_best_proxy(
            target_country=target,
            probe=req.probe,
            allow_fallback=req.allow_fallback,
            refresh=req.refresh,
        )
        applied = False
        fallback = None
        proxy = selection.get("proxy")
        if req.apply_fallback and proxy:
            current = config_mgr.config.model_dump()
            current["fallback_proxy"] = {
                "proxy_type": proxy.get("proxy_type") or "socks5",
                "addr": proxy.get("addr"),
                "port": int(proxy.get("port")),
                "username": proxy.get("username"),
                "password": proxy.get("password"),
            }
            saved = config_mgr.save_config(AppConfigModel(**current))
            fallback = saved.fallback_proxy
            applied = True
            selection["message"] = (
                f"{selection.get('message')}；已一键写入 fallback_proxy "
                f"{proxy.get('addr')}:{proxy.get('port')}"
            )
        return ProxySellerAutoSelectResponse(
            success=bool(selection.get("success")),
            message=selection.get("message") or "未匹配到可用代理",
            matched=bool(selection.get("matched")),
            fallback_used=bool(selection.get("fallback_used")),
            applied=applied,
            target_country=target,
            source=selection.get("source"),
            hint=selection.get("hint"),
            proxy=proxy,
            fallback_proxy=fallback,
        )
    except Exception as e:
        return ProxySellerAutoSelectResponse(
            success=False,
            message=f"自动挑选区域代理失败: {e}",
            target_country=req.target_country,
        )
    finally:
        await svc.close()


@router.post("/proxy-seller/test-all", response_model=ProxySellerTestAllResponse, summary="批量测活 Proxy-Seller 代理出口")
async def test_all_proxy_seller(req: ProxySellerTestAllRequest):
    """批量测试连通性，并回写每个节点的出口 IP / 国家。"""
    svc = _proxy_seller_service(req.api_key)
    try:
        result = await svc.test_all(
            country=req.country,
            refresh=req.refresh,
            limit=req.limit,
            concurrency=req.concurrency,
        )
        return ProxySellerTestAllResponse(**result)
    except Exception as e:
        return ProxySellerTestAllResponse(
            success=False,
            message=f"批量测活失败: {e}",
            country=req.country,
        )
    finally:
        await svc.close()


# ==================== 2b. 自定义代理池 (手动粘贴导入) ====================
@router.get("/proxy/custom-list", response_model=CustomProxyListResponse, summary="获取已保存的自建代理列表")
async def list_custom_proxy_pool(country: Optional[str] = None):
    items = list_custom_proxies(country=country)
    summary = custom_pool_summary(country)
    config = ConfigManager.get_instance().config
    scope = (country or "ALL").upper()
    return CustomProxyListResponse(
        success=True,
        message=f"自建代理池共 {summary['total']} 条，{scope} 匹配 {len(items)} 条，{summary['healthy']} 条已测通",
        total=len(items),
        healthy=sum(1 for item in items if item.get("healthy") is True),
        country=country,
        countries=summary.get("countries") or [],
        proxies=items,
        fallback_proxy=config.fallback_proxy,
        role_counts=summary.get("roles") or {},
    )


@router.post("/proxy/import-text", response_model=CustomProxyImportResponse, summary="批量解析并导入粘贴的代理文本")
async def import_custom_proxy_text(req: CustomProxyImportRequest):
    if not (req.text or "").strip():
        return CustomProxyImportResponse(success=False, message="代理文本为空")
    try:
        result = await import_proxy_text_async(
            req.text,
            probe=req.probe,
            replace=req.replace,
            default_scheme=req.default_protocol or "socks5",
            default_country=req.default_country,
            default_role=req.default_role or "all",
            concurrency=req.concurrency,
        )
        return CustomProxyImportResponse(**result)
    except Exception as exc:
        return CustomProxyImportResponse(success=False, message=f"导入自建代理失败: {exc}")


@router.post("/proxy/test-all", response_model=CustomProxyTestAllResponse, summary="对自建代理池进行并发测活")
async def test_all_custom_proxies(req: CustomProxyTestAllRequest = None):
    payload = req or CustomProxyTestAllRequest()
    try:
        result = await probe_custom_proxies(
            persist=True,
            concurrency=payload.concurrency,
            limit=payload.limit,
        )
        return CustomProxyTestAllResponse(**result)
    except Exception as exc:
        return CustomProxyTestAllResponse(success=False, message=f"自建代理池测活失败: {exc}")


@router.post("/proxy/set-fallback", response_model=CustomProxySetFallbackResponse, summary="将某个自建代理设为全局 fallback_proxy")
async def set_custom_proxy_fallback(req: CustomProxySetFallbackRequest):
    target = find_custom_proxy(
        proxy_id=req.proxy_id,
        addr=req.addr,
        port=req.port,
        username=req.username,
    )
    if not target:
        return CustomProxySetFallbackResponse(success=False, message="未找到指定的自建代理")
    config_mgr = ConfigManager.get_instance()
    current = config_mgr.config.model_dump()
    current["fallback_proxy"] = {
        "proxy_type": target.get("proxy_type") or "socks5",
        "addr": target.get("addr"),
        "port": int(target.get("port")),
        "username": target.get("username"),
        "password": target.get("password"),
    }
    saved = config_mgr.save_config(AppConfigModel(**current))
    return CustomProxySetFallbackResponse(
        success=True,
        message=f"已将 {target.get('addr')}:{target.get('port')} 设为当前后备代理",
        proxy=target,
        fallback_proxy=saved.fallback_proxy,
    )


@router.post("/proxy/update-item", response_model=CustomProxyUpdateItemResponse, summary="修改单个自建代理的角色、绑定国家与协议")
async def update_custom_proxy(req: CustomProxyUpdateItemRequest):
    if not req.proxy_id and not (req.addr and req.port):
        return CustomProxyUpdateItemResponse(success=False, message="请指定 proxy_id 或 addr+port")
    result = update_custom_proxy_item(
        proxy_id=req.proxy_id,
        addr=req.addr,
        port=req.port,
        username=req.username,
        role=req.role,
        assigned_country=req.assigned_country,
        clear_assigned_country=req.clear_assigned_country,
        proxy_type=req.proxy_type,
        country=req.country,
        country_code=req.country_code,
    )
    return CustomProxyUpdateItemResponse(**result)


@router.delete("/proxy/delete", response_model=CustomProxyDeleteResponse, summary="删除指定自建代理或清空自建代理池")
async def delete_custom_proxy(req: CustomProxyDeleteRequest):
    if not req.clear_all and not req.proxy_id and not (req.addr and req.port):
        return CustomProxyDeleteResponse(success=False, message="请指定 proxy_id / addr+port，或设置 clear_all=true")
    result = delete_custom_proxies(
        proxy_id=req.proxy_id,
        addr=req.addr,
        port=req.port,
        username=req.username,
        clear_all=req.clear_all,
    )
    return CustomProxyDeleteResponse(**result)


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
        proxy_override=proxy_dict,
        set_2fa=req.set_2fa,
        proxy_id=req.proxy_id,
        proxy_mode=req.proxy_mode,
        sms_provider=req.sms_provider,
        max_price=req.max_price,
    )

    return RegisterTaskResponse(
        task_id=task_id,
        status="pending",
        message="虚拟节点引导与协议握手任务已提交后台编排流水线"
    )

@router.post("/register/batch", response_model=BatchRegisterResponse, summary="并发批量触发边缘节点引导任务")
@router.post("/provision/batch", response_model=BatchRegisterResponse, summary="并发批量触发边缘节点引导任务 (学术规范路径)")
async def start_batch_registration(req: BatchRegisterRequest, background_tasks: BackgroundTasks):
    manager = RegistrationTaskManager.get_instance()
    batch_id, task_ids = manager.create_batch(
        count=req.count,
        concurrency=req.concurrency,
        country=req.country,
        app_type=req.app_type,
    )
    proxy_dict = req.proxy.model_dump() if req.proxy else None
    background_tasks.add_task(
        RegistrationOrchestrator.run_batch,
        batch_id=batch_id,
        task_ids=task_ids,
        country=req.country,
        app_type=req.app_type,
        proxy_override=proxy_dict,
        set_2fa=req.set_2fa,
        concurrency=req.concurrency,
        proxy_id=req.proxy_id,
        proxy_mode=req.proxy_mode,
        sms_provider=req.sms_provider,
        max_price=req.max_price,
    )
    return BatchRegisterResponse(
        batch_id=batch_id,
        task_ids=task_ids,
        count=len(task_ids),
        concurrency=req.concurrency,
        status="pending",
        country=req.country,
        app_type=req.app_type,
        message=(
            f"已提交并发批量引导: {len(task_ids)} 个任务 / 并发度 {req.concurrency}"
            f"（batch_id={batch_id}）"
        ),
    )

@router.get("/register/batches", summary="获取并发批次列表")
@router.get("/provision/batches", summary="获取并发批次列表 (学术规范路径)")
async def list_batches():
    return RegistrationTaskManager.get_instance().list_batches()

@router.get("/register/batches/{batch_id}", response_model=BatchStatusResponse, summary="获取指定批次聚合状态")
@router.get("/provision/batches/{batch_id}", response_model=BatchStatusResponse, summary="获取指定批次聚合状态 (学术规范路径)")
async def get_batch_status(batch_id: str):
    batch = RegistrationTaskManager.get_instance().get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch

@router.get("/register/tasks", summary="获取节点任务队列列表")
@router.get("/provision/tasks", summary="获取节点任务队列列表 (学术规范路径)")
async def list_tasks(batch_id: Optional[str] = None):
    return RegistrationTaskManager.get_instance().list_tasks(batch_id=batch_id)

@router.get("/register/tasks/{task_id}", response_model=TaskStatusResponse, summary="获取指定节点状态机审计详情")
@router.get("/provision/tasks/{task_id}", response_model=TaskStatusResponse, summary="获取指定节点状态机审计详情 (学术规范路径)")
async def get_task_status(task_id: str):
    task = RegistrationTaskManager.get_instance().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get(
    "/phone-precheck/status",
    response_model=PhonePrecheckStatusResponse,
    summary="号码注册状态预检探测器就绪状态",
)
async def phone_precheck_status():
    return PhonePrecheckService.describe_status().to_dict()


@router.get(
    "/banned-phones/status",
    response_model=BannedPhonesCacheStatusResponse,
    summary="本地封禁号缓存与号段画像",
)
async def banned_phones_status():
    return BannedPhonesCache.describe_status().to_dict()

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
    "/vault/upload",
    response_model=VaultUploadResponse,
    summary="上传 .zip / .session / .json 并导入到凭证库",
)
async def upload_vault_accounts(file: UploadFile = File(..., description="账号压缩包或单个凭证文件")):
    """浏览器端一键导入账号：ZIP 安全解压到 lod_user/<zip名>/，单文件落到 lod_user/imports/。"""
    filename = file.filename or "upload.bin"
    try:
        content = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取上传文件失败: {exc}") from exc
    result = AccountVaultService.import_uploaded_bytes(filename, content)
    if not result.success:
        status = 413 if "过大" in result.message else 400
        raise HTTPException(status_code=status, detail=result.message)
    return result


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


@router.post(
    "/vault/accounts/toggle-probe",
    response_model=ToggleVaultProbeResponse,
    summary="开启或停用某个凭证库账号作为预检探测源",
)
async def toggle_vault_probe(req: ToggleVaultProbeRequest):
    result = AccountVaultService.toggle_probe(req.account_id, req.active)
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
