"""InitConnection lang_pack/tz_offset 与 vault 指纹回放回归。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.device_profile import (  # noqa: E402
    DeviceProfileManager,
    load_vault_android_fingerprints,
    pick_vault_fingerprint,
    split_app_version,
)
from backend.app.services.init_connection import (  # noqa: E402
    apply_init_connection_overrides,
    describe_init_connection,
    inspect_tz_offset,
)


class FakeInitRequest:
    def __init__(self):
        self.lang_pack = ""
        self.params = None


class FakeClient:
    def __init__(self, with_init: bool = True):
        if with_init:
            self._init_request = FakeInitRequest()


class TestInitConnectionOverrides(unittest.TestCase):
    def test_schema_defaults_off(self):
        cfg = AppConfigModel()
        self.assertFalse(cfg.init_connection_set_lang_pack)
        self.assertFalse(cfg.init_connection_set_tz_offset)
        self.assertIsNone(cfg.init_connection_tz_offset_override)
        self.assertFalse(cfg.force_country_locale)
        self.assertFalse(cfg.vault_fingerprint_replay)
        self.assertEqual(cfg.device_alignment_mode, "loose")
        self.assertFalse(cfg.strict_vault_device_alignment)
        self.assertTrue(cfg.app_delivery_fast_drop)
        self.assertTrue(AppConfigModel(init_connection_set_lang_pack="true").init_connection_set_lang_pack)
        self.assertEqual(
            AppConfigModel(init_connection_tz_offset_override="19800").init_connection_tz_offset_override,
            19800,
        )
        self.assertIsNone(AppConfigModel(init_connection_tz_offset_override="").init_connection_tz_offset_override)

    def test_default_leaves_telethon_empty(self):
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"lang_pack": "android", "tz_offset": 19800},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
            ),
        )
        self.assertTrue(snap["available"])
        self.assertEqual(client._init_request.lang_pack, "")
        self.assertIsNone(client._init_request.params)
        self.assertIn("(empty)", describe_init_connection(client))
        self.assertIn("未写入", describe_init_connection(client))

    def test_sets_lang_pack_android_and_tz(self):
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"lang_pack": "android", "tz_offset": 19800},
            SimpleNamespace(
                init_connection_set_lang_pack=True,
                init_connection_set_tz_offset=True,
                init_connection_tz_offset_override=None,
            ),
        )
        self.assertTrue(snap["lang_pack_set"])
        self.assertTrue(snap["tz_offset_set"])
        self.assertEqual(client._init_request.lang_pack, "android")
        self.assertEqual(inspect_tz_offset(client._init_request.params), 19800)
        self.assertIn("lang_pack=android", describe_init_connection(client))
        self.assertIn("tz_offset=19800", describe_init_connection(client))

    def test_tz_override_chile_default(self):
        client = FakeClient()
        apply_init_connection_overrides(
            client,
            {"lang_pack": "android", "tz_offset": 19800},
            SimpleNamespace(
                init_connection_set_lang_pack=True,
                init_connection_set_tz_offset=True,
                init_connection_tz_offset_override=-14400,
            ),
        )
        self.assertEqual(inspect_tz_offset(client._init_request.params), -14400)

    def test_strict_mode_writes_lang_pack_and_tz_without_flags(self):
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"lang_pack": "android", "tz_offset": 25200},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                init_connection_tz_offset_override=None,
                device_alignment_mode="strict",
                strict_vault_device_alignment=True,
            ),
        )
        self.assertTrue(snap["lang_pack_set"])
        self.assertTrue(snap["tz_offset_set"])
        self.assertEqual(client._init_request.lang_pack, "android")
        self.assertEqual(inspect_tz_offset(client._init_request.params), 25200)
        self.assertIn("lang_pack=android", describe_init_connection(client))
        self.assertIn("tz_offset=25200", describe_init_connection(client))

    def test_loose_mode_keeps_telethon_empty_without_flags(self):
        client = FakeClient()
        apply_init_connection_overrides(
            client,
            {"lang_pack": "android", "tz_offset": 28800},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                device_alignment_mode="loose",
                strict_vault_device_alignment=False,
            ),
        )
        self.assertEqual(client._init_request.lang_pack, "")
        self.assertIsNone(client._init_request.params)

    def test_api_id_4_writes_handshake_even_in_loose(self):
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"api_id": 4, "lang_pack": "android", "tz_offset": 28800},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                device_alignment_mode="loose",
                strict_vault_device_alignment=False,
            ),
        )
        self.assertTrue(snap["lang_pack_set"])
        self.assertTrue(snap["tz_offset_set"])
        self.assertEqual(client._init_request.lang_pack, "android")
        self.assertEqual(inspect_tz_offset(client._init_request.params), 28800)

    def test_api_id_6_writes_android_lang_pack_in_loose(self):
        """官方 Android api_id=6 声称 lang_pack=android，握手不能再留 Telethon 空串。"""
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"api_id": 6, "lang_pack": "android", "tz_offset": 10800},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                device_alignment_mode="loose",
                strict_vault_device_alignment=False,
                official_client_emulation=False,
            ),
        )
        self.assertTrue(snap["lang_pack_set"])
        self.assertTrue(snap["tz_offset_set"])
        self.assertEqual(client._init_request.lang_pack, "android")
        self.assertEqual(inspect_tz_offset(client._init_request.params), 10800)

    def test_telegram_x_writes_android_x_lang_pack(self):
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"api_id": 21724, "lang_pack": "android_x", "tz_offset": 25200},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                device_alignment_mode="loose",
                strict_vault_device_alignment=False,
            ),
        )
        self.assertTrue(snap["lang_pack_set"])
        self.assertEqual(client._init_request.lang_pack, "android_x")
        self.assertEqual(inspect_tz_offset(client._init_request.params), 25200)

    def test_official_emulation_writes_handshake_for_api6(self):
        client = FakeClient()
        snap = apply_init_connection_overrides(
            client,
            {"api_id": 6, "lang_pack": "android", "tz_offset": -14400},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                device_alignment_mode="loose",
                official_client_emulation=True,
            ),
        )
        self.assertTrue(snap["lang_pack_set"])
        self.assertTrue(snap["tz_offset_set"])
        self.assertEqual(client._init_request.lang_pack, "android")

    def test_custom_unpublished_api_id_leaves_telethon_empty(self):
        client = FakeClient()
        apply_init_connection_overrides(
            client,
            {"api_id": 35337905, "lang_pack": "android", "tz_offset": 25200},
            SimpleNamespace(
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
                device_alignment_mode="loose",
                official_client_emulation=False,
            ),
        )
        self.assertEqual(client._init_request.lang_pack, "")
        self.assertIsNone(client._init_request.params)

    def test_missing_init_request_is_blocked(self):
        client = FakeClient(with_init=False)
        snap = apply_init_connection_overrides(client, {}, SimpleNamespace())
        self.assertFalse(snap["available"])
        self.assertEqual(snap["blocked"], "no_init_request")

    def test_magicmock_init_request_is_not_writable(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        snap = apply_init_connection_overrides(
            client,
            {"api_id": 4, "lang_pack": "android", "tz_offset": 25200},
            SimpleNamespace(init_connection_set_lang_pack=True, init_connection_set_tz_offset=True),
        )
        self.assertFalse(snap["available"])
        self.assertEqual(snap["blocked"], "init_request_not_writable")
        self.assertFalse(snap["lang_pack_set"])


class TestVaultFingerprintLoader(unittest.TestCase):
    def test_split_app_version(self):
        self.assertEqual(
            split_app_version("12.7.3 (67502)")["app_build"],
            "67502",
        )
        self.assertEqual(split_app_version("12.7.3 (67502)")["app_version_pure"], "12.7.3")

    def test_load_skips_secrets(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            payload = {
                "phone": "918349951655",
                "app_id": 4,
                "device": "vivoV2050",
                "sdk": "SDK 32",
                "app_version": "12.7.3 (67502)",
                "lang_pack": "android",
                "system_lang_pack": "en-gb",
                "tz_offset": 19800,
                "device_token": "SHOULD_NOT_APPEAR",
                "device_secret": "SHOULD_NOT_APPEAR",
            }
            (root / "918349951655.json").write_text(json.dumps(payload), encoding="utf-8")
            rows = load_vault_android_fingerprints(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["device_model"], "vivoV2050")
            blob = json.dumps(rows[0])
            self.assertNotIn("SHOULD_NOT_APPEAR", blob)
            self.assertNotIn("device_token", rows[0])
            picked = pick_vault_fingerprint(root)
            self.assertEqual(picked["system_version"], "SDK 32")
        finally:
            tmp.cleanup()


class TestForceCountryLocale(unittest.TestCase):
    def test_force_country_locale_overrides_pack(self):
        sampled = {
            "lang_code": "es",
            "system_lang_code": "es-cl",
            "tz_offset": -14400,
        }
        profile: dict = {}
        cfg = SimpleNamespace(force_country_locale=True)
        with patch(
            "backend.app.services.device_profile.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=cfg),
        ):
            DeviceProfileManager._apply_locale(profile, "in", sampled, "country")
        self.assertEqual(profile["system_lang_code"], "en-in")
        self.assertEqual(profile["tz_offset"], 19800)
        self.assertEqual(profile["locale_source"], "country_overlay")

    def test_pack_locale_kept_when_not_forced(self):
        sampled = {
            "lang_code": "en",
            "system_lang_code": "en-in",
            "tz_offset": 19800,
        }
        profile: dict = {}
        cfg = SimpleNamespace(force_country_locale=False)
        with patch(
            "backend.app.services.device_profile.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=cfg),
        ):
            DeviceProfileManager._apply_locale(profile, "in", sampled, "country")
        self.assertEqual(profile["system_lang_code"], "en-in")
        self.assertEqual(profile["tz_offset"], 19800)
        self.assertEqual(profile["locale_source"], "pack")

    def test_mismatched_pack_locale_overlays_country(self):
        sampled = {
            "lang_code": "es",
            "system_lang_code": "es-cl",
            "tz_offset": -14400,
        }
        profile: dict = {}
        cfg = SimpleNamespace(force_country_locale=False)
        with patch(
            "backend.app.services.device_profile.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=cfg),
        ):
            DeviceProfileManager._apply_locale(profile, "in", sampled, "country")
        self.assertEqual(profile["system_lang_code"], "en-in")
        self.assertEqual(profile["tz_offset"], 19800)
        self.assertEqual(profile["locale_source"], "country_overlay")


if __name__ == "__main__":
    unittest.main()
