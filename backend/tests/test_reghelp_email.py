"""REGHelp 设备配对邮箱 (iCloud Hide My Email / Gmail OAuth) 设备基础设施测试。

覆盖范围：
  - `RegHelpService.get_email` / `get_email_status` / `wait_for_email` / `get_device_email`
    (成功 / error / timeout / 非法邮箱类型)
  - `AttestationGatewayService.request_device_email` 触发策略 (ios_only/always/never)、
    未启用跳过、失败降级不阻塞注册

**重要**: 本测试仅覆盖"设备基础设施层"能力 (与 Push Token / Play Integrity 同层)，
不涉及、也不应涉及 Telegram 账号找回邮箱/2FA 绑定 (账号安全层职责)。
"""
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

from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.attestation_gateway import AttestationGatewayService  # noqa: E402
from backend.app.services.reghelp import RegHelpService  # noqa: E402


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class TestRegHelpEmailClient(unittest.TestCase):
    def _svc(self) -> RegHelpService:
        return RegHelpService("rh-key-12345678", api_bases=["https://api.reghelp.net"])

    def test_get_email_rejects_unknown_type(self):
        svc = self._svc()
        svc.client.get = AsyncMock(side_effect=AssertionError("不应发起网络请求"))
        try:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(svc.get_email("+8613800000000", email_type="yahoo"))
            self.assertIn("不支持", str(ctx.exception))
        finally:
            asyncio.run(svc.close())

    def test_get_email_success_creates_task(self):
        svc = self._svc()
        svc.client.get = AsyncMock(return_value=DummyResponse({
            "id": "email-task-1", "status": "success", "price": 0.5, "balance": 99.5
        }))
        try:
            data = asyncio.run(svc.get_email("+8613800000000", app_name="tg", app_device="iOS", email_type="icloud"))
            self.assertEqual(data["id"], "email-task-1")
            self.assertEqual(data["_used_base"], "https://api.reghelp.net")

            args, kwargs = svc.client.get.await_args
            self.assertTrue(str(args[0]).endswith("/email/getEmail"))
            self.assertEqual(kwargs["params"]["phone"], "+8613800000000")
            self.assertEqual(kwargs["params"]["type"], "icloud")
            self.assertEqual(kwargs["params"]["appDevice"], "iOS")
        finally:
            asyncio.run(svc.close())

    def test_get_email_creation_error_raises(self):
        svc = self._svc()
        svc.client.get = AsyncMock(return_value=DummyResponse({"status": "error", "message": "insufficient balance"}))
        try:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(svc.get_email("+8613800000000"))
            self.assertIn("insufficient balance", str(ctx.exception))
        finally:
            asyncio.run(svc.close())

    def test_get_email_missing_task_id_raises(self):
        svc = self._svc()
        svc.client.get = AsyncMock(return_value=DummyResponse({"status": "success"}))
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(svc.get_email("+8613800000000"))
        finally:
            asyncio.run(svc.close())

    def test_get_email_idempotency_header_passthrough(self):
        svc = self._svc()
        svc.client.get = AsyncMock(return_value=DummyResponse({"id": "t1", "status": "success"}))
        try:
            asyncio.run(svc.get_email("+8613800000000", request_id="req-abc"))
            _, kwargs = svc.client.get.await_args
            self.assertEqual(kwargs["headers"], {"Idempotency-Key": "req-abc"})
        finally:
            asyncio.run(svc.close())

    def test_get_email_status_returns_raw_payload(self):
        svc = self._svc()
        svc.client.get = AsyncMock(return_value=DummyResponse({"id": "t1", "status": "done", "email": "a@icloud.com"}))
        try:
            res = asyncio.run(svc.get_email_status("t1"))
            self.assertEqual(res["email"], "a@icloud.com")
        finally:
            asyncio.run(svc.close())

    def test_wait_for_email_success(self):
        svc = self._svc()
        statuses = [
            {"status": "pending"},
            {"status": "done", "email": "hidden@icloud.com", "code": "654321"},
        ]
        svc.get_email_status = AsyncMock(side_effect=statuses)
        try:
            result = asyncio.run(svc.wait_for_email("t1", interval=0.01, max_attempts=5))
            self.assertEqual(result["email"], "hidden@icloud.com")
            self.assertEqual(result["code"], "654321")
        finally:
            asyncio.run(svc.close())

    def test_wait_for_email_error_status_raises(self):
        svc = self._svc()
        svc.get_email_status = AsyncMock(return_value={"status": "error", "message": "no email quota"})
        try:
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(svc.wait_for_email("t1", interval=0.01, max_attempts=3))
            self.assertIn("no email quota", str(ctx.exception))
        finally:
            asyncio.run(svc.close())

    def test_wait_for_email_done_without_email_raises(self):
        svc = self._svc()
        svc.get_email_status = AsyncMock(return_value={"status": "done"})
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(svc.wait_for_email("t1", interval=0.01, max_attempts=2))
        finally:
            asyncio.run(svc.close())

    def test_wait_for_email_timeout(self):
        svc = self._svc()
        svc.get_email_status = AsyncMock(return_value={"status": "pending"})
        try:
            with self.assertRaises(TimeoutError):
                asyncio.run(svc.wait_for_email("t1", interval=0.01, max_attempts=3))
            self.assertEqual(svc.get_email_status.await_count, 3)
        finally:
            asyncio.run(svc.close())

    def test_get_device_email_combines_create_and_poll(self):
        svc = self._svc()
        svc.get_email = AsyncMock(return_value={"id": "combo-1", "status": "success", "_used_base": "https://api.reghelp.net"})
        svc.wait_for_email = AsyncMock(return_value={"email": "combo@icloud.com", "code": None, "raw": {}})
        try:
            result = asyncio.run(svc.get_device_email("+8613800000000", app_device="iOS"))
            self.assertEqual(result["email"], "combo@icloud.com")
            self.assertEqual(result["task_id"], "combo-1")
            svc.wait_for_email.assert_awaited_once()
            _, kwargs = svc.wait_for_email.await_args
            self.assertEqual(kwargs["api_base"], "https://api.reghelp.net")
        finally:
            asyncio.run(svc.close())

    def test_async_context_manager_closes_client(self):
        async def run():
            async with RegHelpService("rh-key-12345678") as svc:
                svc.client.aclose = AsyncMock()
                self.assertIsInstance(svc, RegHelpService)
            svc.client.aclose.assert_awaited_once()
        asyncio.run(run())


class TestAttestationGatewayDeviceEmail(unittest.TestCase):
    def _config(self, **overrides):
        data = dict(
            reghelp_enabled=True,
            reghelp_api_key="w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR",
            reghelp_base_urls=["https://api.reghelp.net"],
            reghelp_connect_timeout=1.0,
            reghelp_total_timeout=2.0,
            antisafety_enabled=False,
            antisafety_api_key="",
            attestation_provider_mode="reghelp_primary",
            reghelp_email_enabled=True,
            reghelp_email_type="icloud",
            reghelp_email_when="ios_only",
            reghelp_email_app_device=None,
        )
        data.update(overrides)
        return SimpleNamespace(**data)

    def _gateway(self, **config_overrides) -> AttestationGatewayService:
        return AttestationGatewayService(self._config(**config_overrides))

    def test_disabled_returns_none_without_calling_reghelp(self):
        gw = self._gateway(reghelp_email_enabled=False)
        try:
            gw.reghelp.get_device_email = AsyncMock(side_effect=AssertionError("不应调用"))
            result = asyncio.run(gw.request_device_email({"app_device": "iOS"}, "+8613800000000"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_never_policy_returns_none(self):
        gw = self._gateway(reghelp_email_when="never")
        try:
            gw.reghelp.get_device_email = AsyncMock(side_effect=AssertionError("不应调用"))
            result = asyncio.run(gw.request_device_email({"app_device": "iOS"}, "+8613800000000"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_ios_only_skips_android_profile(self):
        gw = self._gateway(reghelp_email_when="ios_only")
        try:
            gw.reghelp.get_device_email = AsyncMock(side_effect=AssertionError("不应调用"))
            result = asyncio.run(gw.request_device_email({"app_device": "Android"}, "+8613800000000"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_ios_only_triggers_for_ios_profile(self):
        gw = self._gateway(reghelp_email_when="ios_only")
        try:
            gw.reghelp.get_device_email = AsyncMock(return_value={"email": "abc@icloud.com", "code": None, "task_id": "t1"})
            result = asyncio.run(gw.request_device_email({"app_device": "iOS", "app_name": "tg"}, "8613800000000"))
            self.assertEqual(result["email"], "abc@icloud.com")

            args, kwargs = gw.reghelp.get_device_email.await_args
            self.assertEqual(args[0], "+8613800000000")  # 自动补齐 E.164 前导 +
            self.assertEqual(kwargs["email_type"], "icloud")
        finally:
            asyncio.run(gw.close())

    def test_always_policy_triggers_for_android_profile(self):
        gw = self._gateway(reghelp_email_when="always")
        try:
            gw.reghelp.get_device_email = AsyncMock(return_value={"email": "xyz@gmail.com", "code": "1234", "task_id": "t2"})
            result = asyncio.run(gw.request_device_email({"app_device": "Android", "app_name": "tg"}, "+8613800000000"))
            self.assertEqual(result["email"], "xyz@gmail.com")
        finally:
            asyncio.run(gw.close())

    def test_failure_degrades_to_none_without_raising(self):
        gw = self._gateway(reghelp_email_when="always")
        try:
            gw.reghelp.get_device_email = AsyncMock(side_effect=RuntimeError("网关暂时不可达"))
            result = asyncio.run(gw.request_device_email({"app_device": "Android"}, "+8613800000000"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_empty_result_returns_none(self):
        gw = self._gateway(reghelp_email_when="always")
        try:
            gw.reghelp.get_device_email = AsyncMock(return_value={"email": None})
            result = asyncio.run(gw.request_device_email({"app_device": "Android"}, "+8613800000000"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_no_reghelp_provider_returns_none(self):
        gw = self._gateway(reghelp_api_key="")
        try:
            self.assertIsNone(gw.reghelp)
            result = asyncio.run(gw.request_device_email({"app_device": "iOS"}, "+8613800000000"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_app_device_override_takes_precedence(self):
        gw = self._gateway(reghelp_email_when="always", reghelp_email_app_device="iOS")
        try:
            gw.reghelp.get_device_email = AsyncMock(return_value={"email": "override@icloud.com"})
            asyncio.run(gw.request_device_email({"app_device": "Android", "app_name": "tg"}, "+8613800000000"))
            _, kwargs = gw.reghelp.get_device_email.await_args
            self.assertEqual(kwargs["app_device"], "iOS")
        finally:
            asyncio.run(gw.close())


class TestAppConfigModelDeviceEmailDefaults(unittest.TestCase):
    def test_defaults_are_conservative(self):
        cfg = AppConfigModel()
        self.assertFalse(cfg.reghelp_email_enabled)
        self.assertEqual(cfg.reghelp_email_type, "icloud")
        self.assertEqual(cfg.reghelp_email_when, "ios_only")
        self.assertIsNone(cfg.reghelp_email_app_device)


if __name__ == "__main__":
    unittest.main(verbosity=2)
