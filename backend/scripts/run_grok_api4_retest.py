#!/usr/bin/env python3
"""Grok 4.6 api_id=4 Push attach 对照实验。

变体（每变体 ≥2 号，smsbower email）::

    1  official + api_id=4 + 强制 attach Push（校验日志 attach_token=是 / hash=014b35…）
    2  official + api_id=6 同国对照
    3  api_id=4 + force_skip_push_attach（故意不 attach，应 FLOOD）
    4  vault +91 成功账号同款 api_id=4 / hash / 12.7.3 设备 replay（country=in）

用法::

    python3 backend/scripts/run_grok_api4_retest.py --country iq --threads 2
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
from backend.scripts.run_payment_bypass_ab import (  # noqa: E402
    analyze_round,
    build_comparison_table,
    enrich_row,
)
from backend.scripts.run_registration_sprint import parse_task_evidence, snapshot_balances, summarize_evidence  # noqa: E402

VAULT_API4_HASH = "014b35b6184100b085b0d0572f9b5103"
VAULT_API4_HASH_PREFIX = "014b35"
ATTACH_YES_RE = re.compile(r"attach_token=是")
CRED_CHECK_RE = re.compile(
    r"sendCode 凭证核对: api_id=(?P<api_id>\d+) api_hash=(?P<api_hash>\S+) "
    r"attach_token=(?P<attach>\S) push_token=(?P<push>\S) code_settings.token=(?P<cs>\S)"
)
PUSH_GOT_RE = re.compile(r"成功获取平台合规签署的 Attestation Push Token")
FLOOD_RE = re.compile(r"API_ID_PUBLISHED_FLOOD")
FLOOD_MISDIAG_RE = re.compile(r"缺少合法 Push Token")
FLOOD_ATTACHED_RE = re.compile(r"已 attach REGHelp Push Token")
EMAIL_SETUP_RE = re.compile(r"SentCodeTypeSetUpEmailRequired")
MISSING_PUSH_RE = re.compile(r"PUSH_TOKEN_MISSING|未返回可用凭证|拒绝以 api_id=")


def load_vault_api4_meta() -> List[Dict[str, Any]]:
    """从 lod_user 成功 +91 JSON 抽取 api_id=4 账号指纹（不写入密钥到报告正文过长字段）。"""
    rows: List[Dict[str, Any]] = []
    root = REPO_ROOT / "lod_user"
    if not root.exists():
        return rows
    for path in sorted(root.rglob("91*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        app_id = data.get("app_id") or data.get("api_id")
        try:
            app_id = int(app_id)
        except (TypeError, ValueError):
            continue
        if app_id != 4:
            continue
        phone = str(data.get("phone") or path.stem)
        rows.append({
            "file": str(path.relative_to(REPO_ROOT)),
            "phone_tail": phone[-4:] if len(phone) >= 4 else phone,
            "app_id": app_id,
            "app_hash": str(data.get("app_hash") or data.get("api_hash") or ""),
            "app_version": data.get("app_version"),
            "device": data.get("device"),
            "sdk": data.get("sdk"),
            "lang_pack": data.get("lang_pack"),
            "system_lang_pack": data.get("system_lang_pack"),
            "tz_offset": data.get("tz_offset"),
            "has_device_token": bool(data.get("device_token")),
            "has_device_secret": bool(data.get("device_secret")),
        })
    return rows


EXPERIMENT_MATRIX: Dict[str, Dict[str, Any]] = {
    "1": {
        "label": "G1_api4_official_push",
        "description": "official + api_id=4 + attach Push（日志必须 attach_token=是 / hash=014b35）",
        "expect": "SetUpEmailRequired 或至少非 FLOOD；禁止 attach_token=否",
        "country": None,
        "verify": "attach_api4",
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "force_skip_push_attach": False,
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android_public",
        },
    },
    "2": {
        "label": "G2_api6_official_control",
        "description": "official + api_id=6 同国对照",
        "expect": "基线（iq 历史上 email→PaymentRequired）",
        "country": None,
        "verify": "attach_api6",
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "force_skip_push_attach": False,
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android",
        },
    },
    "3": {
        "label": "G3_api4_no_push_control",
        "description": "api_id=4 + 故意不 attach Push（旧污染路径对照）",
        "expect": "API_ID_PUBLISHED_FLOOD 或 PUSH_TOKEN_MISSING",
        "country": None,
        "verify": "skip_attach_flood",
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "force_skip_push_attach": True,
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android_public",
        },
    },
    "4": {
        "label": "G4_vault_api4_replay_in",
        "description": "vault +91 成功路径 replay：api_id=4 + 正确 hash + official attach",
        "expect": "若号池允许，应能 sendCode；日志 api_id=4 / 014b35 / attach=是",
        "country": "in",
        "verify": "attach_api4",
        "apply": {
            "official_client_emulation": True,
            "api_credential_mode": "official",
            "code_delivery_mode": "push_required",
            "force_skip_push_attach": False,
            "email_provider_mode": "smsbower_only",
            "active_app_type": "telegram_android_public",
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


def verify_task_logs(blob: str, verify: str) -> Dict[str, Any]:
    checks = CRED_CHECK_RE.findall(blob)
    parsed = [
        {"api_id": m[0], "api_hash": m[1], "attach": m[2], "push": m[3], "cs_token": m[4]}
        for m in checks
    ]
    out: Dict[str, Any] = {
        "verify": verify,
        "attach_token_yes": bool(ATTACH_YES_RE.search(blob)),
        "push_obtained": bool(PUSH_GOT_RE.search(blob)),
        "flood": bool(FLOOD_RE.search(blob)),
        "flood_misdiagnosed_missing_push": bool(FLOOD_MISDIAG_RE.search(blob)),
        "flood_with_attached_token": bool(FLOOD_ATTACHED_RE.search(blob)),
        "email_setup": bool(EMAIL_SETUP_RE.search(blob)),
        "missing_required_push": bool(MISSING_PUSH_RE.search(blob)),
        "cred_checks": parsed,
        "ok": False,
        "problems": [],
    }
    if verify == "attach_api4":
        if not out["attach_token_yes"]:
            out["problems"].append("日志缺少 attach_token=是")
        hashes = {c["api_hash"] for c in parsed} or set(re.findall(r"api_hash=(\S+)", blob))
        api_ids = {c["api_id"] for c in parsed} or set(re.findall(r"api_id=(\d+)", blob))
        if "4" not in api_ids:
            out["problems"].append(f"日志未见 api_id=4（见到 {sorted(api_ids)}）")
        if not any(h.startswith(VAULT_API4_HASH_PREFIX) for h in hashes):
            out["problems"].append(f"日志未见 api_hash=014b35…（见到 {sorted(hashes)}）")
        if out["flood"] and out["flood_misdiagnosed_missing_push"] and out["push_obtained"]:
            out["problems"].append("有 Push 仍用「缺少合法 Push Token」文案（误判未修）")
        out["ok"] = not out["problems"]
    elif verify == "attach_api6":
        if not out["attach_token_yes"]:
            out["problems"].append("日志缺少 attach_token=是")
        api_ids = {c["api_id"] for c in parsed} or set(re.findall(r"api_id=(\d+)", blob))
        if "6" not in api_ids:
            out["problems"].append(f"日志未见 api_id=6（见到 {sorted(api_ids)}）")
        out["ok"] = not out["problems"]
    elif verify == "skip_attach_flood":
        attached_send = any(c["attach"] == "是" and c["cs_token"] == "有" for c in parsed)
        if attached_send:
            out["problems"].append("对照变体仍 attach 了 Push，污染 FLOOD 对照")
        if not (out["flood"] or out["missing_required_push"]):
            out["problems"].append("故意不 attach 却未出现 FLOOD / PUSH_TOKEN_MISSING")
        out["ok"] = not out["problems"]
    else:
        out["ok"] = True
    return out


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
    verifications = []
    for t in tasks:
        tid = t.get("task_id") or t.get("id")
        full = client.get_task(tid)
        base = parse_task_evidence(full, exp_country)
        row = enrich_row(base, full)
        blob = "\n".join(full.get("logs") or [])
        v = verify_task_logs(blob, spec.get("verify") or "")
        row["log_verification"] = v
        verifications.append(v)
        rows.append(row)
        print(
            f"    task={tid} flood={v['flood']} attach_yes={v['attach_token_yes']} "
            f"email={v['email_setup']} ok={v['ok']} problems={v['problems']}",
            flush=True,
        )
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    print(
        f"    -> 租号={analysis['leased_numbers']} 发码={analysis['sendcode_samples']} "
        f"types={analysis['sent_code_types']} 校验通过="
        f"{sum(1 for v in verifications if v.get('ok'))}/{len(verifications)} "
        f"耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "experiment_id": exp_id,
        "label": spec["label"],
        "description": spec["description"],
        "expect": spec.get("expect"),
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
        "log_verification_ok": all(v.get("ok") for v in verifications) if verifications else False,
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
    parser.add_argument("--country", default="iq", help="变体 1–3 国家（默认 iq；变体 4 固定 in）")
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
    vault_meta = load_vault_api4_meta()

    print(
        f"启动快照 emu={original.get('official_client_emulation')} "
        f"delivery={original.get('code_delivery_mode')} "
        f"skip_attach={original.get('force_skip_push_attach')} "
        f"vault_api4={len(vault_meta)} experiments={exp_ids}",
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
            f"skip_attach={restored.get('force_skip_push_attach')}",
            flush=True,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"grok_api4_retest_{args.country.lower()}_{stamp}.json"
    report = {
        "generated_at": utc_now(),
        "mode": "grok_api4_retest",
        "cli_country": args.country.lower(),
        "vault_api4_success_meta": vault_meta,
        "balances_before": balances_before,
        "balances_after": snapshot_balances(client),
        "comparison_table": build_comparison_table(rounds),
        "rounds": rounds,
        "contamination_notes": {
            "pre_fix_034448": (
                "vault_compare V3 03:44:48：plan attach_token=是且拿到 Push 后仍 FLOOD，"
                "但错误文案写「缺少合法 Push Token」，该条不能当「无 Push / 国家结论」。"
            ),
            "hunt_skip_published": (
                "修复前 api_credential_mode=official + 泄露 api_id 会被猎号连续 App "
                "强制 sms_first（不 attach），污染 official 实验。"
            ),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {report_path}", flush=True)
    failed_verify = [r["experiment_id"] for r in rounds if not r.get("log_verification_ok")]
    if failed_verify:
        print(f"日志校验未全部通过: {failed_verify}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
