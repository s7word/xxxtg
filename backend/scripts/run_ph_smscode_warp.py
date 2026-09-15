#!/usr/bin/env python3
"""菲律宾 SMSCode：官方 + push_required，按波次跑（批量接口上限 10 路）。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.run_code_delivery_ab import (  # noqa: E402
    ApiClient,
    parse_task,
    run_round,
    summarize,
    utc_now,
)

COUNTRY_RE = re.compile(r"国家=([A-Za-z]{2})")
ALIGN_RE = re.compile(r"出口拓扑对齐: IP=(\S+) 国家=(\S+)")
EGRESS_IP_RE = re.compile(r"(?:出口拓扑对齐: IP=|出口拓扑: IP=|egress_ip=)(?P<ip>[0-9a-fA-F:.]+)")
ORIGIN_RE = re.compile(r"成功从 (.+?) 自动匹配到")
API_ID_RE = re.compile(r"api_id=(\d+)")
PUSH_RE = re.compile(r"Push Token|attach_token=是|跳过 Push")
SLOT_PORT_RE = re.compile(r"res\.proxy-seller\.com:(\d+)")
UNKNOWN_RE = re.compile(r"unknown=([是否])")

# 控制台 /register/batch 的 count/concurrency 上限是 10。
BATCH_CAP = 10

APPLY = {
    "attestation_provider_mode": "antisafety_primary",
    "proxy_require_country_match": True,
    "proxy_unique_ip_per_task": True,
    "use_proxy_seller_auto": True,
    "code_delivery_mode": "push_required",
    "api_credential_mode": "official",
    "active_app_type": "telegram_android",
    "official_client_emulation": True,
    "ignore_published_flood_window": True,
    "email_provider_mode": "smsbower_primary",
    "email_smsbower_fallback_enabled": True,
    "device_alignment_mode": "loose",
}


def apply_patch_for(app_type: str) -> Dict[str, Any]:
    """iOS 成功对照走 REGHelp；Android Public 对齐同一套出口/握手，不先打 AntiSafety。"""
    patched = dict(APPLY)
    patched["active_app_type"] = app_type
    if app_type == "telegram_ios":
        patched["attestation_provider_mode"] = "reghelp_primary"
    elif app_type == "telegram_android_public":
        patched["attestation_provider_mode"] = "reghelp_primary"
        patched["force_country_locale"] = True
        patched["init_connection_set_lang_pack"] = True
        patched["init_connection_set_tz_offset"] = True
        patched["code_settings_unknown_number"] = False
        patched["hunt_device_max_uses"] = 1
        patched["pin_app_version_substr"] = "12.8.3"
    return patched


def enrich(row: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    blob = "\n".join(task.get("logs") or [])
    align = ALIGN_RE.search(blob)
    egress = EGRESS_IP_RE.search(blob)
    origin = ORIGIN_RE.search(blob)
    countries = COUNTRY_RE.findall(blob)
    out = dict(row)
    align_ip = align.group(1) if align and align.group(1) not in {"-", "None"} else None
    out["egress_country"] = align.group(2).rstrip(",") if align else (countries[-1] if countries else None)
    out["egress_ip"] = align_ip or (egress.group("ip") if egress else row.get("egress_ip"))
    out["proxy_origin"] = origin.group(1) if origin else None
    out["sentcode_app"] = any(s.get("bucket") == "app" for s in row.get("samples") or [])
    out["sentcode_sms"] = any(s.get("bucket") == "sms" for s in row.get("samples") or [])
    api_ids = API_ID_RE.findall(blob)
    out["api_ids"] = sorted({int(x) for x in api_ids}) if api_ids else []
    out["push_mentioned"] = bool(PUSH_RE.search(blob))
    unknowns = UNKNOWN_RE.findall(blob)
    out["unknown_number"] = unknowns[-1] if unknowns else None
    ports = [int(x) for x in SLOT_PORT_RE.findall(blob)]
    out["proxy_ports"] = sorted(set(ports))
    return out


def collect_wave(client: ApiClient, args: argparse.Namespace, wave_idx: int) -> Dict[str, Any]:
    class NS:
        country = args.country
        app_type = args.app_type
        count = args.count
        concurrency = args.concurrency
        sms_provider = "smscode"
        max_price = args.max_price
        max_number_attempts = args.max_number_attempts
        proxy_mode = "auto"
        poll = args.poll
        batch_timeout = args.batch_timeout

    print(f"\n##### wave {wave_idx}/{args.waves} count={args.count} concurrency={args.concurrency}", flush=True)
    report = run_round(client, "push_required", NS)
    tasks = client.list_tasks(report["batch_id"])
    detailed = []
    for t in tasks:
        full = client.get_task(t.get("task_id") or t.get("id"))
        detailed.append(enrich(parse_task(full), full))
    report["rows"] = detailed
    report["wave"] = wave_idx
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--user", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD") or "")
    parser.add_argument("--count", type=int, default=10, help="每波任务数，API 上限 10")
    parser.add_argument("--waves", type=int, default=1, help="波次数；30 路用 --waves 3 --count 10")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--max-price", type=float, default=0.5)
    parser.add_argument("--max-number-attempts", type=int, default=2)
    parser.add_argument("--poll", type=float, default=15.0)
    parser.add_argument("--batch-timeout", type=float, default=1800.0)
    parser.add_argument("--app-type", default="telegram_android")
    parser.add_argument("--country", default="ph")
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()
    if args.count < 1 or args.count > BATCH_CAP:
        raise SystemExit(f"--count 必须在 1..{BATCH_CAP}")
    if args.waves < 1:
        raise SystemExit("--waves 必须 >= 1")
    args.concurrency = max(1, min(args.concurrency, BATCH_CAP, args.count))

    client = ApiClient(args.base, args.user, args.password or None)
    snapshot = client.get_config()
    patched = dict(snapshot)
    patched.update(apply_patch_for(args.app_type))
    client.put_config(patched)
    saved = client.get_config()
    print(
        f"config attest={saved.get('attestation_provider_mode')} "
        f"cred={saved.get('api_credential_mode')} "
        f"app={saved.get('active_app_type')} "
        f"emu={saved.get('official_client_emulation')} "
        f"delivery={saved.get('code_delivery_mode')} "
        f"unique_ip={saved.get('proxy_unique_ip_per_task')} "
        f"email={saved.get('email_provider_mode')} @ {utc_now()}",
        flush=True,
    )
    waves: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    try:
        for idx in range(1, args.waves + 1):
            wave = collect_wave(client, args, idx)
            waves.append(wave)
            all_rows.extend(wave.get("rows") or [])
            summary = wave.get("summary") or {}
            print(
                f"WAVE {idx} success={summary.get('success')} SMS={summary.get('sms')} "
                f"App={summary.get('app')} statuses={summary.get('statuses')}",
                flush=True,
            )
        report: Dict[str, Any] = {
            "mode": "push_required",
            "waves": [
                {
                    "wave": w.get("wave"),
                    "batch_id": w.get("batch_id"),
                    "timed_out": w.get("timed_out"),
                    "elapsed_seconds": w.get("elapsed_seconds"),
                    "batch_status": w.get("batch_status"),
                    "summary": w.get("summary"),
                }
                for w in waves
            ],
            "summary": summarize(all_rows),
            "rows": all_rows,
            "hypothesis": {
                "claim": (
                    "官方 iOS api_id=8 / lang_pack=ios"
                    if args.app_type == "telegram_ios"
                    else "Android Public unknown_number=false + official emu"
                    if args.app_type == "telegram_android_public"
                    else "push_required + official emu"
                ),
                "follow_task": "90aa174f",
                "warp_hop": True,
                "country": args.country,
                "sms_provider": "smscode",
                "app_type": args.app_type,
                "api_credential_mode": "official",
                "official_client_emulation": True,
                "code_delivery_mode": "push_required",
                "email_provider_mode": "smsbower_primary",
                "waves": args.waves,
                "count_per_wave": args.count,
                "push": "REGHelp appDevice=iOS" if args.app_type == "telegram_ios" else "antisafety_primary then REGHelp",
                "email": "SMS Bower Google gmail.com",
                "proxy_unique_ip_per_task": True,
            },
        }
        ips = [r.get("egress_ip") for r in all_rows if r.get("egress_ip")]
        ports = [p for r in all_rows for p in (r.get("proxy_ports") or [])]
        report["unique_ips"] = {
            "egress_ips": sorted(set(ips)),
            "egress_ip_count": len(set(ips)),
            "proxy_ports": sorted(set(ports)),
            "proxy_port_count": len(set(ports)),
            "ip_reused": len(ips) != len(set(ips)),
            "port_reused": len(ports) != len(set(ports)),
        }
    finally:
        client.put_config(snapshot)
        restored = client.get_config()
        print(
            f"restored attest={restored.get('attestation_provider_mode')} "
            f"delivery={restored.get('code_delivery_mode')}",
            flush=True,
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ph_smscode_warp_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}", flush=True)
    summary = report.get("summary") or {}
    uniq = report.get("unique_ips") or {}
    print(
        f"RESULT success={summary.get('success')} SMS={summary.get('sms')} "
        f"App={summary.get('app')} samples={summary.get('sendcode_samples')} "
        f"statuses={summary.get('statuses')} "
        f"unique_ips={uniq.get('egress_ip_count')} "
        f"unique_ports={uniq.get('proxy_port_count')} "
        f"ip_reused={uniq.get('ip_reused')} port_reused={uniq.get('port_reused')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
