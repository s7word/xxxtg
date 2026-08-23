"""合法开发者 API 凭证申请助手 (Telegram Apps Helper)

标准交互流程：
1. 指定已有 session 账号
2. 向 https://my.telegram.org/auth/send_password 发起登录请求
   （Telegram 会把登录验证码发到该账号已登录的官方客户端，而不是短信）
3. 若存在 Telethon .session，则读取官方账号 777000 的消息提取验证码
4. 完成 Web 登录，访问 /apps 查询或创建 api_id / api_hash
5. 返回给调用方，或一键写入 config.json 的 custom_api_id / custom_api_hash
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException

from backend.app.config import ConfigManager
from backend.app.models.schemas import TelegramAppsJobResponse
from backend.app.services.account_vault import AccountVaultService, normalize_phone
from backend.app.services.net_utils import create_httpx_client

logger = logging.getLogger("TelegramAppsHelper")

MY_TELEGRAM_ORG = "https://my.telegram.org"
TELEGRAM_SYSTEM_USER_ID = 777000

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Origin": MY_TELEGRAM_ORG,
    "Referer": f"{MY_TELEGRAM_ORG}/auth",
}

LOGIN_CODE_PATTERNS = [
    # my.telegram.org Web 登录码现在是字母数字串，不再是 5-6 位纯数字。
    re.compile(r"this is your login code:\s*([A-Za-z0-9_-]{6,24})", re.I),
    re.compile(r"web login code[\s\S]{0,280}?login code:\s*([A-Za-z0-9_-]{6,24})", re.I),
    re.compile(r"login code[:\s]+(\d{5,6})", re.I),
    re.compile(r"web login code[^\d]{0,40}(\d{5,6})", re.I),
    re.compile(r"confirmation code[:\s]+(\d{5,6})", re.I),
    re.compile(r"my\.telegram\.org[^\d]{0,60}(\d{5,6})", re.I),
    re.compile(r"код[^\d]{0,24}(\d{5,6})", re.I),
    re.compile(r"验证码[^\d]{0,12}(\d{5,6})"),
    re.compile(r"(?:^|\n)\s*(\d{5})\s*(?:\n|$)"),
]

API_ID_PATTERNS = [
    re.compile(r'id="app_id"[^>]*value="(\d+)"', re.I),
    re.compile(r'name="app_id"[^>]*value="(\d+)"', re.I),
    re.compile(r"App api_id.*?</label>\s*<[^>]+>(\d+)", re.I | re.S),
    re.compile(r"App api_id[:\s]*</(?:label|strong)>\s*<[^>]+>(\d+)", re.I | re.S),
    re.compile(r"<strong>\s*App api_id\s*</strong>\s*<span[^>]*>\s*(\d+)\s*</span>", re.I),
    re.compile(r"api_id[^0-9]{0,40}(\d{4,10})", re.I),
]

API_HASH_PATTERNS = [
    re.compile(r'id="app_hash"[^>]*value="([a-fA-F0-9]{16,64})"', re.I),
    re.compile(r'name="app_hash"[^>]*value="([a-fA-F0-9]{16,64})"', re.I),
    re.compile(r"App api_hash.*?</label>\s*<[^>]+>([a-fA-F0-9]{16,64})", re.I | re.S),
    re.compile(r"<strong>\s*App api_hash\s*</strong>\s*<span[^>]*>\s*([a-fA-F0-9]{16,64})\s*</span>", re.I),
    re.compile(r"api_hash[^a-fA-F0-9]{0,40}([a-fA-F0-9]{32})", re.I),
]

CREATE_HASH_PATTERNS = [
    re.compile(r'<input[^>]*name="hash"[^>]*value="([a-fA-F0-9]+)"', re.I),
    re.compile(r'<input[^>]*value="([a-fA-F0-9]+)"[^>]*name="hash"', re.I),
    re.compile(r'["\']hash["\']\s*[:=]\s*["\']([a-fA-F0-9]+)["\']', re.I),
]


def extract_login_code(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for pattern in LOGIN_CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def parse_apps_page(html: str) -> Dict[str, Any]:
    """从 my.telegram.org/apps HTML 中提取已有凭证或创建表单 hash。"""
    api_id = None
    api_hash = None
    create_hash = None
    app_title = None

    for pattern in API_ID_PATTERNS:
        match = pattern.search(html)
        if match:
            try:
                api_id = int(match.group(1))
                break
            except ValueError:
                continue

    for pattern in API_HASH_PATTERNS:
        match = pattern.search(html)
        if match:
            api_hash = match.group(1)
            break

    for pattern in CREATE_HASH_PATTERNS:
        match = pattern.search(html)
        if match:
            create_hash = match.group(1)
            break

    title_match = re.search(
        r'(?:id|name)="app_title"[^>]*value="([^"]+)"',
        html,
        re.I,
    )
    if title_match:
        app_title = title_match.group(1)

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "create_hash": create_hash,
        "app_title": app_title,
        "has_create_form": bool(create_hash) and not (api_id and api_hash),
        "raw_length": len(html or ""),
    }


def _telethon_session_base(session_path: Path) -> str:
    text = str(session_path)
    if text.endswith(".session"):
        return text[:-8]
    return text


def _proxy_dict_from_config() -> Optional[Dict[str, Any]]:
    config = ConfigManager.get_instance().config
    proxy = config.fallback_proxy.model_dump() if config.fallback_proxy else None
    if not proxy or not proxy.get("addr") or not proxy.get("port"):
        return None
    if str(proxy.get("proxy_type", "")).lower() in {"direct", "none", ""}:
        return None
    return proxy


class TelegramAppsJobManager:
    """my.telegram.org 申请任务内存状态机。"""

    _instance = None
    jobs: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_instance(cls) -> "TelegramAppsJobManager":
        if cls._instance is None:
            cls._instance = TelegramAppsJobManager()
        return cls._instance

    def create_job(self, account_id: Optional[str], phone: str) -> str:
        job_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now().isoformat()
        self.jobs[job_id] = {
            "job_id": job_id,
            "account_id": account_id or "",
            "phone": phone,
            "status": "pending",
            "logs": [],
            "api_id": None,
            "api_hash": None,
            "app_title": None,
            "created_new_app": False,
            "applied_to_config": False,
            "needs_manual_code": False,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "random_hash": None,
            "cookies": {},
            "app_title_pref": None,
            "app_shortname_pref": None,
        }
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[Dict[str, Any]]:
        return sorted(self.jobs.values(), key=lambda x: x["created_at"], reverse=True)

    async def append_log(self, job_id: str, message: str):
        job = self.jobs.get(job_id)
        if not job:
            return
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{stamp}] {message}"
        job["logs"].append(entry)
        job["updated_at"] = datetime.datetime.now().isoformat()
        logger.info("[%s] %s", job_id, message)

    def update(self, job_id: str, **kwargs):
        job = self.jobs.get(job_id)
        if not job:
            return
        job.update(kwargs)
        job["updated_at"] = datetime.datetime.now().isoformat()

    def to_response(self, job: Dict[str, Any]) -> TelegramAppsJobResponse:
        return TelegramAppsJobResponse(
            job_id=job["job_id"],
            account_id=job.get("account_id"),
            phone=job.get("phone"),
            status=job.get("status", "pending"),
            logs=list(job.get("logs") or []),
            api_id=job.get("api_id"),
            api_hash=job.get("api_hash"),
            app_title=job.get("app_title"),
            created_new_app=bool(job.get("created_new_app")),
            applied_to_config=bool(job.get("applied_to_config")),
            needs_manual_code=bool(job.get("needs_manual_code")),
            error=job.get("error"),
            created_at=job.get("created_at") or "",
            updated_at=job.get("updated_at") or "",
        )


class TelegramAppsHelper:
    """my.telegram.org 官方开发者门户交互助手。"""

    @classmethod
    def _http_client(cls, cookies: Optional[Dict[str, str]] = None) -> httpx.AsyncClient:
        proxy = _proxy_dict_from_config()
        client = create_httpx_client(proxy=proxy, connect_timeout=10.0, total_timeout=30.0)
        client.headers.update(BROWSER_HEADERS)
        if cookies:
            for key, value in cookies.items():
                client.cookies.set(key, value, domain="my.telegram.org")
        return client

    @classmethod
    def _dump_cookies(cls, client: httpx.AsyncClient) -> Dict[str, str]:
        dumped: Dict[str, str] = {}
        for cookie in client.cookies.jar:
            dumped[cookie.name] = cookie.value
        return dumped

    @classmethod
    async def send_login_code(cls, phone: str, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            urljoin(MY_TELEGRAM_ORG, "/auth/send_password"),
            data={"phone": phone},
            headers={
                **BROWSER_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        text = (resp.text or "").strip()
        if resp.status_code >= 400:
            raise RuntimeError(f"my.telegram.org 发送登录码失败 HTTP {resp.status_code}: {text[:300]}")

        random_hash = None
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                random_hash = payload.get("random_hash")
                if payload.get("error"):
                    raise RuntimeError(str(payload.get("error")))
        except ValueError:
            payload = None

        if not random_hash:
            match = re.search(r'"random_hash"\s*:\s*"([^"]+)"', text)
            if match:
                random_hash = match.group(1)

        if not random_hash:
            lowered = text.lower()
            if "flood" in lowered or "too many" in lowered:
                raise RuntimeError(f"my.telegram.org 触发频控: {text[:240]}")
            if "invalid" in lowered or "incorrect" in lowered:
                raise RuntimeError(f"手机号不被 my.telegram.org 接受: {text[:240]}")
            raise RuntimeError(f"my.telegram.org 未返回 random_hash: {text[:240]}")
        return random_hash

    @classmethod
    async def complete_web_login(
        cls,
        phone: str,
        random_hash: str,
        code: str,
        client: httpx.AsyncClient,
    ) -> None:
        resp = await client.post(
            urljoin(MY_TELEGRAM_ORG, "/auth/login"),
            data={
                "phone": phone,
                "random_hash": random_hash,
                "password": code.strip(),
            },
            headers={
                **BROWSER_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{MY_TELEGRAM_ORG}/auth",
            },
        )
        text = (resp.text or "").strip()
        if resp.status_code >= 400:
            raise RuntimeError(f"my.telegram.org 登录失败 HTTP {resp.status_code}: {text[:300]}")
        lowered = text.lower()
        if "invalid" in lowered or "incorrect" in lowered or "error" in lowered:
            if text not in {"true", "1"}:
                raise RuntimeError(f"my.telegram.org 登录被拒绝: {text[:240]}")
        if not client.cookies.jar:
            # 某些响应只回 true，cookie 仍可能已写入
            if text.lower() not in {"true", "1", "ok", ""}:
                logger.warning("登录响应未携带 cookie: %s", text[:200])

    @classmethod
    async def fetch_or_create_apps(
        cls,
        client: httpx.AsyncClient,
        app_title: Optional[str] = None,
        app_shortname: Optional[str] = None,
    ) -> Dict[str, Any]:
        resp = await client.get(
            urljoin(MY_TELEGRAM_ORG, "/apps"),
            headers={
                **BROWSER_HEADERS,
                "Accept": "text/html,application/xhtml+xml",
                "Referer": f"{MY_TELEGRAM_ORG}/",
            },
            follow_redirects=True,
        )
        html = resp.text or ""
        if "auth" in str(resp.url) and "login" in html.lower() and "app_id" not in html.lower():
            raise RuntimeError("my.telegram.org 会话未建立，/apps 被重定向到登录页")

        parsed = parse_apps_page(html)
        if parsed.get("api_id") and parsed.get("api_hash"):
            parsed["created_new_app"] = False
            return parsed

        create_hash = parsed.get("create_hash")
        if not create_hash:
            raise RuntimeError("未能在 /apps 页面解析到已有凭证或创建表单 hash")

        title = (app_title or "EdgeNode Auditor").strip()[:32] or "EdgeNode Auditor"
        shortname = (app_shortname or f"edgenode{random.randint(1000, 9999)}").strip()
        shortname = re.sub(r"[^a-zA-Z0-9_]", "", shortname)[:32] or f"edgenode{random.randint(1000, 9999)}"

        create_resp = await client.post(
            urljoin(MY_TELEGRAM_ORG, "/apps/create"),
            data={
                "hash": create_hash,
                "app_title": title,
                "app_shortname": shortname,
                "app_url": "",
                "app_platform": "android",
                "app_desc": "Personal MTProto development application",
            },
            headers={
                **BROWSER_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Referer": f"{MY_TELEGRAM_ORG}/apps",
                "X-Requested-With": "XMLHttpRequest",
            },
            follow_redirects=True,
        )

        apps_again = await client.get(
            urljoin(MY_TELEGRAM_ORG, "/apps"),
            headers={**BROWSER_HEADERS, "Accept": "text/html"},
            follow_redirects=True,
        )
        parsed = parse_apps_page(apps_again.text or create_resp.text or "")
        parsed["created_new_app"] = True
        parsed["app_title"] = parsed.get("app_title") or title
        if not (parsed.get("api_id") and parsed.get("api_hash")):
            raise RuntimeError(
                "已提交创建应用请求，但未能从 /apps 解析到 api_id / api_hash。"
                "请稍后在 https://my.telegram.org/apps 手动确认。"
            )
        return parsed

    @classmethod
    async def _read_code_via_telethon(
        cls,
        account,
        timeout: float = 90.0,
        since: Optional[datetime.datetime] = None,
    ) -> Optional[str]:
        session_path = AccountVaultService.resolve_session_file(account)
        if not session_path:
            return None
        if not account.app_id or not account.app_hash:
            raise RuntimeError("该账号缺少创建 session 时使用的 app_id/app_hash，无法通过 Telethon 读取验证码")

        from telethon import TelegramClient, events

        proxy = _proxy_dict_from_config()
        client = TelegramClient(
            session=_telethon_session_base(session_path),
            api_id=int(account.app_id),
            api_hash=account.app_hash,
            proxy=proxy,
        )
        deadline = datetime.datetime.now(datetime.timezone.utc)
        since = since or (deadline - datetime.timedelta(seconds=15))

        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Telethon session 未授权，无法读取官方登录验证码")

            async for message in client.iter_messages(TELEGRAM_SYSTEM_USER_ID, limit=8):
                msg_date = message.date
                if msg_date and msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=datetime.timezone.utc)
                if msg_date and msg_date < since - datetime.timedelta(seconds=5):
                    continue
                code = extract_login_code(message.message or getattr(message, "raw_text", "") or "")
                if code:
                    return code

            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()

            @client.on(events.NewMessage(from_users=TELEGRAM_SYSTEM_USER_ID))
            async def _on_official(event):
                code = extract_login_code(event.raw_text or "")
                if code and not future.done():
                    future.set_result(code)

            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                return None
        finally:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass

    @classmethod
    async def run_job(
        cls,
        job_id: str,
        auto_read_code: bool = True,
        apply_to_config: bool = False,
        manual_code: Optional[str] = None,
    ):
        manager = TelegramAppsJobManager.get_instance()
        job = manager.get_job(job_id)
        if not job:
            return

        account = None
        if job.get("account_id"):
            account = AccountVaultService.get_account(job["account_id"])

        phone = (account.phone if account else None) or job.get("phone")
        if not phone:
            manager.update(job_id, status="failed", error="账号缺少手机号")
            await manager.append_log(job_id, "❌ 缺少手机号，无法向 my.telegram.org 发起登录")
            return

        if not account:
            await manager.append_log(
                job_id,
                "未绑定凭证库账号 / 缺少 .session：将向该手机号已登录的 Telegram 客户端发送 Web 登录码，"
                "请在官方号 777000 的消息中查看后于控制台提交。",
            )

        http = cls._http_client(job.get("cookies") or None)
        try:
            random_hash = job.get("random_hash")
            if not random_hash:
                manager.update(job_id, status="sending_code")
                await manager.append_log(job_id, f"向 my.telegram.org 申请登录验证码，账号 {phone}")
                random_hash = await cls.send_login_code(phone, http)
                manager.update(job_id, random_hash=random_hash, cookies=cls._dump_cookies(http))
                await manager.append_log(job_id, "登录验证码已请求，Telegram 会将其发送到该账号已登录的官方客户端")

            code = (manual_code or "").strip() or None
            if not code and auto_read_code and account and account.has_session:
                manager.update(job_id, status="waiting_code", needs_manual_code=False)
                await manager.append_log(job_id, "正在通过 Telethon 读取官方账号 777000 的登录验证码...")
                try:
                    code = await cls._read_code_via_telethon(account)
                except Exception as exc:
                    await manager.append_log(job_id, f"自动读取验证码失败: {exc}")
                    code = None

            if not code:
                manager.update(job_id, status="waiting_code", needs_manual_code=True)
                await manager.append_log(
                    job_id,
                    "未自动获得验证码。请打开该手机号已登录的 Telegram 客户端，"
                    "查看官方号 777000 发来的 my.telegram.org Web 登录码，然后在控制台填入。",
                )
                return

            manager.update(job_id, status="logging_in", needs_manual_code=False)
            await manager.append_log(job_id, "已获得登录验证码，正在完成 my.telegram.org Web 登录")
            await cls.complete_web_login(phone, random_hash, code, http)
            manager.update(job_id, cookies=cls._dump_cookies(http))

            manager.update(job_id, status="fetching_apps")
            await manager.append_log(job_id, "查询 https://my.telegram.org/apps 上的开发者应用")
            apps = await cls.fetch_or_create_apps(
                http,
                app_title=job.get("app_title_pref"),
                app_shortname=job.get("app_shortname_pref"),
            )
            if apps.get("created_new_app"):
                manager.update(job_id, status="creating_app", created_new_app=True)
                await manager.append_log(job_id, "该账号尚未创建应用，已提交创建请求并重新读取凭证")

            api_id = apps.get("api_id")
            api_hash = apps.get("api_hash")
            manager.update(
                job_id,
                api_id=api_id,
                api_hash=api_hash,
                app_title=apps.get("app_title"),
                created_new_app=bool(apps.get("created_new_app")),
            )
            await manager.append_log(job_id, f"成功获取开发者凭证 api_id={api_id}")

            if apply_to_config and api_id and api_hash:
                AccountVaultService.apply_raw_credentials(int(api_id), str(api_hash), set_mode_custom=True)
                manager.update(job_id, applied_to_config=True)
                await manager.append_log(job_id, "已将专属 api_id / api_hash 写入全局 config.json")

            manager.update(job_id, status="success", error=None)
            await manager.append_log(job_id, "🎉 my.telegram.org 开发者凭证申请流程完成")
        except Exception as exc:
            err = str(exc) or repr(exc)
            manager.update(job_id, status="failed", error=err)
            await manager.append_log(job_id, f"❌ {err}")
        finally:
            await http.aclose()

    @classmethod
    def _resolve_start_target(
        cls,
        account_id: Optional[str],
        phone: Optional[str],
    ):
        account = None
        if account_id:
            account = AccountVaultService.get_account(account_id)
            if not account:
                raise HTTPException(status_code=404, detail="Account not found in vault")
        normalized = normalize_phone(phone) if phone else None
        if not account and normalized:
            for item in AccountVaultService.scan_accounts():
                if item.phone == normalized:
                    account = item
                    account_id = item.account_id
                    break
        resolved_phone = (account.phone if account else None) or normalized
        if not resolved_phone:
            raise HTTPException(status_code=400, detail="account_id 或有效 phone 至少提供一个")
        return account, account_id, resolved_phone

    @classmethod
    async def start_job(
        cls,
        account_id: Optional[str] = None,
        phone: Optional[str] = None,
        auto_read_code: bool = True,
        app_title: Optional[str] = None,
        app_shortname: Optional[str] = None,
        apply_to_config: bool = False,
    ) -> TelegramAppsJobResponse:
        account, account_id, resolved_phone = cls._resolve_start_target(account_id, phone)

        manager = TelegramAppsJobManager.get_instance()
        job_id = manager.create_job(account_id, resolved_phone)
        manager.update(
            job_id,
            app_title_pref=app_title,
            app_shortname_pref=app_shortname,
        )
        await manager.append_log(job_id, f"已创建申请任务，目标账号 {resolved_phone}")
        if account and not account.has_session:
            stem = Path(account.filename or (account.phone or "phone")).stem
            await manager.append_log(
                job_id,
                f"⚠️ 该账号缺少同名 .session。自动读取 my.telegram.org 登录码需要 Telethon session："
                f"请将 `{stem}.session` 放到 lod_user/ 或 data/sessions/；"
                "否则请在已登录的 Telegram 客户端查看官方号 777000 的 Web 登录码后在控制台提交。",
            )
        elif account and account.apps_apply_hint:
            await manager.append_log(job_id, account.apps_apply_hint)
        elif not account:
            await manager.append_log(
                job_id,
                "轨 A：无 .session，将向该手机号已登录的 Telegram 客户端发送 Web 登录码，"
                "请在官方号 777000 查看后于本页提交。",
            )
        asyncio.create_task(
            cls.run_job(
                job_id,
                auto_read_code=auto_read_code,
                apply_to_config=apply_to_config,
            )
        )
        return manager.to_response(manager.get_job(job_id))

    @classmethod
    async def submit_code(
        cls,
        job_id: str,
        code: str,
        apply_to_config: bool = False,
    ) -> TelegramAppsJobResponse:
        manager = TelegramAppsJobManager.get_instance()
        job = manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Apps job not found")
        if job.get("status") == "success":
            return manager.to_response(job)
        cleaned = re.sub(r"\D", "", code or "")
        if len(cleaned) < 3:
            raise HTTPException(status_code=400, detail="Invalid confirmation code")
        await manager.append_log(job_id, "已收到手动提交的 my.telegram.org 登录验证码")
        asyncio.create_task(
            cls.run_job(
                job_id,
                auto_read_code=False,
                apply_to_config=apply_to_config,
                manual_code=cleaned,
            )
        )
        return manager.to_response(job)
