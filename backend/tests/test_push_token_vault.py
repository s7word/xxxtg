"""Push Token 本地库存：入库、按 use_count 复用、退款/成功退役。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
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
