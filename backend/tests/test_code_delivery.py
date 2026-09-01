"""验证码投递通道策略单元测试。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        self.assertFalse(plan.allow_app_hash)
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
        self.assertFalse(plan.allow_app_hash)

    def test_push_required_always_attaches(self):
        plan = resolve_code_delivery_plan(
            _config(code_delivery_mode=CODE_DELIVERY_PUSH_REQUIRED),
            _profile(api_id=35337905),
        )
        self.assertTrue(plan.attach_push_token)
        self.assertTrue(plan.allow_app_hash)
        self.assertTrue(plan.should_request_push_token)

    def test_hunt_streak_forces_sms_even_with_published_id(self):
        plan = resolve_code_delivery_plan(
            _config(api_credential_mode="official"),
            _profile(api_id=6),
            hunt_app_streak=2,
        )
        self.assertTrue(plan.forced_sms)
        self.assertEqual(plan.effective_mode, CODE_DELIVERY_SMS_FIRST)
        self.assertFalse(plan.attach_push_token)

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


if __name__ == "__main__":
    unittest.main()
