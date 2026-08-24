"""动态接码库存发现、未知国家拓扑推断与 /api/sms/available-countries。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.device_generator import (  # noqa: E402
    locale_matches_country,
    resolve_synth_spec,
    sku_sdk_consistent,
    synthesize_rows,
    tz_matches_country,
)
from backend.app.services.device_profile import (  # noqa: E402
    COUNTRY_LANG_MAP,
    DeviceProfileManager,
)
from backend.app.services.geo_catalog import infer_locale, resolve_iso2  # noqa: E402
from backend.app.services.grizzlysms import (  # noqa: E402
    grizzly_country_id_to_iso,
    resolve_grizzly_country_id,
)
from backend.app.services.sms_stock_service import (  # noqa: E402
    SmsStockService,
    enrich_stock_rows,
    parse_grizzly_price_payload,
    parse_vak_count_payload,
    reset_stock_cache,
)


GRIZZLY_PRICES_FIXTURE = {
    "151": {"tg": {"cost": 0.26, "count": 23603}},
    "22": {"tg": {"cost": 0.35, "count": 15200}},
    "6": {"tg": {"cost": 0.18, "count": 820}},
    "49": {"tg": {"cost": 0.41, "count": 140}},
    "37": {"tg": {"cost": 0.22, "count": 0}},
    "999": {"tg": {"cost": 0.99, "count": 12}},
}


class TestGrizzlyPriceFullParse(unittest.TestCase):
    def test_filters_zero_stock_and_sorts_by_count(self):
        rows = parse_grizzly_price_payload(GRIZZLY_PRICES_FIXTURE, service="tg")
        stocks = {item["provider_country_id"]: item for item in rows}
        self.assertIn("151", stocks)
        self.assertIn("22", stocks)
        self.assertIn("6", stocks)
        self.assertIn("49", stocks)
        self.assertNotIn("37", stocks)
        self.assertEqual(stocks["151"]["stock"], 23603)
        self.assertAlmostEqual(stocks["151"]["cost"], 0.26)
        self.assertEqual(stocks["22"]["stock"], 15200)
        self.assertAlmostEqual(stocks["22"]["cost"], 0.35)

        items = enrich_stock_rows(rows, "grizzlysms")
        self.assertEqual(items[0]["code"], "cl")
        self.assertEqual(items[0]["stock"], 23603)
        self.assertEqual(items[1]["code"], "in")
        self.assertEqual(items[2]["code"], "id")
        codes = [item["code"] for item in items]
        self.assertIn("lv", codes)
        unknown = next(item for item in items if item["provider_country_id"] == "999")
        self.assertEqual(unknown["stock"], 12)
        self.assertTrue(unknown["code"])

    def test_service_outer_map_shape(self):
        payload = {
            "tg": {
                "151": {"cost": 0.26, "count": 23603},
                "22": {"cost": 0.35, "count": 15200},
            }
        }
        rows = parse_grizzly_price_payload(payload, service="tg")
        self.assertEqual(len(rows), 2)
        self.assertEqual({item["provider_country_id"] for item in rows}, {"151", "22"})

    def test_unknown_countries_map_via_extended_ids(self):
        self.assertEqual(grizzly_country_id_to_iso(49), "lv")
        self.assertEqual(grizzly_country_id_to_iso(37), "ma")
        self.assertEqual(grizzly_country_id_to_iso(9), "tz")
        self.assertEqual(grizzly_country_id_to_iso(77), "cy")
        self.assertEqual(resolve_grizzly_country_id("lv"), 49)
        self.assertEqual(resolve_grizzly_country_id("拉脱维亚"), 49)
        self.assertEqual(resolve_grizzly_country_id("Morocco"), 37)


class TestVakCountParse(unittest.TestCase):
    def test_nested_and_flat_payloads(self):
        nested = {"cl": {"tg": 900}, "id": {"tg": 400}, "lv": {"tg": 0}}
        rows = parse_vak_count_payload(nested, service="tg")
        codes = {item["provider_country_id"] for item in rows}
        self.assertEqual(codes, {"cl", "id"})

        flat = {"tg": {"cl": 1200, "in": 80}}
        rows = parse_vak_count_payload(flat, service="tg")
        self.assertEqual(len(rows), 2)
        self.assertEqual({item["stock"] for item in rows}, {1200, 80})


class TestAdaptiveLocaleEngine(unittest.TestCase):
    def test_unknown_countries_are_not_hardcoded_presets(self):
        for code in ("lv", "cy", "ma", "tz"):
            self.assertNotIn(code, COUNTRY_LANG_MAP)

    def test_latvia_cyprus_morocco_tanzania(self):
        lv = infer_locale("lv")
        self.assertEqual(lv["lang_code"], "lv")
        self.assertEqual(lv["system_lang_code"], "lv-lv")
        self.assertEqual(lv["dial"], "371")
        self.assertEqual(lv["tz_offset"], 7200)
        self.assertEqual(lv["name_zh"], "拉脱维亚")

        cy = infer_locale("CY")
        self.assertEqual(cy["system_lang_code"], "el-cy")
        self.assertEqual(cy["dial"], "357")
        self.assertEqual(cy["tz_offset"], 7200)

        ma = infer_locale("ma")
        self.assertEqual(ma["system_lang_code"], "ar-ma")
        self.assertIn("fr-ma", ma.get("alt_system_lang_codes") or ())
        self.assertEqual(ma["dial"], "212")
        self.assertEqual(ma["tz_offset"], 3600)

        tz = infer_locale("TZA")
        self.assertEqual(tz["code"], "tz")
        self.assertEqual(tz["system_lang_code"], "sw-tz")
        self.assertEqual(tz["dial"], "255")
        self.assertEqual(tz["tz_offset"], 10800)

    def test_profile_overlay_uses_inference_not_chile_fallback(self):
        profile = {}
        DeviceProfileManager._apply_locale(profile, "lv", None, "none")
        self.assertEqual(profile["lang_code"], "lv")
        self.assertEqual(profile["system_lang_code"], "lv-lv")
        self.assertEqual(profile["tz_offset"], 7200)
        self.assertNotEqual(profile["system_lang_code"], "es-cl")

        cy_profile = {}
        DeviceProfileManager._apply_locale(cy_profile, "cy", {"lang_code": "en", "system_lang_code": "en-us"}, "fallback")
        self.assertEqual(cy_profile["system_lang_code"], "el-cy")
        self.assertEqual(cy_profile["locale_source"], "country_overlay")

    def test_name_resolver(self):
        self.assertEqual(resolve_iso2("拉脱维亚"), "lv")
        self.assertEqual(resolve_iso2("Cyprus"), "cy")
        self.assertEqual(resolve_iso2("摩洛哥"), "ma")
        self.assertEqual(resolve_iso2("tanzania"), "tz")

    def test_synth_engine_accepts_unpreset_country(self):
        spec = resolve_synth_spec("lv")
        self.assertTrue(spec.get("inferred"))
        self.assertEqual(spec["name"], "Latvia")
        rows = synthesize_rows("lv", 20, seed=7)
        self.assertEqual(len(rows), 20)
        self.assertTrue(any(row["system_lang_code"] == "lv-lv" for row in rows))
        for row in rows:
            self.assertTrue(sku_sdk_consistent(row["device_model"], row["system_version"]))
            self.assertTrue(locale_matches_country(row["lang_code"], row["system_lang_code"], "lv"))
            self.assertTrue(tz_matches_country(row["tz_offset"], "lv"))


class TestSmsStockServiceCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_stock_cache()

    async def asyncTearDown(self):
        reset_stock_cache()

    async def test_ttl_cache_and_forced_refresh(self):
        items = enrich_stock_rows(
            parse_grizzly_price_payload(GRIZZLY_PRICES_FIXTURE),
            "grizzlysms",
        )
        fetch = AsyncMock(return_value=items)
        with patch.object(SmsStockService, "_fetch_grizzly", fetch):
            first = await SmsStockService.get_available_countries(provider="grizzlysms", refresh=False)
            second = await SmsStockService.get_available_countries(provider="grizzlysms", refresh=False)
            third = await SmsStockService.get_available_countries(provider="grizzlysms", refresh=True)

        self.assertEqual(fetch.await_count, 2)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertFalse(third.cached)
        self.assertEqual(first.total_countries, len(items))
        self.assertEqual(first.total_stock, sum(item["stock"] for item in items))
        self.assertEqual(first.items[0]["code"], "cl")

    async def test_refresh_failure_falls_back_to_stale_cache(self):
        items = enrich_stock_rows(
            parse_grizzly_price_payload({"151": {"tg": {"cost": 0.26, "count": 10}}}),
            "grizzlysms",
        )
        fetch = AsyncMock(side_effect=[items, RuntimeError("NO_KEY")])
        with patch.object(SmsStockService, "_fetch_grizzly", fetch):
            first = await SmsStockService.get_available_countries(provider="grizzlysms", refresh=True)
            failed = await SmsStockService.get_available_countries(provider="grizzlysms", refresh=True)
        self.assertEqual(first.total_countries, 1)
        self.assertTrue(failed.cached)
        self.assertEqual(failed.total_countries, 1)
        self.assertIn("NO_KEY", failed.message)


class TestAvailableCountriesApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.client = TestClient(app)

    def tearDown(self):
        reset_stock_cache()

    def test_available_countries_shape_and_refresh_param(self):
        items = [
            {
                "code": "cl",
                "name": "Chile",
                "name_zh": "智利",
                "dial": "56",
                "flag": "🇨🇱",
                "stock": 23603,
                "cost": 0.26,
                "provider": "grizzlysms",
                "provider_country_id": "151",
                "lang_code": "es",
                "system_lang_code": "es-cl",
                "tz_offset": -14400,
            },
            {
                "code": "in",
                "name": "India",
                "name_zh": "印度",
                "dial": "91",
                "flag": "🇮🇳",
                "stock": 15200,
                "cost": 0.35,
                "provider": "grizzlysms",
                "provider_country_id": "22",
                "lang_code": "en",
                "system_lang_code": "en-in",
                "tz_offset": 19800,
            },
        ]
        snap = type("Snap", (), {
            "to_dict": lambda self: {
                "success": True,
                "provider": "grizzlysms",
                "service": "tg",
                "items": items,
                "total_countries": 2,
                "total_stock": 38803,
                "updated_at": 1710000000.0,
                "cached": False,
                "cache_age_seconds": 0.0,
                "message": "ok",
            }
        })()
        with patch(
            "backend.app.api.routes.SmsStockService.get_available_countries",
            new=AsyncMock(return_value=snap),
        ) as mocked:
            res = self.client.get("/api/sms/available-countries?provider=grizzlysms&refresh=true")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertTrue(body["success"])
            self.assertEqual(body["provider"], "grizzlysms")
            self.assertEqual(body["total_countries"], 2)
            self.assertEqual(body["total_stock"], 38803)
            self.assertEqual(body["items"][0]["code"], "cl")
            self.assertEqual(body["items"][0]["stock"], 23603)
            self.assertAlmostEqual(body["items"][0]["cost"], 0.26)
            self.assertEqual(body["items"][0]["dial"], "56")
            self.assertIn("flag", body["items"][0])
            mocked.assert_awaited()
            kwargs = mocked.await_args.kwargs
            self.assertEqual(kwargs["provider"], "grizzlysms")
            self.assertTrue(kwargs["refresh"])


if __name__ == "__main__":
    unittest.main()
