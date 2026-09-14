#!/usr/bin/env python3
"""菲律宾 SMSCode 小批量：官方公开 api_id=4 + WARP hop 后看 SMS/App。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.run_code_delivery_ab import (  # noqa: E402
    ApiClient,
    parse_task,
    run_round,
    utc_now,
)

COUNTRY_RE = re.compile(r"国家=([A-Za-z]{2})")
ALIGN_RE = re.compile(r"出口拓扑对齐: IP=(\S+) 国家=([A-Za-z]{2}|-)")
ORIGIN_RE = re.compile(r"成功从 (.+?) 自动匹配到")


APPLY = {
    "attestation_provider_mode": "antisafety_primary",
    "proxy_require_country_match": True,
    "use_proxy_seller_auto": True,
    "code_delivery_mode": "balanced",
    "api_credential_mode": "official",
    "active_app_type": "telegram_android_public",
    "official_client_emulation": True,
    "ignore_published_flood_window": True,
}


def enrich(row: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    blob = "\n".join(task.get("logs") or [])
    align = ALIGN_RE.search(blob)
    origin = ORIGIN_RE.search(blob)
    countries = COUNTRY_RE.findall(blob)
    out = dict(row)
    out["egress_country"] = align.group(2) if align else (countries[-1] if countries else None)
    out["egress_ip"] = (align.group(1) if align else row.get("egress_ip"))
    out["proxy_origin"] = origin.group(1) if origin else None
    out["sentcode_app"] = any(s.get("bucket") == "app" for s in row.get("samples") or [])
    out["sentcode_sms"] = any(s.get("bucket") == "sms" for s in row.get("samples") or [])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--user", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD") or "")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-price", type=float, default=0.5)
    parser.add_argument("--max-number-attempts", type=int, default=3)
    parser.add_argument("--poll", type=float, default=15.0)
    parser.add_argument("--batch-timeout", type=float, default=1500.0)
    parser.add_argument("--app-type", default="telegram_android_public")
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    client = ApiClient(args.base, args.user, args.password or None)
    snapshot = client.get_config()
    patched = dict(snapshot)
    patched.update(APPLY)
    client.put_config(patched)
    saved = client.get_config()
    print(
        f"config attest={saved.get('attestation_provider_mode')} "
        f"cred={saved.get('api_credential_mode')} "
        f"app={saved.get('active_app_type')} "
        f"emu={saved.get('official_client_emulation')} "
        f"delivery={saved.get('code_delivery_mode')} @ {utc_now()}",
        flush=True,
    )
    try:
        # reuse run_round machinery via a tiny namespace
        class NS:
            country = "ph"
            app_type = args.app_type
            count = args.count
            concurrency = args.concurrency
            sms_provider = "smscode"
            max_price = args.max_price
            max_number_attempts = args.max_number_attempts
            proxy_mode = "auto"
            poll = args.poll
            batch_timeout = args.batch_timeout

        report = run_round(client, "balanced", NS)
        tasks = client.list_tasks(report["batch_id"])
        detailed = []
        for t in tasks:
            full = client.get_task(t.get("task_id") or t.get("id"))
            detailed.append(enrich(parse_task(full), full))
        report["rows"] = detailed
        report["hypothesis"] = {
            "claim": "私有 api_id 无法完成注册；改用公开 api_id=4 + Push 再测 PH",
            "warp_hop": True,
            "country": "ph",
            "sms_provider": "smscode",
            "api_id": 4,
            "app_type": "telegram_android_public",
            "api_credential_mode": "official",
            "push": "antisafety_primary",
            "recaptcha": "REGHelp RecaptchaMobile only",
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
    print(
        f"RESULT success={summary.get('success')} SMS={summary.get('sms')} "
        f"App={summary.get('app')} samples={summary.get('sendcode_samples')} "
        f"statuses={summary.get('statuses')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
