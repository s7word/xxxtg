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
    classify_push_token,
    describe_push_slot,
    official_lang_pack_for_api_id,
)
from backend.app.services.device_profile import (  # noqa: E402
    DEFAULT_PROFILES,
    DeviceProfileManager,
    OFFICIAL_API_CREDENTIALS,
    apply_official_api_id,
)
from backend.app.services.init_connection import (  # noqa: E402
    apply_init_connection_overrides,
    inspect_init_param_keys,
)
from backend.app.services.ios_protocol import (  # noqa: E402
    REGHELP_IOS_PUSH_APP_NAME,
    TELEGRAM_IOS_BUNDLE_ID,
    UNOFFICIAL_IOS_API_CANDIDATES,
    assert_no_android_init_keys,
    reghelp_email_app_name,
    reghelp_push_app_name,
    resolve_login_email_types,
    should_migrate_to_nearest_dc,
    skip_antisafety_for_profile,
)
from backend.app.services.registrar import (  # noqa: E402
    DEFAULT_SMS_POLL_ATTEMPTS,
    SMS_POLL_INTERVAL_SECONDS,
    RegistrationOrchestrator,
)


class TestTelegramIosProfile(unittest.TestCase):
    def test_official_ios_credentials(self):
        self.assertEqual(OFFICIAL_API_CREDENTIALS[8], "7245de8e747a0d6fbe11f7cc14fcc0bb")
        ios = DEFAULT_PROFILES["telegram_ios"]
        self.assertEqual(ios["api_id"], 8)
        self.assertEqual(ios["api_hash"], OFFICIAL_API_CREDENTIALS[8])
        self.assertEqual(ios["lang_pack"], "ios")
        self.assertEqual(ios["app_device"], "iOS")
        self.assertEqual(ios["app_name"], REGHELP_IOS_PUSH_APP_NAME)
        self.assertEqual(ios["default_aid"], "")
        self.assertTrue(str(ios["device_model"]).startswith("iPhone"))
        self.assertNotIn(94575, OFFICIAL_API_CREDENTIALS)
        self.assertIn(94575, UNOFFICIAL_IOS_API_CANDIDATES)

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

    def test_apns_hex_not_suspicious_on_ios(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        token = "a" * 64
        info = classify_push_token(token, ios)
        self.assertEqual(info["kind"], "apns_hex")
        self.assertTrue(info["ok"])
        self.assertFalse(info["suspicious"])
        android = classify_push_token(token, DEFAULT_PROFILES["telegram_android"])
        self.assertTrue(android["suspicious"])

    def test_sms_poll_honors_telegram_timeout(self):
        sent = SimpleNamespace(timeout=90)
        attempts = RegistrationOrchestrator._sms_poll_attempts_for_sent_code(
            sent, DEFAULT_SMS_POLL_ATTEMPTS
        )
        self.assertGreaterEqual(attempts * SMS_POLL_INTERVAL_SECONDS, 90)
        self.assertGreaterEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)

    def test_otp_resend_allows_call_next_type(self):
        class CodeTypeCall:
            pass

        sent = SimpleNamespace(next_type=CodeTypeCall())
        self.assertTrue(RegistrationOrchestrator._next_type_allows_otp_resend(sent))
        empty = SimpleNamespace(next_type=None)
        self.assertFalse(RegistrationOrchestrator._next_type_allows_otp_resend(empty))

    def test_reghelp_app_names_are_not_android_tg(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        self.assertEqual(reghelp_push_app_name(ios), "tgiOS")
        self.assertEqual(reghelp_email_app_name(ios), "tg")
        self.assertEqual(reghelp_push_app_name(DEFAULT_PROFILES["telegram_android"]), "tg")

    def test_ios_skips_antisafety_and_aid(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        self.assertTrue(skip_antisafety_for_profile(ios))
        self.assertFalse(skip_antisafety_for_profile(DEFAULT_PROFILES["telegram_android"]))

    def test_smsbower_primary_never_asks_icloud(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        self.assertEqual(
            resolve_login_email_types(ios, SimpleNamespace(email_provider_mode="smsbower_primary")),
            ["gmail"],
        )
        self.assertEqual(
            resolve_login_email_types(ios, SimpleNamespace(email_provider_mode="smsbower_only")),
            ["gmail"],
        )
        self.assertEqual(
            resolve_login_email_types(ios, SimpleNamespace(email_provider_mode="reghelp_primary")),
            ["icloud", "gmail"],
        )

    def test_ios_migrates_dc2_to_suggested_dc5(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        self.assertTrue(should_migrate_to_nearest_dc(ios, 2, 5))
        self.assertFalse(should_migrate_to_nearest_dc(ios, 5, 5))
        self.assertFalse(should_migrate_to_nearest_dc(DEFAULT_PROFILES["telegram_android"], 2, 5))

    def test_ios_init_params_are_official_bundle_not_android_safety(self):
        class FakeInitRequest:
            def __init__(self):
                self.lang_pack = ""
                self.params = None

        class FakeClient:
            def __init__(self):
                self._init_request = FakeInitRequest()

        client = FakeClient()
        apns = "a" * 64
        apply_init_connection_overrides(
            client,
            dict(DEFAULT_PROFILES["telegram_ios"], tz_offset=28800),
            SimpleNamespace(
                official_client_emulation=True,
                init_connection_set_lang_pack=False,
                init_connection_set_tz_offset=False,
            ),
            push_token=apns,
        )
        keys = inspect_init_param_keys(client._init_request.params)
        self.assertEqual(client._init_request.lang_pack, "ios")
        self.assertEqual(keys, ["tz_offset", "bundleId", "device_token"])
        self.assertEqual(assert_no_android_init_keys(keys), [])
        values = {item.key: getattr(item.value, "value", None) for item in client._init_request.params.value}
        self.assertEqual(values["bundleId"], TELEGRAM_IOS_BUNDLE_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
