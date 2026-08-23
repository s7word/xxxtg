"""全球国家拓扑矩阵：语言、区号、姓名库与指纹合成。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.device_db_manager import (  # noqa: E402
    country_dial_code,
    country_display_name,
    country_display_name_zh,
    infer_country_from_filename,
    infer_country_from_stats,
    normalize_country,
)
from backend.app.services.device_generator import (  # noqa: E402
    COUNTRY_SYNTH,
    SUPPORTED_COUNTRIES,
    list_supported_countries,
    locale_matches_country,
    sku_sdk_consistent,
    synthesize_rows,
    tz_matches_country,
)
from backend.app.services.device_profile import (  # noqa: E402
    COUNTRY_LANG_MAP,
    GLOBAL_TOPOLOGY_COUNTRIES,
    DeviceProfileManager,
)
from backend.app.services.proxyseller import (  # noqa: E402
    COUNTRY_PROFILES,
    PHONE_DIAL_TO_ISO2,
    expand_country_aliases,
    infer_country_from_phone,
)
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    SYNTHETIC_IDENTITY_POOLS,
)


REQUIRED_COUNTRIES = (
    "ca", "gb", "de", "fr", "au", "jp", "kr", "th", "vn", "ph",
    "mx", "co", "pe", "ar", "eg", "za", "ng", "ke", "ua", "uz",
    "ae", "sa", "tr", "br", "us", "kz", "ru", "af", "cl", "in", "id",
)


class TestCountryLangMap(unittest.TestCase):
    def test_required_countries_registered(self):
        for code in REQUIRED_COUNTRIES:
            self.assertIn(code, COUNTRY_LANG_MAP, f"COUNTRY_LANG_MAP 缺少 {code}")
            spec = COUNTRY_LANG_MAP[code]
            self.assertTrue(spec.get("lang_code"), code)
            self.assertTrue(spec.get("system_lang_code"), code)
            self.assertIsInstance(spec.get("tz_offset"), int)
            self.assertTrue(spec.get("dial"), code)

    def test_canada_locale_and_timezone(self):
        ca = COUNTRY_LANG_MAP["ca"]
        self.assertEqual(ca["lang_code"], "en")
        self.assertEqual(ca["system_lang_code"], "en-ca")
        self.assertIn("fr-ca", ca.get("alt_system_lang_codes") or ())
        self.assertEqual(ca["tz_offset"], -18000)
        self.assertEqual(ca["dial"], "1")
        low, high = ca["tz_offset_range"]
        self.assertLessEqual(low, -18000)
        self.assertGreaterEqual(high, -14400)
        self.assertLessEqual(low, -28800)

    def test_global_topology_matches_lang_map(self):
        self.assertEqual(set(GLOBAL_TOPOLOGY_COUNTRIES), set(COUNTRY_LANG_MAP))
        self.assertTrue(set(REQUIRED_COUNTRIES).issubset(GLOBAL_TOPOLOGY_COUNTRIES))


class TestPhoneDialIntelligence(unittest.TestCase):
    def test_required_dial_prefixes(self):
        expected = {
            "1": "us",
            "44": "gb",
            "49": "de",
            "33": "fr",
            "61": "au",
            "81": "jp",
            "82": "kr",
            "66": "th",
            "84": "vn",
            "63": "ph",
            "52": "mx",
            "57": "co",
            "51": "pe",
            "54": "ar",
            "380": "ua",
            "998": "uz",
            "971": "ae",
            "966": "sa",
            "56": "cl",
            "91": "in",
            "62": "id",
            "7": "ru",
            "20": "eg",
            "27": "za",
            "234": "ng",
            "254": "ke",
            "93": "af",
            "90": "tr",
            "55": "br",
        }
        for prefix, iso2 in expected.items():
            self.assertEqual(PHONE_DIAL_TO_ISO2.get(prefix), iso2, prefix)

    def test_nanp_plus_one_us_vs_canada(self):
        self.assertEqual(infer_country_from_phone("+14165550100"), "ca")
        self.assertEqual(infer_country_from_phone("+1 604 555 0199"), "ca")
        self.assertEqual(infer_country_from_phone("15145551234"), "ca")
        self.assertEqual(infer_country_from_phone("+12125550199"), "us")
        self.assertEqual(infer_country_from_phone("+13105550123"), "us")
        self.assertEqual(infer_country_from_phone("12025550100"), "us")

    def test_plus_seven_ru_vs_kz(self):
        self.assertEqual(infer_country_from_phone("+77011234567"), "kz")
        self.assertEqual(infer_country_from_phone("+77771234567"), "kz")
        self.assertEqual(infer_country_from_phone("+79161234567"), "ru")
        self.assertEqual(infer_country_from_phone("74951234567"), "ru")

    def test_common_international_prefixes(self):
        self.assertEqual(infer_country_from_phone("+447911123456"), "gb")
        self.assertEqual(infer_country_from_phone("+4915112345678"), "de")
        self.assertEqual(infer_country_from_phone("+33612345678"), "fr")
        self.assertEqual(infer_country_from_phone("+61412345678"), "au")
        self.assertEqual(infer_country_from_phone("+819012345678"), "jp")
        self.assertEqual(infer_country_from_phone("+821012345678"), "kr")
        self.assertEqual(infer_country_from_phone("+66812345678"), "th")
        self.assertEqual(infer_country_from_phone("+84901234567"), "vn")
        self.assertEqual(infer_country_from_phone("+639171234567"), "ph")
        self.assertEqual(infer_country_from_phone("+525512345678"), "mx")
        self.assertEqual(infer_country_from_phone("+380501234567"), "ua")
        self.assertEqual(infer_country_from_phone("+998901234567"), "uz")
        self.assertEqual(infer_country_from_phone("+971501234567"), "ae")
        self.assertEqual(infer_country_from_phone("+966501234567"), "sa")

    def test_canada_proxy_aliases(self):
        aliases = expand_country_aliases("ca")
        self.assertIn("ca", aliases)
        self.assertIn("can", aliases)
        self.assertIn("canada", aliases)
        self.assertIn("ca", COUNTRY_PROFILES)


class TestSyntheticIdentityPools(unittest.TestCase):
    def test_canada_and_major_locales_have_name_pools(self):
        for code in REQUIRED_COUNTRIES:
            pool = SYNTHETIC_IDENTITY_POOLS.get(code) or SYNTHETIC_IDENTITY_POOLS["default"]
            self.assertGreaterEqual(len(pool["first"]), 6, code)
            self.assertGreaterEqual(len(pool["last"]), 6, code)
        ca = SYNTHETIC_IDENTITY_POOLS["ca"]
        self.assertIn("Tremblay", ca["last"])
        self.assertTrue(any(name in ca["first"] for name in ("Liam", "Étienne", "Amélie")))

    def test_orchestrator_draws_canada_names(self):
        first, last = RegistrationOrchestrator._get_random_name("ca")
        self.assertIn(first, SYNTHETIC_IDENTITY_POOLS["ca"]["first"])
        self.assertIn(last, SYNTHETIC_IDENTITY_POOLS["ca"]["last"])


class TestDeviceFingerprintSynth(unittest.TestCase):
    def test_supported_countries_cover_required(self):
        self.assertTrue(set(REQUIRED_COUNTRIES).issubset(COUNTRY_SYNTH))
        self.assertEqual(set(SUPPORTED_COUNTRIES), set(COUNTRY_SYNTH))
        listed = {item["code"]: item for item in list_supported_countries()}
        self.assertIn("ca", listed)
        self.assertEqual(listed["ca"]["name"], "Canada")
        self.assertEqual(listed["ca"]["name_zh"], "加拿大")
        self.assertEqual(listed["ca"]["dial"], "1")

    def test_canada_rows_are_internally_consistent(self):
        rows = synthesize_rows("ca", 80, seed=23)
        self.assertEqual(len(rows), 80)
        locales = {(row["lang_code"], row["system_lang_code"]) for row in rows}
        self.assertIn(("en", "en-ca"), locales)
        self.assertTrue(any(row["system_lang_code"] == "fr-ca" for row in rows))
        offsets = {row["tz_offset"] for row in rows}
        self.assertIn(-18000, offsets)
        self.assertTrue(offsets <= {-18000, -14400, -21600, -25200, -28800})
        for row in rows:
            self.assertTrue(sku_sdk_consistent(row["device_model"], row["system_version"]))
            self.assertTrue(locale_matches_country(row["lang_code"], row["system_lang_code"], "ca"))
            self.assertTrue(tz_matches_country(row["tz_offset"], "ca"))

    def test_filename_and_locale_infer_canada(self):
        self.assertEqual(infer_country_from_filename("2026-08-23_12-00-00_Canada.db"), "ca")
        self.assertEqual(infer_country_from_filename("canadian_pack.db"), "ca")
        self.assertEqual(normalize_country("Canada"), "ca")
        self.assertEqual(country_display_name("ca"), "Canada")
        self.assertEqual(country_display_name_zh("ca"), "加拿大")
        self.assertEqual(country_dial_code("ca"), "1")
        hinted = infer_country_from_stats({
            "system_lang_codes": {"en-ca": 40, "fr-ca": 12},
            "tz_offsets": {"-18000": 38},
            "lang_codes": {"en": 40, "fr": 12},
        })
        self.assertEqual(hinted, "ca")

    def test_locale_overlay_uses_canada_map(self):
        profile = {}
        DeviceProfileManager._apply_locale(profile, "ca", None, "none")
        self.assertEqual(profile["lang_code"], "en")
        self.assertEqual(profile["system_lang_code"], "en-ca")
        self.assertEqual(profile["tz_offset"], -18000)
        self.assertEqual(profile["locale_source"], "country_overlay")


class TestCatalogHelpers(unittest.TestCase):
    def test_display_helpers_cover_matrix(self):
        for code in REQUIRED_COUNTRIES:
            self.assertTrue(country_display_name(code))
            self.assertTrue(country_display_name_zh(code), code)
            self.assertTrue(country_dial_code(code), code)


if __name__ == "__main__":
    unittest.main()
