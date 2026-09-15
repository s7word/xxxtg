"""官方 iOS 协议模板：api_id=8、lang_pack=ios、不钉成 Android api_id=4。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.code_delivery import (  # noqa: E402
    profile_allows_app_hash,
    resolve_code_delivery_plan,
)
from backend.app.services.ios_protocol import (  # noqa: E402
    REGHELP_IOS_PUSH_APP_NAME,
    TELEGRAM_IOS_APPSTORE_ID,
    TELEGRAM_IOS_BUNDLE_ID,
    UNOFFICIAL_IOS_API_CANDIDATES,
    apply_ios_country_locale,
    assert_ios_init_keys_official,
    assert_no_android_init_keys,
    canonicalize_ios_system_lang,
    format_ios_locale_alignment,
    format_ios_submission_audit,
    ios_locale_aligned_with_country,
    is_apns_hex_token,
    ios_apns_device_token_b64,
    reghelp_email_app_name,
    reghelp_push_app_name,
    resolve_ios_app_sandbox,
    resolve_ios_code_settings_number_flags,
    resolve_login_email_types,
    should_migrate_to_nearest_dc,
    skip_antisafety_for_profile,
)
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
        self.assertIn(
            "device_token",
            describe_push_slot(
                False,
                profile=DEFAULT_PROFILES["telegram_android"],
                token="dGVzdA:APA91" + ("x" * 140),
            ),
        )

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
        self.assertTrue(should_migrate_to_nearest_dc(DEFAULT_PROFILES["telegram_android"], 2, 4))
        self.assertTrue(should_migrate_to_nearest_dc(DEFAULT_PROFILES["telegram_android_public"], 2, 4))
        self.assertFalse(should_migrate_to_nearest_dc({"api_id": 35337905}, 2, 4))

    def test_ios_init_params_tz_offset_and_bundle_id(self):
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
        self.assertEqual(keys, ["tz_offset", "bundleId"])
        self.assertEqual(assert_no_android_init_keys(keys), [])
        self.assertEqual(assert_ios_init_keys_official(keys), [])
        values = {item.key: getattr(item.value, "value", None) for item in client._init_request.params.value}
        self.assertEqual(int(values["tz_offset"]), 28800)
        self.assertEqual(values["bundleId"], TELEGRAM_IOS_BUNDLE_ID)
        self.assertNotIn("device_token", keys)
        self.assertNotIn("cert_fingerprint", keys)
        self.assertNotIn("safety_net", keys)
        self.assertNotIn("perf_cat", keys)
        self.assertIsNotNone(ios_apns_device_token_b64(apns))

    def test_ph_locale_is_en_PH_not_en_US(self):
        self.assertEqual(canonicalize_ios_system_lang("en-ph"), "en-PH")
        self.assertEqual(canonicalize_ios_system_lang("en_PH"), "en-PH")
        profile = apply_ios_country_locale(dict(DEFAULT_PROFILES["telegram_ios"]), "ph")
        self.assertEqual(profile["lang_code"], "en")
        self.assertEqual(profile["system_lang_code"], "en-PH")
        self.assertEqual(profile["tz_offset"], 28800)
        self.assertEqual(profile["bundle_id"], TELEGRAM_IOS_BUNDLE_ID)
        self.assertEqual(profile["appstore_id"], TELEGRAM_IOS_APPSTORE_ID)
        self.assertEqual(profile["install_source"], "appstore")
        self.assertTrue(ios_locale_aligned_with_country(profile, "ph"))
        self.assertFalse(
            ios_locale_aligned_with_country(
                {**profile, "system_lang_code": "en-US", "tz_offset": 28800},
                "ph",
            )
        )
        blob = format_ios_locale_alignment(country="ph", profile=profile)
        self.assertIn("system_lang=en-PH", blob)
        self.assertIn("aligned=是", blob)
        self.assertNotIn("perf_cat", blob)

    def test_resolved_ios_profile_follows_exit_country(self):
        profile = DeviceProfileManager.get_resolved_profile("telegram_ios", "ph")
        self.assertEqual(profile["lang_code"], "en")
        self.assertEqual(profile["system_lang_code"], "en-PH")
        self.assertEqual(profile["tz_offset"], 28800)
        self.assertEqual(profile["lang_pack"], "ios")
        self.assertEqual(profile["api_id"], 8)
        self.assertNotIn("perf_cat", profile)
        self.assertTrue(str(profile["device_model"]).startswith("iPhone"))
        self.assertRegex(str(profile["system_version"]), r"^18\.")
        self.assertTrue(ios_locale_aligned_with_country(profile, "ph"))

    def test_resolved_ios_profile_follows_turkey(self):
        profile = DeviceProfileManager.get_resolved_profile("telegram_ios", "tr")
        self.assertEqual(profile["lang_code"], "tr")
        self.assertEqual(profile["system_lang_code"], "tr-TR")
        self.assertEqual(profile["tz_offset"], 10800)
        self.assertEqual(profile["lang_pack"], "ios")
        self.assertEqual(profile["api_id"], 8)
        self.assertTrue(str(profile["device_model"]).startswith("iPhone"))
        self.assertTrue(ios_locale_aligned_with_country(profile, "tr"))
        self.assertFalse(ios_locale_aligned_with_country(profile, "ph"))

    def test_ios_display_and_resolve_ignore_custom_roommate_id(self):
        cfg = SimpleNamespace(
            api_credential_mode="custom",
            custom_api_id=35337905,
            custom_api_hash="deadbeef" * 4,
            official_client_emulation=False,
            antisafety_aids={},
        )
        from backend.app.config import ConfigManager

        mgr = SimpleNamespace(config=cfg)
        with patch.object(ConfigManager, "get_instance", return_value=mgr):
            cards = DeviceProfileManager.get_all_profiles()
        ios = next(item for item in cards if item["key"] == "telegram_ios")
        android = next(item for item in cards if item["key"] == "telegram_android")
        self.assertEqual(ios["api_id"], 8)
        self.assertEqual(ios["template_api_id"], 8)
        self.assertTrue(ios["is_ios"])
        self.assertNotEqual(ios["api_id"], 35337905)
        self.assertEqual(android["api_id"], 6)
        self.assertFalse(android.get("custom_overlay"))

        resolved = DeviceProfileManager.resolve_effective_credentials(
            dict(DEFAULT_PROFILES["telegram_ios"]),
            cfg,
            has_push_token=False,
        )
        self.assertEqual(resolved["api_id"], 8)
        self.assertEqual(resolved["credential_source"], "official")
        android_resolved = DeviceProfileManager.resolve_effective_credentials(
            dict(DEFAULT_PROFILES["telegram_android"]),
            cfg,
            has_push_token=False,
        )
        self.assertEqual(android_resolved["api_id"], 6)
        self.assertNotEqual(android_resolved["api_id"], 35337905)

    def test_resolved_ios_profile_follows_portugal(self):
        profile = DeviceProfileManager.get_resolved_profile("telegram_ios", "pt")
        self.assertEqual(profile["lang_code"], "pt")
        self.assertEqual(profile["system_lang_code"], "pt-PT")
        self.assertEqual(profile["tz_offset"], 0)
        self.assertEqual(profile["lang_pack"], "ios")
        self.assertEqual(profile["api_id"], 8)
        self.assertEqual(profile["bundle_id"], TELEGRAM_IOS_BUNDLE_ID)
        self.assertTrue(str(profile["device_model"]).startswith("iPhone"))
        self.assertTrue(ios_locale_aligned_with_country(profile, "pt"))
        self.assertFalse(ios_locale_aligned_with_country(profile, "ph"))
        self.assertFalse(ios_locale_aligned_with_country(profile, "tr"))
        self.assertNotEqual(profile["system_lang_code"], "pt-BR")

    def test_app_sandbox_is_apns_production_not_process_sandbox(self):
        self.assertIs(resolve_ios_app_sandbox(True), False)
        self.assertIsNone(resolve_ios_app_sandbox(False))
        ios = DEFAULT_PROFILES["telegram_ios"]
        plan = resolve_code_delivery_plan(
            SimpleNamespace(
                official_client_emulation=True,
                code_delivery_mode="balanced",
                code_settings_allow_firebase=True,
                code_settings_unknown_number=True,
            ),
            ios,
        )
        self.assertFalse(plan.allow_app_hash)
        self.assertTrue(plan.allow_firebase)
        self.assertIs(plan.app_sandbox, False)
        self.assertFalse(plan.unknown_number)
        self.assertTrue(plan.allow_flashcall)
        self.assertTrue(plan.allow_missed_call)
        cs = RegistrationOrchestrator._build_code_settings_from_plan("a" * 64, plan, ios)
        self.assertIs(cs.app_sandbox, False)
        self.assertTrue(cs.allow_firebase)
        self.assertEqual(cs.token, "a" * 64)
        self.assertFalse(cs.unknown_number)
        self.assertTrue(cs.allow_flashcall)
        self.assertTrue(cs.allow_missed_call)

    def test_ios_codesettings_follow_verified_success_payload(self):
        flags = resolve_ios_code_settings_number_flags()
        self.assertEqual(
            flags,
            {
                "unknown_number": False,
                "allow_flashcall": True,
                "allow_missed_call": True,
            },
        )
        self.assertEqual(
            resolve_ios_code_settings_number_flags("off"),
            {
                "unknown_number": False,
                "allow_flashcall": False,
                "allow_missed_call": False,
            },
        )

    def test_ios_rejects_fcm_in_codesettings_token(self):
        ios = DEFAULT_PROFILES["telegram_ios"]
        plan = SimpleNamespace(
            allow_app_hash=False,
            attach_push_token=True,
            allow_firebase=True,
            unknown_number=False,
            allow_flashcall=True,
            allow_missed_call=True,
            app_sandbox=False,
        )
        cs = RegistrationOrchestrator._build_code_settings_from_plan("legacy:APA91xxxx", plan, ios)
        self.assertFalse(cs.token)
        self.assertIsNone(cs.app_sandbox)
        self.assertFalse(cs.allow_firebase)

    def test_submission_audit_never_mentions_cert_as_submitted(self):
        lines = format_ios_submission_audit(
            profile=DEFAULT_PROFILES["telegram_ios"],
            push_token="b" * 64,
            init_keys=["tz_offset", "bundleId"],
            app_sandbox=False,
            allow_firebase=True,
            allow_app_hash=False,
            unknown_number=False,
            allow_flashcall=True,
            allow_missed_call=True,
        )
        blob = "\n".join(lines)
        self.assertIn("cert_fingerprint（Android APK 签名指纹）", blob)
        self.assertIn("明确未提交", blob)
        self.assertIn("APNS 生产证书", blob)
        self.assertIn("unknown_number=否", blob)
        self.assertIn("flashcall=是", blob)
        self.assertIn("missed=是", blob)
        self.assertIn("真机 bundleData 允许键=bundleId,tz_offset", blob)
        self.assertIn("params.perf_cat", blob)
        self.assertTrue(is_apns_hex_token("b" * 64))
        self.assertNotIn("cert_fingerprint=unknown", blob)
        extra_lines = format_ios_submission_audit(
            profile=DEFAULT_PROFILES["telegram_ios"],
            init_keys=["tz_offset", "bundleId", "device_token", "perf_cat"],
        )
        extra_blob = "\n".join(extra_lines)
        self.assertIn("非公开合同键", extra_blob)
        self.assertIn("device_token", extra_blob)
        self.assertIn("perf_cat", extra_blob)
        self.assertNotIn("非公开合同键: bundleId", extra_blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
