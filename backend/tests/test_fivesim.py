"""5SIM 客户端、国家映射、价格库存、订单状态与注册流水线接入测试。"""
from __future__ import annotations

import json
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

from backend.app.models.schemas import AppConfigModel, RegisterTaskRequest  # noqa: E402
from backend.app.services.fivesim import (  # noqa: E402
    DEFAULT_PRODUCT,
    FiveSimError,
    FiveSimService,
    InsufficientBalanceError,
    extract_sms_code,
    fivesim_country_to_iso,
    parse_fivesim_price_payload,
    resolve_country_iso2,
    resolve_fivesim_country,
    resolve_product,
)
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)
from backend.app.services.sms_stock_service import (  # noqa: E402
    SmsStockService,
    enrich_stock_rows,
    normalize_sms_provider,
    parse_fivesim_price_payload as stock_parse_fivesim,
    reset_stock_cache,
)
from backend.app.services.vaksms import NoNumberAvailableError  # noqa: E402


FIVESIM_JWT = (
    "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE4MTg5MzAxMzYsImlhdCI6MTc4NzM5NDEzNiwicmF5Ijoi"
    "MTBiOGU4OTkwMmQzODdkYmUzY2Y2NzE5Mzc2MGJkOGQiLCJzdWIiOjI5NjU0NDJ9"
)

FIVESIM_PRICES_COUNTRY_OUTER = {
    "indonesia": {
        "telegram": {
            "virtual34": {"cost": 0.3473, "count": 3309},
            "virtual53": {"cost": 0.3, "count": 4765},
            "virtual2": {"cost": 0.2064, "count": 0},
        }
    },
    "usa": {
        "telegram": {
            "virtual28": {"cost": 0.89, "count": 580},
            "virtual51": {"cost": 1.3, "count": 80},
        }
    },
    "england": {
        "telegram": {
            "vodafone": {"cost": 1.3, "count": 0},
        }
    },
}

FIVESIM_PRICES_PRODUCT_OUTER = {
    "telegram": {
        "indonesia": {
            "virtual34": {"cost": 0.3473, "count": 3309},
            "virtual53": {"cost": 0.3, "count": 4765},
        },
        "canada": {
            "virtual34": {"cost": 0.6, "count": 1200},
        },
        "chile": {
            "virtual1": {"cost": 0.4, "count": 0},
        },
    }
}


class DummyResponse:
    def __init__(self, payload, status_code=200):
        if isinstance(payload, (dict, list)):
            self.text = json.dumps(payload)
            self._json = payload
        else:
            self.text = str(payload)
            self._json = None
        self.status_code = status_code

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


class FakeSms:
    def __init__(self):
        self.cancel_calls = []
        self.finish_calls = []
        self.ban_calls = []
        self.PROVIDER_NAME = "fivesim"
        self.PROVIDER_LABEL = "5SIM (5sim.net)"

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": "CANCELED"}

    async def finish(self, act_id):
        self.finish_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": "FINISHED"}

    async def ban(self, act_id):
        self.ban_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": "BANNED"}

    async def close(self):
        return None


class TestFiveSimCountryMapping(unittest.TestCase):
    def test_authoritative_iso2_slugs(self):
        expected = {
            "id": "indonesia",
            "ru": "russia",
            "us": "usa",
            "gb": "england",
            "in": "india",
            "cl": "chile",
            "ca": "canada",
            "kz": "kazakhstan",
            "br": "brazil",
            "co": "colombia",
            "ph": "philippines",
            "pk": "pakistan",
            "ke": "kenya",
            "hk": "hongkong",
            "za": "southafrica",
            "ci": "ivorycoast",
            "cz": "czech",
        }
        for iso, slug in expected.items():
            self.assertEqual(resolve_fivesim_country(iso), slug)
            self.assertEqual(fivesim_country_to_iso(slug), iso if iso != "gb" else "gb")

    def test_uk_alias_and_iso3_and_names(self):
        self.assertEqual(resolve_fivesim_country("uk"), "england")
        self.assertEqual(resolve_fivesim_country("USA"), "usa")
        self.assertEqual(resolve_fivesim_country("indonesia"), "indonesia")
        self.assertEqual(resolve_fivesim_country("IDN"), "indonesia")
        self.assertEqual(resolve_fivesim_country("IND"), "india")
        self.assertEqual(resolve_fivesim_country("印度"), "india")
        self.assertEqual(resolve_fivesim_country("智利"), "chile")
        self.assertEqual(resolve_country_iso2("england"), "gb")
        self.assertEqual(resolve_country_iso2("usa"), "us")
        self.assertEqual(resolve_country_iso2("bih"), "ba")
        self.assertEqual(resolve_product("tg"), "telegram")
        self.assertEqual(resolve_product("telegram"), "telegram")

    def test_unknown_country_raises(self):
        with self.assertRaises(FiveSimError):
            resolve_fivesim_country("")
        with self.assertRaises(FiveSimError):
            resolve_fivesim_country(None)


class TestFiveSimPriceParse(unittest.TestCase):
    def test_country_outer_filters_zero_and_sums_operators(self):
        rows = parse_fivesim_price_payload(FIVESIM_PRICES_COUNTRY_OUTER, product="telegram")
        by_slug = {item["provider_country_id"]: item for item in rows}
        self.assertEqual(by_slug["indonesia"]["stock"], 3309 + 4765)
        self.assertAlmostEqual(by_slug["indonesia"]["cost"], 0.3)
        self.assertEqual(by_slug["usa"]["stock"], 660)
        self.assertNotIn("england", by_slug)

        items = enrich_stock_rows(rows, "fivesim")
        self.assertEqual(items[0]["code"], "id")
        self.assertEqual(items[0]["stock"], 8074)
        codes = [item["code"] for item in items]
        self.assertIn("us", codes)
        self.assertNotIn("gb", codes)

    def test_product_outer_shape(self):
        rows = parse_fivesim_price_payload(FIVESIM_PRICES_PRODUCT_OUTER, product="telegram")
        by_slug = {item["provider_country_id"]: item for item in rows}
        self.assertEqual(set(by_slug), {"indonesia", "canada"})
        self.assertEqual(by_slug["canada"]["stock"], 1200)
        self.assertEqual(stock_parse_fivesim(FIVESIM_PRICES_PRODUCT_OUTER)[0]["stock"], 8074)

    def test_single_country_filter(self):
        rows = parse_fivesim_price_payload(
            FIVESIM_PRICES_COUNTRY_OUTER, product="telegram", country="indonesia"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider_country_id"], "indonesia")


class TestFiveSimClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.svc = FiveSimService("test-jwt")
        self.svc.client.get = AsyncMock()

    async def asyncTearDown(self):
        self.svc.client.aclose = AsyncMock()
        await self.svc.close()

    async def test_get_balance_parses_profile(self):
        self.svc.client.get.return_value = DummyResponse({
            "balance": 25.1154,
            "email": "s7word@gmail.com",
            "rating": 96,
        })
        balance = await self.svc.get_balance()
        self.assertAlmostEqual(balance, 25.1154)
        args, kwargs = self.svc.client.get.await_args
        self.assertTrue(str(args[0]).endswith("/user/profile"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-jwt")
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")

    async def test_get_number_success(self):
        self.svc.client.get.return_value = DummyResponse({
            "id": 1234567,
            "phone": "62812345678",
            "operator": "virtual53",
            "product": "telegram",
            "price": 0.3,
            "status": "PENDING",
            "sms": [],
        })
        act_id, phone = await self.svc.get_number(country="id", service="tg")
        self.assertEqual(act_id, "1234567")
        self.assertEqual(phone, "+62812345678")
        buy_call = self.svc.client.get.await_args
        self.assertIn("/user/buy/activation/indonesia/any/telegram", buy_call.args[0])
        self.assertIsNone(buy_call.kwargs.get("params"))

    async def test_get_number_with_max_price_filters_operator(self):
        self.svc.client.get.side_effect = [
            DummyResponse(FIVESIM_PRICES_COUNTRY_OUTER),
            DummyResponse({
                "id": 99,
                "phone": "+62811111111",
                "operator": "virtual53",
                "product": "telegram",
                "price": 0.3,
                "status": "PENDING",
                "sms": [],
            }),
        ]
        act_id, phone = await self.svc.get_number(country="indonesia", service="tg", max_price=0.35)
        self.assertEqual(act_id, "99")
        self.assertEqual(phone, "+62811111111")
        buy_url = self.svc.client.get.await_args_list[-1].args[0]
        self.assertIn("/indonesia/virtual53/telegram", buy_url)
        params = self.svc.client.get.await_args_list[-1].kwargs["params"]
        self.assertEqual(params["maxPrice"], "0.35")
        self.assertEqual(params["max_price"], "0.35")

    async def test_get_number_no_free_phones(self):
        self.svc.get_prices = AsyncMock(return_value=FIVESIM_PRICES_COUNTRY_OUTER)
        self.svc.client.get.return_value = DummyResponse("no free phones", status_code=400)
        with self.assertRaises(NoNumberAvailableError) as ctx:
            await self.svc.get_number(country="cl")
        self.assertIn("CL", str(ctx.exception))

    async def test_get_number_not_enough_balance(self):
        self.svc.get_prices = AsyncMock(return_value={"indonesia": {"telegram": {"any": {"cost": 0.3, "count": 10}}}})
        self.svc.client.get.return_value = DummyResponse("not enough user balance", status_code=400)
        with self.assertRaises(InsufficientBalanceError) as ctx:
            await self.svc.get_number(country="id")
        self.assertIn("not enough user balance", str(ctx.exception))

    async def test_wait_for_code_extracts_latest_sms(self):
        self.svc.client.get.side_effect = [
            DummyResponse({"id": 1, "status": "PENDING", "sms": []}),
            DummyResponse({
                "id": 1,
                "status": "RECEIVED",
                "sms": [
                    {"code": "11111", "text": "old"},
                    {"code": "48291", "text": "Your Telegram code is 48291"},
                ],
            }),
        ]
        with patch("backend.app.services.fivesim.asyncio.sleep", new=AsyncMock()):
            code = await self.svc.wait_for_code("1", max_attempts=5, interval=0.0)
        self.assertEqual(code, "48291")
        self.assertEqual(extract_sms_code({"sms": [{"text": "Use 415127 as your login code"}]}), "415127")

    async def test_wait_for_code_timeout_and_canceled(self):
        self.svc.client.get.return_value = DummyResponse({"id": 1, "status": "PENDING", "sms": []})
        with patch("backend.app.services.fivesim.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(TimeoutError):
                await self.svc.wait_for_code("1", max_attempts=2, interval=0.0)

        self.svc.client.get.return_value = DummyResponse({"id": 1, "status": "CANCELED", "sms": []})
        with patch("backend.app.services.fivesim.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(FiveSimError) as ctx:
                await self.svc.wait_for_code("2", max_attempts=2, interval=0.0)
        self.assertIn("CANCELED", str(ctx.exception))

    async def test_finish_cancel_ban_status_handling(self):
        self.svc.client.get.return_value = DummyResponse({
            "id": 123, "status": "FINISHED", "phone": "+1",
        })
        finished = await self.svc.finish("123")
        self.assertTrue(finished["success"])
        self.assertEqual(finished["status"], "FINISHED")
        self.assertTrue(self.svc.client.get.await_args.args[0].endswith("/user/finish/123"))

        self.svc.client.get.return_value = DummyResponse({
            "id": 123, "status": "CANCELED",
        })
        canceled = await self.svc.cancel("123")
        self.assertTrue(canceled["success"])
        self.assertEqual(canceled["status"], "CANCELED")

        self.svc.client.get.side_effect = [
            DummyResponse("order cannot be cancelled", status_code=400),
            DummyResponse({"id": 123, "status": "BANNED"}),
        ]
        banned = await self.svc.cancel("123")
        self.assertTrue(banned["success"])
        self.assertEqual(banned["status"], "BANNED")
        self.assertEqual(banned.get("fallback"), "ban")

        skipped = await self.svc.cancel("")
        self.assertTrue(skipped["skipped"])
        self.assertFalse(skipped["success"])

    async def test_context_manager_closes_owned_client(self):
        svc = FiveSimService("k")
        svc.client.aclose = AsyncMock()
        async with svc:
            self.assertFalse(svc.client.is_closed if hasattr(svc.client, "is_closed") else False)
        svc.client.aclose.assert_awaited()


class TestFiveSimConfig(unittest.TestCase):
    def test_default_provider_is_fivesim(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.sms_provider, "fivesim")
        self.assertTrue(cfg.fivesim_api_key.startswith("eyJ"))
        self.assertIn("5XJMXfZ0spP2", cfg.fivesim_api_key)

    def test_aliases_and_task_override(self):
        self.assertEqual(AppConfigModel(sms_provider="5SIM").sms_provider, "fivesim")
        self.assertEqual(AppConfigModel(sms_provider="Five-Sim").sms_provider, "fivesim")
        self.assertEqual(normalize_sms_provider("5sim.net"), "fivesim")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider("5sim"), "fivesim")
        self.assertEqual(RegistrationOrchestrator.normalize_sms_provider(None), "fivesim")
        req = RegisterTaskRequest(country="id", sms_provider="5sim")
        self.assertEqual(req.sms_provider, "fivesim")


class TestRegistrarFiveSimPipeline(unittest.IsolatedAsyncioTestCase):
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
            "lang_code": "id",
            "system_lang_code": "id-id",
            "tz_offset": 25200,
            "credential_source": "custom",
            "is_published_api_id": False,
        }

    def _config(self, provider="fivesim"):
        return SimpleNamespace(
            target_country="id",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider=provider,
            fivesim_api_key=FIVESIM_JWT,
            grizzly_sms_api_key="grizzly",
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

    async def test_factory_selects_fivesim(self):
        cfg = self._config("fivesim")
        with patch("backend.app.services.registrar.FiveSimService") as f_cls, \
             patch("backend.app.services.registrar.GrizzlySmsService") as g_cls, \
             patch("backend.app.services.registrar.VakSmsService") as v_cls:
            f_cls.return_value = FakeSms()
            svc = RegistrationOrchestrator._create_sms_service(cfg)
            f_cls.assert_called_once_with(cfg.fivesim_api_key)
            g_cls.assert_not_called()
            v_cls.assert_not_called()
            self.assertIsInstance(svc, FakeSms)

    async def test_pipeline_logs_fivesim_channel_and_refunds(self):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-5", "+62811112222"))
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
        cfg_mgr = SimpleNamespace(config=self._config("fivesim"))

        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.FiveSimService", return_value=sms), \
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
                country="id",
                sms_provider="fivesim",
            )

        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("[接码平台] 当前使用接码通道: 5SIM (5sim.net)", logs)
        self.assertEqual(sms.cancel_calls, ["act-5"])
        self.assertIn("CANCELED", logs)
        self.assertIn("NO_CODE", logs)

    async def test_refund_helper_prints_fivesim_cancel(self):
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
        self.assertIn("CANCELED", manager.logs[0])
        self.assertIn("5SIM", manager.logs[0])


class TestFiveSimStockService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_stock_cache()

    async def asyncTearDown(self):
        reset_stock_cache()

    async def test_fetch_fivesim_sorted_by_stock(self):
        items = enrich_stock_rows(
            parse_fivesim_price_payload(FIVESIM_PRICES_PRODUCT_OUTER),
            "fivesim",
        )
        fetch = AsyncMock(return_value=items)
        with patch.object(SmsStockService, "_fetch_fivesim", fetch):
            snap = await SmsStockService.get_available_countries(provider="fivesim", refresh=True)
        self.assertEqual(snap.provider, "fivesim")
        self.assertEqual(snap.items[0]["code"], "id")
        self.assertGreater(snap.items[0]["stock"], snap.items[1]["stock"])
        self.assertEqual(snap.total_countries, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
