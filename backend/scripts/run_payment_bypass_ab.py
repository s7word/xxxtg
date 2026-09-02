#!/usr/bin/env python3
"""PaymentRequired 规避 A/B：控制变量对比 official 路径 sent_code 分布。

实验矩阵（每实验 2 号，official_client_emulation + smsbower email）::

    A  api_id=6  telegram_android（基线）
    B  api_id=4  telegram_android_public
    C  api_id=21724  telegram_x
    D  api_id=6  telegram_9（旧版 app_version 9.6.7）
    E  api_id=6  device_max=1 proxy_max=1（全新设备+代理）

用法::

    python3 backend/scripts/run_payment_bypass_ab.py \\
        --country pe --experiments A,B,C,D,E

    python3 backend/scripts/run_payment_bypass_ab.py \\
        --country iq --experiments A,E --threads 2
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
    r"分发通道类型:.*SentCodeTypeSms|分发通道为运营商短信|SentCodeTypeApp"
)

EXPERIMENT_MATRIX: Dict[str, Dict[str, Any]] = {
    "A": {
        "label": "A_api6_baseline",
        "description": "official api_id=6 telegram_android 基线",
        "active_app_type": "telegram_android",
    },
    "B": {
        "label": "B_api4_public",
        "description": "official api_id=4 telegram_android_public",
        "active_app_type": "telegram_android_public",
    },
    "C": {
        "label": "C_telegram_x",
        "description": "telegram_x api_id=21724",
        "active_app_type": "telegram_x",
    },
    "D": {
        "label": "D_telegram9_oldver",
        "description": "api_id=6 telegram_9 模板 app_version=9.6.7",
        "active_app_type": "telegram_9",
    },
    "E": {
        "label": "E_fresh_device_proxy",
        "description": "api_id=6 + hunt_device_max_uses=1 hunt_proxy_max_uses=1",
        "active_app_type": "telegram_android",
        "hunt_device_max_uses": 1,
        "hunt_proxy_max_uses": 1,
    },
}


def apply_experiment_config(
    client: ApiClient,
    snapshot: Dict[str, Any],
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    cfg = dict(snapshot)
    cfg["official_client_emulation"] = True
    cfg["api_credential_mode"] = "official"
    cfg["code_delivery_mode"] = "push_required"
    cfg["email_provider_mode"] = "smsbower_only"
    cfg["active_app_type"] = spec["active_app_type"]
    cfg["hunt_device_max_uses"] = int(spec.get("hunt_device_max_uses", snapshot.get("hunt_device_max_uses") or 8))
    cfg["hunt_proxy_max_uses"] = int(spec.get("hunt_proxy_max_uses", snapshot.get("hunt_proxy_max_uses") or 5))
    saved = client.request("POST", "/api/config", cfg)
    return {
        "official_client_emulation": bool(saved.get("official_client_emulation")),
        "api_credential_mode": saved.get("api_credential_mode"),
        "code_delivery_mode": saved.get("code_delivery_mode"),
        "email_provider_mode": saved.get("email_provider_mode"),
        "active_app_type": saved.get("active_app_type"),
        "hunt_device_max_uses": saved.get("hunt_device_max_uses"),
        "hunt_proxy_max_uses": saved.get("hunt_proxy_max_uses"),
    }


def enrich_row(row: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    logs = list(task.get("logs") or [])
    blob = "\n".join(logs)
    pay_info = task.get("payment_required")
    payment_hits = list(PAYMENT_SIMPLE_RE.finditer(blob))
    email_verify = bool(EMAIL_VERIFY_RE.search(blob))
    sms_or_app_after_email = bool(SMS_AFTER_EMAIL_RE.search(blob))
    products: List[str] = []
    for m in PAYMENT_LOG_RE.finditer(blob):
        products.append(m.group("product"))
    out = dict(row)
    out["payment_required"] = pay_info
    out["payment_log_count"] = len(payment_hits)
    out["email_verified"] = email_verify
    out["sms_or_app_after_email"] = sms_or_app_after_email
    out["store_products"] = products or ([pay_info.get("store_product")] if pay_info else [])
    out["final_failure_reason"] = _extract_hunt_reason(blob, task.get("error"))
    # post-verifyEmail sent_code（verifyEmail 之后的最后一次 sendCode 回包）
    post_email_samples = []
    if email_verify:
        post_blob = blob.split("verifyEmail")[-1] if "verifyEmail" in blob else blob
        for m in re.finditer(
            r"分发通道类型:\s*(\S+)\s*\(type=(\S+)",
            post_blob,
        ):
            post_email_samples.append({"name": m.group(1), "type": m.group(2)})
    out["post_email_sent_codes"] = post_email_samples
    if post_email_samples:
        out["post_email_sent_code_type"] = post_email_samples[-1]["type"]
    else:
        out["post_email_sent_code_type"] = None
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
    post_email_types = Counter(
        r.get("post_email_sent_code_type") for r in rows if r.get("post_email_sent_code_type")
    )
    payment_rows = [r for r in rows if r.get("payment_required") or r.get("payment_log_count")]
    email_then_payment = [
        r for r in rows
        if r.get("email_verified") and (r.get("payment_required") or r.get("payment_log_count"))
    ]
    email_then_other = [
        r for r in rows
        if r.get("email_verified")
        and not (r.get("payment_required") or r.get("payment_log_count"))
        and r.get("post_email_sent_code_type")
    ]
    sendcode_attempts = len(samples)
    payment_samples = sum(1 for s in samples if "PaymentRequired" in str(s.get("sent_code_type", "")))
    leased = sum(int(r.get("leased_numbers") or 0) for r in rows)
    return {
        "tasks": len(rows),
        "leased_numbers": leased,
        "sendcode_samples": sendcode_attempts,
        "sent_code_types": dict(type_counts),
        "post_email_sent_code_types": dict(post_email_types),
        "payment_task_count": len(payment_rows),
        "payment_sample_count": payment_samples,
        "payment_rate_of_sendcode": round(payment_samples / sendcode_attempts, 4) if sendcode_attempts else None,
        "payment_rate_after_email": round(len(email_then_payment) / max(1, sum(1 for r in rows if r.get("email_verified"))), 4),
        "email_verified_tasks": sum(1 for r in rows if r.get("email_verified")),
        "email_then_payment": len(email_then_payment),
        "email_then_other": len(email_then_other),
        "failure_reasons": dict(Counter(r.get("final_failure_reason") for r in rows if r.get("final_failure_reason"))),
        "devices": dict(Counter(r.get("device") for r in rows if r.get("device"))),
        "api_ids": dict(Counter(a for r in rows for a in (r.get("api_ids") or []))),
        "proxy_countries": dict(Counter(r.get("proxy_country") for r in rows if r.get("proxy_country"))),
    }


def run_experiment(
    client: ApiClient,
    *,
    exp_id: str,
    spec: Dict[str, Any],
    snapshot: Dict[str, Any],
    country: str,
    sms_provider: str,
    threads: int,
    attempts_per_thread: int,
    max_price: float,
    proxy_mode: str,
    poll: float,
    batch_timeout: float,
) -> Dict[str, Any]:
    applied = apply_experiment_config(client, snapshot, spec)
    app_type = spec["active_app_type"]
    print(
        f"\n=== [{exp_id}] {spec['label']} === {spec['description']}\n"
        f"    country={country} threads={threads} attempts={attempts_per_thread} "
        f"app_type={app_type} applied={applied} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type=app_type,
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
        f"    -> 租号={analysis['leased_numbers']} 发码={analysis['sendcode_samples']} "
        f"email后Payment={analysis['email_then_payment']} "
        f"post_email_types={analysis['post_email_sent_code_types']} "
        f"耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "experiment_id": exp_id,
        "label": spec["label"],
        "description": spec["description"],
        "country": country,
        "sms_provider": sms_provider,
        "threads": threads,
        "attempts_per_thread": attempts_per_thread,
        "applied": applied,
        "proxy_mode": proxy_mode,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "summary": summary,
        "analysis": analysis,
        "rows": rows,
    }


def build_comparison_table(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = []
    for item in rounds:
        a = item.get("analysis") or {}
        table.append({
            "experiment": item.get("experiment_id"),
            "label": item.get("label"),
            "description": item.get("description"),
            "active_app_type": (item.get("applied") or {}).get("active_app_type"),
            "leased": a.get("leased_numbers"),
            "sendcode_samples": a.get("sendcode_samples"),
            "email_verified": a.get("email_verified_tasks"),
            "email_then_payment": a.get("email_then_payment"),
            "payment_rate_after_email": a.get("payment_rate_after_email"),
            "sent_code_types": a.get("sent_code_types"),
            "post_email_sent_code_types": a.get("post_email_sent_code_types"),
            "api_ids": a.get("api_ids"),
            "failure_reasons": a.get("failure_reasons"),
        })
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--country", default="pe")
    parser.add_argument("--experiments", default="A,B,C,D,E", help="逗号分隔实验 ID")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--threads", type=int, default=2, help="每实验并发任务数（默认 2 号）")
    parser.add_argument("--attempts-per-thread", type=int, default=1)
    parser.add_argument("--max-price", type=float, default=1.2)
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=1200.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    exp_ids = [x.strip().upper() for x in args.experiments.split(",") if x.strip()]
    unknown = [x for x in exp_ids if x not in EXPERIMENT_MATRIX]
    if unknown:
        print(f"未知实验 ID: {unknown}，可选: {list(EXPERIMENT_MATRIX)}", file=sys.stderr)
        return 2

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"启动快照 emu={original.get('official_client_emulation')} "
        f"email={original.get('email_provider_mode')} experiments={exp_ids}",
        flush=True,
    )
    balances_before = snapshot_balances(client)
    rounds: List[Dict[str, Any]] = []

    try:
        for exp_id in exp_ids:
            rounds.append(run_experiment(
                client,
                exp_id=exp_id,
                spec=EXPERIMENT_MATRIX[exp_id],
                snapshot=original,
                country=args.country.lower(),
                sms_provider=args.sms_provider,
                threads=args.threads,
                attempts_per_thread=args.attempts_per_thread,
                max_price=args.max_price,
                proxy_mode=args.proxy_mode,
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
    comparison = build_comparison_table(rounds)
    report = {
        "generated_at": utc_now(),
        "mode": "payment_bypass_ab",
        "country": args.country.lower(),
        "config_baseline": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "email_provider_mode": "smsbower_only",
            "code_delivery_mode": "push_required",
        },
        "balances_before": balances_before,
        "balances_after": balances_after,
        "comparison_table": comparison,
        "rounds": rounds,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"payment_bypass_ab_{args.country}_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(f"{'Exp':<4} {'label':<28} {'租号':>4} {'email':>5} {'email→pay':>9} {'post_email_types'}")
    print("-" * 96)
    for row in comparison:
        print(
            f"{row.get('experiment','?'):<4} {row.get('label',''):<28} "
            f"{row.get('leased',0):>4} {row.get('email_verified',0):>5} "
            f"{row.get('email_then_payment',0):>9} {row.get('post_email_sent_code_types')}"
        )
    print("=" * 96)
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
