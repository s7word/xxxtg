#!/usr/bin/env python3
"""Vault 模式注册冲刺：关闭 official_client_emulation，模仿凭证库成功 +91 路径。

强制配置快照::

    official_client_emulation=false
    api_id=4 + hash 014b35…（official 凭证或 custom 显式配对）
    code_delivery_mode=push_required
    email=smsbower_only
    attach Push Token（force_skip_push_attach=false）

每轮日志核对：api_id=4、hash=014b、attach_token=是、official=false（无「官方客户端模拟」）。

用法::

    python3 backend/scripts/run_vault_mode_sprint.py \\
        --country in --count 5 --max-attempts 2 --threads 3

    python3 backend/scripts/run_vault_mode_sprint.py \\
        --country in --vault-replay --control-country cl --control-count 2
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
from backend.scripts.run_registration_sprint import (  # noqa: E402
    parse_task_evidence,
    snapshot_balances,
    summarize_evidence,
)

VAULT_API4_HASH = "014b35b6184100b085b0d0572f9b5103"
VAULT_API4_HASH_PREFIX = "014b35"

ATTACH_YES_RE = re.compile(r"attach_token=是")
OFFICIAL_EMU_RE = re.compile(r"官方客户端模拟")
CRED_CHECK_RE = re.compile(
    r"sendCode 凭证核对: api_id=(?P<api_id>\d+) api_hash=(?P<api_hash>\S+) "
    r"attach_token=(?P<attach>\S) push_token=(?P<push>\S) code_settings.token=(?P<cs>\S)"
)
PUSH_GOT_RE = re.compile(r"成功获取平台合规签署的 Attestation Push Token")
FLOOD_RE = re.compile(r"API_ID_PUBLISHED_FLOOD")
SMS_CODE_RE = re.compile(r"STATUS_OK|收到验证码|验证码[:：]\s*\d{4,6}|接码成功")
SUCCESS_STATUS = {"success"}

# 可复制到 Settings 的 vault 模式清单
VAULT_MODE_SETTINGS_SNAPSHOT = {
    "official_client_emulation": False,
    "force_skip_push_attach": False,
    "code_delivery_mode": "push_required",
    "email_provider_mode": "smsbower_only",
    "api_credential_mode": "official",
    "active_app_type": "telegram_android_public",
    "custom_api_id": 4,
    "custom_api_hash": VAULT_API4_HASH,
}


def load_vault_api4_meta() -> List[Dict[str, Any]]:
    """从 lod_user 成功 +91 JSON 抽取 api_id=4 设备指纹（不含密钥正文）。"""
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
        })
    return rows


def pick_vault_replay_profile(meta: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """选一条 +91 vault 设备模板用于 replay 变体说明。"""
    for row in meta:
        if row.get("app_version") and "12.7.3" in str(row["app_version"]):
            return row
    return meta[0] if meta else None


def vault_mode_apply(
    *,
    vault_replay: bool,
    replay_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建 vault 模式 config 补丁。"""
    cfg = dict(VAULT_MODE_SETTINGS_SNAPSHOT)
    if vault_replay:
        cfg["api_credential_mode"] = "custom"
        cfg["custom_api_id"] = 4
        cfg["custom_api_hash"] = VAULT_API4_HASH
    if replay_profile:
        cfg["_vault_replay_device_hint"] = {
            "device": replay_profile.get("device"),
            "app_version": replay_profile.get("app_version"),
            "sdk": replay_profile.get("sdk"),
            "system_lang_pack": replay_profile.get("system_lang_pack"),
            "source_file": replay_profile.get("file"),
        }
    return cfg


def apply_vault_config(
    client: ApiClient,
    snapshot: Dict[str, Any],
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    cfg = dict(snapshot)
    hint = patch.pop("_vault_replay_device_hint", None)
    for key, value in patch.items():
        cfg[key] = value
    saved = client.request("POST", "/api/config", cfg)
    applied = {k: saved.get(k) for k in patch}
    if hint:
        applied["vault_replay_device_hint"] = hint
    return applied


def verify_vault_task_logs(blob: str) -> Dict[str, Any]:
    checks = CRED_CHECK_RE.findall(blob)
    parsed = [
        {"api_id": m[0], "api_hash": m[1], "attach": m[2], "push": m[3], "cs_token": m[4]}
        for m in checks
    ]
    out: Dict[str, Any] = {
        "attach_token_yes": bool(ATTACH_YES_RE.search(blob)),
        "official_emulation_log": bool(OFFICIAL_EMU_RE.search(blob)),
        "push_obtained": bool(PUSH_GOT_RE.search(blob)),
        "flood": bool(FLOOD_RE.search(blob)),
        "sms_code_received": bool(SMS_CODE_RE.search(blob)),
        "cred_checks": parsed,
        "problems": [],
        "ok": False,
    }
    if out["official_emulation_log"]:
        out["problems"].append("日志仍含「官方客户端模拟」，official_client_emulation 未关闭")
    if not out["attach_token_yes"]:
        out["problems"].append("日志缺少 attach_token=是")
    api_ids = {c["api_id"] for c in parsed} or set(re.findall(r"api_id=(\d+)", blob))
    hashes = {c["api_hash"] for c in parsed} or set(re.findall(r"api_hash=(\S+)", blob))
    if "4" not in api_ids:
        out["problems"].append(f"日志未见 api_id=4（见到 {sorted(api_ids)}）")
    if not any(h.startswith(VAULT_API4_HASH_PREFIX) for h in hashes):
        out["problems"].append(f"日志未见 hash=014b35…（见到 {sorted(hashes)}）")
    out["ok"] = not out["problems"]
    return out


def load_official_baseline(country: str) -> Dict[str, Any]:
    """从既有 AB 报告读取 official 模式 sent_code 分布作对照。"""
    out_dir = REPO_ROOT / "data" / "ab_reports"
    candidates = sorted(out_dir.glob("vault_compare_*_*.json"), reverse=True)
    candidates += sorted(out_dir.glob("grok_api4_retest_*_*.json"), reverse=True)
    baseline: Dict[str, Any] = {"source": None, "official_sent_code_types": {}, "vault_sent_code_types": {}}
    cc = country.lower()
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in data.get("comparison_table") or []:
            label = str(row.get("label") or "")
            if cc not in label.lower() and cc != str(data.get("cli_country", "")).lower():
                continue
            types = row.get("sent_code_types") or {}
            if "official" in label.lower() or label.startswith("V1") or label.startswith("G2"):
                baseline["official_sent_code_types"] = types
                baseline["source"] = str(path.relative_to(REPO_ROOT))
            if "vault" in label.lower() or label.startswith("V3"):
                baseline["vault_sent_code_types"] = types
        if baseline["source"]:
            break
    return baseline


def run_round(
    client: ApiClient,
    *,
    label: str,
    snapshot: Dict[str, Any],
    country: str,
    count: int,
    threads: int,
    max_attempts: int,
    sms_provider: str,
    max_price: float,
    proxy_mode: str,
    poll: float,
    batch_timeout: float,
    vault_replay: bool,
    replay_profile: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    patch = vault_mode_apply(vault_replay=vault_replay, replay_profile=replay_profile)
    applied = apply_vault_config(client, snapshot, patch)
    app_type = applied.get("active_app_type") or "telegram_android_public"
    concurrency = min(threads, count)
    print(
        f"\n=== {label} === country={country} count={count} threads={concurrency} "
        f"max_attempts={max_attempts} vault_replay={vault_replay}\n"
        f"    applied={applied} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type=app_type,
        count=count,
        concurrency=concurrency,
        sms_provider=sms_provider,
        max_price=max_price,
        max_number_attempts=max_attempts,
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
        base = parse_task_evidence(full, country)
        row = enrich_row(base, full)
        blob = "\n".join(full.get("logs") or [])
        v = verify_vault_task_logs(blob)
        row["log_verification"] = v
        row["registration_success"] = str(full.get("status") or "").lower() in SUCCESS_STATUS
        verifications.append(v)
        rows.append(row)
        print(
            f"    task={tid} status={row.get('status')} flood={v['flood']} "
            f"sms={v['sms_code_received']} attach={v['attach_token_yes']} ok={v['ok']}",
            flush=True,
        )
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    summary["log_verification_pass"] = sum(1 for v in verifications if v.get("ok"))
    print(
        f"    -> 租号={analysis['leased_numbers']} 发码={analysis['sendcode_samples']} "
        f"types={analysis['sent_code_types']} SMS收码={summary.get('sms_code_received')} "
        f"成功={summary.get('success')} 校验={summary['log_verification_pass']}/{len(verifications)} "
        f"耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "label": label,
        "country": country,
        "count": count,
        "threads": concurrency,
        "max_attempts": max_attempts,
        "vault_replay": vault_replay,
        "applied": applied,
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
    parser.add_argument("--country", default="in", help="主实验国家（默认 in +91）")
    parser.add_argument("--count", type=int, default=5, help="任务数")
    parser.add_argument("--max-attempts", type=int, default=2, help="每任务最大取号次数")
    parser.add_argument("--threads", type=int, default=3, help="并发线程数")
    parser.add_argument("--vault-replay", action="store_true", help="custom api_id=4 + vault 设备 meta 对照")
    parser.add_argument("--control-country", default="", help="主实验有 SMS 信号时追加对照国（如 cl）")
    parser.add_argument("--control-count", type=int, default=2, help="对照国任务数")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--max-price", type=float, default=1.0)
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=900.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original = client.get_config()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vault_meta = load_vault_api4_meta()
    replay_profile = pick_vault_replay_profile(vault_meta) if args.vault_replay else None
    baseline = load_official_baseline(args.country.lower())

    print(
        f"Vault 模式冲刺 emu={original.get('official_client_emulation')} "
        f"vault_meta={len(vault_meta)} baseline={baseline.get('source')}",
        flush=True,
    )
    balances_before = snapshot_balances(client)
    rounds: List[Dict[str, Any]] = []

    try:
        rounds.append(run_round(
            client,
            label="vault_mode_primary",
            snapshot=original,
            country=args.country.lower(),
            count=args.count,
            threads=args.threads,
            max_attempts=args.max_attempts,
            sms_provider=args.sms_provider,
            max_price=args.max_price,
            proxy_mode=args.proxy_mode,
            poll=args.poll,
            batch_timeout=args.batch_timeout,
            vault_replay=args.vault_replay,
            replay_profile=replay_profile,
        ))
        primary = rounds[0]
        sms_signal = (
            int(primary["summary"].get("sms_code_received") or 0) > 0
            or (primary["analysis"].get("sent_code_types") or {}).get("SentCodeTypeSms", 0) > 0
        )
        control_cc = (args.control_country or "").strip().lower()
        if control_cc and sms_signal:
            print(f"\n>>> in 有 SMS 信号，追加对照国 {control_cc} x{args.control_count}", flush=True)
            rounds.append(run_round(
                client,
                label=f"vault_mode_control_{control_cc}",
                snapshot=original,
                country=control_cc,
                count=args.control_count,
                threads=min(args.threads, args.control_count),
                max_attempts=args.max_attempts,
                sms_provider=args.sms_provider,
                max_price=args.max_price,
                proxy_mode=args.proxy_mode,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
                vault_replay=args.vault_replay,
                replay_profile=replay_profile,
            ))
        elif control_cc and not sms_signal:
            print(f"\n>>> in 无 SMS 信号，跳过对照国 {control_cc}", flush=True)
    finally:
        restored = client.request("POST", "/api/config", original)
        print(
            f"\n已恢复 config emu={restored.get('official_client_emulation')} "
            f"delivery={restored.get('code_delivery_mode')}",
            flush=True,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"vault_mode_sprint_{args.country.lower()}_{stamp}.json"
    primary_summary = rounds[0]["summary"] if rounds else {}
    report = {
        "generated_at": utc_now(),
        "mode": "vault_mode_sprint",
        "cli": {
            "country": args.country.lower(),
            "count": args.count,
            "max_attempts": args.max_attempts,
            "threads": args.threads,
            "vault_replay": args.vault_replay,
            "control_country": args.control_country or None,
        },
        "vault_mode_settings_snapshot": VAULT_MODE_SETTINGS_SNAPSHOT,
        "vault_api4_success_meta": vault_meta,
        "vault_replay_profile": replay_profile,
        "official_baseline_comparison": baseline,
        "balances_before": balances_before,
        "balances_after": snapshot_balances(client),
        "comparison_table": build_comparison_table(
            [{"experiment_id": str(i + 1), **r} for i, r in enumerate(rounds)]
        ),
        "rounds": rounds,
        "outcomes": {
            "sms_code_received_tasks": primary_summary.get("sms_code_received"),
            "registration_success_tasks": primary_summary.get("success"),
            "sent_code_distribution": primary_summary.get("sent_code_types"),
            "had_sms_signal": bool(
                int(primary_summary.get("sms_code_received") or 0) > 0
                or (primary_summary.get("sent_code_types") or {}).get("SentCodeTypeSms")
            ),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {report_path}", flush=True)

    failed_verify = [r["label"] for r in rounds if not r.get("log_verification_ok")]
    if failed_verify:
        print(f"日志校验未全部通过: {failed_verify}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
