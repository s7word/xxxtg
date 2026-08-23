"""RECAPTCHA_CHECK 解析、REGHelp RecaptchaMobile 解题与 SendCode 重试测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.attestation_gateway import AttestationGatewayService  # noqa: E402
from backend.app.services.recaptcha_check import (  # noqa: E402
    RecaptchaChallengeError,
    parse_recaptcha_check,
    recaptcha_app_device,
    recaptcha_app_name,
)
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.reghelp import RegHelpService  # noqa: E402


USER_LOG_ERROR = (
    "RPCError 403: RECAPTCHA_CHECK_signup__6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt "
    "(caused by SendCodeRequest)"
)


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class TestParseRecaptchaCheck(unittest.TestCase):
    def test_parse_user_log_error(self):
        self.assertEqual(
            parse_recaptcha_check(USER_LOG_ERROR),
            ("signup", "6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"),
        )

    def test_parse_rpcerror_message_attr(self):
        err = SimpleNamespace(
            message="RECAPTCHA_CHECK_signup__6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"
        )
        self.assertEqual(
            parse_recaptcha_check(err),
            ("signup", "6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"),
        )

    def test_parse_unrelated_error(self):
        self.assertIsNone(parse_recaptcha_check("PHONE_NUMBER_BANNED"))
        self.assertIsNone(parse_recaptcha_check(None))

    def test_app_name_android_default(self):
        self.assertEqual(recaptcha_app_name({"app_device": "Android"}), "org.telegram.messenger")
        self.assertEqual(recaptcha_app_name({"app_device": "iOS"}), "ph.telegra.Telegraph")
        self.assertEqual(recaptcha_app_device({"app_device": "android"}), "Android")


class TestReghelpRecaptchaMobile(unittest.TestCase):
    def test_create_and_poll_token(self):
        svc = RegHelpService(
            "w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR",
            api_bases=["https://api.reghelp.net", "https://api.antisafety.net"],
        )
        self.assertEqual(svc.api_bases, ["https://api.reghelp.net"])
        payloads = [
            DummyResponse({"id": "rc-1", "status": "success", "price": 0.2, "balance": 12}),
            DummyResponse({"status": "processing"}),
            DummyResponse({"status": "done", "token": "recaptcha-token-xyz"}),
        ]

        async def fake_get(url, params=None, headers=None):
            self.assertTrue(str(url).startswith("https://api.reghelp.net/RecaptchaMobile/"))
            self.assertEqual(params.get("apiKey"), "w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
            if str(url).endswith("/getToken"):
                self.assertEqual(params["appName"], "org.telegram.messenger")
                self.assertEqual(params["appKey"], "6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt")
                self.assertEqual(params["appAction"], "signup")
                self.assertEqual(params["appDevice"], "Android")
            return payloads.pop(0)

        svc.client.get = fake_get
        try:
            with patch("backend.app.services.reghelp.asyncio.sleep", new=AsyncMock()):
                token = asyncio.run(
                    svc.get_recaptcha_mobile_token(
                        app_key="6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt",
                        app_action="signup",
                    )
                )
            self.assertEqual(token, "recaptcha-token-xyz")
            self.assertTrue(callable(svc.RecaptchaMobile))
        finally:
            asyncio.run(svc.close())

    def test_auth_error_on_create(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.client.get = AsyncMock(return_value=DummyResponse({"detail": "Invalid API key"}))
        try:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(svc.get_recaptcha_mobile_token(app_key="6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"))
            self.assertIn("reghelp", str(ctx.exception).lower())
        finally:
            asyncio.run(svc.close())


class TestSendCodeRecaptchaRetry(unittest.TestCase):
    def test_retries_with_invoke_after_solve(self):
        class FakeRPC(Exception):
            def __init__(self):
                super().__init__(USER_LOG_ERROR)
                self.message = "RECAPTCHA_CHECK_signup__6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def __call__(self, req):
                self.calls.append(req)
                if req.__class__.__name__ == "SendCodeRequest":
                    raise FakeRPC()
                return SimpleNamespace(phone_code_hash="hash123")

        class FakeGateway:
            def __init__(self):
                self.kwargs = None

            async def get_recaptcha_mobile_token(self, **kwargs):
                self.kwargs = kwargs
                return "recaptcha-token-xyz"

        class FakeManager:
            def __init__(self):
                self.logs = []

            async def append_log(self, task_id, message):
                self.logs.append(message)

        client = FakeClient()
        gw = FakeGateway()
        manager = FakeManager()
        sent = asyncio.run(
            RegistrationOrchestrator._send_code_with_recaptcha(
                client,
                "+56971948355",
                {"api_id": 6, "api_hash": "h", "app_device": "Android"},
                SimpleNamespace(),
                gw,
                {"proxy_type": "socks5", "addr": "127.0.0.1", "port": 7897},
                "t1",
                manager,
            )
        )
        self.assertEqual(sent.phone_code_hash, "hash123")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1].token, "recaptcha-token-xyz")
        self.assertEqual(gw.kwargs["site_key"], "6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt")
        self.assertEqual(gw.kwargs["action"], "signup")
        self.assertTrue(any("RECAPTCHA_CHECK" in log for log in manager.logs))

    def test_unsolved_raises_challenge_error(self):
        class FakeRPC(Exception):
            def __init__(self):
                super().__init__(USER_LOG_ERROR)
                self.message = "RECAPTCHA_CHECK_signup__6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt"

        class FakeClient:
            async def __call__(self, req):
                raise FakeRPC()

        class FakeGateway:
            async def get_recaptcha_mobile_token(self, **kwargs):
                raise RuntimeError("Invalid API key")

        class FakeManager:
            async def append_log(self, task_id, message):
                return None

        with self.assertRaises(RecaptchaChallengeError) as ctx:
            asyncio.run(
                RegistrationOrchestrator._send_code_with_recaptcha(
                    FakeClient(),
                    "+56971948355",
                    {"api_id": 6, "api_hash": "h"},
                    SimpleNamespace(),
                    FakeGateway(),
                    None,
                    "t1",
                    FakeManager(),
                )
            )
        self.assertEqual(ctx.exception.action, "signup")
        self.assertIn("解题失败", str(ctx.exception))


class TestGatewayRecaptchaUsesReghelpOnly(unittest.TestCase):
    def test_missing_reghelp_raises(self):
        gw = AttestationGatewayService(SimpleNamespace(
            reghelp_enabled=False,
            reghelp_api_key="",
            antisafety_enabled=True,
            antisafety_api_key="as2b21dc7b71b5ce8166a42c22b54566",
            antisafety_base_urls=["https://api.antisafety.net", "https://api.reghelp.net"],
            attestation_provider_mode="reghelp_primary",
        ))
        try:
            self.assertIsNone(gw.reghelp)
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(gw.get_recaptcha_mobile_token("6LdcRsEqAAAAAHUaNCc1GUe47g5jKlOzbJJiyIZt", "signup"))
            self.assertIn("reghelp_api_key", str(ctx.exception))
        finally:
            asyncio.run(gw.close())


if __name__ == "__main__":
    unittest.main(verbosity=2)
