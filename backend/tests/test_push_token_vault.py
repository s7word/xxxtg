"""Push Token 本地库存：入库、按 use_count 复用、退款/成功退役、跨任务租约互斥。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.attestation_gateway import AttestationGatewayService  # noqa: E402
from backend.app.services.push_token_vault import (  # noqa: E402
    PushTokenVault,
    REUSE_PROVIDER,
    STATUS_AVAILABLE,
    STATUS_CONSUMED,
    STATUS_REFUNDED,
    STATUS_RETIRED,
)
from backend.app.services.reghelp import PushTokenResult  # noqa: E402


class TestPushTokenVault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vault.json"
        self.vault = PushTokenVault.reset_for_tests(self.path)

    def tearDown(self):
        self.tmp.cleanup()
        PushTokenVault.reset_for_tests(Path(self.tmp.name) / "gone.json")

    def test_store_and_acquire_prefers_unused(self):
        a = self.vault.store_issued(token="tok-a", reghelp_task_id="t-a", app_type="telegram_android")
        b = self.vault.store_issued(token="tok-b", reghelp_task_id="t-b", app_type="telegram_android")
        self.vault.mark_attempt(vault_id=a["id"])
        picked = self.vault.acquire_for_reuse(max_uses=2)
        self.assertEqual(picked["token"], "tok-b")
        self.assertEqual(picked["use_count"], 1)
        picked2 = self.vault.acquire_for_reuse(max_uses=2)
        self.assertEqual(picked2["token"], "tok-a")
        self.assertEqual(picked2["use_count"], 2)

    def test_success_and_refund_not_reusable(self):
        row = self.vault.store_issued(token="tok-x", reghelp_task_id="t-x")
        self.vault.mark_success(vault_id=row["id"])
        self.assertIsNone(self.vault.acquire_for_reuse(max_uses=3))
        row2 = self.vault.store_issued(token="tok-y", reghelp_task_id="t-y")
        self.vault.mark_refunded(vault_id=row2["id"])
        self.assertIsNone(self.vault.acquire_for_reuse(max_uses=3))
        summary = self.vault.summary()
        self.assertEqual(summary["consumed"], 1)
        self.assertEqual(summary["refunded"], 1)
        self.assertEqual(summary["reusable"], 0)

    def test_failed_keep_stays_available(self):
        row = self.vault.store_issued(token="tok-z", reghelp_task_id="t-z")
        self.vault.mark_attempt(vault_id=row["id"])
        self.vault.mark_failed_keep(vault_id=row["id"], reason="SENT_CODE_TYPE_APP")
        items = self.vault.list_items()
        self.assertEqual(items[0]["status"], STATUS_AVAILABLE)
        self.assertEqual(items[0]["use_count"], 1)


class TestPushTokenLease(unittest.TestCase):
    """Push Token 与设备指纹绑定，同一枚不得被两个任务同时持有。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vault.json"
        self.vault = PushTokenVault.reset_for_tests(self.path)

    def tearDown(self):
        self.tmp.cleanup()
        PushTokenVault.reset_for_tests(Path(self.tmp.name) / "gone.json")

    def _row(self, vault_id):
        return next(row for row in self.vault.list_items() if row["id"] == vault_id)

    def test_two_tasks_cannot_hold_same_reuse_token(self):
        self.vault.store_issued(token="tok-a", reghelp_task_id="t-a")
        first = self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-A")
        self.assertIsNotNone(first)
        self.assertEqual(first["lease_task_id"], "task-A")
        self.assertEqual(first["use_count_before"], 0)
        self.assertEqual(first["use_count"], 1)
        # 库里只有这一枚，且已被 A 持有：B 只能拿到 None，不能转租
        self.assertIsNone(self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-B"))
        # A 自己再取（同一任务续用）不受租约限制
        self.assertIsNotNone(self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-A"))

    def test_freshly_issued_token_is_owned_by_requesting_task(self):
        row = self.vault.store_issued(
            token="tok-new", reghelp_task_id="t-new", source_task_id="task-A"
        )
        self.assertEqual(row["lease_task_id"], "task-A")
        self.assertIsNone(self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-B"))
        # 非持有者也不能通过 mark_attempt 抢走
        self.assertIsNone(
            self.vault.mark_attempt(vault_id=row["id"], lease_task_id="task-B")
        )

    def test_non_holder_cannot_retire_or_refund(self):
        row = self.vault.store_issued(token="tok-a", reghelp_task_id="t-a")
        self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-A")

        self.assertIsNone(
            self.vault.mark_retired(reghelp_task_id="t-a", lease_task_id="task-B")
        )
        self.assertIsNone(
            self.vault.mark_refunded(reghelp_task_id="t-a", lease_task_id="task-B")
        )
        self.assertIsNone(
            self.vault.mark_failed_keep(reghelp_task_id="t-a", lease_task_id="task-B")
        )
        self.assertFalse(self.vault.release_lease(reghelp_task_id="t-a", lease_task_id="task-B"))
        current = self._row(row["id"])
        self.assertEqual(current["status"], STATUS_AVAILABLE)
        self.assertEqual(current["lease_task_id"], "task-A")

        # 持有者本人 retire 生效，并顺带释放租约
        self.assertIsNotNone(
            self.vault.mark_retired(reghelp_task_id="t-a", lease_task_id="task-A")
        )
        retired = self._row(row["id"])
        self.assertEqual(retired["status"], STATUS_RETIRED)
        self.assertIsNone(retired["lease_task_id"])

    def test_release_task_leases_frees_tokens_for_others(self):
        self.vault.store_issued(token="tok-a", reghelp_task_id="t-a")
        self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-A")
        self.assertIsNone(self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-B"))
        self.assertEqual(self.vault.release_task_leases("task-A"), 1)
        picked = self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-B")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["lease_task_id"], "task-B")

    def test_expired_lease_can_be_taken_over(self):
        self.vault.store_issued(token="tok-a", reghelp_task_id="t-a")
        self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-crashed")
        # 模拟持有任务被 kill：租约到期后其它任务可以接管，令牌不会永久卡死
        stale = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.vault._items[0]["leased_until"] = stale
        picked = self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-B")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["lease_task_id"], "task-B")

    def test_summary_reports_leased_and_excludes_from_reusable(self):
        self.vault.store_issued(token="tok-a", reghelp_task_id="t-a")
        self.vault.store_issued(token="tok-b", reghelp_task_id="t-b")
        self.vault.acquire_for_reuse(max_uses=3, lease_task_id="task-A")
        summary = self.vault.summary()
        self.assertEqual(summary["available"], 2)
        self.assertEqual(summary["leased"], 1)
        self.assertEqual(summary["reusable"], 1)


class TestGatewayReuse(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vault.json"
        self.vault = PushTokenVault.reset_for_tests(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_reuse_before_fresh_issue(self):
        self.vault.store_issued(token="cached-token", reghelp_task_id="old-1")
        cfg = SimpleNamespace(
            reghelp_api_key="",
            antisafety_api_key="",
            reghelp_enabled=False,
            antisafety_enabled=False,
            attestation_provider_mode="reghelp_only",
            push_token_reuse_enabled=True,
            push_token_reuse_max_uses=2,
            push_token_save_issued=True,
            reghelp_base_urls=[],
            antisafety_base_urls=[],
            antisafety_reporting_base_urls=[],
        )
        gw = AttestationGatewayService(cfg)
        token, task_id, provider = await gw.get_push_token({"app_name": "tg"}, ref="task-new")
        self.assertEqual(token, "cached-token")
        self.assertEqual(task_id, "old-1")
        self.assertEqual(provider, REUSE_PROVIDER)

    async def test_second_task_cannot_reuse_token_held_by_first(self):
        """并发两任务走同一网关：第二个任务不得拿到第一个任务正在用的令牌。"""
        self.vault.store_issued(token="cached-token", reghelp_task_id="old-1")
        cfg = SimpleNamespace(
            reghelp_api_key="",
            antisafety_api_key="",
            reghelp_enabled=False,
            antisafety_enabled=False,
            attestation_provider_mode="reghelp_only",
            push_token_reuse_enabled=True,
            push_token_reuse_max_uses=3,
            push_token_save_issued=True,
            reghelp_base_urls=[],
            antisafety_base_urls=[],
            antisafety_reporting_base_urls=[],
        )
        gw = AttestationGatewayService(cfg)
        first = await gw.get_push_token({"app_name": "tg"}, ref="task-A")
        self.assertEqual(first, ("cached-token", "old-1", REUSE_PROVIDER))
        # 没有可用提供源，且唯一库存已被 task-A 持有 → task-B 只能空手而归
        second = await gw.get_push_token({"app_name": "tg"}, ref="task-B")
        self.assertEqual(second, (None, None, None))

        # task-B 也不能把 task-A 的令牌 retire 掉
        self.assertIsNone(self.vault.mark_retired(reghelp_task_id="old-1", lease_task_id="task-B"))
        self.assertEqual(self.vault.list_items()[0]["status"], STATUS_AVAILABLE)

        # task-A 结束归还后，task-B 才能接手
        self.assertEqual(self.vault.release_task_leases("task-A"), 1)
        third = await gw.get_push_token({"app_name": "tg"}, ref="task-B")
        self.assertEqual(third, ("cached-token", "old-1", REUSE_PROVIDER))

    async def test_fresh_issue_saves_to_vault(self):
        cfg = SimpleNamespace(
            reghelp_api_key="k",
            antisafety_api_key="",
            reghelp_enabled=True,
            antisafety_enabled=False,
            attestation_provider_mode="reghelp_only",
            push_token_reuse_enabled=False,
            push_token_reuse_max_uses=2,
            push_token_save_issued=True,
            reghelp_base_urls=["https://api.reghelp.net"],
            antisafety_base_urls=[],
            antisafety_reporting_base_urls=[],
            reghelp_connect_timeout=6.0,
            reghelp_total_timeout=20.0,
        )
        gw = AttestationGatewayService(cfg)
        gw.reghelp = SimpleNamespace(
            api_bases=["https://api.reghelp.net"],
            get_push_token=AsyncMock(
                return_value=PushTokenResult(
                    token="fresh-token",
                    task_id="rh-99",
                    provider="reghelp",
                    obtained_at=1.0,
                )
            ),
        )
        token, task_id, provider = await gw.get_push_token(
            {"app_name": "tg", "app_device": "Android", "app_type": "telegram_android"},
            ref="task-1",
        )
        self.assertEqual(token, "fresh-token")
        self.assertEqual(provider, "reghelp")
        items = self.vault.list_items(include_token=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["token"], "fresh-token")
        self.assertEqual(items[0]["use_count"], 1)
        self.assertEqual(items[0]["status"], STATUS_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
