#!/usr/bin/env python3
"""菲律宾 iOS：闪信/漏接 A/B，两组各 10 路分开跑。

off：关掉 allow_flashcall / allow_missed_call
grammers：两者打开（对齐已验证成功 payload）
unknown_number 两组都保持 false。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
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
from backend.scripts.run_ph_smscode_warp import BATCH_CAP, enrich  # noqa: E402

AFTER_EMAIL_RE = re.compile(
    r"EmailVerifiedLogin 完成，继续处理新 sent_code (\S+)"
    r"|已取得 Email 验证码[\s\S]{0,400}?分发通道类型:\s*(\S+)",
)
PAYMENT_RE = re.compile(r"SentCodePaymentRequired")
CALL_RE = re.compile(r"SentCodeTypeCall")
SMS_RE = re.compile(r"SentCodeTypeSms\b|SentCodeTypeFirebaseSms")
EMAIL_OK_RE = re.compile(r"已取得 Email 验证码")
BIND_PORT_RE = re.compile(r"1:1 绑定预分配出口 socks5://res\.proxy-seller\.com:(\d+)")
FLASH_RE = re.compile(r"unknown=(\S+)\s+flashcall=(\S+)\s+missed=(\S+)")


APPLY = {
    "attestation_provider_mode": "antisafety_primary",
    "proxy_require_country_match": True,
    "proxy_unique_ip_per_task": True,
    "use_proxy_seller_auto": True,
    "code_delivery_mode": "push_required",
    "api_credential_mode": "official",
    "active_app_type": "telegram_ios",
    "official_client_emulation": True,
    "ignore_published_flood_window": True,
    "email_provider_mode": "smsbower_primary",
    "email_smsbower_fallback_enabled": True,
    "device_alignment_mode": "loose",
}


def classify_after_email(blob: str) -> Dict[str, Any]:
    email_ok = bool(EMAIL_OK_RE.search(blob))
    after = None
    m = AFTER_EMAIL_RE.search(blob)
    if m:
        after = m.group(1) or m.group(2)
    if after is None and email_ok:
        if PAYMENT_RE.search(blob):
            after = "SentCodePaymentRequired"
        elif CALL_RE.search(blob):
            after = "SentCodeTypeCall"
        elif SMS_RE.search(blob):
            after = "SentCodeTypeSms"
    bucket = "no_email"
    if after:
        if "Payment" in after:
            bucket = "payment"
        elif "Call" in after:
            bucket = "call"
        elif "Sms" in after:
            bucket = "sms"
        else:
            bucket = after
    elif email_ok:
        bucket = "email_ok_unknown"
    flags = FLASH_RE.findall(blob)
    port = BIND_PORT_RE.search(blob)
    return {
        "email_verified": email_ok,
        "after_email_type": after,
        "after_email_bucket": bucket,
        "codesettings": flags[-1] if flags else None,
        "bound_port": int(port.group(1)) if port else None,
    }


def run_arm(client: ApiClient, args: argparse.Namespace, call_flags: str) -> Dict[str, Any]:
    patched = client.get_config()
    patched.update(APPLY)
    patched["ios_code_settings_call_flags"] = call_flags
    client.put_config(patched)
    saved = client.get_config()
    print(
        f"\n===== ARM {call_flags} app={saved.get('active_app_type')} "
        f"call_flags={saved.get('ios_code_settings_call_flags')} "
        f"unique_ip={saved.get('proxy_unique_ip_per_task')} @ {utc_now()}",
        flush=True,
    )

    class NS:
        country = "ph"
        app_type = "telegram_ios"
        count = args.count
        concurrency = args.concurrency
        sms_provider = "smscode"
        max_price = args.max_price
        max_number_attempts = args.max_number_attempts
        proxy_mode = "auto"
        poll = args.poll
        batch_timeout = args.batch_timeout

    report = run_round(client, "push_required", NS)
    detailed = []
    after_buckets: Counter[str] = Counter()
    for t in client.list_tasks(report["batch_id"]):
        full = client.get_task(t.get("task_id") or t.get("id"))
        row = enrich(parse_task(full), full)
        extra = classify_after_email("\n".join(full.get("logs") or []))
        row.update(extra)
        after_buckets[str(extra["after_email_bucket"])] += 1
        detailed.append(row)
    report["rows"] = detailed
    report["call_flags"] = call_flags
    report["after_email"] = dict(after_buckets)
    print(f"ARM {call_flags} after_email={dict(after_buckets)}", flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--user", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD") or "")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-price", type=float, default=0.5)
    parser.add_argument("--max-number-attempts", type=int, default=2)
    parser.add_argument("--poll", type=float, default=15.0)
    parser.add_argument("--batch-timeout", type=float, default=1800.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    parser.add_argument(
        "--arms",
        default="off,grammers",
        help="逗号分隔，默认先 off 再 grammers",
    )
    args = parser.parse_args()
    if args.count < 1 or args.count > BATCH_CAP:
        raise SystemExit(f"--count 必须在 1..{BATCH_CAP}")
    args.concurrency = max(1, min(args.concurrency, BATCH_CAP, args.count))
    arms = [a.strip() for a in str(args.arms).split(",") if a.strip()]
    if not arms:
        raise SystemExit("--arms 不能为空")

    client = ApiClient(args.base, args.user, args.password or None)
    snapshot = client.get_config()
    reports: List[Dict[str, Any]] = []
    try:
        for arm in arms:
            reports.append(run_arm(client, args, arm))
    finally:
        client.put_config(snapshot)
        print(f"restored delivery={client.get_config().get('code_delivery_mode')}", flush=True)

    out = {
        "hypothesis": (
            "iOS 关掉 flashcall/missed 后，邮箱后会回到 SMS，"
            "还是仍走 Call / 付款墙"
        ),
        "count_per_arm": args.count,
        "arms": [
            {
                "call_flags": r.get("call_flags"),
                "batch_id": r.get("batch_id"),
                "elapsed_seconds": r.get("elapsed_seconds"),
                "summary": r.get("summary"),
                "after_email": r.get("after_email"),
            }
            for r in reports
        ],
        "rows": {r["call_flags"]: r.get("rows") for r in reports},
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ph_ios_call_ab_{stamp}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}", flush=True)
    for arm in out["arms"]:
        print(
            f"RESULT {arm['call_flags']} batch={arm['batch_id']} "
            f"after_email={arm['after_email']} "
            f"success={(arm.get('summary') or {}).get('success')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
