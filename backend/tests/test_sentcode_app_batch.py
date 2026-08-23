"""SentCodeTypeApp 降级与并发批量编排单元测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import (  # noqa: E402
    BatchRegisterRequest,
    BatchRegisterResponse,
    BatchStatusResponse,
)
from backend.app.services.registrar import (  # noqa: E402
    DEFAULT_SMS_POLL_ATTEMPTS,
    FAST_FAIL_SMS_POLL_ATTEMPTS,
    MAX_RESEND_WAIT_SECONDS,
    RegistrationOrchestrator,
    RegistrationTaskManager,
    SentCodeAppDeliveryError,
)


def _tl(name: str):
    return type(name, (), {})()


def make_sent_code(type_name: str, next_type_name=None, timeout=None, code_hash="hash-app"):
    return SimpleNamespace(
        type=_tl(type_name),
        next_type=_tl(next_type_name) if next_type_name else None,
        timeout=timeout,
        phone_code_hash=code_hash,
    )


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def __call__(self, req):
        self.calls.append(req)
        if self.error is not None:
            raise self.error
        return self.result


class TestSentCodeHelpers(unittest.TestCase):
    def test_classifies_app_and_sms_channels(self):
        app = make_sent_code("SentCodeTypeApp", "CodeTypeSms", 30)
        sms = make_sent_code("SentCodeTypeSms")
        firebase = make_sent_code("SentCodeTypeFirebaseSms")
        self.assertTrue(RegistrationOrchestrator._is_app_delivery(app))
        self.assertFalse(RegistrationOrchestrator._is_sms_delivery(app))
        self.assertTrue(RegistrationOrchestrator._next_type_is_sms(app))
        self.assertTrue(RegistrationOrchestrator._is_sms_delivery(sms))
        self.assertTrue(RegistrationOrchestrator._is_sms_delivery(firebase))
        self.assertIn("SentCodeTypeApp", RegistrationOrchestrator._describe_sent_code(app))
        self.assertIn("CodeTypeSms", RegistrationOrchestrator._describe_sent_code(app))


class TestResolveSentCodeChannel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self.task_id = self.manager.create_task()

    async def test_sms_passthrough_does_not_resend(self):
        client = FakeClient()
        sent = make_sent_code("SentCodeTypeSms")
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            client, "+56911112222", sent, self.task_id, self.manager
        )
        self.assertIs(result, sent)
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        self.assertEqual(client.calls, [])
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("SentCodeTypeSms", logs)
        self.assertIn("运营商短信", logs)

    async def test_app_without_next_type_fails_fast(self):
        client = FakeClient()
        sent = make_sent_code("SentCodeTypeApp")
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                client, "+56911112222", sent, self.task_id, self.manager
            )
        self.assertEqual(ctx.exception.reason, "SENT_CODE_TYPE_APP")
        self.assertEqual(client.calls, [])
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("已有设备客户端", logs)
        self.assertIn("SentCodeTypeApp", logs)

    async def test_app_resend_to_sms_succeeds(self):
        resent = make_sent_code("SentCodeTypeSms", code_hash="hash-sms")
        client = FakeClient(result=resent)
        sent = make_sent_code("SentCodeTypeApp", "CodeTypeSms", timeout=60)
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            client, "+56911112222", sent, self.task_id, self.manager, wait_timeout=0
        )
        self.assertEqual(result.phone_code_hash, "hash-sms")
        self.assertEqual(attempts, DEFAULT_SMS_POLL_ATTEMPTS)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(type(client.calls[0]).__name__, "ResendCodeRequest")
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("已成功将挑战通道降级/切换为短信分发", logs)
        self.assertIn("auth.resendCode", logs)

    async def test_app_resend_still_app_fails_fast(self):
        resent = make_sent_code("SentCodeTypeApp", "CodeTypeSms", timeout=30)
        client = FakeClient(result=resent)
        sent = make_sent_code("SentCodeTypeApp", "CodeTypeSms", timeout=30)
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                client, "+56911112222", sent, self.task_id, self.manager, wait_timeout=0
            )
        self.assertEqual(ctx.exception.reason, "SENT_CODE_TYPE_APP")
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("重发后服务端仍将验证码下发", logs)

    async def test_app_resend_error_fails_fast(self):
        client = FakeClient(error=RuntimeError("SEND_CODE_UNAVAILABLE"))
        sent = make_sent_code("SentCodeTypeApp", "CodeTypeSms", timeout=30)
        with self.assertRaises(SentCodeAppDeliveryError) as ctx:
            await RegistrationOrchestrator.resolve_sent_code_channel(
                client, "+56911112222", sent, self.task_id, self.manager, wait_timeout=0
            )
        self.assertIn("SEND_CODE_UNAVAILABLE", str(ctx.exception))
        logs = "\n".join(self.manager.get_task(self.task_id)["logs"])
        self.assertIn("auth.resendCode 探测失败", logs)

    async def test_resend_non_sms_uses_short_poll(self):
        resent = make_sent_code("SentCodeTypeCall")
        client = FakeClient(result=resent)
        sent = make_sent_code("SentCodeTypeApp", "CodeTypeCall", timeout=15)
        result, attempts = await RegistrationOrchestrator.resolve_sent_code_channel(
            client, "+56911112222", sent, self.task_id, self.manager, wait_timeout=0
        )
        self.assertEqual(attempts, FAST_FAIL_SMS_POLL_ATTEMPTS)
        self.assertEqual(RegistrationOrchestrator._tl_type_name(result.type), "SentCodeTypeCall")

    async def test_resend_wait_is_capped(self):
        resent = make_sent_code("SentCodeTypeSms")
        client = FakeClient(result=resent)
        sent = make_sent_code("SentCodeTypeApp", "CodeTypeSms", timeout=999)
        with patch("backend.app.services.registrar.asyncio.sleep", new_callable=AsyncMock) as slept:
            await RegistrationOrchestrator._maybe_resend_to_sms(
                client, "+56911112222", sent, self.task_id, self.manager
            )
        slept.assert_awaited()
        self.assertEqual(slept.await_args.args[0], MAX_RESEND_WAIT_SECONDS)


class TestBatchTaskManager(unittest.TestCase):
    def setUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}

    def test_create_batch_and_filter_tasks(self):
        batch_id, task_ids = self.manager.create_batch(
            count=3, concurrency=2, country="cl", app_type="telegram_android"
        )
        self.assertEqual(len(task_ids), 3)
        self.assertEqual(len(set(task_ids)), 3)
        extra = self.manager.create_task()
        self.assertEqual(len(self.manager.list_tasks(batch_id=batch_id)), 3)
        self.assertEqual(len(self.manager.list_tasks()), 4)
        self.assertNotIn(extra, task_ids)
        batch = self.manager.get_batch(batch_id)
        self.assertEqual(batch["count"], 3)
        self.assertEqual(batch["pending"], 3)
        self.assertEqual(batch["status"], "pending")

    def test_batch_status_aggregates(self):
        batch_id, task_ids = self.manager.create_batch(count=3, concurrency=3)
        self.manager.update_task_status(task_ids[0], "success")
        self.manager.update_task_status(task_ids[1], "failed", error="NO_CODE")
        self.manager.update_task_status(task_ids[2], "running")
        batch = self.manager.get_batch(batch_id)
        self.assertEqual(batch["success"], 1)
        self.assertEqual(batch["failed"], 1)
        self.assertEqual(batch["running"], 1)
        self.assertEqual(batch["status"], "running")
        self.manager.update_task_status(task_ids[2], "success")
        batch = self.manager.get_batch(batch_id)
        self.assertEqual(batch["status"], "partial")


class TestBatchScheduler(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = RegistrationTaskManager()
        self.manager.tasks = {}
        self.manager.batches = {}
        self._prev = RegistrationTaskManager._instance
        RegistrationTaskManager._instance = self.manager

    async def asyncTearDown(self):
        RegistrationTaskManager._instance = self._prev

    async def test_semaphore_limits_concurrency(self):
        batch_id, task_ids = self.manager.create_batch(count=5, concurrency=2, country="cl")
        running = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def fake_run(task_id, country=None, app_type=None, proxy_override=None, set_2fa=None):
            nonlocal running, max_seen
            async with lock:
                running += 1
                max_seen = max(max_seen, running)
            await asyncio.sleep(0.04)
            async with lock:
                running -= 1
            self.manager.update_task_status(task_id, "success")

        with patch.object(RegistrationOrchestrator, "run_registration", side_effect=fake_run):
            await RegistrationOrchestrator.run_batch(
                batch_id=batch_id,
                task_ids=task_ids,
                country="cl",
                concurrency=2,
            )
        self.assertEqual(max_seen, 2)
        self.assertLessEqual(max_seen, 2)
        batch = self.manager.get_batch(batch_id)
        self.assertEqual(batch["success"], 5)
        self.assertEqual(batch["status"], "success")
        logs = "\n".join(self.manager.get_task(task_ids[0])["logs"])
        self.assertIn("batch_id=", logs)


class TestBatchSchemas(unittest.TestCase):
    def test_count_and_concurrency_bounds(self):
        ok = BatchRegisterRequest(count=3, concurrency=3, country="cl")
        self.assertEqual(ok.count, 3)
        with self.assertRaises(ValidationError):
            BatchRegisterRequest(count=0)
        with self.assertRaises(ValidationError):
            BatchRegisterRequest(count=11)
        with self.assertRaises(ValidationError):
            BatchRegisterRequest(concurrency=0)
        with self.assertRaises(ValidationError):
            BatchRegisterRequest(concurrency=11)

    def test_response_models_accept_payload(self):
        resp = BatchRegisterResponse(
            batch_id="abcd1234",
            task_ids=["a", "b", "c"],
            count=3,
            concurrency=2,
            status="pending",
            message="ok",
            country="cl",
        )
        self.assertEqual(len(resp.task_ids), 3)
        status = BatchStatusResponse(
            batch_id="abcd1234",
            task_ids=["a", "b", "c"],
            count=3,
            concurrency=2,
            status="running",
            success=1,
            failed=0,
            running=2,
            pending=0,
            created_at="2026-08-23T00:00:00",
            updated_at="2026-08-23T00:00:01",
        )
        self.assertEqual(status.running, 2)


class TestBatchApiRoutes(unittest.IsolatedAsyncioTestCase):
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

    async def test_batch_endpoints_create_and_list(self):
        with patch(
            "backend.app.api.routes.RegistrationOrchestrator.run_batch",
            new_callable=AsyncMock,
        ) as runner:
            res = await self.client.post(
                "/api/register/batch",
                json={"count": 3, "concurrency": 2, "country": "cl", "app_type": "telegram_android"},
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["count"], 3)
            self.assertEqual(len(data["task_ids"]), 3)
            self.assertEqual(data["concurrency"], 2)
            self.assertTrue(data["batch_id"])

            listed = await self.client.get(f"/api/register/tasks?batch_id={data['batch_id']}")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()), 3)

            batch = await self.client.get(f"/api/register/batches/{data['batch_id']}")
            self.assertEqual(batch.status_code, 200)
            self.assertEqual(batch.json()["count"], 3)

            alias = await self.client.post(
                "/api/provision/batch",
                json={"count": 2, "concurrency": 1, "country": "id"},
            )
            self.assertEqual(alias.status_code, 200)
            self.assertEqual(alias.json()["count"], 2)
            self.assertEqual(runner.await_count, 2)


if __name__ == "__main__":
    unittest.main()
