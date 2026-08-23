"""Telegram 号码注册状态预检探测器 (Phone Precheck Probe)

在向 REGHelp 申请 Push Token 与调用 auth.sendCode 之前，
利用 lod_user / data/sessions 中已授权的 Telethon session，
通过 contacts.ResolvePhone / contacts.ImportContacts 查询号码是否已在 Telegram 注册。

已注册（二手/回收号）几乎必然走 SentCodeTypeApp，接码平台收不到短信；
预检拦截后立即退订换号，避免白白消耗 1.0 RUB Push Token 与十几秒握手时间。

若本地暂无可用探测 session，则优雅降级走现有流程。
"""
from __future__ import annotations

import logging
import random
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.app.config import ConfigManager
from backend.app.services.account_vault import AccountVaultService, normalize_phone

logger = logging.getLogger("PhonePrecheck")

PRECHECK_ALREADY_REGISTERED = "PRECHECK_PHONE_ALREADY_REGISTERED"
PRECHECK_CLEAN = "PRECHECK_PHONE_CLEAN"
PRECHECK_DEGRADED = "PRECHECK_NO_PROBE_SESSION"

INTERCEPT_LOG_TEMPLATE = (
    "[预检拦截] 检测到号码 {phone} 已在 Telegram 注册并存在活跃用户 "
    "(uid={user_id})，直接撤销退订换号，不消耗 Push Token 与短信！"
)
CLEAN_LOG_TEMPLATE = (
    "预检通过：号码 {phone} 在 Telegram 官方库中未注册（白号），"
    "继续申请 Push Token 并发送 auth.sendCode"
)
DEGRADE_LOG_TEMPLATE = (
    "⚠️ 本地暂无可用于预检的 session 探测器，优雅降级走现有流程"
    "（将在 sendCode 后再识别 SentCodeTypeApp）"
)

PROBE_CONNECT_TIMEOUT = 20.0
MAX_PROBE_ACCOUNTS = 6


@dataclass
class PhonePrecheckResult:
    """单次号码预检结论。"""

    available: bool
    is_registered: Optional[bool]
    user_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    probe_phone: Optional[str] = None
    method: Optional[str] = None
    degraded: bool = False
    reason: str = ""
    intercept: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def intercept_log(self) -> str:
        return INTERCEPT_LOG_TEMPLATE.format(
            phone=self._phone_for_log(),
            user_id=self.user_id if self.user_id is not None else "unknown",
        )

    def _phone_for_log(self) -> str:
        return self.probe_phone or "unknown"


@dataclass
class PhonePrecheckStatus:
    """控制台展示用的探测器就绪状态。"""

    enabled: bool
    active: bool
    probe_count: int
    probe_phones: List[str] = field(default_factory=list)
    degraded: bool = False
    message: str = ""
    active_probes: List[Dict[str, Any]] = field(default_factory=list)
    precheck_proxy: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def format_precheck_intercept_log(phone: str, user_id: Any = None) -> str:
    return INTERCEPT_LOG_TEMPLATE.format(
        phone=phone or "unknown",
        user_id=user_id if user_id is not None else "unknown",
    )


def _digits_only(phone: str) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _mask_phone(phone: Optional[str]) -> str:
    normalized = normalize_phone(phone) or (str(phone).strip() if phone else "")
    digits = _digits_only(normalized)
    if len(digits) < 6:
        return normalized or "-"
    return f"+{digits[:4]}****{digits[-4:]}"


def _user_from_entity(user: Any) -> Dict[str, Any]:
    if user is None:
        return {}
    return {
        "user_id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
    }


class PhonePrecheckService:
    """基于已授权 session 的 Telegram 号码注册状态探测器。"""

    @classmethod
    def is_enabled(cls, config=None) -> bool:
        cfg = config if config is not None else ConfigManager.get_instance().config
        return bool(getattr(cfg, "phone_precheck_enabled", True))

    @classmethod
    def _account_is_probe_active(cls, acc, config=None) -> bool:
        explicit = getattr(acc, "is_probe_active", None)
        if explicit is False:
            return False
        if explicit is True:
            return True
        try:
            from backend.app.services.account_vault import is_account_probe_active

            return is_account_probe_active(
                getattr(acc, "account_id", None),
                bool(getattr(acc, "has_session", False)),
                config=config,
            )
        except Exception:
            return True

    @classmethod
    def list_probe_accounts(cls, accounts=None, config=None) -> List[Any]:
        """仅使用已激活且具备 .session 的探测账号。"""
        items = list(accounts) if accounts is not None else AccountVaultService.scan_accounts()
        probes = []
        for acc in items:
            if not getattr(acc, "has_session", False):
                continue
            if not cls._account_is_probe_active(acc, config=config):
                continue
            if not getattr(acc, "app_id", None) or not getattr(acc, "app_hash", None):
                continue
            if not AccountVaultService.resolve_session_file(acc):
                continue
            probes.append(acc)
        return probes[:MAX_PROBE_ACCOUNTS]

    @classmethod
    def resolve_precheck_proxy(cls, country: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            from backend.app.services.proxy_manager import select_proxy_for_precheck

            return select_proxy_for_precheck(country)
        except Exception as exc:
            logger.debug("选择预检专用代理失败: %s", exc)
            return None

    @classmethod
    def _summarize_proxy(cls, proxy: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not proxy:
            return None
        return {
            "id": proxy.get("id"),
            "addr": proxy.get("addr"),
            "port": proxy.get("port"),
            "proxy_type": proxy.get("proxy_type") or "socks5",
            "role": proxy.get("role") or "all",
            "assigned_country": proxy.get("assigned_country"),
            "country_code": proxy.get("country_code") or proxy.get("assigned_country"),
            "egress_ip": proxy.get("egress_ip"),
            "healthy": proxy.get("healthy"),
            "username": proxy.get("username"),
        }

    @classmethod
    def describe_status(cls, config=None, accounts=None) -> PhonePrecheckStatus:
        enabled = cls.is_enabled(config)
        probes = cls.list_probe_accounts(accounts, config=config)
        phones = [_mask_phone(getattr(acc, "phone", None) or getattr(acc, "phone_raw", None)) for acc in probes]
        active_probes = []
        for acc in probes:
            active_probes.append({
                "account_id": getattr(acc, "account_id", None),
                "phone": _mask_phone(getattr(acc, "phone", None) or getattr(acc, "phone_raw", None)),
                "has_session": bool(getattr(acc, "has_session", False)),
                "is_probe_active": True,
                "has_credentials": bool(getattr(acc, "app_id", None) and getattr(acc, "app_hash", None)),
                "healthy": True,
                "source": getattr(acc, "source", None),
            })
        precheck_proxy = cls._summarize_proxy(cls.resolve_precheck_proxy())
        if not enabled:
            return PhonePrecheckStatus(
                enabled=False,
                active=False,
                probe_count=len(probes),
                probe_phones=phones,
                degraded=True,
                message="号码白号预检探测器已关闭，将走现有 sendCode 流程",
                active_probes=active_probes,
                precheck_proxy=precheck_proxy,
            )
        if not probes:
            return PhonePrecheckStatus(
                enabled=True,
                active=False,
                probe_count=0,
                probe_phones=[],
                degraded=True,
                message="本地暂无已激活的预检 session 探测器，将优雅降级走现有流程",
                active_probes=[],
                precheck_proxy=precheck_proxy,
            )
        proxy_hint = ""
        if precheck_proxy and precheck_proxy.get("addr"):
            proxy_hint = (
                f"；预检出口 {precheck_proxy.get('proxy_type')}://"
                f"{precheck_proxy.get('addr')}:{precheck_proxy.get('port')}"
                f" [{(precheck_proxy.get('role') or 'all')}]"
            )
        return PhonePrecheckStatus(
            enabled=True,
            active=True,
            probe_count=len(probes),
            probe_phones=phones,
            degraded=False,
            message=f"号码白号预检探测器已激活（{len(probes)} 个授权 session）{proxy_hint}",
            active_probes=active_probes,
            precheck_proxy=precheck_proxy,
        )

    @classmethod
    def interpret_resolve_result(cls, result: Any) -> Optional[Dict[str, Any]]:
        """解析 contacts.ResolvePhone 返回值。有用户即视为已注册。"""
        users = list(getattr(result, "users", None) or [])
        if not users:
            peer = getattr(result, "peer", None)
            peer_id = getattr(peer, "user_id", None) if peer is not None else None
            if peer_id:
                return {"user_id": peer_id, "username": None, "first_name": None}
            return None
        return _user_from_entity(users[0]) or None

    @classmethod
    def interpret_import_result(cls, result: Any) -> Optional[Dict[str, Any]]:
        """解析 contacts.ImportContacts 返回值。users 非空即已注册。"""
        users = list(getattr(result, "users", None) or [])
        if users:
            return _user_from_entity(users[0]) or None
        imported = list(getattr(result, "imported", None) or [])
        for item in imported:
            uid = getattr(item, "user_id", None)
            if uid:
                return {"user_id": uid, "username": None, "first_name": None}
        return None

    @classmethod
    def result_from_user(
        cls,
        phone: str,
        user: Optional[Dict[str, Any]],
        *,
        method: str,
        probe_phone: Optional[str] = None,
    ) -> PhonePrecheckResult:
        if user and user.get("user_id"):
            return PhonePrecheckResult(
                available=True,
                is_registered=True,
                user_id=int(user["user_id"]),
                username=user.get("username"),
                first_name=user.get("first_name"),
                probe_phone=phone,
                method=method,
                degraded=False,
                reason=PRECHECK_ALREADY_REGISTERED,
                intercept=True,
            )
        return PhonePrecheckResult(
            available=True,
            is_registered=False,
            probe_phone=phone,
            method=method,
            degraded=False,
            reason=PRECHECK_CLEAN,
            intercept=False,
        )

    @classmethod
    def degraded_result(cls, reason: str = PRECHECK_DEGRADED) -> PhonePrecheckResult:
        return PhonePrecheckResult(
            available=False,
            is_registered=None,
            degraded=True,
            reason=reason,
            intercept=False,
        )

    @classmethod
    async def check_phone(
        cls,
        phone: str,
        *,
        proxy: Optional[Dict[str, Any]] = None,
        log_callback: Optional[Callable] = None,
        accounts=None,
        probe_client=None,
        enabled: Optional[bool] = None,
        config=None,
    ) -> PhonePrecheckResult:
        """查询号码是否已在 Telegram 注册。

        probe_client 仅供单测注入：实现异步 ``__call__(request)``。
        """
        if enabled is None:
            enabled = cls.is_enabled(config)
        if not enabled:
            return cls.degraded_result("PRECHECK_DISABLED")

        normalized = normalize_phone(phone) or str(phone or "").strip()
        if not _digits_only(normalized):
            return cls.degraded_result("PRECHECK_INVALID_PHONE")

        if probe_client is not None:
            return await cls._query_with_client(probe_client, normalized)

        dedicated = cls.resolve_precheck_proxy()
        if dedicated:
            proxy = dedicated

        probes = cls.list_probe_accounts(accounts, config=config)
        if not probes:
            if log_callback:
                await log_callback(DEGRADE_LOG_TEMPLATE)
            return cls.degraded_result(PRECHECK_DEGRADED)

        last_error = None
        for acc in probes:
            try:
                result = await cls._check_with_account(acc, normalized, proxy=proxy)
                if result.available:
                    return result
                last_error = result.reason
            except Exception as exc:
                last_error = str(exc)
                logger.warning("预检探测账号 %s 失败: %s", getattr(acc, "phone", None), exc)
                continue

        if log_callback:
            await log_callback(
                f"⚠️ 预检探测器已尝试 {len(probes)} 个 session 仍未得到结论"
                f"（{last_error or 'unknown'}），优雅降级走现有流程"
            )
        return cls.degraded_result(last_error or PRECHECK_DEGRADED)

    @classmethod
    async def _query_with_client(cls, client, phone: str) -> PhonePrecheckResult:
        """对已连接的探测 client 执行 ResolvePhone → ImportContacts。"""
        from telethon.errors import (
            PhoneNumberUnoccupiedError,
            UsernameNotOccupiedError,
        )
        from telethon.errors.rpcerrorlist import RPCError
        from telethon.tl import functions, types

        digits = _digits_only(phone)
        plus = f"+{digits}"

        try:
            resolved = await client(functions.contacts.ResolvePhoneRequest(phone=digits))
            user = cls.interpret_resolve_result(resolved)
            if user:
                return cls.result_from_user(plus, user, method="resolve_phone")
            return cls.result_from_user(plus, None, method="resolve_phone")
        except (PhoneNumberUnoccupiedError, UsernameNotOccupiedError):
            return cls.result_from_user(plus, None, method="resolve_phone")
        except RPCError as exc:
            err = str(exc).upper()
            if "PHONE_NOT_OCCUPIED" in err or "USERNAME_NOT_OCCUPIED" in err:
                return cls.result_from_user(plus, None, method="resolve_phone")
            logger.info("ResolvePhone 不可用，回退 ImportContacts: %s", exc)
        except AttributeError:
            logger.info("当前 Telethon 层不支持 ResolvePhoneRequest，回退 ImportContacts")
        except Exception as exc:
            logger.info("ResolvePhone 探测异常，回退 ImportContacts: %s", exc)

        contact = types.InputPhoneContact(
            client_id=random.randint(1, 2**31 - 1),
            phone=plus,
            first_name="P",
            last_name="",
        )
        imported = await client(functions.contacts.ImportContactsRequest(contacts=[contact]))
        user = cls.interpret_import_result(imported)
        try:
            imported_users = list(getattr(imported, "users", None) or [])
            if imported_users:
                await client(functions.contacts.DeleteContactsRequest(id=imported_users))
        except Exception as cleanup_exc:
            logger.debug("预检后清理临时联系人跳过: %s", cleanup_exc)
        return cls.result_from_user(plus, user, method="import_contacts")

    @classmethod
    async def _check_with_account(
        cls,
        account,
        phone: str,
        proxy: Optional[Dict[str, Any]] = None,
    ) -> PhonePrecheckResult:
        from telethon import TelegramClient

        from backend.app.services.telegram_apps import (
            TelegramAppsHelper,
            _telethon_session_base,
            connect_telethon_with_timeout,
            to_telethon_proxy,
        )

        session_path = AccountVaultService.resolve_session_file(account)
        if not session_path:
            return cls.degraded_result("PRECHECK_SESSION_MISSING")

        bound = to_telethon_proxy(proxy)
        work_session = TelegramAppsHelper._copy_session_workspace(session_path)
        client = TelegramClient(
            session=_telethon_session_base(work_session),
            api_id=int(account.app_id),
            api_hash=account.app_hash,
            proxy=bound,
            **TelegramAppsHelper._telethon_client_kwargs(account),
        )
        try:
            await connect_telethon_with_timeout(client, timeout=PROBE_CONNECT_TIMEOUT)
            me = await client.get_me()
            if not me:
                return cls.degraded_result("PRECHECK_SESSION_UNAUTHORIZED")
            return await cls._query_with_client(client, phone)
        finally:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass
            shutil.rmtree(work_session.parent, ignore_errors=True)
