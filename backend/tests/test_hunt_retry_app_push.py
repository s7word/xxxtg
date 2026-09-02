"""循环试号：SentCodeTypeApp 换号并复用同一 Push Token。"""

from __future__ import annotations

import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.models.schemas import RegisterTaskRequest
from backend.app.services.banned_phones import CATEGORY_APP_DELIVERY
from backend.app.services.registrar import (
    DEFAULT_SMS_POLL_ATTEMPTS,
    HUNT_MIN_SMS_POLL_ATTEMPTS,
    HUNT_PUSH_TOKEN_MAX_AGE_SECONDS,
    RegistrationOrchestrator,
    RegistrationTaskManager,
    SentCodeAppDeliveryError,
)


class FakeSms:
    PROVIDER_NAME = "vaksms"

    def __init__(self, numbers):
        self._numbers = list(numbers)
        self.cancel_calls = []
        self.finish_calls = []
        self.poll_attempts = []
        self.closed = False

    async def get_number(self, country, service="tg", max_price=None):
        if not self._numbers:
            raise RuntimeError("no more numbers in fake pool")
        return self._numbers.pop(0)

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "status": "cancel"}

    async def wait_for_code(self, act_id, max_attempts=30, log_callback=None):
        self.poll_attempts.append(max_attempts)
        if log_callback:
            await log_callback(f"fake otp for {act_id}")
        return "12345"

    async def finish(self, act_id):
        self.finish_calls.append(act_id)
        return {"success": True}

    async def close(self):
        self.closed = True


class TimeoutSms(FakeSms):
    """进入 OTP 阶段后直接超时，便于断言真正下发的轮询次数。"""

    async def wait_for_code(self, act_id, max_attempts=30, log_callback=None):
        self.poll_attempts.append(max_attempts)
        raise TimeoutError("no code")


def make_profile():
    return {
        "name": "test",
        "aid": "aid-1",
        "api_id": 123456,
        "api_hash": "hash",
        "device_model": "Pixel",
        "system_version": "SDK 33",
        "app_version": "12.0",
        "lang_code": "es",
        "system_lang_code": "es-cl",
        "tz_offset": -14400,
        "credential_source": "custom",
        "is_published_api_id": False,
    }


def make_config(**overrides):
    base = SimpleNamespace(
        target_country="cl",
        active_app_type="telegram_android",
        vak_sms_api_key="vak",
        sms_provider="vaksms",
        grizzly_sms_api_key="",
        use_proxy_seller_auto=False,
        fallback_proxy=SimpleNamespace(
            model_dump=lambda: {
                "proxy_type": "socks5",
                "addr": "127.0.0.1",
                "port": 10808,
                "username": None,
                "password": None,
            }
        ),
        custom_proxies=[],
        phone_precheck_enabled=True,
        api_credential_mode="custom",
        custom_api_id=123456,
        custom_api_hash="hash",
        default_2fa_password="x",
        auto_set_2fa=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def make_gateway(refund_status="STATUS_RETRY", push_tokens=None):
    gw = MagicMock()
    gw.check_phone_history = AsyncMock(return_value=None)
    tokens = push_tokens or [("TOKEN", "push-task-1", "reghelp")]
    gw.get_push_token = AsyncMock(side_effect=lambda *a, **kw: tokens[
        min(gw.get_push_token.await_count - 1, len(tokens) - 1)
    ])
    gw.close = AsyncMock()
    gw.report_result = AsyncMock()
    gw.refund_push_token = AsyncMock(return_value=refund_status)
    return gw


CLEAN_PRECHECK = SimpleNamespace(
    intercept=False, is_registered=False, degraded=False, reason="", user_id=None
)


class TestHuntRetryAppPush(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    def _profile(self):
        return make_profile()

    def _config(self):
        # Push Token 的复用/退款生命周期只在真的 attach Token 时才有意义；
        # 默认 balanced + 自建非泄露 api_id 根本不申请 Token（见 TestCodeDeliveryModeInHunt）
        return make_config(code_delivery_mode="push_required")

    async def test_app_delivery_retries_with_same_push_token(self):
        sms = FakeSms([
            ("act-1", "+56911110001"),
        ])
        # 第二次取号故意无库存，证明 APP 换号后未退 Push、且只申请过一次 Token
        from backend.app.services.vaksms import NoNumberAvailableError

        original_get = sms.get_number

        async def get_then_empty(*args, **kwargs):
            if not sms._numbers:
                raise NoNumberAvailableError("NO_NUMBERS")
            return await original_get(*args, **kwargs)

        sms.get_number = get_then_empty

        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN-KEEP", "push-task-1", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value="STATUS_RETRY")

        clean = SimpleNamespace(
            intercept=False, is_registered=False, degraded=False, reason="", user_id=None
        )

        cfg_mgr = SimpleNamespace(config=self._config())
        client = MagicMock()
        client.is_connected = MagicMock(return_value=True)
        client.disconnect = AsyncMock()
        client.connect = AsyncMock()

        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch(
                 "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                 side_effect=lambda p, c, has_push_token=False: {**p, "credential_source": "custom"},
             ), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch("backend.app.services.registrar.BannedPhonesCache.remember") as remember, \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(
                 RegistrationOrchestrator,
                 "_send_code_with_recaptcha",
                 new=AsyncMock(side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")),
             ), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch("backend.app.services.registrar.TelegramClient", return_value=client):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
                no_number_retries=0,
            )

        self.assertEqual(gw.get_push_token.await_count, 1)
        self.assertEqual(sms.cancel_calls, ["act-1"])
        gw.refund_push_token.assert_not_awaited()
        remember.assert_called()
        # APP 投递只临时拉黑（TTL），不得再写永久 already_registered
        kwargs = remember.call_args.kwargs
        self.assertEqual(kwargs["category"], CATEGORY_APP_DELIVERY)
        self.assertEqual(kwargs["ttl_hours"], 48.0)
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("APP 投递不可用（未必已注册）", logs)
        self.assertIn("换号继续", logs)
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task.get("no_number"))

    async def test_last_app_attempt_refunds_push(self):
        sms = FakeSms([("act-1", "+56911110001")])
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", "push-task-9", "reghelp"))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()
        gw.refund_push_token = AsyncMock(return_value="RETRY_PUSH")

        clean = SimpleNamespace(
            intercept=False, is_registered=False, degraded=False, reason="", user_id=None
        )
        cfg_mgr = SimpleNamespace(config=self._config())
        client = MagicMock()
        client.is_connected = MagicMock(return_value=True)
        client.disconnect = AsyncMock()

        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch(
                 "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                 side_effect=lambda p, c, has_push_token=False: {**p, "credential_source": "custom"},
             ), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch("backend.app.services.registrar.BannedPhonesCache.remember") as remember, \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(
                 RegistrationOrchestrator,
                 "_send_code_with_recaptcha",
                 new=AsyncMock(side_effect=SentCodeAppDeliveryError("app", reason="SENT_CODE_TYPE_APP")),
             ), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch("backend.app.services.registrar.TelegramClient", return_value=client), \
             patch.dict("os.environ", {"EDGENODE_SKIP_PUSH_REFUND_WAIT": "1"}):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=1,
            )

        gw.refund_push_token.assert_awaited()
        # 普通路径（max_number_attempts=1）：循环内已 cancel + 拉黑，外层兜底不得重复
        self.assertEqual(sms.cancel_calls, ["act-1"])
        self.assertEqual(remember.call_count, 1)
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("SENT_CODE_TYPE_APP", task.get("error", ""))


class TestHuntSchema(unittest.TestCase):
    def test_max_number_attempts_bounds(self):
        ok = RegisterTaskRequest(country="cl", max_number_attempts=100)
        self.assertEqual(ok.max_number_attempts, 100)
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            RegisterTaskRequest(country="cl", max_number_attempts=0)
        with self.assertRaises(ValidationError):
            RegisterTaskRequest(country="cl", max_number_attempts=501)

    def test_no_number_retries_field(self):
        ok = RegisterTaskRequest(country="cl", no_number_retries=20)
        self.assertEqual(ok.no_number_retries, 20)


class TestHuntLimitsHelpers(unittest.IsolatedAsyncioTestCase):
    def test_resolve_hunt_limits_defaults(self):
        cfg = SimpleNamespace()
        limits = RegistrationOrchestrator._resolve_hunt_limits(cfg)
        self.assertEqual(limits["no_number_retries"], 20)
        self.assertEqual(limits["proxy_max_uses"], 5)
        self.assertEqual(limits["device_max_uses"], 8)

    async def test_lease_number_retries_then_raises(self):
        from backend.app.services.vaksms import NoNumberAvailableError

        calls = {"n": 0}

        class Sms:
            async def get_number(self, **kwargs):
                calls["n"] += 1
                raise NoNumberAvailableError("NO_NUMBERS")

        manager = RegistrationTaskManager()
        manager.tasks = {}
        prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = manager
        tid = manager.create_task()
        try:
            with self.assertRaises(NoNumberAvailableError):
                await RegistrationOrchestrator._lease_number_with_retries(
                    Sms(),
                    "cl",
                    None,
                    tid,
                    manager,
                    hunt_enabled=True,
                    no_number_retries=2,
                    no_number_delay=0.0,
                )
            self.assertEqual(calls["n"], 3)
        finally:
            RegistrationTaskManager._instance = prev


class HuntRunMixin:
    """把猎号主循环所需的一整套 patch 收敛成一个上下文，供多个回归用例复用。"""

    def _run_ctx(
        self,
        *,
        sms,
        gw,
        send_code,
        config=None,
        precheck=None,
        extra=(),
    ):
        from contextlib import ExitStack

        cfg_mgr = SimpleNamespace(config=config or make_config())
        client = MagicMock()
        client.is_connected = MagicMock(return_value=True)
        client.disconnect = AsyncMock()
        client.connect = AsyncMock()

        stack = ExitStack()
        from backend.app.services.registrar import SendCodeFloodWindow

        SendCodeFloodWindow.get().reset()
        patches = [
            patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr),
            patch("backend.app.services.registrar.VakSmsService", return_value=sms),
            patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw),
            patch(
                "backend.app.services.registrar.DeviceProfileManager.get_resolved_profile",
                return_value=make_profile(),
            ),
            patch(
                "backend.app.services.registrar.DeviceProfileManager.resolve_effective_credentials",
                side_effect=lambda p, c, has_push_token=False: {**p, "credential_source": "custom"},
            ),
            patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None),
            patch(
                "backend.app.services.registrar.PhonePrecheckService.check_phone",
                new=AsyncMock(return_value=precheck or CLEAN_PRECHECK),
            ),
            patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()),
            patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)),
            patch.object(RegistrationOrchestrator, "_send_code_with_recaptcha", new=send_code),
            patch("backend.app.services.registrar.TelegramClient", return_value=client),
            patch.dict("os.environ", {"EDGENODE_SKIP_PUSH_REFUND_WAIT": "1"}),
            *extra,
        ]
        for item in patches:
            stack.enter_context(item)
        return stack


class TestHuntNoDoubleCancel(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_hunt_exhausted_cancels_each_number_once(self):
        """三轮全部 SentCodeTypeApp：每个号只 cancel/拉黑一次，终态为 HUNT_EXHAUSTED。"""
        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
            ("act-3", "+56911110003"),
        ])
        gw = make_gateway()
        send_code = AsyncMock(
            side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
        )
        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=send_code,
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=3,
                no_number_retries=0,
            )

        self.assertEqual(sms.cancel_calls, ["act-1", "act-2", "act-3"])
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("HUNT_EXHAUSTED", task.get("error", ""))
        self.assertEqual(task.get("hunt_scanned"), 3)
        self.assertEqual(task.get("hunt_blacklisted"), 3)
        self.assertEqual(task.get("hunt_last_reason"), "SENT_CODE_TYPE_APP")

    async def test_blacklist_gate_does_not_pollute_final_state(self):
        """中途命中黑名单闸门后继续猎号，filtered/error 残留不得污染最终终态。"""
        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        record = SimpleNamespace(
            reason="PHONE_NUMBER_BANNED", source="telegram_rpc", hits=1, category="banned"
        )
        lookups = [record, None]
        send_code = AsyncMock(
            side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
        )
        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=send_code,
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
                patch(
                    "backend.app.services.registrar.BannedPhonesCache.lookup",
                    side_effect=lambda phone: lookups.pop(0) if lookups else None,
                ),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=2,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(sms.cancel_calls, ["act-1", "act-2"])
        self.assertNotEqual(task["status"], "filtered")
        self.assertFalse(task.get("banned_cache_hit"))
        self.assertIsNone(task.get("blacklist_category"))
        self.assertIn("HUNT_EXHAUSTED", task.get("error", ""))
        self.assertNotIn("LOCAL_BANNED_PHONE_CACHE", task.get("error", ""))

    async def test_long_flood_aborts_hunt(self):
        """小时级 FLOOD_WAIT 直接终止猎号，不再紧凑烧号。"""
        from telethon.errors import FloodWaitError

        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=AsyncMock(side_effect=FloodWaitError(request=None, capture=7200)),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(sms.cancel_calls, ["act-1"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("HUNT_FLOOD_ABORT", task.get("error", ""))

    async def test_short_flood_without_rotatable_proxy_aborts(self):
        """短 FLOOD 但换不到出口时必须终止，而不是用同一个 IP 继续撞频控。"""
        from telethon.errors import FloodWaitError

        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=AsyncMock(side_effect=FloodWaitError(request=None, capture=15)),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(sms.cancel_calls, ["act-1"])
        self.assertIn("HUNT_FLOOD_NO_PROXY", task.get("error", ""))
        logs = "\n".join(task["logs"])
        self.assertNotIn("出口已轮换", logs)

    async def test_short_flood_rotates_proxy_and_backs_off(self):
        """短 FLOOD 且池内有其它节点：真换出口 + 退避后继续扫号。"""
        from telethon.errors import FloodWaitError

        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        pool = [
            {
                "id": "custom-a", "proxy_type": "socks5", "addr": "10.0.0.1", "port": 1080,
                "username": "u1", "role": "registration", "assigned_country": "cl",
                "healthy": True, "latency_ms": 100,
            },
            {
                "id": "custom-b", "proxy_type": "socks5", "addr": "10.0.0.2", "port": 1080,
                "username": "u2", "role": "registration", "assigned_country": "cl",
                "healthy": True, "latency_ms": 200,
            },
        ]
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with self._run_ctx(
            sms=sms,
            gw=gw,
            config=make_config(custom_proxies=pool),
            send_code=AsyncMock(side_effect=FloodWaitError(request=None, capture=120)),
            extra=[
                patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=pool),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
                patch("backend.app.services.registrar.asyncio.sleep", new=fake_sleep),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=2,
                no_number_retries=0,
            )

        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("出口已轮换", logs)
        # 必须等满 Telegram 给出的 FLOOD 窗，禁止 30s 短退避后再发把窗口填满
        self.assertTrue(sleeps)
        self.assertEqual(max(sleeps), 120.0)
        self.assertEqual(sms.cancel_calls, ["act-1", "act-2"])

    async def test_hunt_sms_stage_keeps_full_poll_window(self):
        """跨轮复用的 Push Token 过期后应换新 Token，OTP 轮询不得被砍到 1 次。"""
        sms = TimeoutSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway(
            push_tokens=[("TOKEN-A", "push-a", "reghelp"), ("TOKEN-B", "push-b", "reghelp")]
        )
        sent_code = SimpleNamespace(phone_code_hash="hash-1")
        calls = {"n": 0}

        async def send_code_impl(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
            return sent_code

        with self._run_ctx(
            sms=sms,
            gw=gw,
            config=make_config(code_delivery_mode="push_required"),
            send_code=AsyncMock(side_effect=send_code_impl),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
                patch.object(
                    RegistrationOrchestrator,
                    "resolve_sent_code_channel",
                    new=AsyncMock(return_value=(sent_code, DEFAULT_SMS_POLL_ATTEMPTS)),
                ),
                # 模拟第二轮开始时旧 Token 的退款窗口已耗尽
                patch.object(
                    RegistrationOrchestrator,
                    "_push_token_window_exhausted",
                    side_effect=[True],
                ),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=2,
                no_number_retries=0,
            )

        self.assertEqual(gw.get_push_token.await_count, 2)
        self.assertTrue(sms.poll_attempts, "应已进入 OTP 阶段")
        self.assertGreaterEqual(sms.poll_attempts[-1], HUNT_MIN_SMS_POLL_ATTEMPTS)
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("先退旧 Token 再申请新的", logs)


class TestHuntAppDeliveryFuse(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    """连续 APP 投递是系统性失败，不该把整个取号预算烧完。"""

    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_consecutive_app_deliveries_trip_the_fuse(self):
        sms = FakeSms([(f"act-{i}", f"+5691111000{i}") for i in range(1, 9)])
        gw = make_gateway()
        with self._run_ctx(
            sms=sms,
            gw=gw,
            config=make_config(hunt_app_delivery_fuse=3),
            send_code=AsyncMock(
                side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
            ),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=8,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(len(sms.cancel_calls), 3, "熔断后不得继续租号")
        self.assertEqual(task["status"], "failed")
        self.assertIn("HUNT_APP_FUSE", task.get("error", ""))
        logs = "\n".join(task["logs"])
        self.assertIn("熔断阈值 3", logs)

    async def test_fuse_disabled_runs_full_budget(self):
        sms = FakeSms([(f"act-{i}", f"+5691111000{i}") for i in range(1, 5)])
        gw = make_gateway()
        with self._run_ctx(
            sms=sms,
            gw=gw,
            config=make_config(hunt_app_delivery_fuse=0),
            send_code=AsyncMock(
                side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
            ),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=4,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(len(sms.cancel_calls), 4)
        self.assertIn("HUNT_EXHAUSTED", task.get("error", ""))

    async def test_non_app_round_resets_the_streak(self):
        """APP → 封禁 → APP：连续计数被打断，不该在第 2 次 APP 就熔断。"""
        from telethon.errors import PhoneNumberBannedError

        sms = FakeSms([(f"act-{i}", f"+5691111000{i}") for i in range(1, 4)])
        gw = make_gateway()
        calls = {"n": 0}

        async def send_code_impl(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise PhoneNumberBannedError(request=None)
            raise SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")

        with self._run_ctx(
            sms=sms,
            gw=gw,
            config=make_config(hunt_app_delivery_fuse=2),
            send_code=AsyncMock(side_effect=send_code_impl),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=3,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(calls["n"], 3)
        self.assertIn("HUNT_EXHAUSTED", task.get("error", ""))


class TestCodeDeliveryModeInHunt(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    """默认 balanced + 自建非泄露 api_id：整条猎号链路不得申请或 attach Push Token。"""

    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_balanced_custom_api_id_never_requests_push(self):
        sms = TimeoutSms([("act-1", "+56911110001")])
        gw = make_gateway()
        sent_code = SimpleNamespace(phone_code_hash="hash-1")
        captured = {}

        async def send_code_impl(**kwargs):
            captured["code_settings"] = kwargs["code_settings"]
            return sent_code

        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=AsyncMock(side_effect=send_code_impl),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
                patch.object(
                    RegistrationOrchestrator,
                    "resolve_sent_code_channel",
                    new=AsyncMock(return_value=(sent_code, DEFAULT_SMS_POLL_ATTEMPTS)),
                ),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=1,
                no_number_retries=0,
            )

        gw.get_push_token.assert_not_awaited()
        gw.refund_push_token.assert_not_awaited()
        code_settings = captured["code_settings"]
        self.assertIsNone(code_settings.token)
        self.assertIsNone(code_settings.app_sandbox)
        # Android 指纹下 allow_app_hash 必须保持官方客户端行为
        self.assertTrue(code_settings.allow_app_hash)
        # 不申请 Token 时收码窗口不该被 REGHelp 退款窗口截断
        self.assertEqual(sms.poll_attempts, [DEFAULT_SMS_POLL_ATTEMPTS])
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("跳过 Push Token 申请（非泄露 api_id）", logs)


class TestHuntSwappableSendErrors(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    """可换号的 sendCode 异常必须继续扫号，而不是烧掉剩余预算。"""

    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_invalid_phone_continues_hunting(self):
        from telethon.errors import PhoneNumberInvalidError

        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
            ("act-3", "+56911110003"),
        ])
        gw = make_gateway()
        with self._run_ctx(
            sms=sms,
            gw=gw,
            config=make_config(code_delivery_mode="push_required"),
            send_code=AsyncMock(side_effect=PhoneNumberInvalidError(request=None)),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=3,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(sms.cancel_calls, ["act-1", "act-2", "act-3"])
        self.assertEqual(task.get("hunt_scanned"), 3)
        self.assertEqual(task.get("hunt_last_reason"), "PHONE_NUMBER_INVALID")
        self.assertIn("HUNT_EXHAUSTED", task.get("error", ""))
        # 只申请过一次 Push Token：坏号不该连带把 Token 换掉
        self.assertEqual(gw.get_push_token.await_count, 1)

    async def test_api_id_published_flood_still_terminates(self):
        """凭证层的全局故障换号也救不了，保持终止而不是继续烧预算。"""
        from telethon.errors import ApiIdPublishedFloodError

        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=AsyncMock(side_effect=ApiIdPublishedFloodError(request=None)),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(sms.cancel_calls, ["act-1"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("API_ID_PUBLISHED_FLOOD", task.get("error", ""))


class TestPrecheckPoolAlert(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    """预检探测池全不可用时必须显式告警，不能静默降级。"""

    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_unauthorized_probe_pool_warns_once(self):
        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        degraded = SimpleNamespace(
            intercept=False,
            is_registered=None,
            degraded=True,
            reason="PRECHECK_SESSION_UNAUTHORIZED",
            user_id=None,
        )
        with self._run_ctx(
            sms=sms,
            gw=gw,
            precheck=degraded,
            send_code=AsyncMock(
                side_effect=SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")
            ),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=2,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        logs = task["logs"]
        alerts = [line for line in logs if "白号预检探测池当前整体不可用" in line]
        self.assertEqual(len(alerts), 1, "显式告警必须有，且每任务只播报一次")
        self.assertIn("PRECHECK_SESSION_UNAUTHORIZED", alerts[0])
        self.assertTrue(task.get("precheck_pool_alerted"))


class TestPollWindowReuseAging(unittest.TestCase):
    """复用令牌的老化判断不能因 provider != reghelp 就整枚跳过。"""

    def test_reuse_token_age_triggers_rotation(self):
        self.assertTrue(
            RegistrationOrchestrator._push_token_window_exhausted(
                "reghelp_reuse",
                time.monotonic() - 1.0,
                DEFAULT_SMS_POLL_ATTEMPTS,
                token_age_seconds=HUNT_PUSH_TOKEN_MAX_AGE_SECONDS + 5.0,
            )
        )

    def test_fresh_reuse_token_is_kept(self):
        self.assertFalse(
            RegistrationOrchestrator._push_token_window_exhausted(
                "reghelp_reuse",
                time.monotonic() - 1.0,
                DEFAULT_SMS_POLL_ATTEMPTS,
                token_age_seconds=5.0,
            )
        )

    def test_reuse_token_age_reads_vault_created_at(self):
        import tempfile
        from datetime import datetime, timedelta, timezone

        from backend.app.services.push_token_vault import PushTokenVault

        prev = PushTokenVault._instance
        with tempfile.TemporaryDirectory() as tmp:
            vault = PushTokenVault.reset_for_tests(path=Path(tmp) / "vault.json")
            try:
                row = vault.store_issued(token="TOKEN-OLD", reghelp_task_id="task-old")
                aged = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
                vault._items[0]["created_at"] = aged
                age = RegistrationOrchestrator._push_token_age_seconds(
                    "reghelp_reuse", time.monotonic() - 1.0, "task-old", "TOKEN-OLD"
                )
                self.assertIsNotNone(age)
                self.assertGreater(age, 500.0)
                self.assertTrue(
                    RegistrationOrchestrator._push_token_window_exhausted(
                        "reghelp_reuse",
                        time.monotonic() - 1.0,
                        DEFAULT_SMS_POLL_ATTEMPTS,
                        token_age_seconds=age,
                    )
                )
                self.assertEqual(row["use_count"], 0)
            finally:
                PushTokenVault._instance = prev


class TestHuntPollWindowFloor(unittest.TestCase):
    def test_stale_token_keeps_min_attempts(self):
        stale = time.monotonic() - 170.0
        self.assertEqual(
            RegistrationOrchestrator._sms_poll_attempts_for_push_window(
                DEFAULT_SMS_POLL_ATTEMPTS, "reghelp", stale
            ),
            1,
        )
        self.assertEqual(
            RegistrationOrchestrator._sms_poll_attempts_for_push_window(
                DEFAULT_SMS_POLL_ATTEMPTS, "reghelp", stale,
                min_attempts=HUNT_MIN_SMS_POLL_ATTEMPTS,
            ),
            HUNT_MIN_SMS_POLL_ATTEMPTS,
        )

    def test_window_exhausted_detection(self):
        self.assertTrue(
            RegistrationOrchestrator._push_token_window_exhausted(
                "reghelp", time.monotonic() - 120.0
            )
        )
        self.assertFalse(
            RegistrationOrchestrator._push_token_window_exhausted(
                "reghelp", time.monotonic() - 1.0
            )
        )
        self.assertFalse(
            RegistrationOrchestrator._push_token_window_exhausted("reghelp_reuse", None)
        )


class TestHuntRefundReasonAliases(unittest.TestCase):
    """猎号私有原因必须能落到 REGHelp 的 setStatus 枚举，否则退款会被静默跳过。"""

    def test_hunt_reasons_resolve_to_platform_status(self):
        from backend.app.services.reghelp import RegHelpService
        from backend.app.services.registrar import HUNT_REFUND_REASON_ALIASES

        expected = {
            "HUNT_DEVICE_ROTATE": "NOSMS",
            "HUNT_PUSH_ROTATE": "NOSMS",
            "HUNT_EXHAUSTED": "NOSMS",
            "HUNT_CANCELED": "NOSMS",
            "HUNT_FLOOD_ABORT": "FLOOD",
            "HUNT_FLOOD_NO_PROXY": "FLOOD",
        }
        for reason, status in expected.items():
            canonical = HUNT_REFUND_REASON_ALIASES[reason]
            self.assertEqual(RegHelpService.resolve_refund_status(canonical), status, reason)


class TestPushTokenRetire(unittest.TestCase):
    """设备/窗口轮换后作废的 Token 不能被立刻租回来。"""

    def test_retired_token_leaves_reuse_pool(self):
        import tempfile
        from backend.app.services.push_token_vault import PushTokenVault, STATUS_RETIRED

        prev = PushTokenVault._instance
        with tempfile.TemporaryDirectory() as tmp:
            vault = PushTokenVault.reset_for_tests(path=Path(tmp) / "vault.json")
            try:
                vault.store_issued(token="TOKEN-A", reghelp_task_id="task-a")
                self.assertIsNotNone(vault.acquire_for_reuse(max_uses=3))
                vault.mark_retired(reghelp_task_id="task-a", reason="HUNT_DEVICE_ROTATE")
                self.assertIsNone(vault.acquire_for_reuse(max_uses=3))
                self.assertEqual(vault.list_items()[0]["status"], STATUS_RETIRED)
                self.assertEqual(vault.summary()["retired"], 1)
            finally:
                PushTokenVault._instance = prev


class TestHuntProxyRotation(unittest.IsolatedAsyncioTestCase):
    def _pool(self):
        return [
            {
                "id": "custom-a",
                "proxy_type": "socks5",
                "addr": "10.0.0.1",
                "port": 1080,
                "username": "u1",
                "password": "p",
                "role": "registration",
                "assigned_country": "cl",
                "healthy": True,
                "latency_ms": 100,
            },
            {
                "id": "custom-b",
                "proxy_type": "socks5",
                "addr": "10.0.0.2",
                "port": 1080,
                "username": "u2",
                "password": "p",
                "role": "registration",
                "assigned_country": "cl",
                "healthy": True,
                "latency_ms": 200,
            },
        ]

    def test_select_registration_proxy_honours_exclude(self):
        from backend.app.services.proxy_manager import select_proxy_for_registration
        from backend.app.services.proxyseller import proxy_identity

        pool = self._pool()
        with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=pool):
            first = select_proxy_for_registration("cl")
            self.assertIsNotNone(first)
            second = select_proxy_for_registration("cl", exclude=[proxy_identity(first)])
        self.assertIsNotNone(second)
        self.assertNotEqual(proxy_identity(first), proxy_identity(second))

    async def test_rotate_hunt_proxy_changes_identity(self):
        from backend.app.services.proxy_manager import select_proxy_for_registration
        from backend.app.services.proxyseller import proxy_identity

        manager = RegistrationTaskManager()
        manager.tasks = {}
        prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = manager
        tid = manager.create_task()
        pool = self._pool()
        config = make_config(custom_proxies=pool)
        try:
            with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=pool):
                current = select_proxy_for_registration("cl")
                rotated, changed = await RegistrationOrchestrator._rotate_hunt_proxy(
                    config=config,
                    target_country="cl",
                    task_id=tid,
                    manager=manager,
                    current_proxy=current,
                    proxy_mode="custom_pool",
                    reason="单测轮换",
                )
            self.assertTrue(changed)
            self.assertNotEqual(proxy_identity(current), proxy_identity(rotated))
            logs = "\n".join(manager.get_task(tid)["logs"])
            self.assertIn("出口已轮换", logs)
        finally:
            RegistrationTaskManager._instance = prev

    async def test_rotate_hunt_proxy_is_honest_when_pinned(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = manager
        tid = manager.create_task()
        pinned = {"proxy_type": "socks5", "addr": "10.9.9.9", "port": 1080}
        try:
            rotated, changed = await RegistrationOrchestrator._rotate_hunt_proxy(
                config=make_config(),
                target_country="cl",
                task_id=tid,
                manager=manager,
                current_proxy=pinned,
                proxy_mode="custom_pool",
                reason="批量槽位",
                proxy_override=pinned,
            )
            self.assertFalse(changed)
            self.assertEqual(rotated, pinned)
            logs = "\n".join(manager.get_task(tid)["logs"])
            self.assertIn("本模式不轮换代理", logs)
            self.assertNotIn("出口已轮换", logs)
        finally:
            RegistrationTaskManager._instance = prev

    async def test_rotate_hunt_proxy_reports_single_node_pool(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = manager
        tid = manager.create_task()
        pool = self._pool()[:1]
        try:
            from backend.app.services.proxy_manager import select_proxy_for_registration

            with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=pool):
                current = select_proxy_for_registration("cl")
                _, changed = await RegistrationOrchestrator._rotate_hunt_proxy(
                    config=make_config(custom_proxies=pool),
                    target_country="cl",
                    task_id=tid,
                    manager=manager,
                    current_proxy=current,
                    proxy_mode="custom_pool",
                    reason="单节点池",
                )
            self.assertFalse(changed)
            logs = "\n".join(manager.get_task(tid)["logs"])
            self.assertIn("没有其它候选节点", logs)
        finally:
            RegistrationTaskManager._instance = prev


class TestHuntCancel(HuntRunMixin, unittest.IsolatedAsyncioTestCase):
    """S1：取消接口置位后，猎号循环必须在下一轮取号前收住。"""

    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    def test_request_cancel_on_pending_task_terminates_immediately(self):
        result = self.manager.request_cancel(self.task_id)
        self.assertEqual(result["status"], "canceled")
        self.assertTrue(result["changed"])
        self.assertTrue(result["terminated"])
        self.assertTrue(self.manager.cancel_requested(self.task_id))

    def test_request_cancel_on_running_task_only_sets_flag(self):
        self.manager.update_task_status(self.task_id, "running")
        result = self.manager.request_cancel(self.task_id)
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["changed"])
        self.assertFalse(result["terminated"])
        self.assertTrue(self.manager.get_task(self.task_id)["cancel_requested"])

    def test_request_cancel_on_terminal_task_is_noop(self):
        self.manager.update_task_status(self.task_id, "success")
        result = self.manager.request_cancel(self.task_id)
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["changed"])
        self.assertIsNone(self.manager.request_cancel("no-such-task"))

    def test_request_batch_cancel_groups_tasks(self):
        batch_id, task_ids = self.manager.create_batch(count=3, concurrency=2)
        self.manager.update_task_status(task_ids[0], "running")
        self.manager.update_task_status(task_ids[1], "success")
        result = self.manager.request_batch_cancel(batch_id)
        self.assertEqual(result["requested"], [task_ids[0]])
        self.assertEqual(result["skipped"], [task_ids[1]])
        self.assertEqual(result["terminated"], [task_ids[2]])
        self.assertIsNone(self.manager.request_batch_cancel("nope"))

    async def test_canceled_task_never_leases_a_number(self):
        """启动前取消：run_registration 不得把任务复活成 running，也不得租号。"""
        sms = FakeSms([("act-1", "+56911110001")])
        gw = make_gateway()
        self.manager.request_cancel(self.task_id)

        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=AsyncMock(),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "canceled")
        self.assertIn("HUNT_CANCELED", task.get("error", ""))
        self.assertEqual(sms.cancel_calls, [])
        gw.get_push_token.assert_not_awaited()

    async def test_cancel_during_first_round_stops_before_second(self):
        """第一轮进行中收到取消：第二轮不得开始，终态为 HUNT_CANCELED。"""
        sms = FakeSms([
            ("act-1", "+56911110001"),
            ("act-2", "+56911110002"),
        ])
        gw = make_gateway()
        calls = {"n": 0}

        async def send_code_impl(**kwargs):
            calls["n"] += 1
            # 模拟用户在第一轮进行中点了「停止」
            self.manager.request_cancel(self.task_id)
            raise SentCodeAppDeliveryError("app only", reason="SENT_CODE_TYPE_APP")

        with self._run_ctx(
            sms=sms,
            gw=gw,
            send_code=AsyncMock(side_effect=send_code_impl),
            extra=[
                patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)),
                patch("backend.app.services.registrar.BannedPhonesCache.remember"),
            ],
        ):
            await RegistrationOrchestrator.run_registration(
                task_id=self.task_id,
                country="cl",
                max_number_attempts=5,
                no_number_retries=0,
            )

        task = self.manager.get_task(self.task_id)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sms.cancel_calls, ["act-1"])
        self.assertEqual(task["status"], "canceled")
        self.assertIn("HUNT_CANCELED", task.get("error", ""))
        logs = "\n".join(task["logs"])
        self.assertIn("收到取消请求", logs)


class TestHuntLeaseBudget(unittest.TestCase):
    """S7：每任务取号次数 × 任务数 的联合上限。"""

    def test_default_limit_clamps_single_task(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(), count=1, max_number_attempts=500
        )
        self.assertEqual(budget["limit"], 200)
        self.assertEqual(budget["max_number_attempts"], 200)
        self.assertEqual(budget["requested_attempts"], 500)
        self.assertEqual(budget["planned_leases"], 200)
        self.assertTrue(budget["clamped"])
        self.assertFalse(budget["rejected"])

    def test_default_limit_clamps_batch_product(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(), count=10, max_number_attempts=500
        )
        self.assertEqual(budget["max_number_attempts"], 20)
        self.assertEqual(budget["planned_leases"], 200)
        self.assertTrue(budget["clamped"])

    def test_within_limit_is_untouched(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(), count=4, max_number_attempts=50
        )
        self.assertEqual(budget["max_number_attempts"], 50)
        self.assertEqual(budget["planned_leases"], 200)
        self.assertFalse(budget["clamped"])

    def test_config_override_limit(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(hunt_max_total_leases=50), count=3, max_number_attempts=100
        )
        self.assertEqual(budget["limit"], 50)
        self.assertEqual(budget["max_number_attempts"], 16)
        self.assertEqual(budget["planned_leases"], 48)
        self.assertTrue(budget["clamped"])

    def test_count_over_limit_is_rejected(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(hunt_max_total_leases=2), count=5, max_number_attempts=10
        )
        self.assertTrue(budget["rejected"])
        self.assertIn("hunt_max_total_leases", budget["message"])

    def test_hunt_disabled_stays_single_lease(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(), count=3, max_number_attempts=None
        )
        self.assertEqual(budget["max_number_attempts"], 1)
        self.assertEqual(budget["planned_leases"], 3)
        self.assertFalse(budget["clamped"])

    def test_garbage_config_falls_back_to_default(self):
        budget = RegistrationOrchestrator.resolve_hunt_lease_budget(
            make_config(hunt_max_total_leases="oops"), count=1, max_number_attempts=300
        )
        self.assertEqual(budget["limit"], 200)
        self.assertEqual(budget["max_number_attempts"], 200)


class TestHuntCancelHttpApi(unittest.TestCase):
    """取消接口与租号预算接口的 HTTP 形状。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        self.manager = RegistrationTaskManager.get_instance()
        self.manager.tasks = {}
        self.manager.batches = {}

    def test_cancel_pending_task_terminates(self):
        task_id = self.manager.create_task()
        res = self.client.post(f"/api/register/tasks/{task_id}/cancel")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["accepted"])
        self.assertTrue(body["terminated"])
        self.assertEqual(body["status"], "canceled")
        self.assertTrue(body["cancel_requested"])

    def test_cancel_running_task_is_accepted_but_not_terminal(self):
        task_id = self.manager.create_task()
        self.manager.update_task_status(task_id, "running")
        res = self.client.post(
            f"/api/register/tasks/{task_id}/cancel", json={"reason": "手工停止"}
        )
        body = res.json()
        self.assertTrue(body["accepted"])
        self.assertFalse(body["terminated"])
        self.assertEqual(body["status"], "running")
        self.assertTrue(any("手工停止" in line for line in body["logs"]))

    def test_cancel_terminal_task_reports_not_accepted(self):
        task_id = self.manager.create_task()
        self.manager.update_task_status(task_id, "success")
        body = self.client.post(f"/api/register/tasks/{task_id}/cancel").json()
        self.assertFalse(body["accepted"])
        self.assertEqual(body["status"], "success")

    def test_cancel_unknown_task_is_404(self):
        self.assertEqual(
            self.client.post("/api/register/tasks/does-not-exist/cancel").status_code, 404
        )
        self.assertEqual(
            self.client.post("/api/register/batches/does-not-exist/cancel").status_code, 404
        )

    def test_cancel_batch_groups_tasks(self):
        batch_id, task_ids = self.manager.create_batch(count=3, concurrency=2)
        self.manager.update_task_status(task_ids[0], "running")
        self.manager.update_task_status(task_ids[1], "failed")
        body = self.client.post(f"/api/register/batches/{batch_id}/cancel").json()
        self.assertEqual(body["requested"], [task_ids[0]])
        self.assertEqual(body["skipped"], [task_ids[1]])
        self.assertEqual(body["terminated"], [task_ids[2]])

    def test_task_detail_exposes_hunt_progress(self):
        task_id = self.manager.create_task()
        self.manager.update_task_status(
            task_id, "running", hunt_attempt=7, hunt_max=100, hunt_scanned=6,
            hunt_blacklisted=5, hunt_last_reason="SENT_CODE_TYPE_APP",
        )
        body = self.client.get(f"/api/register/tasks/{task_id}").json()
        self.assertEqual(body["hunt_attempt"], 7)
        self.assertEqual(body["hunt_max"], 100)
        self.assertEqual(body["hunt_scanned"], 6)
        self.assertEqual(body["hunt_blacklisted"], 5)
        self.assertEqual(body["hunt_last_reason"], "SENT_CODE_TYPE_APP")
        self.assertFalse(body["cancel_requested"])

    def test_hunt_budget_endpoint_reports_clamped_plan(self):
        body = self.client.get(
            "/api/register/hunt-budget",
            params={"count": 10, "max_number_attempts": 500, "check_balance": False},
        ).json()
        self.assertEqual(body["planned_leases"], 200)
        self.assertEqual(body["max_number_attempts"], 20)
        self.assertTrue(body["clamped"])
        self.assertIsNone(body["balance"])

    def test_batch_start_clamps_attempts_and_reports_plan(self):
        with patch.object(
            RegistrationOrchestrator, "run_batch", new=AsyncMock(return_value=None)
        ):
            body = self.client.post(
                "/api/register/batch",
                json={"count": 10, "concurrency": 10, "max_number_attempts": 500},
            ).json()
        self.assertEqual(body["max_number_attempts"], 20)
        self.assertEqual(body["requested_max_number_attempts"], 500)
        self.assertEqual(body["planned_leases"], 200)
        self.assertEqual(body["hunt_max_total_leases"], 200)
        self.assertTrue(body["attempts_clamped"])

    def test_single_start_clamps_attempts(self):
        with patch.object(
            RegistrationOrchestrator, "run_registration", new=AsyncMock(return_value=None)
        ):
            body = self.client.post(
                "/api/register/start", json={"max_number_attempts": 500}
            ).json()
        self.assertEqual(body["max_number_attempts"], 200)
        self.assertEqual(body["planned_leases"], 200)
        self.assertTrue(body["attempts_clamped"])


class TestRegistrarClassmethodDecorators(unittest.TestCase):
    """registrar 里首参为 cls 的方法必须真的挂着 @classmethod。"""

    def test_all_cls_methods_are_classmethods(self):
        source_path = (
            Path(__file__).resolve().parents[1] / "app" / "services" / "registrar.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                args = member.args.posonlyargs + member.args.args
                if not args or args[0].arg != "cls":
                    continue
                decorators = {
                    d.id for d in member.decorator_list if isinstance(d, ast.Name)
                }
                if "classmethod" not in decorators:
                    offenders.append(f"{node.name}.{member.name}")
        self.assertEqual(offenders, [])
