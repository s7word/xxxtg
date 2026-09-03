"""号码注册状态预检、Push Token 时机与 noNumber 优雅告警单元测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import (  # noqa: E402
    AppConfigModel,
    BatchStatusResponse,
    PhonePrecheckStatusResponse,
    TaskStatusResponse,
)
from backend.app.services.phone_precheck import (  # noqa: E402
    CLEAN_LOG_TEMPLATE,
    DEGRADE_LOG_TEMPLATE,
    PRECHECK_ALREADY_REGISTERED,
    PRECHECK_DEGRADED,
    PhonePrecheckResult,
    PhonePrecheckService,
    format_precheck_intercept_log,
)
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)
from backend.app.services.vaksms import (  # noqa: E402
    NoNumberAvailableError,
    VakSmsService,
    format_no_number_message,
    is_no_number_error,
)


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeAccount:
    def __init__(self, phone="+918310013712", has_session=True, app_id=4, app_hash="hash"):
        self.account_id = f"probe-{phone}"
        self.phone = phone
        self.phone_raw = phone.lstrip("+")
        self.has_session = has_session
        self.app_id = app_id
        self.app_hash = app_hash
        self.is_probe_active = True
        self.device_model = "Samsung SM-G950F"
        self.system_version = "SDK 33"
        self.app_version = "12.7.3"
        self.system_lang_code = "en"


class FakeProbeClient:
    def __init__(self, users=None, error=None, import_users=None):
        self.users = list(users or [])
        self.import_users = list(import_users or [])
        self.error = error
        self.calls = []

    async def __call__(self, req):
        self.calls.append(type(req).__name__)
        name = type(req).__name__
        if name == "ResolvePhoneRequest":
            if self.error is not None:
                raise self.error
            return SimpleNamespace(users=self.users, peer=None)
        if name == "ImportContactsRequest":
            if self.error is not None:
                raise self.error
            return SimpleNamespace(users=self.import_users, imported=[])
        if name == "DeleteContactsRequest":
            return True
        raise AssertionError(f"unexpected request {name}")


class FakeSms:
    def __init__(self):
        self.cancel_calls = []
        self.finish_calls = []

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": "bad"}

    async def finish(self, act_id):
        self.finish_calls.append(act_id)

    async def close(self):
        return None


class FakePrecheck:
    def __init__(self, result: PhonePrecheckResult):
        self.result = result
        self.calls = []

    async def check_phone(self, phone, **kwargs):
        self.calls.append(phone)
        return self.result


class TestNoNumberHelpers(unittest.TestCase):
    def test_detects_nonumber_aliases(self):
        self.assertTrue(is_no_number_error("noNumber"))
        self.assertTrue(is_no_number_error("no_number"))
        self.assertTrue(is_no_number_error("NO NUMBER"))
        self.assertFalse(is_no_number_error("noMoney"))
        self.assertFalse(is_no_number_error(""))

    def test_friendly_message_mentions_country_and_hints(self):
        msg = format_no_number_message("cl")
        self.assertIn("CL", msg)
        self.assertIn("noNumber", msg)
        self.assertIn("ID 印尼", msg)
        self.assertIn("KZ", msg)
        self.assertIn("RU", msg)

    def test_get_number_raises_no_number(self):
        svc = VakSmsService("vak-key")
        svc.client.get = AsyncMock(return_value=DummyResponse({"error": "noNumber"}))
        try:
            with self.assertRaises(NoNumberAvailableError) as ctx:
                asyncio.run(svc.get_number(country="cl", service="tg"))
            self.assertIn("CL", str(ctx.exception))
            self.assertIn("noNumber", str(ctx.exception))
        finally:
            svc.client.aclose = AsyncMock()
            asyncio.run(svc.close())


class TestPhonePrecheckInterpret(unittest.TestCase):
    def test_resolve_registered_user(self):
        result = SimpleNamespace(users=[SimpleNamespace(id=6125786846, username="alice", first_name="Ali")])
        user = PhonePrecheckService.interpret_resolve_result(result)
        self.assertEqual(user["user_id"], 6125786846)
        self.assertEqual(user["username"], "alice")

    def test_resolve_empty_means_not_registered(self):
        self.assertIsNone(PhonePrecheckService.interpret_resolve_result(SimpleNamespace(users=[])))

    def test_import_registered_user(self):
        result = SimpleNamespace(users=[SimpleNamespace(id=99, username=None, first_name="Bob")], imported=[])
        user = PhonePrecheckService.interpret_import_result(result)
        self.assertEqual(user["user_id"], 99)

    def test_status_active_when_probe_sessions_exist(self):
        acc = FakeAccount()
        with patch.object(PhonePrecheckService, "list_probe_accounts", return_value=[acc]):
            status = PhonePrecheckService.describe_status(
                config=SimpleNamespace(phone_precheck_enabled=True),
                accounts=[acc],
            )
        self.assertTrue(status.enabled)
        self.assertTrue(status.active)
        self.assertIn("已激活", status.message)
        payload = PhonePrecheckStatusResponse(**status.to_dict())
        self.assertEqual(payload.probe_count, 1)

    def test_status_degraded_without_probe_session(self):
        with patch.object(PhonePrecheckService, "list_probe_accounts", return_value=[]):
            status = PhonePrecheckService.describe_status(
                config=SimpleNamespace(phone_precheck_enabled=True),
                accounts=[],
            )
        self.assertTrue(status.degraded)
        self.assertFalse(status.active)
        self.assertIn("降级", status.message)

    def test_list_probe_accounts_requires_session_and_credentials(self):
        good = FakeAccount()
        no_sess = FakeAccount(has_session=False)
        no_hash = FakeAccount(app_hash=None)
        with patch(
            "backend.app.services.phone_precheck.AccountVaultService.resolve_session_file",
            side_effect=lambda acc: Path("/tmp/fake.session") if acc.has_session and acc.app_hash else None,
        ):
            probes = PhonePrecheckService.list_probe_accounts([good, no_sess, no_hash])
        self.assertEqual(len(probes), 1)
        self.assertIs(probes[0], good)

    def test_config_model_defaults_precheck_enabled(self):
        cfg = AppConfigModel()
        self.assertTrue(cfg.phone_precheck_enabled)


class TestPhonePrecheckQuery(unittest.IsolatedAsyncioTestCase):
    async def test_probe_client_registered_via_resolve(self):
        client = FakeProbeClient(users=[SimpleNamespace(id=111, username="old", first_name="X")])
        result = await PhonePrecheckService.check_phone(
            "+56912345678",
            probe_client=client,
            enabled=True,
        )
        self.assertTrue(result.available)
        self.assertTrue(result.is_registered)
        self.assertTrue(result.intercept)
        self.assertEqual(result.user_id, 111)
        self.assertEqual(result.method, "resolve_phone")
        self.assertIn("ResolvePhoneRequest", client.calls)

    async def test_probe_client_unregistered_is_clean(self):
        from telethon.errors import PhoneNumberUnoccupiedError

        client = FakeProbeClient(error=PhoneNumberUnoccupiedError(request=None))
        result = await PhonePrecheckService.check_phone(
            "56912345678",
            probe_client=client,
            enabled=True,
        )
        self.assertTrue(result.available)
        self.assertFalse(result.is_registered)
        self.assertFalse(result.intercept)
        self.assertEqual(result.reason, "PRECHECK_PHONE_CLEAN")

    async def test_no_probe_session_degrades(self):
        result = await PhonePrecheckService.check_phone(
            "+56912345678",
            accounts=[],
            enabled=True,
        )
        self.assertTrue(result.degraded)
        self.assertIsNone(result.is_registered)
        self.assertEqual(result.reason, PRECHECK_DEGRADED)

    async def test_disabled_degrades(self):
        result = await PhonePrecheckService.check_phone(
            "+56912345678",
            enabled=False,
        )
        self.assertTrue(result.degraded)
        self.assertEqual(result.reason, "PRECHECK_DISABLED")


class TestRegistrarPrecheckGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self.task_id = self.manager.create_task()
        self.sms = FakeSms()

    async def test_registered_phone_refunds_without_continuing(self):
        fake = FakePrecheck(PhonePrecheckService.result_from_user(
            "+56911112222",
            {"user_id": 6125786846, "username": "olduser"},
            method="resolve_phone",
        ))
        with patch("backend.app.services.registrar.BannedPhonesCache.remember") as remember:
            should_continue = await RegistrationOrchestrator._apply_phone_precheck(
                phone="+56911112222",
                act_id="act-precheck",
                sms_svc=self.sms,
                task_id=self.task_id,
                manager=self.manager,
                precheck_svc=fake,
            )
        self.assertFalse(should_continue)
        remember.assert_called_once()
        self.assertEqual(remember.call_args.kwargs.get("category"), "already_registered")
        self.assertEqual(self.sms.cancel_calls, ["act-precheck"])
        task = self.manager.get_task(self.task_id)
        self.assertTrue(task["precheck_intercepted"])
        self.assertEqual(task["precheck_user_id"], 6125786846)
        self.assertEqual(task["status"], "filtered")
        self.assertIn(PRECHECK_ALREADY_REGISTERED, task["error"])
        logs = "\n".join(task["logs"])
        self.assertIn("[预检拦截]", logs)
        self.assertIn("6125786846", logs)
        self.assertIn("不消耗 Push Token", logs)
        self.assertIn("[自动退订/撤销信道句柄完成]", logs)

    async def test_clean_phone_continues(self):
        fake = FakePrecheck(PhonePrecheckService.result_from_user(
            "+56911112222", None, method="resolve_phone"
        ))
        should_continue = await RegistrationOrchestrator._apply_phone_precheck(
            phone="+56911112222",
            act_id="act-clean",
            sms_svc=self.sms,
            task_id=self.task_id,
            manager=self.manager,
            precheck_svc=fake,
        )
        self.assertTrue(should_continue)
        self.assertEqual(self.sms.cancel_calls, [])
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("白号", logs)
        self.assertIn("+56911112222", logs)

    async def test_no_probe_session_degrades_and_continues(self):
        fake = FakePrecheck(PhonePrecheckService.degraded_result(PRECHECK_DEGRADED))
        should_continue = await RegistrationOrchestrator._apply_phone_precheck(
            phone="+56911112222",
            act_id="act-deg",
            sms_svc=self.sms,
            task_id=self.task_id,
            manager=self.manager,
            precheck_svc=fake,
        )
        self.assertTrue(should_continue)
        self.assertEqual(self.sms.cancel_calls, [])
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("优雅降级", logs)


class TestRunRegistrationPrecheckTiming(unittest.IsolatedAsyncioTestCase):
    """确认预检拦截发生在 Push Token / sendCode 之前。"""

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
            # 本组用例断言的是「预检是否放行到申请 Push / sendCode」，
            # 所以固定走会真的申请 Token 的通道模式，避免被投递策略掩盖
            code_delivery_mode="push_required",
        )

    async def test_registered_skips_push_token_and_sendcode(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-reg", "+56911112222"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value=None)

        registered = PhonePrecheckService.result_from_user(
            "+56911112222",
            {"user_id": 42, "username": "used"},
            method="resolve_phone",
        )

        cfg_mgr = SimpleNamespace(config=self._config())
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=registered)), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch("backend.app.services.registrar.BannedPhonesCache.remember") as remember, \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "_send_code_with_recaptcha", new=AsyncMock()) as send_code, \
             patch("backend.app.services.registrar.TelegramClient") as tg_cls:
            await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="cl")

        gw.get_push_token.assert_not_awaited()
        gw.check_phone_history.assert_not_awaited()
        send_code.assert_not_awaited()
        tg_cls.assert_not_called()
        remember.assert_called()
        self.assertEqual(sms.cancel_calls, ["act-reg"])
        task = self.manager.get_task(self.task_id)
        self.assertTrue(task["precheck_intercepted"])
        self.assertEqual(task["status"], "filtered")
        self.assertIn(PRECHECK_ALREADY_REGISTERED, task["error"])

    async def test_clean_phone_requests_push_token(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-clean", "+56911112222"))
        sms.wait_for_code = AsyncMock(side_effect=TimeoutError("no sms"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value=None)

        clean = PhonePrecheckService.result_from_user(
            "+56911112222", None, method="resolve_phone"
        )
        sent = SimpleNamespace(
            type=type("SentCodeTypeSms", (), {})(),
            next_type=None,
            timeout=None,
            phone_code_hash="h",
        )
        fake_client = MagicMock()
        fake_client.is_connected = lambda: False
        fake_client.disconnect = AsyncMock()

        cfg_mgr = SimpleNamespace(config=self._config())
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch(
                 "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                 side_effect=lambda profile, config, has_push_token=False: {**profile, "credential_source": "custom"},
             ), \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(RegistrationOrchestrator, "_send_code_with_recaptcha", new=AsyncMock(return_value=sent)) as send_code, \
             patch.object(RegistrationOrchestrator, "resolve_sent_code_channel", new=AsyncMock(return_value=(sent, 3))), \
             patch("backend.app.services.registrar.TelegramClient", return_value=fake_client):
            await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="cl")

        gw.get_push_token.assert_awaited()
        send_code.assert_awaited()
        self.assertEqual(sms.cancel_calls, ["act-clean"])  # timeout path refunds
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("白号", logs)
        self.assertIn("Push Token", logs)

    async def test_nonumber_graceful_exit_no_push_token(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(side_effect=NoNumberAvailableError("cl", {"error": "noNumber"}))
        gw = MagicMock()
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-1", "reghelp"))
        gw.close = AsyncMock()

        cfg_mgr = SimpleNamespace(config=self._config())
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch("backend.app.services.registrar.TelegramClient") as tg_cls:
            await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="cl")

        gw.get_push_token.assert_not_awaited()
        tg_cls.assert_not_called()
        self.assertEqual(sms.cancel_calls, [])
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task["no_number"])
        self.assertIn("noNumber", task["error"])
        self.assertIn("CL", task["error"])
        logs = "\n".join(task["logs"])
        self.assertIn("建议在控制台切换", logs)


class TestBatchPrecheckStats(unittest.TestCase):
    def test_batch_counts_precheck_intercepts(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        manager.batches = {}
        batch_id, task_ids = manager.create_batch(count=3, concurrency=2, country="cl")
        manager.update_task_status(
            task_ids[0], "filtered",
            precheck_intercepted=True,
            error="PRECHECK_PHONE_ALREADY_REGISTERED: x",
        )
        manager.update_task_status(task_ids[1], "failed", no_number=True, error="noNumber")
        manager.update_task_status(task_ids[2], "success")
        batch = manager.get_batch(batch_id)
        self.assertEqual(batch["precheck_intercepted"], 1)
        self.assertEqual(batch["no_number"], 1)
        self.assertEqual(batch["success"], 1)
        payload = BatchStatusResponse(**batch)
        self.assertEqual(payload.precheck_intercepted, 1)
        self.assertEqual(payload.no_number, 1)

    def test_task_status_schema_accepts_precheck_fields(self):
        item = TaskStatusResponse(
            task_id="abcd1234",
            status="filtered",
            phone="+56911112222",
            error="PRECHECK_PHONE_ALREADY_REGISTERED",
            logs=["[预检拦截]"],
            precheck_intercepted=True,
            precheck_user_id=42,
            created_at="2026-08-23T00:00:00",
            updated_at="2026-08-23T00:00:01",
        )
        self.assertTrue(item.precheck_intercepted)
        self.assertEqual(item.precheck_user_id, 42)

    def test_intercept_log_template(self):
        text = format_precheck_intercept_log("+56900", 7)
        self.assertIn("[预检拦截]", text)
        self.assertIn("+56900", text)
        self.assertIn("uid=7", text)
        self.assertIn("不消耗 Push Token", text)
        self.assertIn("白号", CLEAN_LOG_TEMPLATE)
        self.assertIn("降级", DEGRADE_LOG_TEMPLATE)


class TestPhonePrecheckApiRoute(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from backend.app.api.routes import router

        app = FastAPI()
        app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_status_endpoint(self):
        fake = SimpleNamespace(
            to_dict=lambda: {
                "enabled": True,
                "active": True,
                "probe_count": 2,
                "probe_phones": ["+9183****3712"],
                "degraded": False,
                "message": "号码白号预检探测器已激活（2 个授权 session）",
            }
        )
        with patch(
            "backend.app.api.routes.PhonePrecheckService.describe_status",
            return_value=fake,
        ):
            res = await self.client.get("/api/phone-precheck/status")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["active"])
        self.assertEqual(data["probe_count"], 2)
        self.assertIn("已激活", data["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
