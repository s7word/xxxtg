"""官方 iOS 协议模板：api_id=8、lang_pack=ios、不钉成 Android api_id=4。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.code_delivery import profile_allows_app_hash  # noqa: E402
from backend.app.services.device_alignment import (  # noqa: E402
    OFFICIAL_IOS_API_ID,
    PUSH_SLOT_IOS_APNS,
    PUSH_SLOT_IOS_NON_APNS,
    describe_push_slot,
    official_lang_pack_for_api_id,
)
from backend.app.services.device_profile import (  # noqa: E402
    DEFAULT_PROFILES,
    DeviceProfileManager,
    OFFICIAL_API_CREDENTIALS,
    apply_official_api_id,
)


class TestTelegramIosProfile(unittest.TestCase):
    def test_official_ios_credentials(self):
        self.assertEqual(OFFICIAL_API_CREDENTIALS[8], "7245de8e747a0d6fbe11f7cc14fcc0bb")
        ios = DEFAULT_PROFILES["telegram_ios"]
        self.assertEqual(ios["api_id"], 8)
        self.assertEqual(ios["api_hash"], OFFICIAL_API_CREDENTIALS[8])
        self.assertEqual(ios["lang_pack"], "ios")
        self.assertEqual(ios["app_device"], "iOS")
        self.assertTrue(str(ios["device_model"]).startswith("iPhone"))

    def test_official_lang_pack_ios(self):
        self.assertEqual(official_lang_pack_for_api_id(8), "ios")
        self.assertEqual(official_lang_pack_for_api_id(OFFICIAL_IOS_API_ID), "ios")
        self.assertEqual(official_lang_pack_for_api_id(6), "android")
        self.assertEqual(official_lang_pack_for_api_id(21724), "android_x")

    def test_ios_disables_allow_app_hash(self):
        self.assertFalse(profile_allows_app_hash(DEFAULT_PROFILES["telegram_ios"]))
        self.assertTrue(profile_allows_app_hash(DEFAULT_PROFILES["telegram_android"]))

    def test_strict_finalize_does_not_pin_ios_to_api4(self):
        cfg = SimpleNamespace(device_alignment_mode="strict", strict_vault_device_alignment=True)
        profile = apply_official_api_id(dict(DEFAULT_PROFILES["telegram_ios"]), 8)
        out = DeviceProfileManager._finalize_credentials(profile, cfg)
        self.assertEqual(out["api_id"], 8)
        self.assertEqual(out["api_hash"], OFFICIAL_API_CREDENTIALS[8])
        self.assertEqual(out["lang_pack"], "ios")

    def test_push_slot_ios_apns(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        apns = "a" * 64
        self.assertEqual(describe_push_slot(True, profile=ios, token=apns), PUSH_SLOT_IOS_APNS)
        self.assertEqual(
            describe_push_slot(True, profile=ios, token="legacy:APA91xxxx"),
            PUSH_SLOT_IOS_NON_APNS,
        )
        self.assertIn("android_fcm", describe_push_slot(True, profile=DEFAULT_PROFILES["telegram_android"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
