#!/usr/bin/env python3
"""PaymentRequired 触发率调查：official 模拟 + SMS Bower Email 全流程。

每轮临时写入 official_client_emulation / smsbower_only / hunt 轮换参数，
跑完无条件恢复启动时的 config 快照。

用法::

    python3 backend/scripts/run_payment_required_survey.py \\
        --country iq --threads 10 --attempts-per-thread 2 \\
        --refresh-device --refresh-proxy

    python3 backend/scripts/run_payment_required_survey.py \\
        --country id --threads 5 --attempts-per-thread 2 \\
        --control-label id_official_control
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.scripts.run_code_delivery_ab import (  # noqa: E402
    ApiClient,
    utc_now,
    wait_batch,
)
from backend.scripts.run_registration_sprint import (  # noqa: E402
    parse_task_evidence,
    snapshot_balances,
    summarize_evidence,
)

PAYMENT_LOG_RE = re.compile(
    r"SentCodePaymentRequired.*?store_product=(?P<product>\S+).*?"
    r"currency=(?P<currency>\S+)\s+amount=(?P<amount>\S+)"
)
PAYMENT_SIMPLE_RE = re.compile(r"SentCodePaymentRequired")
EMAIL_VERIFY_RE = re.compile(r"verifyEmail|account\.verifyEmail|邮箱验证")
SMS_AFTER_EMAIL_RE = re.compile(
    r"分发通道类型:.*SentCodeTypeSms|分发通道为运营商短信"
)


def apply_survey_config(
    client: ApiClient,
    snapshot: Dict[str, Any],
    *,
    refresh_device: bool,
    refresh_proxy: bool,
) -> Dict[str, Any]:
    cfg = dict(snapshot)
    cfg["official_client_emulation"] = True
    cfg["api_credential_mode"] = "official"
    cfg["code_delivery_mode"] = "push_required"
    cfg["active_app_type"] = "telegram_android"
    cfg["email_provider_mode"] = "smsbower_only"
    if refresh_device:
        cfg["hunt_device_max_uses"] = 1
    if refresh_proxy:
        cfg["hunt_proxy_max_uses"] = 1
    saved = client.request("POST", "/api/config", cfg)
    return {
        "official_client_emulation": bool(saved.get("official_client_emulation")),
        "api_credential_mode": saved.get("api_credential_mode"),
        "code_delivery_mode": saved.get("code_delivery_mode"),
        "email_provider_mode": saved.get("email_provider_mode"),
        "active_app_type": saved.get("active_app_type"),
        "custom_api_id": saved.get("custom_api_id"),
        "hunt_device_max_uses": saved.get("hunt_device_max_uses"),
        "hunt_proxy_max_uses": saved.get("hunt_proxy_max_uses"),
    }


def enrich_row(row: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    logs = list(task.get("logs") or [])
    blob = "\n".join(logs)
    pay_info = task.get("payment_required")
    payment_hits = list(PAYMENT_SIMPLE_RE.finditer(blob))
    email_verify = bool(EMAIL_VERIFY_RE.search(blob))
    sms_after_email = bool(SMS_AFTER_EMAIL_RE.search(blob))
    products: List[str] = []
    for m in PAYMENT_LOG_RE.finditer(blob):
        products.append(m.group("product"))
    out = dict(row)
    out["payment_required"] = pay_info
    out["payment_log_count"] = len(payment_hits)
    out["email_verified"] = email_verify
    out["sms_after_email"] = sms_after_email
    out["store_products"] = products or ([pay_info.get("store_product")] if pay_info else [])
    out["final_failure_reason"] = _extract_hunt_reason(blob, task.get("error"))
    return out


def _extract_hunt_reason(blob: str, error: Optional[str]) -> Optional[str]:
    for token in (
        "PAYMENT_REQUIRED_OFFICIAL_ONLY",
        "SENT_CODE_TYPE_APP",
        "EMAIL_CODE_UNAVAILABLE",
        "EMAIL_SETUP_FAILED",
        "LOCAL_BANNED",
        "NO_NUMBERS",
    ):
        if token in blob or (error and token in str(error)):
            return token
    if error and "HUNT_EXHAUSTED" in str(error):
        m = re.search(r"最后一次失败原因\s+(\S+)", str(error))
        return m.group(1) if m else "HUNT_EXHAUSTED"
    return None


def analyze_round(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    samples = [s for r in rows for s in r.get("samples") or []]
    type_counts = Counter(s.get("sent_code_type") or "Unknown" for s in samples)
    payment_rows = [r for r in rows if r.get("payment_required") or r.get("payment_log_count")]
    email_then_payment = [
        r for r in rows
        if r.get("email_verified") and (r.get("payment_required") or r.get("payment_log_count"))
    ]
    email_then_sms = [r for r in rows if r.get("email_verified") and r.get("sms_after_email")]
    sendcode_attempts = len(samples)
    payment_samples = sum(1 for s in samples if "PaymentRequired" in str(s.get("sent_code_type", "")))
    leased = sum(int(r.get("leased_numbers") or 0) for r in rows)
    tasks_with_sendcode = sum(1 for r in rows if r.get("samples"))
    return {
        "tasks": len(rows),
        "leased_numbers": leased,
        "tasks_with_sendcode": tasks_with_sendcode,
        "sendcode_samples": sendcode_attempts,
        "sent_code_types": dict(type_counts),
        "payment_task_count": len(payment_rows),
        "payment_sample_count": payment_samples,
        "payment_rate_of_sendcode": round(payment_samples / sendcode_attempts, 4) if sendcode_attempts else None,
        "payment_rate_of_leased": round(len(payment_rows) / leased, 4) if leased else None,
        "email_verified_tasks": sum(1 for r in rows if r.get("email_verified")),
        "email_then_payment": len(email_then_payment),
        "email_then_sms": len(email_then_sms),
        "failure_reasons": dict(Counter(r.get("final_failure_reason") for r in rows if r.get("final_failure_reason"))),
        "devices": dict(Counter(r.get("device") for r in rows if r.get("device"))),
        "api_ids": dict(Counter(a for r in rows for a in (r.get("api_ids") or []))),
        "proxy_countries": dict(Counter(r.get("proxy_country") for r in rows if r.get("proxy_country"))),
    }


def run_country_round(
    client: ApiClient,
    *,
    label: str,
    snapshot: Dict[str, Any],
    country: str,
    sms_provider: str,
    threads: int,
    attempts_per_thread: int,
    max_price: float,
    proxy_mode: str,
    refresh_device: bool,
    refresh_proxy: bool,
    poll: float,
    batch_timeout: float,
) -> Dict[str, Any]:
    applied = apply_survey_config(
        client,
        snapshot,
        refresh_device=refresh_device,
        refresh_proxy=refresh_proxy,
    )
    print(
        f"\n=== {label} === country={country} threads={threads} "
        f"attempts={attempts_per_thread} applied={applied} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type="telegram_android",
        count=threads,
        concurrency=threads,
        sms_provider=sms_provider,
        max_price=max_price,
        max_number_attempts=attempts_per_thread,
        no_number_retries=3,
        proxy_mode=proxy_mode,
    )
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)
    final_batch, tasks, timed_out = wait_batch(client, batch_id, poll, batch_timeout)
    rows = []
    for t in tasks:
        tid = t.get("task_id") or t.get("id")
        full = client.get_task(tid)
        base = parse_task_evidence(full, country)
        rows.append(enrich_row(base, full))
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    print(
        f"    -> 租号={analysis['leased_numbers']} 发码样本={analysis['sendcode_samples']} "
        f"PaymentRequired任务={analysis['payment_task_count']} "
        f"types={analysis['sent_code_types']} 耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "label": label,
        "country": country,
        "sms_provider": sms_provider,
        "threads": threads,
        "attempts_per_thread": attempts_per_thread,
        "applied": applied,
        "proxy_mode": proxy_mode,
        "refresh_device": refresh_device,
        "refresh_proxy": refresh_proxy,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "summary": summary,
        "analysis": analysis,
        "rows": rows,
    }


def load_historical_refs(out_dir: Path) -> Dict[str, Any]:
    refs: Dict[str, Any] = {}
    patterns = (
        "official_emulation_iq_*.json",
        "official_emulation_*.json",
        "code_delivery_ab_*.json",
        "registration_sprint_*.json",
    )
    for pat in patterns:
        for path in sorted(out_dir.glob(pat))[-5:]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            refs[path.name] = {
                "generated_at": data.get("generated_at"),
                "country": data.get("country") or data.get("countries"),
                "payment_mentions": "PaymentRequired" in path.read_text(encoding="utf-8"),
            }
    return refs


def build_comparison(primary: Dict[str, Any], control: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    p = primary.get("analysis") or {}
    c = (control or {}).get("analysis") or {}
    return {
        "primary_country": primary.get("country"),
        "control_country": (control or {}).get("country"),
        "primary_payment_rate": p.get("payment_rate_of_sendcode"),
        "control_payment_rate": c.get("payment_rate_of_sendcode"),
        "primary_email_then_payment": p.get("email_then_payment"),
        "control_email_then_payment": c.get("email_then_payment"),
        "primary_email_then_sms": p.get("email_then_sms"),
        "control_email_then_sms": c.get("email_then_sms"),
        "primary_types": p.get("sent_code_types"),
        "control_types": c.get("sent_code_types"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--country", default="iq")
    parser.add_argument("--control-country", default="", help="对照国家，留空则仅跑主国家")
    parser.add_argument("--control-label", default="control_official")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--attempts-per-thread", type=int, default=2)
    parser.add_argument("--control-threads", type=int, default=5)
    parser.add_argument("--control-attempts", type=int, default=2)
    parser.add_argument("--max-price", type=float, default=1.2)
    parser.add_argument("--proxy-mode", default="auto", help="auto=猎号轮换 proxy_seller 出口")
    parser.add_argument("--refresh-device", action="store_true")
    parser.add_argument("--refresh-proxy", action="store_true")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=1800.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    parser.add_argument("--skip-primary", action="store_true")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"启动快照 emu={original.get('official_client_emulation')} "
        f"email={original.get('email_provider_mode')} api_mode={original.get('api_credential_mode')}",
        flush=True,
    )
    balances_before = snapshot_balances(client)
    rounds: List[Dict[str, Any]] = []

    try:
        if not args.skip_primary:
            rounds.append(run_country_round(
                client,
                label=f"primary_{args.country}_official",
                snapshot=original,
                country=args.country.lower(),
                sms_provider=args.sms_provider,
                threads=args.threads,
                attempts_per_thread=args.attempts_per_thread,
                max_price=args.max_price,
                proxy_mode=args.proxy_mode,
                refresh_device=args.refresh_device,
                refresh_proxy=args.refresh_proxy,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
            ))
        if args.control_country:
            rounds.append(run_country_round(
                client,
                label=args.control_label,
                snapshot=original,
                country=args.control_country.lower(),
                sms_provider=args.sms_provider,
                threads=args.control_threads,
                attempts_per_thread=args.control_attempts,
                max_price=args.max_price,
                proxy_mode=args.proxy_mode,
                refresh_device=args.refresh_device,
                refresh_proxy=args.refresh_proxy,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
            ))
    finally:
        restored = client.request("POST", "/api/config", original)
        print(
            f"\n已恢复 config emu={restored.get('official_client_emulation')} "
            f"email={restored.get('email_provider_mode')}",
            flush=True,
        )

    balances_after = snapshot_balances(client)
    primary = rounds[0] if rounds else {}
    control = rounds[1] if len(rounds) > 1 else None
    report = {
        "generated_at": utc_now(),
        "mode": "payment_required_survey",
        "config_applied": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "email_provider_mode": "smsbower_only",
            "refresh_device": args.refresh_device,
            "refresh_proxy": args.refresh_proxy,
        },
        "balances_before": balances_before,
        "balances_after": balances_after,
        "historical_refs": load_historical_refs(out_dir),
        "rounds": rounds,
        "comparison": build_comparison(primary, control) if control else None,
        "mechanism_notes": {
            "telegram_doc": (
                "auth.sentCodePaymentRequired: official apps only; triggered when SMS cost "
                "for user's country/provider is high — requires Premium purchase."
            ),
            "observed_iq_flow": "sendCode → SetUpEmailRequired → verifyEmail(smsbower) → PaymentRequired",
            "non_official_iq_flow": "balanced+custom api_id → SentCodeTypeApp (no PaymentRequired in historical runs)",
        },
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"payment_survey_{args.country}_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 88)
    for item in rounds:
        a = item.get("analysis") or {}
        print(
            f"{item.get('label'):<32} country={item.get('country'):<4} "
            f"租号={a.get('leased_numbers',0):>3} 发码={a.get('sendcode_samples',0):>3} "
            f"Payment任务={a.get('payment_task_count',0):>2} "
            f"email→pay={a.get('email_then_payment',0):>2} "
            f"email→sms={a.get('email_then_sms',0):>2} "
            f"types={a.get('sent_code_types')}"
        )
    print("=" * 88)
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
