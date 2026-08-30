"""SMSBazaar 程序推送入口：公开 POST /hooks/smsall。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from backend.app.config import ConfigManager
from backend.app.services.registrar import RegistrationOrchestrator, RegistrationTaskManager
from backend.app.services.smsall_webhook import attach_batch, ingest, resolve_secret, verify_request

hooks_router = APIRouter(tags=["smsall-webhook"])
logger = logging.getLogger("SmsallHooks")


def start_country_batch(
    country: str,
    count: int,
    concurrency: int,
    background_tasks: BackgroundTasks,
    config,
    max_number_attempts: Optional[int] = None,
    max_price: Optional[float] = None,
    sms_provider: Optional[str] = None,
    no_number_retries: Optional[int] = None,
    sniper: bool = False,
) -> Dict[str, Any]:
    """用当前全局接码源 / 代理策略给某国开一批注册；max_number_attempts>1 即走猎号。"""
    manager = RegistrationTaskManager.get_instance()
    proxy_mode = "auto" if getattr(config, "use_proxy_seller_auto", False) else "custom_pool"
    sms_provider = sms_provider or getattr(config, "sms_provider", None)
    if max_price is None:
        max_price = getattr(config, "sms_max_price", None)
    app_type = getattr(config, "active_app_type", None)
    set_2fa = getattr(config, "auto_set_2fa", None)
    safe_count = max(1, min(10, int(count or 1)))
    safe_conc = max(1, min(10, int(concurrency or safe_count)))
    safe_conc = min(safe_conc, safe_count)

    # 猎号联合上限（hunt_max_total_leases，默认 200）会裁剪 count × attempts，
    # 裁剪结果必须原样打日志，不能悄悄改小用户配的数字。
    budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
        config,
        count=safe_count,
        max_number_attempts=max_number_attempts,
    )
    attempts = int(budget["max_number_attempts"])
    if budget.get("clamped") or budget.get("rejected"):
        logger.warning("%s 批次租号预算：%s", str(country or "").upper(), budget["message"])
    elif attempts > 1:
        logger.info("%s 批次租号预算：%s", str(country or "").upper(), budget["message"])

    batch_id, task_ids = manager.create_batch(
        count=safe_count,
        concurrency=safe_conc,
        country=country,
        app_type=app_type,
    )
    background_tasks.add_task(
        RegistrationOrchestrator.run_batch,
        batch_id=batch_id,
        task_ids=task_ids,
        country=country,
        app_type=app_type,
        set_2fa=set_2fa,
        concurrency=safe_conc,
        proxy_mode=proxy_mode,
        sms_provider=sms_provider,
        max_price=max_price,
        max_number_attempts=attempts,
        no_number_retries=no_number_retries,
    )
    return {
        "batch_id": batch_id,
        "task_ids": list(task_ids),
        "count": len(task_ids),
        "concurrency": safe_conc,
        "country": country,
        "sms_provider": sms_provider,
        "proxy_mode": proxy_mode,
        "app_type": app_type,
        "max_number_attempts": attempts,
        "max_price": max_price,
        "sniper": bool(sniper),
        "planned_leases": budget["planned_leases"],
        "budget_message": budget["message"],
        "budget_clamped": bool(budget.get("clamped") or budget.get("rejected")),
    }


def _schedule_launches(launches, background_tasks: BackgroundTasks, config) -> None:
    for item in launches:
        country = item.get("country")
        sniper = bool(item.get("sniper"))
        started = start_country_batch(
            country=country,
            count=int(item.get("count") or 3),
            concurrency=int(item.get("concurrency") or item.get("count") or 3),
            background_tasks=background_tasks,
            config=config,
            max_number_attempts=item.get("max_number_attempts"),
            max_price=item.get("max_price"),
            sniper=sniper,
        )
        item["batch_id"] = started["batch_id"]
        item["task_ids"] = list(started["task_ids"])
        item["max_number_attempts"] = started["max_number_attempts"]
        item["planned_leases"] = started["planned_leases"]
        attach_batch(
            event_id=item.get("event_id"),
            country=country,
            batch_id=started["batch_id"],
            task_ids=started["task_ids"],
            source="sniper" if sniper else "auto",
        )


@hooks_router.post("/hooks/smsall", summary="SMSBazaar Telegram 补货/新上架 Webhook")
async def receive_smsall_alert(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
    x_smsall_signature: Optional[str] = Header(default=None),
    x_smsall_schema: Optional[str] = Header(default=None),
    x_smsall_sniper: Optional[str] = Header(default=None),
    x_smsall_priority: Optional[str] = Header(default=None),
):
    raw = await request.body()
    config = ConfigManager.get_instance().config
    secret = resolve_secret(config)
    if not verify_request(raw, authorization or "", x_smsall_signature or "", secret):
        return JSONResponse({"detail": "bad signature"}, status_code=401)

    payload: Dict[str, Any] = {}
    if raw:
        import json as json_lib

        try:
            parsed = json_lib.loads(raw.decode("utf-8"))
        except Exception:
            return JSONResponse({"detail": "invalid json"}, status_code=400)
        if isinstance(parsed, dict):
            payload = parsed

    result = ingest(payload, config, headers={
        "x-smsall-sniper": x_smsall_sniper or "",
        "x-smsall-priority": x_smsall_priority or "",
    })
    launches = result.get("launches") or []
    if launches:
        _schedule_launches(launches, background_tasks, config)
    # 文档：2xx 即可；返回摘要方便联调，SMSBazaar 不依赖响应体。
    return {
        "ok": True,
        "schema": x_smsall_schema or result.get("schema"),
        "accepted": True,
        "launched": len(launches),
        "sniper_launched": sum(1 for item in launches if item.get("sniper")),
        "launches": [
            {
                "country": item.get("country"),
                "batch_id": item.get("batch_id"),
                "count": item.get("count"),
                "price_usd": item.get("price_usd"),
                "event_type": item.get("event_type"),
                "provider": item.get("provider"),
                "sniper": bool(item.get("sniper")),
                "max_number_attempts": item.get("max_number_attempts"),
                "planned_leases": item.get("planned_leases"),
            }
            for item in launches
        ],
    }


@hooks_router.post("/hooks/smsall/", include_in_schema=False)
async def receive_smsall_alert_slash(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
    x_smsall_signature: Optional[str] = Header(default=None),
    x_smsall_schema: Optional[str] = Header(default=None),
    x_smsall_sniper: Optional[str] = Header(default=None),
    x_smsall_priority: Optional[str] = Header(default=None),
):
    return await receive_smsall_alert(
        request,
        background_tasks,
        authorization,
        x_smsall_signature,
        x_smsall_schema,
        x_smsall_sniper,
        x_smsall_priority,
    )
