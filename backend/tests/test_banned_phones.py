"""本地封禁号缓存、号段画像与租号后拦截门闩测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import (  # noqa: E402
    BannedPhonesCacheStatusResponse,
    TaskStatusResponse,
)
from backend.app.services.banned_phones import (  # noqa: E402
    LOCAL_BANNED_REASON,
    SOURCE_ANTISAFETY,
    SOURCE_TELEGRAM_RPC,
    BannedPhonesCache,
    infer_country,
    normalize_digits,
    prefix_of,
)
from backend.app.services.phone_precheck import PhonePrecheckService  # noqa: E402
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)
from telethon.errors import PhoneNumberBannedError  # noqa: E402


class FakeSms:
    def __init__(self):
        self.cancel_calls = []

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": "bad"}

    async def close(self):
        return None


class FakeCache:
    def __init__(self, record=None):
        self.record = record
        self.lookups = []

    def lookup(self, phone):
        self.lookups.append(phone)
        return self.record


class TestBannedPhonesHelpers(unittest.TestCase):
    def test_normalize_and_prefix(self):
        self.assertEqual(normalize_digits("+62 838-5698-2093"), "6283856982093")
        self.assertEqual(prefix_of("6283856982093"), "628385")
        self.assertEqual(infer_country("6283856982093"), "id")
        self.assertEqual(infer_country("918310013712"), "in")

    def test_remember_lookup_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_phones_cache.json"
            BannedPhonesCache.reset_memory()
            first = BannedPhonesCache.remember(
                "+6283856982093",
                reason="PHONE_NUMBER_BANNED",
                source=SOURCE_TELEGRAM_RPC,
                country="id",
                path=path,
            )
            self.assertIsNotNone(first)
            self.assertEqual(first.digits, "6283856982093")
            self.assertEqual(first.hits, 1)
            self.assertEqual(first.prefix, "628385")

            BannedPhonesCache.reset_memory()
            hit = BannedPhonesCache.lookup("+62 83856982093", path=path)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.reason, "PHONE_NUMBER_BANNED")
            self.assertEqual(hit.source, SOURCE_TELEGRAM_RPC)

            again = BannedPhonesCache.remember(
                "6283856982093",
                reason="PHONE_NUMBER_BANNED",
                source=SOURCE_TELEGRAM_RPC,
                path=path,
            )
            self.assertEqual(again.hits, 2)

            stats = BannedPhonesCache.prefix_stats(path=path)
            self.assertEqual(stats[0]["prefix"], "628385")
            self.assertEqual(stats[0]["count"], 1)
            self.assertEqual(BannedPhonesCache.country_stats(path=path)[0]["country"], "id")

            status = BannedPhonesCache.describe_status(path=path)
            parsed = BannedPhonesCacheStatusResponse(**status.to_dict())
            self.assertEqual(parsed.size, 1)
            self.assertIn("628385", parsed.message)
            self.assertEqual(first.category, "banned")

    def test_already_registered_and_list_manage(self):
        from backend.app.services.banned_phones import (
            CATEGORY_ALREADY_REGISTERED,
            SOURCE_PRECHECK,
            SOURCE_SENT_CODE,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_phones_cache.json"
            BannedPhonesCache.reset_memory()
            BannedPhonesCache.remember(
                "+27821234567",
                reason="SENT_CODE_TYPE_APP",
                source=SOURCE_SENT_CODE,
                country="za",
                path=path,
            )
            BannedPhonesCache.remember(
                "+573001112233",
                reason="PRECHECK_PHONE_ALREADY_REGISTERED",
                source=SOURCE_PRECHECK,
                path=path,
            )
            BannedPhonesCache.remember(
                "+6283856982093",
                reason="MANUAL_BLACKLIST",
                source="manual",
                category="manual",
                note="ops",
                path=path,
            )
            items, total = BannedPhonesCache.list_items(path=path)
            self.assertEqual(total, 3)
            summary = BannedPhonesCache.summary(path=path)
            self.assertEqual(summary["already_registered"], 2)
            self.assertEqual(summary["manual"], 1)

            filtered, n = BannedPhonesCache.list_items(
                category=CATEGORY_ALREADY_REGISTERED, path=path,
            )
            self.assertEqual(n, 2)
            self.assertTrue(all(r["category"] == CATEGORY_ALREADY_REGISTERED for r in filtered))

            za_items, za_n = BannedPhonesCache.list_items(country="za", path=path)
            self.assertEqual(za_n, 1)
            self.assertEqual(za_items[0]["digits"], "27821234567")

            self.assertTrue(BannedPhonesCache.remove("+573001112233", path=path))
            self.assertEqual(BannedPhonesCache.size(path=path), 2)
            deleted = BannedPhonesCache.purge(category="manual", path=path)
            self.assertEqual(deleted, 1)
            self.assertEqual(BannedPhonesCache.size(path=path), 1)

    def test_unknown_phone_is_not_banned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_phones_cache.json"
            BannedPhonesCache.reset_memory()
            self.assertIsNone(BannedPhonesCache.lookup("+56911112222", path=path))

    def test_category_priority_prefers_banned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_phones_cache.json"
            BannedPhonesCache.reset_memory()
            BannedPhonesCache.remember(
                "+27820001111",
                reason="SENT_CODE_TYPE_APP",
                source="sent_code_app",
                path=path,
            )
            again = BannedPhonesCache.remember(
                "+27820001111",
                reason="PHONE_NUMBER_BANNED",
                source=SOURCE_TELEGRAM_RPC,
                path=path,
            )
            self.assertEqual(again.category, "banned")
            self.assertEqual(again.hits, 2)


class TestRegistrarBannedCacheGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self.task_id = self.manager.create_task()
        self.sms = FakeSms()

    async def test_cache_hit_refunds_without_continuing(self):
        record = SimpleNamespace(
            reason="PHONE_NUMBER_BANNED",
            source=SOURCE_TELEGRAM_RPC,
            hits=3,
        )
        should_continue = await RegistrationOrchestrator._apply_banned_cache_gate(
            phone="+6283856982093",
            act_id="act-banned",
            sms_svc=self.sms,
            task_id=self.task_id,
            manager=self.manager,
            cache=FakeCache(record),
        )
        self.assertFalse(should_continue)
        self.assertEqual(self.sms.cancel_calls, ["act-banned"])
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "filtered")
        self.assertTrue(task["banned_cache_hit"])
        self.assertIn(LOCAL_BANNED_REASON, task["error"])
        logs = "\n".join(task["logs"])
        self.assertIn("[号码黑名单拦截]", logs)
        self.assertIn("[自动退订/撤销信道句柄完成]", logs)
        TaskStatusResponse(**{k: task[k] for k in TaskStatusResponse.model_fields if k in task})

    async def test_cache_miss_continues(self):
        should_continue = await RegistrationOrchestrator._apply_banned_cache_gate(
            phone="+56911112222",
            act_id="act-clean",
            sms_svc=self.sms,
            task_id=self.task_id,
            manager=self.manager,
            cache=FakeCache(None),
        )
        self.assertTrue(should_continue)
        self.assertEqual(self.sms.cancel_calls, [])


class TestRunRegistrationBannedCacheTiming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    def _profile(self):
        return {
            "name": "test",
            "aid": "aid-1",
            "api_id": 123456,
            "api_hash": "hash",
            "device_model": "Pixel",
            "system_version": "SDK 33",
            "app_version": "12.0",
            "lang_code": "id",
            "system_lang_code": "id-id",
            "tz_offset": 25200,
            "credential_source": "custom",
            "is_published_api_id": False,
        }

    def _config(self):
        return SimpleNamespace(
            target_country="id",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider="vaksms",
            grizzly_sms_api_key="",
            use_proxy_seller_auto=False,
            fallback_proxy=SimpleNamespace(model_dump=lambda: {
                "proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808,
                "username": None, "password": None,
            }),
            custom_proxies=[],
            phone_precheck_enabled=True,
            api_credential_mode="custom",
            custom_api_id=123456,
            custom_api_hash="hash",
            default_2fa_password="x",
            auto_set_2fa=False,
        )

    async def test_cached_banned_skips_precheck_and_push_token(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-ban", "+6283856982093"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value=None)
        record = SimpleNamespace(
            reason="PHONE_NUMBER_BANNED",
            source=SOURCE_TELEGRAM_RPC,
            hits=1,
        )

        cfg_mgr = SimpleNamespace(config=self._config())
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=record), \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock()) as precheck, \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "_send_code_with_recaptcha", new=AsyncMock()) as send_code, \
             patch("backend.app.services.registrar.TelegramClient") as tg_cls:
            await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="id")

        precheck.assert_not_awaited()
        gw.get_push_token.assert_not_awaited()
        gw.check_phone_history.assert_not_awaited()
        send_code.assert_not_awaited()
        tg_cls.assert_not_called()
        self.assertEqual(sms.cancel_calls, ["act-ban"])
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "filtered")
        self.assertTrue(task["banned_cache_hit"])

    async def test_telegram_banned_is_remembered(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-live", "+6283856982093"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value=None)
        clean = PhonePrecheckService.result_from_user(
            "+6283856982093", None, method="resolve_phone"
        )

        cfg_mgr = SimpleNamespace(config=self._config())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_phones_cache.json"
            BannedPhonesCache.reset_memory()
            with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
                 patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
                 patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
                 patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
                 patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
                 patch("backend.app.services.banned_phones.CACHE_PATH", path), \
                 patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
                 patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
                 patch.object(
                     RegistrationOrchestrator,
                     "_send_code_with_recaptcha",
                     new=AsyncMock(side_effect=PhoneNumberBannedError(request=None)),
                 ), \
                 patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
                 patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
                 patch.object(RegistrationOrchestrator, "_release_registration_resources", new=AsyncMock()), \
                 patch("backend.app.services.registrar.TelegramClient") as tg_cls:
                client = MagicMock()
                client.is_connected = lambda: False
                client.disconnect = AsyncMock()
                tg_cls.return_value = client
                await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="id")

            remembered = BannedPhonesCache.lookup("+6283856982093", path=path)
            self.assertIsNotNone(remembered)
            self.assertEqual(remembered.reason, "PHONE_NUMBER_BANNED")
            self.assertEqual(remembered.source, SOURCE_TELEGRAM_RPC)
            self.assertEqual(remembered.country, "id")
            gw.report_result.assert_not_awaited()
            self.assertEqual(sms.cancel_calls, ["act-live"])
            task = self.manager.get_task(self.task_id)
            self.assertEqual(task["status"], "failed")
            self.assertIn("PHONE_NUMBER_BANNED", task["error"])


class TestAntiSafetyBannedIsRemembered(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_antisafety_history_banned_writes_cache(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-hist", "+628111111111"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value={"id": "chk-1", "statuses": ["BANNED"]})
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value=None)
        clean = PhonePrecheckService.result_from_user(
            "+628111111111", None, method="resolve_phone"
        )
        cfg_mgr = SimpleNamespace(config=SimpleNamespace(
            target_country="id",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider="vaksms",
            grizzly_sms_api_key="",
            use_proxy_seller_auto=False,
            fallback_proxy=SimpleNamespace(model_dump=lambda: {
                "proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808,
                "username": None, "password": None,
            }),
            custom_proxies=[],
            phone_precheck_enabled=True,
            api_credential_mode="custom",
            custom_api_id=123456,
            custom_api_hash="hash",
            default_2fa_password="x",
            auto_set_2fa=False,
        ))
        profile = {
            "name": "test", "aid": "aid-1", "api_id": 123456, "api_hash": "hash",
            "device_model": "Pixel", "system_version": "SDK 33", "app_version": "12.0",
            "lang_code": "id", "system_lang_code": "id-id", "tz_offset": 25200,
            "credential_source": "custom", "is_published_api_id": False,
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_phones_cache.json"
            BannedPhonesCache.reset_memory()
            with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
                 patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
                 patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
                 patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=profile), \
                 patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
                 patch("backend.app.services.banned_phones.CACHE_PATH", path), \
                 patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
                 patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)):
                await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="id")

            remembered = BannedPhonesCache.lookup("+628111111111", path=path)
            self.assertIsNotNone(remembered)
            self.assertEqual(remembered.source, SOURCE_ANTISAFETY)
            self.assertEqual(remembered.reason, "PHONE_PREAUDIT_BANNED")
            gw.get_push_token.assert_not_awaited()
            gw.report_result.assert_awaited()
            self.assertEqual(sms.cancel_calls, ["act-hist"])


if __name__ == "__main__":
    unittest.main()
