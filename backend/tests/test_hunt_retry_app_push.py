"""循环试号：SentCodeTypeApp 换号并复用同一 Push Token。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.models.schemas import RegisterTaskRequest
from backend.app.services.registrar import (
    RegistrationOrchestrator,
    RegistrationTaskManager,
    SentCodeAppDeliveryError,
)


class FakeSms:
    PROVIDER_NAME = "vaksms"

    def __init__(self, numbers):
        self._numbers = list(numbers)
        self.cancel_calls = []
        self.finish_calls = []
        self.closed = False

    async def get_number(self, country, service="tg", max_price=None):
        if not self._numbers:
            raise RuntimeError("no more numbers in fake pool")
        return self._numbers.pop(0)

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "status": "cancel"}

    async def wait_for_code(self, act_id, max_attempts=30, log_callback=None):
        if log_callback:
            await log_callback(f"fake otp for {act_id}")
        return "12345"

    async def finish(self, act_id):
        self.finish_calls.append(act_id)
        return {"success": True}

    async def close(self):
        self.closed = True


class TestHuntRetryAppPush(unittest.IsolatedAsyncioTestCase):
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
            "lang_code": "es",
            "system_lang_code": "es-cl",
            "tz_offset": -14400,
            "credential_source": "custom",
            "is_published_api_id": False,
        }

    def _config(self):
        return SimpleNamespace(
            target_country="cl",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider="vaksms",
            grizzly_sms_api_key="",
            use_proxy_seller_auto=False,
            fallback_proxy=SimpleNamespace(
                model_dump=lambda: {
                    "proxy_type": "socks5",
                    "addr": "127.0.0.1",
                    "port": 10808,
                    "username": None,
                    "password": None,
                }
            ),
            custom_proxies=[],
            phone_precheck_enabled=True,
            api_credential_mode="custom",
            custom_api_id=123456,
            custom_api_hash="hash",
            default_2fa_password="x",
            auto_set_2fa=False,
        )

    async def test_app_delivery_retries_with_same_push_token(self):
        sms = FakeSms([
            ("act-1", "+56911110001"),
        ])
        # 第二次取号故意无库存，证明 APP 换号后未退 Push、且只申请过一次 Token
        from backend.app.services.vaksms import NoNumberAvailableError

        original_get = sms.get_number

        async def get_then_empty(*args, **kwargs):
            if not sms._numbers:
                raise NoNumberAvailableError("NO_NUMBERS")
            return await original_get(*args, **kwargs)

        sms.get_number = get_then_empty

        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN-KEEP", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value="STATUS_RETRY")

        clean = SimpleNamespace(
            intercept=False, is_registered=False, degraded=False, reason="", user_id=None
        )

        cfg_mgr = SimpleNamespace(config=self._config())
        client = MagicMock()
        client.is_connected = MagicMock(return_value=True)
        client.disconnect = AsyncMock()
        client.connect = AsyncMock()

        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch(
                 "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                 side_effect=lambda p, c, has_push_token=False: {**p, "credential_source": "custom"},
             ), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch("backend.app.services.registrar.BannedPhonesCache.remember") as remember, \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(
                 RegistrationOrchestrator,
                 "_send_code_with_recaptcha",
                 new=AsyncMock(side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")),
             ), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch("backend.app.services.registrar.TelegramClient", return_value=client):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
            )

        self.assertEqual(gw.get_push_token.await_count, 1)
        self.assertEqual(sms.cancel_calls, ["act-1"])
        gw.refund_push_token.assert_not_awaited()
        remember.assert_called()
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("复用 Push Token 换号继续", logs)
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task.get("no_number"))

    async def test_last_app_attempt_refunds_push(self):
        sms = FakeSms([("act-1", "+56911110001")])
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-9", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value="RETRY_PUSH")

        clean = SimpleNamespace(
            intercept=False, is_registered=False, degraded=False, reason="", user_id=None
        )
        cfg_mgr = SimpleNamespace(config=self._config())
        client = MagicMock()
        client.is_connected = MagicMock(return_value=True)
        client.disconnect = AsyncMock()

        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch(
                 "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                 side_effect=lambda p, c, has_push_token=False: {**p, "credential_source": "custom"},
             ), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch("backend.app.services.registrar.BannedPhonesCache.remember"), \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(
                 RegistrationOrchestrator,
                 "_send_code_with_recaptcha",
                 new=AsyncMock(side_effect=SentCodeAppDeliveryError("app", reason="SENT_CODE_TYPE_APP")),
             ), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch("backend.app.services.registrar.TelegramClient", return_value=client), \
             patch.dict("os.environ", {"EDGENODE_SKIP_PUSH_REFUND_WAIT": "1"}):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=1,
            )

        gw.refund_push_token.assert_awaited()
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("SENT_CODE_TYPE_APP", task.get("error", ""))


class TestHuntSchema(unittest.TestCase):
    def test_max_number_attempts_bounds(self):
        ok = RegisterTaskRequest(country="cl", max_number_attempts=100)
        self.assertEqual(ok.max_number_attempts, 100)
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            RegisterTaskRequest(country="cl", max_number_attempts=0)
        with self.assertRaises(ValidationError):
            RegisterTaskRequest(country="cl", max_number_attempts=501)

    def test_no_number_retries_field(self):
        ok = RegisterTaskRequest(country="cl", no_number_retries=20)
        self.assertEqual(ok.no_number_retries, 20)


class TestHuntLimitsHelpers(unittest.IsolatedAsyncioTestCase):
    def test_resolve_hunt_limits_defaults(self):
        cfg = SimpleNamespace()
        limits = RegistrationOrchestrator._resolve_hunt_limits(cfg)
        self.assertEqual(limits["no_number_retries"], 20)
        self.assertEqual(limits["proxy_max_uses"], 5)
        self.assertEqual(limits["device_max_uses"], 8)

    async def test_lease_number_retries_then_raises(self):
        from backend.app.services.vaksms import NoNumberAvailableError

        calls = {"n": 0}

        class Sms:
            async def get_number(self, **kwargs):
                calls["n"] += 1
                raise NoNumberAvailableError("NO_NUMBERS")

        manager = RegistrationTaskManager()
        manager.tasks = {}
        prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = manager
        tid = manager.create_task()
        try:
            with self.assertRaises(NoNumberAvailableError):
                await RegistrationOrchestrator._lease_number_with_retries(
                    Sms(),
                    "cl",
                    None,
                    tid,
                    manager,
                    hunt_enabled=True,
                    no_number_retries=2,
                    no_number_delay=0.0,
                )
            self.assertEqual(calls["n"], 3)
        finally:
            RegistrationTaskManager._instance = prev
