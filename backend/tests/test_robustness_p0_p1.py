"""P0 / P1 加固：连接超时、原子配置、代理 URL、SPA 穿越、资源释放、区域匹配、任务容量。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.config import (  # noqa: E402
    ConfigManager,
    atomic_write_config,
    backup_corrupted_config,
    corrupted_config_backup_path,
)
from backend.app.main import resolve_spa_file  # noqa: E402
from backend.app.models.schemas import AppConfigModel  # noqa: E402
from backend.app.services.net_utils import format_httpx_proxy_url  # noqa: E402
from backend.app.services.proxy_manager import format_outbound_proxy_url  # noqa: E402
from backend.app.services.proxyseller import ProxySellerService  # noqa: E402
from backend.app.services.registrar import (  # noqa: E402
    MAX_RETAINED_TASKS,
    RegistrationOrchestrator,
    RegistrationTaskManager,
)
from backend.app.services.telegram_apps import (  # noqa: E402
    MAX_RETAINED_JOBS,
    TelegramAppsJobManager,
    TelethonConnectTimeout,
    connect_telethon_with_timeout,
)


class SlowConnectClient:
    async def connect(self):
        await asyncio.sleep(60)


class TestConnectTimeoutAndRefund(unittest.IsolatedAsyncioTestCase):
    async def test_connect_timeout_marks_failed_and_refunds(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        task_id = manager.create_task()
        sms = AsyncMock()
        sms.cancel = AsyncMock(return_value={"success": True})

        ok = await RegistrationOrchestrator._connect_mtproto(
            SlowConnectClient(),
            task_id,
            manager,
            sms,
            "act-connect-1",
            timeout=0.05,
        )
        self.assertFalse(ok)
        sms.cancel.assert_awaited_once_with("act-connect-1")
        task = manager.get_task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("CONNECT_TIMEOUT", task["error"])
        logs = "\n".join(task["logs"])
        self.assertIn("连接超时", logs)

    async def test_connect_timeout_mark_failed_false_keeps_task_running(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        task_id = manager.create_task()
        manager.update_task_status(task_id, "running")
        sms = AsyncMock()
        sms.cancel = AsyncMock(return_value={"success": True})

        ok = await RegistrationOrchestrator._connect_mtproto(
            SlowConnectClient(),
            task_id,
            manager,
            sms,
            "act-connect-hunt",
            timeout=0.05,
            mark_failed=False,
        )
        self.assertFalse(ok)
        sms.cancel.assert_awaited_once_with("act-connect-hunt")
        task = manager.get_task(task_id)
        self.assertEqual(task["status"], "running")
        self.assertNotIn("CONNECT_TIMEOUT", str(task.get("error") or ""))

    async def test_telethon_apps_connect_timeout_raises_typed_error(self):
        with self.assertRaises(TelethonConnectTimeout):
            await connect_telethon_with_timeout(SlowConnectClient(), timeout=0.05)

    async def test_apps_job_marks_needs_manual_code_on_connect_timeout(self):
        manager = TelegramAppsJobManager()
        manager.jobs = {}
        job_id = manager.create_job("acc-1", "+56900001111")
        manager.update(job_id, needs_manual_code=True, status="waiting_code")
        await manager.append_log(
            job_id,
            "⚠️ Telethon connect() 超时（25s）。已立即标记 needs_manual_code=True，"
            "请在控制台手动提交官方号 777000 的 Web 登录码，避免前端无限等待。",
        )
        job = manager.get_job(job_id)
        self.assertTrue(job["needs_manual_code"])
        self.assertEqual(job["status"], "waiting_code")
        self.assertTrue(any("needs_manual_code=True" in line for line in job["logs"]))


class TestAtomicConfigWrite(unittest.TestCase):
    def test_save_config_uses_tmp_and_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            payload = AppConfigModel(target_country="cl").model_dump()
            replace_calls = []
            real_replace = os.replace

            def _spy_replace(src, dst):
                replace_calls.append((str(src), str(dst)))
                return real_replace(src, dst)

            with patch("backend.app.config.os.replace", side_effect=_spy_replace):
                atomic_write_config(payload, dest)

            self.assertTrue(dest.exists())
            self.assertFalse((Path(tmp) / "config.json.tmp").exists())
            self.assertEqual(len(replace_calls), 1)
            self.assertTrue(replace_calls[0][0].endswith("config.json.tmp"))
            self.assertTrue(replace_calls[0][1].endswith("config.json"))
            disk = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(disk["target_country"], "cl")

    def test_corrupted_config_is_backed_up_before_default_rewrite(self):
        import backend.app.config as cfg_mod

        original_file = cfg_mod.CONFIG_FILE
        original_instance = cfg_mod.ConfigManager._instance
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "config.json"
            dest.write_text("{this is not json", encoding="utf-8")
            cfg_mod.CONFIG_FILE = dest
            cfg_mod.ConfigManager._instance = None
            try:
                mgr = ConfigManager()
                bak = corrupted_config_backup_path(dest)
                self.assertTrue(bak.exists())
                self.assertEqual(bak.read_text(encoding="utf-8"), "{this is not json")
                rewritten = json.loads(dest.read_text(encoding="utf-8"))
                self.assertIn("target_country", rewritten)
                self.assertIsInstance(mgr.config, AppConfigModel)
            finally:
                cfg_mod.CONFIG_FILE = original_file
                cfg_mod.ConfigManager._instance = original_instance

    def test_backup_helper_copies_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "config.json"
            src.write_text('{"keep": true}', encoding="utf-8")
            bak = backup_corrupted_config(src)
            self.assertEqual(bak, Path(tmp) / "config.json.corrupted.bak")
            self.assertEqual(bak.read_text(encoding="utf-8"), '{"keep": true}')


class TestProxyUrlEscaping(unittest.TestCase):
    def test_special_characters_and_ipv6(self):
        url = format_httpx_proxy_url({
            "proxy_type": "socks5",
            "addr": "10.0.0.1",
            "port": 1080,
            "username": "user@name",
            "password": "p:ass/word#x",
        })
        self.assertEqual(
            url,
            "socks5://user%40name:p%3Aass%2Fword%23x@10.0.0.1:1080",
        )
        self.assertIn(quote("user@name", safe=""), url)
        self.assertIn(quote("p:ass/word#x", safe=""), url)
        self.assertNotIn("user@name:", url)

        ipv6 = format_httpx_proxy_url({
            "proxy_type": "http",
            "addr": "2001:db8::1",
            "port": 8080,
            "username": "u",
            "password": "p",
        })
        self.assertEqual(ipv6, "http://u:p@[2001:db8::1]:8080")

        already_bracketed = format_httpx_proxy_url({
            "proxy_type": "socks5",
            "addr": "[2001:db8::2]",
            "port": 1080,
        })
        self.assertEqual(already_bracketed, "socks5://[2001:db8::2]:1080")

    def test_proxy_manager_outbound_helper_matches(self):
        proxy = {
            "proxy_type": "http",
            "addr": "198.51.100.9",
            "port": 8080,
            "username": "web@user",
            "password": "pa:ss#1",
        }
        self.assertEqual(
            format_outbound_proxy_url(proxy),
            format_httpx_proxy_url(proxy),
        )
        self.assertIn("%40", format_outbound_proxy_url(proxy))
        self.assertIn("%3A", format_outbound_proxy_url(proxy))
        self.assertIn("%23", format_outbound_proxy_url(proxy))


class TestSpaPathTraversal(unittest.TestCase):
    def test_blocks_directory_escape_and_serves_inside_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dist"
            (root / "assets").mkdir(parents=True)
            (root / "index.html").write_text("index", encoding="utf-8")
            (root / "assets" / "app.js").write_text("js", encoding="utf-8")
            secret = Path(tmp) / "secret.txt"
            secret.write_text("classified", encoding="utf-8")

            inside = resolve_spa_file("assets/app.js", root)
            self.assertEqual(inside, (root / "assets" / "app.js").resolve())

            self.assertIsNone(resolve_spa_file("../secret.txt", root))
            self.assertIsNone(resolve_spa_file("assets/../../secret.txt", root))
            self.assertIsNone(resolve_spa_file("/etc/passwd", root))
            self.assertIsNone(resolve_spa_file("..", root))
            self.assertIsNone(resolve_spa_file("missing.js", root))


class TestIndependentResourceRelease(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_error_does_not_block_http_close(self):
        class BoomClient:
            def is_connected(self):
                return True

            async def disconnect(self):
                raise RuntimeError("disconnect boom")

        sms = AsyncMock()
        bypass = AsyncMock()
        await RegistrationOrchestrator._release_registration_resources(
            BoomClient(), sms, bypass
        )
        sms.close.assert_awaited_once()
        bypass.close.assert_awaited_once()

    async def test_sms_close_error_still_closes_bypass(self):
        class OkClient:
            def is_connected(self):
                return False

        sms = AsyncMock()
        sms.close = AsyncMock(side_effect=RuntimeError("sms close boom"))
        bypass = AsyncMock()
        await RegistrationOrchestrator._release_registration_resources(
            OkClient(), sms, bypass
        )
        bypass.close.assert_awaited_once()


class TestStrictRegionalProxy(unittest.IsolatedAsyncioTestCase):
    async def test_no_cross_region_assignment_when_target_missing(self):
        ProxySellerService._pool_cache.clear()
        ProxySellerService._health.clear()
        ProxySellerService._rr_cursor.clear()

        usa = {
            "id": "us-1",
            "proxy_type": "socks5",
            "addr": "23.81.44.9",
            "port": 41080,
            "username": "user_us",
            "password": "pass_us",
            "country": "United States",
            "country_code": "us",
            "country_alpha3": "USA",
        }
        svc = ProxySellerService("test-key", include_static=False)
        svc.get_proxy_list = AsyncMock(side_effect=lambda country=None, **kwargs: (
            [] if country in {"id", "cl", "in"} else [usa]
        ))
        svc.ensure_tg_resident_list = AsyncMock(return_value={
            "success": False, "created": False, "proxies": [], "title": None,
        })
        try:
            selected = await svc.select_best_proxy(target_country="id", allow_fallback=True)
            self.assertFalse(selected["success"])
            self.assertIsNone(selected["proxy"])
            self.assertFalse(selected["fallback_used"])
            self.assertIn("禁止跨大区", selected["message"])
            self.assertIn("fallback_proxy", selected["message"])
        finally:
            await svc.close()

    async def test_registrar_degrades_to_fallback_proxy_not_other_region(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        task_id = manager.create_task()

        class FakeSvc:
            def __init__(self, api_key):
                self.api_key = api_key

            async def get_proxy_list(self, country=None, refresh=False, include_health=True):
                return []

            async def select_best_proxy(self, **kwargs):
                return {
                    "success": False,
                    "matched": False,
                    "fallback_used": False,
                    "proxy": None,
                    "hint": "目标区域 ID 暂无可用区域代理",
                    "message": "已禁止跨大区隐式兜底",
                }

            async def close(self):
                return None

        config = SimpleNamespace(
            use_proxy_seller_auto=True,
            proxy_seller_key="demo-key",
            fallback_proxy=SimpleNamespace(model_dump=lambda: {
                "proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808,
            }),
        )
        with patch("backend.app.services.proxyseller.ProxySellerService", FakeSvc):
            resolved = await RegistrationOrchestrator._resolve_proxy_seller_auto(
                config, "id", task_id, manager
            )
        self.assertIsNone(resolved)
        logs = "\n".join(manager.get_task(task_id)["logs"])
        self.assertIn("禁止跨大区隐式兜底", logs)
        self.assertIn("fallback_proxy", logs)
        self.assertNotIn("智能兜底至", logs)


class TestTaskManagerCapacityAndIteration(unittest.TestCase):
    def test_list_tasks_uses_shallow_copy(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        first = manager.create_task()
        second = manager.create_task()
        snapshot = manager.list_tasks()
        self.assertEqual({item["task_id"] for item in snapshot}, {first, second})
        manager.create_task()
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(len(manager.list_tasks()), 3)

    def test_list_tasks_strips_logs_by_default(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        tid = manager.create_task()
        for i in range(8):
            manager.tasks[tid]["logs"].append(f"line-{i}")
        slim = manager.list_tasks()
        self.assertEqual(len(slim), 1)
        self.assertEqual(slim[0]["log_count"], 8)
        self.assertEqual(len(slim[0]["logs"]), 3)
        self.assertTrue(str(slim[0]["last_log"]).endswith("line-7"))
        full = manager.list_tasks(include_logs=True)
        self.assertEqual(len(full[0]["logs"]), 8)
        active = manager.list_tasks(active_task_id=tid)
        self.assertEqual(len(active[0]["logs"]), 8)

    def test_evicts_oldest_completed_when_over_capacity(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        manager.max_retained_tasks = 5
        base = datetime(2026, 1, 1, 0, 0, 0)
        for i in range(5):
            tid = f"old{i}"
            stamp = (base + timedelta(seconds=i)).isoformat()
            manager.tasks[tid] = {
                "task_id": tid,
                "status": "success",
                "created_at": stamp,
                "updated_at": stamp,
                "logs": [],
            }
        new_id = manager.create_task()
        self.assertLessEqual(len(manager.tasks), 5)
        self.assertIn(new_id, manager.tasks)
        self.assertNotIn("old0", manager.tasks)
        self.assertIn("old4", manager.tasks)

    def test_does_not_evict_running_tasks(self):
        manager = RegistrationTaskManager()
        manager.tasks = {}
        manager.max_retained_tasks = 3
        for i in range(3):
            manager.tasks[f"run{i}"] = {
                "task_id": f"run{i}",
                "status": "running",
                "created_at": f"2026-01-01T00:00:0{i}",
                "updated_at": f"2026-01-01T00:00:0{i}",
                "logs": [],
            }
        extra = manager.create_task()
        self.assertIn(extra, manager.tasks)
        self.assertEqual(len(manager.tasks), 4)
        self.assertTrue(all(f"run{i}" in manager.tasks for i in range(3)))

    def test_apps_job_manager_capacity_and_list_copy(self):
        manager = TelegramAppsJobManager()
        manager.jobs = {}
        manager.max_retained_jobs = 4
        base = datetime(2026, 2, 1, 0, 0, 0)
        for i in range(4):
            jid = f"job{i}"
            stamp = (base + timedelta(minutes=i)).isoformat()
            manager.jobs[jid] = {
                "job_id": jid,
                "status": "failed",
                "created_at": stamp,
                "updated_at": stamp,
                "logs": [],
                "phone": f"+56{i}",
            }
        snapshot = manager.list_tasks()
        self.assertEqual(len(snapshot), 4)
        new_id = manager.create_job("acc", "+56911112222")
        self.assertNotIn("job0", manager.jobs)
        self.assertIn(new_id, manager.jobs)
        self.assertLessEqual(len(manager.jobs), 4)
        self.assertEqual(len(snapshot), 4)

    def test_default_capacity_constants(self):
        self.assertEqual(MAX_RETAINED_TASKS, 200)
        self.assertEqual(MAX_RETAINED_JOBS, 200)


class TestCodeSettingsPushTokenPairing(unittest.TestCase):
    """token / app_sandbox 必须同真或同假，否则 Telethon _bytes 会 AssertionError。"""

    def test_with_push_token_serializes_without_assertion(self):
        token = "reghelp-attestation-push-token"
        settings = RegistrationOrchestrator._build_code_settings(token)
        self.assertEqual(settings.token, token)
        self.assertIsInstance(settings.token, str)
        self.assertIs(settings.app_sandbox, False)
        payload = settings._bytes()
        self.assertIsInstance(payload, (bytes, bytearray))
        self.assertGreater(len(payload), 0)

    def test_without_push_token_serializes_without_assertion(self):
        settings = RegistrationOrchestrator._build_code_settings(None)
        self.assertIsNone(settings.token)
        self.assertIsNone(settings.app_sandbox)
        payload = settings._bytes()
        self.assertIsInstance(payload, (bytes, bytearray))
        self.assertGreater(len(payload), 0)

    def test_empty_push_token_treated_as_absent(self):
        settings = RegistrationOrchestrator._build_code_settings("")
        self.assertIsNone(settings.token)
        self.assertIsNone(settings.app_sandbox)
        settings._bytes()

    def test_token_without_app_sandbox_still_raises(self):
        from telethon.tl import types

        broken = types.CodeSettings(
            allow_flashcall=False,
            current_number=False,
            allow_app_hash=True,
            allow_missed_call=False,
            token="only-token-no-sandbox",
        )
        with self.assertRaises(AssertionError) as ctx:
            broken._bytes()
        self.assertIn("token, app_sandbox", str(ctx.exception))


class TestTwoFaOverrideHelpers(unittest.TestCase):
    def test_set_2fa_request_overrides_config(self):
        config = SimpleNamespace(auto_set_2fa=True)
        self.assertFalse(RegistrationOrchestrator._should_set_2fa(config, False))
        self.assertTrue(RegistrationOrchestrator._should_set_2fa(config, True))
        self.assertTrue(RegistrationOrchestrator._should_set_2fa(config, None))
        config.auto_set_2fa = False
        self.assertFalse(RegistrationOrchestrator._should_set_2fa(config, None))
        self.assertTrue(RegistrationOrchestrator._should_set_2fa(config, True))

    def test_edit_2fa_passes_current_password_for_old_account(self):
        fresh = RegistrationOrchestrator._edit_2fa_kwargs("new-secret")
        self.assertEqual(fresh, {"new_password": "new-secret"})
        existing = RegistrationOrchestrator._edit_2fa_kwargs(
            "new-secret", current_password="old-secret"
        )
        self.assertEqual(
            existing,
            {"new_password": "new-secret", "current_password": "old-secret"},
        )


if __name__ == "__main__":
    unittest.main()
