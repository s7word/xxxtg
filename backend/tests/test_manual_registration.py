"""手动单号注册调试控制台：发码、提交验证码、新号 SignUp、旧号 SignIn 与取消。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import (  # noqa: E402
    ManualRegisterStartRequest,
    ManualRegisterStartResponse,
    ManualRegisterSubmitCodeRequest,
    ManualRegisterSubmitCodeResponse,
    ManualRegisterCancelRequest,
)
from backend.app.services.manual_registrar import (  # noqa: E402
    MANUAL_CODE_WAIT_SECONDS,
    ManualLiveSession,
    ManualRegisterError,
    ManualRegistrationOrchestrator,
    ManualSessionStore,
    normalize_manual_phone,
    resolve_manual_country,
    session_artifact_paths,
)
from backend.app.services.registrar import RegistrationTaskManager  # noqa: E402
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError  # noqa: E402


class InvalidCode(PhoneCodeInvalidError):
    def __init__(self, message="PHONE_CODE_INVALID"):
        Exception.__init__(self, message)


class NeedPassword(SessionPasswordNeededError):
    def __init__(self, message="SESSION_PASSWORD_NEEDED"):
        Exception.__init__(self, message)


class FakeBypass:
    def __init__(self):
        self.closed = False
        self.reported = []
        self.refunded = []

    async def get_push_token(self, *args, **kwargs):
        return "push-token", "push-task-1", "reghelp"

    async def check_phone_history(self, *args, **kwargs):
        return None

    async def report_result(self, check_id, aid, status):
        self.reported.append((check_id, aid, status))

    async def refund_push_token(self, task_id, phone, reason, log_callback=None):
        self.refunded.append((task_id, phone, reason))
        return None

    async def close(self):
        self.closed = True


class FakeTelegramClient:
    def __init__(self, sign_in_result=None, sign_in_error=None, sign_up_result=None):
        self.sign_in_result = sign_in_result
        self.sign_in_error = sign_in_error
        self.sign_up_result = sign_up_result or SimpleNamespace(
            user=SimpleNamespace(id=4242),
            terms_of_service=None,
        )
        self.calls = []
        self.connected = False
        self.disconnected = False
        self.edited_2fa = None
        self.signed_in_password = None

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False
        self.disconnected = True

    async def __call__(self, req):
        self.calls.append(req)
        name = type(req).__name__
        if name == "SignInRequest":
            if self.sign_in_error is not None:
                raise self.sign_in_error
            return self.sign_in_result
        if name == "SignUpRequest":
            return self.sign_up_result
        if name == "AcceptTermsOfServiceRequest":
            return True
        return SimpleNamespace()

    async def sign_in(self, password=None):
        self.signed_in_password = password
        return self.sign_in_result or SimpleNamespace(user=SimpleNamespace(id=9001))

    async def edit_2fa(self, **kwargs):
        self.edited_2fa = kwargs

    async def get_dialogs(self, limit=5):
        return []


SAMPLE_PROFILE = {
    "name": "MTProto Android",
    "aid": "aid-android",
    "api_id": 123456,
    "api_hash": "hash-custom",
    "device_model": "Pixel 8",
    "system_version": "SDK 34",
    "app_version": "11.2.0",
    "lang_code": "es",
    "system_lang_code": "es-cl",
    "lang_pack": "android",
    "tz_offset": -14400,
    "device_pack_alias": "Chile.db",
    "device_pack_country": "cl",
    "device_pack_match": "country",
    "credential_source": "custom",
}


def _sent_code(type_name="SentCodeTypeSms", code_hash="hash-manual"):
    return SimpleNamespace(
        type=type(type_name, (), {})(),
        next_type=None,
        timeout=None,
        phone_code_hash=code_hash,
    )


class TestPhoneAndCountryHelpers(unittest.TestCase):
    def test_normalize_plus_and_digits(self):
        self.assertEqual(normalize_manual_phone("+9647706110434"), "+9647706110434")
        self.assertEqual(normalize_manual_phone("628123456789"), "+628123456789")
        self.assertEqual(normalize_manual_phone("+62 812 3456 789"), "+628123456789")

    def test_normalize_rejects_short_or_empty(self):
        with self.assertRaises(ManualRegisterError):
            normalize_manual_phone("")
        with self.assertRaises(ManualRegisterError):
            normalize_manual_phone("12345")
        with self.assertRaises(ManualRegisterError):
            normalize_manual_phone("abc")

    def test_resolve_country_explicit_wins(self):
        self.assertEqual(resolve_manual_country("+9647706110434", "iq"), "iq")

    def test_resolve_country_infers_from_phone(self):
        self.assertEqual(resolve_manual_country("+9647706110434"), "iq")
        self.assertEqual(resolve_manual_country("628123456789"), "id")
        self.assertEqual(resolve_manual_country("+56971948355"), "cl")

    def test_resolve_country_fallback(self):
        self.assertEqual(resolve_manual_country("+99900001111", None, fallback="ca"), "ca")

    def test_session_artifact_paths(self):
        session_path, meta_path, filename = session_artifact_paths("+56911112222")
        self.assertTrue(str(session_path).endswith("56911112222.session"))
        self.assertTrue(str(meta_path).endswith("56911112222.json"))
        self.assertEqual(filename, "56911112222.session")


class TestManualSchemas(unittest.TestCase):
    def test_start_request_requires_phone(self):
        req = ManualRegisterStartRequest(phone="+9647706110434", country="iq")
        self.assertEqual(req.phone, "+9647706110434")
        self.assertEqual(req.proxy_mode, "custom_pool")
        with self.assertRaises(ValidationError):
            ManualRegisterStartRequest(phone="123")

    def test_submit_request_validates_code(self):
        req = ManualRegisterSubmitCodeRequest(task_id="abcd1234", code="12345")
        self.assertEqual(req.code, "12345")
        with self.assertRaises(ValidationError):
            ManualRegisterSubmitCodeRequest(task_id="abcd1234", code="1")

    def test_response_models(self):
        start = ManualRegisterStartResponse(
            task_id="abcd1234",
            status="waiting_code",
            phone="+56911112222",
            phone_code_hash="hh",
            delivery_type="SentCodeTypeSms",
            message="ok",
        )
        self.assertEqual(start.status, "waiting_code")
        submit = ManualRegisterSubmitCodeResponse(
            task_id="abcd1234",
            status="success",
            phone="+56911112222",
            user_id=42,
            message="ok",
            session_file="56911112222.session",
        )
        self.assertEqual(submit.user_id, 42)
        cancel = ManualRegisterCancelRequest(task_id="abcd1234")
        self.assertEqual(cancel.task_id, "abcd1234")


class ManualTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev_mgr = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.store = ManualSessionStore()
        self._prev_store = ManualSessionStore._instance
        ManualSessionStore._instance = self.store
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    async def asyncTearDown(self):
        for tid in list(self.store.task_ids()):
            sess = self.store.pop(tid)
            if sess and sess.expire_task and not sess.expire_task.done():
                sess.expire_task.cancel()
                try:
                    await sess.expire_task
                except (asyncio.CancelledError, Exception):
                    pass
        RegistrationTaskManager._instance = self._prev_mgr
        ManualSessionStore._instance = self._prev_store
        self.tmp.cleanup()

    def _config(self):
        return SimpleNamespace(
            target_country="cl",
            active_app_type="telegram_android",
            auto_set_2fa=True,
            default_2fa_password="Pw@2026",
            fallback_proxy=SimpleNamespace(
                model_dump=lambda: {
                    "proxy_type": "socks5",
                    "addr": "127.0.0.1",
                    "port": 10808,
                }
            ),
            use_proxy_seller_auto=False,
            custom_proxies=[],
            api_credential_mode="custom",
            custom_api_id=123456,
            custom_api_hash="hash-custom",
        )

    def _seed_waiting(self, client, phone="+56911112222"):
        tid = self.manager.create_task()
        session_path = self.tmp_path / f"{phone.lstrip('+')}.session"
        meta_path = self.tmp_path / f"{phone.lstrip('+')}.json"
        client.connected = True
        self.manager.update_task_status(
            tid, "waiting_code", mode="manual", phone=phone, phone_code_hash="hash-1"
        )
        live = ManualLiveSession(
            task_id=tid,
            client=client,
            phone=phone,
            phone_code_hash="hash-1",
            delivery_type="SentCodeTypeSms",
            profile=dict(SAMPLE_PROFILE),
            config=self._config(),
            set_2fa=True,
            bypass_svc=FakeBypass(),
            check_id=None,
            aid="aid-android",
            target_country="cl",
            session_path=session_path,
            meta_path=meta_path,
            session_filename=session_path.name,
        )
        self.store.put(live)
        return tid, live

    @contextmanager
    def _patch_start_dependencies(self, client, bypass=None, sent_code=None):
        """封装 start() 阶段 1 所需的全部外部依赖 mock，供多个用例复用。"""
        bypass = bypass or FakeBypass()
        with patch(
            "backend.app.services.manual_registrar.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=self._config()),
        ), patch(
            "backend.app.services.manual_registrar.DeviceProfileManager.get_resolved_profile",
            return_value=dict(SAMPLE_PROFILE),
        ), patch(
            "backend.app.services.manual_registrar.DeviceProfileManager.resolve_effective_credentials",
            return_value=dict(SAMPLE_PROFILE),
        ), patch(
            "backend.app.services.manual_registrar.AttestationGatewayService",
            return_value=bypass,
        ), patch(
            "backend.app.services.manual_registrar.TelegramClient",
            return_value=client,
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator.resolve_active_proxy",
            new=AsyncMock(return_value={"proxy_type": "socks5", "addr": "10.0.0.2", "port": 1080}),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator._connect_mtproto",
            new=AsyncMock(return_value=True),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator.perform_handshake",
            new=AsyncMock(),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator._send_code_with_recaptcha",
            new=AsyncMock(return_value=sent_code or _sent_code()),
        ), patch(
            "backend.app.services.manual_registrar.PhonePrecheckService.check_phone",
            new=AsyncMock(return_value=SimpleNamespace(
                is_registered=False, user_id=None, intercept=False, degraded=False, reason=""
            )),
        ), patch(
            "backend.app.services.manual_registrar.SESSIONS_DIR",
            self.tmp_path,
        ):
            yield


class TestCompleteAuth(ManualTestBase):
    async def test_existing_account_signin(self):
        client = FakeTelegramClient(
            sign_in_result=SimpleNamespace(user=SimpleNamespace(id=777))
        )
        result = await ManualRegistrationOrchestrator._complete_auth(
            client, "+56911112222", "hash-1", "12345", country="cl"
        )
        self.assertEqual(result["user_id"], 777)
        self.assertEqual(result["account_kind"], "existing_no_2fa")
        self.assertFalse(result["needs_signup"])
        self.assertEqual(type(client.calls[0]).__name__, "SignInRequest")

    async def test_new_account_signup(self):
        client = FakeTelegramClient(
            sign_in_error=Exception("AuthorizationSignUpRequired"),
            sign_up_result=SimpleNamespace(user=SimpleNamespace(id=42), terms_of_service=None),
        )
        result = await ManualRegistrationOrchestrator._complete_auth(
            client,
            "+56911112222",
            "hash-1",
            "12345",
            first_name="Mateo",
            last_name="González",
            country="cl",
        )
        self.assertEqual(result["user_id"], 42)
        self.assertEqual(result["account_kind"], "new")
        self.assertTrue(result["needs_signup"])
        names = [type(c).__name__ for c in client.calls]
        self.assertIn("SignInRequest", names)
        self.assertIn("SignUpRequest", names)

    async def test_existing_2fa_uses_password(self):
        client = FakeTelegramClient(
            sign_in_error=NeedPassword(),
            sign_in_result=SimpleNamespace(user=SimpleNamespace(id=9001)),
        )
        result = await ManualRegistrationOrchestrator._complete_auth(
            client,
            "+56911112222",
            "hash-1",
            "12345",
            password="Secret@2FA",
            country="cl",
        )
        self.assertEqual(result["account_kind"], "existing_2fa")
        self.assertEqual(result["user_id"], 9001)
        self.assertEqual(client.signed_in_password, "Secret@2FA")


class TestManualStartPhase(ManualTestBase):
    async def test_start_reaches_waiting_code_without_sms_lease(self):
        fake_client = FakeTelegramClient()
        fake_bypass = FakeBypass()
        sms_get = AsyncMock(side_effect=AssertionError("manual mode must not lease SMS"))
        with patch(
            "backend.app.services.manual_registrar.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=self._config()),
        ), patch(
            "backend.app.services.manual_registrar.DeviceProfileManager.get_resolved_profile",
            return_value=dict(SAMPLE_PROFILE),
        ), patch(
            "backend.app.services.manual_registrar.DeviceProfileManager.resolve_effective_credentials",
            return_value=dict(SAMPLE_PROFILE),
        ), patch(
            "backend.app.services.manual_registrar.AttestationGatewayService",
            return_value=fake_bypass,
        ), patch(
            "backend.app.services.manual_registrar.TelegramClient",
            return_value=fake_client,
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator.resolve_active_proxy",
            new=AsyncMock(return_value={"proxy_type": "socks5", "addr": "10.0.0.2", "port": 1080}),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator._connect_mtproto",
            new=AsyncMock(return_value=True),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator.perform_handshake",
            new=AsyncMock(),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator._send_code_with_recaptcha",
            new=AsyncMock(return_value=_sent_code()),
        ), patch(
            "backend.app.services.manual_registrar.PhonePrecheckService.check_phone",
            new=AsyncMock(return_value=SimpleNamespace(
                is_registered=False, user_id=None, intercept=False, degraded=False, reason=""
            )),
        ), patch(
            "backend.app.services.manual_registrar.SESSIONS_DIR",
            self.tmp_path,
        ), patch(
            "backend.app.services.fivesim.FiveSimService.get_number",
            new=sms_get,
        ):
            result = await ManualRegistrationOrchestrator.start(
                phone="+56911112222",
                country="cl",
                wait_seconds=30,
            )

        self.assertEqual(result["status"], "waiting_code")
        self.assertEqual(result["phone"], "+56911112222")
        self.assertEqual(result["phone_code_hash"], "hash-manual")
        self.assertEqual(result["delivery_type"], "SentCodeTypeSms")
        self.assertTrue(result["task_id"])
        sms_get.assert_not_called()
        task = self.manager.get_task(result["task_id"])
        self.assertEqual(task["status"], "waiting_code")
        self.assertEqual(task["mode"], "manual")
        live = self.store.get(result["task_id"])
        self.assertIsNotNone(live)
        self.assertEqual(live.phone_code_hash, "hash-manual")
        logs = "\n".join(result["logs"])
        self.assertIn("跳过接码平台租号", logs)
        self.assertIn("waiting_code", logs)
        self.assertIn("SentCodeTypeSms", logs)

    async def test_start_invalid_phone_raises_before_task(self):
        with self.assertRaises(ManualRegisterError):
            await ManualRegistrationOrchestrator.start(phone="12")
        self.assertEqual(self.manager.tasks, {})

    async def test_duplicate_start_same_phone_cancels_previous_waiting_task(self):
        """复现用户报告的 bug：同号重复点击「发送验证码」不应堆出多个 waiting_code 任务。"""
        fake_client_1 = FakeTelegramClient()
        fake_client_2 = FakeTelegramClient()

        with self._patch_start_dependencies(fake_client_1):
            first = await ManualRegistrationOrchestrator.start(
                phone="+56911112222", country="cl", wait_seconds=30
            )
        self.assertEqual(first["status"], "waiting_code")
        # _connect_mtproto 在测试里被 mock 掉，未真正调用 client.connect()；
        # 手动置位以模拟生产环境下握手成功后的已连接状态。
        fake_client_1.connected = True

        with self._patch_start_dependencies(fake_client_2):
            second = await ManualRegistrationOrchestrator.start(
                phone="+56911112222", country="cl", wait_seconds=30
            )
        self.assertEqual(second["status"], "waiting_code")

        self.assertNotEqual(first["task_id"], second["task_id"])

        first_task = self.manager.get_task(first["task_id"])
        second_task = self.manager.get_task(second["task_id"])
        self.assertEqual(first_task["status"], "canceled")
        self.assertEqual(second_task["status"], "waiting_code")

        # 旧任务连接已释放，只有新任务持有活跃 MTProto session
        self.assertIsNone(self.store.get(first["task_id"]))
        self.assertIsNotNone(self.store.get(second["task_id"]))
        self.assertTrue(fake_client_1.disconnected)
        self.assertFalse(fake_client_2.disconnected)

        waiting = [
            t for t in self.manager.list_tasks()
            if t.get("status") == "waiting_code" and t.get("phone") == "+56911112222"
        ]
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["task_id"], second["task_id"])

        # 第二个任务的日志中应说明已自动取消旧任务
        logs = "\n".join(second["logs"])
        self.assertIn(first["task_id"], logs)
        self.assertIn("自动取消", logs)

    async def test_start_after_cancel_creates_fresh_waiting_task(self):
        """用户先手动取消旧任务后，重新发码应正常进入 waiting_code，不被误判为冲突。"""
        old_client = FakeTelegramClient()
        old_client.connected = True
        old_tid, _old_live = self._seed_waiting(old_client, phone="+56911112222")
        cancel_result = await ManualRegistrationOrchestrator.cancel(old_tid)
        self.assertEqual(cancel_result["status"], "canceled")

        new_client = FakeTelegramClient()
        with self._patch_start_dependencies(new_client):
            result = await ManualRegistrationOrchestrator.start(
                phone="+56911112222", country="cl", wait_seconds=30
            )

        self.assertEqual(result["status"], "waiting_code")
        self.assertNotEqual(result["task_id"], old_tid)
        self.assertIsNotNone(self.store.get(result["task_id"]))
        # 已取消的旧任务不应被再次「取消」或出现在日志替换说明里
        logs = "\n".join(result["logs"])
        self.assertNotIn(old_tid, logs)
        self.assertEqual(self.manager.get_task(old_tid)["status"], "canceled")


class TestManualSubmitAndCancel(ManualTestBase):
    async def test_submit_new_account_writes_session_pair(self):
        client = FakeTelegramClient(
            sign_in_error=Exception("AuthorizationSignUpRequired"),
            sign_up_result=SimpleNamespace(user=SimpleNamespace(id=42), terms_of_service=None),
        )
        tid, live = self._seed_waiting(client)
        with patch("backend.app.services.manual_registrar.SESSIONS_DIR", self.tmp_path):
            result = await ManualRegistrationOrchestrator.submit_code(
                tid, "12345", first_name="Mateo", last_name="González"
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["user_id"], 42)
        self.assertEqual(result["account_kind"], "new")
        self.assertEqual(result["session_file"], "56911112222.session")
        self.assertTrue(live.meta_path.exists())
        meta = live.meta_path.read_text(encoding="utf-8")
        self.assertIn("56911112222", meta)
        self.assertIn("42", meta)
        self.assertEqual(self.manager.get_task(tid)["status"], "success")
        self.assertIsNone(self.store.get(tid))
        self.assertTrue(client.disconnected)
        self.assertIsNotNone(client.edited_2fa)

    async def test_submit_existing_account_login(self):
        client = FakeTelegramClient(
            sign_in_result=SimpleNamespace(user=SimpleNamespace(id=777))
        )
        tid, _live = self._seed_waiting(client)
        with patch("backend.app.services.manual_registrar.SESSIONS_DIR", self.tmp_path):
            result = await ManualRegistrationOrchestrator.submit_code(tid, "99999")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["user_id"], 777)
        self.assertEqual(result["account_kind"], "existing_no_2fa")

    async def test_submit_existing_2fa_account(self):
        client = FakeTelegramClient(
            sign_in_error=NeedPassword(),
            sign_in_result=SimpleNamespace(user=SimpleNamespace(id=9001)),
        )
        tid, _live = self._seed_waiting(client)
        with patch("backend.app.services.manual_registrar.SESSIONS_DIR", self.tmp_path):
            result = await ManualRegistrationOrchestrator.submit_code(
                tid, "12345", password="Old@2FA"
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["account_kind"], "existing_2fa")
        self.assertEqual(client.signed_in_password, "Old@2FA")

    async def test_invalid_code_keeps_waiting(self):
        client = FakeTelegramClient(sign_in_error=InvalidCode())
        tid, live = self._seed_waiting(client)
        result = await ManualRegistrationOrchestrator.submit_code(tid, "00000")
        self.assertEqual(result["status"], "waiting_code")
        self.assertEqual(self.manager.get_task(tid)["status"], "waiting_code")
        self.assertIs(self.store.get(tid), live)
        self.assertFalse(client.disconnected)

    async def test_cancel_releases_connection(self):
        client = FakeTelegramClient()
        client.connected = True
        tid, live = self._seed_waiting(client)
        live.session_path.write_text("tmp", encoding="utf-8")
        result = await ManualRegistrationOrchestrator.cancel(tid)
        self.assertEqual(result["status"], "canceled")
        self.assertEqual(self.manager.get_task(tid)["status"], "canceled")
        self.assertIsNone(self.store.get(tid))
        self.assertTrue(client.disconnected)
        self.assertFalse(live.session_path.exists())

    async def test_cancel_is_idempotent_after_already_canceled(self):
        """重复调用 cancel（如用户连点取消按钮）必须幂等返回成功，不能 500 或报错。"""
        client = FakeTelegramClient()
        client.connected = True
        tid, _live = self._seed_waiting(client)
        first = await ManualRegistrationOrchestrator.cancel(tid)
        self.assertEqual(first["status"], "canceled")
        second = await ManualRegistrationOrchestrator.cancel(tid)
        self.assertEqual(second["status"], "canceled")
        self.assertIn("无需再次取消", second["message"])

    async def test_cancel_missing_task_raises_friendly_404(self):
        with self.assertRaises(ManualRegisterError) as ctx:
            await ManualRegistrationOrchestrator.cancel("does-not-exist")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_submit_missing_task_404(self):
        with self.assertRaises(ManualRegisterError) as ctx:
            await ManualRegistrationOrchestrator.submit_code("missing", "12345")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_waiting_timeout_fails_task(self):
        fake_client = FakeTelegramClient()
        fake_bypass = FakeBypass()
        with patch(
            "backend.app.services.manual_registrar.ConfigManager.get_instance",
            return_value=SimpleNamespace(config=self._config()),
        ), patch(
            "backend.app.services.manual_registrar.DeviceProfileManager.get_resolved_profile",
            return_value=dict(SAMPLE_PROFILE),
        ), patch(
            "backend.app.services.manual_registrar.DeviceProfileManager.resolve_effective_credentials",
            return_value=dict(SAMPLE_PROFILE),
        ), patch(
            "backend.app.services.manual_registrar.AttestationGatewayService",
            return_value=fake_bypass,
        ), patch(
            "backend.app.services.manual_registrar.TelegramClient",
            return_value=fake_client,
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator.resolve_active_proxy",
            new=AsyncMock(return_value={"proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808}),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator._connect_mtproto",
            new=AsyncMock(return_value=True),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator.perform_handshake",
            new=AsyncMock(),
        ), patch(
            "backend.app.services.manual_registrar.RegistrationOrchestrator._send_code_with_recaptcha",
            new=AsyncMock(return_value=_sent_code()),
        ), patch(
            "backend.app.services.manual_registrar.PhonePrecheckService.check_phone",
            new=AsyncMock(return_value=SimpleNamespace(
                is_registered=False, user_id=None, intercept=False, degraded=False, reason=""
            )),
        ), patch(
            "backend.app.services.manual_registrar.SESSIONS_DIR",
            self.tmp_path,
        ):
            result = await ManualRegistrationOrchestrator.start(
                phone="56911112222", wait_seconds=0.05
            )
            self.assertEqual(result["status"], "waiting_code")
            await asyncio.sleep(0.15)

        task = self.manager.get_task(result["task_id"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"], "MANUAL_CODE_TIMEOUT")
        self.assertIsNone(self.store.get(result["task_id"]))


class TestManualApiRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from backend.app.api.routes import router

        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        app = FastAPI()
        app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        RegistrationTaskManager._instance = self._prev

    async def test_manual_start_submit_cancel_and_aliases(self):
        start_payload = {
            "task_id": "manu0001",
            "status": "waiting_code",
            "phone": "+56911112222",
            "phone_code_hash": "hh",
            "delivery_type": "SentCodeTypeSms",
            "message": "ok",
            "logs": ["sent"],
            "country": "cl",
        }
        submit_payload = {
            "task_id": "manu0001",
            "status": "success",
            "phone": "+56911112222",
            "user_id": 42,
            "message": "ok",
            "session_file": "56911112222.session",
            "account_kind": "new",
            "logs": ["done"],
        }
        cancel_payload = {
            "task_id": "manu0001",
            "status": "canceled",
            "message": "canceled",
            "logs": ["bye"],
        }
        with patch(
            "backend.app.api.routes.ManualRegistrationOrchestrator.start",
            new=AsyncMock(return_value=start_payload),
        ) as starter, patch(
            "backend.app.api.routes.ManualRegistrationOrchestrator.submit_code",
            new=AsyncMock(return_value=submit_payload),
        ) as submitter, patch(
            "backend.app.api.routes.ManualRegistrationOrchestrator.cancel",
            new=AsyncMock(return_value=cancel_payload),
        ) as canceler:
            res = await self.client.post(
                "/api/register/manual/start",
                json={"phone": "+56911112222", "country": "cl"},
            )
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["status"], "waiting_code")
            self.assertEqual(res.json()["delivery_type"], "SentCodeTypeSms")

            alias = await self.client.post(
                "/api/provision/manual/start",
                json={"phone": "56911112222"},
            )
            self.assertEqual(alias.status_code, 200)

            submitted = await self.client.post(
                "/api/register/manual/submit-code",
                json={"task_id": "manu0001", "code": "12345"},
            )
            self.assertEqual(submitted.status_code, 200, submitted.text)
            self.assertEqual(submitted.json()["user_id"], 42)
            self.assertEqual(submitted.json()["session_file"], "56911112222.session")

            alias_submit = await self.client.post(
                "/api/provision/manual/submit-code",
                json={"task_id": "manu0001", "code": "12345"},
            )
            self.assertEqual(alias_submit.status_code, 200)

            canceled = await self.client.post(
                "/api/register/manual/cancel",
                json={"task_id": "manu0001"},
            )
            self.assertEqual(canceled.status_code, 200)
            self.assertEqual(canceled.json()["status"], "canceled")

            alias_cancel = await self.client.post(
                "/api/provision/manual/cancel",
                json={"task_id": "manu0001"},
            )
            self.assertEqual(alias_cancel.status_code, 200)

            self.assertEqual(starter.await_count, 2)
            self.assertEqual(submitter.await_count, 2)
            self.assertEqual(canceler.await_count, 2)

    async def test_manual_start_rejects_short_phone(self):
        res = await self.client.post("/api/register/manual/start", json={"phone": "12"})
        self.assertEqual(res.status_code, 422)

    async def test_manual_error_maps_http_status(self):
        with patch(
            "backend.app.api.routes.ManualRegistrationOrchestrator.submit_code",
            new=AsyncMock(side_effect=ManualRegisterError("任务不存在", status_code=404)),
        ):
            res = await self.client.post(
                "/api/register/manual/submit-code",
                json={"task_id": "missing", "code": "12345"},
            )
        self.assertEqual(res.status_code, 404)
        self.assertIn("不存在", res.json()["detail"])


class TestWaitWindowConstant(unittest.TestCase):
    def test_wait_window_is_five_to_ten_minutes(self):
        self.assertGreaterEqual(MANUAL_CODE_WAIT_SECONDS, 300)
        self.assertLessEqual(MANUAL_CODE_WAIT_SECONDS, 600)


if __name__ == "__main__":
    unittest.main()
