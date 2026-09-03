"""批次级代理槽位池：并发线程与目标国家代理 1:1 绑定，禁止跨区 silent fallback。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.app.models.schemas import normalize_proxy_mode
from backend.app.services.proxyseller import (
    ProxySellerService,
    format_proxy_endpoint,
    is_custom_proxy,
    is_resident_tg,
    is_static_residential,
    match_proxy_country,
    proxy_identity,
)

logger = logging.getLogger("ProxySlotPool")

_PROXY_ORIGIN_LABELS = {
    "custom_pool": "用户自建代理池",
    "resident_tg": "xxxtg 专用住宅列表",
    "static_residential": "内置静态住宅代理池",
    "regional": "Proxy-Seller API",
}


def _proxy_origin_label(proxy: Dict[str, Any]) -> str:
    if is_custom_proxy(proxy):
        return _PROXY_ORIGIN_LABELS["custom_pool"]
    if is_resident_tg(proxy):
        return _PROXY_ORIGIN_LABELS["resident_tg"]
    if is_static_residential(proxy):
        return _PROXY_ORIGIN_LABELS["static_residential"]
    return _PROXY_ORIGIN_LABELS["regional"]


class ProxyLeaseRegistry:
    """进程级：同一出口端点同时仅允许一个注册任务占用（1:1）。"""

    _instance: Optional["ProxyLeaseRegistry"] = None
    _instance_lock = asyncio.Lock()

    def __init__(self) -> None:
        self._leases: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> "ProxyLeaseRegistry":
        if cls._instance is None:
            async with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = ProxyLeaseRegistry()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def is_leased(self, proxy: Dict[str, Any]) -> bool:
        ident = proxy_identity(proxy)
        return ident in self._leases

    async def try_lease(self, proxy: Dict[str, Any], owner: str) -> bool:
        ident = proxy_identity(proxy)
        async with self._lock:
            if ident in self._leases:
                return False
            self._leases[ident] = owner
            return True

    async def release(self, proxy: Dict[str, Any], owner: str) -> None:
        ident = proxy_identity(proxy)
        async with self._lock:
            if self._leases.get(ident) == owner:
                del self._leases[ident]


class BatchProxySlotPool:
    """大小为并发度的可复用代理队列；活跃任务与出口 1:1，任务结束归还槽位。"""

    def __init__(self, country: str, proxies: List[Dict[str, Any]], batch_id: str) -> None:
        self.country = (country or "").lower()
        self.batch_id = batch_id
        self._capacity = len(proxies)
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        for item in proxies:
            self._queue.put_nowait(dict(item))

    @property
    def size(self) -> int:
        return self._capacity

    async def acquire(self, task_id: str) -> Dict[str, Any]:
        proxy = await self._queue.get()
        registry = await ProxyLeaseRegistry.get_instance()
        owner = f"{self.batch_id}:{task_id}"
        if not await registry.try_lease(proxy, owner):
            await self._queue.put(proxy)
            raise RuntimeError(
                f"代理 {format_proxy_endpoint(proxy)} 已被其它任务占用，1:1 槽位冲突"
            )
        return proxy

    async def release(self, proxy: Dict[str, Any], task_id: str) -> None:
        registry = await ProxyLeaseRegistry.get_instance()
        owner = f"{self.batch_id}:{task_id}"
        await registry.release(proxy, owner)
        await self._queue.put(dict(proxy))


async def _allocate_from_custom_pool(
    country: str,
    need: int,
    registry: ProxyLeaseRegistry,
) -> List[Dict[str, Any]]:
    from backend.app.services.proxy_manager import (
        custom_proxy_eligible_for_country,
        filter_proxies_by_role,
        list_custom_proxies,
        match_assigned_country,
        proxy_assigned_country,
    )

    items = filter_proxies_by_role(list_custom_proxies(), ("registration", "all"))
    if not items:
        return []
    if country:
        bound = [item for item in items if match_assigned_country(item, country)]
        fallback = [
            item for item in items
            if not proxy_assigned_country(item) and custom_proxy_eligible_for_country(item, country)
        ]
        regional = bound or fallback
    else:
        regional = list(items)

    picked: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in regional:
        ident = proxy_identity(item)
        if ident in seen or registry.is_leased(item):
            continue
        seen.add(ident)
        picked.append(dict(item))
        if len(picked) >= need:
            break
    return picked


async def _allocate_from_proxy_seller(
    country: str,
    need: int,
    api_key: str,
    registry: ProxyLeaseRegistry,
) -> List[Dict[str, Any]]:
    if not api_key or not country:
        return []

    svc = ProxySellerService(api_key)
    picked: List[Dict[str, Any]] = []
    try:
        ensure_fn = getattr(svc, "ensure_tg_resident_list", None)
        if callable(ensure_fn):
            try:
                ensured = await ensure_fn(country, create=True)
                if ensured.get("created") or ensured.get("proxies"):
                    invalidate = getattr(svc, "invalidate_cache", None)
                    if callable(invalidate):
                        invalidate()
            except Exception as exc:
                logger.warning("批次预分配 ensure_tg_resident_list 失败: %s", exc)

        regional = await svc.get_proxy_list(country=country, refresh=True, include_health=False)
        if not regional:
            return []

        candidates = svc._sort_candidates(regional)  # type: ignore[attr-defined]
        rotated = svc._rotate(country, candidates)  # type: ignore[attr-defined]
        seen: set[str] = set()
        for item in rotated:
            if not match_proxy_country(item, country):
                continue
            ident = proxy_identity(item)
            if ident in seen or registry.is_leased(item):
                continue
            seen.add(ident)
            picked.append(dict(item))
            if len(picked) >= need:
                break
    finally:
        await svc.close()
    return picked


async def prepare_batch_proxy_pool(
    *,
    batch_id: str,
    country: str,
    slots: int,
    config: Any,
    proxy_mode: str,
) -> Tuple[Optional[BatchProxySlotPool], int, List[str]]:
    """
    为批次预拉 ``slots`` 条同国代理（等于并发度，非任务总数）。
    返回 (池, 有效并发度, 日志行)。池为 None 表示沿用任务级旧逻辑。
    """
    mode = normalize_proxy_mode(proxy_mode)
    need = max(1, int(slots or 1))
    target = (country or "").lower()
    logs: List[str] = []
    registry = await ProxyLeaseRegistry.get_instance()

    if mode == "fallback":
        return None, need, logs

    picked: List[Dict[str, Any]] = []

    if mode in {"custom_pool", "explicit"}:
        picked = await _allocate_from_custom_pool(target, need, registry)

    if len(picked) < need and mode in {"auto", "custom_pool"}:
        api_key = str(getattr(config, "proxy_seller_key", "") or "").strip()
        if api_key:
            api_picked = await _allocate_from_proxy_seller(target, need, api_key, registry)
            seen = {proxy_identity(p) for p in picked}
            for item in api_picked:
                ident = proxy_identity(item)
                if ident in seen:
                    continue
                seen.add(ident)
                picked.append(item)
                if len(picked) >= need:
                    break

    if mode == "auto" and not picked:
        msg = (
            f"[代理槽位] 批次 {batch_id}: 目标区域 {target.upper()} 无法预分配任何同国代理，"
            "已禁止跨区 fallback；请补充 ZA 住宅列表/自建池或降低并发。"
        )
        logs.append(msg)
        return None, 0, logs

    if not picked and mode == "custom_pool":
        return None, need, logs

    effective = min(need, len(picked))
    pool_proxies = picked[:effective]
    origins = sorted({_proxy_origin_label(p) for p in pool_proxies})
    logs.append(
        f"[代理槽位] 批次 {batch_id}: 预分配 {effective}/{need} 条 {target.upper()} 同国代理"
        f"（来源: {' / '.join(origins)}），活跃任务与出口 1:1 绑定"
    )
    for idx, proxy in enumerate(pool_proxies, start=1):
        logs.append(
            f"[代理槽位] 槽 {idx}/{effective}: {_proxy_origin_label(proxy)} "
            f"{format_proxy_endpoint(proxy)}"
        )
    if effective < need:
        logs.append(
            f"[代理槽位] ⚠️ 同国代理仅 {effective} 条，批次并发由 {need} 降为 {effective}"
        )

    return BatchProxySlotPool(target, pool_proxies, batch_id), effective, logs


async def fail_batch_tasks_no_proxy(
    task_ids: List[str],
    manager: Any,
    logs: List[str],
) -> None:
    message = "批次启动失败：目标国家代理不足，已禁止跨区 fallback"
    for tid in task_ids:
        for line in logs:
            await manager.append_log(tid, line)
        await manager.append_log(tid, message)
        manager.update_task_status(tid, "failed", message=message)
