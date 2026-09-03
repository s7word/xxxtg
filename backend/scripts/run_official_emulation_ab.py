#!/usr/bin/env python3
"""官方客户端模拟 vs balanced+custom api_id 的 fresh 对照。

Round A：official_client_emulation + official api + push_required
Round B：balanced + custom api_id（关闭官方模拟）

每轮 3 任务 × 最多 2 次取号。报告写入 data/ab_reports/official_emulation_YYYYMMDD.json。
不打印 API Key。跑完无条件恢复启动时的 config 快照。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.scripts.run_code_delivery_ab import (  # noqa: E402
    ApiClient,
    utc_now,
    wait_batch,
)
from backend.scripts.run_registration_sprint import (  # noqa: E402
    snapshot_balances,
    parse_task_evidence,
    summarize_evidence,
)

PREFERRED_COUNTRIES = ("pe", "cl", "ma", "id", "co")


def apply_round_config(client: ApiClient, snapshot: Dict[str, Any], *, official: bool) -> Dict[str, Any]:
    cfg = dict(snapshot)
    if official:
        cfg["official_client_emulation"] = True
        cfg["api_credential_mode"] = "official"
        cfg["code_delivery_mode"] = "push_required"
        cfg["active_app_type"] = "telegram_android"
    else:
        cfg["official_client_emulation"] = False
        cfg["api_credential_mode"] = "custom"
        cfg["code_delivery_mode"] = "balanced"
    saved = client.request("POST", "/api/config", cfg)
    return {
        "official_client_emulation": bool(saved.get("official_client_emulation")),
        "api_credential_mode": saved.get("api_credential_mode"),
        "code_delivery_mode": saved.get("code_delivery_mode"),
        "active_app_type": saved.get("active_app_type"),
        "custom_api_id": saved.get("custom_api_id"),
    }


def pick_country(client: ApiClient, requested: Optional[str], provider: str) -> str:
    if requested:
        return requested.lower()
    try:
        data = client.request("GET", f"/api/sms/available-countries?provider={provider}&refresh=true")
    except Exception as exc:
        print(f"库存查询失败，回落 cl: {exc}", flush=True)
        return "cl"
    items = data.get("items") or []
    by_code = {str(item.get("code") or "").lower(): item for item in items}
    for code in PREFERRED_COUNTRIES:
        item = by_code.get(code)
        if item and int(item.get("stock") or 0) > 0:
            print(
                f"选国 {code} stock={item.get('stock')} cost={item.get('cost')} "
                f"name={item.get('name_zh') or item.get('name')}",
                flush=True,
            )
            return code
    if items:
        top = max(items, key=lambda it: int(it.get("stock") or 0))
        code = str(top.get("code") or "cl").lower()
        print(f"无优选国有货，改用库存最高 {code} stock={top.get('stock')}", flush=True)
        return code
    return "cl"


def run_round(
    client: ApiClient,
    *,
    label: str,
    official: bool,
    snapshot: Dict[str, Any],
    country: str,
    sms_provider: str,
    count: int,
    concurrency: int,
    max_price: float,
    max_number_attempts: int,
    proxy_mode: str,
    poll: float,
    batch_timeout: float,
) -> Dict[str, Any]:
    applied = apply_round_config(client, snapshot, official=official)
    print(
        f"\n=== {label} === country={country} official={official} "
        f"applied={applied} count={count} attempts={max_number_attempts} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type="telegram_android",
        count=count,
        concurrency=concurrency,
        sms_provider=sms_provider,
        max_price=max_price,
        max_number_attempts=max_number_attempts,
        no_number_retries=3,
        proxy_mode=proxy_mode,
    )
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)
    final_batch, tasks, timed_out = wait_batch(client, batch_id, poll, batch_timeout)
    rows = [
        parse_task_evidence(client.get_task(t.get("task_id") or t.get("id")), country)
        for t in tasks
    ]
    summary = summarize_evidence(rows)
    type_counts = Counter()
    for row in rows:
        for sample in row.get("samples") or []:
            type_counts[sample.get("sent_code_type") or "Unknown"] += 1
        blob = "\n".join(row.get("log_excerpt") or [])
        if "SetUpEmailRequired" in blob or "SentCodeTypeSetUpEmailRequired" in json.dumps(row, ensure_ascii=False):
            type_counts["SentCodeTypeSetUpEmailRequired"] += 0
    summary["sent_code_types"] = dict(type_counts)
    flags = {
        "saw_setup_email": any(
            "SetUpEmail" in json.dumps(row, ensure_ascii=False) for row in rows
        ),
        "saw_payment": any(
            "PaymentRequired" in json.dumps(row, ensure_ascii=False) for row in rows
        ),
        "saw_sms": any(
            s.get("bucket") == "sms" for row in rows for s in (row.get("samples") or [])
        ),
        "saw_app": any(
            s.get("bucket") == "app" for row in rows for s in (row.get("samples") or [])
        ),
        "saw_firebase": any(
            "Firebase" in json.dumps(row, ensure_ascii=False) for row in rows
        ),
    }
    print(
        f"    -> 发码={summary['sendcode_samples']} types={dict(type_counts)} "
        f"成功={summary['success']} flags={flags} 耗时={int(time.time() - started)}s",
        flush=True,
    )
    return {
        "label": label,
        "official": official,
        "applied": applied,
        "country": country,
        "sms_provider": sms_provider,
        "count": count,
        "max_number_attempts": max_number_attempts,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "summary": summary,
        "flags": flags,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--country", default="")
    parser.add_argument("--sms-provider", default="grizzlysms")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-price", type=float, default=0.8)
    parser.add_argument("--max-number-attempts", type=int, default=2)
    parser.add_argument("--proxy-mode", default="custom_pool")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=1200.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    country = pick_country(client, args.country or None, args.sms_provider)
    print(
        f"启动快照 cred={original.get('api_credential_mode')} "
        f"delivery={original.get('code_delivery_mode')} "
        f"emu={original.get('official_client_emulation')} "
        f"api_id={original.get('custom_api_id')} country={country}",
        flush=True,
    )
    balances_before = snapshot_balances(client)
    print(f"余额前: {json.dumps(balances_before, ensure_ascii=False)}", flush=True)

    rounds: List[Dict[str, Any]] = []
    try:
        rounds.append(run_round(
            client,
            label="round_a_official_emulation",
            official=True,
            snapshot=original,
            country=country,
            sms_provider=args.sms_provider,
            count=args.count,
            concurrency=args.concurrency,
            max_price=args.max_price,
            max_number_attempts=args.max_number_attempts,
            proxy_mode=args.proxy_mode,
            poll=args.poll,
            batch_timeout=args.batch_timeout,
        ))
        rounds.append(run_round(
            client,
            label="round_b_balanced_custom",
            official=False,
            snapshot=original,
            country=country,
            sms_provider=args.sms_provider,
            count=args.count,
            concurrency=args.concurrency,
            max_price=args.max_price,
            max_number_attempts=args.max_number_attempts,
            proxy_mode=args.proxy_mode,
            poll=args.poll,
            batch_timeout=args.batch_timeout,
        ))
    finally:
        restored = client.request("POST", "/api/config", original)
        print(
            f"\n已恢复 config delivery={restored.get('code_delivery_mode')} "
            f"emu={restored.get('official_client_emulation')} "
            f"cred={restored.get('api_credential_mode')}",
            flush=True,
        )

    balances_after = snapshot_balances(client)
    any_success = any((item.get("summary") or {}).get("success") for item in rounds)
    report = {
        "generated_at": utc_now(),
        "country": country,
        "sms_provider": args.sms_provider,
        "user_phone_skipped": "+51939693509 已在黑名单（SENT_CODE_TYPE_APP），本次用新号",
        "balances_before": balances_before,
        "balances_after": balances_after,
        "any_success": bool(any_success),
        "rounds": rounds,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out_path = out_dir / f"official_emulation_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 88)
    print(f"{'round':<28}{'发码':>6}{'SMS':>5}{'App':>5}{'成功':>5}  types")
    for item in rounds:
        s = item.get("summary") or {}
        print(
            f"{item.get('label'):<28}{s.get('sendcode_samples', 0):>6}"
            f"{s.get('sms', 0):>5}{s.get('app', 0):>5}{s.get('success', 0):>5}  "
            f"{s.get('sent_code_types')}"
        )
    print("=" * 88)
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
