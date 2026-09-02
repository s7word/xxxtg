"""手动单号注册调试控制台：两阶段交互式状态机。

与全自动引导共享设备指纹、代理配对、Attestation Push Token、
自建 API 凭证与 MTProto 握手；差异点仅在于：
- 不向接码平台租号，直接使用用户填写的手机号调用 auth.sendCode
- 发码成功后保持 MTProto 连接，状态机进入 waiting_code
- 由控制台人工提交验证码后完成 signIn / signUp、2FA 与凭证落盘
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.errors import (
    ApiIdPublishedFloodError,
    FloodWaitError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    SessionPasswordNeededError,
)
from telethon.tl import functions, types

from backend.app.config import ConfigManager, SESSIONS_DIR
from backend.app.services.account_vault import normalize_phone
from backend.app.services.attestation_gateway import AttestationGatewayService
from backend.app.services.banned_phones import BannedPhonesCache, SOURCE_TELEGRAM_RPC
from backend.app.services.code_delivery import (
    escalation_plan_after_published_flood,
    resolve_code_delivery_plan,
)
from backend.app.services.device_profile import DeviceProfileManager
from backend.app.services.init_connection import (
    apply_init_connection_overrides,
    describe_init_connection,
)
from backend.app.services.phone_precheck import PhonePrecheckService
from backend.app.services.proxyseller import infer_country_from_phone
from backend.app.services.recaptcha_check import RecaptchaChallengeError, parse_recaptcha_check
from backend.app.services.registrar import (
    CONNECT_TIMEOUT_SECONDS,
    RegistrationOrchestrator,
    RegistrationTaskManager,
)

logger = logging.getLogger("ManualRegistrationConsole")

MANUAL_CODE_WAIT_SECONDS = 480  # 8 分钟，落在 5~10 分钟有效窗口内
MANUAL_PHONE_MIN_DIGITS = 8
MANUAL_PHONE_MAX_DIGITS = 15
RETRYABLE_CODE_ERRORS = (PhoneCodeInvalidError, PhoneCodeEmptyError)


class ManualRegisterError(Exception):
    """可映射为 HTTP 4xx 的输入/状态错误。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_manual_phone(raw: Optional[str]) -> str:
    """将用户输入规范为 +E.164；非法输入抛 ManualRegisterError。"""
    normalized = normalize_phone(raw)
    if not normalized:
        raise ManualRegisterError("请填写有效手机号，例如 +9647706110434 或 628123456789")
    digits = normalized.lstrip("+")
    if not digits.isdigit():
        raise ManualRegisterError("手机号只能包含数字与可选的 + 前缀")
    if len(digits) < MANUAL_PHONE_MIN_DIGITS or len(digits) > MANUAL_PHONE_MAX_DIGITS:
        raise ManualRegisterError(
            f"手机号位数异常（{len(digits)}），国际号码应为 {MANUAL_PHONE_MIN_DIGITS}~{MANUAL_PHONE_MAX_DIGITS} 位数字"
        )
    return normalized


def _infer_country_from_geo_dial(phone: str) -> Optional[str]:
    """用全球区号目录补全 Proxy-Seller 前缀表未覆盖的国家（如 +964 伊拉克）。"""
    from backend.app.services.geo_catalog import _ISO2_CORE

    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return None
    best_iso: Optional[str] = None
    best_len = 0
    for iso2, row in _ISO2_CORE.items():
        dial = str(row[3] if len(row) > 3 else "").lstrip("+")
        if dial and digits.startswith(dial) and len(dial) > best_len:
            best_iso = str(iso2).strip().lower()
            best_len = len(dial)
    return best_iso


def resolve_manual_country(
    phone: str,
    country: Optional[str] = None,
    fallback: Optional[str] = None,
) -> str:
    """优先使用显式国家，其次从手机号推断，最后回落全局默认。"""
    explicit = str(country or "").strip().lower()
    if explicit:
        return explicit
    inferred = infer_country_from_phone(phone) or _infer_country_from_geo_dial(phone)
    if inferred:
        return str(inferred).strip().lower()
    if fallback:
        return str(fallback).strip().lower()
    return "cl"


def session_artifact_paths(phone: str) -> tuple[Path, Path, str]:
    """标准凭证对：data/sessions/{digits}.session + {digits}.json。"""
    digits = str(phone or "").replace("+", "").strip()
    filename = f"{digits}.session"
    return SESSIONS_DIR / filename, SESSIONS_DIR / f"{digits}.json", filename


def _delivery_type_name(sent_code: Any) -> str:
    return RegistrationOrchestrator._sent_code_type_name(sent_code)


def _task_logs(task_id: str) -> List[str]:
    task = RegistrationTaskManager.get_instance().get_task(task_id) or {}
    return list(task.get("logs") or [])


@dataclass
class ManualLiveSession:
    """内存中保持的未完成 MTProto 连接。"""

    task_id: str
    client: Any
    phone: str
    phone_code_hash: str
    delivery_type: str
    profile: Dict[str, Any]
    config: Any
    set_2fa: Optional[bool]
    bypass_svc: Any
    check_id: Optional[str]
    aid: Any
    target_country: str
    session_path: Path
    meta_path: Path
    session_filename: str
    push_task_id: Optional[str] = None
    push_provider: Optional[str] = None
    push_token_obtained_at: Optional[float] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    expires_at: Optional[datetime.datetime] = None
    expire_task: Optional[asyncio.Task] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ManualSessionStore:
    """按 task_id 持有等待验证码的活跃连接。"""

    _instance: Optional["ManualSessionStore"] = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        self._sessions: Dict[str, ManualLiveSession] = {}

    @classmethod
    def get_instance(cls) -> "ManualSessionStore":
        if cls._instance is None:
            cls._instance = ManualSessionStore()
        return cls._instance

    def put(self, session: ManualLiveSession) -> None:
        with self._lock:
            self._sessions[session.task_id] = session

    def get(self, task_id: str) -> Optional[ManualLiveSession]:
        with self._lock:
            return self._sessions.get(task_id)

    def pop(self, task_id: str) -> Optional[ManualLiveSession]:
        with self._lock:
            return self._sessions.pop(task_id, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def task_ids(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def find_task_ids_by_phone(self, phone: str) -> List[str]:
        """返回当前仍持有活跃 MTProto 连接、且号码匹配的 task_id 列表（用于同号去重）。"""
        with self._lock:
            return [
                tid
                for tid, sess in self._sessions.items()
                if sess.phone == phone
            ]


class ManualRegistrationOrchestrator:
    """手动单号调试：发码 → 等待验证码 → 提交 / 取消。"""

    wait_seconds = MANUAL_CODE_WAIT_SECONDS

    @classmethod
    def _snapshot_start(
        cls,
        task_id: str,
        status: str,
        phone: Optional[str],
        message: str,
        *,
        phone_code_hash: Optional[str] = None,
        delivery_type: Optional[str] = None,
        country: Optional[str] = None,
        expires_at: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "task_id": task_id,
            "status": status,
            "phone": phone,
            "phone_code_hash": phone_code_hash,
            "delivery_type": delivery_type,
            "message": message,
            "logs": _task_logs(task_id),
            "country": country,
            "expires_at": expires_at,
            "error": error,
        }

    @classmethod
    def _snapshot_submit(
        cls,
        task_id: str,
        status: str,
        phone: Optional[str],
        message: str,
        *,
        user_id: Optional[int] = None,
        session_file: Optional[str] = None,
        account_kind: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "task_id": task_id,
            "status": status,
            "phone": phone,
            "user_id": user_id,
            "message": message,
            "session_file": session_file,
            "account_kind": account_kind,
            "logs": _task_logs(task_id),
            "error": error,
        }

    @classmethod
    async def _release_live(
        cls,
        session: Optional[ManualLiveSession],
        *,
        unlink_artifacts: bool = False,
    ) -> None:
        if session is None:
            return
        if session.expire_task and not session.expire_task.done():
            session.expire_task.cancel()
        await RegistrationOrchestrator._release_registration_resources(
            session.client, None, session.bypass_svc
        )
        if unlink_artifacts:
            for path in (session.session_path, session.meta_path):
                try:
                    if path.exists():
                        path.unlink()
                except OSError as exc:
                    logger.warning("清理未完成凭证失败 %s: %s", path, exc)

    @classmethod
    async def _cancel_conflicting_sessions(
        cls, phone: str, manager: RegistrationTaskManager
    ) -> List[str]:
        """同号重复 start 前：先取消同号仍处于 waiting_code/logging_in 的旧手动任务。

        单活跃手动槽策略——同一手机号任意时刻只保留一个存活的 waiting_code/logging_in
        任务，避免用户反复点击「发送验证码」时堆出多个僵尸任务占用任务队列。
        """
        store = ManualSessionStore.get_instance()
        candidate_ids = set(store.find_task_ids_by_phone(phone))
        for task in manager.list_tasks():
            if (
                task.get("mode") == "manual"
                and task.get("phone") == phone
                and (task.get("status") or "") in {"waiting_code", "logging_in"}
            ):
                candidate_ids.add(task.get("task_id"))

        canceled_ids: List[str] = []
        for tid in candidate_ids:
            task = manager.get_task(tid)
            if not task or task.get("phone") != phone:
                continue
            if (task.get("status") or "") not in {"waiting_code", "logging_in"}:
                continue
            try:
                await manager.append_log(
                    tid,
                    f"⚠️ 检测到号码 {phone} 发起了新的手动发码请求，本任务作为重复/过期会话被自动取消",
                )
                await cls.cancel(tid)
                canceled_ids.append(tid)
            except ManualRegisterError as exc:
                logger.warning("同号去重取消旧任务 %s 失败（忽略）: %s", tid, exc)
        return canceled_ids

    @classmethod
    async def _expire_waiting(cls, task_id: str, wait_seconds: float) -> None:
        try:
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            return
        store = ManualSessionStore.get_instance()
        session = store.get(task_id)
        if session is None:
            return
        manager = RegistrationTaskManager.get_instance()
        task = manager.get_task(task_id) or {}
        if (task.get("status") or "") != "waiting_code":
            return
        popped = store.pop(task_id)
        await manager.append_log(
            task_id,
            f"❌ 等待人工输入验证码超时（{int(wait_seconds)}s），已安全释放 MTProto 连接",
        )
        await cls._release_live(popped, unlink_artifacts=True)
        manager.update_task_status(
            task_id,
            "failed",
            error="MANUAL_CODE_TIMEOUT",
            phone_code_hash=None,
        )

    @classmethod
    async def _complete_auth(
        cls,
        client,
        phone: str,
        phone_code_hash: str,
        code: str,
        *,
        password: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        country: str = "cl",
        default_2fa: Optional[str] = None,
        task_id: Optional[str] = None,
        manager: Optional[RegistrationTaskManager] = None,
    ) -> Dict[str, Any]:
        """复用阶段 1 连接完成 signIn / signUp，返回 auth 结果描述。"""
        auth_result = None
        needs_signup = False
        account_kind = "unknown"
        existing_2fa_password = None
        log = manager.append_log if manager and task_id else None

        try:
            auth_result = await client(functions.auth.SignInRequest(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                phone_code=code,
            ))
            if isinstance(auth_result, types.auth.AuthorizationSignUpRequired):
                needs_signup = True
        except SessionPasswordNeededError:
            account_kind = "existing_2fa"
            existing_2fa_password = (password or "").strip() or default_2fa
            if not existing_2fa_password:
                raise ManualRegisterError(
                    "该号码已启用 2FA，请在提交时填写 password",
                    status_code=400,
                )
            if log:
                await log(
                    task_id,
                    "检测到已注册旧号且已启用 2FA（SignIn 触发 SessionPasswordNeeded），"
                    "使用提交/配置的二级口令完成认证，不走 SignUp。",
                )
            auth_result = await client.sign_in(password=existing_2fa_password)
        except (PhoneNumberUnoccupiedError, Exception) as exc:
            err_str = str(exc)
            if isinstance(exc, RETRYABLE_CODE_ERRORS) or isinstance(exc, PhoneCodeExpiredError):
                raise
            if (
                "SignUpRequired" in err_str
                or isinstance(exc, (PhoneNumberUnoccupiedError, types.auth.AuthorizationSignUpRequired))
            ):
                needs_signup = True
            else:
                raise

        if needs_signup:
            account_kind = "new"
            generated_first, generated_last = RegistrationOrchestrator._get_random_name(country)
            resolved_first = (first_name or "").strip() or generated_first
            resolved_last = (last_name or "").strip() or generated_last
            if log:
                await log(
                    task_id,
                    f"状态机判定为新号（SignUpRequired），注入身份属性: {resolved_first} {resolved_last}",
                )
            reg_result = await client(functions.auth.SignUpRequest(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                first_name=resolved_first,
                last_name=resolved_last,
            ))
            if hasattr(reg_result, "terms_of_service") and reg_result.terms_of_service:
                await client(functions.help.AcceptTermsOfServiceRequest(
                    id=reg_result.terms_of_service.id
                ))
            auth_result = reg_result
        elif account_kind != "existing_2fa":
            account_kind = "existing_no_2fa"
            if log:
                await log(
                    task_id,
                    "检测到已注册旧号且无 2FA（SignIn 成功，无需 SignUp），仅同步已有节点状态。",
                )

        user = auth_result.user if hasattr(auth_result, "user") else auth_result
        user_id = user.id if hasattr(user, "id") else 0
        return {
            "auth_result": auth_result,
            "user": user,
            "user_id": user_id,
            "account_kind": account_kind,
            "needs_signup": needs_signup,
            "existing_2fa_password": existing_2fa_password,
        }

    @classmethod
    def _write_session_meta(
        cls,
        meta_path: Path,
        *,
        phone: str,
        user_id: int,
        country: str,
        profile: Dict[str, Any],
        two_fa_password: Optional[str],
    ) -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "phone": phone,
            "user_id": user_id,
            "country": country,
            "secondary_state_key": two_fa_password,
            "two_fa_password": two_fa_password,
            "app_id": profile.get("api_id"),
            "app_hash": profile.get("api_hash"),
            "device_model": profile.get("device_model"),
            "system_version": profile.get("system_version"),
            "app_version": profile.get("app_version"),
            "registered_at": datetime.datetime.now().isoformat(),
        }
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @classmethod
    async def start(
        cls,
        phone: str,
        country: Optional[str] = None,
        app_type: Optional[str] = None,
        proxy_override: Optional[Dict[str, Any]] = None,
        set_2fa: Optional[bool] = None,
        proxy_id: Optional[str] = None,
        proxy_mode: str = "custom_pool",
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        wait_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """阶段 1：握手 + auth.sendCode，成功后进入 waiting_code。"""
        normalized = normalize_manual_phone(phone)
        manager = RegistrationTaskManager.get_instance()
        replaced_ids = await cls._cancel_conflicting_sessions(normalized, manager)
        task_id = manager.create_task()
        config = ConfigManager.get_instance().config
        target_country = resolve_manual_country(
            normalized, country, fallback=getattr(config, "target_country", None)
        )
        active_app = app_type or getattr(config, "active_app_type", None) or "telegram_android"
        ttl = float(wait_seconds if wait_seconds is not None else cls.wait_seconds)

        manager.update_task_status(
            task_id,
            "running",
            mode="manual",
            phone=normalized,
        )
        await manager.append_log(
            task_id,
            f"[手动调试] 跳过接码平台租号，直接使用用户指定号码 {normalized} 发起 auth.sendCode",
        )
        await manager.append_log(
            task_id,
            f"[手动调试] 目标拓扑国家: {target_country.upper()}"
            + ("（由手机号推断）" if not str(country or "").strip() else "（用户指定）"),
        )
        if replaced_ids:
            await manager.append_log(
                task_id,
                f"[手动调试] 号码 {normalized} 存在 {len(replaced_ids)} 个未完成的旧手动任务 "
                f"({', '.join(replaced_ids)})，已自动取消并释放旧 MTProto 连接，"
                f"由本任务 {task_id} 继续承接单活跃手动槽",
            )

        bypass_svc = None
        client = None
        check_id = None
        profile: Dict[str, Any] = {}
        aid = None
        session_path = None
        meta_path = None
        session_filename = None
        push_task_id = None
        push_provider = None
        push_token_obtained_at = None

        try:
            active_proxy = await RegistrationOrchestrator.resolve_active_proxy(
                config=config,
                target_country=target_country,
                task_id=task_id,
                manager=manager,
                proxy_override=proxy_override,
                proxy_id=proxy_id,
                proxy_mode=proxy_mode,
            )

            banned = BannedPhonesCache.lookup(normalized)
            if banned:
                await manager.append_log(
                    task_id,
                    f"⚠️ 本地封禁库命中 {normalized}（原因={banned.reason}），"
                    "手动调试模式不拦截，继续发码以便核验平台",
                )

            bypass_svc = AttestationGatewayService(config, proxy=active_proxy)
            profile = DeviceProfileManager.get_resolved_profile(active_app, target_country)
            aid = profile.get("aid")

            await manager.append_log(task_id, f"选定端点模板: {profile.get('name')} (AID: {aid})")
            pack_alias = profile.get("device_pack_alias")
            pack_country = (profile.get("device_pack_country") or "").upper()
            pack_match = profile.get("device_pack_match") or "none"
            pack_auto = bool(profile.get("device_pack_auto"))
            if pack_alias:
                match_label = DeviceProfileManager.describe_pack_match(pack_match, pack_auto)
                await manager.append_log(
                    task_id,
                    f"硬件指纹包: {pack_alias}"
                    + (f" [{pack_country}]" if pack_country else "")
                    + f" · {match_label}",
                )
            await manager.append_log(
                task_id,
                f"绑定硬件特征: {profile.get('device_model')} ({profile.get('system_version')}), "
                f"App: {profile.get('app_version')}",
            )

            try:
                await manager.append_log(
                    task_id,
                    f"正在对通信句柄 {normalized} 执行 Telegram 号码注册状态预检探测（仅日志，不拦截）...",
                )
                precheck = await PhonePrecheckService.check_phone(
                    normalized,
                    proxy=active_proxy,
                    log_callback=lambda msg: manager.append_log(task_id, msg),
                    config=config,
                )
                if precheck.is_registered is True:
                    await manager.append_log(
                        task_id,
                        f"ℹ️ 预检显示该号码可能已注册 (uid={precheck.user_id})。"
                        "手动模式继续发码，提交验证码后将走 SignIn 登录旧号。",
                    )
                    manager.update_task_status(
                        task_id, "running", precheck_user_id=precheck.user_id
                    )
            except Exception as exc:
                await manager.append_log(task_id, f"⚠️ 预检探测跳过: {exc}")

            push_token = None
            push_task_id = None
            push_provider = None
            push_token_obtained_at = None

            delivery_plan = resolve_code_delivery_plan(config, profile)
            await RegistrationOrchestrator._log_code_delivery_plan(task_id, manager, delivery_plan)
            push_token, push_task_id, push_provider, push_token_obtained_at = (
                await RegistrationOrchestrator._fetch_push_token_if_needed(
                    bypass_svc=bypass_svc,
                    profile=profile,
                    aid=aid,
                    task_id=task_id,
                    manager=manager,
                    plan=delivery_plan,
                    push_token=push_token,
                    push_task_id=push_task_id,
                    push_provider=push_provider,
                    push_token_obtained_at=push_token_obtained_at,
                    hunt_enabled=False,
                )
            )

            original_api_id = profile.get("api_id")
            profile = DeviceProfileManager.resolve_effective_credentials(
                profile, config, has_push_token=bool(push_token)
            )
            if profile.get("credential_source") == "custom":
                await manager.append_log(
                    task_id,
                    f"API 凭证策略: 强制使用自建开发者凭证 (api_id={profile.get('api_id')})",
                )
            elif profile.get("credential_source") == "custom_auto_fallback":
                await manager.append_log(
                    task_id,
                    f"⚠️ 未获取到有效 Push Token，且官方 api_id={original_api_id} "
                    f"属于已知公开泄露 ID，已自动回退至自建开发者凭证 (api_id={profile.get('api_id')})",
                )

            session_path, meta_path, session_filename = session_artifact_paths(normalized)
            proxy_dict = {
                "proxy_type": active_proxy.get("proxy_type", "socks5"),
                "addr": active_proxy.get("addr", "127.0.0.1"),
                "port": int(active_proxy.get("port", 10808)),
                "username": active_proxy.get("username"),
                "password": active_proxy.get("password"),
            }
            await manager.append_log(
                task_id,
                f"建立 MTProto 协议传输通道 (中继节点: {proxy_dict['addr']}:{proxy_dict['port']})...",
            )
            client = TelegramClient(
                session=str(session_path),
                api_id=profile["api_id"],
                api_hash=profile["api_hash"],
                proxy=proxy_dict if proxy_dict["addr"] else None,
                device_model=profile.get("device_model"),
                system_version=profile.get("system_version"),
                app_version=profile.get("app_version"),
                lang_code=profile.get("lang_code"),
                system_lang_code=profile.get("system_lang_code"),
            )
            init_snap = apply_init_connection_overrides(client, profile, config)
            if init_snap.get("blocked"):
                await manager.append_log(
                    task_id,
                    f"InitConnection 指纹未写入: {init_snap.get('blocked')}",
                )
            else:
                await manager.append_log(task_id, describe_init_connection(client))
            connected = await RegistrationOrchestrator._connect_mtproto(
                client, task_id, manager, None, None, timeout=CONNECT_TIMEOUT_SECONDS
            )
            if not connected:
                await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
                return cls._snapshot_start(
                    task_id,
                    "failed",
                    normalized,
                    "MTProto 连接超时，任务已失败",
                    country=target_country,
                    error="CONNECT_TIMEOUT",
                )
            await manager.append_log(task_id, "已完成 MTProto 传输层 Diffie-Hellman 密钥交换与加密连接建立")
            await RegistrationOrchestrator.perform_handshake(client, profile, task_id, manager)

            plan = delivery_plan
            code_settings = RegistrationOrchestrator._build_code_settings_from_plan(push_token, plan)
            await manager.append_log(task_id, "调用 auth.sendCode 触发服务端瞬时握手挑战分发...")
            try:
                sent_code = await RegistrationOrchestrator._send_code_with_recaptcha(
                    client=client,
                    phone=normalized,
                    profile=profile,
                    code_settings=code_settings,
                    bypass_svc=bypass_svc,
                    active_proxy=active_proxy,
                    task_id=task_id,
                    manager=manager,
                )
            except ApiIdPublishedFloodError:
                if not plan.can_escalate_on_published_flood:
                    raise
                escalated = escalation_plan_after_published_flood(plan)
                await RegistrationOrchestrator._log_code_delivery_plan(task_id, manager, escalated)
                push_token, push_task_id, push_provider, push_token_obtained_at = (
                    await RegistrationOrchestrator._fetch_push_token_if_needed(
                        bypass_svc=bypass_svc,
                        profile=profile,
                        aid=aid,
                        task_id=task_id,
                        manager=manager,
                        plan=escalated,
                        push_token=push_token,
                        push_task_id=push_task_id,
                        push_provider=push_provider,
                        push_token_obtained_at=push_token_obtained_at,
                        hunt_enabled=False,
                    )
                )
                plan = escalated
                code_settings = RegistrationOrchestrator._build_code_settings_from_plan(push_token, plan)
                sent_code = await RegistrationOrchestrator._send_code_with_recaptcha(
                    client=client,
                    phone=normalized,
                    profile=profile,
                    code_settings=code_settings,
                    bypass_svc=bypass_svc,
                    active_proxy=active_proxy,
                    task_id=task_id,
                    manager=manager,
                )
            delivery_type = _delivery_type_name(sent_code)
            phone_code_hash = getattr(sent_code, "phone_code_hash", None)
            await manager.append_log(
                task_id,
                f"挑战已由服务端下发! 分发通道类型: {delivery_type} "
                f"({RegistrationOrchestrator._describe_sent_code(sent_code)})",
            )
            if RegistrationOrchestrator._is_app_delivery(sent_code):
                await manager.append_log(
                    task_id,
                    "⚠️ 服务端将验证码下发到了已有设备客户端 (SentCodeTypeApp)。"
                    "手动模式不会自动退订，请在控制台填入客户端/短信验证码。",
                )
            elif RegistrationOrchestrator._is_sms_delivery(sent_code):
                await manager.append_log(task_id, "分发通道为运营商短信，可在接码平台或网页端读取验证码")

            expires_at = datetime.datetime.now() + datetime.timedelta(seconds=ttl)
            expires_iso = expires_at.isoformat()
            manager.update_task_status(
                task_id,
                "waiting_code",
                phone=normalized,
                mode="manual",
                phone_code_hash=phone_code_hash,
                delivery_type=delivery_type,
                session_file=session_filename,
                expires_at=expires_iso,
            )
            await manager.append_log(
                task_id,
                f"状态机已进入 waiting_code，请在 {int(ttl)} 秒内于控制台提交验证码",
            )

            live = ManualLiveSession(
                task_id=task_id,
                client=client,
                phone=normalized,
                phone_code_hash=str(phone_code_hash or ""),
                delivery_type=delivery_type,
                profile=profile,
                config=config,
                set_2fa=set_2fa,
                bypass_svc=bypass_svc,
                check_id=check_id,
                aid=aid,
                target_country=target_country,
                session_path=session_path,
                meta_path=meta_path,
                session_filename=session_filename,
                push_task_id=push_task_id,
                push_provider=push_provider,
                push_token_obtained_at=push_token_obtained_at,
                first_name=first_name,
                last_name=last_name,
                expires_at=expires_at,
            )
            live.expire_task = asyncio.create_task(cls._expire_waiting(task_id, ttl))
            ManualSessionStore.get_instance().put(live)
            return cls._snapshot_start(
                task_id,
                "waiting_code",
                normalized,
                "验证码已发送，请在控制台提交短信/客户端验证码",
                phone_code_hash=phone_code_hash,
                delivery_type=delivery_type,
                country=target_country,
                expires_at=expires_iso,
            )
        except ManualRegisterError:
            raise
        except PhoneNumberInvalidError:
            err = f"通信句柄 {normalized} 不是有效手机号 (PHONE_NUMBER_INVALID)"
            await manager.append_log(task_id, f"❌ {err}")
            await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
            manager.update_task_status(task_id, "failed", error=err, phone=normalized)
            return cls._snapshot_start(
                task_id, "failed", normalized, err, country=target_country, error=err
            )
        except PhoneNumberBannedError:
            err = f"通信句柄 {normalized} 处于服务端拒绝服务状态 (PHONE_NUMBER_BANNED)"
            await manager.append_log(task_id, f"❌ {err}")
            BannedPhonesCache.remember(
                normalized,
                reason="PHONE_NUMBER_BANNED",
                source=SOURCE_TELEGRAM_RPC,
                country=target_country,
            )
            await RegistrationOrchestrator._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                normalized, task_id, manager, "PHONE_NUMBER_BANNED",
            )
            await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
            manager.update_task_status(task_id, "failed", error=err, phone=normalized)
            return cls._snapshot_start(
                task_id, "failed", normalized, err, country=target_country, error=err
            )
        except ApiIdPublishedFloodError:
            err = (
                f"当前 api_id={profile.get('api_id')} 已被 Telegram 判定为公开泄露 ID，"
                "触发 API_ID_PUBLISHED_FLOOD。请配置自建 api_id/api_hash 或修复 Push Token 后重试"
            )
            await manager.append_log(task_id, f"❌ {err}")
            await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
            manager.update_task_status(task_id, "failed", error=err, phone=normalized)
            return cls._snapshot_start(
                task_id, "failed", normalized, err, country=target_country, error=err
            )
        except (PhoneNumberFloodError, FloodWaitError) as exc:
            sec = getattr(exc, "seconds", 0)
            err = f"触发协议频控与退避限流，需等待 {sec} 秒 (FLOOD_WAIT)"
            await manager.append_log(task_id, f"❌ {err}")
            await RegistrationOrchestrator._refund_push_token(
                bypass_svc, push_task_id, push_provider, push_token_obtained_at,
                normalized, task_id, manager, "FLOOD_WAIT",
            )
            await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
            manager.update_task_status(task_id, "failed", error=err, phone=normalized)
            return cls._snapshot_start(
                task_id, "failed", normalized, err, country=target_country, error=err
            )
        except RecaptchaChallengeError as exc:
            err = f"RECAPTCHA_CHECK 人机挑战未能突破: {exc}"
            await manager.append_log(task_id, f"❌ {err}")
            await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
            manager.update_task_status(task_id, "failed", error=err, phone=normalized)
            return cls._snapshot_start(
                task_id, "failed", normalized, err, country=target_country, error=err
            )
        except Exception as exc:
            parsed = parse_recaptcha_check(exc)
            reason = "RECAPTCHA_CHECK" if parsed else "EXCEPTION"
            err = f"手动发码流程异常: {exc or repr(exc)}"
            await manager.append_log(task_id, f"❌ {err}")
            await RegistrationOrchestrator._release_registration_resources(client, None, bypass_svc)
            if session_path and Path(session_path).exists() and (not meta_path or not Path(meta_path).exists()):
                try:
                    Path(session_path).unlink()
                except OSError:
                    pass
            manager.update_task_status(task_id, "failed", error=f"{reason}: {err}", phone=normalized)
            return cls._snapshot_start(
                task_id, "failed", normalized, err, country=target_country, error=err
            )

    @classmethod
    async def submit_code(
        cls,
        task_id: str,
        code: str,
        password: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """阶段 2：复用活跃连接完成登录/注册、2FA 与凭证落盘。"""
        cleaned = str(code or "").strip()
        if len(cleaned) < 3:
            raise ManualRegisterError("请填写有效验证码")

        manager = RegistrationTaskManager.get_instance()
        task = manager.get_task(task_id)
        if not task:
            raise ManualRegisterError("任务不存在或已被清理", status_code=404)

        store = ManualSessionStore.get_instance()
        session = store.get(task_id)
        if session is None:
            status = task.get("status") or "unknown"
            raise ManualRegisterError(
                f"任务 {task_id} 没有可复用的 MTProto 连接（当前状态: {status}），请重新发码",
                status_code=409,
            )
        if (task.get("status") or "") != "waiting_code":
            raise ManualRegisterError(
                f"任务当前状态为 {task.get('status')}，无法提交验证码",
                status_code=409,
            )

        async with session.lock:
            manager.update_task_status(task_id, "logging_in")
            await manager.append_log(task_id, f"正在提交验证码完成 auth.signIn / auth.signUp ...")
            try:
                outcome = await cls._complete_auth(
                    session.client,
                    session.phone,
                    session.phone_code_hash,
                    cleaned,
                    password=password,
                    first_name=first_name or session.first_name,
                    last_name=last_name or session.last_name,
                    country=session.target_country,
                    default_2fa=getattr(session.config, "default_2fa_password", None),
                    task_id=task_id,
                    manager=manager,
                )
                user_id = int(outcome["user_id"] or 0)
                account_kind = outcome["account_kind"]
                if account_kind == "existing_2fa":
                    # 旧号已有 2FA：本次 Push Token 对新号验证已无意义，尝试触发 REGHelp 退款
                    await RegistrationOrchestrator._refund_push_token(
                        session.bypass_svc, session.push_task_id, session.push_provider,
                        session.push_token_obtained_at, session.phone, task_id, manager, "existing_2fa",
                    )
                await manager.append_log(
                    task_id,
                    f"虚拟节点状态机初始化成功! 节点 UID: {user_id}, 句柄: {session.phone}, "
                    f"账号类型: {account_kind}",
                )

                two_fa_set = False
                two_fa_password = None
                should_set_2fa = RegistrationOrchestrator._should_set_2fa(
                    session.config, session.set_2fa
                )
                default_2fa = getattr(session.config, "default_2fa_password", None)
                if should_set_2fa and default_2fa:
                    try:
                        await manager.append_log(
                            task_id,
                            f"启用二级密码学状态保护: {str(default_2fa)[:3]}***"
                            + ("（已传入 current_password）" if outcome.get("existing_2fa_password") else ""),
                        )
                        await session.client.edit_2fa(**RegistrationOrchestrator._edit_2fa_kwargs(
                            default_2fa,
                            current_password=outcome.get("existing_2fa_password"),
                        ))
                        two_fa_set = True
                        two_fa_password = default_2fa
                        await manager.append_log(task_id, "二级密码学状态锁已成功锁定")
                    except Exception as exc:
                        await manager.append_log(task_id, f"配置二级状态锁跳过或提示: {exc}")
                elif session.set_2fa is False:
                    await manager.append_log(task_id, "请求显式关闭 set_2fa，跳过二级密码设定")

                try:
                    await session.client.get_dialogs(limit=5)
                    await manager.append_log(task_id, "节点状态机完成全量就绪，首屏状态遥测已同步")
                except Exception:
                    pass

                cls._write_session_meta(
                    session.meta_path,
                    phone=session.phone,
                    user_id=user_id,
                    country=session.target_country,
                    profile=session.profile,
                    two_fa_password=two_fa_password if two_fa_set else None,
                )
                if session.check_id and session.bypass_svc is not None:
                    try:
                        await session.bypass_svc.report_result(session.check_id, session.aid, "REGISTERED")
                    except Exception:
                        pass

                try:
                    from backend.app.services.push_token_vault import PushTokenVault

                    if session.push_task_id:
                        # 带上本任务作为租约持有者，否则库存层会当成外来任务而拒绝写入
                        PushTokenVault.get_instance().mark_success(
                            reghelp_task_id=session.push_task_id,
                            lease_task_id=task_id,
                        )
                except Exception:
                    pass
                RegistrationOrchestrator._release_push_token_leases(task_id)

                store.pop(task_id)
                await cls._release_live(session, unlink_artifacts=False)
                manager.update_task_status(
                    task_id,
                    "success",
                    phone=session.phone,
                    user_id=user_id,
                    account_kind=account_kind,
                    needs_signup=bool(outcome.get("needs_signup")),
                    session_file=session.session_filename,
                    phone_code_hash=session.phone_code_hash,
                    delivery_type=session.delivery_type,
                )
                await manager.append_log(
                    task_id,
                    f"已标准序列化凭证对: {session.session_filename} + {session.meta_path.name}",
                )
                return cls._snapshot_submit(
                    task_id,
                    "success",
                    session.phone,
                    f"注册/登录成功，UID={user_id}，凭证 {session.session_filename}",
                    user_id=user_id,
                    session_file=session.session_filename,
                    account_kind=account_kind,
                )
            except ManualRegisterError as exc:
                manager.update_task_status(task_id, "waiting_code", error=exc.message)
                await manager.append_log(task_id, f"⚠️ {exc.message}")
                raise
            except RETRYABLE_CODE_ERRORS as exc:
                err = f"验证码不正确: {exc}"
                await manager.append_log(task_id, f"⚠️ {err}，连接保持，可重新提交")
                manager.update_task_status(task_id, "waiting_code", error=err)
                return cls._snapshot_submit(
                    task_id,
                    "waiting_code",
                    session.phone,
                    err,
                    error=err,
                )
            except PhoneCodeExpiredError as exc:
                err = f"验证码已过期: {exc}"
                await manager.append_log(task_id, f"❌ {err}")
                store.pop(task_id)
                await cls._release_live(session, unlink_artifacts=True)
                manager.update_task_status(task_id, "failed", error=err, phone=session.phone)
                return cls._snapshot_submit(
                    task_id, "failed", session.phone, err, error=err
                )
            except Exception as exc:
                err = f"提交验证码失败: {exc or repr(exc)}"
                await manager.append_log(task_id, f"❌ {err}")
                store.pop(task_id)
                await cls._release_live(session, unlink_artifacts=True)
                manager.update_task_status(task_id, "failed", error=err, phone=session.phone)
                return cls._snapshot_submit(
                    task_id, "failed", session.phone, err, error=err
                )

    @classmethod
    async def cancel(cls, task_id: str) -> Dict[str, Any]:
        """阶段 3：取消未完成任务并安全释放连接。"""
        manager = RegistrationTaskManager.get_instance()
        task = manager.get_task(task_id)
        if not task:
            raise ManualRegisterError("任务不存在或已被清理", status_code=404)

        store = ManualSessionStore.get_instance()
        session = store.pop(task_id)
        status = task.get("status") or ""
        if status in {"success", "failed", "filtered", "canceled"} and session is None:
            return {
                "task_id": task_id,
                "status": status,
                "message": f"任务已处于终态 {status}，无需再次取消",
                "logs": _task_logs(task_id),
            }

        await manager.append_log(task_id, "用户取消手动注册任务，正在安全释放 MTProto 连接...")
        await cls._release_live(session, unlink_artifacts=True)
        manager.update_task_status(
            task_id,
            "canceled",
            error=None,
            phone_code_hash=None,
        )
        await manager.append_log(task_id, "任务已取消，未完成的临时 session 已清理")
        return {
            "task_id": task_id,
            "status": "canceled",
            "message": "手动注册任务已取消，连接已释放",
            "logs": _task_logs(task_id),
        }
