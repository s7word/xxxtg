"""控制台 Session 登录：凭证来自环境变量，校验不写日志。"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse

from backend.app.config import DATA_DIR

logger = logging.getLogger("EdgeNodeAuth")

SESSION_COOKIE_NAME = "edgenode_session"
SESSION_USER_KEY = "username"
SESSION_MAX_AGE = 7 * 24 * 60 * 60
SECRET_FILENAME = "edgenode_auth_secret"
PASSWORD_FILENAME = "edgenode_auth_password"

_FALLBACK_USER = "s7word"

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


def is_auth_disabled() -> bool:
    value = (os.getenv("EDGENODE_AUTH_DISABLED") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_configured_username() -> str:
    return os.getenv("EDGENODE_AUTH_USER") or _FALLBACK_USER


def password_file_path() -> Any:
    return DATA_DIR / PASSWORD_FILENAME


def get_configured_password() -> str:
    """口令来源：环境变量 > data/ 下的免提交文件 > 随机生成并落盘。

    绝不在源码里放明文口令，否则 Git 历史一旦公开就等于交出控制台。
    """
    env = (os.getenv("EDGENODE_AUTH_PASSWORD") or "").strip()
    if env:
        return env

    path = password_file_path()
    try:
        if path.exists():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        logger.warning("读取控制台口令文件失败，将尝试重新生成")

    password = secrets.token_urlsafe(18)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(password + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.warning(
            "未配置 EDGENODE_AUTH_PASSWORD，已生成随机控制台口令并写入 %s（请读取后妥善保存）",
            path,
        )
    except OSError:
        logger.error("无法持久化控制台口令，进程重启后口令会变化，请设置 EDGENODE_AUTH_PASSWORD")
    return password


def _digest_eq(left: str, right: str) -> bool:
    a = (left or "").encode("utf-8")
    b = (right or "").encode("utf-8")
    if len(a) != len(b):
        hmac.compare_digest(a, a)
        return False
    return hmac.compare_digest(a, b)


def verify_credentials(username: str, password: str) -> bool:
    user_ok = _digest_eq(username, get_configured_username())
    pass_ok = _digest_eq(password, get_configured_password())
    return user_ok and pass_ok


def secret_file_path() -> Any:
    return DATA_DIR / SECRET_FILENAME


def resolve_session_secret() -> str:
    env = (os.getenv("EDGENODE_AUTH_SECRET") or "").strip()
    if env:
        return env
    path = secret_file_path()
    try:
        if path.exists():
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        logger.warning("读取 session secret 文件失败，将尝试重新生成")
    secret = secrets.token_urlsafe(48)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        logger.warning("无法持久化 session secret，进程重启后已登录会话会失效")
    return secret


def path_requires_auth(path: str) -> bool:
    """未登录时需要 401 的路径；SPA 静态资源与公开探针除外。"""
    raw = path or "/"
    if raw != "/" and raw.endswith("/"):
        raw = raw.rstrip("/")
    if raw == "/api/health" or raw == "/api/auth/login":
        return False
    if raw == "/hooks/smsall":
        return False
    if raw.startswith("/api/"):
        return True
    if raw in {"/docs", "/redoc", "/openapi.json"}:
        return True
    if raw.startswith("/docs/") or raw.startswith("/redoc/"):
        return True
    return False


def session_username(scope_or_request: Any) -> Optional[str]:
    session = None
    if isinstance(scope_or_request, dict):
        session = scope_or_request.get("session")
    else:
        session = getattr(scope_or_request, "session", None)
    if not isinstance(session, dict):
        return None
    value = session.get(SESSION_USER_KEY)
    if isinstance(value, str) and value:
        return value
    return None


class AuthGateMiddleware:
    """请求级鉴权：EDGENODE_AUTH_DISABLED=1 时直接放行（仅测试）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if is_auth_disabled():
            await self.app(scope, receive, send)
            return
        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        if not path_requires_auth(path):
            await self.app(scope, receive, send)
            return
        if session_username(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"detail": "未登录"}, status_code=401)
        await response(scope, receive, send)


def install_auth(app) -> None:
    """挂载鉴权网关、Session cookie 与 /api/auth/*。可被测试用最小 app 复用。"""
    app.include_router(auth_router)
    app.add_middleware(AuthGateMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolve_session_secret(),
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        path="/",
        same_site="lax",
        https_only=False,
    )


@auth_router.post("/login")
async def login(body: LoginBody, request: Request):
    if not verify_credentials(body.username, body.password):
        return JSONResponse(
            {"success": False, "detail": "用户名或密码错误"},
            status_code=401,
        )
    username = get_configured_username()
    request.session[SESSION_USER_KEY] = username
    return {"success": True, "username": username}


@auth_router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"success": True}


@auth_router.get("/me")
async def me(request: Request):
    username = session_username(request)
    if not username:
        return JSONResponse({"detail": "未登录"}, status_code=401)
    return {"authenticated": True, "username": username}
