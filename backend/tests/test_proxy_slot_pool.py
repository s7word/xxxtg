"""批次代理槽位池：1:1 绑定与预分配测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.proxy_slot_pool import (  # noqa: E402
    BatchProxySlotPool,
    ProxyLeaseRegistry,
    compute_batch_proxy_demand,
    prepare_batch_proxy_pool,
)
from backend.app.services.proxyseller import RESIDENT_TG_PORT_CAP  # noqa: E402
from backend.app.services.registrar import RegistrationOrchestrator, RegistrationTaskManager  # noqa: E402


def _proxy(port: int, country: str = "za") -> dict:
    return {
        "proxy_type": "socks5",
        "addr": "res.proxy-seller.com",
        "port": port,
        "username": f"user_{port}",
        "password": "pass",
        "country_code": country,
    }


class TestProxyLeaseRegistry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ProxyLeaseRegistry.reset_for_tests()

    async def test_lease_is_exclusive(self):
        reg = await ProxyLeaseRegistry.get_instance()
        p = _proxy(10000)
        self.assertTrue(await reg.try_lease(p, "a"))
        self.assertFalse(await reg.try_lease(p, "b"))
        await reg.release(p, "a")
        self.assertTrue(await reg.try_lease(p, "b"))


class TestBatchProxySlotPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ProxyLeaseRegistry.reset_for_tests()

    async def test_acquire_release_allows_reuse_after_return(self):
        pool = BatchProxySlotPool("za", [_proxy(10000)], "batch1")
        first = await pool.acquire("t1")
        port = first["port"]
        await pool.release(first, "t1")
        second = await pool.acquire("t2")
        self.assertEqual(second["port"], port)

    async def test_consume_once_does_not_return_to_queue(self):
        pool = BatchProxySlotPool("za", [_proxy(10000), _proxy(10001)], "batch1", consume_once=True)
        first = await pool.acquire("t1")
        await pool.release(first, "t1")
        second = await pool.acquire("t2")
        self.assertNotEqual(second["port"], first["port"])
        self.assertTrue((await ProxyLeaseRegistry.get_instance()).is_leased(first))

    async def test_try_rotate_swaps_when_spare_exists(self):
        pool = BatchProxySlotPool("za", [_proxy(10000), _proxy(10001), _proxy(10002)], "batch1")
        held = await pool.acquire("t1")
        swapped = await pool.try_rotate("t1")
        self.assertIsNotNone(swapped)
        self.assertNotEqual(swapped["port"], held["port"])
        self.assertEqual(pool.held_proxy("t1")["port"], swapped["port"])
        await pool.release(held, "t1")

    async def test_try_rotate_returns_none_without_spare(self):
        pool = BatchProxySlotPool("za", [_proxy(10000), _proxy(10001)], "batch1")
        a = await pool.acquire("t1")
        b = await pool.acquire("t2")
        self.assertIsNone(await pool.try_rotate("t1"))
        self.assertEqual(pool.held_proxy("t1")["port"], a["port"])
        await pool.release(a, "t1")
        await pool.release(b, "t2")

    async def test_try_rotate_consume_once_retires_old_line(self):
        pool = BatchProxySlotPool(
            "za", [_proxy(10000), _proxy(10001)], "batch1", consume_once=True
        )
        first = await pool.acquire("t1")
        swapped = await pool.try_rotate("t1")
        self.assertIsNotNone(swapped)
        self.assertNotEqual(swapped["port"], first["port"])
        registry = await ProxyLeaseRegistry.get_instance()
        self.assertTrue(registry.is_leased(first))
        await pool.release(swapped, "t1")


class TestComputeBatchProxyDemand(unittest.TestCase):
    def test_sniper_default_requests_forty_not_two_hundred(self):
        demand = compute_batch_proxy_demand(
            task_count=10,
            concurrency=10,
            planned_leases=200,
            attempts_per_task=20,
            proxy_max_uses=5,
        )
        self.assertEqual(demand["requested"], 40)
        self.assertEqual(demand["live_slots"], 10)
        self.assertEqual(demand["rotation_spare"], 30)
        self.assertFalse(demand["capped"])
        self.assertIn("请求 40 条", demand["message"])

    def test_strict_uses_one_caps_at_resident_port_limit(self):
        demand = compute_batch_proxy_demand(
            task_count=10,
            concurrency=10,
            planned_leases=200,
            attempts_per_task=20,
            proxy_max_uses=1,
        )
        self.assertEqual(demand["requested"], RESIDENT_TG_PORT_CAP)
        self.assertTrue(demand["capped"])
        self.assertEqual(demand["hunt_need"], 200)
        self.assertIn("无法按租号数 1:1", demand["message"])

    def test_non_hunt_keeps_concurrency(self):
        demand = compute_batch_proxy_demand(
            task_count=10,
            concurrency=3,
            planned_leases=10,
            attempts_per_task=1,
            proxy_max_uses=5,
        )
        self.assertEqual(demand["requested"], 3)
        self.assertEqual(demand["live_slots"], 3)

    def test_unique_ip_non_hunt_uses_task_count(self):
        demand = compute_batch_proxy_demand(
            task_count=8,
            concurrency=3,
            planned_leases=8,
            attempts_per_task=1,
            proxy_max_uses=5,
            unique_ip=True,
        )
        self.assertEqual(demand["requested"], 8)

    def test_unique_ip_hunt_still_caps_at_port_limit(self):
        demand = compute_batch_proxy_demand(
            task_count=10,
            concurrency=10,
            planned_leases=200,
            attempts_per_task=20,
            proxy_max_uses=5,
            unique_ip=True,
        )
        self.assertEqual(demand["requested"], 40)


def proxy_identity(proxy):
    from backend.app.services.proxyseller import proxy_identity as pi

    return pi(proxy)


class TestPrepareBatchProxyPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ProxyLeaseRegistry.reset_for_tests()

    async def test_auto_mode_fails_without_proxies(self):
        cfg = type("Cfg", (), {"proxy_seller_key": "k"})()
        with patch(
            "backend.app.services.proxy_slot_pool._allocate_from_proxy_seller",
            new=AsyncMock(return_value=[]),
        ):
            pool, limit, logs = await prepare_batch_proxy_pool(
                batch_id="b1",
                country="za",
                slots=3,
                config=cfg,
                proxy_mode="auto",
            )
        self.assertIsNone(pool)
        self.assertEqual(limit, 0)
        self.assertTrue(any("禁止跨区 fallback" in line for line in logs))

    async def test_auto_reduces_concurrency_when_partial(self):
        cfg = type("Cfg", (), {"proxy_seller_key": "k"})()
        proxies = [_proxy(10000), _proxy(10001)]
        with patch(
            "backend.app.services.proxy_slot_pool._allocate_from_proxy_seller",
            new=AsyncMock(return_value=proxies),
        ):
            pool, limit, logs = await prepare_batch_proxy_pool(
                batch_id="b2",
                country="za",
                slots=5,
                config=cfg,
                proxy_mode="auto",
            )
        self.assertIsNotNone(pool)
        self.assertEqual(limit, 2)
        self.assertEqual(pool.size, 2)
        self.assertTrue(any("降为 2" in line for line in logs))

    async def test_unique_ip_skips_retired_across_batches(self):
        cfg = type("Cfg", (), {"proxy_seller_key": "k", "proxy_unique_ip_per_task": True})()
        first_wave = [_proxy(10000), _proxy(10001)]
        second_wave = [_proxy(10000), _proxy(10001), _proxy(10002), _proxy(10003)]
        with patch(
            "backend.app.services.proxy_slot_pool._allocate_from_proxy_seller",
            new=AsyncMock(return_value=first_wave),
        ):
            pool1, _, logs1 = await prepare_batch_proxy_pool(
                batch_id="w1",
                country="za",
                slots=2,
                config=cfg,
                proxy_mode="auto",
            )
        self.assertTrue(pool1.consume_once)
        self.assertTrue(any("不复用" in line for line in logs1))
        a = await pool1.acquire("t1")
        b = await pool1.acquire("t2")
        await pool1.release(a, "t1")
        await pool1.release(b, "t2")

        with patch(
            "backend.app.services.proxy_slot_pool._allocate_from_proxy_seller",
            new=AsyncMock(return_value=second_wave),
        ):
            pool2, limit2, _ = await prepare_batch_proxy_pool(
                batch_id="w2",
                country="za",
                slots=2,
                config=cfg,
                proxy_mode="auto",
            )
        self.assertEqual(limit2, 2)
        c = await pool2.acquire("t3")
        d = await pool2.acquire("t4")
        self.assertEqual({c["port"], d["port"]}, {10002, 10003})

    async def test_unique_prepare_uses_task_count_not_concurrency(self):
        cfg = type("Cfg", (), {"proxy_seller_key": "k", "proxy_unique_ip_per_task": True})()
        proxies = [_proxy(10000 + i) for i in range(10)]
        with patch(
            "backend.app.services.proxy_slot_pool._allocate_from_proxy_seller",
            new=AsyncMock(return_value=proxies),
        ) as alloc:
            pool, limit, _ = await prepare_batch_proxy_pool(
                batch_id="full",
                country="za",
                slots=10,
                config=cfg,
                proxy_mode="auto",
            )
        self.assertEqual(pool.size, 10)
        self.assertEqual(limit, 10)
        alloc.assert_awaited()

    async def test_prepare_keeps_spare_lines_above_concurrency(self):
        cfg = type("Cfg", (), {"proxy_seller_key": "k"})()
        proxies = [_proxy(10000 + i) for i in range(40)]
        with patch(
            "backend.app.services.proxy_slot_pool._allocate_from_proxy_seller",
            new=AsyncMock(return_value=proxies),
        ) as alloc:
            pool, limit, logs = await prepare_batch_proxy_pool(
                batch_id="hunt",
                country="id",
                slots=40,
                config=cfg,
                proxy_mode="auto",
                concurrency=10,
            )
        self.assertEqual(pool.size, 40)
        self.assertEqual(limit, 10)
        self.assertTrue(any("猎号轮换余量 30" in line for line in logs))
        alloc.assert_awaited()
        self.assertEqual(alloc.await_args.args[1], 40)

    async def test_allocate_asks_resident_list_for_requested_ports(self):
        from backend.app.services.proxy_slot_pool import _allocate_from_proxy_seller

        cfg_proxies = [_proxy(10000 + i, "id") for i in range(12)]
        ensure = AsyncMock(return_value={"created": False, "proxies": cfg_proxies})
        svc = type(
            "Svc",
            (),
            {
                "ensure_tg_resident_list": ensure,
                "invalidate_cache": lambda self: None,
                "get_proxy_list": AsyncMock(return_value=cfg_proxies),
                "_sort_candidates": lambda self, items: items,
                "_rotate": lambda self, country, items: items,
                "close": AsyncMock(),
            },
        )()
        registry = await ProxyLeaseRegistry.get_instance()
        with patch(
            "backend.app.services.proxy_slot_pool.ProxySellerService",
            return_value=svc,
        ):
            picked = await _allocate_from_proxy_seller("id", 12, "k", registry)
        ensure.assert_awaited()
        self.assertEqual(ensure.await_args.kwargs.get("ports"), 12)
        self.assertEqual(len(picked), 12)


class TestRunBatchWithSlotPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ProxyLeaseRegistry.reset_for_tests()
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev
        ProxyLeaseRegistry.reset_for_tests()

    async def test_run_batch_passes_distinct_proxies_one_to_one(self):
        batch_id, task_ids = self.manager.create_batch(count=3, concurrency=2, country="za")
        proxies = [_proxy(10000), _proxy(10001)]
        fake_pool = BatchProxySlotPool("za", proxies, batch_id)
        seen = []

        async def fake_run(task_id, proxy_override=None, **_kwargs):
            seen.append(proxy_override.get("port"))
            self.manager.update_task_status(task_id, "success")

        with patch(
            "backend.app.services.proxy_slot_pool.prepare_batch_proxy_pool",
            new=AsyncMock(return_value=(fake_pool, 2, ["[代理槽位] ok"])),
        ), patch.object(RegistrationOrchestrator, "run_registration", side_effect=fake_run):
            await RegistrationOrchestrator.run_batch(
                batch_id=batch_id,
                task_ids=task_ids,
                country="za",
                concurrency=2,
                proxy_mode="auto",
            )

        self.assertEqual(len(seen), 3)
        self.assertTrue(all(port in {10000, 10001} for port in seen))
        logs = "\n".join(self.manager.get_task(task_ids[0])["logs"])
        self.assertIn("[代理槽位]", logs)

    async def test_run_batch_requests_hunt_proxy_demand_not_just_concurrency(self):
        batch_id, task_ids = self.manager.create_batch(count=10, concurrency=10, country="id")
        captured = {}

        async def fake_prepare(**kwargs):
            captured.update(kwargs)
            proxies = [_proxy(10000 + i, "id") for i in range(int(kwargs["slots"]))]
            return BatchProxySlotPool("id", proxies, batch_id), 10, ["[代理槽位] ok"]

        async def fake_run(task_id, proxy_override=None, **_kwargs):
            self.manager.update_task_status(task_id, "success")

        with patch(
            "backend.app.services.proxy_slot_pool.prepare_batch_proxy_pool",
            new=AsyncMock(side_effect=fake_prepare),
        ), patch.object(RegistrationOrchestrator, "run_registration", side_effect=fake_run), patch.object(
            RegistrationOrchestrator,
            "_resolve_hunt_limits",
            return_value={"proxy_max_uses": 5, "no_number_retries": 0, "no_number_delay": 0, "device_max_uses": 1, "app_blacklist_ttl_hours": 24, "app_delivery_fuse": 0},
        ), patch.object(
            RegistrationOrchestrator,
            "resolve_hunt_lease_budget",
            return_value={
                "planned_leases": 200,
                "max_number_attempts": 20,
                "requested_attempts": 20,
                "count": 10,
                "limit": 200,
                "clamped": False,
                "rejected": False,
                "message": "计划最多租号 200 次",
            },
        ):
            await RegistrationOrchestrator.run_batch(
                batch_id=batch_id,
                task_ids=task_ids,
                country="id",
                concurrency=10,
                proxy_mode="auto",
                max_number_attempts=20,
            )

        self.assertEqual(captured.get("slots"), 40)
        self.assertEqual(captured.get("concurrency"), 10)
        self.assertEqual(captured.get("demand", {}).get("requested"), 40)
        self.assertEqual(captured.get("demand", {}).get("planned_leases"), 200)

    async def test_rotate_hunt_proxy_uses_slot_pool_spare(self):
        tid = self.manager.create_task()
        pool = BatchProxySlotPool("id", [_proxy(10000, "id"), _proxy(10001, "id")], "b-rot")
        current = await pool.acquire(tid)
        rotated, changed = await RegistrationOrchestrator._rotate_hunt_proxy(
            config=type("Cfg", (), {})(),
            target_country="id",
            task_id=tid,
            manager=self.manager,
            current_proxy=current,
            proxy_mode="auto",
            reason="单测批次池轮换",
            proxy_override=current,
            slot_pool=pool,
        )
        self.assertTrue(changed)
        self.assertNotEqual(rotated["port"], current["port"])
        logs = "\n".join(self.manager.get_task(tid)["logs"])
        self.assertIn("出口已轮换", logs)
        self.assertIn("批次预分配池", logs)
        await pool.release(rotated, tid)

    async def test_rotate_hunt_proxy_stays_pinned_without_spare(self):
        tid = self.manager.create_task()
        pool = BatchProxySlotPool("id", [_proxy(10000, "id")], "b-pin")
        current = await pool.acquire(tid)
        rotated, changed = await RegistrationOrchestrator._rotate_hunt_proxy(
            config=type("Cfg", (), {})(),
            target_country="id",
            task_id=tid,
            manager=self.manager,
            current_proxy=current,
            proxy_mode="auto",
            reason="单测无余量",
            proxy_override=current,
            slot_pool=pool,
        )
        self.assertFalse(changed)
        self.assertEqual(rotated["port"], current["port"])
        logs = "\n".join(self.manager.get_task(tid)["logs"])
        self.assertIn("暂无空闲同国出口", logs)
        await pool.release(current, tid)


if __name__ == "__main__":
    unittest.main()
