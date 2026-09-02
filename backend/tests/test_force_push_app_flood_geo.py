"""四项落地核对：强制 Push、App 黑名单丢号、FLOOD 不填满、代理=号国。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.banned_phones import (  # noqa: E402
    CATEGORY_APP_DELIVERY,
    SOURCE_SENT_CODE,
    BannedPhonesCache,
)
from backend.app.services.code_delivery import (  # noqa: E402
    CODE_DELIVERY_PUSH_REQUIRED,
    resolve_code_delivery_plan,
)
from backend.app.services.proxy_manager import (  # noqa: E402
    proxy_is_labeled_foreign,
)
from backend.app.services.registrar import (  # noqa: E402
    ProxyCountryMismatchError,
    RegistrationOrchestrator,
    RegistrationTaskManager,
    SendCodeFloodWindow,
    SentCodeAppDeliveryError,
)
from backend.tests.test_code_delivery import _config, _profile  # noqa: E402
from backend.tests.test_hunt_retry_app_push import (  # noqa: E402
    FakeSms,
    HuntRunMixin,
    make_config,
    make_gateway,
)
from backend.tests.test_sentcode_app_batch import make_sent_code  # noqa: E402


class TestForcePushPlan(unittest.TestCase):
    def test_schema_proxy_country_match_default_on(self):
        cfg = AppConfigModel()
        self.assertTrue(cfg.proxy_require_country_match)
        self.assertTrue(cfg.app_delivery_fast_drop)
        self.assertTrue(cfg.flood_rotate_push_token)

    def test_strict_pins_published_api4_so_plan_attaches(self):
        plan = resolve_code_delivery_plan(
            _config(
                device_alignment_mode="strict",
                strict_vault_device_alignment=True,
                api_credential_mode="custom",
                custom_api_id=35337905,
            ),
            _profile(api_id=35337905),
        )
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)
        self.assertTrue(plan.attach_push_token)
        self.assertTrue(plan.use_published_api_id)


class TestAppDeliveryBlacklist(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()
        SendCodeFloodWindow.get().reset()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev
        SendCodeFloodWindow.get().reset()

    async def test_app_only_fast_drop_writes_blacklist(self):
        sms = FakeSms([("act-1", "+56911110001")])
        gw = make_gateway()
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "banned.json"
        BannedPhonesCache.reset_memory()
        try:
            with self._run_ctx(
                sms=sms,
                gw=gw,
                send_code=AsyncMock(
                    side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
                ),
                extra=[
                    patch.object(
                        RegistrationOrchestrator,
                        "_resolve_custom_proxy",
                        new=AsyncMock(return_value=None),
                    ),
                    patch("backend.app.services.banned_phones.CACHE_PATH", path),
                ],
            ):
                await RegistrationOrchestrator.run_registration(
                    task_id=self.task_id,
                    country="cl",
                    max_number_attempts=1,
                    no_number_retries=0,
                )
            record = BannedPhonesCache.lookup("+56911110001", path=path)
            self.assertIsNotNone(record)
            self.assertEqual(record.category, CATEGORY_APP_DELIVERY)
            self.assertEqual(record.reason, "SENT_CODE_TYPE_APP")
            self.assertEqual(record.source, SOURCE_SENT_CODE)
            self.assertTrue(record.expires_at)
        finally:
            BannedPhonesCache.reset_memory()
            tmp.cleanup()

    async def test_strict_fast_drops_app_without_next_type(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        sent = make_sent_code("SentCodeTypeApp", timeout=120)
        cfg = SimpleNamespace(
            app_delivery_fast_drop=False,
            device_alignment_mode="strict",
            strict_vault_device_alignment=True,
        )
        with patch(
            "backend.app.services.registrar.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=cfg),
        ):
            with self.assertRaises(SentCodeAppDeliveryError) as ctx:
                await RegistrationOrchestrator.resolve_sent_code_channel(
                    SimpleNamespace(), "+56911110001", sent, tid, manager
                )
        self.assertEqual(ctx.exception.reason, "SENT_CODE_TYPE_APP")
        logs = "\n".join(manager.get_task(tid)["logs"])
        self.assertIn("快丢号", logs)


class TestFloodWindowGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SendCodeFloodWindow.get().reset()

    def tearDown(self):
        SendCodeFloodWindow.get().reset()

    def test_hard_trip_blocks_siblings(self):
        gate = SendCodeFloodWindow.get()
        gate.trip(reason="API_ID_PUBLISHED_FLOOD", seconds=12, hard=True, api_id=4)
        self.assertTrue(gate.is_hard_stop())
        self.assertGreaterEqual(gate.remaining(), 12)
        self.assertEqual(gate.api_id(), 4)

    def test_published_hard_hold_defaults_to_pause_not_hour(self):
        gate = SendCodeFloodWindow.get()
        gate.trip(reason="API_ID_PUBLISHED_FLOOD", seconds=0, hard=True, api_id=4)
        # 默认约 DEFAULT_PUBLISHED_FLOOD_PAUSE_SECONDS，不再无条件顶满 3600
        self.assertTrue(gate.is_hard_stop())
        self.assertLess(gate.remaining(), 3600)
        self.assertGreater(gate.remaining(), 60)

    def test_soft_trip_waits_full_seconds(self):
        gate = SendCodeFloodWindow.get()
        gate.trip(reason="FLOOD_WAIT", seconds=120, hard=False)
        self.assertFalse(gate.is_hard_stop())
        self.assertGreater(gate.remaining(), 100)

    async def test_respect_hard_stop_returns_reason(self):
        SendCodeFloodWindow.get().trip(
            reason="API_ID_PUBLISHED_FLOOD", seconds=3600, hard=True, api_id=4
        )
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        stop = await RegistrationOrchestrator._respect_flood_window(tid, manager)
        self.assertEqual(stop, "HUNT_FLOOD_WINDOW")
        logs = "\n".join(manager.get_task(tid)["logs"])
        self.assertIn("跳过本任务的租号/发码", logs)
        self.assertNotIn("停止本任务以免继续填满窗口", logs)
        self.assertIn("不会被 cancel", logs)

    async def test_ignore_published_flood_window_allows_sibling(self):
        SendCodeFloodWindow.get().trip(
            reason="API_ID_PUBLISHED_FLOOD", seconds=3600, hard=True, api_id=4
        )
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        cfg = SimpleNamespace(
            ignore_published_flood_window=True,
            flood_window_scope="process",
            flood_block_new_sends=True,
            published_flood_hold_seconds=120.0,
        )
        stop = await RegistrationOrchestrator._respect_flood_window(
            tid, manager, config=cfg
        )
        self.assertIsNone(stop)
        logs = "\n".join(manager.get_task(tid)["logs"])
        self.assertIn("ignore_published_flood_window=开", logs)
        self.assertIn("通常仍 FLOOD", logs)

    async def test_task_scope_allows_sibling(self):
        SendCodeFloodWindow.get().trip(
            reason="API_ID_PUBLISHED_FLOOD", seconds=3600, hard=True, api_id=4
        )
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        cfg = SimpleNamespace(
            ignore_published_flood_window=False,
            flood_window_scope="task",
            flood_block_new_sends=True,
            published_flood_hold_seconds=120.0,
        )
        stop = await RegistrationOrchestrator._respect_flood_window(
            tid, manager, config=cfg
        )
        self.assertIsNone(stop)
        logs = "\n".join(manager.get_task(tid)["logs"])
        self.assertIn("flood_window_scope=task", logs)

    def test_trip_with_task_scope_does_not_hard_latch(self):
        cfg = SimpleNamespace(
            ignore_published_flood_window=False,
            flood_window_scope="task",
            flood_block_new_sends=True,
            published_flood_hold_seconds=120.0,
        )
        RegistrationOrchestrator._trip_flood_window(
            reason="API_ID_PUBLISHED_FLOOD",
            seconds=0,
            hard=True,
            api_id=4,
            config=cfg,
        )
        gate = SendCodeFloodWindow.get()
        self.assertFalse(gate.is_hard_stop())
        self.assertLess(gate.remaining(), 1.0)

    def test_push_slot_label_is_android_fcm_not_ios_client(self):
        from backend.app.services.device_alignment import describe_push_slot

        label = describe_push_slot(True)
        self.assertIn("android_fcm", label)
        self.assertNotIn("iOS-semantic", label)
        self.assertIn("CodeSettings.token", label)


class TestProxyCountryMatch(unittest.IsolatedAsyncioTestCase):
    def test_labeled_foreign_helper(self):
        us = {"country_code": "us", "country": "US", "addr": "1.1.1.1", "port": 1080}
        unlabeled = {"addr": "127.0.0.1", "port": 10808}
        self.assertTrue(proxy_is_labeled_foreign(us, "id"))
        self.assertFalse(proxy_is_labeled_foreign(us, "us"))
        self.assertFalse(proxy_is_labeled_foreign(unlabeled, "id"))

    async def test_labeled_foreign_fallback_rejected(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        config = SimpleNamespace(
            custom_proxies=[],
            use_proxy_seller_auto=False,
            proxy_require_country_match=True,
            device_alignment_mode="loose",
            strict_vault_device_alignment=False,
            fallback_proxy=SimpleNamespace(
                model_dump=lambda: {
                    "proxy_type": "socks5",
                    "addr": "8.8.8.8",
                    "port": 1080,
                    "country_code": "us",
                    "country": "US",
                }
            ),
        )
        with self.assertRaises(ProxyCountryMismatchError) as ctx:
            await RegistrationOrchestrator.resolve_active_proxy(
                config, "id", tid, manager
            )
        self.assertIn("PROXY_COUNTRY_MISMATCH", str(ctx.exception))

    async def test_unlabeled_fallback_still_allowed(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        config = SimpleNamespace(
            custom_proxies=[],
            use_proxy_seller_auto=False,
            proxy_require_country_match=True,
            device_alignment_mode="loose",
            strict_vault_device_alignment=False,
            fallback_proxy=SimpleNamespace(
                model_dump=lambda: {
                    "proxy_type": "socks5",
                    "addr": "127.0.0.1",
                    "port": 10808,
                }
            ),
        )
        resolved = await RegistrationOrchestrator.resolve_active_proxy(
            config, "id", tid, manager
        )
        self.assertEqual(resolved["addr"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
