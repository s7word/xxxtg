#!/usr/bin/env python3
"""SMSCode 新一轮约 20 号注册测试（严格设备 + 强制 Push + 代理国匹配）。

选国依据：历史 passrate 中 App 信号最好的 vn/id/ph + SMSCode 有库存的 kz。
硬开：device_alignment_mode=strict、code_delivery_mode=push_required、
proxy_require_country_match、hunt_proxy_max_uses=1、app_delivery_fast_drop、
official_client_emulation=false。

用法::

    python3 backend/scripts/run_smscode_round_20.py --check-only
    python3 backend/scripts/run_smscode_round_20.py --lease-cap 20
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.run_code_delivery_ab import ApiClient, utc_now, wait_batch  # noqa: E402
from backend.scripts.run_registration_sprint import (  # noqa: E402
    parse_task_evidence,
    summarize_evidence,
)

INIT_RE = re.compile(
    r"InitConnection 指纹: lang_pack=(?P<lp>\S+) tz_offset=(?P<tz>\S+)"
)
LOCALE_RE = re.compile(r"网络语言拓扑:\s*(?P<lang>\S+),\s*时区偏置:\s*(?P<tz>\S+)")
DEVICE_RE = re.compile(r"绑定硬件特征:\s*(?P<dev>.+)")
PUSH_SLOT_RE = re.compile(r"push_slot=(?P<slot>\S+)")
TOKEN_KIND_RE = re.compile(r"token_kind=(?P<kind>\S+)")
ALIGN_RE = re.compile(r"设备对齐模式=(?P<mode>\S+)")
STRICT_REJECT_RE = re.compile(r"严格设备对齐拒绝发码|DEVICE_ALIGNMENT_REJECTED")
GEO_MISMATCH_RE = re.compile(r"异国|country.?match|geo.?mismatch|拒绝使用异国|禁止跨区 fallback 失败", re.I)
FLOOD_GATE_RE = re.compile(r"\[FLOOD窗\]")
# 旧分类器把任意含「代理」的日志（含「代理槽位」成功预分配）误判为 PROXY_UNAVAILABLE。
PROXY_TRUE_FAIL_RE = re.compile(r"没有可用于注册的节点|ProxyError|代理匹配失败|无可用.*代理")

PROVIDER = "smscode"
# 历史 R1/R2：vn App 率最高，其次 id/ph；kz 俄语圈 + SMSCode 有库存
PLAN: List[Dict[str, Any]] = [
    {"country": "vn", "target": 8, "max_price": 0.55, "tz": 25200, "role": "best_app_signal"},
    {"country": "id", "target": 6, "max_price": 0.25, "tz": 25200, "role": "stock_plus_app"},
    {"country": "ph", "target": 4, "max_price": 0.45, "tz": 28800, "role": "preferred_history"},
    {"country": "kz", "target": 2, "max_price": 0.55, "tz": 18000, "role": "ru_sphere_probe"},
]
STRICT_APPLY = {
    "sms_provider": PROVIDER,
    "device_alignment_mode": "strict",
    "strict_vault_device_alignment": True,
    "code_delivery_mode": "push_required",
    "proxy_require_country_match": True,
    "hunt_proxy_max_uses": 1,
    "hunt_device_max_uses": 1,
    "app_delivery_fast_drop": True,
    "official_client_emulation": False,
    "flood_rotate_push_token": True,
    "force_country_locale": True,
    "init_connection_set_lang_pack": True,
    "init_connection_set_tz_offset": True,
    "vault_fingerprint_replay": True,
    "pin_app_version_substr": "12.7.3",
    "api_credential_mode": "official",
    "active_app_type": "telegram_android_public",
    "force_skip_push_attach": False,
}
RESTORE_KEYS = list(STRICT_APPLY.keys())


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def enrich_row(row: Dict[str, Any], task: Dict[str, Any], expect_tz: Optional[int]) -> Dict[str, Any]:
    logs = list(task.get("logs") or [])
    blob = "\n".join(logs)
    init = INIT_RE.search(blob)
    locale = LOCALE_RE.search(blob)
    lang_pack = init.group("lp") if init else None
    tz_raw = init.group("tz") if init else None
    tz_written = bool(tz_raw) and tz_raw not in {"未写入", "(empty)"}
    tz_val = None
    if tz_written:
        try:
            tz_val = int(tz_raw)
        except (TypeError, ValueError):
            tz_val = None
    issues: List[str] = []
    if STRICT_REJECT_RE.search(blob):
        issues.append("strict_reject")
    if row.get("api_id_published_flood") or "API_ID_PUBLISHED_FLOOD" in blob:
        issues.append("API_ID_PUBLISHED_FLOOD")
    if FLOOD_GATE_RE.search(blob) and "成功获取端点通信句柄" not in blob:
        issues.append("flood_gate_no_lease")
    if row.get("geo_1to1") is False:
        issues.append("proxy_geo_mismatch")
    if lang_pack not in {None, "android"} and lang_pack not in {"(empty)", ""}:
        issues.append(f"lang_pack={lang_pack}")
    if expect_tz is not None and tz_val is not None and tz_val != expect_tz:
        issues.append(f"tz_mismatch:{tz_val}!={expect_tz}")
    if any("SentCodeTypeApp" in str(s.get("sent_code_type")) for s in row.get("samples") or []):
        issues.append("sentcode_app")
    if PROXY_TRUE_FAIL_RE.search(blob):
        issues.append("proxy_unavailable_true")
    nosend = row.get("no_sendcode_reason")
    if nosend == "PROXY_UNAVAILABLE" and "proxy_unavailable_true" not in issues:
        # 分类器误伤：日志里常见「代理槽位」成功行
        nosend = "MISCLASSIFIED_PROXY_UNAVAILABLE"
    out = dict(row)
    out.update(
        {
            "init_lang_pack": None if lang_pack in {None, "(empty)", ""} else lang_pack,
            "init_tz_offset": tz_val,
            "init_tz_written": tz_written,
            "profile_lang": locale.group("lang") if locale else None,
            "profile_tz": locale.group("tz") if locale else None,
            "device": (DEVICE_RE.search(blob).group("dev")[:80] if DEVICE_RE.search(blob) else None),
            "push_slot": (PUSH_SLOT_RE.search(blob).group("slot") if PUSH_SLOT_RE.search(blob) else None),
            "token_kind": (TOKEN_KIND_RE.search(blob).group("kind") if TOKEN_KIND_RE.search(blob) else None),
            "align_mode": (ALIGN_RE.search(blob).group("mode") if ALIGN_RE.search(blob) else None),
            "error": (task.get("error") or task.get("last_error") or "")[:240] or None,
            "issues": issues,
            "expect_tz": expect_tz,
            "no_sendcode_reason": nosend,
        }
    )
    return out


def smscode_balance(client: ApiClient) -> Dict[str, Any]:
    try:
        data = client.request("POST", "/api/test/smscode", {"country": "vn"})
        payload = data.get("data") or {}
        return {
            "success": data.get("success"),
            "balance": payload.get("balance"),
            "currency": payload.get("currency") or "USD",
            "telegram_stock": payload.get("telegram_stock"),
            "message": (data.get("message") or "")[:180],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)[:200]}


def country_stock(client: ApiClient, codes: List[str]) -> Dict[str, Any]:
    try:
        data = client.request(
            "GET",
            f"/api/sms/available-countries?provider={PROVIDER}&refresh=true",
        )
    except Exception as exc:
        return {"error": str(exc)[:240]}
    items = data.get("countries") or data.get("items") or []
    wanted = {c.lower() for c in codes}
    out: Dict[str, Any] = {}
    for item in items:
        code = str(item.get("code") or item.get("country") or "").lower()
        if code in wanted:
            out[code] = {
                "stock": item.get("stock") or item.get("count"),
                "cost": item.get("cost") or item.get("price"),
                "name": item.get("name_zh") or item.get("name"),
                "tz_offset": item.get("tz_offset"),
            }
    return out


def apply_config(client: ApiClient, snapshot: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(snapshot)
    cfg.update(patch)
    saved = client.request("POST", "/api/config", cfg)
    return {k: saved.get(k) for k in patch}


def restore_config(client: ApiClient, snapshot: Dict[str, Any]) -> None:
    cfg = client.get_config()
    for key in RESTORE_KEYS:
        if key in snapshot:
            cfg[key] = snapshot[key]
    client.request("POST", "/api/config", cfg)


def run_wave(
    client: ApiClient,
    *,
    country: str,
    count: int,
    max_price: float,
    expect_tz: Optional[int],
    role: str,
    wave: str,
    poll: float,
    batch_timeout: float,
) -> Dict[str, Any]:
    label = f"{country}_{wave}_{count}"
    print(
        f"\n=== {label} role={role} price<={max_price} @ {utc_now()} ===",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type="telegram_android_public",
        count=count,
        concurrency=min(2, count),
        sms_provider=PROVIDER,
        max_price=max_price,
        max_number_attempts=1,
        no_number_retries=3,
        proxy_mode="auto",
    )
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)
    final_batch, tasks, timed_out = wait_batch(client, batch_id, poll, batch_timeout)
    rows = []
    for t in tasks:
        tid = t.get("task_id") or t.get("id")
        full = client.get_task(tid) if tid else t
        base = parse_task_evidence(full, country)
        rows.append(enrich_row(base, full, expect_tz))
    summary = summarize_evidence(rows)
    flood = sum(1 for r in rows if r.get("api_id_published_flood"))
    print(
        f"    -> leased~{summary.get('leased_numbers')} send={summary.get('sendcode_samples')} "
        f"App={summary.get('app')} SMS={summary.get('sms')} ok={summary.get('success')} "
        f"FLOOD={flood} 1:1={summary.get('geo_1to1_true')}/{summary.get('geo_1to1_false')} "
        f"{int(time.time()-started)}s",
        flush=True,
    )
    return {
        "label": label,
        "country": country,
        "role": role,
        "wave": wave,
        "count": count,
        "max_price": max_price,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "summary": summary,
        "flood_tasks": flood,
        "rows": rows,
    }


def collect_issues(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for rnd in rounds:
        for row in rnd.get("rows") or []:
            for issue in row.get("issues") or []:
                issues.append(
                    {
                        "country": rnd.get("country"),
                        "task_id": row.get("task_id"),
                        "phone": row.get("phone"),
                        "issue": issue,
                        "proxy_cc": row.get("proxy_country"),
                        "api_ids": row.get("api_ids"),
                        "init_lang_pack": row.get("init_lang_pack"),
                        "init_tz_offset": row.get("init_tz_offset"),
                        "samples": row.get("samples"),
                        "status": row.get("status"),
                        "error": row.get("error"),
                    }
                )
            if not row.get("samples") and row.get("no_sendcode_reason"):
                issues.append(
                    {
                        "country": rnd.get("country"),
                        "task_id": row.get("task_id"),
                        "phone": row.get("phone"),
                        "issue": f"no_sendcode:{row.get('no_sendcode_reason')}",
                        "proxy_cc": row.get("proxy_country"),
                        "status": row.get("status"),
                        "error": row.get("error"),
                    }
                )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="SMSCode round ~20 registration probe")
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--lease-cap", type=int, default=20)
    parser.add_argument("--wave-size", type=int, default=2)
    parser.add_argument("--poll", type=float, default=4.0)
    parser.add_argument("--batch-timeout", type=float, default=420.0)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--keep-strict", action="store_true", help="测完不恢复 loose（默认恢复）")
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    pw_path = Path(args.password_file)
    if not pw_path.is_absolute():
        pw_path = REPO_ROOT / pw_path
    password = pw_path.read_text(encoding="utf-8").strip() if pw_path.exists() else os.environ.get("EDGENODE_AUTH_PASSWORD")
    if not password:
        print("missing console password", file=sys.stderr)
        return 2

    client = ApiClient(args.base, args.username, password)
    snapshot = client.get_config()
    bal_before = smscode_balance(client)
    codes = [p["country"] for p in PLAN]
    stock = country_stock(client, codes)
    print("SMSCode balance:", bal_before, flush=True)
    print("stock:", json.dumps(stock, ensure_ascii=False), flush=True)

    if args.check_only:
        print("check-only OK", flush=True)
        return 0

    lease_cap = max(1, int(args.lease_cap))
    wave_size = max(1, int(args.wave_size))
    applied = apply_config(client, snapshot, STRICT_APPLY)
    print("applied:", json.dumps(applied, ensure_ascii=False), flush=True)

    report: Dict[str, Any] = {
        "title": "SMSCode round ~20（strict device + push_required + geo match）",
        "started_at": utc_now(),
        "provider": PROVIDER,
        "plan": PLAN,
        "lease_cap": lease_cap,
        "strict_apply": STRICT_APPLY,
        "applied": applied,
        "balances_before": bal_before,
        "stock": stock,
        "rounds": [],
        "skipped": [],
    }

    leased_budget = 0
    try:
        for arm in PLAN:
            if leased_budget >= lease_cap:
                break
            country = arm["country"]
            remain = min(int(arm["target"]), lease_cap - leased_budget)
            st = stock.get(country) or {}
            if _as_int(st.get("stock")) <= 0 and "error" not in stock:
                report["skipped"].append({"country": country, "reason": "no_stock", "stock": st})
                print(f"SKIP {country}: no stock", flush=True)
                continue
            # 先探针 2；若全 FLOOD 且 0 App/SMS，停该国
            while remain > 0 and leased_budget < lease_cap:
                n = min(wave_size, remain, lease_cap - leased_budget)
                wave_name = f"w{len(report['rounds'])+1}"
                result = run_wave(
                    client,
                    country=country,
                    count=n,
                    max_price=float(arm["max_price"]),
                    expect_tz=arm.get("tz"),
                    role=arm["role"],
                    wave=wave_name,
                    poll=args.poll,
                    batch_timeout=args.batch_timeout,
                )
                report["rounds"].append(result)
                leased_budget += n
                remain -= n
                flood = int(result.get("flood_tasks") or 0)
                summary = result.get("summary") or {}
                app = int(summary.get("app") or 0)
                sms = int(summary.get("sms") or 0)
                if flood >= n and app == 0 and sms == 0 and n >= 2:
                    report["skipped"].append(
                        {
                            "country": country,
                            "reason": "flood_dead_after_wave",
                            "leftover": remain,
                            "flood": flood,
                        }
                    )
                    print(f"STOP {country}: wave FLOOD-dead leftover={remain}", flush=True)
                    # 短暂停一下，避免填满 FLOOD 窗口
                    time.sleep(8)
                    break
                time.sleep(3)
    finally:
        report["balances_after"] = smscode_balance(client)
        if not args.keep_strict:
            restore_config(client, snapshot)
            report["config_restored"] = True
            report["restored_sample"] = {
                k: client.get_config().get(k)
                for k in (
                    "device_alignment_mode",
                    "official_client_emulation",
                    "sms_provider",
                    "hunt_proxy_max_uses",
                )
            }
        else:
            report["config_restored"] = False

    report["finished_at"] = utc_now()
    report["issues"] = collect_issues(report["rounds"])
    all_rows = [r for rnd in report["rounds"] for r in (rnd.get("rows") or [])]
    report["totals"] = {
        "tasks": len(all_rows),
        "leased_budget": leased_budget,
        "sendcode": sum(len(r.get("samples") or []) for r in all_rows),
        "app": sum(1 for r in all_rows for s in (r.get("samples") or []) if s.get("bucket") == "app"),
        "sms": sum(1 for r in all_rows for s in (r.get("samples") or []) if s.get("bucket") == "sms"),
        "flood": sum(1 for r in all_rows if r.get("api_id_published_flood")),
        "success": sum(1 for r in all_rows if str(r.get("status") or "").lower() in {"success", "completed", "ok"}),
        "geo_1to1_true": sum(1 for r in all_rows if r.get("geo_1to1") is True),
        "geo_1to1_false": sum(1 for r in all_rows if r.get("geo_1to1") is False),
        "issue_counts": dict(Counter(i["issue"] for i in report["issues"])),
        "api_ids": dict(Counter(i for r in all_rows for i in (r.get("api_ids") or []))),
        "countries": dict(Counter(rnd.get("country") for rnd in report["rounds"] for _ in (rnd.get("rows") or []))),
    }

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"smscode_round_20_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nREPORT {path}", flush=True)
    print("TOTALS", json.dumps(report["totals"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
