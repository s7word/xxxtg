"""批次级代理槽位池：并发线程与目标国家代理 1:1 绑定，禁止跨区 silent fallback。"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.models.schemas import normalize_proxy_mode
from backend.app.services.proxyseller import (
    RESIDENT_TG_PORT_CAP,
    ProxySellerService,
    apply_resident_session_tag,
    format_proxy_endpoint,
    is_custom_proxy,
    is_resident_tg,
    is_static_residential,
    match_proxy_country,
    mint_resident_session_tag,
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
        self._retired: set[str] = set()
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
        return ident in self._leases or ident in self._retired

    async def retire(self, proxy: Dict[str, Any]) -> None:
        ident = proxy_identity(proxy)
        async with self._lock:
            self._retired.add(ident)

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


def unique_proxy_ip_enabled(config: Any) -> bool:
    """读取单线不复用开关（config / 环境变量 / 标志文件）。"""
    if bool(getattr(config, "proxy_unique_ip_per_task", False)):
        return True
    if os.environ.get("EDGENODE_UNIQUE_PROXY_IP", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return True
    for flag in (Path("/app/data/.unique_proxy_ip"), Path("data/.unique_proxy_ip")):
        if flag.exists():
            return True
    return False


def compute_batch_proxy_demand(
    *,
    task_count: int,
    concurrency: int,
    planned_leases: int,
    attempts_per_task: int,
    proxy_max_uses: int,
    unique_ip: bool = False,
    port_cap: int = RESIDENT_TG_PORT_CAP,
) -> Dict[str, Any]:
    """按计划租号量预计算同国代理条数，再交给住宅列表按量导出。

    200 次租号 ≠ 200 条独立出口。``{CC}_tg`` 住宅列表最多展开
    ``RESIDENT_TG_PORT_CAP``（40）个 session 口。公式：

    - 活跃绑定 = 并发度（unique_ip 时至少等于任务数）
    - 猎号轮换 = max(活跃绑定, ceil(计划租号 / 每出口 sendCode 上限),
      并发 × ceil(每任务取号 / 每出口上限))
    - 实际请求 = min(住宅口上限, 猎号轮换需求)

    非猎号（每任务只租 1 次）退化为旧语义：只要并发条数。
    """
    live = max(1, int(concurrency or 1))
    tasks = max(1, int(task_count or 1))
    leases = max(0, int(planned_leases or 0))
    attempts = max(1, int(attempts_per_task or 1))
    uses = max(1, int(proxy_max_uses or 1))
    cap = max(1, int(port_cap or RESIDENT_TG_PORT_CAP))
    cap = min(cap, RESIDENT_TG_PORT_CAP)

    live_need = max(live, tasks) if unique_ip else live
    if attempts <= 1:
        hunt_need = live_need
    else:
        per_task = max(1, math.ceil(attempts / uses))
        lease_need = max(1, math.ceil(leases / uses)) if leases else per_task * live
        isolated_need = per_task * (tasks if unique_ip else live)
        hunt_need = max(live_need, lease_need, isolated_need)

    requested = min(cap, hunt_need)
    capped = hunt_need > cap
    rotation_spare = max(0, requested - live_need)
    message = (
        f"计划租号 {leases} 次（{tasks} 路 × {attempts} 次），"
        f"每出口最多 {uses} 次 sendCode，活跃绑定 {live_need} 条"
        f"{'（单线不复用）' if unique_ip else ''} → 请求 {requested} 条同国代理"
    )
    if capped:
        message += f"（住宅列表上限 {cap}，理论需求 {hunt_need}，无法按租号数 1:1 配独立 IP）"
    elif attempts > 1:
        message += f"，猎号轮换余量 {rotation_spare} 条"
    return {
        "live_slots": live_need,
        "hunt_need": hunt_need,
        "requested": requested,
        "port_cap": cap,
        "capped": capped,
        "rotation_spare": rotation_spare,
        "planned_leases": leases,
        "attempts_per_task": attempts,
        "proxy_max_uses": uses,
        "unique_ip": bool(unique_ip),
        "message": message,
    }


class BatchProxySlotPool:
    """批次预分配的同国代理队列；活跃任务与出口 1:1。

    池容量可以大于并发度：多出来的线留给猎号 ``try_rotate``。
    ``consume_once=True`` 时任务结束后不归还，跨批次也不再发同一条单线。
    """

    def __init__(
        self,
        country: str,
        proxies: List[Dict[str, Any]],
        batch_id: str,
        *,
        consume_once: bool = False,
    ) -> None:
        self.country = (country or "").lower()
        self.batch_id = batch_id
        self.consume_once = bool(consume_once)
        self._capacity = len(proxies)
        self._held: Dict[str, Dict[str, Any]] = {}
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        for item in proxies:
            self._queue.put_nowait(dict(item))

    @property
    def size(self) -> int:
        return self._capacity

    def available(self) -> int:
        return self._queue.qsize()

    def held_proxy(self, task_id: str) -> Optional[Dict[str, Any]]:
        held = self._held.get(task_id)
        return dict(held) if held else None

    async def acquire(self, task_id: str) -> Dict[str, Any]:
        proxy = await self._queue.get()
        registry = await ProxyLeaseRegistry.get_instance()
        owner = f"{self.batch_id}:{task_id}"
        if not await registry.try_lease(proxy, owner):
            await self._queue.put(proxy)
            raise RuntimeError(
                f"代理 {format_proxy_endpoint(proxy)} 已被其它任务占用，1:1 槽位冲突"
            )
        self._held[task_id] = proxy
        return proxy

    async def try_rotate(self, task_id: str) -> Optional[Dict[str, Any]]:
        """把当前持有的线换成池里另一条空闲同国出口；没有余量则返回 None。"""
        current = self._held.get(task_id)
        if current is None:
            return None
        registry = await ProxyLeaseRegistry.get_instance()
        owner = f"{self.batch_id}:{task_id}"
        skipped: List[Dict[str, Any]] = []
        nxt: Optional[Dict[str, Any]] = None
        while True:
            try:
                cand = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if registry.is_leased(cand):
                skipped.append(cand)
                continue
            if await registry.try_lease(cand, owner):
                nxt = cand
                break
            skipped.append(cand)
        for item in skipped:
            self._queue.put_nowait(item)
        if nxt is None:
            return None
        await registry.release(current, owner)
        if self.consume_once:
            await registry.retire(current)
        else:
            self._queue.put_nowait(dict(current))
        self._held[task_id] = nxt
        return nxt

    async def release(self, proxy: Dict[str, Any], task_id: str) -> None:
        current = self._held.pop(task_id, None) or proxy
        registry = await ProxyLeaseRegistry.get_instance()
        owner = f"{self.batch_id}:{task_id}"
        await registry.release(current, owner)
        if self.consume_once:
            await registry.retire(current)
            return
        await self._queue.put(dict(current))


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
                ensured = await ensure_fn(country, create=True, ports=need)
                if ensured.get("created") or ensured.get("proxies"):
                    invalidate = getattr(svc, "invalidate_cache", None)
                    if callable(invalidate):
                        invalidate()
            except Exception as exc:
                logger.warning("批次预分配 ensure_tg_resident_list 失败: %s", exc)

        regional = await svc.get_proxy_list(country=country, refresh=True, include_health=False)
        if not regional:
            return []

        session_tag = mint_resident_session_tag()
        regional = [
            apply_resident_session_tag(item, session_tag) if is_resident_tg(item) else dict(item)
            for item in regional
        ]
        logger.info(
            "批次住宅 session 已轮换 country=%s tag=%s nodes=%s",
            country,
            session_tag,
            sum(1 for item in regional if is_resident_tg(item)),
        )

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
    concurrency: Optional[int] = None,
    demand: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[BatchProxySlotPool], int, List[str]]:
    """为批次预拉 ``slots`` 条同国代理（按租号需求计算，可大于并发度）。

    ``concurrency`` 是活跃 1:1 绑定条数；池容量可以更大，多出来的留给猎号轮换。
    返回 (池, 有效并发度, 日志行)。池为 None 表示沿用任务级旧逻辑。
    """
    mode = normalize_proxy_mode(proxy_mode)
    need = max(1, int(slots or 1))
    live = max(1, int(concurrency if concurrency is not None else need))
    target = (country or "").lower()
    unique = unique_proxy_ip_enabled(config)
    logs: List[str] = []
    registry = await ProxyLeaseRegistry.get_instance()

    if mode == "fallback":
        return None, live, logs

    if demand and demand.get("message"):
        logs.append(f"[代理需求] {demand['message']}")

    picked: List[Dict[str, Any]] = []

    if mode in {"custom_pool", "explicit"}:
        picked = await _allocate_from_custom_pool(target, need, registry)

    if len(picked) < need and mode in {"auto", "custom_pool"}:
        api_key = str(getattr(config, "proxy_seller_key", "") or "").strip()
        if api_key:
            want = need + len(getattr(registry, "_retired", set()) or [])
            api_picked = await _allocate_from_proxy_seller(target, max(need, want), api_key, registry)
            seen = {proxy_identity(p) for p in picked}
            for item in api_picked:
                ident = proxy_identity(item)
                if ident in seen or registry.is_leased(item):
                    continue
                seen.add(ident)
                picked.append(item)
                if len(picked) >= need:
                    break

    if mode == "auto" and not picked:
        msg = (
            f"[代理槽位] 批次 {batch_id}: 目标区域 {target.upper()} 无法预分配任何同国代理，"
            f"已禁止跨区 fallback；请补充 {target.upper()} 住宅列表/自建池或降低并发。"
        )
        logs.append(msg)
        return None, 0, logs

    if not picked and mode == "custom_pool":
        return None, live, logs

    allocated = min(need, len(picked))
    pool_proxies = picked[:allocated]
    effective = min(live, allocated)
    origins = sorted({_proxy_origin_label(p) for p in pool_proxies})
    bind_note = "单线消耗不复用" if unique else "活跃任务与出口 1:1 绑定"
    spare = max(0, allocated - effective)
    session_tags = sorted({
        str(item.get("session_tag") or "")
        for item in pool_proxies
        if item.get("session_tag")
    })
    logs.append(
        f"[代理槽位] 批次 {batch_id}: 预分配 {allocated}/{need} 条 {target.upper()} 同国代理"
        f"（来源: {' / '.join(origins)}），{bind_note}"
        f"，活跃并发 {effective}，猎号轮换余量 {spare}"
    )
    if session_tags:
        logs.append(
            f"[代理槽位] 住宅 session 已轮换 tag={','.join(session_tags)}"
            "（同口换新出口，不复用上一轮 ttl_24h 粘性 IP）"
        )
    for idx, proxy in enumerate(pool_proxies, start=1):
        logs.append(
            f"[代理槽位] 槽 {idx}/{allocated}: {_proxy_origin_label(proxy)} "
            f"{format_proxy_endpoint(proxy)}"
        )
    if allocated < need:
        logs.append(
            f"[代理槽位] ⚠️ 同国代理仅 {allocated} 条（请求 {need}）"
        )
    if effective < live:
        logs.append(
            f"[代理槽位] ⚠️ 批次并发由 {live} 降为 {effective}"
        )
    if allocated <= effective and need > live:
        logs.append(
            f"[代理槽位] ⚠️ 猎号轮换余量为 0，达 sendCode 上限后无法换出口"
        )

    return BatchProxySlotPool(target, pool_proxies, batch_id, consume_once=unique), effective, logs


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
