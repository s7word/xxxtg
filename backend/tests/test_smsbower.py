"""SMS Bower 客户端、国家映射、配置别名与库存发现测试（全部 mock httpx，禁止真租号）。"""
from __future__ import annotations

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
from backend.app.services.grizzlysms import (  # noqa: E402
    GrizzlySmsError,
    resolve_grizzly_country_id,
)
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.sms_stock_service import (  # noqa: E402
    SmsStockService,
    enrich_stock_rows,
    normalize_sms_provider,
    parse_grizzly_price_payload,
    reset_stock_cache,
)
from backend.app.services.smsbower import SmsBowerService  # noqa: E402


class DummyResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json
        return json.loads(self.text)


SMSBOWER_PRICES_FIXTURE = {
    "31": {"tg": {"cost": 0.594, "count": 115458}},
    "22": {"tg": {"cost": 0.42, "count": 8800}},
    "66": {"tg": {"cost": 0.31, "count": 0}},
}


class TestSmsBowerCountryMapping(unittest.TestCase):
    def test_za_in_country_ids(self):
        self.assertEqual(resolve_grizzly_country_id("za"), 31)
        self.assertEqual(resolve_grizzly_country_id("ZA"), 31)
        self.assertEqual(resolve_grizzly_country_id("in"), 22)
        self.assertEqual(resolve_grizzly_country_id("IN"), 22)
        self.assertEqual(resolve_grizzly_country_id(31), 31)
        self.assertEqual(resolve_grizzly_country_id(22), 22)


class TestSmsBowerClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.svc = SmsBowerService("test-bower-key")
        self.svc.client.get = AsyncMock()

    async def asyncTearDown(self):
        self.svc.client.aclose = AsyncMock()
        await self.svc.close()

    async def test_get_balance_parses_access_balance(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_BALANCE:24.8")
        balance = await self.svc.get_balance()
        self.assertAlmostEqual(balance, 24.8)
        args, kwargs = self.svc.client.get.await_args
        self.assertEqual(args[0], SmsBowerService.BASE_URL)
        self.assertEqual(args[0], "https://smsbower.page/stubs/handler_api.php")
        self.assertEqual(kwargs["params"]["action"], "getBalance")
        self.assertEqual(kwargs["params"]["api_key"], "test-bower-key")

    async def test_missing_key_uses_provider_label(self):
        svc = SmsBowerService("")
        with self.assertRaises(GrizzlySmsError) as ctx:
            await svc.get_balance()
        self.assertIn("SMS Bower", str(ctx.exception))
        self.assertNotIn("Grizzly SMS API Key", str(ctx.exception))

    async def test_get_number_success(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:88421:27821234567")
        act_id, phone = await self.svc.get_number(country="za", service="tg")
        self.assertEqual(act_id, "88421")
        self.assertEqual(phone, "+27821234567")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "getNumber")
        self.assertEqual(params["country"], 31)
        self.assertEqual(params["service"], "tg")

    async def test_get_number_india(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_NUMBER:91001:919876543210")
        act_id, phone = await self.svc.get_number(country="in", service="tg")
        self.assertEqual(act_id, "91001")
        self.assertEqual(phone, "+919876543210")
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["country"], 22)

    async def test_cancel_uses_status_8(self):
        self.svc.client.get.return_value = DummyResponse("ACCESS_CANCEL")
        result = await self.svc.cancel("88421")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], 8)
        params = self.svc.client.get.await_args.kwargs["params"]
        self.assertEqual(params["action"], "setStatus")
        self.assertEqual(params["status"], 8)
        self.assertEqual(params["id"], "88421")

    async def test_cancel_early_cancel_denied(self):
        self.svc.client.get.return_value = DummyResponse("EARLY_CANCEL_DENIED")
        result = await self.svc.cancel("88421")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], 8)

    async def test_inherits_grizzly_but_uses_own_endpoint(self):
        from backend.app.services.grizzlysms import GrizzlySmsService

        self.assertTrue(issubclass(SmsBowerService, GrizzlySmsService))
        self.assertNotEqual(SmsBowerService.BASE_URL, GrizzlySmsService.BASE_URL)
        self.assertEqual(self.svc.PROVIDER_NAME, "smsbower")
        self.assertEqual(self.svc.PROVIDER_LABEL, "SMS Bower (smsbower.app)")


class TestSmsBowerConfig(unittest.TestCase):
    def test_api_key_default_is_empty(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.smsbower_api_key, "")

    def test_aliases_normalize_to_smsbower(self):
        self.assertEqual(AppConfigModel(sms_provider="SMS-Bower").sms_provider, "smsbower")
        self.assertEqual(AppConfigModel(sms_provider="smsbower").sms_provider, "smsbower")
        self.assertEqual(AppConfigModel(sms_provider="sms-bower").sms_provider, "smsbower")
        self.assertEqual(AppConfigModel(sms_provider="bower").sms_provider, "smsbower")
        self.assertEqual(AppConfigModel(sms_provider="smsbowerapp").sms_provider, "smsbower")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("SMS-Bower"), "smsbower")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("bower"), "smsbower")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("smsbowerapp"), "smsbower")
        self.assertEqual(normalize_sms_provider("sms-bower"), "smsbower")
        self.assertEqual(normalize_sms_provider("bower"), "smsbower")
        req = RegisterTaskRequest(country="za", sms_provider="SMS-Bower")
        self.assertEqual(req.sms_provider, "smsbower")
        req2 = RegisterTaskRequest(country="iq", provider_ids="3330,2579")
        self.assertEqual(req2.provider_ids, ["3330", "2579"])


class TestSmsBowerFactory(unittest.IsolatedAsyncioTestCase):
    async def test_create_sms_service_returns_smsbower(self):
        cfg = SimpleNamespace(
            sms_provider="smsbower",
            smsbower_api_key="test-bower-key",
            vak_sms_api_key="vak",
            fivesim_api_key="fivesim",
            grizzly_sms_api_key="grizzly",
        )
        svc = RegistrationOrchestrator._create_sms_service(cfg)
        try:
            self.assertIsInstance(svc, SmsBowerService)
            self.assertEqual(svc.api_key, "test-bower-key")
            self.assertEqual(svc.BASE_URL, "https://smsbower.page/stubs/handler_api.php")
            self.assertEqual(
                RegistrationOrchestrator._sms_provider_label(svc),
                "SMS Bower (smsbower.app)",
            )
        finally:
            await svc.close()

    async def test_create_sms_service_alias_sms_bower(self):
        cfg = SimpleNamespace(
            sms_provider="fivesim",
            smsbower_api_key="k",
            vak_sms_api_key="",
            fivesim_api_key="",
            grizzly_sms_api_key="",
        )
        svc = RegistrationOrchestrator._create_sms_service(cfg, "SMS-Bower")
        try:
            self.assertIsInstance(svc, SmsBowerService)
            self.assertEqual(svc.api_key, "k")
        finally:
            await svc.close()


class TestSmsBowerStock(unittest.TestCase):
    def test_parse_prices_za_in(self):
        rows = parse_grizzly_price_payload(SMSBOWER_PRICES_FIXTURE, service="tg")
        stocks = {item["provider_country_id"]: item for item in rows}
        self.assertIn("31", stocks)
        self.assertIn("22", stocks)
        self.assertNotIn("66", stocks)
        self.assertEqual(stocks["31"]["stock"], 115458)
        self.assertAlmostEqual(stocks["31"]["cost"], 0.594)
        items = enrich_stock_rows(rows, "smsbower")
        codes = {item["code"]: item for item in items}
        self.assertEqual(codes["za"]["stock"], 115458)
        self.assertEqual(codes["in"]["stock"], 8800)
        self.assertEqual(items[0]["code"], "za")
        self.assertEqual(items[0]["provider"], "smsbower")


class TestSmsBowerStockServiceBranch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_stock_cache()

    async def asyncTearDown(self):
        reset_stock_cache()

    async def test_available_countries_uses_smsbower_fetch(self):
        items = enrich_stock_rows(
            parse_grizzly_price_payload(SMSBOWER_PRICES_FIXTURE),
            "smsbower",
        )
        fetch = AsyncMock(return_value=items)
        with patch.object(SmsStockService, "_fetch_smsbower", fetch), \
             patch.object(SmsStockService, "_fetch_grizzly", AsyncMock()) as g_fetch, \
             patch.object(SmsStockService, "_fetch_fivesim", AsyncMock()) as f_fetch:
            snap = await SmsStockService.get_available_countries(
                provider="smsbower", refresh=True,
            )
        fetch.assert_awaited()
        g_fetch.assert_not_called()
        f_fetch.assert_not_called()
        self.assertEqual(snap.provider, "smsbower")
        self.assertEqual(snap.items[0]["code"], "za")
        self.assertEqual(snap.total_stock, 115458 + 8800)

    async def test_fetch_smsbower_reads_config_key_and_parses(self):
        payload = {"31": {"tg": {"cost": 0.594, "count": 115458}}}
        fake = SimpleNamespace(
            get_prices=AsyncMock(return_value=payload),
            close=AsyncMock(),
        )
        cfg = SimpleNamespace(smsbower_api_key="from-config")
        with patch("backend.app.services.smsbower.SmsBowerService", return_value=fake) as ctor:
            items = await SmsStockService._fetch_smsbower(
                service="tg", api_key=None, config=cfg,
            )
        ctor.assert_called_once_with("from-config")
        fake.get_prices.assert_awaited()
        fake.close.assert_awaited()
        self.assertEqual(items[0]["code"], "za")
        self.assertEqual(items[0]["stock"], 115458)
        self.assertEqual(items[0]["provider"], "smsbower")


if __name__ == "__main__":
    unittest.main(verbosity=2)
