"""SMSCode.gg 客户端、配置别名、库存解析与工厂测试（全部 mock HTTP，禁止真租号）。"""
from __future__ import annotations

import json
import logging
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
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.sms_stock_service import (  # noqa: E402
    SmsStockService,
    enrich_stock_rows,
    normalize_sms_provider,
    parse_smscode_price_payload,
    reset_stock_cache,
)
from backend.app.services.smscode import (  # noqa: E402
    BASE_URL,
    PROVIDER_LABEL,
    InsufficientBalanceError,
    SmsCodeError,
    SmsCodeService,
    mask_api_key,
    parse_money_usd,
    parse_smscode_products_payload,
)
from backend.app.services.vaksms import NoNumberAvailableError  # noqa: E402

TEST_KEY = "test-smscode-key-abcdefghijklmnop"


class DummyResponse:
    def __init__(self, payload, status_code=200, headers=None):
        if isinstance(payload, (dict, list)):
            self.text = json.dumps(payload)
            self._json = payload
        else:
            self.text = str(payload)
            self._json = None
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


COUNTRIES = [
    {"id": 6, "code": "ID", "name": "Indonesia", "dial_code": "+62", "active": True},
    {"id": 7, "code": "IN", "name": "India", "dial_code": "+91", "active": True},
]
SERVICES = [
    {"id": 3, "code": "wa", "name": "WhatsApp", "active": True},
    {"id": 11, "code": "tg", "name": "Telegram", "active": True},
]
PRODUCTS = [
    {
        "id": 142,
        "name": "Telegram Indonesia",
        "country_id": 6,
        "platform_id": 11,
        "catalog_product_id": 87,
        "available": 42,
        "price": {
            "amount": "0.9231",
            "currency": "USD",
            "canonical_amount": 15000,
            "canonical_currency": "IDR",
        },
        "active": True,
    },
    {
        "id": 200,
        "name": "Telegram India",
        "country_id": 7,
        "platform_id": 11,
        "catalog_product_id": 90,
        "available": 12,
        "price": {
            "amount": "0.40",
            "currency": "USD",
            "canonical_amount": 6500,
            "canonical_currency": "IDR",
        },
        "active": True,
    },
    {
        "id": 201,
        "name": "Telegram India empty",
        "country_id": 7,
        "platform_id": 11,
        "catalog_product_id": 91,
        "available": 0,
        "price": {"amount": "0.10", "currency": "USD"},
        "active": True,
    },
]
CREATE_OK = {
    "success": True,
    "data": {
        "orders": [
            {
                "id": 1002,
                "status": "ACTIVE",
                "catalog_product_id": 87,
                "amount": {"amount": "0.9231", "currency": "USD"},
                "phone_number": "+6281234567891",
                "otp_code": None,
            }
        ],
        "failed_count": 0,
    },
}


def ok(data, meta=None):
    payload = {"success": True, "data": data}
    if meta is not None:
        payload["meta"] = meta
    return payload


class TestMaskAndMoney(unittest.TestCase):
    def test_mask_api_key_hides_middle(self):
        masked = mask_api_key(TEST_KEY)
        self.assertTrue(masked.startswith("test-s"))
        self.assertTrue(masked.endswith("mnop"))
        self.assertIn("...", masked)
        self.assertNotEqual(masked, TEST_KEY)
        self.assertNotIn("key-abcdefghijkl", masked)

    def test_mask_empty_and_short(self):
        self.assertEqual(mask_api_key(""), "<empty>")
        self.assertEqual(mask_api_key("abcd"), "ab***cd")

    def test_parse_money_usd_object_and_int(self):
        self.assertAlmostEqual(
            parse_money_usd({"amount": "30.77", "currency": "USD"}),
            30.77,
        )
        self.assertAlmostEqual(parse_money_usd(500000), 500000.0)
        self.assertIsNone(parse_money_usd(None))


class TestSmsCodeClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.svc = SmsCodeService(TEST_KEY)
        self.svc.client.request = AsyncMock()

    async def asyncTearDown(self):
        self.svc.client.aclose = AsyncMock()
        await self.svc.close()

    def _route(self, mapping):
        async def handler(method, url, **kwargs):
            path = url.split("?")[0]
            key = (method.upper(), path)
            if key not in mapping:
                for (m, p), resp in mapping.items():
                    if m == method.upper() and (path == p or path.rstrip("/") == p.rstrip("/")):
                        key = (m, p)
                        break
                else:
                    raise AssertionError(f"unexpected request {method} {url} kwargs={kwargs}")
            value = mapping[key]
            if callable(value):
                return value(kwargs)
            return value

        self.svc.client.request.side_effect = handler

    async def test_missing_key_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMSCODE_API_KEY", None)
            os.environ.pop("SMSCODE_TOKEN", None)
            svc = SmsCodeService("")
            with self.assertRaises(SmsCodeError) as ctx:
                await svc.get_balance()
            self.assertIn("SMSCode", str(ctx.exception))
            self.assertNotIn(TEST_KEY, str(ctx.exception))

    async def test_get_balance_parses_v2_money(self):
        self.svc.client.request.return_value = DummyResponse(
            ok({"balance": {"amount": "30.77", "currency": "USD", "canonical_amount": 500000}})
        )
        balance = await self.svc.get_balance()
        self.assertAlmostEqual(balance, 30.77)
        args, kwargs = self.svc.client.request.await_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(str(args[1]).endswith("/balance"))
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TEST_KEY}")

    async def test_unauthorized_maps_to_bad_key(self):
        self.svc.client.request.return_value = DummyResponse(
            {"success": False, "error": {"code": "UNAUTHORIZED", "message": "invalid token"}},
            status_code=401,
        )
        with self.assertRaises(SmsCodeError) as ctx:
            await self.svc.get_balance()
        self.assertIn("无效", str(ctx.exception))
        self.assertIn(mask_api_key(TEST_KEY), str(ctx.exception))
        self.assertNotIn(TEST_KEY, str(ctx.exception))

    async def test_get_number_success(self):
        self._route({
            ("GET", f"{BASE_URL}/catalog/countries"): DummyResponse(ok(COUNTRIES)),
            ("GET", f"{BASE_URL}/catalog/services"): DummyResponse(ok(SERVICES)),
            ("GET", f"{BASE_URL}/catalog/products"): DummyResponse(
                ok([PRODUCTS[0]], meta={"page": 1, "limit": 10000, "count": 1})
            ),
            ("POST", f"{BASE_URL}/orders/create"): DummyResponse(CREATE_OK),
        })
        act_id, phone = await self.svc.get_number(country="id", service="tg", max_price=1.0)
        self.assertEqual(act_id, "1002")
        self.assertEqual(phone, "+6281234567891")
        create_call = [
            call for call in self.svc.client.request.await_args_list
            if call.args[0] == "POST"
        ][0]
        body = create_call.kwargs["json"]
        self.assertEqual(body["catalog_product_id"], 87)
        self.assertEqual(body["max_price"], "1")
        self.assertEqual(body["quantity"], 1)
        self.assertTrue(create_call.kwargs["headers"].get("Idempotency-Key"))

    async def test_get_number_no_offer(self):
        self._route({
            ("GET", f"{BASE_URL}/catalog/countries"): DummyResponse(ok(COUNTRIES)),
            ("GET", f"{BASE_URL}/catalog/services"): DummyResponse(ok(SERVICES)),
            ("GET", f"{BASE_URL}/catalog/products"): DummyResponse(ok([])),
        })
        with self.assertRaises(NoNumberAvailableError):
            await self.svc.get_number(country="id")

    async def test_get_number_insufficient_balance(self):
        self._route({
            ("GET", f"{BASE_URL}/catalog/countries"): DummyResponse(ok(COUNTRIES)),
            ("GET", f"{BASE_URL}/catalog/services"): DummyResponse(ok(SERVICES)),
            ("GET", f"{BASE_URL}/catalog/products"): DummyResponse(
                ok([PRODUCTS[0]], meta={"count": 1})
            ),
            ("POST", f"{BASE_URL}/orders/create"): DummyResponse(
                {"success": False, "error": {"code": "INSUFFICIENT_BALANCE", "message": "need topup"}},
                status_code=409,
            ),
        })
        with self.assertRaises(InsufficientBalanceError):
            await self.svc.get_number(country="id")

    async def test_wait_for_code_then_finish_and_cancel(self):
        order_wait = ok({"id": 1002, "status": "ACTIVE", "otp_code": None})
        order_ready = ok({"id": 1002, "status": "OTP_RECEIVED", "otp_code": "123456"})
        states = {"n": 0}

        def get_order(kwargs):
            states["n"] += 1
            return DummyResponse(order_wait if states["n"] < 2 else order_ready)

        self._route({
            ("GET", f"{BASE_URL}/orders/1002"): get_order,
            ("POST", f"{BASE_URL}/orders/finish"): DummyResponse(
                ok({"order_id": 1002, "status": "COMPLETED"})
            ),
            ("POST", f"{BASE_URL}/orders/cancel"): DummyResponse(
                ok({"order_id": 1002, "status": "CANCELED", "refund_amount": {"amount": "0.92"}})
            ),
        })
        with patch("backend.app.services.smscode.asyncio.sleep", new=AsyncMock()):
            code = await self.svc.wait_for_code("1002", max_attempts=5, interval=0.01)
        self.assertEqual(code, "123456")
        finished = await self.svc.finish("1002")
        self.assertTrue(finished["success"])
        canceled = await self.svc.cancel("1002")
        self.assertTrue(canceled["success"])
        self.assertEqual(canceled["status"], "CANCELED")

    async def test_cancel_too_early(self):
        self.svc.client.request.return_value = DummyResponse(
            {"success": False, "error": {"code": "CANCEL_TOO_EARLY", "message": "wait 2 minutes"}},
            status_code=409,
        )
        result = await self.svc.cancel("1002")
        self.assertFalse(result["success"])
        self.assertTrue(result.get("early_cancel"))

    async def test_get_code_single_fetch(self):
        self.svc.client.request.return_value = DummyResponse(
            ok({"id": 1002, "status": "OTP_RECEIVED", "otp_code": "654321"})
        )
        self.assertEqual(await self.svc.get_code("1002"), "654321")

    async def test_logs_do_not_contain_full_key(self):
        self.svc.client.request.return_value = DummyResponse(
            ok({"balance": {"amount": "1.00", "currency": "USD"}})
        )
        with self.assertLogs("SmsCodeService", level="INFO") as captured:
            await self.svc.get_balance()
        blob = "\n".join(captured.output)
        self.assertNotIn(TEST_KEY, blob)
        self.assertIn(mask_api_key(TEST_KEY), blob)

    async def test_timeout_maps_to_error(self):
        import httpx

        self.svc.client.request.side_effect = httpx.TimeoutException("timed out")
        with self.assertRaises(SmsCodeError) as ctx:
            await self.svc.get_balance()
        self.assertIn("超时", str(ctx.exception))


class TestSmsCodeConfig(unittest.TestCase):
    def test_api_key_default_is_empty(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.smscode_api_key, "")

    def test_aliases_normalize_to_smscode(self):
        self.assertEqual(AppConfigModel(sms_provider="SMSCode").sms_provider, "smscode")
        self.assertEqual(AppConfigModel(sms_provider="smscode.gg").sms_provider, "smscode")
        self.assertEqual(AppConfigModel(sms_provider="sms-code").sms_provider, "smscode")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("SMSCode.gg"), "smscode")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("smscodegg"), "smscode")
        self.assertEqual(normalize_sms_provider("sms-code-gg"), "smscode")
        req = RegisterTaskRequest(country="id", sms_provider="SMSCode")
        self.assertEqual(req.sms_provider, "smscode")


class TestSmsCodeFactory(unittest.IsolatedAsyncioTestCase):
    async def test_create_sms_service_returns_smscode(self):
        cfg = SimpleNamespace(
            sms_provider="smscode",
            smscode_api_key="live-key",
            smsbower_api_key="",
            vak_sms_api_key="",
            fivesim_api_key="",
            grizzly_sms_api_key="",
        )
        svc = RegistrationOrchestrator._create_sms_service(cfg)
        try:
            self.assertIsInstance(svc, SmsCodeService)
            self.assertEqual(svc.api_key, "live-key")
            self.assertEqual(svc.BASE_URL, BASE_URL)
            self.assertEqual(
                RegistrationOrchestrator._sms_provider_label(svc),
                PROVIDER_LABEL,
            )
        finally:
            await svc.close()


class TestSmsCodeStock(unittest.TestCase):
    def test_parse_products_skips_empty(self):
        rows = parse_smscode_products_payload(PRODUCTS, COUNTRIES)
        stocks = {item["provider_country_id"]: item for item in rows}
        self.assertEqual(stocks["id"]["stock"], 42)
        self.assertAlmostEqual(stocks["id"]["cost"], 0.9231)
        self.assertEqual(stocks["in"]["stock"], 12)
        self.assertNotIn("91", str(stocks["in"].get("catalog_product_id")))
        items = enrich_stock_rows(rows, "smscode")
        codes = {item["code"]: item for item in items}
        self.assertEqual(codes["id"]["stock"], 42)
        self.assertEqual(codes["in"]["stock"], 12)

    def test_stock_wrapper_accepts_get_prices_shape(self):
        rows = parse_smscode_price_payload(
            {"products": PRODUCTS, "countries": COUNTRIES},
            service="tg",
        )
        self.assertEqual(len(rows), 2)


class TestSmsCodeStockFetch(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_available_countries(self):
        reset_stock_cache()
        svc = SmsCodeService(TEST_KEY)
        svc.client.aclose = AsyncMock()
        svc.client.request = AsyncMock(side_effect=[
            DummyResponse(ok(SERVICES)),
            DummyResponse(ok(PRODUCTS, meta={"count": 3})),
            DummyResponse(ok(COUNTRIES)),
        ])
        with patch("backend.app.services.smscode.SmsCodeService", return_value=svc):
            snap = await SmsStockService.get_available_countries(
                provider="smscode",
                refresh=True,
                api_key=TEST_KEY,
            )
        try:
            codes = {item["code"] for item in snap.items}
            self.assertIn("id", codes)
            self.assertIn("in", codes)
            self.assertEqual(snap.provider, "smscode")
        finally:
            reset_stock_cache()


if __name__ == "__main__":
    unittest.main()
