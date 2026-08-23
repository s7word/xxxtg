#!/usr/bin/env python3
"""Use India residential proxy + lod_user +91 session to apply my.telegram.org apps."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.account_vault import AccountVaultService  # noqa: E402
from backend.app.services.proxyseller import ProxySellerService, infer_country_from_phone  # noqa: E402
from backend.app.services.telegram_apps import (  # noqa: E402
    TelegramAppsHelper,
    TelegramAppsJobManager,
)


DEFAULT_PHONES = ("+918302332054", "+918296691905", "+918310013712")


async def _wait_job(job_id: str, timeout: float = 180.0) -> dict:
    manager = TelegramAppsJobManager.get_instance()
    deadline = time.time() + timeout
    last_len = 0
    while time.time() < deadline:
        job = manager.get_job(job_id)
        if not job:
            raise RuntimeError(f"job {job_id} disappeared")
        logs = job.get("logs") or []
        if len(logs) > last_len:
            for line in logs[last_len:]:
                print(line, flush=True)
            last_len = len(logs)
        status = job.get("status")
        if status in {"success", "failed"}:
            return job
        if status == "waiting_code" and job.get("needs_manual_code"):
            return job
        await asyncio.sleep(1.5)
    return manager.get_job(job_id) or {}


async def run(phones: list[str], apply_to_config: bool) -> dict:
    listing = AccountVaultService.list_accounts()
    by_phone = {acc.phone: acc for acc in listing.accounts}
    report = {"attempts": [], "success": False}

    svc = ProxySellerService("")
    try:
        selection = await svc.select_best_proxy(target_country="in", probe=True, allow_fallback=False, max_probes=3)
    finally:
        await svc.close()
    report["proxy_selection"] = {
        "success": selection.get("success"),
        "source": selection.get("source"),
        "message": selection.get("message"),
        "proxy": selection.get("proxy"),
    }
    print(json.dumps(report["proxy_selection"], ensure_ascii=False, indent=2), flush=True)

    for phone in phones:
        account = by_phone.get(phone)
        print(f"\n=== try {phone} inferred={infer_country_from_phone(phone)} "
              f"session={bool(account and account.has_session)} ===", flush=True)
        resp = await TelegramAppsHelper.start_job(
            account_id=account.account_id if account else None,
            phone=phone,
            auto_read_code=True,
            apply_to_config=apply_to_config,
            app_title="EdgeNode Auditor IN",
        )
        job = await _wait_job(resp.job_id)
        attempt = {
            "phone": phone,
            "job_id": resp.job_id,
            "status": job.get("status"),
            "error": job.get("error"),
            "api_id": job.get("api_id"),
            "api_hash": job.get("api_hash"),
            "created_new_app": job.get("created_new_app"),
            "applied_to_config": job.get("applied_to_config"),
            "needs_manual_code": job.get("needs_manual_code"),
            "proxy": job.get("proxy"),
            "logs": job.get("logs"),
        }
        report["attempts"].append(attempt)
        if job.get("status") == "success" and job.get("api_id") and job.get("api_hash"):
            report["success"] = True
            report["api_id"] = job.get("api_id")
            report["api_hash"] = job.get("api_hash")
            report["phone"] = phone
            break
        if job.get("status") == "failed" and "flood" in str(job.get("error") or "").lower():
            print("flood on this number, trying next", flush=True)
            continue
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phones", nargs="*", default=list(DEFAULT_PHONES))
    parser.add_argument("--no-apply", action="store_true")
    parser.add_argument("--out", default="/tmp/india_api_apply.json")
    args = parser.parse_args()
    report = asyncio.run(run(args.phones, apply_to_config=not args.no_apply))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "attempts"}, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    return 0 if report.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
