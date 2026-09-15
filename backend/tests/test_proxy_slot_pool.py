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
    prepare_batch_proxy_pool,
)
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


if __name__ == "__main__":
    unittest.main()
