"""严格设备对齐：缺字段拒绝发码；api_id=4 不漂到 6；Push 形态。"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.device_alignment import (  # noqa: E402
    DeviceAlignmentError,
    classify_push_token,
    detect_push_slot_conflicts,
    is_strict_alignment,
    validate_strict_device_profile,
)
from backend.app.services.device_profile import (  # noqa: E402
    DeviceProfileManager,
    OFFICIAL_API_CREDENTIALS,
    apply_official_api_id,
)
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from telethon.tl import types  # noqa: E402


def _strict_cfg(**kwargs):
    defaults = {
        "device_alignment_mode": "strict",
        "strict_vault_device_alignment": True,
        "pin_app_version_substr": "12.7.3",
        "official_client_emulation": False,
        "api_credential_mode": "official",
        "custom_api_id": None,
        "custom_api_hash": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _ok_profile(**kwargs):
    profile = {
        "api_id": 4,
        "api_hash": OFFICIAL_API_CREDENTIALS[4],
        "device_model": "vivoV2050",
        "system_version": "SDK 32",
        "app_version": "12.7.3 (67502)",
        "lang_code": "vi",
        "system_lang_code": "vi-vn",
        "lang_pack": "android",
        "tz_offset": 25200,
        "app_device": "Android",
    }
    profile.update(kwargs)
    return profile


class TestStrictAlignmentGate(unittest.TestCase):
    def test_schema_defaults_strict(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.device_alignment_mode, "loose")
        self.assertFalse(cfg.strict_vault_device_alignment)
        self.assertFalse(is_strict_alignment(cfg))

    def test_loose_explicit(self):
        self.assertFalse(is_strict_alignment(SimpleNamespace(
            device_alignment_mode="loose",
            strict_vault_device_alignment=False,
        )))

    def test_missing_fields_reject_sendcode(self):
        broken = _ok_profile(lang_pack="", tz_offset=None, device_model="")
        with self.assertRaises(DeviceAlignmentError) as ctx:
            validate_strict_device_profile(broken, _strict_cfg())
        self.assertEqual(ctx.exception.reason, "DEVICE_ALIGNMENT_REJECTED")
        self.assertIn("lang_pack", ctx.exception.missing)
        self.assertIn("tz_offset", ctx.exception.missing)
        self.assertIn("device_model", ctx.exception.missing)
        self.assertIn("严格设备对齐拒绝发码", str(ctx.exception))

    def test_api_id_6_rejected_in_strict(self):
        drifted = _ok_profile(api_id=6, api_hash=OFFICIAL_API_CREDENTIALS[6])
        with self.assertRaises(DeviceAlignmentError) as ctx:
            validate_strict_device_profile(drifted, _strict_cfg())
        self.assertIn("api_id", ctx.exception.missing)
        self.assertIn("禁止漂到 6", str(ctx.exception))

    def test_emulator_rejected(self):
        emu = _ok_profile(device_model="sdk_gphone64_x86_64")
        with self.assertRaises(DeviceAlignmentError) as ctx:
            validate_strict_device_profile(emu, _strict_cfg())
        self.assertIn("模拟器", str(ctx.exception))

    def test_ok_profile_passes(self):
        out = validate_strict_device_profile(_ok_profile(), _strict_cfg())
        self.assertTrue(out["ok"])
        self.assertEqual(out["api_id"], 4)
        self.assertEqual(out["lang_pack"], "android")
        self.assertEqual(out["tz_offset"], 25200)

    def test_loose_skips_gate(self):
        out = validate_strict_device_profile(
            {"api_id": 6},
            SimpleNamespace(device_alignment_mode="loose", strict_vault_device_alignment=False),
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["strict"])

    def test_resolve_credentials_pins_api4(self):
        profile = {"api_id": 6, "api_hash": OFFICIAL_API_CREDENTIALS[6]}
        resolved = DeviceProfileManager.resolve_effective_credentials(
            profile, _strict_cfg(), has_push_token=True
        )
        self.assertEqual(resolved["api_id"], 4)
        self.assertEqual(resolved["api_hash"], OFFICIAL_API_CREDENTIALS[4])
        self.assertEqual(resolved["credential_source"], "vault_strict_api4")

    def test_apply_official_keeps_hash_pair(self):
        out = apply_official_api_id({"api_id": 6, "api_hash": OFFICIAL_API_CREDENTIALS[6]}, 4)
        self.assertEqual(out["api_id"], 4)
        self.assertEqual(out["api_hash"], OFFICIAL_API_CREDENTIALS[4])

    def test_normalize_rejects_api4_paired_with_api6_hash(self):
        from backend.app.services.device_profile import normalize_official_api_credentials

        mixed = normalize_official_api_credentials({
            "api_id": 4,
            "api_hash": OFFICIAL_API_CREDENTIALS[6],
        })
        self.assertEqual(mixed["api_id"], 4)
        self.assertEqual(mixed["api_hash"], OFFICIAL_API_CREDENTIALS[4])
        self.assertTrue(mixed["api_hash_corrected"])
        self.assertEqual(mixed["api_hash_was"], OFFICIAL_API_CREDENTIALS[6])

    def test_telegram_x_keeps_android_x_lang_pack_when_pack_says_android(self):
        sampled = {
            "row": {
                "device_model": "samsungSM-S918B",
                "system_version": "SDK 33",
                "perf_cat": 2,
                "lang_pack": "android",
                "app_version": "12.9.1 (69792)",
                "app_version_pure": "12.9.1",
                "app_build": "69792",
                "api_id": 6,
                "api_hash": OFFICIAL_API_CREDENTIALS[6],
                "lang_code": "en",
                "system_lang_code": "en-us",
                "tz_offset": 0,
            },
            "pack": {"id": "p1", "alias": "gb", "country": "gb"},
            "match": "country",
            "created": False,
        }
        cfg = SimpleNamespace(
            device_alignment_mode="loose",
            strict_vault_device_alignment=False,
            pin_app_version_substr="",
            force_country_locale=False,
            vault_fingerprint_replay=False,
            official_client_emulation=False,
            antisafety_aids={},
            inject_vault_device_secret=False,
            vault_attestation_persist_secrets=False,
        )
        with patch(
            "backend.app.services.device_profile.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=cfg),
        ), patch.object(
            DeviceProfileManager,
            "_manager",
            return_value=SimpleNamespace(select_sample=lambda country: sampled),
        ):
            profile = DeviceProfileManager.get_resolved_profile("telegram_x", "gb")
        self.assertEqual(profile["api_id"], 21724)
        self.assertEqual(profile["api_hash"], OFFICIAL_API_CREDENTIALS[21724])
        self.assertEqual(profile["lang_pack"], "android_x")


class TestPushTokenClassify(unittest.TestCase):
    def test_empty_rejected(self):
        info = classify_push_token("")
        self.assertFalse(info["ok"])
        self.assertEqual(info["kind"], "empty")

    def test_fcm_legacy_ok(self):
        token = "abc:APA91b" + ("x" * 140)
        info = classify_push_token(token)
        self.assertTrue(info["ok"])
        self.assertEqual(info["kind"], "fcm_legacy")

    def test_short_rejected(self):
        info = classify_push_token("short")
        self.assertFalse(info["ok"])


class TestCodeSettingsFirebaseFlags(unittest.TestCase):
    def test_firebase_and_unknown_from_plan(self):
        plan = SimpleNamespace(
            allow_app_hash=True,
            attach_push_token=True,
            allow_firebase=True,
            unknown_number=True,
            allow_flashcall=False,
            allow_missed_call=False,
        )
        cs = RegistrationOrchestrator._build_code_settings_from_plan("FCM_TOKEN", plan)
        self.assertIsInstance(cs, types.CodeSettings)
        self.assertTrue(cs.allow_firebase)
        self.assertTrue(cs.unknown_number)
        self.assertEqual(cs.token, "FCM_TOKEN")


class TestGetResolvedProfileStrict(unittest.TestCase):
    def test_strict_profile_has_expert_fields(self):
        cfg = SimpleNamespace(
            device_alignment_mode="strict",
            strict_vault_device_alignment=True,
            pin_app_version_substr="12.7.3",
            force_country_locale=True,
            vault_fingerprint_replay=True,
            official_client_emulation=False,
            antisafety_aids={"telegram_android": "aid-android"},
            inject_vault_device_secret=False,
            vault_attestation_persist_secrets=False,
        )
        with patch(
            "backend.app.services.device_profile.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=cfg),
        ), patch.object(
            DeviceProfileManager,
            "_manager",
            return_value=SimpleNamespace(select_sample=lambda country: None),
        ):
            profile = DeviceProfileManager.get_resolved_profile("telegram_android", "vn")
        self.assertEqual(profile["api_id"], 4)
        self.assertEqual(profile["api_hash"], OFFICIAL_API_CREDENTIALS[4])
        self.assertEqual(profile["lang_pack"], "android")
        self.assertEqual(profile["tz_offset"], 25200)
        self.assertEqual(profile["system_lang_code"], "vi-vn")
        self.assertIn("12.7.3", profile["app_version"])
        validate_strict_device_profile(profile, cfg)

    def test_strict_missing_model_fails_gate(self):
        cfg = _strict_cfg()
        profile = _ok_profile()
        profile.pop("device_model")
        with self.assertRaises(DeviceAlignmentError) as ctx:
            validate_strict_device_profile(profile, cfg)
        self.assertIn("device_model", ctx.exception.missing)

    def test_strict_hunt_proxy_max_uses_is_one(self):
        limits = RegistrationOrchestrator._resolve_hunt_limits(_strict_cfg(
            hunt_proxy_max_uses=5,
            hunt_device_max_uses=8,
            hunt_no_number_retries=20,
            hunt_no_number_retry_delay_sec=2.0,
            hunt_app_blacklist_ttl_hours=48,
            hunt_app_delivery_fuse=5,
        ))
        self.assertEqual(limits["proxy_max_uses"], 1)

    def test_loose_hunt_proxy_keeps_config(self):
        limits = RegistrationOrchestrator._resolve_hunt_limits(SimpleNamespace(
            device_alignment_mode="loose",
            strict_vault_device_alignment=False,
            hunt_proxy_max_uses=5,
            hunt_device_max_uses=8,
            hunt_no_number_retries=20,
            hunt_no_number_retry_delay_sec=2.0,
            hunt_app_blacklist_ttl_hours=48,
            hunt_app_delivery_fuse=5,
        ))
        self.assertEqual(limits["proxy_max_uses"], 5)


class TestVaultAttestationMetadata(unittest.TestCase):
    def test_scan_does_not_include_secret_text(self):
        from backend.app.services.vault_attestation import scan_vault_attestation

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            payload = {
                "app_id": 4,
                "device_secret": "SUPER_SECRET_VALUE_SHOULD_NOT_LEAK",
                "device_token": "TOKEN_SHOULD_NOT_LEAK",
            }
            (root / "918000000000.json").write_text(json.dumps(payload), encoding="utf-8")
            rows = scan_vault_attestation(root)
            self.assertEqual(len(rows), 1)
            blob = json.dumps(rows)
            self.assertNotIn("SUPER_SECRET", blob)
            self.assertNotIn("TOKEN_SHOULD_NOT_LEAK", blob)
            self.assertTrue(rows[0]["has_device_secret"])
            self.assertGreater(rows[0]["device_secret_len"], 0)
        finally:
            tmp.cleanup()


class TestPushSlotConflicts(unittest.TestCase):
    def test_android_fcm_in_ios_slot_is_flagged(self):
        conflicts = detect_push_slot_conflicts(
            {
                "app_device": "Android",
                "lang_pack": "android",
                "system_version": "SDK 29",
                "device_model": "OPPOCPH2035",
            },
            "dGVzdA:APA91" + ("x" * 140),
            attached=True,
        )
        self.assertTrue(any("错槽" in c for c in conflicts))
        self.assertFalse(any(c.startswith("类型冲突") for c in conflicts))

    def test_android_with_apns_hex_is_hard_conflict(self):
        conflicts = detect_push_slot_conflicts(
            {"app_device": "Android", "lang_pack": "android"},
            "a" * 64,
            attached=True,
        )
        self.assertTrue(any("类型冲突" in c for c in conflicts))


class TestApiHashLogMask(unittest.TestCase):
    def test_masks_full_hash(self):
        shown = RegistrationOrchestrator._api_hash_for_log(
            {"api_hash": OFFICIAL_API_CREDENTIALS[4]}
        )
        self.assertNotEqual(shown, OFFICIAL_API_CREDENTIALS[4])
        self.assertTrue(shown.startswith("014b35b6"))
        self.assertIn("…", shown)


if __name__ == "__main__":
    unittest.main()
