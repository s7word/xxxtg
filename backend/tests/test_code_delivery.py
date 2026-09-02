"""验证码投递通道策略单元测试。"""
from __future__ import annotations

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

from backend.app.services.code_delivery import (  # noqa: E402
    CODE_DELIVERY_BALANCED,
    CODE_DELIVERY_PUSH_REQUIRED,
    CODE_DELIVERY_SMS_FIRST,
    escalation_plan_after_published_flood,
    resolve_code_delivery_plan,
)
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from telethon.errors import ApiIdPublishedFloodError  # noqa: E402
from telethon.tl import types  # noqa: E402


def _profile(api_id=6):
    return {"api_id": api_id, "api_hash": "x"}


def _config(**kwargs):
    defaults = {
        "code_delivery_mode": CODE_DELIVERY_BALANCED,
        "api_credential_mode": "custom",
        "custom_api_id": 35337905,
        "custom_api_hash": "abc",
        "hunt_sms_first_after_app_streak": 2,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestCodeDeliveryPlan(unittest.TestCase):
    def test_balanced_custom_api_skips_push(self):
        plan = resolve_code_delivery_plan(_config(), _profile(api_id=6))
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_SMS_FIRST)
        self.assertFalse(plan.should_request_push_token)
        self.assertFalse(plan.attach_push_token)
        self.assertTrue(plan.can_escalate_on_published_flood)

    def test_balanced_published_official_requires_push(self):
        plan = resolve_code_delivery_plan(
            _config(api_credential_mode="official"),
            _profile(api_id=6),
        )
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)
        self.assertTrue(plan.should_request_push_token)
        self.assertTrue(plan.attach_push_token)
        self.assertTrue(plan.allow_app_hash)

    def test_sms_first_never_attaches_without_escalate(self):
        plan = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_SMS_FIRST),
            _profile(api_id=6),
        )
        self.assertFalse(plan.attach_push_token)

    def test_push_required_always_attaches(self):
        plan = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_PUSH_REQUIRED),
            _profile(api_id=35337905),
        )
        self.assertTrue(plan.attach_push_token)
        self.assertTrue(plan.should_request_push_token)

    def test_allow_app_hash_tracks_device_platform_not_mode(self):
        """allow_app_hash 是 Android SMS Retriever 的短信正文位，不参与通道选择。

        官方 Android 客户端恒设该位，关掉它只会让指纹偏离官方客户端。
        """
        android = {"api_id": 6, "api_hash": "x", "app_device": "Android", "lang_pack": "android"}
        ios = {"api_id": 6, "api_hash": "x", "app_device": "iOS", "device_model": "iPhone 15"}

        for mode in (CODE_DELIVERY_SMS_FIRST, CODE_DELIVERY_BALANCED, CODE_DELIVERY_PUSH_REQUIRED):
            self.assertTrue(
                resolve_code_delivery_plan(_config(code_delivery_mode=mode), android).allow_app_hash,
                mode,
            )
            self.assertFalse(
                resolve_code_delivery_plan(_config(code_delivery_mode=mode), ios).allow_app_hash,
                mode,
            )

    def test_hunt_streak_does_not_skip_push_for_published_id(self):
        """泄露 api_id 被猎号强制 SMS 跳过 attach 只会稳定 FLOOD，不能当样本。"""
        plan = resolve_code_delivery_plan(
            _config(api_credential_mode="official"),
            _profile(api_id=6),
            hunt_app_streak=2,
        )
        self.assertFalse(plan.forced_sms)
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)
        self.assertTrue(plan.should_request_push_token)
        self.assertTrue(plan.attach_push_token)

    def test_official_api4_always_attaches_even_with_sms_first_and_hunt(self):
        plan = resolve_code_delivery_plan(
            _config(
                official_client_emulation=True,
                api_credential_mode="official",
                code_delivery_mode=CODE_DELIVERY_SMS_FIRST,
            ),
            _profile(api_id=4),
            hunt_app_streak=9,
        )
        self.assertTrue(plan.attach_push_token)
        self.assertTrue(plan.should_request_push_token)
        self.assertFalse(plan.forced_sms)
        self.assertIn("attach_token=是", plan.summary_for_log())

    def test_force_skip_push_attach_disables_token_even_for_api4(self):
        plan = resolve_code_delivery_plan(
            _config(
                official_client_emulation=True,
                api_credential_mode="official",
                force_skip_push_attach=True,
            ),
            _profile(api_id=4),
        )
        self.assertFalse(plan.attach_push_token)
        self.assertFalse(plan.should_request_push_token)
        self.assertFalse(plan.can_escalate_on_published_flood)
        self.assertIn("force_skip_push_attach", " ".join(plan.notes))

    def test_hunt_streak_still_forces_sms_for_custom_unpublished(self):
        plan = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_BALANCED),
            _profile(api_id=35337905),
            hunt_app_streak=2,
        )
        self.assertTrue(plan.forced_sms)
        self.assertFalse(plan.attach_push_token)
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_SMS_FIRST)

    def test_official_emulation_forces_push_and_ignores_hunt_streak(self):
        plan = resolve_code_delivery_plan(
            _config(
                official_client_emulation=True,
                api_credential_mode="custom",
                code_delivery_mode=CODE_DELIVERY_BALANCED,
            ),
            _profile(api_id=6),
            hunt_app_streak=5,
        )
        self.assertTrue(plan.official_client_emulation)
        self.assertEqual(plan.emulation_label, "official")
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)
        self.assertTrue(plan.should_request_push_token)
        self.assertTrue(plan.attach_push_token)
        self.assertFalse(plan.forced_sms)
        self.assertIn("official", plan.summary_for_log())

    def test_balanced_label_without_emulation(self):
        plan = resolve_code_delivery_plan(_config(), _profile(api_id=35337905))
        self.assertEqual(plan.emulation_label, "balanced")
        self.assertIn("模式标签=balanced", plan.summary_for_log())

    def test_force_sms_after_app(self):
        plan = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_PUSH_REQUIRED),
            _profile(api_id=6),
            force_sms_after_app=True,
        )
        # push_required 不被猎号覆盖
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)

        plan2 = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_BALANCED),
            _profile(api_id=6),
            force_sms_after_app=True,
        )
        self.assertEqual(plan2.effective_mode, CODE_DELIVERY_SMS_FIRST)

    def test_escalation_after_flood(self):
        base = resolve_code_delivery_plan(_config(), _profile())
        escalated = escalation_plan_after_published_flood(base)
        self.assertTrue(escalated.attach_push_token)
        self.assertTrue(escalated.should_request_push_token)
        self.assertFalse(escalated.can_escalate_on_published_flood)
        self.assertEqual(escalated.allow_app_hash, base.allow_app_hash)


class TestBuildCodeSettings(unittest.TestCase):
    def test_sms_first_settings_no_token(self):
        cs = RegistrationOrchestrator._build_code_settings(
            "FCM_TOKEN",
            allow_app_hash=False,
            attach_push_token=False,
        )
        self.assertIsInstance(cs, types.CodeSettings)
        self.assertFalse(cs.allow_app_hash)
        self.assertFalse(cs.token)
        self.assertIsNone(cs.app_sandbox)

    def test_push_required_settings_with_token(self):
        cs = RegistrationOrchestrator._build_code_settings(
            "FCM_TOKEN",
            allow_app_hash=True,
            attach_push_token=True,
        )
        self.assertTrue(cs.allow_app_hash)
        self.assertEqual(cs.token, "FCM_TOKEN")
        self.assertFalse(cs.app_sandbox)

    def test_firebase_and_unknown_number_flags(self):
        cs = RegistrationOrchestrator._build_code_settings(
            "FCM_TOKEN",
            allow_app_hash=True,
            attach_push_token=True,
            allow_firebase=True,
            unknown_number=True,
        )
        self.assertTrue(cs.allow_firebase)
        self.assertTrue(cs.unknown_number)
        payload = cs._bytes()
        self.assertGreater(len(payload), 0)

    def test_plan_android_enables_firebase_by_default(self):
        plan = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_PUSH_REQUIRED),
            _profile(api_id=4),
        )
        self.assertTrue(plan.allow_firebase)
        self.assertTrue(plan.unknown_number)
        self.assertTrue(plan.force_resend_on_app)


class TestSendCodeRespectingDeliveryPlan(unittest.IsolatedAsyncioTestCase):
    """sendCode 包装层必须原样带回 Push Token 的退款元数据。

    这些字段是 REGHelp setStatus 退款与收码窗口计算的唯一依据，丢一个就退不了款。
    """

    class _Manager:
        def __init__(self):
            self.logs = []

        async def append_log(self, task_id, msg):
            self.logs.append(msg)

        def update_task_status(self, *args, **kwargs):
            pass

    def setUp(self):
        self.manager = self._Manager()
        self.sent_code = object()

    def _patched(self, send_side_effect):
        return patch.multiple(
            RegistrationOrchestrator,
            _send_code_with_recaptcha=AsyncMock(side_effect=send_side_effect),
            resolve_sent_code_channel=AsyncMock(return_value=(self.sent_code, 30)),
        )

    async def _call(self, plan, **overrides):
        kwargs = dict(
            client=None,
            phone="+56911110001",
            profile=_profile(api_id=35337905),
            push_token="TOKEN",
            push_task_id="push-task-1",
            push_provider="reghelp",
            push_token_obtained_at=1234.5,
            delivery_plan=plan,
            bypass_svc=MagicMock(),
            active_proxy=None,
            task_id="task-1",
            manager=self.manager,
            aid="aid-1",
            hunt_enabled=True,
        )
        kwargs.update(overrides)
        return await RegistrationOrchestrator._send_code_respecting_delivery_plan(**kwargs)

    async def test_happy_path_returns_push_metadata_unchanged(self):
        plan = resolve_code_delivery_plan(_config(), _profile(api_id=6))
        with self._patched([self.sent_code]):
            result = await self._call(plan)

        sent, attempts, token, push_task_id, provider, obtained_at, out_plan = result
        self.assertIs(sent, self.sent_code)
        self.assertEqual(attempts, 30)
        self.assertEqual((token, push_task_id, provider, obtained_at),
                         ("TOKEN", "push-task-1", "reghelp", 1234.5))
        self.assertIs(out_plan, plan)

    async def test_escalation_keeps_existing_token_metadata(self):
        plan = resolve_code_delivery_plan(_config(), _profile(api_id=6))
        self.assertTrue(plan.can_escalate_on_published_flood)
        with self._patched([ApiIdPublishedFloodError(request=None), self.sent_code]):
            result = await self._call(plan)

        _, _, token, push_task_id, provider, obtained_at, out_plan = result
        self.assertEqual((token, push_task_id, provider, obtained_at),
                         ("TOKEN", "push-task-1", "reghelp", 1234.5))
        self.assertEqual(out_plan.effective_mode, CODE_DELIVERY_PUSH_REQUIRED)
        self.assertTrue(out_plan.attach_push_token)

    async def test_flood_is_reraised_when_escalation_is_exhausted(self):
        plan = escalation_plan_after_published_flood(
            resolve_code_delivery_plan(_config(), _profile(api_id=6))
        )
        with self._patched([ApiIdPublishedFloodError(request=None)]):
            with self.assertRaises(ApiIdPublishedFloodError):
                await self._call(plan)

    async def test_missing_required_push_token_fails_before_sendcode(self):
        from backend.app.services.registrar import RequiredPushTokenMissingError

        plan = resolve_code_delivery_plan(
            _config(
                official_client_emulation=True,
                api_credential_mode="official",
                code_delivery_mode=CODE_DELIVERY_PUSH_REQUIRED,
            ),
            _profile(api_id=4),
        )
        self.assertTrue(plan.attach_push_token)
        with self._patched([self.sent_code]):
            with self.assertRaises(RequiredPushTokenMissingError) as ctx:
                await self._call(plan, push_token=None, profile=_profile(api_id=4))
            self.assertIn("拒绝以 api_id=4", str(ctx.exception))


class TestPublishedFloodMessage(unittest.TestCase):
    def test_message_distinguishes_attached_token(self):
        from types import SimpleNamespace
        from backend.app.services.registrar import RegistrationOrchestrator

        profile = {"api_id": 4, "api_hash": "014b35b6184100b085b0d0572f9b5103"}
        plan = SimpleNamespace(attach_push_token=True)
        with_token = RegistrationOrchestrator._published_flood_error_message(
            profile, "REGHELP_TOKEN", plan
        )
        self.assertIn("api_id=4", with_token)
        self.assertIn("014b35b6184100b085b0d0572f9b5103", with_token)
        self.assertIn("已 attach", with_token)
        self.assertNotIn("缺少合法 Push Token", with_token)

        without = RegistrationOrchestrator._published_flood_error_message(
            profile, None, plan
        )
        self.assertIn("缺少合法 Push Token", without)


if __name__ == "__main__":
    unittest.main()
