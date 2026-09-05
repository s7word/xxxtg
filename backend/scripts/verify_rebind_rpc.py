#!/usr/bin/env python3
"""单号换绑 RPC 验证脚本（独立，不接入注册主路径）。

流程：
  1. Proxy-Seller API 拉取伊拉克住宅代理并测活
  2. SMSCode 租伊拉克号（或复用已有 order_id）
  3. Telethon 已登录 session → account.sendChangePhoneCode → 等码 → account.changePhone

用法（建议在 backend 容器内执行，本机直连 Proxy-Seller 可能被 IP 白名单拒绝）::

    python3 backend/scripts/verify_rebind_rpc.py --check-only
    python3 backend/scripts/verify_rebind_rpc.py \\
        --account-json lod_user/autoc_sessions_20260823_130603_3/918173845088.json \\
        --max-price 1.0

    # 复用已租号码（跳过租号）::
    python3 backend/scripts/verify_rebind_rpc.py \\
        --account-json ... --order-id 11953801 --new-phone +9647781042141
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberOccupiedError,
    SessionPasswordNeededError,
)
from telethon.tl import functions, types
from telethon.tl.types import CodeSettings

from backend.app.services.recaptcha_check import parse_recaptcha_check

from backend.app.config import ConfigManager
from backend.app.services.attestation_gateway import AttestationGatewayService
from backend.app.services.proxyseller import (
    ProxySellerService,
    format_proxy_endpoint,
    proxy_identity,
)
from backend.app.services.smscode import SmsCodeService
from backend.app.services.telegram_apps import (
    connect_telethon_with_timeout,
    to_telethon_proxy,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("verify_rebind_rpc")

CONNECT_TIMEOUT = 45.0
SMS_POLL_ATTEMPTS = 45
SMS_POLL_INTERVAL = 4.0
MAX_RESEND_WAIT_SECONDS = 90.0


def _telethon_session_base(session_path: Path) -> str:
    text = str(session_path)
    if text.endswith(".session"):
        return text[:-8]
    return text


def _copy_session_workspace(session_path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rebind_verify_"))
    dest = tmp / session_path.name
    shutil.copy2(session_path, dest)
    journal = Path(str(session_path) + "-journal")
    if journal.exists():
        shutil.copy2(journal, tmp / journal.name)
    return dest


def _load_account_meta(json_path: Path) -> Dict[str, Any]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    session_hint = str(data.get("session_file") or json_path.stem)
    session_path = json_path.parent / f"{session_hint}.session"
    if not session_path.exists():
        alt = json_path.with_suffix(".session")
        if alt.exists():
            session_path = alt
    if not session_path.exists():
        raise FileNotFoundError(f"未找到 .session: {session_path}")
    return {
        "meta": data,
        "session_path": session_path,
        "json_path": json_path,
    }


def _client_kwargs(meta: Dict[str, Any]) -> Dict[str, Any]:
    lang = str(meta.get("system_lang_pack") or "en-gb").replace("_", "-")
    app_ver = str(meta.get("app_version") or "12.7.3")
    return {
        "device_model": str(meta.get("device") or meta.get("device_model") or "Samsung SM-G981B"),
        "system_version": str(meta.get("sdk") or meta.get("system_version") or "SDK 29"),
        "app_version": app_ver.split()[0],
        "lang_code": lang.split("-")[0] or "en",
        "system_lang_code": lang,
    }


def _normalize_plus(phone: str) -> str:
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if not digits:
        return str(phone or "").strip()
    return f"+{digits}"


def _build_code_settings() -> CodeSettings:
    """换绑场景：不 attach Push，关闭 allow_app_hash，尽量走运营商短信。"""
    return CodeSettings(
        allow_flashcall=False,
        current_number=False,
        allow_app_hash=False,
        allow_missed_call=False,
        allow_firebase=False,
        unknown_number=True,
    )


def _tl_type_name(obj: Any) -> str:
    return type(obj).__name__ if obj is not None else ""


def _sent_code_type_name(sent_code: Any) -> str:
    if sent_code is None:
        return "Unknown"
    inner = _tl_type_name(getattr(sent_code, "type", None))
    if inner:
        return inner
    return _tl_type_name(sent_code)


def _is_app_delivery(sent_code: Any) -> bool:
    return _sent_code_type_name(sent_code) == "SentCodeTypeApp"


def _is_sms_delivery(sent_code: Any) -> bool:
    name = _sent_code_type_name(sent_code)
    return name in {
        "SentCodeTypeSms",
        "SentCodeTypeFragmentSms",
        "SentCodeTypeFirebaseSms",
    } or ("Sms" in name and "App" not in name and "Firebase" not in name)


async def _maybe_resend_to_sms(
    client: TelegramClient,
    phone: str,
    sent_code: Any,
) -> Any:
    """SentCodeTypeApp + next_type=CodeTypeSms 时调用 auth.resendCode 切短信。"""
    timeout = getattr(sent_code, "timeout", None)
    wait_secs = 0.0
    if timeout is not None:
        try:
            wait_secs = min(max(float(timeout), 0.0), MAX_RESEND_WAIT_SECONDS)
        except (TypeError, ValueError):
            wait_secs = 0.0
    next_name = _tl_type_name(getattr(sent_code, "next_type", None)) or "None"
    if wait_secs > 0:
        logger.info(
            "[RPC] next_type=%s，等待冷却 %ss 后 auth.resendCode...",
            next_name,
            wait_secs,
        )
        await asyncio.sleep(wait_secs)
    else:
        logger.info("[RPC] next_type=%s，立即 auth.resendCode 探测短信...", next_name)
    phone_code_hash = getattr(sent_code, "phone_code_hash", None)
    return await client(
        functions.auth.ResendCodeRequest(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
        )
    )


async def resolve_change_phone_sent_code(
    client: TelegramClient,
    new_phone: str,
    sent_code: Any,
) -> Any:
    """解析 sendChangePhoneCode 返回的分发通道；App 站内信则 resend 降级短信。"""
    delivery = _sent_code_type_name(sent_code)
    logger.info(
        "[RPC] 发码通道 type=%s next_type=%s timeout=%s",
        delivery,
        _tl_type_name(getattr(sent_code, "next_type", None)) or "None",
        getattr(sent_code, "timeout", None),
    )
    if _is_sms_delivery(sent_code):
        logger.info("[RPC] 已是短信通道，等待 SMSCode 接码")
        return sent_code
    if _is_app_delivery(sent_code):
        logger.warning(
            "[RPC] SentCodeTypeApp：验证码进旧客户端，尝试 auth.resendCode 降级短信"
        )
        next_name = _tl_type_name(getattr(sent_code, "next_type", None))
        if not next_name and getattr(sent_code, "timeout", None) is None:
            raise RuntimeError(
                "SentCodeTypeApp 且无 next_type/timeout，带外短信网关无法收码"
            )
        try:
            resent = await _maybe_resend_to_sms(client, new_phone, sent_code)
        except Exception as exc:
            raise RuntimeError(f"auth.resendCode 失败: {exc}") from exc
        new_type = _sent_code_type_name(resent)
        logger.info("[RPC] resendCode 返回 type=%s", new_type)
        if _is_sms_delivery(resent):
            logger.info("[RPC] 已降级为短信通道")
            return resent
        if _is_app_delivery(resent):
            raise RuntimeError("resendCode 后仍为 SentCodeTypeApp，SMSCode 无法收码")
        return resent
    # Call / Flash 等：也尝试 resendCode 切短信（伊拉克号常见先发语音）
    logger.warning(
        "[RPC] 非短信通道 %s，尝试 auth.resendCode 切换短信",
        delivery,
    )
    try:
        resent = await _maybe_resend_to_sms(client, new_phone, sent_code)
        new_type = _sent_code_type_name(resent)
        logger.info("[RPC] resendCode 返回 type=%s", new_type)
        if _is_sms_delivery(resent):
            logger.info("[RPC] 已切换为短信通道")
            return resent
        if _is_app_delivery(resent):
            raise RuntimeError("resendCode 后变为 SentCodeTypeApp，SMSCode 无法收码")
        return resent
    except Exception as exc:
        raise RuntimeError(
            f"通道 {delivery} 且 resendCode 无法切短信: {exc}"
        ) from exc


async def fetch_iq_proxy(probe: bool = True) -> Dict[str, Any]:
    cfg = ConfigManager.get_instance().config
    if not (cfg.proxy_seller_key or "").strip():
        raise RuntimeError("config.proxy_seller_key 为空")
    svc = ProxySellerService(cfg.proxy_seller_key)
    try:
        ensured = await svc.ensure_tg_resident_list("iq", create=False)
        logger.info(
            "[代理] ensure IQ_tg: success=%s message=%s proxies=%s",
            ensured.get("success"),
            ensured.get("message"),
            len(ensured.get("proxies") or []),
        )
        sel = await svc.select_best_proxy(
            "iq",
            probe=probe,
            refresh=True,
            max_probes=3,
        )
        if not sel.get("success") or not sel.get("proxy"):
            raise RuntimeError(sel.get("message") or "select_best_proxy 未返回代理")
        proxy = dict(sel["proxy"])
        logger.info(
            "[代理] 选定 %s identity=%s country=%s healthy=%s egress_ip=%s",
            format_proxy_endpoint(proxy),
            proxy_identity(proxy),
            proxy.get("country_code") or proxy.get("egress_country_code"),
            proxy.get("healthy"),
            proxy.get("egress_ip"),
        )
        if sel.get("probe"):
            pr = sel["probe"]
            logger.info(
                "[代理] 测活 latency_ms=%s ip=%s country=%s",
                pr.get("latency_ms"),
                pr.get("ip"),
                pr.get("country_code") or pr.get("country"),
            )
        return proxy
    finally:
        await svc.close()


async def rent_iq_number(max_price: float) -> Tuple[str, str]:
    cfg = ConfigManager.get_instance().config
    key = (cfg.smscode_api_key or "").strip()
    if not key:
        raise RuntimeError("config.smscode_api_key 为空")
    svc = SmsCodeService(key)
    try:
        bal = await svc.get_balance()
        logger.info("[SMSCode] 余额 USD=%s", bal)
        act_id, phone = await svc.get_number("iq", max_price=max_price)
        phone_plus = _normalize_plus(phone)
        logger.info("[SMSCode] 租号成功 order_id=%s phone=%s", act_id, phone_plus)
        return str(act_id), phone_plus
    finally:
        await svc.close()


async def wait_smscode(act_id: str, max_attempts: int = SMS_POLL_ATTEMPTS) -> str:
    cfg = ConfigManager.get_instance().config
    svc = SmsCodeService(cfg.smscode_api_key)

    async def _log(msg: str) -> None:
        logger.info(msg)

    try:
        code = await svc.wait_for_code(
            act_id,
            max_attempts=max_attempts,
            interval=SMS_POLL_INTERVAL,
            log_callback=_log,
        )
        logger.info("[SMSCode] 收到验证码 len=%d", len(code))
        return str(code).strip()
    finally:
        await svc.close()


async def cancel_smscode(act_id: str, wait_if_too_early: bool = False) -> None:
    cfg = ConfigManager.get_instance().config
    svc = SmsCodeService(cfg.smscode_api_key)
    try:
        res = await svc.cancel(act_id, wait_if_too_early=wait_if_too_early)
        logger.info("[SMSCode] cancel order=%s -> %s", act_id, res)
    except Exception as exc:
        logger.warning("[SMSCode] cancel 失败: %s", exc)
    finally:
        await svc.close()


async def finish_smscode(act_id: str) -> None:
    cfg = ConfigManager.get_instance().config
    svc = SmsCodeService(cfg.smscode_api_key)
    try:
        res = await svc.finish(act_id)
        logger.info("[SMSCode] finish order=%s -> %s", act_id, res)
    except Exception as exc:
        logger.warning("[SMSCode] finish 失败: %s", exc)
    finally:
        await svc.close()


async def send_change_phone_code(
    client: TelegramClient,
    new_phone: str,
    settings: CodeSettings,
    meta: Dict[str, Any],
    proxy: Dict[str, Any],
) -> Any:
    """account.sendChangePhoneCode；遇 RECAPTCHA_CHECK 走 REGHelp + invokeWithReCaptcha。"""
    send_req = functions.account.SendChangePhoneCodeRequest(
        phone_number=new_phone,
        settings=settings,
    )
    try:
        return await client(send_req)
    except Exception as send_err:
        parsed = parse_recaptcha_check(send_err)
        if not parsed:
            raise
        action, site_key = parsed
        logger.info(
            "[RPC] RECAPTCHA_CHECK action=%s site_key=%s → REGHelp 解题",
            action,
            site_key,
        )
        cfg = ConfigManager.get_instance().config
        bypass = AttestationGatewayService(cfg, proxy=proxy)
        try:
            profile = {
                "app_device": "Android",
                "device": meta.get("device"),
                "app_version": meta.get("app_version"),
            }

            async def _reghelp_log(msg: str) -> None:
                logger.info("[REGHelp] %s", msg)

            token = await bypass.get_recaptcha_mobile_token(
                site_key=site_key,
                action=action,
                profile=profile,
                proxy=proxy,
                log_callback=_reghelp_log,
            )
        finally:
            await bypass.close()
        if not token:
            raise RuntimeError("REGHelp RecaptchaMobile 未返回 token")
        logger.info("[RPC] invokeWithReCaptcha(sendChangePhoneCode)...")
        return await client(
            functions.InvokeWithReCaptchaRequest(token=token, query=send_req)
        )


async def run_rebind(
    account_json: Path,
    proxy: Dict[str, Any],
    order_id: str,
    new_phone: str,
    *,
    dry_run: bool = False,
    sms_attempts: int = SMS_POLL_ATTEMPTS,
) -> Dict[str, Any]:
    loaded = _load_account_meta(account_json)
    meta = loaded["meta"]
    session_src = loaded["session_path"]
    work_session = _copy_session_workspace(session_src)
    work_dir = work_session.parent

    api_id = int(meta.get("app_id") or 0)
    api_hash = str(meta.get("app_hash") or "").strip()
    if not api_id or not api_hash:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("账号 JSON 缺少 app_id / app_hash")

    old_phone = _normalize_plus(str(meta.get("phone") or ""))
    new_phone = _normalize_plus(new_phone)
    proxy_dict = to_telethon_proxy(proxy)
    if not proxy_dict:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("代理无效，拒绝直连")

    result: Dict[str, Any] = {
        "old_phone": old_phone,
        "new_phone": new_phone,
        "order_id": order_id,
        "proxy": format_proxy_endpoint(proxy),
        "dry_run": dry_run,
    }

    client = TelegramClient(
        session=_telethon_session_base(work_session),
        api_id=api_id,
        api_hash=api_hash,
        proxy=proxy_dict,
        **(_client_kwargs(meta)),
    )

    try:
        logger.info("[MTProto] 连接 session=%s proxy=%s", session_src.name, result["proxy"])
        await connect_telethon_with_timeout(client, timeout=CONNECT_TIMEOUT)
        me = await client.get_me()
        if not me:
            raise RuntimeError("get_me() 为空，session 未授权")
        logger.info(
            "[MTProto] 已登录 user_id=%s phone=%s username=%s",
            me.id,
            me.phone,
            me.username,
        )
        result["user_id"] = me.id
        result["me_phone_before"] = me.phone

        if dry_run:
            logger.info("[dry-run] 跳过 sendChangePhoneCode / changePhone")
            return result

        settings = _build_code_settings()
        logger.info("[RPC] account.sendChangePhoneCode → %s", new_phone)
        sent = await send_change_phone_code(
            client, new_phone, settings, meta, proxy,
        )
        sent = await resolve_change_phone_sent_code(client, new_phone, sent)
        sent_type = _sent_code_type_name(sent)
        logger.info(
            "[RPC] 最终发码通道 type=%s next_type=%s timeout=%s hash=%s",
            sent_type,
            _tl_type_name(getattr(sent, "next_type", None)) or None,
            getattr(sent, "timeout", None),
            bool(sent.phone_code_hash),
        )
        result["sent_code_type"] = sent_type
        result["phone_code_hash"] = sent.phone_code_hash

        sms_code = await wait_smscode(order_id, max_attempts=sms_attempts)
        logger.info("[RPC] account.changePhone ← code=%s", sms_code)
        change_req = functions.account.ChangePhoneRequest(
            phone_number=new_phone,
            phone_code_hash=sent.phone_code_hash,
            phone_code=sms_code,
        )
        try:
            updated = await client(change_req)
        except SessionPasswordNeededError:
            pwd = str(meta.get("twoFA") or meta.get("two_fa_password") or "").strip()
            if not pwd:
                raise RuntimeError("换绑需 2FA，账号 JSON 无 twoFA / two_fa_password")
            logger.info("[RPC] changePhone 需 2FA，sign_in(password=...)")
            await client.sign_in(password=pwd)
            updated = await client(change_req)
        me_after = await client.get_me()
        logger.info(
            "[RPC] changePhone 成功 user_id=%s phone=%s",
            updated.id if updated else None,
            me_after.phone if me_after else None,
        )
        result["success"] = True
        result["me_phone_after"] = me_after.phone if me_after else None
        await finish_smscode(order_id)
        return result
    except PhoneNumberOccupiedError as exc:
        result["error"] = f"PHONE_NUMBER_OCCUPIED: {exc}"
        logger.error("[RPC] 新号已在 Telegram 注册: %s", exc)
        await cancel_smscode(order_id)
        raise
    except SessionPasswordNeededError as exc:
        result["error"] = f"SESSION_PASSWORD_NEEDED: {exc}"
        logger.error("[RPC] 需要 2FA 密码（换绑路径未实现自动输入）")
        await cancel_smscode(order_id)
        raise
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
        result["error"] = f"PHONE_CODE: {exc}"
        logger.error("[RPC] 验证码无效/过期: %s", exc)
        await cancel_smscode(order_id)
        raise
    except FloodWaitError as exc:
        result["error"] = f"FLOOD_WAIT_{exc.seconds}"
        logger.error("[RPC] FLOOD_WAIT %ss", exc.seconds)
        await cancel_smscode(order_id)
        raise
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("[RPC] 失败: %s", exc)
        traceback.print_exc()
        await cancel_smscode(order_id)
        raise
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        shutil.rmtree(work_dir, ignore_errors=True)


async def main_async(args: argparse.Namespace) -> int:
    if args.check_only:
        proxy = await fetch_iq_proxy(probe=True)
        cfg = ConfigManager.get_instance().config
        svc = SmsCodeService(cfg.smscode_api_key)
        try:
            cid = await svc.resolve_country_id("iq")
            bal = await svc.get_balance()
            logger.info("[check] SMSCode balance=%s iq_country_id=%s", bal, cid)
        finally:
            await svc.close()
        logger.info("[check] OK proxy=%s", format_proxy_endpoint(proxy))
        return 0

    account_json = Path(args.account_json)
    if not account_json.is_absolute():
        account_json = REPO_ROOT / account_json
    if not account_json.exists():
        logger.error("账号 JSON 不存在: %s", account_json)
        return 2

    proxy = await fetch_iq_proxy(probe=not args.skip_proxy_probe)

    order_id = (args.order_id or "").strip()
    new_phone = (args.new_phone or "").strip()
    if not order_id or not new_phone:
        order_id, new_phone = await rent_iq_number(args.max_price)

    logger.info("=== 开始换绑验证 old_json=%s new=%s order=%s ===", account_json, new_phone, order_id)
    try:
        result = await run_rebind(
            account_json,
            proxy,
            order_id,
            new_phone,
            dry_run=args.dry_run,
            sms_attempts=args.sms_attempts,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") or result.get("dry_run") else 1
    except Exception as exc:
        logger.error("验证失败: %s", exc)
        return 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="单号换绑 RPC 验证（IQ 代理 + SMSCode）")
    p.add_argument(
        "--account-json",
        help="账号侧车 JSON 路径（同目录需有 .session）",
    )
    p.add_argument("--order-id", help="复用已有 SMSCode 订单 ID")
    p.add_argument("--new-phone", help="复用订单对应的新号 E.164")
    p.add_argument("--max-price", type=float, default=2.0, help="SMSCode 租号最高出价 USD（IQ 常需 ≥2）")
    p.add_argument("--check-only", action="store_true", help="只测代理 API + SMSCode 余额")
    p.add_argument("--dry-run", action="store_true", help="只登录 get_me，不发换绑 RPC")
    p.add_argument("--skip-proxy-probe", action="store_true", help="跳过代理出口测活")
    p.add_argument(
        "--sms-attempts",
        type=int,
        default=SMS_POLL_ATTEMPTS,
        help="SMSCode 轮询次数（默认 %d，间隔 %ss）" % (SMS_POLL_ATTEMPTS, SMS_POLL_INTERVAL),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.check_only:
        raise SystemExit(asyncio.run(main_async(args)))
    if not args.account_json:
        print("缺少 --account-json", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
