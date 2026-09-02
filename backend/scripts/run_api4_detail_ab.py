#!/usr/bin/env python3
"""api_id=4 细节对照：Vault 指纹 / InitConnection lang_pack / tz_offset / 国家。

必测（总租号硬上限 16，默认每变体 2 任务 × 1 次取号）::

    T0  当前 public 默认（不钉版本、握手 lang_pack 空、不写 tz_offset）in
    T1  Vault 对齐指纹 + lang_pack=android + tz 对齐号国  in
    T2  相对 T1：lang_pack 空
    T3  相对 T1：不写 InitConnection tz_offset
    T4  与 T1 同配置，国家 iq（看 FLOOD/App 是否改善）
    T5  可选：全新设备+代理 vs 复用（余额与租号额度允许时）

禁止 api_id=6 / Payment / 假收据。

用法::

    python3 backend/scripts/run_api4_detail_ab.py
    python3 backend/scripts/run_api4_detail_ab.py --skip-t5 --lease-cap 12
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
from backend.scripts.run_vault_mode_sprint import (  # noqa: E402
    VAULT_API4_HASH,
    load_vault_api4_meta,
    verify_vault_task_logs,
)

INIT_RE = re.compile(
    r"InitConnection 指纹: lang_pack=(?P<lp>\S+) tz_offset=(?P<tz>\S+)"
)
VAULT_SRC_RE = re.compile(r"vault 指纹回放:\s+(?P<src>\S+)")
LOCALE_RE = re.compile(r"网络语言拓扑:\s+(?P<lang>\S+),\s+时区偏置:\s+(?P<tz>\S+)")
APP_VER_RE = re.compile(r"绑定硬件特征:.*App:\s+(?P<ver>.+)$")

VAULT_BASE: Dict[str, Any] = {
    "official_client_emulation": False,
    "force_skip_push_attach": False,
    "code_delivery_mode": "push_required",
    "email_provider_mode": "smsbower_only",
    "api_credential_mode": "official",
    "active_app_type": "telegram_android_public",
    "custom_api_id": 4,
    "custom_api_hash": VAULT_API4_HASH,
}

T1_STACK: Dict[str, Any] = {
    **VAULT_BASE,
    "pin_app_version_substr": "12.7.3",
    "vault_fingerprint_replay": True,
    "force_country_locale": True,
    "init_connection_set_lang_pack": True,
    "init_connection_set_tz_offset": True,
    "init_connection_tz_offset_override": None,
}


def _variant_specs() -> Dict[str, Dict[str, Any]]:
    return {
        "T0": {
            "hypothesis": "T1",
            "label": "T0_public_default_in",
            "description": "当前 public 默认：不钉 12.7.3、不回放 vault 机型、握手 lang_pack 空、不写 tz_offset",
            "country": "in",
            "count": 2,
            "attempts": 1,
            "apply": {
                **VAULT_BASE,
                "pin_app_version_substr": "",
                "vault_fingerprint_replay": False,
                "force_country_locale": False,
                "init_connection_set_lang_pack": False,
                "init_connection_set_tz_offset": False,
                "init_connection_tz_offset_override": None,
                "hunt_device_max_uses": 8,
                "hunt_proxy_max_uses": 8,
            },
            "expect_init": {"lang_pack_empty": True, "tz_written": False},
        },
        "T1": {
            "hypothesis": "T1",
            "label": "T1_vault_aligned_in",
            "description": "Vault 对齐：api_id=4 + Push + 12.7.3 机型回放 + 号国语言/时区 + lang_pack=android + tz_offset",
            "country": "in",
            "count": 2,
            "attempts": 1,
            "apply": {
                **T1_STACK,
                "hunt_device_max_uses": 8,
                "hunt_proxy_max_uses": 8,
            },
            "expect_init": {"lang_pack": "android", "tz_written": True, "tz": 19800},
        },
        "T2": {
            "hypothesis": "T2",
            "label": "T2_langpack_empty_in",
            "description": "相对 T1：InitConnection lang_pack 保持 Telethon 空串",
            "country": "in",
            "count": 2,
            "attempts": 1,
            "apply": {
                **T1_STACK,
                "init_connection_set_lang_pack": False,
                "hunt_device_max_uses": 8,
                "hunt_proxy_max_uses": 8,
            },
            "expect_init": {"lang_pack_empty": True, "tz_written": True, "tz": 19800},
        },
        "T3": {
            "hypothesis": "T3",
            "label": "T3_tz_omit_in",
            "description": "相对 T1：不写 InitConnection.params.tz_offset（Telethon 缺省）",
            "country": "in",
            "count": 2,
            "attempts": 1,
            "apply": {
                **T1_STACK,
                "init_connection_set_tz_offset": False,
                "hunt_device_max_uses": 8,
                "hunt_proxy_max_uses": 8,
            },
            "expect_init": {"lang_pack": "android", "tz_written": False},
        },
        "T4": {
            "hypothesis": "T4",
            "label": "T4_vault_aligned_iq",
            "description": "与 T1 同栈，国家 iq：看 FLOOD / App 是否相对 G1 改善",
            "country": "iq",
            "count": 2,
            "attempts": 1,
            "apply": {
                **T1_STACK,
                "hunt_device_max_uses": 8,
                "hunt_proxy_max_uses": 8,
            },
            "expect_init": {"lang_pack": "android", "tz_written": True, "tz": 10800},
        },
        "T5a": {
            "hypothesis": "T5",
            "label": "T5_fresh_device_proxy_in",
            "description": "同 T1 配置，每号强制新设备+新代理（max_uses=1）",
            "country": "in",
            "count": 2,
            "attempts": 1,
            "optional": True,
            "apply": {
                **T1_STACK,
                "hunt_device_max_uses": 1,
                "hunt_proxy_max_uses": 1,
            },
            "expect_init": {"lang_pack": "android", "tz_written": True, "tz": 19800},
        },
        "T5b": {
            "hypothesis": "T5",
            "label": "T5_reuse_device_proxy_in",
            "description": "同 T1 配置，1 任务 2 次取号复用设备/代理",
            "country": "in",
            "count": 1,
            "attempts": 2,
            "optional": True,
            "apply": {
                **T1_STACK,
                "hunt_device_max_uses": 8,
                "hunt_proxy_max_uses": 8,
            },
            "expect_init": {"lang_pack": "android", "tz_written": True, "tz": 19800},
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


def parse_handshake(blob: str) -> Dict[str, Any]:
    init = INIT_RE.search(blob)
    locale = LOCALE_RE.search(blob)
    vault_src = VAULT_SRC_RE.search(blob)
    app_ver = APP_VER_RE.search(blob)
    lang_pack = init.group("lp") if init else None
    tz_raw = init.group("tz") if init else None
    tz_written = bool(tz_raw) and tz_raw not in {"未写入", "(empty)"}
    tz_val: Optional[int] = None
    if tz_written:
        try:
            tz_val = int(tz_raw)
        except (TypeError, ValueError):
            tz_val = None
    return {
        "init_log_present": bool(init),
        "init_lang_pack": None if lang_pack in {None, "(empty)"} else lang_pack,
        "init_lang_pack_empty": lang_pack in {None, "(empty)", ""},
        "init_tz_offset": tz_val,
        "init_tz_written": tz_written,
        "profile_lang": locale.group("lang") if locale else None,
        "profile_tz": locale.group("tz") if locale else None,
        "vault_source": vault_src.group("src") if vault_src else None,
        "app_version_log": (app_ver.group("ver").strip() if app_ver else None),
    }


def verify_handshake(hs: Dict[str, Any], expect: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not hs.get("init_log_present"):
        problems.append("日志未见 InitConnection 指纹（可能后端未重启或 Telethon 无 _init_request）")
        return problems
    if expect.get("lang_pack_empty") and not hs.get("init_lang_pack_empty"):
        problems.append(f"期望 lang_pack 空，实际 {hs.get('init_lang_pack')}")
    want_lp = expect.get("lang_pack")
    if want_lp and hs.get("init_lang_pack") != want_lp:
        problems.append(f"期望 lang_pack={want_lp}，实际 {hs.get('init_lang_pack')}")
    if expect.get("tz_written") is True and not hs.get("init_tz_written"):
        problems.append("期望写入 tz_offset，实际未写入")
    if expect.get("tz_written") is False and hs.get("init_tz_written"):
        problems.append(f"期望不写 tz_offset，实际 {hs.get('init_tz_offset')}")
    want_tz = expect.get("tz")
    if want_tz is not None and hs.get("init_tz_offset") != want_tz:
        problems.append(f"期望 tz_offset={want_tz}，实际 {hs.get('init_tz_offset')}")
    return problems


def enrich_handshake(row: Dict[str, Any], full_task: Dict[str, Any], expect: Dict[str, Any]) -> Dict[str, Any]:
    logs = list(full_task.get("logs") or [])
    blob = "\n".join(str(x) for x in logs)
    hs = parse_handshake(blob)
    vault_chk = verify_vault_task_logs(blob)
    hs_problems = verify_handshake(hs, expect)
    row["handshake"] = hs
    row["vault_log_check"] = {
        "ok": vault_chk.get("ok"),
        "problems": vault_chk.get("problems"),
        "flood": vault_chk.get("flood"),
        "attach_token_yes": vault_chk.get("attach_token_yes"),
    }
    row["handshake_problems"] = hs_problems
    row["handshake_ok"] = not hs_problems
    return row


def smsbower_balance(balances: Dict[str, Any]) -> Optional[float]:
    block = balances.get("smsbower") or {}
    raw = block.get("balance")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def country_stock(client: ApiClient, provider: str, codes: List[str]) -> Dict[str, Any]:
    try:
        data = client.request(
            "GET",
            f"/api/sms/available-countries?provider={provider}&refresh=true",
        )
    except Exception as exc:
        return {"error": str(exc)[:240]}
    items = data.get("countries") or data.get("items") or []
    out: Dict[str, Any] = {"provider": provider}
    wanted = {c.lower() for c in codes}
    for item in items:
        code = str(item.get("code") or item.get("country") or "").lower()
        if code in wanted:
            out[code] = {
                "count": item.get("count") or item.get("stock") or item.get("telegram_stock"),
                "price": item.get("price") or item.get("cost") or item.get("price_usd"),
                "name": item.get("name") or item.get("name_zh"),
            }
    return out


def run_variant(
    client: ApiClient,
    *,
    exp_id: str,
    spec: Dict[str, Any],
    snapshot: Dict[str, Any],
    sms_provider: str,
    max_price: float,
    proxy_mode: str,
    poll: float,
    batch_timeout: float,
) -> Dict[str, Any]:
    applied = apply_experiment_config(client, snapshot, spec)
    country = spec["country"]
    count = int(spec["count"])
    attempts = int(spec["attempts"])
    app_type = applied.get("active_app_type") or "telegram_android_public"
    print(
        f"\n=== [{exp_id}] {spec['label']} === {spec['description']}\n"
        f"    country={country} count={count} attempts={attempts} "
        f"app_type={app_type} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type=app_type,
        count=count,
        concurrency=min(count, 2),
        sms_provider=sms_provider,
        max_price=max_price,
        max_number_attempts=attempts,
        no_number_retries=3,
        proxy_mode=proxy_mode,
    )
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)
    final_batch, tasks, timed_out = wait_batch(client, batch_id, poll, batch_timeout)
    rows = []
    expect = spec.get("expect_init") or {}
    for t in tasks:
        tid = t.get("task_id") or t.get("id")
        full = client.get_task(tid)
        base = parse_task_evidence(full, country)
        row = enrich_row(base, full)
        rows.append(enrich_handshake(row, full, expect))
    analysis = analyze_round(rows)
    summary = summarize_evidence(rows)
    summary["sent_code_types"] = analysis["sent_code_types"]
    handshake_ok = all(r.get("handshake_ok") for r in rows) if rows else False
    print(
        f"    -> 租号={analysis['leased_numbers']} 发码={analysis['sendcode_samples']} "
        f"types={analysis['sent_code_types']} FLOOD={summary.get('api_id_published_flood')} "
        f"SMS={summary.get('sms_code_received')} success={summary.get('success')} "
        f"handshake_ok={handshake_ok} 耗时={int(time.time()-started)}s",
        flush=True,
    )
    return {
        "experiment_id": exp_id,
        "hypothesis": spec.get("hypothesis"),
        "label": spec["label"],
        "description": spec["description"],
        "country": country,
        "sms_provider": sms_provider,
        "count": count,
        "attempts_per_thread": attempts,
        "applied": applied,
        "proxy_mode": proxy_mode,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "expect_init": expect,
        "handshake_ok": handshake_ok,
        "summary": summary,
        "analysis": analysis,
        "rows": rows,
    }


def hypothesis_table(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = []
    for item in rounds:
        a = item.get("analysis") or {}
        s = item.get("summary") or {}
        types = a.get("sent_code_types") or s.get("sent_code_types") or {}
        table.append({
            "id": item.get("experiment_id"),
            "hypothesis": item.get("hypothesis"),
            "label": item.get("label"),
            "country": item.get("country"),
            "leased": a.get("leased_numbers") or s.get("leased_numbers") or 0,
            "sendcode": a.get("sendcode_samples") or 0,
            "sent_code_types": types,
            "app": types.get("SentCodeTypeApp") or types.get("auth.sentCodeTypeApp") or 0,
            "sms": sum(v for k, v in types.items() if "Sms" in str(k) and "Firebase" not in str(k)),
            "flood": s.get("api_id_published_flood") or 0,
            "sms_received": s.get("sms_code_received") or 0,
            "success": s.get("success") or 0,
            "handshake_ok": item.get("handshake_ok"),
        })
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--max-price", type=float, default=1.0)
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=900.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    parser.add_argument("--lease-cap", type=int, default=16)
    parser.add_argument("--min-smsbower", type=float, default=4.0)
    parser.add_argument("--skip-t5", action="store_true")
    parser.add_argument("--variants", default="T0,T1,T2,T3,T4")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    client = ApiClient(args.base, args.username, password)
    snapshot = client.get_config()
    vault_meta = load_vault_api4_meta()
    specs = _variant_specs()
    wanted = [tok.strip() for tok in args.variants.split(",") if tok.strip()]
    if not args.skip_t5 and "T5a" not in wanted:
        wanted.extend(["T5a", "T5b"])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"api4_detail_ab_{stamp}.json"

    balances_before = snapshot_balances(client)
    bower = smsbower_balance(balances_before)
    stock = country_stock(client, args.sms_provider, ["in", "iq"])
    print(
        f"smsbower balance={bower} min={args.min_smsbower} stock={stock} "
        f"vault_samples={len(vault_meta)} lease_cap={args.lease_cap}",
        flush=True,
    )

    report: Dict[str, Any] = {
        "started_at": utc_now(),
        "cli": {
            "sms_provider": args.sms_provider,
            "max_price": args.max_price,
            "lease_cap": args.lease_cap,
            "variants": wanted,
        },
        "vault_fingerprint_samples": [
            {k: v for k, v in row.items() if k not in {"app_hash"}}
            for row in vault_meta
        ],
        "balances_before": balances_before,
        "stock": stock,
        "stopped_need_topup": False,
        "rounds": [],
        "comparison_table": [],
        "hypothesis_table": [],
    }

    if bower is None:
        print("ERROR: 无法读取 smsbower 余额，停止。", flush=True)
        report["error"] = "smsbower_balance_unreadable"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2
    if bower < args.min_smsbower:
        print(
            f"STOP: smsbower 余额 {bower} < 最低 {args.min_smsbower}，请充值后再测。",
            flush=True,
        )
        report["stopped_need_topup"] = True
        report["need_topup_amount"] = round(args.min_smsbower - bower + 5, 2)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 3
    if args.check_only:
        print("check-only: 余额充足，未租号。", flush=True)
        report["check_only"] = True
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    leased_total = 0
    exit_code = 0
    try:
        for exp_id in wanted:
            spec = specs.get(exp_id)
            if not spec:
                print(f"skip unknown variant {exp_id}", flush=True)
                continue
            planned = int(spec["count"]) * int(spec["attempts"])
            if leased_total + planned > args.lease_cap:
                print(
                    f"skip {exp_id}: 计划租号 {planned} 会超过 cap {args.lease_cap} "
                    f"(已租 {leased_total})",
                    flush=True,
                )
                report.setdefault("skipped", []).append(
                    {"id": exp_id, "reason": "lease_cap", "planned": planned}
                )
                continue
            if spec.get("optional") and args.skip_t5:
                continue
            live_bal = smsbower_balance(snapshot_balances(client))
            if live_bal is not None and live_bal < args.min_smsbower:
                print(f"STOP mid-run: smsbower {live_bal} < {args.min_smsbower}", flush=True)
                report["stopped_need_topup"] = True
                break
            result = run_variant(
                client,
                exp_id=exp_id,
                spec=spec,
                snapshot=snapshot,
                sms_provider=args.sms_provider,
                max_price=args.max_price,
                proxy_mode=args.proxy_mode,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
            )
            report["rounds"].append(result)
            leased = int((result.get("analysis") or {}).get("leased_numbers") or 0)
            leased_total += leased
            print(f"    cumulative leased={leased_total}/{args.lease_cap}", flush=True)
    except Exception as exc:
        exit_code = 1
        report["error"] = str(exc)[:400]
        print(f"ERROR: {exc}", flush=True)
    finally:
        try:
            client.put_config(snapshot)
            print("config snapshot restored", flush=True)
        except Exception as exc:
            print(f"WARNING: restore config failed: {exc}", flush=True)
            report["restore_error"] = str(exc)[:200]

    report["finished_at"] = utc_now()
    report["leased_total"] = leased_total
    report["balances_after"] = snapshot_balances(client)
    report["comparison_table"] = build_comparison_table(report["rounds"])
    report["hypothesis_table"] = hypothesis_table(report["rounds"])
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport -> {report_path}", flush=True)
    for row in report["hypothesis_table"]:
        print(
            f"  {row['id']:<4} {row['country']:<3} leased={row['leased']} "
            f"send={row['sendcode']} types={row['sent_code_types']} "
            f"FLOOD={row['flood']} SMS={row['sms_received']} ok={row['success']}",
            flush=True,
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
