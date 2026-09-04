#!/usr/bin/env python3
"""换绑并发探测：账号与目标国住宅代理 1:1，每账号申请 N 个 SMSCode 号。

默认 lod_user/10_91 × 10 并发。用 --country 切换国家（代理走 {CC}_tg，与号码国对齐）。
接码：SMSCode；遇 RECAPTCHA_CHECK 走 REGHelp。发码/MTProto 禁止直连。

用法（必须在 edgenode-backend 容器内）::

    python /app/backend/scripts/batch_rebind_ve_smscode.py \
        --country co --account-dir lod_user/10_91 \
        --tries-per-account 3 --concurrency 10 --max-price 0.8 --fresh-results
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberOccupiedError,
    SessionPasswordNeededError,
)
from telethon.tl import functions

from backend.app.config import ConfigManager
from backend.app.services.proxyseller import (
    ProxySellerService,
    format_proxy_endpoint,
    proxy_identity,
)
from backend.app.services.smscode import (
    InsufficientBalanceError,
    NoNumberAvailableError,
    SmsCodeService,
)
from backend.app.services.telegram_apps import (
    connect_telethon_with_timeout,
    to_telethon_proxy,
)
from backend.scripts.verify_rebind_rpc import (
    CONNECT_TIMEOUT,
    _build_code_settings,
    _client_kwargs,
    _load_account_meta,
    _normalize_plus,
    _sent_code_type_name,
    _telethon_session_base,
    resolve_change_phone_sent_code,
    send_change_phone_code,
)

logger = logging.getLogger("batch_rebind_ve_smscode")

DEFAULT_ACCOUNT_DIR = "lod_user/10_91"
TARGET_COUNTRY = "ve"
RESULT_JSONL = "rebind_ve_smscode_results.jsonl"
SUMMARY_JSON = "rebind_ve_smscode_summary.json"
RUN_LOG = "rebind_ve_smscode_run.log"


def set_target_country(country: str) -> str:
    """切换目标国，并同步默认产物文件名 rebind_{cc}_smscode_*。"""
    global TARGET_COUNTRY, RESULT_JSONL, SUMMARY_JSON, RUN_LOG
    cc = (country or "ve").strip().lower()
    if len(cc) != 2 or not cc.isalpha():
        raise ValueError(f"国家码必须是 ISO-2，收到: {country!r}")
    TARGET_COUNTRY = cc
    RESULT_JSONL = f"rebind_{cc}_smscode_results.jsonl"
    SUMMARY_JSON = f"rebind_{cc}_smscode_summary.json"
    RUN_LOG = f"rebind_{cc}_smscode_run.log"
    return cc
RENT_INNER_RETRIES = 8
RENT_NO_STOCK_SLEEP = 12.0
RENT_ERROR_SLEEP = 6.0
SMS_POLL_INTERVAL = 4.0
# 单次 attempt 内遇 PHONE_NUMBER_OCCUPIED 自动 cancel+重租，不消耗外层 tries 配额浪费。
OCCUPIED_RENT_RETRIES = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_session_workspace(session_path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rebind_ve_"))
    dest = tmp / session_path.name
    shutil.copy2(session_path, dest)
    for suffix in ("-journal", "-wal", "-shm"):
        extra = Path(str(session_path) + suffix)
        if extra.exists():
            shutil.copy2(extra, tmp / extra.name)
    return dest


def _smscode_key(cfg: Any, cli_key: str = "") -> Tuple[str, str]:
    """返回 (key, source)。不把密钥写入日志。"""
    if (cli_key or "").strip():
        return cli_key.strip(), "cli"
    import os

    env = (os.getenv("SMSCODE_API_KEY") or os.getenv("SMSCODE_TOKEN") or "").strip()
    if env:
        return env, "env"
    dedicated = str(getattr(cfg, "smscode_api_key", "") or "").strip()
    if dedicated:
        return dedicated, "config.smscode_api_key"
    # SMSCode 专用密钥。
    return "", "missing"


def discover_accounts(account_dir: Path) -> List[Path]:
    rows = []
    for p in sorted(account_dir.glob("*.json")):
        session = account_dir / f"{p.stem}.session"
        if session.exists() or p.with_suffix(".session").exists():
            rows.append(p)
    return rows


async def fetch_country_proxy_pool(need: int, probe: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cfg = ConfigManager.get_instance().config
    if not (cfg.proxy_seller_key or "").strip():
        raise RuntimeError("config.proxy_seller_key 为空")
    meta: Dict[str, Any] = {"country": TARGET_COUNTRY, "need": need}
    svc = ProxySellerService(cfg.proxy_seller_key)
    try:
        ports = max(need, 10)
        ensured = await svc.ensure_tg_resident_list(TARGET_COUNTRY, create=True, ports=ports)
        raw = list(ensured.get("proxies") or [])
        logger.info(
            "[代理池] ensure %s_tg success=%s created=%s count=%s msg=%s",
            TARGET_COUNTRY.upper(),
            ensured.get("success"),
            ensured.get("created"),
            len(raw),
            ensured.get("message"),
        )
        meta["ensure"] = {
            "success": bool(ensured.get("success")),
            "created": bool(ensured.get("created")),
            "title": ensured.get("title"),
            "list_id": ensured.get("list_id"),
            "exported": len(raw),
            "message": ensured.get("message"),
            "hint": ensured.get("hint"),
        }
        if not raw:
            raise RuntimeError(ensured.get("message") or "目标国 _tg 代理池为空")

        unique: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for p in raw:
            ident = proxy_identity(p)
            if ident in seen:
                continue
            seen.add(ident)
            unique.append(dict(p))

        probed: List[Dict[str, Any]] = []
        if probe:
            for i, item in enumerate(unique, 1):
                try:
                    pr = await svc.test_proxy_connectivity(item)
                    ok = bool(pr.get("success"))
                    item = dict(item)
                    item["healthy"] = ok
                    item["egress_ip"] = pr.get("ip") or item.get("egress_ip")
                    item["egress_country_code"] = (
                        pr.get("country_code") or pr.get("country") or item.get("egress_country_code")
                    )
                    item["probe"] = {
                        "success": ok,
                        "latency_ms": pr.get("latency_ms"),
                        "ip": pr.get("ip"),
                        "country_code": pr.get("country_code") or pr.get("country"),
                        "error": pr.get("error"),
                    }
                    logger.info(
                        "[代理池] #%d %s healthy=%s egress=%s/%s",
                        i,
                        format_proxy_endpoint(item),
                        ok,
                        item.get("egress_ip"),
                        item.get("egress_country_code"),
                    )
                    probed.append(item)
                except Exception as exc:
                    logger.warning("[代理池] probe 失败 %s: %s", format_proxy_endpoint(item), exc)
                    item = dict(item)
                    item["healthy"] = False
                    item["probe"] = {"success": False, "error": str(exc)}
                    probed.append(item)
        else:
            for item in unique:
                row = dict(item)
                row.setdefault("healthy", True)
                probed.append(row)

        def _egress_cc(item: Dict[str, Any]) -> str:
            return str(
                item.get("egress_country_code")
                or item.get("country_code")
                or ""
            ).strip().upper()

        want = TARGET_COUNTRY.upper()
        healthy = [p for p in probed if p.get("healthy")]
        weak = [p for p in probed if not p.get("healthy")]
        healthy_aligned = [p for p in healthy if _egress_cc(p) == want]
        healthy_other = [p for p in healthy if _egress_cc(p) != want]
        weak_aligned = [p for p in weak if _egress_cc(p) == want]
        weak_other = [p for p in weak if _egress_cc(p) != want]
        selected = (healthy_aligned + healthy_other + weak_aligned + weak_other)[:need]
        logger.info(
            "[代理池] 对齐筛选 want=%s healthy_aligned=%d/%d selected=%d",
            want,
            len(healthy_aligned),
            len(healthy),
            len(selected),
        )
        if len(selected) < need:
            logger.warning(
                "目标国代理不足：需要 %s 个不同 identity，实际 %s（healthy=%s aligned=%s）",
                need,
                len(selected),
                len(healthy),
                len(healthy_aligned),
            )
        for i, p in enumerate(selected, 1):
            logger.info(
                "[代理池] 槽位 %d → %s identity=%s egress=%s/%s",
                i,
                format_proxy_endpoint(p),
                proxy_identity(p),
                p.get("egress_ip"),
                p.get("egress_country_code") or p.get("country_code"),
            )
        meta["unique"] = len(unique)
        meta["healthy"] = len(healthy)
        meta["selected"] = len(selected)
        meta["shortfall"] = max(0, need - len(selected))
        return selected, meta
    finally:
        await svc.close()


async def preflight_account(account_json: Path, proxy: Dict[str, Any]) -> Dict[str, Any]:
    loaded = _load_account_meta(account_json)
    meta = loaded["meta"]
    work_session = _copy_session_workspace(loaded["session_path"])
    work_dir = work_session.parent
    api_id = int(meta.get("app_id") or meta.get("api_id") or 0)
    api_hash = str(meta.get("app_hash") or meta.get("api_hash") or "").strip()
    proxy_dict = to_telethon_proxy(proxy)
    info: Dict[str, Any] = {
        "account": str(account_json),
        "phone": str(meta.get("phone") or account_json.stem),
        "proxy": format_proxy_endpoint(proxy),
        "identity": proxy_identity(proxy),
        "egress_ip": proxy.get("egress_ip"),
        "ok": False,
    }
    if not api_id or not api_hash:
        info["error"] = "账号 JSON 缺少 app_id / app_hash"
        logger.error("[预检] FAIL %s: %s", account_json.name, info["error"])
        shutil.rmtree(work_dir, ignore_errors=True)
        return info
    if not proxy_dict:
        info["error"] = "代理无效，拒绝直连"
        logger.error("[预检] FAIL %s: %s", account_json.name, info["error"])
        shutil.rmtree(work_dir, ignore_errors=True)
        return info

    client = TelegramClient(
        session=_telethon_session_base(work_session),
        api_id=api_id,
        api_hash=api_hash,
        proxy=proxy_dict,
        **(_client_kwargs(meta)),
    )
    try:
        await connect_telethon_with_timeout(client, timeout=CONNECT_TIMEOUT)
        me = await client.get_me()
        if not me:
            info["error"] = "get_me 为空"
            logger.error("[预检] FAIL %s get_me 为空", account_json.name)
            return info
        info["ok"] = True
        info["user_id"] = me.id
        info["me_phone"] = me.phone
        logger.info(
            "[预检] OK %s user_id=%s phone=%s proxy=%s egress=%s",
            account_json.name,
            me.id,
            me.phone,
            info["proxy"],
            info.get("egress_ip"),
        )
        return info
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("[预检] FAIL %s: %s", account_json.name, info["error"])
        return info
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        shutil.rmtree(work_dir, ignore_errors=True)


class RentGate:
    """租号闸：在 ≤max_price 的 SMSCode 产品通道间轮换；API 调用串行，sleep 放锁外。"""

    def __init__(self, sms: SmsCodeService, country: str = TARGET_COUNTRY) -> None:
        self._lock = asyncio.Lock()
        self.sms = sms
        self.country = country
        self.rented = 0
        self.no_stock_hits = 0
        self.failures = 0
        self._products: List[Dict[str, Any]] = []
        self._cursor = 0
        self._product_stats: Counter = Counter()
        self.rotate_products = True

    async def refresh_products(self, max_price: float) -> List[Dict[str, Any]]:
        rows = await self.sms.list_priced_products(
            self.country, service="tg", max_price=max_price
        )
        self._products = [r for r in rows if r.get("product_id")]
        self._cursor = 0
        logger.info(
            "[租号闸] 刷新产品通道 max_price=%.4f count=%d → %s",
            max_price,
            len(self._products),
            [
                f"{r.get('product_id')}@${float(r.get('price') or 0):.4f}/stk={r.get('available')}"
                for r in self._products
            ],
        )
        return self._products

    def _next_product(self) -> Optional[Dict[str, Any]]:
        if not self._products:
            return None
        prod = self._products[self._cursor % len(self._products)]
        self._cursor += 1
        return prod

    def demote_product(self, product_id: Any, reason: str = "") -> None:
        """某通道接码失败后暂时移出轮换，优先试其它供应商档位。"""
        if product_id is None:
            return
        before = len(self._products)
        self._products = [p for p in self._products if p.get("product_id") != product_id]
        if len(self._products) != before:
            logger.warning(
                "[租号闸] 降级通道 product_id=%s reason=%s 剩余=%d",
                product_id,
                reason or "n/a",
                len(self._products),
            )

    async def rent(self, max_price: float) -> Tuple[str, str, Dict[str, Any]]:
        last_err: Optional[BaseException] = None
        meta: Dict[str, Any] = {}
        for i in range(1, RENT_INNER_RETRIES + 1):
            try:
                async with self._lock:
                    if self.rotate_products and not self._products:
                        await self.refresh_products(max_price)
                    product = self._next_product() if self.rotate_products else None
                    if product and product.get("product_id"):
                        meta = {
                            "product_id": product.get("product_id"),
                            "catalog_product_id": product.get("catalog_product_id"),
                            "product_price": product.get("price"),
                            "product_name": product.get("name"),
                            "product_available": product.get("available"),
                        }
                        act_id, phone = await self.sms.get_number(
                            self.country,
                            service="tg",
                            product_id=int(product["product_id"]),
                        )
                    else:
                        meta = {"product_id": None, "mode": "catalog_cheapest"}
                        act_id, phone = await self.sms.get_number(
                            self.country, service="tg", max_price=max_price
                        )
                self.rented += 1
                pid = meta.get("product_id")
                if pid is not None:
                    self._product_stats[str(pid)] += 1
                logger.info(
                    "[租号闸] 成功 #%d order=%s phone=%s product_id=%s price=%s (inner=%d)",
                    self.rented,
                    act_id,
                    phone,
                    meta.get("product_id"),
                    meta.get("product_price"),
                    i,
                )
                return str(act_id), _normalize_plus(phone), meta
            except NoNumberAvailableError as exc:
                self.no_stock_hits += 1
                last_err = exc
                # 当前通道无号：刷新目录并跳过该 product
                async with self._lock:
                    dead = meta.get("product_id")
                    if dead is not None:
                        self._products = [
                            p for p in self._products if p.get("product_id") != dead
                        ]
                        logger.warning(
                            "[租号闸] 通道 product_id=%s 无号，剩余通道 %d",
                            dead,
                            len(self._products),
                        )
                    else:
                        await self.refresh_products(max_price)
                logger.warning(
                    "[租号闸] VE 无库存 inner=%d/%d: %s",
                    i,
                    RENT_INNER_RETRIES,
                    exc,
                )
                await asyncio.sleep(RENT_NO_STOCK_SLEEP)
            except InsufficientBalanceError as exc:
                self.failures += 1
                last_err = exc
                logger.error("[租号闸] 余额不足 inner=%d: %s", i, exc)
                await asyncio.sleep(RENT_ERROR_SLEEP)
            except Exception as exc:
                self.failures += 1
                last_err = exc
                logger.warning("[租号闸] 失败 inner=%d: %s", i, exc)
                await asyncio.sleep(RENT_ERROR_SLEEP)
        raise RuntimeError(f"租号失败（{RENT_INNER_RETRIES} 次）: {last_err}")


def classify_error(record: Dict[str, Any]) -> str:
    if record.get("success"):
        return "SUCCESS"
    err = str(record.get("error") or "")
    stage = str(record.get("stage") or "")
    blob = f"{stage} {err}"
    upper = blob.upper()
    if "OCCUPIED" in upper or "Occupied" in err:
        return "OCCUPIED"
    if "BANNED" in upper or "PhoneNumberBanned" in err:
        return "BANNED"
    if "FLOOD_WAIT" in upper:
        return "FLOOD_WAIT"
    if "RECAPTCHA" in upper or "REGHelp" in blob or "Recaptcha" in blob:
        return "RECAPTCHA"
    if stage == "sms_wait" or "等待 SMSCode" in err or "等待 SMS" in err or err.startswith("TimeoutError"):
        return "SMS_TIMEOUT"
    if "租号失败" in err or "NoNumber" in err or "noNumber" in err or "NO_STOCK" in upper or "NO_NUMBERS" in upper:
        return "NO_STOCK"
    if "NO_BALANCE" in upper or "余额不足" in err:
        return "NO_BALANCE"
    if "get_me" in blob or "BAD_SESSION" in upper or "AuthKey" in err or "session 未授权" in err:
        return "BAD_SESSION"
    if err.startswith("PHONE_CODE:") or "PhoneCodeInvalid" in err or "PhoneCodeExpired" in err:
        return "PHONE_CODE"
    if "SentCodeTypeApp" in err or ("APP" in upper and "无法收码" in err):
        return "APP_DELIVERY"
    if "SentCodeTypeCall" in err or "切短信" in err:
        return "CALL_ONLY"
    if ":" in err:
        return err.split(":", 1)[0].strip()[:40] or "UNKNOWN"
    return "UNKNOWN"


async def wait_smscode(
    sms: SmsCodeService,
    act_id: str,
    max_attempts: int,
    label: str,
) -> str:
    async def _log(msg: str) -> None:
        logger.info("[%s] %s", label, msg)

    code = await sms.wait_for_code(
        act_id,
        max_attempts=max_attempts,
        interval=SMS_POLL_INTERVAL,
        log_callback=_log,
    )
    logger.info("[%s] SMSCode 收到验证码 len=%d", label, len(str(code)))
    return str(code).strip()


async def cancel_smscode(sms: SmsCodeService, act_id: Optional[str], label: str) -> None:
    if not act_id:
        return
    try:
        res = await sms.cancel(act_id)
        logger.info("[%s] SMSCode cancel order=%s -> %s", label, act_id, res)
        if not res.get("success"):
            raw = str(res.get("data") or res.get("error") or "")
            if "EARLY_CANCEL" in raw.upper() or "DENIED" in raw.upper():
                logger.info("[%s] EARLY_CANCEL_DENIED，20s 后重试 cancel", label)
                await asyncio.sleep(20)
                res2 = await sms.cancel(act_id)
                logger.info("[%s] SMSCode cancel-retry order=%s -> %s", label, act_id, res2)
    except Exception as exc:
        logger.warning("[%s] SMSCode cancel 失败: %s", label, exc)


async def finish_smscode(sms: SmsCodeService, act_id: Optional[str], label: str) -> None:
    if not act_id:
        return
    try:
        res = await sms.finish(act_id)
        logger.info("[%s] SMSCode finish order=%s -> %s", label, act_id, res)
    except Exception as exc:
        logger.warning("[%s] SMSCode finish 失败: %s", label, exc)


async def run_rebind_once(
    account_json: Path,
    proxy: Dict[str, Any],
    order_id: str,
    new_phone: str,
    sms: SmsCodeService,
    *,
    dry_run: bool = False,
    sms_attempts: int = 45,
    label: str = "",
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
        "identity": proxy_identity(proxy),
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
        logger.info("[MTProto][%s] 连接 session=%s proxy=%s", label, session_src.name, result["proxy"])
        await connect_telethon_with_timeout(client, timeout=CONNECT_TIMEOUT)
        me = await client.get_me()
        if not me:
            raise RuntimeError("get_me() 为空，session 未授权")
        logger.info(
            "[MTProto][%s] 已登录 user_id=%s phone=%s username=%s",
            label,
            me.id,
            me.phone,
            me.username,
        )
        result["user_id"] = me.id
        result["me_phone_before"] = me.phone

        if dry_run:
            logger.info("[dry-run][%s] 跳过 sendChangePhoneCode / changePhone", label)
            return result

        settings = _build_code_settings()
        logger.info("[RPC][%s] account.sendChangePhoneCode → %s", label, new_phone)
        sent = await send_change_phone_code(client, new_phone, settings, meta, proxy)
        sent = await resolve_change_phone_sent_code(client, new_phone, sent)
        sent_type = _sent_code_type_name(sent)
        logger.info(
            "[RPC][%s] 最终发码通道 type=%s next_type=%s timeout=%s hash=%s",
            label,
            sent_type,
            type(getattr(sent, "next_type", None)).__name__ if getattr(sent, "next_type", None) else None,
            getattr(sent, "timeout", None),
            bool(getattr(sent, "phone_code_hash", None)),
        )
        result["sent_code_type"] = sent_type
        result["phone_code_hash"] = sent.phone_code_hash

        sms_code = await wait_smscode(sms, order_id, sms_attempts, label)
        logger.info("[RPC][%s] account.changePhone ← code_len=%d", label, len(sms_code))
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
            logger.info("[RPC][%s] changePhone 需 2FA，sign_in(password=...)", label)
            await client.sign_in(password=pwd)
            updated = await client(change_req)
        me_after = await client.get_me()
        logger.info(
            "[RPC][%s] changePhone 成功 user_id=%s phone=%s",
            label,
            updated.id if updated else None,
            me_after.phone if me_after else None,
        )
        result["success"] = True
        result["me_phone_after"] = me_after.phone if me_after else None
        await finish_smscode(sms, order_id, label)
        return result
    except PhoneNumberOccupiedError as exc:
        result["error"] = f"PHONE_NUMBER_OCCUPIED: {exc}"
        logger.error("[RPC][%s] 新号已在 Telegram 注册: %s", label, exc)
        await cancel_smscode(sms, order_id, label)
        raise
    except SessionPasswordNeededError as exc:
        result["error"] = f"SESSION_PASSWORD_NEEDED: {exc}"
        logger.error("[RPC][%s] 需要 2FA 密码", label)
        await cancel_smscode(sms, order_id, label)
        raise
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
        result["error"] = f"PHONE_CODE: {exc}"
        logger.error("[RPC][%s] 验证码无效/过期: %s", label, exc)
        await cancel_smscode(sms, order_id, label)
        raise
    except FloodWaitError as exc:
        result["error"] = f"FLOOD_WAIT_{exc.seconds}"
        result["flood_seconds"] = int(getattr(exc, "seconds", 0) or 0)
        logger.error("[RPC][%s] FLOOD_WAIT %ss", label, exc.seconds)
        await cancel_smscode(sms, order_id, label)
        raise
    except TimeoutError as exc:
        # 把已解析的发码通道带进异常，便于 jsonl 归因
        tip = result.get("sent_code_type") or "?"
        result["error"] = f"TimeoutError: {exc}"
        logger.error("[RPC][%s] 等码超时 sent_type=%s: %s", label, tip, exc)
        await cancel_smscode(sms, order_id, label)
        raise TimeoutError(
            f"SMS_TIMEOUT sent_code_type={tip} phone={new_phone}: {exc}"
        ) from exc
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("[RPC][%s] 失败: %s", label, exc)
        logger.debug(traceback.format_exc())
        await cancel_smscode(sms, order_id, label)
        raise
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        shutil.rmtree(work_dir, ignore_errors=True)


_JSONL_LOCK = asyncio.Lock()


async def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with _JSONL_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


async def one_attempt(
    account_json: Path,
    proxy: Dict[str, Any],
    attempt_idx: int,
    tries: int,
    max_price: float,
    sms_attempts: int,
    rent_gate: RentGate,
    sms: SmsCodeService,
    result_path: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    label = f"{account_json.stem}#{attempt_idx}"
    t0 = time.time()
    record: Dict[str, Any] = {
        "ts": _utc_now(),
        "account": str(account_json),
        "account_stem": account_json.stem,
        "attempt": attempt_idx,
        "tries": tries,
        "country": TARGET_COUNTRY,
        "sms_provider": "smscode",
        "proxy": format_proxy_endpoint(proxy),
        "identity": proxy_identity(proxy),
        "egress_ip": proxy.get("egress_ip"),
        "egress_country": proxy.get("egress_country_code") or proxy.get("country_code") or "",
        "success": False,
        "dry_run": dry_run,
    }
    logger.info(
        "=== [%s] 第 %d/%d 次 | proxy=%s egress=%s ===",
        label,
        attempt_idx,
        tries,
        record["proxy"],
        record.get("egress_ip"),
    )
    order_id = ""
    rent_trail: List[Dict[str, str]] = []
    occupied_skips = 0
    try:
        if dry_run:
            result = await run_rebind_once(
                account_json, proxy, "", "", sms,
                dry_run=True, sms_attempts=sms_attempts, label=label,
            )
            record.update({k: v for k, v in result.items() if k not in record})
            record["success"] = True
            record["stage"] = "dry_run"
        else:
            last_occupied: Optional[BaseException] = None
            for rent_i in range(1, OCCUPIED_RENT_RETRIES + 1):
                order_id, new_phone, product_meta = await rent_gate.rent(max_price)
                rent_trail.append({
                    "order_id": order_id,
                    "phone": new_phone,
                    "product_id": product_meta.get("product_id"),
                    "product_price": product_meta.get("product_price"),
                })
                record["order_id"] = order_id
                record["new_phone"] = new_phone
                record["product_id"] = product_meta.get("product_id")
                record["product_price"] = product_meta.get("product_price")
                record["catalog_product_id"] = product_meta.get("catalog_product_id")
                record["rent_index"] = rent_i
                record["occupied_skips"] = occupied_skips
                try:
                    result = await run_rebind_once(
                        account_json, proxy, order_id, new_phone, sms,
                        dry_run=False, sms_attempts=sms_attempts, label=label,
                    )
                    record.update({k: v for k, v in result.items() if k not in record or k in {
                        "success", "me_phone_before", "me_phone_after", "user_id",
                        "sent_code_type", "phone_code_hash", "error",
                    }})
                    if result.get("success"):
                        record["success"] = True
                        record["stage"] = "done"
                        logger.info(
                            "✅ [%s] 换绑成功 new=%s order=%s occupied_skips=%d",
                            label, new_phone, order_id, occupied_skips,
                        )
                    else:
                        record["stage"] = "rebind"
                        record["error"] = result.get("error") or "run_rebind 未返回 success"
                    break
                except PhoneNumberOccupiedError as exc:
                    last_occupied = exc
                    occupied_skips += 1
                    record["occupied_skips"] = occupied_skips
                    logger.warning(
                        "[%s] 号已占用 phone=%s，自动重租 %d/%d",
                        label, new_phone, occupied_skips, OCCUPIED_RENT_RETRIES,
                    )
                    # cancel 已在 run_rebind_once 内完成
                    if rent_i >= OCCUPIED_RENT_RETRIES:
                        record["stage"] = "sendChangePhoneCode"
                        record["error"] = (
                            f"PHONE_NUMBER_OCCUPIED x{occupied_skips}: {exc}"
                        )
                    continue
            record["rent_trail"] = rent_trail[-8:]
            if (
                not record.get("success")
                and not record.get("error")
                and last_occupied is not None
            ):
                record["stage"] = "sendChangePhoneCode"
                record["error"] = f"PHONE_NUMBER_OCCUPIED: {last_occupied}"
    except PhoneNumberBannedError as exc:
        record["stage"] = "sendChangePhoneCode"
        record["error"] = f"PHONE_NUMBER_BANNED: {exc}"
    except PhoneNumberOccupiedError as exc:
        # 理论上已被内层循环吃掉；保留兜底。
        record["stage"] = "sendChangePhoneCode"
        record["error"] = f"PHONE_NUMBER_OCCUPIED: {exc}"
        record["occupied_skips"] = max(occupied_skips, 1)
    except SessionPasswordNeededError as exc:
        record["stage"] = "changePhone"
        record["error"] = f"SESSION_PASSWORD_NEEDED: {exc}"
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
        record["stage"] = "changePhone"
        record["error"] = f"PHONE_CODE: {exc}"
    except FloodWaitError as exc:
        record["stage"] = "rpc"
        record["error"] = f"FLOOD_WAIT_{exc.seconds}"
        record["flood_seconds"] = int(getattr(exc, "seconds", 0) or 0)
    except TimeoutError as exc:
        record["stage"] = "sms_wait"
        record["error"] = f"TimeoutError: {exc}"
        msg = str(exc)
        if "sent_code_type=" in msg:
            try:
                record["sent_code_type"] = msg.split("sent_code_type=", 1)[1].split()[0]
            except Exception:
                pass
    except Exception as exc:
        msg = str(exc)
        if "租号失败" in msg or isinstance(exc, NoNumberAvailableError):
            record["stage"] = "rent"
        elif "sendChangePhoneCode" in msg or "RECAPTCHA" in msg.upper():
            record["stage"] = "sendChangePhoneCode"
        elif "get_me" in msg:
            record["stage"] = "get_me"
        else:
            record["stage"] = "unknown"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()[-2000:]
        logger.error("[%s] 异常: %s", label, record["error"])
        if order_id and record["stage"] not in {"rent"}:
            await cancel_smscode(sms, order_id, label)
    if rent_trail and "rent_trail" not in record:
        record["rent_trail"] = rent_trail[-8:]
    record["occupied_skips"] = occupied_skips

    record["elapsed_s"] = round(time.time() - t0, 1)
    record["class"] = classify_error(record)
    if record.get("class") == "SMS_TIMEOUT" and record.get("product_id") is not None:
        rent_gate.demote_product(record.get("product_id"), reason="SMS_TIMEOUT")
    logger.info(
        "→ [%s] #%d class=%s stage=%s elapsed=%ss error=%s",
        label,
        attempt_idx,
        record["class"],
        record.get("stage"),
        record["elapsed_s"],
        (record.get("error") or "")[:180],
    )
    await append_jsonl(result_path, record)
    return record


async def account_worker(
    account_json: Path,
    proxy: Dict[str, Any],
    tries: int,
    max_price: float,
    sms_attempts: int,
    rent_gate: RentGate,
    sms: SmsCodeService,
    result_path: Path,
    dry_run: bool,
    pause_between: float,
    sem: asyncio.Semaphore,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    async with sem:
        for i in range(1, tries + 1):
            row = await one_attempt(
                account_json, proxy, i, tries, max_price, sms_attempts,
                rent_gate, sms, result_path, dry_run,
            )
            rows.append(row)
            flood = int(row.get("flood_seconds") or 0)
            if flood > 300:
                logger.warning(
                    "[%s] FLOOD_WAIT %ss 过长，跳过该账号剩余尝试（批次其它账号继续）",
                    account_json.stem,
                    flood,
                )
                break
            if i < tries and pause_between > 0:
                await asyncio.sleep(pause_between)
    return rows


async def probe_smscode(sms: SmsCodeService) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "provider": "smscode",
        "endpoint": getattr(sms, "base_url", None) or getattr(SmsCodeService, "BASE_URL", "https://api.smscode.gg"),
    }
    try:
        bal = await sms.get_balance()
        info["balance"] = bal
        cid = await sms.resolve_country_id(TARGET_COUNTRY)
        info["country_id"] = cid
        try:
            priced = await sms.list_priced_products(
                TARGET_COUNTRY, service="tg", max_price=0.8
            )
            info["products"] = len(priced)
            info["channels_le_0_8"] = [
                {
                    "product_id": r.get("product_id"),
                    "price": r.get("price"),
                    "available": r.get("available"),
                    "name": r.get("name"),
                }
                for r in priced
            ]
        except Exception as exc:
            info["products_error"] = f"{type(exc).__name__}: {exc}"
        logger.info(
            "[SMSCode] 余额=%s country=%s country_id=%s channels(≤0.8)=%s detail=%s",
            bal,
            TARGET_COUNTRY.upper(),
            cid,
            info.get("products"),
            info.get("channels_le_0_8"),
        )
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("[SMSCode] 探针失败: %s", info["error"])
    return info



def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    # httpx INFO 会把 api_key 打进完整 URL，降到 WARNING 避免密钥进日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main_async(args: argparse.Namespace) -> int:
    set_target_country(getattr(args, "country", None) or TARGET_COUNTRY)
    data_dir = REPO_ROOT / "data"
    log_path = Path(args.log_file) if args.log_file else data_dir / RUN_LOG
    if not log_path.is_absolute():
        log_path = REPO_ROOT / log_path
    _setup_logging(log_path)

    account_dir = Path(args.account_dir)
    if not account_dir.is_absolute():
        account_dir = REPO_ROOT / account_dir
    accounts = discover_accounts(account_dir)
    if args.max_accounts and args.max_accounts > 0:
        accounts = accounts[: args.max_accounts]
    if not accounts:
        logger.error("目录无账号: %s", account_dir)
        return 2

    cfg = ConfigManager.get_instance().config
    key, key_source = _smscode_key(cfg, args.api_key)
    if not key:
        logger.error("SMSCode API key 为空（请设 config.smscode_api_key 或环境变量 SMSCODE_API_KEY）")
        return 2
    logger.info("[SMSCode] 使用密钥来源=%s len=%d", key_source, len(key))

    sms = SmsCodeService(key)
    rent_gate = RentGate(sms, TARGET_COUNTRY)
    await rent_gate.refresh_products(args.max_price)
    sms_info = await probe_smscode(sms)
    if sms_info.get("error") and not args.dry_run:
        logger.error("SMSCode 不可用，中止: %s", sms_info["error"])
        await sms.close()
        return 2

    need = min(args.concurrency, len(accounts))
    logger.info(
        "并发换绑启动: accounts=%d tries_each=%d concurrency=%d target=VE sms=SMSCode "
        "max_price=%.2f → 目标租号 %d key_source=%s",
        len(accounts),
        args.tries_per_account,
        args.concurrency,
        args.max_price,
        len(accounts) * args.tries_per_account,
        key_source,
    )

    proxies, proxy_meta = await fetch_country_proxy_pool(need, probe=not args.skip_proxy_probe)
    n_bind = min(len(accounts), len(proxies), args.concurrency)
    pairs = list(zip(accounts[:n_bind], proxies[:n_bind]))
    skipped_no_proxy = accounts[n_bind:]
    if skipped_no_proxy:
        logger.warning("代理不足，跳过 %d 个账号: %s", len(skipped_no_proxy), [p.stem for p in skipped_no_proxy])

    preflight_rows = await asyncio.gather(*[preflight_account(acc, proxy) for acc, proxy in pairs])
    ok_pairs = [
        (acc, proxy, pf)
        for (acc, proxy), pf in zip(pairs, preflight_rows)
        if pf.get("ok")
    ]
    logger.info("[预检] 可用 %d/%d", len(ok_pairs), len(pairs))
    skipped_preflight = []
    for (acc, proxy), pf in zip(pairs, preflight_rows):
        if not pf.get("ok"):
            logger.warning("[预检跳过] %s err=%s", acc.name, pf.get("error"))
            skipped_preflight.append({"account": acc.stem, "error": pf.get("error")})

    result_path = Path(args.result_file) if args.result_file else data_dir / RESULT_JSONL
    summary_path = Path(args.summary_file) if args.summary_file else data_dir / SUMMARY_JSON
    if not result_path.is_absolute():
        result_path = REPO_ROOT / result_path
    if not summary_path.is_absolute():
        summary_path = REPO_ROOT / summary_path
    if args.fresh_results and result_path.exists():
        result_path.unlink()

    binding_table = [
        {
            "account": acc.stem,
            "proxy": format_proxy_endpoint(proxy),
            "identity": proxy_identity(proxy),
            "egress_ip": proxy.get("egress_ip"),
            "egress_country": proxy.get("egress_country_code") or proxy.get("country_code"),
            "preflight_ok": bool(pf.get("ok")),
            "preflight_user_id": pf.get("user_id"),
            "preflight_phone": pf.get("me_phone"),
            "preflight_error": pf.get("error"),
        }
        for (acc, proxy), pf in zip(pairs, preflight_rows)
    ]

    if args.check_only:
        summary = {
            "ts": _utc_now(),
            "mode": "check_only",
            "country": TARGET_COUNTRY,
            "sms_provider": "smscode",
            "key_source": key_source,
            "sms": sms_info,
            "proxy_meta": proxy_meta,
            "binding": binding_table,
            "preflight_ok": sum(1 for r in preflight_rows if r.get("ok")),
            "preflight_fail": skipped_preflight,
            "skipped_no_proxy": [p.stem for p in skipped_no_proxy],
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[check-only] 已写入 %s", summary_path)
        await sms.close()
        return 0 if ok_pairs else 1

    if not ok_pairs:
        logger.error("预检后无可用账号，中止")
        summary = {
            "ts": _utc_now(),
            "country": TARGET_COUNTRY,
            "sms_provider": "smscode",
            "key_source": key_source,
            "sms": sms_info,
            "proxy_meta": proxy_meta,
            "binding": binding_table,
            "preflight_fail": skipped_preflight,
            "error": "预检后无可用账号",
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        await sms.close()
        return 1

    t0 = time.time()
    sem = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [
        account_worker(
            acc, proxy, args.tries_per_account, args.max_price, args.sms_attempts,
            rent_gate, sms, result_path, args.dry_run, args.pause_between, sem,
        )
        for acc, proxy, _pf in ok_pairs
    ]
    nested = await asyncio.gather(*tasks, return_exceptions=True)
    rows: List[Dict[str, Any]] = []
    for item in nested:
        if isinstance(item, Exception):
            logger.error("worker 崩溃: %s", item)
            rows.append({
                "success": False,
                "class": "WORKER_CRASH",
                "error": f"{type(item).__name__}: {item}",
            })
        else:
            rows.extend(item)

    counter = Counter(str(r.get("class") or "UNKNOWN") for r in rows)
    success_n = sum(1 for r in rows if r.get("success"))
    success_rows = [r for r in rows if r.get("success") and not r.get("dry_run")]
    summary = {
        "ts": _utc_now(),
        "elapsed_s": round(time.time() - t0, 1),
        "country": TARGET_COUNTRY,
        "sms_provider": "smscode",
        "key_source": key_source,
        "sms": sms_info,
        "proxy_meta": proxy_meta,
        "binding": binding_table,
        "accounts_total": len(accounts),
        "accounts_bound": len(pairs),
        "accounts_preflight_ok": len(ok_pairs),
        "skipped_preflight": skipped_preflight,
        "skipped_no_proxy": [p.stem for p in skipped_no_proxy],
        "tries_per_account": args.tries_per_account,
        "concurrency": args.concurrency,
        "max_price": args.max_price,
        "product_channels": [
            {
                "product_id": r.get("product_id"),
                "price": r.get("price"),
                "available": r.get("available"),
                "name": r.get("name"),
            }
            for r in getattr(rent_gate, "_products", []) or []
        ],
        "product_rent_stats": dict(getattr(rent_gate, "_product_stats", {}) or {}),
        "target_rents": len(ok_pairs) * args.tries_per_account,
        "rented": rent_gate.rented,
        "no_stock_hits": rent_gate.no_stock_hits,
        "rent_failures": rent_gate.failures,
        "attempts": len(rows),
        "success": success_n,
        "classes": dict(counter),
        "success_detail": [
            {
                "account": r.get("account_stem"),
                "old_phone": r.get("old_phone") or r.get("me_phone_before"),
                "new_phone": r.get("new_phone") or r.get("me_phone_after"),
                "order_id": r.get("order_id"),
                "sent_code_type": r.get("sent_code_type"),
                "proxy": r.get("proxy"),
                "egress_ip": r.get("egress_ip"),
            }
            for r in success_rows
        ],
        "result_file": str(result_path),
        "log_file": str(log_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "结束: rented=%d attempts=%d success=%d classes=%s → %s",
        rent_gate.rented,
        len(rows),
        success_n,
        dict(counter),
        summary_path,
    )
    await sms.close()
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="换绑并发探测（SMSCode + 目标国对齐代理）")
    p.add_argument("--country", default="co", help="目标国 ISO-2（号码+代理对齐，默认 co）")
    p.add_argument("--account-dir", default=DEFAULT_ACCOUNT_DIR)
    p.add_argument("--tries-per-account", type=int, default=5)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--max-accounts", type=int, default=0)
    p.add_argument("--max-price", type=float, default=0.8, help="SMSCode 租号最高出价 USD；会在 ≤该价的产品通道间轮换")
    p.add_argument("--sms-attempts", type=int, default=60, help="每轮 SMS 轮询次数（×约4s）")
    p.add_argument("--pause-between", type=float, default=1.0)
    p.add_argument("--skip-proxy-probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check-only", action="store_true")
    p.add_argument("--fresh-results", action="store_true")
    p.add_argument("--result-file", default="")
    p.add_argument("--summary-file", default="")
    p.add_argument("--log-file", default="")
    p.add_argument("--api-key", default="", help="覆盖 config.smscode_api_key（勿写入仓库）")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
