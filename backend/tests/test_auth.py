"""控制台 Session 登录：未登录 401、错误密码、正确密码后带 cookie 放行。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

FALLBACK_PASSWORD = "darking"


def _seed_fallback_password() -> None:
    """口令回退链是「环境变量 > data/ 文件 > 随机生成」。

    这里显式把口令文件写进 conftest 隔离出来的 DATA_DIR，用例才不会依赖生产
    data/edgenode_auth_password 恰好是这个值（干净检出时它不存在，get_configured_password
    会随机生成，登录断言必红）。
    """
    from backend.app.services.auth import password_file_path

    path = password_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FALLBACK_PASSWORD + "\n", encoding="utf-8")


def _enable_auth_env() -> None:
    os.environ["EDGENODE_AUTH_DISABLED"] = "0"
    os.environ["EDGENODE_AUTH_SECRET"] = "unit-test-edgenode-auth-secret"
    os.environ.pop("EDGENODE_AUTH_USER", None)
    os.environ.pop("EDGENODE_AUTH_PASSWORD", None)
    _seed_fallback_password()


def _restore_auth_env() -> None:
    os.environ["EDGENODE_AUTH_DISABLED"] = "1"


def _make_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.services.auth import install_auth

    app = FastAPI()
    install_auth(app)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config")
    async def config():
        return {"ok": True}

    return TestClient(app)


class TestAuthCredentials(unittest.TestCase):
    def setUp(self):
        _enable_auth_env()

    def tearDown(self):
        _restore_auth_env()

    def test_fallback_credentials_and_rejects_wrong_password(self):
        from backend.app.services.auth import verify_credentials

        self.assertTrue(verify_credentials("s7word", FALLBACK_PASSWORD))
        self.assertFalse(verify_credentials("s7word", "wrong-password"))
        self.assertFalse(verify_credentials("other", FALLBACK_PASSWORD))

    def test_env_overrides_fallback(self):
        from backend.app.services.auth import verify_credentials

        os.environ["EDGENODE_AUTH_USER"] = "alice"
        os.environ["EDGENODE_AUTH_PASSWORD"] = "secret-from-env"
        try:
            self.assertTrue(verify_credentials("alice", "secret-from-env"))
            self.assertFalse(verify_credentials("s7word", FALLBACK_PASSWORD))
        finally:
            os.environ.pop("EDGENODE_AUTH_USER", None)
            os.environ.pop("EDGENODE_AUTH_PASSWORD", None)

    def test_path_requires_auth(self):
        from backend.app.services.auth import path_requires_auth

        self.assertFalse(path_requires_auth("/api/health"))
        self.assertFalse(path_requires_auth("/api/auth/login"))
        self.assertFalse(path_requires_auth("/hooks/smsall"))
        self.assertFalse(path_requires_auth("/"))
        self.assertFalse(path_requires_auth("/assets/app.js"))
        self.assertTrue(path_requires_auth("/api/config"))
        self.assertTrue(path_requires_auth("/api/auth/me"))
        self.assertTrue(path_requires_auth("/docs"))
        self.assertTrue(path_requires_auth("/redoc"))
        self.assertTrue(path_requires_auth("/openapi.json"))


class TestAuthHttp(unittest.TestCase):
    def setUp(self):
        _enable_auth_env()
        self.client = _make_client()

    def tearDown(self):
        self.client.close()
        _restore_auth_env()

    def test_unauthenticated_config_is_401(self):
        res = self.client.get("/api/config")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get("detail"), "未登录")

    def test_health_is_public(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")

    def test_wrong_password_login_is_401(self):
        res = self.client.post(
            "/api/auth/login",
            json={"username": "s7word", "password": "not-the-password"},
        )
        self.assertEqual(res.status_code, 401)
        body = res.json()
        self.assertFalse(body.get("success"))
        self.assertEqual(body.get("detail"), "用户名或密码错误")
        follow = self.client.get("/api/config")
        self.assertEqual(follow.status_code, 401)

    def test_login_then_me_and_config_ok(self):
        res = self.client.post(
            "/api/auth/login",
            json={"username": "s7word", "password": FALLBACK_PASSWORD},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("username"), "s7word")
        self.assertTrue(self.client.cookies.get("edgenode_session"))

        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        me_body = me.json()
        self.assertTrue(me_body.get("authenticated"))
        self.assertEqual(me_body.get("username"), "s7word")

        cfg = self.client.get("/api/config")
        self.assertEqual(cfg.status_code, 200)

    def test_logout_then_config_401(self):
        login = self.client.post(
            "/api/auth/login",
            json={"username": "s7word", "password": FALLBACK_PASSWORD},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.get("/api/config").status_code, 200)

        out = self.client.post("/api/auth/logout")
        self.assertEqual(out.status_code, 200)

        blocked = self.client.get("/api/config")
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(blocked.json().get("detail"), "未登录")
