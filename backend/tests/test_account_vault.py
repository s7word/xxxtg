"""Account Vault & Telegram Apps Helper — 离线单元 / API 契约测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import (  # noqa: E402
    ApplyVaultCredentialsRequest,
    TelegramAppsStartRequest,
    TelegramAppsSubmitCodeRequest,
    TelegramAppsApplyRequest,
    TelegramAppsJobResponse,
    VaultAccountItem,
    VaultAccountListResponse,
    AppConfigModel,
)
from backend.app.services.account_vault import (  # noqa: E402
    AccountVaultService,
    normalize_phone,
    parse_register_time,
    make_account_id,
)
from backend.app.services.telegram_apps import (  # noqa: E402
    TelegramAppsHelper,
    extract_login_code,
    format_proxy_log,
    parse_apps_page,
    to_telethon_proxy,
)


SAMPLE_APPS_HTML = """
<html>
  <body>
    <label for="app_id">App api_id:</label>
    <input id="app_id" value="12345678" />
    <label for="app_hash">App api_hash:</label>
    <input id="app_hash" value="0123456789abcdef0123456789abcdef" />
    <label>App title</label>
    <input id="app_title" value="EdgeNode Auditor" />
  </body>
</html>
"""

SAMPLE_CREATE_HTML = """
<form action="/apps/create">
  <input type="hidden" name="hash" value="aabbccddeeff00112233445566778899" />
  <input name="app_title" />
</form>
"""

SAMPLE_UNEDITABLE_APPS_HTML = """
<html>
  <head><title>App configuration</title>
  <meta name="apple-itunes-app" content="app-id=686449807">
  </head>
  <body>
    <div class="form-group">
      App api_id:
      <span class="form-control input-xlarge uneditable-input" onclick="this.select();">
        21781234
      </span>
    </div>
    <div class="form-group">
      App api_hash:
      <span class="form-control input-xlarge uneditable-input" onclick="this.select();">
        0a1b2c3d4e5f60718293a4b5c6d7e8f9
      </span>
    </div>
  </body>
</html>
"""

SAMPLE_LOGIN_MESSAGES = [
    "Login code: 48291. Do not give this code to anyone, even if they say they are from Telegram!",
    "Web login code. Telegram (my.telegram.org code).\n48291",
    "Your confirmation code: 90311",
    "Код для входа: 11552",
    (
        "Web login code. Dear Tester, we received a request from your account to log in "
        "on my.telegram.org. This is your login code:\nAbC12-xyZ9\n\nDo not give this code "
        "to anyone, even if they say they are from Telegram."
    ),
]


class TestPhoneAndTimeHelpers(unittest.TestCase):
    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("918310013712"), "+918310013712")
        self.assertEqual(normalize_phone("+91 83100 13712"), "+918310013712")
        self.assertIsNone(normalize_phone(""))
        self.assertIsNone(normalize_phone(None))

    def test_parse_register_time_unix(self):
        readable, unix = parse_register_time(1780306281)
        self.assertEqual(unix, 1780306281)
        self.assertIn("2026", readable)

    def test_account_id_stable(self):
        a = make_account_id("lod_user", "/tmp/a.json")
        b = make_account_id("lod_user", "/tmp/a.json")
        c = make_account_id("lod_user", "/tmp/b.json")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 16)


class TestLodUserVaultScan(unittest.TestCase):
    def test_scan_includes_three_lod_user_accounts(self):
        listing = AccountVaultService.list_accounts()
        self.assertIsInstance(listing, VaultAccountListResponse)
        self.assertGreaterEqual(listing.total, 3)
        phones = {acc.phone for acc in listing.accounts}
        self.assertIn("+918310013712", phones)
        self.assertIn("+918296691905", phones)
        self.assertIn("+918302332054", phones)

        by_phone = {acc.phone: acc for acc in listing.accounts}
        sample = by_phone["+918310013712"]
        self.assertEqual(sample.source, "lod_user")
        self.assertEqual(sample.app_id, 4)
        self.assertEqual(sample.app_hash, "014b35b6184100b085b0d0572f9b5103")
        self.assertTrue(sample.is_published_api_id)
        self.assertFalse(sample.has_usable_custom_credentials)
        self.assertTrue(sample.has_json)
        self.assertIn("samsung", (sample.device_model or "").lower())
        self.assertTrue(sample.register_time)
        self.assertTrue(sample.can_request_new_api_credentials)
        # *.session 受 gitignore 保护：CI 上通常只有 JSON；对话窗口上传后可与同名 JSON 成对出现。
        sibling_session = (
            Path(REPO_ROOT) / "lod_user" / "autoc_sessions_20260823_084149_3" / "918310013712.session"
        )
        if sibling_session.exists():
            self.assertTrue(sample.has_session)
            self.assertFalse(sample.session_missing_for_auto_code)
            self.assertTrue(sample.session_path)
            self.assertTrue(sample.session_path.endswith("918310013712.session"))
            self.assertIn("777000", sample.apps_apply_hint)
        else:
            self.assertFalse(sample.has_session)
            self.assertTrue(sample.session_missing_for_auto_code)
            self.assertIn(".session", sample.apps_apply_hint)
        self.assertGreaterEqual(listing.published_api_id_count, 3)
        self.assertTrue(listing.guidance)
        self.assertIn("custom_api_id", listing.guidance)

    def test_three_target_accounts_pair_session_when_present(self):
        listing = AccountVaultService.list_accounts()
        wanted = {"+918296691905", "+918302332054", "+918310013712"}
        by_phone = {acc.phone: acc for acc in listing.accounts if acc.phone in wanted}
        self.assertEqual(set(by_phone), wanted)
        root = Path(REPO_ROOT) / "lod_user" / "autoc_sessions_20260823_084149_3"
        for phone, stem in (
            ("+918296691905", "918296691905"),
            ("+918302332054", "918302332054"),
            ("+918310013712", "918310013712"),
        ):
            acc = by_phone[phone]
            sess = root / f"{stem}.session"
            js = root / f"{stem}.json"
            self.assertTrue(js.exists(), f"missing sibling json for {stem}")
            if not sess.exists():
                continue
            self.assertTrue(acc.has_session, phone)
            self.assertFalse(acc.session_missing_for_auto_code, phone)
            self.assertTrue(acc.session_path)
            self.assertTrue(acc.session_path.endswith(f"{stem}.session"))
            self.assertTrue((acc.json_path or "").endswith(f"{stem}.json"))
            resolved = AccountVaultService.resolve_session_file(acc)
            self.assertIsNotNone(resolved)
            self.assertTrue(resolved.exists())
            self.assertEqual(resolved.resolve(), sess.resolve())

    def test_new_india_batch_113451_pairs_and_has_session(self):
        listing = AccountVaultService.list_accounts()
        wanted = {"+918176905015", "+918367324489", "+918484878461"}
        by_phone = {acc.phone: acc for acc in listing.accounts if acc.phone in wanted}
        self.assertEqual(set(by_phone), wanted)
        self.assertGreaterEqual(listing.total, 6)
        root = Path(REPO_ROOT) / "lod_user" / "autoc_sessions_20260823_113451_3"
        for phone, stem in (
            ("+918176905015", "918176905015"),
            ("+918367324489", "918367324489"),
            ("+918484878461", "918484878461"),
        ):
            acc = by_phone[phone]
            js = root / f"{stem}.json"
            sess = root / f"{stem}.session"
            self.assertTrue(js.exists(), f"missing sibling json for {stem}")
            self.assertTrue(acc.has_json, phone)
            self.assertTrue(acc.source == "lod_user")
            self.assertTrue(acc.can_request_new_api_credentials)
            self.assertTrue((acc.json_path or "").endswith(f"{stem}.json"))
            if not sess.exists():
                self.assertFalse(acc.has_session)
                self.assertTrue(acc.session_missing_for_auto_code)
                continue
            self.assertTrue(acc.has_session, phone)
            self.assertFalse(acc.session_missing_for_auto_code, phone)
            self.assertTrue(acc.session_path.endswith(f"{stem}.session"))
            resolved = AccountVaultService.resolve_session_file(acc)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), sess.resolve())

    def test_get_account_roundtrip(self):
        listing = AccountVaultService.list_accounts()
        first = listing.accounts[0]
        found = AccountVaultService.get_account(first.account_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.phone, first.phone)


class TestApplyVaultCredentials(unittest.TestCase):
    def test_apply_then_restore_config(self):
        from backend.app.config import ConfigManager, CONFIG_FILE

        manager = ConfigManager.get_instance()
        original = manager.config.model_copy(deep=True)
        listing = AccountVaultService.list_accounts()
        target = next(acc for acc in listing.accounts if acc.phone == "+918310013712")
        try:
            result = AccountVaultService.apply_account_credentials(target.account_id, set_mode_custom=True)
            self.assertTrue(result.success)
            self.assertEqual(result.custom_api_id, 4)
            self.assertEqual(result.custom_api_hash, "014b35b6184100b085b0d0572f9b5103")
            self.assertEqual(result.api_credential_mode, "custom")
            self.assertTrue(result.is_published_api_id)
            self.assertTrue(result.warning)

            reloaded = ConfigManager.get_instance().config
            self.assertEqual(reloaded.custom_api_id, 4)
            self.assertEqual(reloaded.custom_api_hash, target.app_hash)
        finally:
            manager.save_config(original)
            self.assertTrue(CONFIG_FILE.exists())


class TestTelegramAppsParsers(unittest.TestCase):
    def test_extract_login_codes(self):
        self.assertEqual(extract_login_code(SAMPLE_LOGIN_MESSAGES[0]), "48291")
        self.assertEqual(extract_login_code(SAMPLE_LOGIN_MESSAGES[1]), "48291")
        self.assertEqual(extract_login_code(SAMPLE_LOGIN_MESSAGES[2]), "90311")
        self.assertEqual(extract_login_code(SAMPLE_LOGIN_MESSAGES[3]), "11552")
        self.assertEqual(extract_login_code(SAMPLE_LOGIN_MESSAGES[4]), "AbC12-xyZ9")
        self.assertIsNone(extract_login_code("hello world without digits code"))

    def test_parse_existing_apps_html(self):
        parsed = parse_apps_page(SAMPLE_APPS_HTML)
        self.assertEqual(parsed["api_id"], 12345678)
        self.assertEqual(parsed["api_hash"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(parsed["app_title"], "EdgeNode Auditor")
        self.assertFalse(parsed["has_create_form"])

    def test_parse_uneditable_span_apps_html(self):
        parsed = parse_apps_page(SAMPLE_UNEDITABLE_APPS_HTML)
        self.assertEqual(parsed["api_id"], 21781234)
        self.assertEqual(parsed["api_hash"], "0a1b2c3d4e5f60718293a4b5c6d7e8f9")
        self.assertFalse(parsed["has_create_form"])
        # 不把 iTunes app-id=686449807 误当成 api_id
        self.assertNotEqual(parsed["api_id"], 686449807)

    def test_parse_create_form_html(self):
        parsed = parse_apps_page(SAMPLE_CREATE_HTML)
        self.assertEqual(parsed["create_hash"], "aabbccddeeff00112233445566778899")
        self.assertTrue(parsed["has_create_form"])

    def test_telethon_proxy_and_log_for_india_node(self):
        proxy = {
            "proxy_type": "socks5",
            "addr": "res.proxy-seller.com",
            "port": 10003,
            "username": "2f11184ffd63ed46",
            "password": "secret",
            "country_code": "in",
        }
        tg = to_telethon_proxy(proxy)
        self.assertEqual(tg["proxy_type"], "socks5")
        self.assertEqual(tg["addr"], "res.proxy-seller.com")
        self.assertEqual(tg["port"], 10003)
        self.assertEqual(tg["username"], "2f11184ffd63ed46")
        self.assertTrue(tg["rdns"])
        self.assertIn("10003", format_proxy_log(proxy))
        self.assertIn("IN", format_proxy_log(proxy))
        self.assertIsNone(to_telethon_proxy({"proxy_type": "direct"}))


class TestSchemasComplete(unittest.TestCase):
    def test_request_models_accept_expected_payloads(self):
        apply_req = ApplyVaultCredentialsRequest(account_id="abc123", set_mode_custom=True)
        self.assertEqual(apply_req.account_id, "abc123")
        start_req = TelegramAppsStartRequest(account_id="abc123", auto_read_code=True)
        self.assertTrue(start_req.auto_read_code)
        phone_req = TelegramAppsStartRequest(phone="+56971948355")
        self.assertEqual(phone_req.phone, "+56971948355")
        self.assertIsNone(phone_req.account_id)
        submit_req = TelegramAppsSubmitCodeRequest(job_id="job1", code="12345")
        self.assertEqual(submit_req.code, "12345")
        alpha_req = TelegramAppsSubmitCodeRequest(job_id="job1", code="AbC12-xyZ9")
        self.assertEqual(alpha_req.code, "AbC12-xyZ9")
        apply_job = TelegramAppsApplyRequest(job_id="job1")
        self.assertTrue(apply_job.set_mode_custom)
        item = VaultAccountItem(account_id="x", source="lod_user", phone="+1")
        self.assertEqual(item.source, "lod_user")
        cfg = AppConfigModel()
        self.assertIn("custom_api_id", cfg.model_fields)
        self.assertIn("custom_api_hash", cfg.model_fields)
        self.assertIn("job_id", TelegramAppsJobResponse.model_fields)


class TestVaultHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.client = TestClient(app)

    def test_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_list_vault_accounts_api(self):
        res = self.client.get("/api/vault/accounts")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertGreaterEqual(body["total"], 3)
        phones = {acc["phone"] for acc in body["accounts"]}
        self.assertIn("+918310013712", phones)
        first = body["accounts"][0]
        for key in (
            "account_id", "source", "phone", "device_model",
            "app_id", "app_hash", "is_published_api_id", "has_session",
        ):
            self.assertIn(key, first)

    def test_apply_unknown_account(self):
        res = self.client.post(
            "/api/vault/accounts/apply",
            json={"account_id": "does-not-exist", "set_mode_custom": True},
        )
        self.assertEqual(res.status_code, 400)

    def test_apps_start_unknown_account(self):
        res = self.client.post(
            "/api/vault/apps/start",
            json={"account_id": "does-not-exist"},
        )
        self.assertEqual(res.status_code, 404)

    def test_apps_start_requires_account_or_phone(self):
        res = self.client.post("/api/vault/apps/start", json={})
        self.assertEqual(res.status_code, 400)

    def test_resolve_phone_only_and_vault_match(self):
        account, account_id, phone = TelegramAppsHelper._resolve_start_target(None, "56971948355")
        self.assertIsNone(account)
        self.assertEqual(phone, "+56971948355")
        matched, matched_id, matched_phone = TelegramAppsHelper._resolve_start_target(None, "918310013712")
        self.assertIsNotNone(matched)
        self.assertEqual(matched_phone, "+918310013712")
        self.assertTrue(matched_id)

    def test_apps_job_not_found(self):
        res = self.client.get("/api/vault/apps/jobs/not-a-job")
        self.assertEqual(res.status_code, 404)

    def test_apps_job_list(self):
        res = self.client.get("/api/vault/apps/jobs")
        self.assertEqual(res.status_code, 200)
        self.assertIn("jobs", res.json())

    def test_openapi_contains_vault_routes(self):
        res = self.client.get("/openapi.json")
        self.assertEqual(res.status_code, 200)
        paths = res.json().get("paths", {})
        self.assertIn("/api/vault/accounts", paths)
        self.assertIn("/api/vault/accounts/apply", paths)
        self.assertIn("/api/vault/apps/start", paths)
        self.assertIn("/api/vault/apps/submit-code", paths)
        self.assertIn("/api/vault/apps/jobs", paths)
        self.assertIn("/api/vault/apps/apply", paths)

    def test_apply_real_account_and_restore(self):
        from backend.app.config import ConfigManager

        original = ConfigManager.get_instance().config.model_copy(deep=True)
        listing = self.client.get("/api/vault/accounts").json()
        target = next(acc for acc in listing["accounts"] if acc["phone"] == "+918296691905")
        try:
            res = self.client.post(
                "/api/vault/accounts/apply",
                json={"account_id": target["account_id"], "set_mode_custom": True},
            )
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertTrue(body["success"])
            self.assertEqual(body["custom_api_id"], 4)
            cfg = self.client.get("/api/config").json()
            self.assertEqual(cfg["custom_api_id"], 4)
            self.assertEqual(cfg["api_credential_mode"], "custom")
        finally:
            ConfigManager.get_instance().save_config(original)


class TestSessionJsonPairing(unittest.TestCase):
    def test_extra_dir_with_json_and_session_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {
                "phone": "12025550123",
                "register_time": 1700000000,
                "app_id": 12345678,
                "app_hash": "abcdefabcdefabcdefabcdefabcdefab",
                "device": "Pixel 8",
                "sdk": "SDK 34",
                "app_version": "11.0.0 (1)",
                "session_file": "12025550123",
            }
            (root / "12025550123.json").write_text(json.dumps(meta), encoding="utf-8")
            (root / "12025550123.session").write_bytes(b"not-a-real-sqlite-but-present")
            old = os.environ.get("VAULT_EXTRA_DIRS")
            os.environ["VAULT_EXTRA_DIRS"] = str(root)
            try:
                accounts = AccountVaultService.scan_accounts()
                extra = [a for a in accounts if a.phone == "+12025550123"]
                self.assertEqual(len(extra), 1)
                self.assertTrue(extra[0].has_json)
                self.assertTrue(extra[0].has_session)
                self.assertTrue(extra[0].has_usable_custom_credentials)
                self.assertFalse(extra[0].is_published_api_id)
            finally:
                if old is None:
                    os.environ.pop("VAULT_EXTRA_DIRS", None)
                else:
                    os.environ["VAULT_EXTRA_DIRS"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
