#!/usr/bin/env python3
"""凭证库成功账号 vs official api_id=4/6 对照实验。

变体（每变体 2–3 号）::

    1  api_id=4 + 正确 hash + official + smsbower + country=in
    2  api_id=4 + 正确 hash + official + country=iq
    3  vault 成功路径 replay：custom api_id=4 + balanced（非 official）
    4  api_id=6 official 同国对照（随 --country）

用法::

    python3 backend/scripts/run_vault_compare_ab.py \\
        --country in --experiments 1,2,3,4 --threads 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from backend.scripts.run_code_delivery_ab import ApiClient, utc_now, wait_batch  # noqa: E402
from backend.scripts.run_payment_bypass_ab import (  # noqa: E402
    analyze_round,
    build_comparison_table,
    enrich_row,
)
from backend.scripts.run_registration_sprint import parse_task_evidence, snapshot_balances, summarize_evidence  # noqa: E402

VAULT_API4_HASH = "014b35b6184100b085b0d0572f9b5103"

EXPERIMENT_MATRIX: Dict[str, Dict[str, Any]] = {
    "1": {
        "label": "V1_api4_official_in",
        "description": "api_id=4 + 正确 hash + official + smsbower email",
        "country": "in",
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android_public",
        },
    },
    "2": {
        "label": "V2_api4_official_iq",
        "description": "api_id=4 + 正确 hash + official + iq 对照",
        "country": "iq",
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android_public",
        },
    },
    "3": {
        "label": "V3_vault_replay_balanced",
        "description": "vault +91 成功路径：api_id=4 custom + balanced 非 official",
        "country": "in",
        "apply": {
            "official_client_emulation": False,
            "api_credential_mode": "custom",
            "custom_api_id": 4,
            "custom_api_hash": VAULT_API4_HASH,
            "code_delivery_mode": "balanced",
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android_public",
        },
    },
    "4": {
        "label": "V4_api6_official_control",
        "description": "api_id=6 official 同国对照",
        "country": None,  # 使用 CLI --country
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android",
        },
    },
}


def apply_experiment_config(
    client: ApiClient,
    snapshot: Dict[str, Any],
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    cfg = dict(snapshot)
    for key, value in (spec.get("apply") or {}).items():
        cfg[key] = value
    saved = client.request("POST", "/api/config", cfg)
    return {k: saved.get(k) for k in (spec.get("apply") or {})}


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
    exp_country = spec.get("country") or country
    app_type = applied.get("active_app_type") or "telegram_android"
    print(
        f"\n=== [{exp_id}] {spec['label']} === {spec['description']}\n"
        f"    country={exp_country} threads={threads} attempts={attempts_per_thread} "
        f"app_type={app_type} applied={applied} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=exp_country,
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
        base = parse_task_evidence(full, exp_country)
        rows.append(enrich_row(base, full))
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    print(
        f"    -> 租号={analysis['leased_numbers']} 发码={analysis['sendcode_samples']} "
        f"types={analysis['sent_code_types']} email后Payment={analysis['email_then_payment']} "
        f"耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "experiment_id": exp_id,
        "label": spec["label"],
        "description": spec["description"],
        "country": exp_country,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--country", default="in", help="变体 4 对照国（默认 in）")
    parser.add_argument("--experiments", default="1,2,3,4")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--attempts-per-thread", type=int, default=2)
    parser.add_argument("--max-price", type=float, default=1.0)
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=900.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    exp_ids = [x.strip() for x in args.experiments.split(",") if x.strip()]
    unknown = [x for x in exp_ids if x not in EXPERIMENT_MATRIX]
    if unknown:
        print(f"未知实验 ID: {unknown}，可选: {list(EXPERIMENT_MATRIX)}", file=sys.stderr)
        return 2

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
        client.request("POST", "/api/config", original)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"vault_compare_{args.country.lower()}_{stamp}.json"
    report = {
        "generated_at": utc_now(),
        "mode": "vault_compare_ab",
        "cli_country": args.country.lower(),
        "balances_before": balances_before,
        "balances_after": snapshot_balances(client),
        "comparison_table": build_comparison_table(rounds),
        "rounds": rounds,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
