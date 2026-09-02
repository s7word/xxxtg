#!/usr/bin/env python3
"""Grok 自主长程注册冲刺：iq 主战场，api_id 仅 4/6。

分批租号、分析 sent_code、按规则切换假设，成功注册即停。
报告写入 data/ab_reports/grok_autonomous_sprint_*.json。

用法::

    python3 backend/scripts/run_grok_autonomous_sprint.py
    python3 backend/scripts/run_grok_autonomous_sprint.py --hypotheses H1,H3 --budget 20
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
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.run_code_delivery_ab import ApiClient, utc_now, wait_batch  # noqa: E402
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
from backend.scripts.run_vault_mode_sprint import load_vault_api4_meta  # noqa: E402

VAULT_API4_HASH = "014b35b6184100b085b0d0572f9b5103"
VAULT_API4_HASH_PREFIX = "014b35"
API6_HASH_PREFIX = "eb06d4"

CRED_CHECK_RE = re.compile(
    r"sendCode 凭证核对: api_id=(?P<api_id>\d+) api_hash=(?P<api_hash>\S+) "
    r"attach_token=(?P<attach>\S) push_token=(?P<push>\S) "
    r"code_settings.token=(?P<cs>\S+)"
)
FIREBASE_FLAG_RE = re.compile(r"firebase=(?P<flag>\S)")
UNKNOWN_FLAG_RE = re.compile(r"unknown=(?P<flag>\S)")
ATTACH_YES_RE = re.compile(r"attach_token=是")
OFFICIAL_EMU_RE = re.compile(r"官方客户端模拟")
FLOOD_RE = re.compile(r"API_ID_PUBLISHED_FLOOD")
SMS_CODE_RE = re.compile(r"STATUS_OK|收到验证码|验证码[:：]\s*\d{4,6}|接码成功")
FIREBASE_TYPE_RE = re.compile(r"SentCodeTypeFirebaseSms")
PAYMENT_RE = re.compile(r"SentCodePaymentRequired")
EMAIL_SETUP_RE = re.compile(r"SentCodeTypeSetUpEmailRequired")
PLAY_PROBE_RE = re.compile(r"\[PAYMENT_PROBE\]")
RESEND_RE = re.compile(r"auth\.resendCode 已返回，新分发通道类型:\s*(?P<name>\S+)")
SUCCESS_STATUS = {"success"}

COMMON_FLAGS = {
    "force_skip_push_attach": False,
    "code_delivery_mode": "push_required",
    "email_provider_mode": "smsbower_only",
    "code_settings_allow_firebase": True,
    "code_settings_unknown_number": True,
    "code_settings_allow_flashcall": False,
    "code_settings_allow_missed_call": False,
    "force_resend_on_app": True,
    "payment_required_probe": "off",
    "pin_app_version_substr": "",
    "hunt_device_max_uses": 1,
    "hunt_proxy_max_uses": 1,
}

VAULT4 = {
    **COMMON_FLAGS,
    "official_client_emulation": False,
    "api_credential_mode": "custom",
    "active_app_type": "telegram_android_public",
    "custom_api_id": 4,
    "custom_api_hash": VAULT_API4_HASH,
    "pin_app_version_substr": "12.7.3",
}

VAULT6 = {
    **COMMON_FLAGS,
    "official_client_emulation": False,
    "api_credential_mode": "official",
    "active_app_type": "telegram_android",
    "custom_api_id": 6,
    "custom_api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
}

OFFICIAL6 = {
    **COMMON_FLAGS,
    "official_client_emulation": True,
    "api_credential_mode": "official",
    "active_app_type": "telegram_android",
    "custom_api_id": 6,
    "custom_api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
}

HYPOTHESES: Dict[str, Dict[str, Any]] = {
    "H1": {
        "label": "H1_vault4_firebase_iq",
        "hypothesis": "iq + 非 official + api_id=4 + Push + firebase/unknown + 强制 resend + 每号新设备",
        "country": "iq",
        "count": 6,
        "threads": 6,
        "max_attempts": 2,
        "max_price": 0.85,
        "verify": "vault_api4",
        "apply": dict(VAULT4),
    },
    "H2": {
        "label": "H2_vault4_flashcall_iq",
        "hypothesis": "同 H1，打开 flashcall/missed_call 观察是否离开 App",
        "country": "iq",
        "count": 4,
        "threads": 4,
        "max_attempts": 2,
        "max_price": 0.85,
        "verify": "vault_api4",
        "apply": {**VAULT4, "code_settings_allow_flashcall": True, "code_settings_allow_missed_call": True},
    },
    "H3": {
        "label": "H3_vault6_firebase_iq",
        "hypothesis": "iq + 非 official + api_id=6 + Push + firebase/unknown（不走 official email 旗标）",
        "country": "iq",
        "count": 6,
        "threads": 6,
        "max_attempts": 2,
        "max_price": 0.85,
        "verify": "api6_push",
        "apply": dict(VAULT6),
    },
    "H4": {
        "label": "H4_official6_firebase_iq",
        "hypothesis": "official api_id=6 + allow_firebase，检验能否走 Firebase 而非 Email→Payment",
        "country": "iq",
        "count": 4,
        "threads": 4,
        "max_attempts": 2,
        "max_price": 0.85,
        "verify": "official_api6",
        "apply": dict(OFFICIAL6),
    },
    "H5": {
        "label": "H5_payment_probe_iq",
        "hypothesis": "PaymentRequired 后 resend + assignPlayMarketTransaction 探测（无真实收据）",
        "country": "iq",
        "count": 3,
        "threads": 3,
        "max_attempts": 2,
        "max_price": 0.85,
        "verify": "official_api6",
        "apply": {**OFFICIAL6, "payment_required_probe": "both"},
    },
    "H6": {
        "label": "H6_vault4_highprice_iq",
        "hypothesis": "H1 配置 + 更高 max_price 筛 SMS 友好 iq 号",
        "country": "iq",
        "count": 5,
        "threads": 5,
        "max_attempts": 2,
        "max_price": 1.5,
        "verify": "vault_api4",
        "apply": dict(VAULT4),
    },
    "H7": {
        "label": "H7_vault4_jo",
        "hypothesis": "换国 jo，复用 H1 vault api_id=4",
        "country": "jo",
        "count": 5,
        "threads": 5,
        "max_attempts": 2,
        "max_price": 1.0,
        "verify": "vault_api4",
        "apply": dict(VAULT4),
    },
    "H8": {
        "label": "H8_vault6_ae",
        "hypothesis": "换国 ae，复用 H3 vault api_id=6",
        "country": "ae",
        "count": 5,
        "threads": 5,
        "max_attempts": 2,
        "max_price": 1.2,
        "verify": "api6_push",
        "apply": dict(VAULT6),
    },
}


def apply_config(client: ApiClient, snapshot: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(snapshot)
    for key, value in patch.items():
        cfg[key] = value
    saved = client.request("POST", "/api/config", cfg)
    return {k: saved.get(k) for k in patch}


def verify_task_logs(blob: str, verify: str, expect_official: bool) -> Dict[str, Any]:
    checks = CRED_CHECK_RE.findall(blob)
    parsed = [
        {"api_id": m[0], "api_hash": m[1], "attach": m[2], "push": m[3], "cs_token": m[4]}
        for m in checks
    ]
    firebase_flags = FIREBASE_FLAG_RE.findall(blob)
    unknown_flags = UNKNOWN_FLAG_RE.findall(blob)
    out: Dict[str, Any] = {
        "verify": verify,
        "attach_token_yes": bool(ATTACH_YES_RE.search(blob)),
        "official_emulation_log": bool(OFFICIAL_EMU_RE.search(blob)),
        "flood": bool(FLOOD_RE.search(blob)),
        "sms_code_received": bool(SMS_CODE_RE.search(blob)),
        "firebase_sent": bool(FIREBASE_TYPE_RE.search(blob)),
        "payment": bool(PAYMENT_RE.search(blob)),
        "email_setup": bool(EMAIL_SETUP_RE.search(blob)),
        "play_probe": bool(PLAY_PROBE_RE.search(blob)),
        "resend_types": RESEND_RE.findall(blob),
        "firebase_flags": firebase_flags,
        "unknown_flags": unknown_flags,
        "cred_checks": parsed,
        "problems": [],
        "ok": False,
    }
    if expect_official and not out["official_emulation_log"]:
        out["problems"].append("期望官方模拟日志但未出现")
    if not expect_official and out["official_emulation_log"]:
        out["problems"].append("非 official 假设却出现「官方客户端模拟」")
    if not out["attach_token_yes"] and parsed:
        out["problems"].append("日志缺少 attach_token=是")
    api_ids = {c["api_id"] for c in parsed} or set(re.findall(r"api_id=(\d+)", blob))
    hashes = {c["api_hash"] for c in parsed} or set(re.findall(r"api_hash=(\S+)", blob))
    if verify == "vault_api4":
        if "4" not in api_ids and parsed:
            out["problems"].append(f"未见 api_id=4（见到 {sorted(api_ids)}）")
        if parsed and not any(h.startswith(VAULT_API4_HASH_PREFIX) for h in hashes):
            out["problems"].append(f"未见 hash=014b35…（见到 {sorted(hashes)}）")
    elif verify in {"api6_push", "official_api6"}:
        if "6" not in api_ids and parsed:
            out["problems"].append(f"未见 api_id=6（见到 {sorted(api_ids)}）")
        if parsed and not any(h.startswith(API6_HASH_PREFIX) for h in hashes):
            out["problems"].append(f"未见 hash=eb06d4…（见到 {sorted(hashes)}）")
    out["ok"] = not out["problems"]
    return out


def classify_outcome(analysis: Dict[str, Any], summary: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    if int(summary.get("success") or 0) > 0:
        return "SUCCESS"
    types = analysis.get("sent_code_types") or {}
    if int(summary.get("sms_code_received") or 0) > 0 or types.get("SentCodeTypeSms") or types.get("SentCodeTypeFirebaseSms"):
        return "SMS_SIGNAL"
    if any("FirebaseSms" in str(k) for k in types):
        return "FIREBASE"
    if any("PaymentRequired" in str(k) for k in types) or analysis.get("email_then_payment"):
        return "PAYMENT"
    if any("SetUpEmailRequired" in str(k) or "Email" in str(k) for k in types):
        return "EMAIL"
    if any(r.get("log_verification", {}).get("flood") for r in rows):
        flood_n = sum(1 for r in rows if r.get("log_verification", {}).get("flood"))
        send_n = int(analysis.get("sendcode_samples") or 0)
        if send_n == 0 and flood_n:
            return "FLOOD"
        if flood_n >= max(1, len(rows) // 2) and send_n == 0:
            return "FLOOD"
    if types.get("SentCodeTypeApp") and not types.get("SentCodeTypeSms"):
        return "APP"
    if int(analysis.get("sendcode_samples") or 0) == 0:
        return "NO_SENDCODE"
    return "OTHER"


def run_hypothesis(
    client: ApiClient,
    *,
    hid: str,
    spec: Dict[str, Any],
    snapshot: Dict[str, Any],
    sms_provider: str,
    proxy_mode: str,
    poll: float,
    batch_timeout: float,
    count_override: Optional[int] = None,
    threads_override: Optional[int] = None,
) -> Dict[str, Any]:
    applied = apply_config(client, snapshot, spec["apply"])
    country = spec["country"]
    count = int(count_override or spec["count"])
    threads = min(int(threads_override or spec["threads"]), count, 10)
    attempts = int(spec.get("max_attempts") or 2)
    app_type = applied.get("active_app_type") or spec["apply"].get("active_app_type")
    print(
        f"\n=== {hid} {spec['label']} === {spec['hypothesis']}\n"
        f"    country={country} count={count} threads={threads} attempts={attempts} "
        f"price={spec.get('max_price')} app={app_type} @ {utc_now()}",
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
    verifications: List[Dict[str, Any]] = []
    success_records: List[Dict[str, Any]] = []
    for t in tasks:
        tid = t.get("task_id") or t.get("id")
        full = client.get_task(tid)
        base = parse_task_evidence(full, country)
        row = enrich_row(base, full)
        blob = "\n".join(full.get("logs") or [])
        v = verify_task_logs(blob, spec.get("verify") or "", expect_official)
        row["log_verification"] = v
        row["registration_success"] = str(full.get("status") or "").lower() in SUCCESS_STATUS
        if row["registration_success"]:
            phone = str(full.get("phone") or base.get("phone") or "")
            session_name = str(full.get("session_name") or "")
            success_records.append({
                "task_id": tid,
                "phone_masked": _mask(phone),
                "api_id": (v.get("cred_checks") or [{}])[0].get("api_id"),
                "session_path": f"data/sessions/{session_name}.session" if session_name else None,
                "applied": applied,
            })
        verifications.append(v)
        rows.append(row)
        print(
            f"    task={tid} status={row.get('status')} flood={v['flood']} "
            f"sms={v['sms_code_received']} pay={v['payment']} fb={v['firebase_sent']} "
            f"ok={v['ok']}",
            flush=True,
        )
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    outcome = classify_outcome(analysis, summary, rows)
    leased = int(analysis.get("leased_numbers") or 0)
    print(
        f"    -> 租号={leased} 发码={analysis['sendcode_samples']} "
        f"types={analysis['sent_code_types']} SMS={summary.get('sms_code_received')} "
        f"成功={summary.get('success')} outcome={outcome} "
        f"校验={sum(1 for v in verifications if v.get('ok'))}/{len(verifications)} "
        f"耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "hypothesis_id": hid,
        "label": spec["label"],
        "hypothesis": spec["hypothesis"],
        "country": country,
        "count": count,
        "threads": threads,
        "max_attempts": attempts,
        "max_price": spec.get("max_price"),
        "applied": applied,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "log_verification_ok": all(v.get("ok") for v in verifications) if verifications else False,
        "outcome": outcome,
        "summary": summary,
        "analysis": analysis,
        "success_records": success_records,
        "rows": rows,
    }


def _mask(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        return "***"
    return f"+{digits[:4]}****{digits[-3:]}"


def next_queue(
    hid: str,
    outcome: str,
    ran: List[str],
    skipped: set,
    remaining_budget: int,
) -> Tuple[List[str], set]:
    """根据本轮结果决定后续假设。返回 (prepend_ids, new_skipped)。"""
    skip = set(skipped)
    prepend: List[str] = []
    ran_set = set(ran)

    def take(name: str) -> None:
        if name not in ran_set and name not in skip:
            prepend.append(name)

    if outcome == "SUCCESS":
        return [], skip
    if outcome == "SMS_SIGNAL":
        prepend.append(hid)
        return prepend, skip
    if outcome == "FLOOD" and hid in {"H1", "H2", "H6", "H7"}:
        skip.update({"H2", "H6"})
        take("H3")
        take("H4")
        return prepend, skip
    if outcome == "APP":
        if hid == "H1":
            take("H2")
            take("H6")
            take("H3")
        elif hid in {"H3", "H4"}:
            take("H6") if hid == "H3" else take("H5")
            take("H7")
        elif hid in {"H6", "H2"}:
            take("H3")
            take("H7")
        return prepend, skip
    if outcome == "PAYMENT":
        skip.update({"H4"})
        take("H5")
        if hid != "H3":
            take("H3")
        take("H7")
        return prepend, skip
    if outcome == "FIREBASE":
        prepend.append(hid)
        return prepend, skip
    if outcome == "EMAIL":
        take("H5")
        take("H1")
        return prepend, skip
    if remaining_budget >= 8:
        take("H7")
        take("H8")
    return prepend, skip


def persist_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--hypotheses", default="auto", help="auto 或逗号分隔 H1,H3,...")
    parser.add_argument("--budget", type=int, default=42, help="最大租号次数")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=1200.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"grok_autonomous_sprint_{stamp}.json"

    if args.hypotheses.strip().lower() == "auto":
        queue = ["H1"]
        auto = True
    else:
        queue = [x.strip().upper() for x in args.hypotheses.split(",") if x.strip()]
        auto = False
    unknown = [x for x in queue if x not in HYPOTHESES]
    if unknown:
        print(f"未知假设: {unknown}，可选 {list(HYPOTHESES)}", file=sys.stderr)
        return 2

    print(
        f"Grok 自主冲刺 budget={args.budget} queue={queue} auto={auto} "
        f"emu={original.get('official_client_emulation')} vault_meta={len(load_vault_api4_meta())}",
        flush=True,
    )
    balances_before = snapshot_balances(client)
    rounds: List[Dict[str, Any]] = []
    ran: List[str] = []
    skipped: set = set()
    leases_used = 0
    success_records: List[Dict[str, Any]] = []
    stop_reason = "budget_or_queue_exhausted"

    try:
        while queue and leases_used < args.budget:
            hid = queue.pop(0)
            if hid in skipped:
                continue
            spec = HYPOTHESES[hid]
            planned = int(spec["count"]) * int(spec.get("max_attempts") or 2)
            if leases_used + max(spec["count"], 1) > args.budget and ran:
                print(f"\n>>> 预算不足，跳过 {hid}（已用 {leases_used}/{args.budget}）", flush=True)
                break
            if planned > args.budget - leases_used:
                # 缩小本批任务数以贴合剩余预算
                remain = max(1, args.budget - leases_used)
                spec_count = max(1, min(spec["count"], remain))
            else:
                spec_count = spec["count"]
            result = run_hypothesis(
                client,
                hid=hid,
                spec=spec,
                snapshot=original,
                sms_provider=args.sms_provider,
                proxy_mode=args.proxy_mode,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
                count_override=spec_count,
            )
            leases_used += int(result["analysis"].get("leased_numbers") or 0)
            rounds.append(result)
            ran.append(hid)
            success_records.extend(result.get("success_records") or [])
            persist_report(report_path, _report_payload(
                stamp, args, original, balances_before, client, rounds,
                leases_used, success_records, stop_reason, queue, list(skipped),
            ))
            if result["outcome"] == "SUCCESS" or success_records:
                stop_reason = "registration_success"
                print("\n*** 注册成功，停止冲刺 ***", flush=True)
                break
            if auto:
                extra, skipped = next_queue(
                    hid, result["outcome"], ran, skipped, args.budget - leases_used,
                )
                # 已跑过的不再无脑重复，除非 SMS_SIGNAL / FIREBASE 明确要求加码
                if result["outcome"] in {"SMS_SIGNAL", "FIREBASE"}:
                    queue = [hid] + extra + queue
                else:
                    seen = set(ran) | set(queue)
                    for item in extra:
                        if item not in seen:
                            queue.append(item)
                    if not queue:
                        for fallback in ("H3", "H4", "H6", "H7", "H8"):
                            if fallback not in ran and fallback not in skipped:
                                queue.append(fallback)
                                break
            print(
                f"    预算 {leases_used}/{args.budget}  下一队列={queue}  skipped={sorted(skipped)}",
                flush=True,
            )
        else:
            if not success_records:
                stop_reason = "exhausted_hypotheses" if not queue else "budget_exhausted"
    finally:
        restored = client.request("POST", "/api/config", original)
        print(
            f"\n已恢复 config emu={restored.get('official_client_emulation')} "
            f"delivery={restored.get('code_delivery_mode')}",
            flush=True,
        )

    payload = _report_payload(
        stamp, args, original, balances_before, client, rounds,
        leases_used, success_records, stop_reason, queue, list(skipped),
    )
    persist_report(report_path, payload)
    print(f"\n报告: {report_path}", flush=True)
    print(
        f"结论: stop={stop_reason} 租号={leases_used} 成功={len(success_records)} "
        f"轮次={[r['hypothesis_id']+':'+r['outcome'] for r in rounds]}",
        flush=True,
    )
    return 0 if success_records else 1


def _report_payload(
    stamp: str,
    args: argparse.Namespace,
    original: Dict[str, Any],
    balances_before: Dict[str, Any],
    client: ApiClient,
    rounds: List[Dict[str, Any]],
    leases_used: int,
    success_records: List[Dict[str, Any]],
    stop_reason: str,
    queue: List[str],
    skipped: List[str],
) -> Dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "mode": "grok_autonomous_sprint",
        "stamp": stamp,
        "cli": {
            "budget": args.budget,
            "hypotheses": args.hypotheses,
            "sms_provider": args.sms_provider,
        },
        "vault_api4_success_meta_count": len(load_vault_api4_meta()),
        "balances_before": balances_before,
        "balances_after": snapshot_balances(client),
        "leases_used": leases_used,
        "stop_reason": stop_reason,
        "pending_queue": queue,
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
        "config_restored_emu": original.get("official_client_emulation"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
