"""Logistica Integral 阿根廷接码：客户端、配置别名、库存发现、webhook（全部 mock，禁止真租号）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import AppConfigModel, RegisterTaskRequest  # noqa: E402
from backend.app.services.grizzlysms import GrizzlySmsError, InsufficientBalanceError  # noqa: E402
from backend.app.services.integral_sms import (  # noqa: E402
    COUNTRY_ID,
    EARLY_CANCEL_DENIED,
    IntegralSmsService,
    ingest_webhook_payload,
    is_argentina,
    remember_activation,
    reset_webhook_state,
    verify_webhook_signature,
)
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.sms_stock_service import (  # noqa: E402
    SmsStockService,
    normalize_sms_provider,
    reset_stock_cache,
)
from backend.app.services.vaksms import NoNumberAvailableError  # noqa: E402


class DummyResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class TestIntegralCountryGuard(unittest.TestCase):
    def test_argentina_aliases(self):
        self.assertTrue(is_argentina(None))
        self.assertTrue(is_argentina(""))
        self.assertTrue(is_argentina("ar"))
        self.assertTrue(is_argentina("AR"))
        self.assertTrue(is_argentina("argentina"))
        self.assertTrue(is_argentina("阿根廷"))
        self.assertTrue(is_argentina(39))
        self.assertTrue(is_argentina("39"))
        self.assertTrue(is_argentina("+54"))
        self.assertFalse(is_argentina("cl"))
        self.assertFalse(is_argentina(151))
        self.assertFalse(is_argentina("ve"))


class TestIntegralClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_webhook_state()
        self.svc = IntegralSmsService("test-integral-key")
        self.svc.client.get = AsyncMock()

    async def asyncTearDown(self):
        self.svc.client.aclose = AsyncMock()
        await self.svc.close()
        reset_webhook_state()

    async def test_get_balance_sends_header_and_query_key(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_BALANCE:12.5")
        balance = await self.svc.get_balance()
        self.assertAlmostEqual(balance, 12.5)
        args, kwargs = self.svc.client.get.await_args
        self.assertEqual(args[0], IntegralSmsService.BASE_URL)
        self.assertEqual(kwargs["params"]["action"], "getBalance")
        self.assertEqual(kwargs["params"]["api_key"], "test-integral-key")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], "test-integral-key")

    async def test_get_number_argentina_only(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:441:549112223344")
        act_id, phone = await self.svc.get_number(country="ar", service="tg")
        self.assertEqual(act_id, "441")
        self.assertEqual(phone, "+549112223344")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "getNumber")
        self.assertEqual(params["country"], COUNTRY_ID)
        self.assertEqual(params["service"], "tg")

    async def test_get_number_rejects_other_country_without_http(self):
        with self.assertRaises(GrizzlySmsError) as ctx:
            await self.svc.get_number(country="cl")
        self.assertIn("阿根廷", str(ctx.exception))
        self.svc.client.get.assert_not_called()

    async def test_get_number_no_numbers(self):
        self.svc.client.get.return_value = DummyResponse("NO_NUMBERS")
        with self.assertRaises(NoNumberAvailableError):
            await self.svc.get_number(country="ar")

    async def test_get_number_no_balance(self):
        self.svc.client.get.return_value = DummyResponse("NO_BALANCE")
        with self.assertRaises(InsufficientBalanceError):
            await self.svc.get_number(country=39)

    async def test_get_prices_never_calls_getprices(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_BALANCE:3")
        payload = await self.svc.get_prices()
        self.assertEqual(payload["39"]["tg"]["count"], 1)
        actions = [call.kwargs["params"]["action"] for call in self.svc.client.get.await_args_list]
        self.assertEqual(actions, ["getBalance"])
        self.assertNotIn("getNumber", actions)
        self.assertNotIn("getPrices", actions)

    async def test_cancel_does_not_hit_setstatus(self):
        result = await self.svc.cancel("441")
        self.assertFalse(result["success"])
        self.assertEqual(result["data"], EARLY_CANCEL_DENIED)
        self.svc.client.get.assert_not_called()

    async def test_wait_for_code_ok_and_cancel(self):
        self.svc.client.get.side_effect = [
            DummyResponse("STATUS_WAIT_CODE"),
            DummyResponse("STATUS_OK:54321"),
        ]
        with patch("backend.app.services.integral_sms.asyncio.sleep", new=AsyncMock()):
            code = await self.svc.wait_for_code("441", max_attempts=5, interval=0)
        self.assertEqual(code, "54321")

        self.svc.client.get.side_effect = [DummyResponse("STATUS_CANCEL")]
        with patch("backend.app.services.integral_sms.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(GrizzlySmsError) as ctx:
                await self.svc.wait_for_code("442", max_attempts=3, interval=0)
        self.assertIn("STATUS_CANCEL", str(ctx.exception))

    async def test_wait_for_code_uses_webhook_cache(self):
        remember_activation("441")
        ingest_webhook_payload(
            {"activation_id": 441, "phone": "54911", "service": "tg", "code": "98765"},
            raw_body=b"{}",
            signature="",
            secret="",
        )
        with patch("backend.app.services.integral_sms.asyncio.sleep", new=AsyncMock()):
            code = await self.svc.wait_for_code("441", max_attempts=2, interval=0)
        self.assertEqual(code, "98765")
        self.svc.client.get.assert_not_called()


class TestIntegralWebhook(unittest.TestCase):
    def setUp(self):
        reset_webhook_state()

    def tearDown(self):
        reset_webhook_state()

    def test_hmac_signature(self):
        body = b'{"activation_id":123,"code":"54321"}'
        secret = "whsec_test"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(body, sig, secret))
        self.assertTrue(verify_webhook_signature(body, "sha256=" + sig, secret))
        self.assertFalse(verify_webhook_signature(body, "deadbeef", secret))

    def test_reject_unknown_id_without_secret(self):
        with self.assertRaises(GrizzlySmsError):
            ingest_webhook_payload(
                {"activation_id": 99, "code": "11111"},
                raw_body=b"{}",
                signature="",
                secret="",
            )


class TestIntegralConfig(unittest.TestCase):
    def test_api_key_default_is_empty(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.integral_sms_api_key, "")
        self.assertEqual(cfg.integral_webhook_secret, "")

    def test_aliases_normalize_to_integral(self):
        self.assertEqual(AppConfigModel(sms_provider="integral").sms_provider, "integral")
        self.assertEqual(AppConfigModel(sms_provider="Logistica-Integral").sms_provider, "integral")
        self.assertEqual(AppConfigModel(sms_provider="logistica").sms_provider, "integral")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("integral-sms"), "integral")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("logisticaintegralsrl"), "integral")
        self.assertEqual(normalize_sms_provider("logistica-integral"), "integral")
        req = RegisterTaskRequest(country="ar", sms_provider="Logistica Integral")
        self.assertEqual(req.sms_provider, "integral")


class TestIntegralFactory(unittest.IsolatedAsyncioTestCase):
    async def test_create_sms_service_returns_integral(self):
        cfg = SimpleNamespace(
            sms_provider="integral",
            integral_sms_api_key="k-integral",
            smsbower_api_key="",
            vak_sms_api_key="",
            fivesim_api_key="",
            grizzly_sms_api_key="",
            smscode_api_key="",
        )
        svc = RegistrationOrchestrator._create_sms_service(cfg)
        try:
            self.assertIsInstance(svc, IntegralSmsService)
            self.assertEqual(svc.api_key, "k-integral")
            self.assertEqual(
                RegistrationOrchestrator._sms_provider_label(svc),
                "Logistica Integral (Argentina TG)",
            )
        finally:
            await svc.close()


class TestIntegralStockServiceBranch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_stock_cache()
        reset_webhook_state()

    async def asyncTearDown(self):
        reset_stock_cache()
        reset_webhook_state()

    async def test_available_countries_uses_integral_fetch(self):
        items = [{
            "code": "ar",
            "name": "Argentina",
            "name_zh": "阿根廷",
            "dial": "54",
            "flag": "🇦🇷",
            "stock": 1,
            "cost": 0.0,
            "provider": "integral",
            "provider_country_id": "39",
        }]
        fetch = AsyncMock(return_value=items)
        with patch.object(SmsStockService, "_fetch_integral", fetch), \
             patch.object(SmsStockService, "_fetch_grizzly", AsyncMock()) as g_fetch:
            snap = await SmsStockService.get_available_countries(
                provider="integral", refresh=True,
            )
        fetch.assert_awaited()
        g_fetch.assert_not_called()
        self.assertEqual(snap.provider, "integral")
        self.assertEqual(snap.items[0]["code"], "ar")

    async def test_fetch_integral_reads_config_key_without_getnumber(self):
        fake = SimpleNamespace(
            get_prices=AsyncMock(return_value={"39": {"tg": {"cost": 0, "count": 1}}}),
            close=AsyncMock(),
        )
        cfg = SimpleNamespace(integral_sms_api_key="from-config")
        with patch("backend.app.services.integral_sms.IntegralSmsService", return_value=fake) as ctor:
            items = await SmsStockService._fetch_integral(
                service="tg", api_key=None, config=cfg,
            )
        ctor.assert_called_once_with("from-config")
        fake.get_prices.assert_awaited()
        fake.close.assert_awaited()
        self.assertEqual(items[0]["code"], "ar")
        self.assertEqual(items[0]["stock"], 1)


if __name__ == "__main__":
    unittest.main()
