"""Grizzly SMS 客户端、国家映射、状态码与注册流水线接入测试。"""
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
    BatchRegisterRequest,
    RegisterBatchRequest,
    RegisterTaskRequest,
    format_sms_max_price,
    normalize_sms_max_price,
)
from backend.app.services.grizzlysms import (  # noqa: E402
    GrizzlySmsError,
    GrizzlySmsService,
    InsufficientBalanceError,
    grizzly_country_id_to_iso,
    resolve_country_iso2,
    resolve_grizzly_country_id,
)
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)
from backend.app.services.vaksms import NoNumberAvailableError  # noqa: E402


class DummyResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json
        return json.loads(self.text)


class FakeSms:
    def __init__(self):
        self.cancel_calls = []
        self.finish_calls = []
        self.PROVIDER_NAME = "grizzlysms"
        self.PROVIDER_LABEL = "Grizzly SMS (grizzlysms.com)"

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": 8}

    async def finish(self, act_id):
        self.finish_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": 6}

    async def close(self):
        return None


class TestGrizzlyCountryMapping(unittest.TestCase):
    def test_authoritative_iso2_ids(self):
        expected = {
            "in": 22, "id": 6, "cl": 151, "ca": 36, "us": 187,
            "ru": 0, "kz": 2, "gb": 16, "br": 73, "co": 33,
            "vn": 10, "th": 52, "ph": 4,
        }
        for iso, cid in expected.items():
            self.assertEqual(resolve_grizzly_country_id(iso), cid)
            self.assertEqual(grizzly_country_id_to_iso(cid), iso if iso != "gb" else "gb")

    def test_uk_alias_and_us_virtual_id(self):
        self.assertEqual(resolve_grizzly_country_id("uk"), 16)
        self.assertEqual(resolve_grizzly_country_id(12), 187)
        self.assertEqual(grizzly_country_id_to_iso(12), "us")
        self.assertEqual(grizzly_country_id_to_iso(187), "us")

    def test_iso3_and_name_inference(self):
        self.assertEqual(resolve_country_iso2("IND"), "in")
        self.assertEqual(resolve_country_iso2("Chile"), "cl")
        self.assertEqual(resolve_country_iso2("印度"), "in")
        self.assertEqual(resolve_country_iso2("加拿大"), "ca")
        self.assertEqual(resolve_grizzly_country_id("CHL"), 151)
        self.assertEqual(resolve_grizzly_country_id("india"), 22)
        self.assertEqual(resolve_country_iso2("伊拉克"), "iq")
        self.assertEqual(resolve_country_iso2("IRQ"), "iq")
        self.assertEqual(resolve_grizzly_country_id("iq"), 47)

    def test_numeric_passthrough_and_unknown(self):
        self.assertEqual(resolve_grizzly_country_id("22"), 22)
        self.assertEqual(resolve_grizzly_country_id(6), 6)
        with self.assertRaises(GrizzlySmsError):
            resolve_grizzly_country_id("zz-unknown-land")


class TestGrizzlySmsClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.svc = GrizzlySmsService("test-key")
        self.svc.client.get = AsyncMock()

    async def asyncTearDown(self):
        self.svc.client.aclose = AsyncMock()
        await self.svc.close()

    async def test_get_balance_parses_access_balance(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_BALANCE:32.5000")
        balance = await self.svc.get_balance()
        self.assertAlmostEqual(balance, 32.5)
        args, kwargs = self.svc.client.get.await_args
        self.assertEqual(args[0], GrizzlySmsService.BASE_URL)
        self.assertEqual(kwargs["params"]["action"], "getBalance")
        self.assertEqual(kwargs["params"]["api_key"], "test-key")

    async def test_get_number_success(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:88421:919876543210")
        act_id, phone = await self.svc.get_number(country="in", service="tg")
        self.assertEqual(act_id, "88421")
        self.assertEqual(phone, "+919876543210")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "getNumber")
        self.assertEqual(params["country"], 22)
        self.assertEqual(params["service"], "tg")
        self.assertNotIn("maxPrice", params)
        self.assertNotIn("max_price", params)

    async def test_get_number_with_max_price_sends_both_aliases(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:91001:9647782082712")
        act_id, phone = await self.svc.get_number(country="iq", service="tg", max_price=1.0)
        self.assertEqual(act_id, "91001")
        self.assertEqual(phone, "+9647782082712")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "getNumber")
        self.assertEqual(params["country"], 47)
        self.assertEqual(params["service"], "tg")
        self.assertEqual(params["maxPrice"], "1")
        self.assertEqual(params["max_price"], "1")

    async def test_get_number_usd_decimal_max_price_0_6(self):
        """美元账户伊拉克实测：maxPrice=0.6 立即返回 ACCESS_NUMBER。"""
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:579688409:9647724612701")
        with self.assertLogs("GrizzlySmsService", level="INFO") as cm:
            act_id, phone = await self.svc.get_number(country="iq", service="tg", max_price=0.6)
        self.assertEqual(act_id, "579688409")
        self.assertEqual(phone, "+9647724612701")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["country"], 47)
        self.assertEqual(params["maxPrice"], "0.6")
        self.assertEqual(params["max_price"], "0.6")
        self.assertTrue(any(isinstance(v, str) for v in (params["maxPrice"], params["max_price"])))
        self.assertTrue(
            any("country=47/IQ" in line and "maxPrice=0.6" in line for line in cm.output),
            msg=cm.output,
        )

    async def test_get_number_usd_decimal_max_price_0_53(self):
        """网页端伊拉克标价 $0.5294，出价 0.53 必须原样以浮点字符串发出。"""
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:579688424:9647706110433")
        act_id, phone = await self.svc.get_number(country=47, service="tg", max_price="0.53")
        self.assertEqual(act_id, "579688424")
        self.assertEqual(phone, "+9647706110433")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["maxPrice"], "0.53")
        self.assertEqual(params["max_price"], "0.53")
        self.assertNotEqual(params["maxPrice"], 50)
        self.assertNotEqual(params["maxPrice"], "50")

    async def test_get_number_with_operator_and_max_price(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:77:9647700111222")
        await self.svc.get_number(country="iraq", service="tg", operator="asiacell", max_price="1.0")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["operator"], "asiacell")
        self.assertEqual(params["maxPrice"], "1")
        self.assertEqual(params["max_price"], "1")

    async def test_get_number_ignores_non_positive_max_price(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:1:919000000000")
        await self.svc.get_number(country="in", max_price=0)
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertNotIn("maxPrice", params)
        self.assertNotIn("max_price", params)

    async def test_get_number_no_numbers(self):
        self.svc.client.get.return_value = DummyResponse("NO_NUMBERS")
        with self.assertRaises(NoNumberAvailableError) as ctx:
            await self.svc.get_number(country="cl")
        self.assertIn("CL", str(ctx.exception))

    async def test_get_number_no_balance(self):
        self.svc.client.get.return_value = DummyResponse("NO_BALANCE")
        with self.assertRaises(InsufficientBalanceError) as ctx:
            await self.svc.get_number(country="id")
        self.assertIn("NO_BALANCE", str(ctx.exception))

    async def test_wait_for_code_polls_until_ok(self):
        responses = [
            DummyResponse("ACCESS_READY"),
            DummyResponse("STATUS_WAIT_CODE"),
            DummyResponse("STATUS_OK:482917"),
        ]
        self.svc.client.get.side_effect = responses
        with patch("backend.app.services.grizzlysms.asyncio.sleep", new=AsyncMock()):
            code = await self.svc.wait_for_code("88421", max_attempts=5, interval=0.0)
        self.assertEqual(code, "482917")
        actions = [call.kwargs["params"]["action"] for call in self.svc.client.get.await_args_list]
        self.assertIn("setStatus", actions)
        self.assertEqual(actions.count("getStatus"), 2)
        ready = self.svc.client.get.await_args_list[0].kwargs["params"]
        self.assertEqual(ready["status"], 1)
        self.assertEqual(ready["id"], "88421")

    async def test_wait_for_code_timeout(self):
        self.svc.client.get.return_value = DummyResponse("STATUS_WAIT_CODE")
        with patch("backend.app.services.grizzlysms.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(TimeoutError):
                await self.svc.wait_for_code("1", max_attempts=2, interval=0.0, notify_ready=False)

    async def test_finish_uses_status_6(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_ACTIVATION")
        result = await self.svc.finish("88421")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], 6)
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "setStatus")
        self.assertEqual(params["status"], 6)
        self.assertEqual(params["id"], "88421")

    async def test_cancel_uses_status_8_refund(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_CANCEL")
        result = await self.svc.cancel("88421")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], 8)
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "setStatus")
        self.assertEqual(params["status"], 8)
        self.assertEqual(params["id"], "88421")

    async def test_cancel_skips_missing_act_id(self):
        result = await self.svc.cancel("")
        self.assertTrue(result["skipped"])
        self.assertFalse(result["success"])
        self.svc.client.get.assert_not_awaited()

    async def test_get_stock_count_from_prices(self):
        payload = '{"22":{"tg":{"cost":4.2,"count":17}}}'
        self.svc.client.get.return_value = DummyResponse(payload)
        stock = await self.svc.get_stock_count(country="in", service="tg")
        self.assertEqual(stock, 17)

    async def test_context_manager_closes_owned_client(self):
        svc = GrizzlySmsService("k")
        svc.client.aclose = AsyncMock()
        async with svc:
            self.assertFalse(svc.client.is_closed if hasattr(svc.client, "is_closed") else False)
        svc.client.aclose.assert_awaited()


class TestSmsProviderConfig(unittest.TestCase):
    def test_default_provider_is_fivesim_but_grizzly_key_remains(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.sms_provider, "fivesim")
        self.assertEqual(cfg.grizzly_sms_api_key, "66bd4d8e5f54db073d15c2856c9a1366")
        self.assertTrue(cfg.fivesim_api_key)
        self.assertIsNone(cfg.sms_max_price)

    def test_sms_max_price_normalizes(self):
        self.assertEqual(AppConfigModel(sms_max_price=50).sms_max_price, 50.0)
        self.assertEqual(AppConfigModel(sms_max_price="80.5").sms_max_price, 80.5)
        self.assertEqual(AppConfigModel(sms_max_price="0.53").sms_max_price, 0.53)
        self.assertEqual(AppConfigModel(sms_max_price=0.6).sms_max_price, 0.6)
        self.assertEqual(AppConfigModel(sms_max_price=1.0).sms_max_price, 1.0)
        self.assertIsNone(AppConfigModel(sms_max_price="").sms_max_price)
        self.assertIsNone(AppConfigModel(sms_max_price=0).sms_max_price)
        self.assertIsNone(normalize_sms_max_price("not-a-price"))
        self.assertEqual(normalize_sms_max_price("0.53"), 0.53)
        self.assertEqual(normalize_sms_max_price(0.6), 0.6)
        self.assertEqual(normalize_sms_max_price("1.0"), 1.0)
        self.assertEqual(format_sms_max_price(0.53), "0.53")
        self.assertEqual(format_sms_max_price(0.6), "0.6")
        self.assertEqual(format_sms_max_price(1.0), "1")
        self.assertEqual(format_sms_max_price("0.5294"), "0.5294")
        self.assertEqual(format_sms_max_price(5.0), "5")
        self.assertIsNone(format_sms_max_price(0))
        self.assertIsNone(format_sms_max_price(None))

    def test_aliases_normalize(self):
        self.assertEqual(AppConfigModel(sms_provider="Grizzly-SMS").sms_provider, "grizzlysms")
        self.assertEqual(AppConfigModel(sms_provider="vak_sms").sms_provider, "vaksms")
        self.assertEqual(AppConfigModel(sms_provider="5sim").sms_provider, "fivesim")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("vak"), "vaksms")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider(None), "fivesim")

    def test_task_request_accepts_override(self):
        req = RegisterTaskRequest(country="in", sms_provider="vak")
        self.assertEqual(req.sms_provider, "vaksms")

    def test_task_and_batch_accept_max_price(self):
        req = RegisterTaskRequest(country="iq", max_price=0.6)
        self.assertEqual(req.max_price, 0.6)
        batch = BatchRegisterRequest(country="iq", count=2, concurrency=2, max_price="0.53")
        self.assertEqual(batch.max_price, 0.53)
        self.assertEqual(RegisterTaskRequest(country="iq", max_price="1.0").max_price, 1.0)
        self.assertIs(RegisterBatchRequest, BatchRegisterRequest)
        self.assertIsNone(RegisterTaskRequest(country="iq", max_price="").max_price)


class TestRegistrarGrizzlyPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._prev = RegistrationTaskManager._instance
        self.manager = RegistrationTaskManager()
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
            "lang_code": "hi",
            "system_lang_code": "hi-in",
            "tz_offset": 19800,
            "credential_source": "custom",
            "is_published_api_id": False,
        }

    def _config(self, provider="grizzlysms"):
        return SimpleNamespace(
            target_country="in",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider=provider,
            fivesim_api_key="fivesim-jwt",
            grizzly_sms_api_key="66bd4d8e5f54db073d15c2856c9a1366",
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
            sms_max_price=None,
        )

    async def test_factory_selects_grizzly(self):
        cfg = self._config("grizzlysms")
        with patch("backend.app.services.registrar.GrizzlySmsService") as g_cls, \
             patch("backend.app.services.registrar.VakSmsService") as v_cls:
            g_cls.return_value = FakeSms()
            svc = RegistrationOrchestrator._create_sms_service(cfg)
            g_cls.assert_called_once_with(cfg.grizzly_sms_api_key)
            v_cls.assert_not_called()
            self.assertIsInstance(svc, FakeSms)

    async def test_factory_selects_vak(self):
        cfg = self._config("vaksms")
        with patch("backend.app.services.registrar.GrizzlySmsService") as g_cls, \
             patch("backend.app.services.registrar.VakSmsService") as v_cls:
            v_cls.return_value = FakeSms()
            RegistrationOrchestrator._create_sms_service(cfg, "vaksms")
            v_cls.assert_called_once_with("vak")
            g_cls.assert_not_called()

    async def test_pipeline_logs_grizzly_channel_and_refunds(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-g", "+91911112222"))
        sms.wait_for_code = AsyncMock(side_effect=TimeoutError("no sms"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        clean = SimpleNamespace(intercept=False, is_registered=False, degraded=False, reason="", user_id=None)
        sent = SimpleNamespace(
            type=type("SentCodeTypeSms", (), {})(),
            next_type=None,
            timeout=None,
            phone_code_hash="h",
        )
        fake_client = MagicMock()
        fake_client.is_connected = lambda: False
        fake_client.disconnect = AsyncMock()
        cfg_mgr = SimpleNamespace(config=self._config("grizzlysms"))

        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.GrizzlySmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch(
                 "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                 side_effect=lambda profile, config, has_push_token=False: {**profile, "credential_source": "custom"},
             ), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch(
                 "backend.app.services.registrar.PhonePrecheckService.check_phone",
                 new=AsyncMock(return_value=clean),
             ), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(RegistrationOrchestrator, "_send_code_with_recaptcha", new=AsyncMock(return_value=sent)), \
             patch.object(RegistrationOrchestrator, "resolve_sent_code_channel", new=AsyncMock(return_value=(sent, 3))), \
             patch("backend.app.services.registrar.TelegramClient", return_value=fake_client):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="in",
                sms_provider="grizzlysms",
            )

        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("[接码平台] 当前使用接码通道: Grizzly SMS (grizzlysms.com)", logs)
        self.assertEqual(sms.cancel_calls, ["act-g"])
        self.assertIn("status=8", logs)
        self.assertIn("NO_CODE", logs)

    async def test_task_override_uses_vak_even_if_config_is_grizzly(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(side_effect=NoNumberAvailableError("in", "NO_NUMBERS"))
        sms.PROVIDER_LABEL = "Vak-SMS (vak-sms.com)"
        gw = MagicMock()
        gw.close = AsyncMock()
        cfg_mgr = SimpleNamespace(config=self._config("grizzlysms"))
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms) as vak_cls, \
             patch("backend.app.services.registrar.GrizzlySmsService") as g_cls, \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="in",
                sms_provider="vaksms",
            )
        vak_cls.assert_called_once()
        g_cls.assert_not_called()
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("Vak-SMS", logs)
        task = self.manager.get_task(self.task_id)
        self.assertTrue(task["no_number"])

    async def test_pipeline_passes_task_max_price_over_config(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(side_effect=NoNumberAvailableError("iq", "NO_NUMBERS"))
        gw = MagicMock()
        gw.close = AsyncMock()
        cfg = self._config("grizzlysms")
        cfg.sms_max_price = 0.55
        cfg_mgr = SimpleNamespace(config=cfg)
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.GrizzlySmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="iq",
                sms_provider="grizzlysms",
                max_price=0.6,
            )
        sms.get_number.assert_awaited()
        kwargs = sms.get_number.await_args.kwargs
        self.assertEqual(kwargs["country"], "iq")
        self.assertEqual(kwargs["service"], "tg")
        self.assertEqual(kwargs["max_price"], 0.6)
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("maxPrice=0.6", logs)
        self.assertNotIn("RUB", logs)
        self.assertIn("可继续上调 sms_max_price", logs)

    async def test_pipeline_falls_back_to_config_sms_max_price(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(side_effect=NoNumberAvailableError("iq", "NO_NUMBERS"))
        gw = MagicMock()
        gw.close = AsyncMock()
        cfg = self._config("grizzlysms")
        cfg.sms_max_price = 0.53
        cfg_mgr = SimpleNamespace(config=cfg)
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.GrizzlySmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="iq",
                sms_provider="grizzlysms",
            )
        self.assertEqual(sms.get_number.await_args.kwargs["max_price"], 0.53)
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("maxPrice=0.53", logs)
        self.assertNotIn("RUB", logs)

    async def test_pipeline_hints_when_max_price_missing(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(side_effect=NoNumberAvailableError("iq", "NO_NUMBERS"))
        gw = MagicMock()
        gw.close = AsyncMock()
        cfg_mgr = SimpleNamespace(config=self._config("grizzlysms"))
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.GrizzlySmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="iq",
                sms_provider="grizzlysms",
            )
        self.assertIsNone(sms.get_number.await_args.kwargs["max_price"])
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("未设置最高出价", logs)
        self.assertIn("伊拉克 IQ 美元账户建议 0.55~1.0", logs)

    async def test_refund_helper_prints_grizzly_status_8(self):
        class FakeManager:
            def __init__(self):
                self.logs = []

            async def append_log(self, task_id, message):
                self.logs.append(message)

        sms = FakeSms()
        manager = FakeManager()
        await RegistrationOrchestrator._refund_and_revoke_channel(
            sms, "act-9", "task1", manager, "PHONE_NUMBER_BANNED"
        )
        self.assertEqual(sms.cancel_calls, ["act-9"])
        self.assertIn("[自动退订/撤销信道句柄完成]", manager.logs[0])
        self.assertIn("status=8", manager.logs[0])
        self.assertIn("Grizzly SMS", manager.logs[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
