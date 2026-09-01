"""REGHelp Push Token `setStatus` 退款闭环单元测试。

覆盖范围：
- `RegHelpService.get_push_token` 携带 `ref` 发起任务，成功后返回携带
  `task_id`/`provider`/`obtained_at` 的 `PushTokenResult`。
- `RegHelpService.set_push_status` 幂等：网络/平台异常只记录 warning，绝不上抛。
- `RegHelpService.refund_push_token` 按内部失败原因映射表工作，未匹配原因/缺少
  task_id 时跳过。
- `AttestationGatewayService.get_push_token` 透传 `ref` 并向上返回 `(token, task_id,
  provider)`；AntiSafety 路径没有 `setStatus` 能力，`task_id` 恒为 None。
- `AttestationGatewayService.refund_push_token` 仅在持有 REGHelp 客户端时生效。
- `RegistrationOrchestrator._refund_push_token`：provider 非 reghelp / 无 task_id 时跳过；
  超出 180s 窗口仍尝试 setStatus；命中已知失败原因时回写并记录任务日志。
- `RegistrationOrchestrator._sms_poll_attempts_for_push_window`：REGHelp 路径截断短信轮询。
- 端到端：`run_registration` 在 PHONE_NUMBER_BANNED 失败分支下，REGHelp 路径触发
  setStatus(BANNED)，AntiSafety 路径（无 task_id）不触发。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.errors import PhoneNumberBannedError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.attestation_gateway import AttestationGatewayService  # noqa: E402
from backend.app.services.reghelp import (  # noqa: E402
    PUSH_REFUND_MIN_SECONDS,
    PUSH_REFUND_REASON_MAP,
    PUSH_REFUND_WINDOW_SECONDS,
    PUSH_STATUS_VALUES,
    PushTokenResult,
    RegHelpService,
)
from backend.app.services.registrar import (  # noqa: E402
    DEFAULT_SMS_POLL_ATTEMPTS,
    PUSH_REFUND_SETSTATUS_RESERVE_SECONDS,
    SMS_POLL_INTERVAL_SECONDS,
    RegistrationOrchestrator,
    RegistrationTaskManager,
)


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSms:
    def __init__(self):
        self.cancel_calls = []

    async def cancel(self, act_id):
        self.cancel_calls.append(act_id)
        return {"success": True, "act_id": act_id, "status": "bad"}

    async def finish(self, act_id):
        pass

    async def close(self):
        return None


class TestRegHelpGetPushTokenRef(unittest.TestCase):
    """getToken 必须携带 ref，成功后返回 PushTokenResult(token, task_id, provider)。"""

    def test_get_push_token_sends_ref_and_returns_task_id(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        seen_params = {}
        payloads = [
            DummyResponse({"id": "push-123", "status": "success", "price": 0.75, "balance": 10}),
            DummyResponse({"status": "pending"}),
            DummyResponse({"status": "done", "token": "push-token-xyz"}),
        ]

        async def fake_get(url, params=None, headers=None):
            if str(url).endswith("/push/getToken"):
                seen_params.update(params or {})
            return payloads.pop(0)

        svc.client.get = fake_get
        try:
            with patch("backend.app.services.reghelp.asyncio.sleep", new=AsyncMock()):
                result = asyncio.run(svc.get_push_token({"app_name": "tg", "app_device": "Android"}, ref="task-abcd1234"))
            self.assertIsInstance(result, PushTokenResult)
            self.assertEqual(result.token, "push-token-xyz")
            self.assertEqual(result.task_id, "push-123")
            self.assertEqual(result.provider, "reghelp")
            self.assertIsNotNone(result.obtained_at)
            self.assertTrue(bool(result))
            self.assertEqual(seen_params.get("ref"), "task-abcd1234")
        finally:
            asyncio.run(svc.close())

    def test_ref_is_truncated_to_50_chars(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        seen_params = {}
        payloads = [
            DummyResponse({"id": "push-999", "status": "success"}),
            DummyResponse({"status": "done", "token": "tok"}),
        ]

        async def fake_get(url, params=None, headers=None):
            if str(url).endswith("/push/getToken"):
                seen_params.update(params or {})
            return payloads.pop(0)

        svc.client.get = fake_get
        long_ref = "x" * 80
        try:
            with patch("backend.app.services.reghelp.asyncio.sleep", new=AsyncMock()):
                asyncio.run(svc.get_push_token({}, ref=long_ref))
            self.assertEqual(len(seen_params.get("ref")), 50)
        finally:
            asyncio.run(svc.close())

    def test_missing_ref_logs_warning_but_still_requests(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        payloads = [
            DummyResponse({"id": "push-no-ref", "status": "success"}),
            DummyResponse({"status": "done", "token": "tok-no-ref"}),
        ]

        async def fake_get(url, params=None, headers=None):
            return payloads.pop(0)

        svc.client.get = fake_get
        try:
            with patch("backend.app.services.reghelp.asyncio.sleep", new=AsyncMock()), \
                 self.assertLogs("RegHelpService", level="WARNING") as logs:
                result = asyncio.run(svc.get_push_token({}))
            self.assertEqual(result.token, "tok-no-ref")
            self.assertTrue(any("ref" in msg for msg in logs.output))
        finally:
            asyncio.run(svc.close())

    def test_timeout_raises_without_task_id(self):
        """轮询超时（未 done）时不应返回半成品结果，调用方据此保持 push_task_id=None。"""
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        payloads = [DummyResponse({"id": "push-timeout", "status": "success"})]

        async def fake_get(url, params=None, headers=None):
            if str(url).endswith("/push/getToken"):
                return payloads[0]
            return DummyResponse({"status": "wait"})

        svc.client.get = fake_get
        try:
            with patch("backend.app.services.reghelp.asyncio.sleep", new=AsyncMock()):
                with self.assertRaises(TimeoutError):
                    asyncio.run(svc.get_push_token({}, ref="task-1"))
        finally:
            asyncio.run(svc.close())


class TestRegHelpSetPushStatus(unittest.TestCase):
    """setStatus 幂等：成功回传参数正确；异常只 warning，不上抛。"""

    def test_set_push_status_sends_expected_params(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.client.get = AsyncMock(return_value=DummyResponse({"status": "success"}))
        try:
            ok, payload = asyncio.run(svc.set_push_status("push-123", "+56911112222", "BANNED"))
            self.assertTrue(ok)
            self.assertEqual(payload, {"status": "success"})
            args, kwargs = svc.client.get.await_args
            self.assertTrue(str(args[0]).endswith("/push/setStatus"))
            self.assertEqual(kwargs["params"]["id"], "push-123")
            self.assertEqual(kwargs["params"]["number"], "+56911112222")
            self.assertEqual(kwargs["params"]["status"], "BANNED")
        finally:
            asyncio.run(svc.close())

    def test_set_push_status_swallow_network_errors(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.client.get = AsyncMock(side_effect=RuntimeError("boom"))
        try:
            with self.assertLogs("RegHelpService", level="WARNING"):
                ok, payload = asyncio.run(svc.set_push_status("push-123", "+56911112222", "FLOOD"))
            self.assertFalse(ok)
            self.assertIsNone(payload)
        finally:
            asyncio.run(svc.close())

    def test_set_push_status_skips_when_no_task_id(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.client.get = AsyncMock(side_effect=AssertionError("should not be called"))
        try:
            ok, payload = asyncio.run(svc.set_push_status("", "+56911112222", "NOSMS"))
            self.assertFalse(ok)
            self.assertIsNone(payload)
        finally:
            asyncio.run(svc.close())

    def test_set_push_status_rejects_not_found(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.client.get = AsyncMock(return_value=DummyResponse({"detail": "NOT_FOUND"}, status_code=404))
        try:
            with self.assertLogs("RegHelpService", level="WARNING"):
                ok, payload = asyncio.run(svc.set_push_status("push-123", "+17788269045", "NOSMS"))
            self.assertFalse(ok)
            self.assertEqual(payload, {"detail": "NOT_FOUND"})
        finally:
            asyncio.run(svc.close())


class TestInterpretSetStatusResponse(unittest.TestCase):
    def test_success_payload_is_accepted(self):
        ok, verdict = RegHelpService.interpret_set_status_response({"status": "success"}, 200)
        self.assertTrue(ok)
        self.assertIn("受理", verdict)

    def test_not_found_detail_is_rejected(self):
        ok, verdict = RegHelpService.interpret_set_status_response({"detail": "NOT_FOUND"}, 200)
        self.assertFalse(ok)
        self.assertIn("平台拒绝", verdict)
        self.assertIn("NOT_FOUND", verdict)

    def test_http_404_is_rejected(self):
        ok, verdict = RegHelpService.interpret_set_status_response({}, 404)
        self.assertFalse(ok)
        self.assertIn("平台拒绝", verdict)

    def test_error_with_balance_is_accepted(self):
        ok, verdict = RegHelpService.interpret_set_status_response(
            {"status": "error", "id": "ALREADY_REFUNDED", "balance": 7.5}, 200
        )
        self.assertTrue(ok)
        self.assertIn("受理", verdict)


class TestRegHelpRefundReasonMap(unittest.TestCase):
    def test_known_reasons_resolve_expected_status(self):
        self.assertEqual(RegHelpService.resolve_refund_status("PHONE_NUMBER_BANNED"), "BANNED")
        self.assertEqual(RegHelpService.resolve_refund_status("PHONE_PREAUDIT_BANNED"), "BANNED")
        self.assertEqual(RegHelpService.resolve_refund_status("LOCAL_BANNED_PHONE_CACHE"), "BANNED")
        self.assertEqual(RegHelpService.resolve_refund_status("FLOOD_WAIT"), "FLOOD")
        self.assertEqual(RegHelpService.resolve_refund_status("NO_CODE"), "NOSMS")
        self.assertEqual(RegHelpService.resolve_refund_status("SENT_CODE_TYPE_APP"), "NOSMS")
        self.assertEqual(RegHelpService.resolve_refund_status("API_ID_PUBLISHED_FLOOD"), "NOSMS")
        self.assertEqual(RegHelpService.resolve_refund_status("RECAPTCHA_CHECK"), "NOSMS")
        self.assertEqual(RegHelpService.resolve_refund_status("EXCEPTION"), "NOSMS")
        self.assertEqual(RegHelpService.resolve_refund_status("existing_2fa"), "2FA")

    def test_unknown_reason_resolves_to_none(self):
        for reason in ("WRONG_CODE", ""):
            self.assertIsNone(RegHelpService.resolve_refund_status(reason))

    def test_all_mapped_statuses_are_valid_enum_values(self):
        self.assertTrue(set(PUSH_REFUND_REASON_MAP.values()).issubset(PUSH_STATUS_VALUES))

    def test_refund_push_token_calls_set_push_status_with_mapped_status(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.set_push_status = AsyncMock(return_value=(True, {"status": "success"}))
        logs = []

        async def _log(msg):
            logs.append(msg)

        try:
            status = asyncio.run(
                svc.refund_push_token(
                    "push-123", "+56911112222", "PHONE_NUMBER_BANNED",
                    log_callback=_log,
                )
            )
            self.assertEqual(status, "BANNED")
            svc.set_push_status.assert_awaited_once_with("push-123", "+56911112222", "BANNED")
            self.assertTrue(logs)
            self.assertIn("提交成功", logs[0])
            self.assertIn("id=push-123", logs[0])
            self.assertIn("status=BANNED", logs[0])
            self.assertIn("act_id=+56911112222", logs[0])
        finally:
            asyncio.run(svc.close())

    def test_refund_push_token_skips_unmapped_reason(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.set_push_status = AsyncMock(side_effect=AssertionError("must not be called"))
        logs = []

        async def _log(msg):
            logs.append(msg)

        try:
            status = asyncio.run(
                svc.refund_push_token("push-123", "+56911112222", "WRONG_CODE", log_callback=_log)
            )
            self.assertIsNone(status)
            self.assertTrue(logs)
            self.assertIn("未映射", logs[0])
        finally:
            asyncio.run(svc.close())

    def test_refund_push_token_logs_platform_rejection(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.set_push_status = AsyncMock(return_value=(False, {"detail": "NOT_FOUND"}))
        logs = []

        async def _log(msg):
            logs.append(msg)

        try:
            status = asyncio.run(
                svc.refund_push_token("push-123", "+17788269045", "SENT_CODE_TYPE_APP", log_callback=_log)
            )
            self.assertIsNone(status)
            self.assertTrue(logs)
            self.assertIn("平台拒绝", logs[0])
            self.assertIn("NOT_FOUND", logs[0])
        finally:
            asyncio.run(svc.close())

    def test_refund_push_token_skips_without_task_id(self):
        svc = RegHelpService("w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR")
        svc.set_push_status = AsyncMock(side_effect=AssertionError("must not be called"))
        try:
            status = asyncio.run(svc.refund_push_token(None, "+56911112222", "FLOOD_WAIT"))
            self.assertIsNone(status)
        finally:
            asyncio.run(svc.close())


class TestAttestationGatewayPushTokenTuple(unittest.TestCase):
    """AttestationGatewayService.get_push_token 需要透传 ref 并回传 (token, task_id, provider)。"""

    def _config(self, **overrides):
        data = dict(
            reghelp_enabled=True,
            reghelp_api_key="w9vcrhw7pOK0WKBtQLhdjH62eYtRSFbR",
            reghelp_base_urls=["https://api.reghelp.net"],
            reghelp_connect_timeout=1.0,
            reghelp_total_timeout=2.0,
            antisafety_enabled=True,
            antisafety_api_key="as2b21dc7b71b5ce8166a42c22b54566",
            antisafety_base_urls=["https://api.antisafety.net"],
            antisafety_reporting_base_urls=["https://reporting.antisafety.net"],
            antisafety_connect_timeout=1.0,
            antisafety_total_timeout=2.0,
            attestation_provider_mode="reghelp_primary",
        )
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_reghelp_success_returns_task_id_and_provider(self):
        gw = AttestationGatewayService(self._config())
        gw.reghelp.get_push_token = AsyncMock(
            return_value=PushTokenResult(token="tok-1", task_id="push-777", provider="reghelp")
        )
        try:
            token, task_id, provider = asyncio.run(
                gw.get_push_token({"app_name": "tg"}, aid="aid-1", ref="task-9")
            )
            self.assertEqual(token, "tok-1")
            self.assertEqual(task_id, "push-777")
            self.assertEqual(provider, "reghelp")
            _, kwargs = gw.reghelp.get_push_token.await_args
            self.assertEqual(kwargs.get("ref"), "task-9")
        finally:
            asyncio.run(gw.close())

    def test_antisafety_fallback_has_no_task_id(self):
        """REGHelp 失败降级到 AntiSafety 时，AntiSafety 无 setStatus 能力，task_id 恒为 None。"""
        gw = AttestationGatewayService(self._config())
        gw.reghelp.get_push_token = AsyncMock(side_effect=RuntimeError("reghelp down"))
        gw.antisafety.get_push_token = AsyncMock(return_value="anti-token")
        try:
            token, task_id, provider = asyncio.run(
                gw.get_push_token({"app_name": "tg"}, aid="aid-1", ref="task-9")
            )
            self.assertEqual(token, "anti-token")
            self.assertIsNone(task_id)
            self.assertEqual(provider, "antisafety")
        finally:
            asyncio.run(gw.close())

    def test_refund_push_token_skips_when_no_reghelp_client(self):
        gw = AttestationGatewayService(self._config(reghelp_api_key=""))
        try:
            self.assertIsNone(gw.reghelp)
            result = asyncio.run(gw.refund_push_token("push-1", "+56911112222", "PHONE_NUMBER_BANNED"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())

    def test_refund_push_token_delegates_to_reghelp(self):
        gw = AttestationGatewayService(self._config())
        gw.reghelp.refund_push_token = AsyncMock(return_value="BANNED")
        try:
            result = asyncio.run(gw.refund_push_token("push-1", "+56911112222", "PHONE_NUMBER_BANNED"))
            self.assertEqual(result, "BANNED")
            gw.reghelp.refund_push_token.assert_awaited_once()
        finally:
            asyncio.run(gw.close())

    def test_refund_push_token_never_raises(self):
        gw = AttestationGatewayService(self._config())
        gw.reghelp.refund_push_token = AsyncMock(side_effect=RuntimeError("boom"))
        try:
            result = asyncio.run(gw.refund_push_token("push-1", "+56911112222", "PHONE_NUMBER_BANNED"))
            self.assertIsNone(result)
        finally:
            asyncio.run(gw.close())


class TestRefundPushTokenHelper(unittest.IsolatedAsyncioTestCase):
    """RegistrationOrchestrator._refund_push_token：provider/task_id/窗口三重门禁。"""

    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager
        self.task_id = self.manager.create_task()

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_skips_when_provider_is_not_reghelp(self):
        bypass_svc = MagicMock()
        bypass_svc.refund_push_token = AsyncMock(side_effect=AssertionError("must not be called"))
        await RegistrationOrchestrator._refund_push_token(
            bypass_svc, "push-1", "antisafety", None,
            "+56911112222", self.task_id, self.manager, "PHONE_NUMBER_BANNED",
        )

    async def test_skips_when_no_task_id(self):
        bypass_svc = MagicMock()
        bypass_svc.refund_push_token = AsyncMock(side_effect=AssertionError("must not be called"))
        await RegistrationOrchestrator._refund_push_token(
            bypass_svc, None, "reghelp", None,
            "+56911112222", self.task_id, self.manager, "PHONE_NUMBER_BANNED",
        )

    async def test_still_tries_when_beyond_refund_window(self):
        bypass_svc = MagicMock()
        bypass_svc.refund_push_token = AsyncMock(return_value="BANNED")
        stale_obtained_at = __import__("time").monotonic() - (PUSH_REFUND_WINDOW_SECONDS + 5.0)
        await RegistrationOrchestrator._refund_push_token(
            bypass_svc, "push-1", "reghelp", stale_obtained_at,
            "+56911112222", self.task_id, self.manager, "PHONE_NUMBER_BANNED",
        )
        bypass_svc.refund_push_token.assert_awaited_once()
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("超过官方约", logs)
        self.assertIn("仍尝试 setStatus", logs)

    async def test_calls_refund_within_window_and_logs(self):
        bypass_svc = MagicMock()

        async def fake_refund(task_id, phone, reason, log_callback=None):
            if log_callback:
                await log_callback(f"[REGHelp 退款] setStatus id={task_id} status=BANNED act_id={phone}")
            return "BANNED"

        bypass_svc.refund_push_token = AsyncMock(side_effect=fake_refund)
        import time as time_mod

        await RegistrationOrchestrator._refund_push_token(
            bypass_svc, "push-1", "reghelp", time_mod.monotonic() - PUSH_REFUND_MIN_SECONDS,
            "+56911112222", self.task_id, self.manager, "PHONE_NUMBER_BANNED",
        )
        bypass_svc.refund_push_token.assert_awaited_once()
        args, kwargs = bypass_svc.refund_push_token.await_args
        self.assertEqual(args[0], "push-1")
        self.assertEqual(args[1], "+56911112222")
        self.assertEqual(args[2], "PHONE_NUMBER_BANNED")
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("[REGHelp 退款] setStatus id=push-1 status=BANNED act_id=+56911112222", logs)

    async def test_no_window_check_when_obtained_at_missing(self):
        """兼容旧路径：未记录 obtained_at 时不因窗口判断而误跳过。"""
        bypass_svc = MagicMock()
        bypass_svc.refund_push_token = AsyncMock(return_value="FLOOD")
        await RegistrationOrchestrator._refund_push_token(
            bypass_svc, "push-2", "reghelp", None,
            "+56911112222", self.task_id, self.manager, "FLOOD_WAIT",
        )
        bypass_svc.refund_push_token.assert_awaited_once()


    @patch("backend.app.services.registrar.asyncio.sleep", new_callable=AsyncMock)
    async def test_waits_until_min_refund_window(self, mock_sleep):
        bypass_svc = MagicMock()
        bypass_svc.refund_push_token = AsyncMock(return_value="NOSMS")
        obtained_at = __import__("time").monotonic() - 10.0
        with patch.dict(os.environ, {"EDGENODE_SKIP_PUSH_REFUND_WAIT": ""}):
            await RegistrationOrchestrator._refund_push_token(
                bypass_svc, "push-1", "reghelp", obtained_at,
                "+17788269045", self.task_id, self.manager, "SENT_CODE_TYPE_APP",
            )
        mock_sleep.assert_awaited()
        wait_s = mock_sleep.await_args.args[0]
        self.assertGreater(wait_s, 45.0)
        self.assertLessEqual(wait_s, 51.0)
        bypass_svc.refund_push_token.assert_awaited_once()
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("等待", logs)
        self.assertIn("60", logs)


class TestSmsPollAttemptsForPushWindow(unittest.TestCase):
    def test_non_reghelp_keeps_requested_attempts(self):
        self.assertEqual(
            RegistrationOrchestrator._sms_poll_attempts_for_push_window(
                DEFAULT_SMS_POLL_ATTEMPTS, "antisafety", __import__("time").monotonic()
            ),
            DEFAULT_SMS_POLL_ATTEMPTS,
        )

    def test_missing_obtained_at_keeps_requested_attempts(self):
        self.assertEqual(
            RegistrationOrchestrator._sms_poll_attempts_for_push_window(
                DEFAULT_SMS_POLL_ATTEMPTS, "reghelp", None
            ),
            DEFAULT_SMS_POLL_ATTEMPTS,
        )

    def test_reghelp_caps_attempts_inside_window(self):
        import time as time_mod

        obtained_at = time_mod.monotonic() - 40.0
        capped = RegistrationOrchestrator._sms_poll_attempts_for_push_window(
            DEFAULT_SMS_POLL_ATTEMPTS, "reghelp", obtained_at
        )
        remain = PUSH_REFUND_WINDOW_SECONDS - PUSH_REFUND_SETSTATUS_RESERVE_SECONDS - 40.0
        expected = min(DEFAULT_SMS_POLL_ATTEMPTS, max(1, int(remain // SMS_POLL_INTERVAL_SECONDS)))
        self.assertEqual(capped, expected)
        self.assertLess(capped, DEFAULT_SMS_POLL_ATTEMPTS)

    def test_reghelp_near_window_end_uses_one_attempt(self):
        import time as time_mod

        obtained_at = time_mod.monotonic() - (PUSH_REFUND_WINDOW_SECONDS - 5.0)
        capped = RegistrationOrchestrator._sms_poll_attempts_for_push_window(
            DEFAULT_SMS_POLL_ATTEMPTS, "reghelp", obtained_at
        )
        self.assertEqual(capped, 1)


class TestRunRegistrationRefundIntegration(unittest.IsolatedAsyncioTestCase):
    """端到端：PHONE_NUMBER_BANNED 失败分支在 REGHelp 路径下触发 setStatus，AntiSafety 路径不触发。"""

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

    def _config(self):
        return SimpleNamespace(
            target_country="cl",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider="vaksms",
            grizzly_sms_api_key="",
            use_proxy_seller_auto=False,
            fallback_proxy=SimpleNamespace(model_dump=lambda: {
                "proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808,
                "username": None, "password": None,
            }),
            custom_proxies=[],
            phone_precheck_enabled=True,
            api_credential_mode="custom",
            custom_api_id=123456,
            custom_api_hash="hash",
            default_2fa_password="x",
            auto_set_2fa=False,
            # 退款闭环只在真的签发了 Push Token 时存在，固定走 attach Token 的通道模式
            code_delivery_mode="push_required",
        )

    async def _run_banned_scenario(self, *, provider: str, push_task_id):
        sms = FakeSms()
        sms.get_number = AsyncMock(return_value=("act-ban", "+56911112222"))
        gw = MagicMock()
        gw.check_phone_history = AsyncMock(return_value=None)
        gw.get_push_token = AsyncMock(return_value=("TOKEN", push_task_id, provider))
        gw.close = AsyncMock()
        gw.report_result = AsyncMock()

        async def fake_refund_push_token(refund_task_id, phone, reason, log_callback=None):
            if not push_task_id:
                return None
            status = "BANNED"
            if log_callback:
                await log_callback(f"[REGHelp 退款] setStatus id={refund_task_id} status={status} act_id={phone}")
            return status

        gw.refund_push_token = AsyncMock(side_effect=fake_refund_push_token)

        clean = SimpleNamespace(intercept=False, is_registered=False, degraded=False, reason="", user_id=None)

        cfg_mgr = SimpleNamespace(config=self._config())
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.registrar.VakSmsService", return_value=sms), \
             patch("backend.app.services.registrar.AttestationGatewayService", return_value=gw), \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value=self._profile()), \
             patch("backend.app.services.registrar.BannedPhonesCache.lookup", return_value=None), \
             patch("backend.app.services.registrar.BannedPhonesCache.remember"), \
             patch("backend.app.services.registrar.PhonePrecheckService.check_phone", new=AsyncMock(return_value=clean)), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)), \
             patch.object(
                 RegistrationOrchestrator,
                 "_send_code_with_recaptcha",
                 new=AsyncMock(side_effect=PhoneNumberBannedError(request=None)),
             ), \
             patch.object(RegistrationOrchestrator, "_connect_mtproto", new=AsyncMock(return_value=True)), \
             patch.object(RegistrationOrchestrator, "perform_handshake", new=AsyncMock()), \
             patch.object(RegistrationOrchestrator, "_release_registration_resources", new=AsyncMock()), \
             patch("backend.app.services.registrar.TelegramClient") as tg_cls:
            client = MagicMock()
            client.is_connected = lambda: False
            client.disconnect = AsyncMock()
            tg_cls.return_value = client
            await RegistrationOrchestrator.run_registration(task_id=self.task_id, country="cl")

        return gw, sms

    async def test_reghelp_path_triggers_setstatus_banned(self):
        gw, sms = await self._run_banned_scenario(provider="reghelp", push_task_id="push-777")

        gw.refund_push_token.assert_awaited_once()
        args, kwargs = gw.refund_push_token.await_args
        self.assertEqual(args[0], "push-777")
        self.assertEqual(args[1], "+56911112222")
        self.assertEqual(args[2], "PHONE_NUMBER_BANNED")
        self.assertEqual(sms.cancel_calls, ["act-ban"])
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        logs = "\n".join(task["logs"])
        self.assertIn("[REGHelp 退款]", logs)

    async def test_antisafety_path_never_calls_setstatus(self):
        """AntiSafety 提供源没有 task_id，退款闭环必须跳过（无 setStatus 能力）。"""
        gw, sms = await self._run_banned_scenario(provider="antisafety", push_task_id=None)

        gw.refund_push_token.assert_not_awaited()
        self.assertEqual(sms.cancel_calls, ["act-ban"])
        task = self.manager.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
