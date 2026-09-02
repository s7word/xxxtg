"""官方客户端模拟：sent_code 新分支、Email / Integrity / Payment 快退。"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.code_delivery import (  # noqa: E402
    CODE_DELIVERY_PUSH_REQUIRED,
    resolve_code_delivery_plan,
)
from backend.app.services.device_profile import DeviceProfileManager  # noqa: E402
from backend.app.services.reghelp import (  # noqa: E402
    EmailInboxResult,
    PUSH_REFUND_REASON_MAP,
    RegHelpService,
)
from backend.app.services.registrar import (  # noqa: E402
    DEFAULT_SMS_POLL_ATTEMPTS,
    RegistrationOrchestrator,
    RegistrationTaskManager,
    SentCodeAppDeliveryError,
)
from backend.tests.test_reghelp_push_refund import DummyResponse  # noqa: E402
from backend.tests.test_sentcode_app_batch import FakeClient, make_sent_code  # noqa: E402


def _android_profile(**kwargs):
    base = {
        "api_id": 6,
        "api_hash": "official-hash",
        "app_name": "tg",
        "app_device": "Android",
        "app_build": "69792",
        "app_version": "12.9.1 (69792)",
        "app_version_pure": "12.9.1",
        "lang_pack": "android",
    }
    base.update(kwargs)
    return base


def make_payment_required():
    return type("SentCodePaymentRequired", (), {
        "store_product": "org.telegram.messenger.premium",
        "phone_code_hash": "hash-pay",
        "support_email_address": "sms@telegram.org",
        "support_email_subject": "Payment",
        "premium_days": 30,
        "currency": "USD",
        "amount": 499,
        "type": None,
        "next_type": None,
        "timeout": None,
    })()


def make_firebase(nonce=b"play-nonce-bytes"):
    code_type = type("SentCodeTypeFirebaseSms", (), {
        "length": 5,
        "play_integrity_nonce": nonce,
        "nonce": None,
        "play_integrity_project_id": 760348033671,
    })()
    return SimpleNamespace(
        type=code_type,
        next_type=None,
        timeout=None,
        phone_code_hash="hash-fb",
    )


class SequenceClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def __call__(self, req):
        self.calls.append(req)
        return self.handler(req)


class TestOfficialEmulationConfig(unittest.TestCase):
    def test_schema_default_and_coercion(self):
        cfg = AppConfigModel()
        self.assertFalse(cfg.official_client_emulation)
        self.assertTrue(AppConfigModel(official_client_emulation="true").official_client_emulation)
        self.assertFalse(AppConfigModel(official_client_emulation="off").official_client_emulation)
        self.assertFalse(AppConfigModel().force_skip_push_attach)
        self.assertTrue(AppConfigModel(force_skip_push_attach="true").force_skip_push_attach)
        self.assertTrue(cfg.code_settings_allow_firebase)
        self.assertTrue(cfg.code_settings_unknown_number)
        self.assertTrue(cfg.force_resend_on_app)
        self.assertEqual(cfg.payment_required_probe, "off")
        self.assertEqual(AppConfigModel(payment_required_probe="BOTH").payment_required_probe, "both")
        self.assertEqual(AppConfigModel(pin_app_version_substr=" 12.7.3 ").pin_app_version_substr, "12.7.3")

    def test_credentials_forced_to_official_template(self):
        profile = _android_profile()
        config = SimpleNamespace(
            official_client_emulation=True,
            api_credential_mode="custom",
            custom_api_id=35337905,
            custom_api_hash="custom-hash",
        )
        resolved = DeviceProfileManager.resolve_effective_credentials(
            profile, config, has_push_token=True
        )
        self.assertEqual(resolved["api_id"], 6)
        self.assertEqual(resolved["credential_source"], "official")

    def test_plan_uses_official_label(self):
        plan = resolve_code_delivery_plan(
            SimpleNamespace(
                official_client_emulation=True,
                code_delivery_mode="balanced",
                api_credential_mode="custom",
                custom_api_id=35337905,
                custom_api_hash="x",
                hunt_sms_first_after_app_streak=2,
            ),
            _android_profile(),
        )
        self.assertEqual(plan.emulation_label, "official")
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)


class TestSentCodeTypeHelpers(unittest.TestCase):
    def test_payment_required_is_not_app(self):
        sent = make_payment_required()
        self.assertEqual(RegistrationOrchestrator._sent_code_type_name(sent), "SentCodePaymentRequired")
        self.assertTrue(RegistrationOrchestrator._is_payment_required(sent))
        self.assertFalse(RegistrationOrchestrator._is_app_delivery(sent))
        self.assertFalse(RegistrationOrchestrator._is_sms_delivery(sent))
        self.assertIn("SentCodePaymentRequired", RegistrationOrchestrator._describe_sent_code(sent))

    def test_email_setup_and_email_code(self):
        setup = make_sent_code("SentCodeTypeSetUpEmailRequired")
        email = make_sent_code("SentCodeTypeEmailCode")
        email.type.email_pattern = "a***@gmail.com"
        self.assertTrue(RegistrationOrchestrator._is_email_setup(setup))
        self.assertTrue(RegistrationOrchestrator._is_email_code(email))
        self.assertFalse(RegistrationOrchestrator._is_app_delivery(setup))
        self.assertIn("email_pattern=", RegistrationOrchestrator._describe_sent_code(email))

    def test_firebase_nonce_encoding(self):
        nonce = RegistrationOrchestrator._play_integrity_nonce(
            SimpleNamespace(play_integrity_nonce=b"abc", nonce=None)
        )
        self.assertEqual(nonce, base64.urlsafe_b64encode(b"abc").decode("ascii"))
        self.assertEqual(RegistrationOrchestrator._app_version_code(_android_profile()), 69792)

    def test_refund_map_covers_new_reasons(self):
        for reason in (
            "PAYMENT_REQUIRED_OFFICIAL_ONLY",
            "EMAIL_SETUP_FAILED",
            "EMAIL_CODE_UNAVAILABLE",
            "FIREBASE_SMS_FAILED",
            "PUSH_TOKEN_MISSING",
        ):
            self.assertEqual(PUSH_REFUND_REASON_MAP[reason], "NOSMS")


class TestResolveNewSentCodeTypes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self.task_id = self.manager.create_task()

    def _logs(self):
        return "\n".join(self.manager.get_task(self.task_id)["logs"])

    async def test_payment_required_fails_fast(self):
        client = FakeClient()
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                client, "+56911112222", make_payment_required(),
                self.task_id, self.manager, emulation_label="official",
            )
        self.assertEqual(ctx.exception.reason, "PAYMENT_REQUIRED_OFFICIAL_ONLY")
        self.assertEqual(client.calls, [])
        logs = self._logs()
        self.assertIn("SentCodePaymentRequired", logs)
        self.assertIn("[模式=official]", logs)
        self.assertIn("需官方 App 内购", logs)

    async def test_payment_play_market_probe_records_rpc_error(self):
        client = FakeClient(error=RuntimeError("PURCHASE_RECEIPT_INVALID"))
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                client, "+96411112222", make_payment_required(),
                self.task_id, self.manager, emulation_label="official",
                payment_required_probe="play_market",
            )
        self.assertEqual(ctx.exception.reason, "PAYMENT_REQUIRED_OFFICIAL_ONLY")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(type(client.calls[0]).__name__, "AssignPlayMarketTransactionRequest")
        logs = self._logs()
        self.assertIn("[PAYMENT_PROBE]", logs)
        self.assertIn("PURCHASE_RECEIPT_INVALID", logs)

    async def test_payment_resend_probe_can_flip_to_sms(self):
        sms_sent = make_sent_code("SentCodeTypeSms", code_hash="hash-after-pay")
        client = FakeClient(result=sms_sent)
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            client, "+96411112222", make_payment_required(),
            self.task_id, self.manager, emulation_label="official",
            payment_required_probe="resend",
        )
        self.assertEqual(result.phone_code_hash, "hash-after-pay")
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        logs = self._logs()
        self.assertIn("[PAYMENT_PROBE] PaymentRequired 后尝试 auth.resendCode", logs)

    async def test_email_code_fails_fast(self):
        sent = make_sent_code("SentCodeTypeEmailCode")
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                FakeClient(), "+56911112222", sent, self.task_id, self.manager,
                emulation_label="official",
            )
        self.assertEqual(ctx.exception.reason, "EMAIL_CODE_UNAVAILABLE")

    async def test_email_setup_without_gateway_fails(self):
        sent = make_sent_code("SentCodeTypeSetUpEmailRequired")
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                FakeClient(), "+56911112222", sent, self.task_id, self.manager,
                emulation_label="official",
            )
        self.assertEqual(ctx.exception.reason, "EMAIL_SETUP_FAILED")
        self.assertIn("SetUpEmailRequired", self._logs())

    async def test_email_setup_then_sms(self):
        sms_sent = make_sent_code("SentCodeTypeSms", code_hash="hash-after-email")
        inbox = EmailInboxResult(email="tmp@gmail.com", task_id="em-1", code="654321", email_type="gmail")
        bypass = MagicMock()
        bypass.get_login_email = AsyncMock(return_value=inbox)
        bypass.poll_email_code = AsyncMock(return_value="654321")

        def handler(req):
            name = type(req).__name__
            if name == "SendVerifyEmailCodeRequest":
                return SimpleNamespace(email_pattern="t***@gmail.com", length=6)
            if name == "VerifyEmailRequest":
                return SimpleNamespace(email="tmp@gmail.com", sent_code=sms_sent)
            raise AssertionError(f"unexpected {name}")

        client = SequenceClient(handler)
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            client, "+56911112222", make_sent_code("SentCodeTypeSetUpEmailRequired"),
            self.task_id, self.manager,
            bypass_svc=bypass,
            profile=_android_profile(),
            emulation_label="official",
        )
        self.assertIs(result, sms_sent)
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        self.assertEqual([type(c).__name__ for c in client.calls], [
            "SendVerifyEmailCodeRequest",
            "VerifyEmailRequest",
        ])
        logs = self._logs()
        self.assertIn("SetUpEmailRequired", logs)
        self.assertIn("SentCodeTypeSms", logs)
        self.assertIn("[模式=official]", logs)
        bypass.get_login_email.assert_awaited()

    async def test_call_type_is_not_app_fail_fast(self):
        sent = make_sent_code("SentCodeTypeCall")
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            FakeClient(), "+56911112222", sent, self.task_id, self.manager,
            emulation_label="official",
        )
        self.assertIs(result, sent)
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        self.assertIn("不按站内信快退", self._logs())

    async def test_firebase_without_gateway_still_sms(self):
        sent = make_firebase()
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            FakeClient(), "+56911112222", sent, self.task_id, self.manager,
            profile=_android_profile(),
            emulation_label="official",
        )
        self.assertIs(result, sent)
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        self.assertIn("SentCodeTypeFirebaseSms", self._logs())
        self.assertIn("跳过 requestFirebaseSms", self._logs())

    async def test_firebase_requests_integrity_token(self):
        sent = make_firebase()
        bypass = MagicMock()
        bypass.get_integrity_token = AsyncMock(return_value="integrity-token")
        seen = {}

        def handler(req):
            seen["name"] = type(req).__name__
            seen["token"] = getattr(req, "play_integrity_token", None)
            return True

        client = SequenceClient(handler)
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            client, "+56911112222", sent, self.task_id, self.manager,
            bypass_svc=bypass,
            profile=_android_profile(),
            emulation_label="official",
        )
        self.assertIs(result, sent)
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        self.assertEqual(seen["name"], "RequestFirebaseSmsRequest")
        self.assertEqual(seen["token"], "integrity-token")
        kwargs = bypass.get_integrity_token.await_args.kwargs
        self.assertEqual(kwargs["app_version_code"], 69792)
        self.assertIn("requestFirebaseSms", self._logs())


class TestRegHelpEmailApi(unittest.IsolatedAsyncioTestCase):
    async def test_get_login_email_returns_inbox(self):
        svc = RegHelpService("test-key")
        payloads = [
            DummyResponse({
                "id": "em-1",
                "status": "success",
                "email": "tmp@icloud.com",
                "service": "tg",
                "product": "email",
                "price": 0.4,
                "balance": 10,
            }),
        ]

        async def fake_get(url, params=None, headers=None):
            if str(url).endswith("/email/getEmail"):
                self.assertEqual(params["type"], "gmail")
                self.assertEqual(params["phone"], "+56911112222")
                return payloads[0]
            raise AssertionError(url)

        svc.client.get = fake_get
        try:
            inbox = await svc.get_login_email(
                {"app_name": "tg", "app_device": "Android"},
                "+56911112222",
                email_type="gmail",
            )
            self.assertEqual(inbox.email, "tmp@icloud.com")
            self.assertEqual(inbox.task_id, "em-1")
        finally:
            await svc.close()

    async def test_poll_email_code(self):
        svc = RegHelpService("test-key")
        svc._last_good_api_base = "https://api.reghelp.net"
        payloads = [
            DummyResponse({"id": "em-1", "status": "wait"}),
            DummyResponse({"id": "em-1", "status": "done", "code": "778899"}),
        ]

        async def fake_get(url, params=None, headers=None):
            self.assertTrue(str(url).endswith("/email/getStatus"))
            return payloads.pop(0)

        svc.client.get = fake_get
        try:
            with patch("backend.app.services.reghelp.asyncio.sleep", new=AsyncMock()):
                code = await svc.poll_email_code("em-1")
            self.assertEqual(code, "778899")
        finally:
            await svc.close()


if __name__ == "__main__":
    unittest.main()
