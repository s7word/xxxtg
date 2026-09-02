"""SMS Bower 临时邮箱 API 与 AttestationGateway Email fallback 测试（全部 mock httpx）。"""
from __future__ import annotations

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

from backend.app.config import ConfigManager  # noqa: E402
from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.attestation_gateway import AttestationGatewayService  # noqa: E402
from backend.app.services.reghelp import EmailInboxResult  # noqa: E402
from backend.app.services.smsbower import (  # noqa: E402
    SmsBowerEmailError,
    SmsBowerService,
)


class DummyResponse:
    def __init__(self, payload, status_code=200):
        import json
        self._payload = payload
        self.text = json.dumps(payload)
        self.status_code = status_code

    def json(self):
        return self._payload


class TestSmsBowerEmailClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.svc = SmsBowerService("test-bower-key")
        self.svc.client.get = AsyncMock()

    async def asyncTearDown(self):
        await self.svc.close()

    async def test_get_login_email_success(self):
        self.svc.client.get.return_value = DummyResponse(
            {"status": 1, "mail": "tmp@gmail.com", "mailId": 42, "price": 0.12}
        )
        inbox = await self.svc.get_login_email(
            {"app_name": "tg", "app_device": "Android"},
            "+56911112222",
            email_type="gmail",
        )
        self.assertEqual(inbox.email, "tmp@gmail.com")
        self.assertEqual(inbox.task_id, "42")
        self.assertEqual(inbox.email_type, "gmail")
        args, kwargs = self.svc.client.get.await_args
        self.assertTrue(str(args[0]).endswith("/api/mail/getActivation"))
        self.assertEqual(kwargs["params"]["service"], "tg")
        self.assertEqual(kwargs["params"]["domain"], "gmail.com")

    async def test_get_login_email_no_stock(self):
        self.svc.client.get.return_value = DummyResponse(
            {"status": 0, "error": "No mails yet"}
        )
        with self.assertRaises(SmsBowerEmailError) as ctx:
            await self.svc.get_login_email({}, "+1")
        self.assertIn("No mails yet", str(ctx.exception))

    async def test_poll_email_code_success_confirms(self):
        self.svc.client.get = AsyncMock(
            side_effect=[
                DummyResponse({"status": 0, "error": "Code has not been received yet, please try again later"}),
                DummyResponse({"status": 1, "code": "123456"}),
                DummyResponse({"status": 1, "message": "Success"}),
            ]
        )
        with patch.object(SmsBowerService, "confirm_email_activation", AsyncMock(return_value=True)) as confirm:
            code = await self.svc.poll_email_code("42", max_attempts=3, interval_sec=0)
        self.assertEqual(code, "123456")
        confirm.assert_awaited_once_with("42")

    async def test_poll_email_code_canceled(self):
        self.svc.client.get.return_value = DummyResponse(
            {"status": 0, "error": "Activation is already canceled"}
        )
        with self.assertRaises(SmsBowerEmailError):
            await self.svc.poll_email_code("42", max_attempts=1, interval_sec=0)


class TestConfigEmailMigration(unittest.TestCase):
    def test_migrate_reghelp_primary_to_smsbower_only(self):
        raw = {"email_provider_mode": "reghelp_primary", "email_smsbower_fallback_enabled": True}
        cfg = AppConfigModel(**raw)
        changed = ConfigManager._migrate_legacy_config(raw, cfg)
        self.assertTrue(changed)
        self.assertEqual(cfg.email_provider_mode, "smsbower_only")
        self.assertFalse(cfg.email_smsbower_fallback_enabled)


class TestAttestationEmailFallback(unittest.IsolatedAsyncioTestCase):
    def _config(self, **overrides):
        base = dict(
            reghelp_api_key="reghelp-key",
            reghelp_enabled=True,
            reghelp_base_urls=["https://api.reghelp.net"],
            smsbower_api_key="bower-key",
            email_provider_mode="smsbower_only",
            email_smsbower_fallback_enabled=False,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_schema_default_smsbower_only(self):
        cfg = AppConfigModel()
        self.assertEqual(cfg.email_provider_mode, "smsbower_only")
        self.assertFalse(cfg.email_smsbower_fallback_enabled)

    async def test_default_mode_skips_reghelp(self):
        cfg = self._config()
        gateway = AttestationGatewayService(cfg)
        try:
            gateway.reghelp.get_login_email = AsyncMock(
                side_effect=AssertionError("reghelp should not be called")
            )
            gateway.smsbower.get_login_email = AsyncMock(
                return_value=EmailInboxResult(email="default@gmail.com", task_id="1")
            )
            inbox = await gateway.get_login_email({}, "+1")
            self.assertEqual(inbox.email, "default@gmail.com")
            gateway.smsbower.get_login_email.assert_awaited_once()
        finally:
            await gateway.close()

    async def test_reghelp_service_disabled_falls_back_to_smsbower(self):
        cfg = self._config(email_provider_mode="reghelp_primary", email_smsbower_fallback_enabled=True)
        gateway = AttestationGatewayService(cfg)
        try:
            gateway.reghelp.get_login_email = AsyncMock(
                side_effect=RuntimeError("REGHelp Email 任务创建失败 (SERVICE_DISABLED)")
            )
            expected = EmailInboxResult(email="bower@gmail.com", task_id="99", email_type="gmail")
            gateway.smsbower.get_login_email = AsyncMock(return_value=expected)

            inbox = await gateway.get_login_email(
                {"app_name": "tg", "app_device": "Android"},
                "+56911112222",
                email_type="gmail",
            )
            self.assertEqual(inbox.email, "bower@gmail.com")
            self.assertEqual(gateway.last_used_email_provider, "smsbower")
            gateway.reghelp.get_login_email.assert_awaited_once()
            gateway.smsbower.get_login_email.assert_awaited_once()
        finally:
            await gateway.close()

    async def test_poll_routes_to_last_email_provider(self):
        cfg = self._config()
        gateway = AttestationGatewayService(cfg)
        try:
            gateway.last_used_email_provider = "smsbower"
            gateway.smsbower.poll_email_code = AsyncMock(return_value="654321")
            gateway.reghelp.poll_email_code = AsyncMock(return_value="000000")

            code = await gateway.poll_email_code("99")
            self.assertEqual(code, "654321")
            gateway.smsbower.poll_email_code.assert_awaited_once()
            gateway.reghelp.poll_email_code.assert_not_called()
        finally:
            await gateway.close()

    async def test_smsbower_only_skips_reghelp(self):
        cfg = self._config(email_provider_mode="smsbower_only")
        gateway = AttestationGatewayService(cfg)
        try:
            gateway.reghelp.get_login_email = AsyncMock(
                side_effect=AssertionError("reghelp should not be called")
            )
            gateway.smsbower.get_login_email = AsyncMock(
                return_value=EmailInboxResult(email="only@gmail.com", task_id="1")
            )
            inbox = await gateway.get_login_email({}, "+1")
            self.assertEqual(inbox.email, "only@gmail.com")
            gateway.smsbower.get_login_email.assert_awaited_once()
        finally:
            await gateway.close()

    async def test_no_providers_raises(self):
        cfg = self._config(reghelp_api_key="", smsbower_api_key="")
        gateway = AttestationGatewayService(cfg)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                await gateway.get_login_email({}, "+1")
            self.assertIn("未启用任何 Email 提供源", str(ctx.exception))
        finally:
            await gateway.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
