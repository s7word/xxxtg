"""Attestation 网关隔离、Vak-SMS 自动退款与 API 凭证风险裁决测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.config import raw_urls_are_contaminated  # noqa: E402
from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.account_vault import VAULT_GUIDANCE, build_apps_apply_hint  # noqa: E402
from backend.app.services.antisafety import AntiSafetyService  # noqa: E402
from backend.app.services.attestation_gateway import AttestationGatewayService  # noqa: E402
from backend.app.services.attestation_urls import (  # noqa: E402
    describe_auth_error,
    has_valid_api_key,
    is_auth_error_payload,
    sanitize_provider_urls,
)
from backend.app.services.device_profile import DeviceProfileManager  # noqa: E402
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.reghelp import RegHelpService  # noqa: E402
from backend.app.services.vaksms import VakSmsService  # noqa: E402


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class TestAttestationUrlIsolation(unittest.TestCase):
    def test_sanitize_strips_cross_provider_hosts(self):
        mixed = [
            "https://api.antisafety.net",
            "https://api.reghelp.net",
            "https://reporting.antisafety.net/",
        ]
        self.assertEqual(
            sanitize_provider_urls(mixed, "antisafety"),
            ["https://api.antisafety.net"],
        )
        self.assertEqual(
            sanitize_provider_urls(mixed, "reghelp"),
            ["https://api.reghelp.net"],
        )
        self.assertEqual(
            sanitize_provider_urls(mixed, "antisafety_reporting"),
            ["https://reporting.antisafety.net"],
        )

    def test_sanitize_falls_back_to_defaults_when_all_contaminated(self):
        self.assertEqual(
            sanitize_provider_urls(["https://api.reghelp.net"], "antisafety"),
            ["https://api.antisafety.net"],
        )
        self.assertEqual(
            sanitize_provider_urls(["https://api.antisafety.net"], "reghelp"),
            ["https://api.reghelp.net"],
        )

    def test_config_model_strips_mixed_urls(self):
        cfg = AppConfigModel(
            antisafety_base_urls=["https://api.antisafety.net", "https://api.reghelp.net"],
            reghelp_base_urls=["https://api.reghelp.net", "https://api.antisafety.net"],
        )
        self.assertEqual(cfg.antisafety_base_urls, ["https://api.antisafety.net"])
        self.assertEqual(cfg.reghelp_base_urls, ["https://api.reghelp.net"])
        self.assertNotIn("reghelp.net", "".join(cfg.antisafety_base_urls))
        self.assertNotIn("antisafety.net", "".join(cfg.reghelp_base_urls))

    def test_service_constructors_drop_foreign_hosts(self):
        anti = AntiSafetyService(
            "as-key-12345678",
            api_bases=["https://api.antisafety.net", "https://api.reghelp.net"],
        )
        rh = RegHelpService(
            "rh-key-12345678",
            api_bases=["https://api.reghelp.net", "https://api.antisafety.net"],
        )
        self.assertEqual(anti.api_bases, ["https://api.antisafety.net"])
        self.assertEqual(rh.api_bases, ["https://api.reghelp.net"])
        asyncio.run(anti.close())
        asyncio.run(rh.close())

    def test_auth_error_payload_detection(self):
        self.assertTrue(is_auth_error_payload({"detail": "Invalid API key"}))
        self.assertTrue(is_auth_error_payload({"message": "unauthorized"}, 200))
        self.assertTrue(is_auth_error_payload({}, 401))
        self.assertFalse(is_auth_error_payload({"id": "task-1", "status": "success"}))
        self.assertIn("antisafety_api_key", describe_auth_error("antisafety", {"detail": "Invalid API key"}))

    def test_has_valid_api_key(self):
        self.assertTrue(has_valid_api_key("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR"))
        self.assertFalse(has_valid_api_key(""))
        self.assertFalse(has_valid_api_key("none"))
        self.assertFalse(has_valid_api_key("short"))

    def test_raw_urls_contamination_detector(self):
        self.assertTrue(raw_urls_are_contaminated({
            "antisafety_base_urls": ["https://api.antisafety.net", "https://api.reghelp.net"],
        }))
        self.assertTrue(raw_urls_are_contaminated({
            "reghelp_base_urls": ["https://api.reghelp.net", "https://api.antisafety.net"],
        }))
        self.assertFalse(raw_urls_are_contaminated({
            "antisafety_base_urls": ["https://api.antisafety.net"],
            "reghelp_base_urls": ["https://api.reghelp.net"],
        }))

    def test_load_config_rewrites_dirty_disk(self):
        import json
        import tempfile
        import backend.app.config as cfg_mod

        original_file = cfg_mod.CONFIG_FILE
        original_instance = cfg_mod.ConfigManager._instance
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({
                "antisafety_base_urls": ["https://api.antisafety.net", "https://api.reghelp.net"],
                "reghelp_base_urls": ["https://api.reghelp.net", "https://api.antisafety.net"],
                "reghelp_api_key": "w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR",
                "attestation_provider_mode": "reghelp_primary",
            }), encoding="utf-8")
            cfg_mod.CONFIG_FILE = path
            cfg_mod.ConfigManager._instance = None
            try:
                mgr = cfg_mod.ConfigManager()
                self.assertEqual(mgr.config.antisafety_base_urls, ["https://api.antisafety.net"])
                self.assertEqual(mgr.config.reghelp_base_urls, ["https://api.reghelp.net"])
                disk = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(disk["antisafety_base_urls"], ["https://api.antisafety.net"])
                self.assertEqual(disk["reghelp_base_urls"], ["https://api.reghelp.net"])
                self.assertNotIn("reghelp.net", "".join(disk["antisafety_base_urls"]))
                self.assertNotIn("antisafety.net", "".join(disk["reghelp_base_urls"]))
            finally:
                cfg_mod.CONFIG_FILE = original_file
                cfg_mod.ConfigManager._instance = original_instance


class TestAttestationGatewayPreference(unittest.TestCase):
    def _config(self, **overrides):
        data = dict(
            reghelp_enabled=True,
            reghelp_api_key="w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR",
            reghelp_base_urls=["https://api.reghelp.net", "https://api.antisafety.net"],
            reghelp_connect_timeout=1.0,
            reghelp_total_timeout=2.0,
            antisafety_enabled=True,
            antisafety_api_key="as2b21dc7b71b5ce8166a42c22b54566",
            antisafety_base_urls=["https://api.antisafety.net", "https://api.reghelp.net"],
            antisafety_reporting_base_urls=["https://reporting.antisafety.net"],
            antisafety_connect_timeout=1.0,
            antisafety_total_timeout=2.0,
            attestation_provider_mode="reghelp_primary",
        )
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_reghelp_primary_uses_isolated_reghelp_first(self):
        gw = AttestationGatewayService(self._config())
        try:
            self.assertIsNotNone(gw.reghelp)
            self.assertIsNotNone(gw.antisafety)
            self.assertEqual(gw.reghelp.api_bases, ["https://api.reghelp.net"])
            self.assertEqual(gw.antisafety.api_bases, ["https://api.antisafety.net"])
            order = gw._provider_order()
            self.assertEqual([name for name, _ in order], ["reghelp", "antisafety"])
        finally:
            asyncio.run(gw.close())

    def test_missing_reghelp_key_skips_primary(self):
        gw = AttestationGatewayService(self._config(reghelp_api_key=""))
        try:
            self.assertIsNone(gw.reghelp)
            order = gw._provider_order()
            self.assertEqual([name for name, _ in order], ["antisafety"])
        finally:
            asyncio.run(gw.close())


class TestAntiSafetyAuthErrorFallback(unittest.TestCase):
    def test_invalid_api_key_is_not_treated_as_success(self):
        svc = AntiSafetyService("as-key-12345678", api_bases=["https://api.antisafety.net"])
        svc.client.get = AsyncMock(return_value=DummyResponse({"detail": "Invalid API key"}))
        try:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(svc._get_with_fallback(svc.api_bases, "/push/getToken", {"apiKey": "x"}))
            self.assertIn("Invalid API key", str(ctx.exception))
        finally:
            asyncio.run(svc.close())


class TestVakSmsCancelRefund(unittest.TestCase):
    def test_cancel_sends_status_bad_and_reports_success(self):
        svc = VakSmsService("vak-key")
        svc.client.get = AsyncMock(return_value=DummyResponse({"status": "ok"}))
        try:
            result = asyncio.run(svc.cancel("27420200"))
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "bad")
            self.assertEqual(result["act_id"], "27420200")
            args, kwargs = svc.client.get.await_args
            self.assertTrue(str(args[0]).endswith("/setStatus/"))
            self.assertEqual(kwargs["params"]["status"], "bad")
            self.assertEqual(kwargs["params"]["idNum"], "27420200")
        finally:
            svc.client.aclose = AsyncMock()
            asyncio.run(svc.close())

    def test_cancel_skips_missing_act_id(self):
        svc = VakSmsService("vak-key")
        try:
            result = asyncio.run(svc.cancel(""))
            self.assertTrue(result["skipped"])
            self.assertFalse(result["success"])
        finally:
            svc.client.aclose = AsyncMock()
            asyncio.run(svc.close())


class TestRegistrarAutoRefundLog(unittest.TestCase):
    def test_refund_helper_prints_completion_marker(self):
        class FakeSms:
            def __init__(self):
                self.called = []

            async def cancel(self, act_id):
                self.called.append(act_id)
                return {"success": True, "act_id": act_id, "status": "bad"}

        class FakeManager:
            def __init__(self):
                self.logs = []

            async def append_log(self, task_id, message):
                self.logs.append(message)

        sms = FakeSms()
        manager = FakeManager()
        asyncio.run(
            RegistrationOrchestrator._refund_and_revoke_channel(
                sms, "27420200", "task1", manager, "API_ID_PUBLISHED_FLOOD"
            )
        )
        self.assertEqual(sms.called, ["27420200"])
        self.assertTrue(manager.logs)
        self.assertIn("[自动退订/撤销信道句柄完成]", manager.logs[0])
        self.assertIn("status=bad", manager.logs[0])
        self.assertIn("API_ID_PUBLISHED_FLOOD", manager.logs[0])

    def test_refund_helper_skips_when_no_act_id(self):
        class FakeSms:
            async def cancel(self, act_id):
                raise AssertionError("cancel should not be called")

        class FakeManager:
            async def append_log(self, task_id, message):
                raise AssertionError("log should not be written")

        asyncio.run(
            RegistrationOrchestrator._refund_and_revoke_channel(
                FakeSms(), None, "task1", FakeManager(), "EXCEPTION"
            )
        )


class TestPublishedCustomCredentials(unittest.TestCase):
    def test_custom_mode_published_id_still_risky(self):
        profile = {"api_id": 6, "api_hash": "official"}
        config = SimpleNamespace(
            api_credential_mode="custom",
            custom_api_id=4,
            custom_api_hash="014b35b6184100b085b0d0572f9b5103",
        )
        resolved = DeviceProfileManager.resolve_effective_credentials(profile, config, has_push_token=False)
        self.assertEqual(resolved["api_id"], 4)
        self.assertTrue(resolved["is_published_api_id"])
        self.assertEqual(resolved["credential_risk"], "published_id_without_push_token")

    def test_auto_mode_ignores_published_custom_fallback(self):
        profile = {"api_id": 6, "api_hash": "official"}
        config = SimpleNamespace(
            api_credential_mode="auto",
            custom_api_id=4,
            custom_api_hash="014b35b6184100b085b0d0572f9b5103",
        )
        resolved = DeviceProfileManager.resolve_effective_credentials(profile, config, has_push_token=False)
        self.assertEqual(resolved["credential_risk"], "published_id_without_push_token")
        self.assertTrue(resolved["is_published_api_id"])


class TestVaultGuidance(unittest.TestCase):
    def test_guidance_mentions_session_and_custom_fields(self):
        self.assertIn(".session", VAULT_GUIDANCE)
        self.assertIn("custom_api_id", VAULT_GUIDANCE)
        self.assertIn("凭证库", VAULT_GUIDANCE)
        self.assertIn("777000", VAULT_GUIDANCE)
        self.assertIn("轨 A", VAULT_GUIDANCE)

    def test_hint_for_json_only_published_account(self):
        hint = build_apps_apply_hint({
            "phone": "+918296691905",
            "filename": "918296691905.json",
            "has_session": False,
            "is_published_api_id": True,
            "app_id": 4,
        })
        self.assertIn("api_id=4", hint)
        self.assertIn("918296691905.session", hint)
        self.assertIn("手动提交", hint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
