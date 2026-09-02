#!/usr/bin/env python3
"""第二轮注册冲刺：对照实验 + 多国猎号 + 证据链采集。

产出 JSON（data/ab_reports/）供报告引用。不打印 API Key。
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
from typing import Any, Dict, List, Optional, Tuple

# 复用 A/B 脚本的鉴权客户端与批次等待。
from backend.scripts.run_code_delivery_ab import (  # noqa: E402
    ApiClient,
    TERMINAL_STATUSES,
    bucket_for,
    classify_no_sendcode,
    mask_phone,
    utc_now,
    wait_batch,
)

DELIVERY_RE = re.compile(
    r"分发通道类型:\s*(?P<name>\S+)\s*\(type=(?P<type>\S+)\s+next_type=(?P<next>\S+)\s+timeout=(?P<timeout>\S+)\)"
)
RESEND_RE = re.compile(r"auth\.resendCode 已返回，新分发通道类型:\s*(?P<name>\S+)")
PLAN_RE = re.compile(
    r"\[验证码通道\]\s*通道策略=(?P<mode>\w+)，申请Push=(?P<req>\S)，"
    r"attach_token=(?P<attach>\S)，allow_app_hash=(?P<hash>\S)"
)
EGRESS_IP_RE = re.compile(r"出口拓扑(?:对齐)?: IP=(?P<ip>\S+)")
EGRESS_CC_RE = re.compile(r"出口拓扑(?:对齐)?: IP=\S+\s+国家=(?P<cc>\S+)")
MATCH_CUSTOM_RE = re.compile(r"\[自建代理池\] 成功匹配 (?P<cc>\w+) 注册通道")
MATCH_AUTO_RE = re.compile(r"自动匹配到 (?P<cc>\w+)\s+区域代理")
FALLBACK_RE = re.compile(r"使用静态后备中继|暂无可用区域代理")
API_ID_RE = re.compile(r"api_id=(\d+)")
CRED_CUSTOM_RE = re.compile(r"强制使用自建开发者凭证")
DEVICE_RE = re.compile(r"绑定硬件特征:\s*(?P<dev>.+)")
PRECHECK_OK_RE = re.compile(r"预检.*白号|ResolvePhone.*未注册|号码预检通过|预检判定该号未注册")
PRECHECK_REG_RE = re.compile(r"PRECHECK_PHONE_ALREADY_REGISTERED|预检判定该号已注册")
MTPROTO_OK_RE = re.compile(r"已完成 MTProto 传输层")
MTPROTO_CONNECT_RE = re.compile(r"建立 MTProto 协议传输通道")
CONNECT_TIMEOUT_RE = re.compile(r"CONNECT_TIMEOUT|MTProto 连接超时")
FLOOD_PUB_RE = re.compile(r"API_ID_PUBLISHED_FLOOD")
FLOOD_WAIT_RE = re.compile(r"FLOOD_WAIT")
LEASE_RE = re.compile(r"成功获取端点通信句柄")
SMS_CODE_RE = re.compile(r"STATUS_OK|收到验证码|验证码[:：]\s*\d{4,6}|接码成功")
SKIP_PUSH_RE = re.compile(r"跳过 Push Token 申请")
PROVIDER_IDS_RE = re.compile(r"providerIds=(?P<ids>\S+)")


def snapshot_balances(client: ApiClient) -> Dict[str, Any]:
    out: Dict[str, Any] = {"at": utc_now()}
    for path, key in (
        ("/api/test/fivesim", "fivesim"),
        ("/api/test/grizzlysms", "grizzlysms"),
        ("/api/test/smsbower", "smsbower"),
    ):
        try:
            data = client.request("POST", path, {"country": "ma"})
            payload = data.get("data") or {}
            out[key] = {
                "success": data.get("success"),
                "balance": payload.get("balance"),
                "currency": payload.get("currency") or payload.get("balance_currency"),
                "message": (data.get("message") or "")[:180],
            }
        except Exception as exc:
            out[key] = {"success": False, "error": str(exc)[:200]}
    return out


def parse_task_evidence(task: Dict[str, Any], phone_country: str) -> Dict[str, Any]:
    logs: List[str] = list(task.get("logs") or [])
    blob = "\n".join(logs)
    plans = [m.groupdict() for m in PLAN_RE.finditer(blob)]
    samples = [
        {
            "sent_code_type": m.group("type"),
            "next_type": m.group("next"),
            "timeout": m.group("timeout"),
            "bucket": bucket_for(m.group("type")),
        }
        for m in DELIVERY_RE.finditer(blob)
    ]
    proxy_ccs = [m.group("cc") for m in EGRESS_CC_RE.finditer(blob)]
    match_ccs = [m.group("cc") for m in MATCH_CUSTOM_RE.finditer(blob)] + [
        m.group("cc") for m in MATCH_AUTO_RE.finditer(blob)
    ]
    proxy_cc = (proxy_ccs or match_ccs or [None])[0]
    if proxy_cc:
        proxy_cc = str(proxy_cc).upper().rstrip(",")
    phone_cc = (phone_country or "").upper()
    geo_1to1 = None
    if proxy_cc and phone_cc and proxy_cc.isalpha():
        geo_1to1 = proxy_cc == phone_cc

    api_ids = API_ID_RE.findall(blob)
    return {
        "task_id": task.get("task_id") or task.get("id"),
        "status": task.get("status"),
        "phone": mask_phone(task.get("phone")),
        "leased_numbers": blob.count("成功获取端点通信句柄"),
        "blacklist_skips": blob.count("[号码黑名单拦截]"),
        "samples": samples,
        "resend_types": [m.group("name") for m in RESEND_RE.finditer(blob)],
        "plan_modes": [p["mode"] for p in plans],
        "plan_attached_token": [p["attach"] for p in plans],
        "plan_allow_app_hash": [p.get("hash") for p in plans],
        "plan_request_push": [p["req"] for p in plans],
        "skipped_push": bool(SKIP_PUSH_RE.search(blob)),
        "api_ids": list(dict.fromkeys(api_ids)),
        "credential_custom": bool(CRED_CUSTOM_RE.search(blob)),
        "device": (DEVICE_RE.search(blob).group("dev") if DEVICE_RE.search(blob) else None),
        "precheck_ok": bool(PRECHECK_OK_RE.search(blob)),
        "precheck_registered": bool(PRECHECK_REG_RE.search(blob)),
        "mtproto_connect": bool(MTPROTO_CONNECT_RE.search(blob)),
        "mtproto_handshake": bool(MTPROTO_OK_RE.search(blob)),
        "connect_timeout": bool(CONNECT_TIMEOUT_RE.search(blob)),
        "api_id_published_flood": len(FLOOD_PUB_RE.findall(blob)),
        "flood_wait": bool(FLOOD_WAIT_RE.search(blob)),
        "sms_code_received": bool(SMS_CODE_RE.search(blob)),
        "proxy_country": proxy_cc,
        "phone_country": phone_cc,
        "geo_1to1": geo_1to1,
        "used_fallback_proxy": bool(FALLBACK_RE.search(blob)),
        "provider_ids": (PROVIDER_IDS_RE.search(blob).group("ids") if PROVIDER_IDS_RE.search(blob) else None),
        "egress_ip": (EGRESS_IP_RE.search(blob).group("ip") if EGRESS_IP_RE.search(blob) else None),
        "no_sendcode_reason": classify_no_sendcode(logs, task.get("error")) if not samples else None,
        "error": task.get("error") or task.get("message"),
        "log_excerpt": _excerpt(logs),
        "log_lines": len(logs),
    }


def _excerpt(logs: List[str], limit: int = 12) -> List[str]:
    keys = (
        "[验证码通道]",
        "分发通道类型",
        "API 凭证",
        "出口拓扑",
        "自动匹配",
        "成功匹配",
        "MTProto",
        "API_ID_PUBLISHED_FLOOD",
        "CONNECT_TIMEOUT",
        "预检",
        "STATUS_OK",
        "providerIds=",
        "绑定硬件特征",
        "跳过 Push",
        "SentCodeType",
    )
    picked: List[str] = []
    for line in logs:
        if any(k in line for k in keys):
            picked.append(line[:240])
        if len(picked) >= limit:
            break
    return picked


def summarize_evidence(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    samples = [s for r in rows for s in r["samples"]]
    buckets = Counter(s["bucket"] for s in samples)
    sms = buckets.get("sms", 0)
    app = buckets.get("app", 0)
    graded = len(samples)
    geo = [r["geo_1to1"] for r in rows if r.get("geo_1to1") is not None]
    return {
        "tasks": len(rows),
        "success": sum(1 for r in rows if str(r["status"]) == "success"),
        "leased_numbers": sum(r["leased_numbers"] for r in rows),
        "blacklist_skips": sum(r["blacklist_skips"] for r in rows),
        "sendcode_samples": graded,
        "sms": sms,
        "app": app,
        "other_type": buckets.get("other_type", 0),
        "sms_rate": round(sms / graded, 4) if graded else None,
        "sent_code_types": dict(Counter(s["sent_code_type"] for s in samples)),
        "next_type_none": sum(1 for s in samples if s["next_type"] == "None"),
        "app_next_type_none": sum(1 for s in samples if s["bucket"] == "app" and s["next_type"] == "None"),
        "plan_modes": dict(Counter(m for r in rows for m in r["plan_modes"])),
        "attached_token": dict(Counter(a for r in rows for a in r["plan_attached_token"])),
        "allow_app_hash": dict(Counter(a for r in rows for a in r["plan_allow_app_hash"] if a)),
        "skipped_push_tasks": sum(1 for r in rows if r.get("skipped_push")),
        "api_id_published_flood": sum(int(r.get("api_id_published_flood") or 0) for r in rows),
        "connect_timeout": sum(1 for r in rows if r.get("connect_timeout")),
        "mtproto_handshake": sum(1 for r in rows if r.get("mtproto_handshake")),
        "sms_code_received": sum(1 for r in rows if r.get("sms_code_received")),
        "geo_1to1_true": sum(1 for v in geo if v),
        "geo_1to1_false": sum(1 for v in geo if v is False),
        "fallback_proxy": sum(1 for r in rows if r.get("used_fallback_proxy")),
        "precheck_registered": sum(1 for r in rows if r.get("precheck_registered")),
        "api_ids": dict(Counter(i for r in rows for i in (r.get("api_ids") or []))),
        "devices": dict(Counter(r.get("device") or "-" for r in rows)),
        "statuses": dict(Counter(str(r["status"]) for r in rows)),
        "no_sendcode_reasons": dict(Counter(r["no_sendcode_reason"] for r in rows if r["no_sendcode_reason"])),
        "tasks_without_sendcode": sum(1 for r in rows if not r["samples"]),
        "task_ids": [r["task_id"] for r in rows],
    }


def run_batch(
    client: ApiClient,
    *,
    label: str,
    country: str,
    sms_provider: str,
    mode: Optional[str],
    count: int,
    concurrency: int,
    max_price: float,
    max_number_attempts: int,
    proxy_mode: str,
    provider_ids: Optional[List[str]],
    no_number_retries: int,
    poll: float,
    batch_timeout: float,
    app_type: str,
) -> Dict[str, Any]:
    applied = None
    if mode:
        applied = client.set_delivery_mode(mode)
        if applied != mode:
            raise RuntimeError(f"code_delivery_mode 未写入: 期望 {mode} 实际 {applied}")
    print(
        f"\n=== {label} === country={country} provider={sms_provider} "
        f"mode={applied or mode or '(unchanged)'} proxy={proxy_mode} "
        f"count={count} attempts={max_number_attempts} max_price={max_price} "
        f"sku={provider_ids or '-'} @ {utc_now()}",
        flush=True,
    )
    payload: Dict[str, Any] = {
        "country": country,
        "app_type": app_type,
        "count": count,
        "concurrency": concurrency,
        "sms_provider": sms_provider,
        "max_price": max_price,
        "max_number_attempts": max_number_attempts,
        "no_number_retries": no_number_retries,
        "proxy_mode": proxy_mode,
    }
    if provider_ids:
        payload["provider_ids"] = provider_ids
    started = time.time()
    batch = client.start_batch(**payload)
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)
    final_batch, tasks, timed_out = wait_batch(client, batch_id, poll, batch_timeout)
    rows = [
        parse_task_evidence(client.get_task(t.get("task_id") or t.get("id")), country)
        for t in tasks
    ]
    summary = summarize_evidence(rows)
    print(
        f"    -> SMS={summary['sms']} App={summary['app']} 发码={summary['sendcode_samples']} "
        f"成功={summary['success']} FLOOD_PUB={summary['api_id_published_flood']} "
        f"1:1={summary['geo_1to1_true']}/{summary['geo_1to1_false']} "
        f"耗时={int(time.time() - started)}s",
        flush=True,
    )
    return {
        "label": label,
        "country": country,
        "sms_provider": sms_provider,
        "mode": applied or mode,
        "proxy_mode": proxy_mode,
        "max_price": max_price,
        "count": count,
        "max_number_attempts": max_number_attempts,
        "provider_ids": provider_ids,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "summary": summary,
        "rows": rows,
    }


def default_plan() -> List[Dict[str, Any]]:
    """对照 2 组 + ≥15 国猎号。价格按账户币种：Grizzly/Bower=USD，FiveSim=RUB。"""
    hunts: List[Dict[str, Any]] = [
        {"country": "ro", "sms_provider": "grizzlysms", "max_price": 2.0, "proxy_mode": "auto"},
        {"country": "pl", "sms_provider": "grizzlysms", "max_price": 1.8, "proxy_mode": "auto"},
        {"country": "fr", "sms_provider": "grizzlysms", "max_price": 1.2, "proxy_mode": "auto"},
        {"country": "ua", "sms_provider": "grizzlysms", "max_price": 1.2, "proxy_mode": "auto"},
        {"country": "br", "sms_provider": "grizzlysms", "max_price": 0.7, "proxy_mode": "auto"},
        {"country": "mx", "sms_provider": "grizzlysms", "max_price": 1.0, "proxy_mode": "auto"},
        {"country": "pe", "sms_provider": "grizzlysms", "max_price": 0.9, "proxy_mode": "auto"},
        {"country": "id", "sms_provider": "grizzlysms", "max_price": 0.5, "proxy_mode": "auto"},
        {"country": "th", "sms_provider": "grizzlysms", "max_price": 1.0, "proxy_mode": "auto"},
        {"country": "kz", "sms_provider": "grizzlysms", "max_price": 1.0, "proxy_mode": "auto"},
        {"country": "ph", "sms_provider": "grizzlysms", "max_price": 0.5, "proxy_mode": "auto"},
        {"country": "in", "sms_provider": "smsbower", "max_price": 0.7, "proxy_mode": "auto"},
        {"country": "uz", "sms_provider": "grizzlysms", "max_price": 1.0, "proxy_mode": "auto"},
        {"country": "gh", "sms_provider": "grizzlysms", "max_price": 0.5, "proxy_mode": "auto"},
        {"country": "ke", "sms_provider": "grizzlysms", "max_price": 0.4, "proxy_mode": "auto"},
        {"country": "eg", "sms_provider": "fivesim", "max_price": 0.5, "proxy_mode": "auto"},
        {"country": "ng", "sms_provider": "grizzlysms", "max_price": 1.2, "proxy_mode": "auto"},
        {"country": "cm", "sms_provider": "smsbower", "max_price": 1.4, "proxy_mode": "auto"},
    ]
    return [
        {
            "label": "ctrl1_balanced_ma",
            "phase": "control1",
            "country": "ma",
            "sms_provider": "grizzlysms",
            "mode": "balanced",
            "count": 5,
            "concurrency": 3,
            "max_price": 0.5,
            "max_number_attempts": 3,
            "proxy_mode": "custom_pool",
        },
        {
            "label": "ctrl1_push_ma",
            "phase": "control1",
            "country": "ma",
            "sms_provider": "grizzlysms",
            "mode": "push_required",
            "count": 5,
            "concurrency": 3,
            "max_price": 0.5,
            "max_number_attempts": 3,
            "proxy_mode": "custom_pool",
        },
        {
            "label": "ctrl2_vn_untested",
            "phase": "control2",
            "country": "vn",
            "sms_provider": "smsbower",
            "mode": "balanced",
            "count": 5,
            "concurrency": 3,
            "max_price": 1.2,
            "max_number_attempts": 3,
            "proxy_mode": "auto",
        },
        {
            "label": "ctrl2_cl_sku",
            "phase": "control2",
            "country": "cl",
            "sms_provider": "smsbower",
            "mode": "balanced",
            "count": 5,
            "concurrency": 3,
            "max_price": 0.4,
            "max_number_attempts": 3,
            "proxy_mode": "auto",
            "provider_ids": ["2421"],
        },
        *[
            {
                "label": f"hunt_{item['country']}_{item['sms_provider']}",
                "phase": "hunt",
                "count": 2,
                "concurrency": 2,
                "max_number_attempts": 2,
                "mode": "balanced",
                **item,
            }
            for item in hunts
        ],
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--app-type", default="telegram_android")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=1500.0)
    parser.add_argument("--no-number-retries", type=int, default=3)
    parser.add_argument("--out-dir", default="data/ab_reports")
    parser.add_argument("--phases", default="control1,control2,hunt")
    parser.add_argument("--plan-json", default="")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original_config = client.get_config()
    original_mode = str(original_config.get("code_delivery_mode"))
    wanted_phases = {p.strip() for p in args.phases.split(",") if p.strip()}
    plan = json.loads(Path(args.plan_json).read_text()) if args.plan_json else default_plan()
    plan = [item for item in plan if item.get("phase") in wanted_phases]

    print(f"启动快照 code_delivery_mode={original_mode} api_id={original_config.get('custom_api_id')} "
          f"cred={original_config.get('api_credential_mode')} rounds={len(plan)}", flush=True)
    balances_before = snapshot_balances(client)
    print(f"余额前: {json.dumps(balances_before, ensure_ascii=False)}", flush=True)

    rounds: List[Dict[str, Any]] = []
    try:
        for item in plan:
            try:
                rounds.append(
                    run_batch(
                        client,
                        label=item["label"],
                        country=item["country"],
                        sms_provider=item["sms_provider"],
                        mode=item.get("mode"),
                        count=int(item.get("count") or 2),
                        concurrency=int(item.get("concurrency") or 2),
                        max_price=float(item["max_price"]),
                        max_number_attempts=int(item.get("max_number_attempts") or 3),
                        proxy_mode=item.get("proxy_mode") or "auto",
                        provider_ids=item.get("provider_ids"),
                        no_number_retries=args.no_number_retries,
                        poll=args.poll,
                        batch_timeout=args.batch_timeout,
                        app_type=args.app_type,
                    )
                )
            except Exception as exc:
                print(f"    !! {item.get('label')} 失败: {exc}", flush=True)
                rounds.append({
                    "label": item.get("label"),
                    "country": item.get("country"),
                    "sms_provider": item.get("sms_provider"),
                    "mode": item.get("mode"),
                    "error": str(exc)[:400],
                    "summary": {"sendcode_samples": 0, "sms": 0, "app": 0, "success": 0},
                    "rows": [],
                })
    finally:
        restored = client.put_config(original_config)
        print(f"\n已恢复 code_delivery_mode={restored}", flush=True)

    balances_after = snapshot_balances(client)
    print(f"余额后: {json.dumps(balances_after, ensure_ascii=False)}", flush=True)

    report = {
        "generated_at": utc_now(),
        "original_mode": original_mode,
        "api_credential_mode": original_config.get("api_credential_mode"),
        "custom_api_id": original_config.get("custom_api_id"),
        "balances_before": balances_before,
        "balances_after": balances_after,
        "rounds": rounds,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"registration_sprint_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(
        f"{'label':<28}{'cc':>4}{'prov':>12}{'mode':>14}"
        f"{'发码':>6}{'SMS':>5}{'App':>5}{'成功':>5}{'1:1':>6}"
    )
    for item in rounds:
        s = item.get("summary") or {}
        geo = f"{s.get('geo_1to1_true', 0)}/{s.get('geo_1to1_false', 0)}"
        print(
            f"{str(item.get('label') or '-'):<28}{str(item.get('country') or '-'):>4}"
            f"{str(item.get('sms_provider') or '-'):>12}{str(item.get('mode') or '-'):>14}"
            f"{s.get('sendcode_samples', 0):>6}{s.get('sms', 0):>5}{s.get('app', 0):>5}"
            f"{s.get('success', 0):>5}{geo:>6}"
        )
    print("=" * 96)
    print(f"报告: {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
