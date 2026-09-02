#!/usr/bin/env python3
"""Payment 后 SentCodeTypeSms 收码对照：延长等待 / 二次 resend / 换平台 / 换国。

用法::

    python3 backend/scripts/run_grok_sms_after_payment.py
    python3 backend/scripts/run_grok_sms_after_payment.py --hypotheses A,B --budget 8
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.run_code_delivery_ab import ApiClient, utc_now, wait_batch  # noqa: E402
from backend.scripts.run_grok_autonomous_sprint import (  # noqa: E402
    OFFICIAL6,
    VAULT4,
    apply_config,
    classify_outcome,
    verify_task_logs,
)
from backend.scripts.run_payment_bypass_ab import (  # noqa: E402
    analyze_round,
    build_comparison_table,
    enrich_row,
)
from backend.scripts.run_registration_sprint import (  # noqa: E402
    parse_task_evidence,
    snapshot_balances,
    summarize_evidence,
)

SUCCESS_STATUS = {"success"}
SMS_WAIT_END_RE = re.compile(
    r"\[SMS_WAIT\] 结束 elapsed=(?P<sec>[\d.]+)s final=(?P<final>\S+)"
)
GETSTATUS_RE = re.compile(r"getStatus raw=(?P<raw>.+?) elapsed=")
HASH_RE = re.compile(r"phone_code_hash=(?P<h>\S+)")

PAYMENT_SMS_FLAGS = {
    **OFFICIAL6,
    "payment_required_probe": "resend",
    "sms_poll_bypass_push_window": True,
    "code_settings_allow_firebase": True,
    "code_settings_unknown_number": True,
    "force_resend_on_app": True,
    "email_provider_mode": "smsbower_only",
}


def _hypotheses() -> Dict[str, Dict[str, Any]]:
    return {
        "A": {
            "label": "A_long_wait_iq",
            "hypothesis": "延长 SMS 等待到 ~240s + 2s 密轮询（Payment 后 1 次 resend）",
            "country": "iq",
            "count": 3,
            "threads": 3,
            "max_attempts": 2,
            "max_price": 0.85,
            "sms_provider": "smsbower",
            "verify": "official_api6",
            "apply": {
                **PAYMENT_SMS_FLAGS,
                "sms_poll_attempts": 120,
                "sms_poll_interval_seconds": 2.0,
                "payment_resend_max": 1,
                "payment_resend_wait_seconds": 0,
            },
        },
        "B": {
            "label": "B_double_resend_iq",
            "hypothesis": "Payment 后连续 resend 两次 + reportMissingCode + 180s 等待",
            "country": "iq",
            "count": 3,
            "threads": 3,
            "max_attempts": 2,
            "max_price": 0.85,
            "sms_provider": "smsbower",
            "verify": "official_api6",
            "apply": {
                **PAYMENT_SMS_FLAGS,
                "sms_poll_attempts": 90,
                "sms_poll_interval_seconds": 2.0,
                "payment_resend_max": 2,
                "payment_resend_wait_seconds": 0,
                "report_missing_sms_code": True,
            },
        },
        "C": {
            "label": "C_alt_provider_iq",
            "hypothesis": "换接码平台对照 smsbower（优先 Grizzly，其次 5SIM）",
            "country": "iq",
            "count": 3,
            "threads": 3,
            "max_attempts": 2,
            "max_price": 0.85,
            "sms_provider": "grizzlysms",
            "verify": "official_api6",
            "apply": {
                **PAYMENT_SMS_FLAGS,
                "sms_poll_attempts": 90,
                "sms_poll_interval_seconds": 2.0,
                "payment_resend_max": 1,
            },
        },
        "D": {
            "label": "D_api4_resend_ma",
            "hypothesis": "非 Payment 路径 api_id=4 在 ma 上 resendCode 能否得到 SMS",
            "country": "ma",
            "count": 2,
            "threads": 2,
            "max_attempts": 2,
            "max_price": 0.8,
            "sms_provider": "smsbower",
            "verify": "vault_api4",
            "apply": {
                **VAULT4,
                "payment_required_probe": "off",
                "force_resend_on_app": True,
                "sms_poll_attempts": 45,
                "sms_poll_interval_seconds": 2.0,
                "sms_poll_bypass_push_window": True,
            },
        },
        "E": {
            "label": "E_long_wait_ma",
            "hypothesis": "同 A，国家换成 ma",
            "country": "ma",
            "count": 3,
            "threads": 3,
            "max_attempts": 2,
            "max_price": 0.8,
            "sms_provider": "smsbower",
            "verify": "official_api6",
            "apply": {
                **PAYMENT_SMS_FLAGS,
                "sms_poll_attempts": 120,
                "sms_poll_interval_seconds": 2.0,
                "payment_resend_max": 1,
            },
        },
        "F": {
            "label": "F_wait_timeout_then_resend_iq",
            "hypothesis": "email verify 后等 90s 再 resend（对齐官方 timeout）再等 180s",
            "country": "iq",
            "count": 3,
            "threads": 3,
            "max_attempts": 2,
            "max_price": 0.85,
            "sms_provider": "smsbower",
            "verify": "official_api6",
            "apply": {
                **PAYMENT_SMS_FLAGS,
                "sms_poll_attempts": 90,
                "sms_poll_interval_seconds": 2.0,
                "payment_resend_max": 1,
                "payment_resend_wait_seconds": 90,
                "resend_before_email_verify": False,
            },
        },
    }


def _mask(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        return "***"
    return f"+{digits[:4]}****{digits[-3:]}"


def extra_sms_wait_stats(blob: str) -> Dict[str, Any]:
    ends = [m.groupdict() for m in SMS_WAIT_END_RE.finditer(blob)]
    raws = GETSTATUS_RE.findall(blob)
    unique_raw = []
    for item in raws:
        token = item.strip()
        if token not in unique_raw:
            unique_raw.append(token)
        if len(unique_raw) >= 8:
            break
    return {
        "sms_wait_ends": ends,
        "getstatus_samples": unique_raw,
        "phone_code_hash_logs": HASH_RE.findall(blob)[:6],
        "report_missing": "auth.reportMissingCode" in blob,
        "double_resend": blob.count("auth.resendCode 已返回") >= 2,
        "email_before_resend": "[EMAIL_PROBE]" in blob,
    }


def country_stock(client: ApiClient, provider: str, country: str) -> Dict[str, Any]:
    try:
        data = client.request(
            "GET",
            f"/api/sms/available-countries?provider={provider}&refresh=true",
            timeout=60.0,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "count": 0}
    rows = data.get("countries") or data.get("items") or []
    hit = None
    for row in rows:
        cc = str(row.get("country") or row.get("code") or "").lower()
        if cc == country.lower():
            hit = row
            break
    count = 0
    if hit:
        for key in ("count", "qty", "stock", "available"):
            if hit.get(key) is not None:
                try:
                    count = int(hit.get(key) or 0)
                except (TypeError, ValueError):
                    count = 0
                break
    return {"ok": True, "count": count, "row": hit, "total_countries": len(rows)}


def pick_alt_provider(client: ApiClient, balances: Dict[str, Any], country: str) -> Dict[str, Any]:
    """选一个有余额+库存的备用接码源；都不够则 skip。"""
    grizzly = balances.get("grizzlysms") or {}
    fivesim = balances.get("fivesim") or {}
    g_bal = float(grizzly.get("balance") or 0)
    f_bal = float(fivesim.get("balance") or 0)
    g_stock = country_stock(client, "grizzlysms", country)
    f_stock = country_stock(client, "fivesim", country)
    if grizzly.get("success") and g_bal >= 3.0 and g_stock.get("count", 0) > 0:
        return {
            "provider": "grizzlysms",
            "reason": f"grizzly balance={g_bal} stock={g_stock.get('count')}",
            "stock": g_stock,
        }
    if fivesim.get("success") and f_bal >= 20 and f_stock.get("count", 0) > 0:
        return {
            "provider": "fivesim",
            "reason": f"5sim balance={f_bal} stock={f_stock.get('count')}",
            "stock": f_stock,
        }
    return {
        "provider": None,
        "reason": (
            f"备用平台不足：grizzly success={grizzly.get('success')} bal={g_bal} "
            f"stock={g_stock.get('count')} err={g_stock.get('error')}; "
            f"5sim success={fivesim.get('success')} bal={f_bal} "
            f"stock={f_stock.get('count')} err={f_stock.get('error')}"
        ),
        "stock": {"grizzlysms": g_stock, "fivesim": f_stock},
    }


def min_balance_ok(balances: Dict[str, Any], provider: str, need: float) -> Dict[str, Any]:
    key = {"smsbower": "smsbower", "grizzlysms": "grizzlysms", "fivesim": "fivesim"}.get(provider, provider)
    row = balances.get(key) or {}
    bal = float(row.get("balance") or 0)
    ok = bool(row.get("success")) and bal >= need
    return {"ok": ok, "balance": bal, "currency": row.get("currency"), "need": need, "provider": provider}


def run_hypothesis(
    client: ApiClient,
    *,
    hid: str,
    spec: Dict[str, Any],
    snapshot: Dict[str, Any],
    proxy_mode: str,
    poll: float,
    batch_timeout: float,
) -> Dict[str, Any]:
    applied = apply_config(client, snapshot, spec["apply"])
    country = spec["country"]
    count = int(spec["count"])
    threads = min(int(spec["threads"]), count, 10)
    attempts = int(spec.get("max_attempts") or 2)
    sms_provider = spec.get("sms_provider") or "smsbower"
    app_type = applied.get("active_app_type") or spec["apply"].get("active_app_type")
    print(
        f"\n=== {hid} {spec['label']} === {spec['hypothesis']}\n"
        f"    country={country} count={count} threads={threads} attempts={attempts} "
        f"provider={sms_provider} price={spec.get('max_price')} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type=app_type,
        count=count,
        concurrency=threads,
        sms_provider=sms_provider,
        max_price=float(spec.get("max_price") or 0.85),
        max_number_attempts=attempts,
        no_number_retries=3,
        proxy_mode=proxy_mode,
    )
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)
    final_batch, tasks, timed_out = wait_batch(client, batch_id, poll, batch_timeout)
    expect_official = bool(spec["apply"].get("official_client_emulation"))
    rows: List[Dict[str, Any]] = []
    success_records: List[Dict[str, Any]] = []
    for t in tasks:
        tid = t.get("task_id") or t.get("id")
        full = client.get_task(tid)
        base = parse_task_evidence(full, country)
        row = enrich_row(base, full)
        blob = "\n".join(full.get("logs") or [])
        v = verify_task_logs(blob, spec.get("verify") or "", expect_official)
        extra = extra_sms_wait_stats(blob)
        row["log_verification"] = v
        row["sms_wait"] = extra
        row["registration_success"] = str(full.get("status") or "").lower() in SUCCESS_STATUS
        if row["registration_success"]:
            phone = str(full.get("phone") or base.get("phone") or "")
            session_name = str(full.get("session_name") or "")
            success_records.append({
                "task_id": tid,
                "phone_masked": _mask(phone),
                "api_id": (v.get("cred_checks") or [{}])[0].get("api_id"),
                "session_path": f"data/sessions/{session_name}.session" if session_name else None,
            })
        rows.append(row)
        ends = extra.get("sms_wait_ends") or []
        wait_s = ends[-1]["sec"] if ends else "-"
        final = ends[-1]["final"] if ends else "-"
        print(
            f"    task={tid} status={row.get('status')} sms={v['sms_code_received']} "
            f"pay={v['payment']} resend={v.get('resend_types')} wait={wait_s}s/{final} "
            f"raw={extra.get('getstatus_samples', [])[:2]}",
            flush=True,
        )
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    outcome = classify_outcome(analysis, summary, rows)
    codes = sum(1 for r in rows if r.get("log_verification", {}).get("sms_code_received"))
    print(
        f"    -> 租号={analysis.get('leased_numbers')} 发码={analysis['sendcode_samples']} "
        f"types={analysis['sent_code_types']} 收码={codes} 成功={summary.get('success')} "
        f"outcome={outcome} 耗时={int(time.time() - started)}s",
        flush=True,
    )
    return {
        "hypothesis_id": hid,
        "label": spec["label"],
        "hypothesis": spec["hypothesis"],
        "country": country,
        "sms_provider": sms_provider,
        "count": count,
        "threads": threads,
        "max_attempts": attempts,
        "max_price": spec.get("max_price"),
        "applied": {k: applied.get(k) for k in spec["apply"] if "key" not in k.lower() and "hash" not in k.lower() or k in {"custom_api_id"}},
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "outcome": outcome,
        "summary": summary,
        "analysis": analysis,
        "success_records": success_records,
        "rows": rows,
    }


def persist(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--hypotheses", default="A,B,C,D,E,F")
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=15.0)
    parser.add_argument("--batch-timeout", type=float, default=1800.0)
    parser.add_argument("--min-smsbower", type=float, default=8.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    out_dir = Path(args.out_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"grok_sms_after_payment_{stamp}.json"
    specs = _hypotheses()
    queue = [x.strip().upper() for x in args.hypotheses.split(",") if x.strip()]
    unknown = [x for x in queue if x not in specs]
    if unknown:
        print(f"未知假设: {unknown}", file=sys.stderr)
        return 2

    balances_before = snapshot_balances(client)
    print(f"SMS after Payment 对照 stamp={stamp} queue={queue}", flush=True)
    print(f"余额 {json.dumps(balances_before, ensure_ascii=False)[:800]}", flush=True)

    bower = min_balance_ok(balances_before, "smsbower", args.min_smsbower)
    recharge_needed: List[str] = []
    if not bower["ok"]:
        recharge_needed.append(
            f"SMS Bower 余额 {bower['balance']} {bower.get('currency') or 'USD'}，"
            f"低于继续测试所需约 {args.min_smsbower} USD。请在 smsbower.app 充值至少 "
            f"{max(10.0, args.min_smsbower - bower['balance'] + 5):.0f} USD。"
        )

    skipped: List[Dict[str, Any]] = []
    rounds: List[Dict[str, Any]] = []
    success_records: List[Dict[str, Any]] = []
    leases_used = 0
    stop_reason = "queue_exhausted"

    try:
        if recharge_needed and not any(h in {"C", "D"} for h in queue):
            print("余额不足，停止。", *recharge_needed, sep="\n", flush=True)
            persist(report_path, {
                "generated_at": utc_now(),
                "mode": "grok_sms_after_payment",
                "stamp": stamp,
                "stop_reason": "insufficient_balance",
                "recharge_needed": recharge_needed,
                "balances_before": balances_before,
                "rounds": [],
            })
            return 3

        for hid in queue:
            spec = dict(specs[hid])
            spec["apply"] = dict(spec["apply"])
            if hid == "C":
                alt = pick_alt_provider(client, balances_before, spec["country"])
                if not alt.get("provider"):
                    skipped.append({"id": hid, "reason": alt["reason"]})
                    print(f"\n>>> 跳过 C：{alt['reason']}", flush=True)
                    continue
                spec["sms_provider"] = alt["provider"]
                spec["label"] = f"C_alt_{alt['provider']}_iq"
                print(f"\n>>> C 选用 {alt['provider']}: {alt['reason']}", flush=True)
            provider = spec.get("sms_provider") or "smsbower"
            need = 2.5 if hid == "D" else 4.0
            gate = min_balance_ok(snapshot_balances(client), provider, need)
            if not gate["ok"] and provider == "smsbower":
                msg = (
                    f"{provider} 余额 {gate['balance']} 不足以跑 {hid}（需要 ≥{need}）。"
                    "请充值后再继续。"
                )
                recharge_needed.append(msg)
                skipped.append({"id": hid, "reason": msg})
                print(f"\n>>> 跳过 {hid}: {msg}", flush=True)
                continue
            if leases_used >= args.budget:
                stop_reason = "budget_exhausted"
                skipped.append({"id": hid, "reason": "budget"})
                break
            result = run_hypothesis(
                client,
                hid=hid,
                spec=spec,
                snapshot=original,
                proxy_mode=args.proxy_mode,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
            )
            leases_used += int(result["analysis"].get("leased_numbers") or 0)
            rounds.append(result)
            success_records.extend(result.get("success_records") or [])
            persist(report_path, _payload(
                stamp, args, balances_before, client, rounds, skipped,
                leases_used, success_records, stop_reason, recharge_needed,
            ))
            if success_records:
                stop_reason = "registration_success"
                print("\n*** 注册成功，停止 ***", flush=True)
                break
            codes = sum(
                1 for r in result.get("rows") or []
                if r.get("log_verification", {}).get("sms_code_received")
            )
            if codes:
                stop_reason = "sms_code_received"
                print("\n*** 已从接码平台读到验证码，停止加码 ***", flush=True)
                break
        else:
            if not success_records:
                stop_reason = "exhausted_hypotheses"
    finally:
        restored = client.request("POST", "/api/config", original)
        print(
            f"\n已恢复 config emu={restored.get('official_client_emulation')} "
            f"probe={restored.get('payment_required_probe')}",
            flush=True,
        )

    payload = _payload(
        stamp, args, balances_before, client, rounds, skipped,
        leases_used, success_records, stop_reason, recharge_needed,
    )
    persist(report_path, payload)
    print(f"\n报告: {report_path}", flush=True)
    print(
        f"结论: stop={stop_reason} 租号={leases_used} 成功={len(success_records)} "
        f"充值提示={recharge_needed or '无'} "
        f"轮次={[r['hypothesis_id']+':'+r['outcome'] for r in rounds]}",
        flush=True,
    )
    if recharge_needed:
        print("\n【需要充值】", *recharge_needed, sep="\n", flush=True)
    return 0 if success_records else 1


def _payload(
    stamp: str,
    args: argparse.Namespace,
    balances_before: Dict[str, Any],
    client: ApiClient,
    rounds: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    leases_used: int,
    success_records: List[Dict[str, Any]],
    stop_reason: str,
    recharge_needed: List[str],
) -> Dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "mode": "grok_sms_after_payment",
        "stamp": stamp,
        "cli": {
            "budget": args.budget,
            "hypotheses": args.hypotheses,
        },
        "balances_before": balances_before,
        "balances_after": snapshot_balances(client),
        "leases_used": leases_used,
        "stop_reason": stop_reason,
        "recharge_needed": recharge_needed,
        "skipped": skipped,
        "success_records": success_records,
        "comparison_table": build_comparison_table(
            [{"experiment_id": r.get("hypothesis_id"), **r} for r in rounds]
        ),
        "rounds": rounds,
        "outcomes": {
            "registration_success": len(success_records),
            "round_outcomes": {r["hypothesis_id"]: r["outcome"] for r in rounds},
        },
    }


if __name__ == "__main__":
    sys.exit(main())
