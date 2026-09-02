#!/usr/bin/env python3
"""俄语选国通过率：约 50 号，api_id=4 + Push，多国对照。

计划分配（合计 50，对照 ≤20%）::

    kz  12  优选（俄语圈第一推）
    ph  12  优选
    vn  10  优选（Soft Expert 低难度）
    id   8  优选（仅 api_id=4；api_id=6 已 100% Payment）
    in   6  历史 vault 成功对照
    kz T0 2 握手对照（不写 lang_pack/tz）

每国先探针 4 号；若 4/4 真实 API_ID_PUBLISHED_FLOOD 且 0 App/SMS，停该国剩余额度，
把名额匀给仍有信号的优选国。禁止 api_id=6 / Payment / 假收据。

余额安全线：smsbower ≥ 4 USD，Grizzly ≥ 5 USD。不够跑满 50 则跑能负担的最大 N。

用法::

    python3 backend/scripts/run_country_passrate_50.py --check-only
    python3 backend/scripts/run_country_passrate_50.py --lease-cap 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.scripts.run_api4_detail_ab import (  # noqa: E402
    T1_STACK,
    apply_experiment_config,
    enrich_handshake,
    smsbower_balance,
)
from backend.scripts.run_api4_followup_ab import (  # noqa: E402
    annotate_result,
    followup_table,
    t0_apply,
)
from backend.scripts.run_code_delivery_ab import ApiClient, utc_now, wait_batch  # noqa: E402
from backend.scripts.run_payment_bypass_ab import analyze_round, enrich_row  # noqa: E402
from backend.scripts.run_registration_sprint import (  # noqa: E402
    parse_task_evidence,
    snapshot_balances,
    summarize_evidence,
)
from backend.scripts.run_vault_mode_sprint import load_vault_api4_meta  # noqa: E402

BATCH_COUNT_MAX = 10
CANDIDATE_COUNTRIES = ["kz", "ph", "vn", "id", "in", "pk", "ua"]
PROVIDER_ORDER = ["smsbower", "grizzlysms"]
COUNTRY_TZ = {
    "kz": 18000,
    "ph": 28800,
    "vn": 25200,
    "id": 25200,
    "in": 19800,
    "pk": 18000,
    "ua": 7200,
}
PRICE_CAP = {
    "kz": 1.6,
    "ph": 1.4,
    "vn": 1.4,
    "id": 1.4,
    "in": 2.2,
    "pk": 1.4,
    "ua": 2.0,
}
PRICE_FLOOR = {
    "kz": 0.35,
    "ph": 0.30,
    "vn": 0.30,
    "id": 0.30,
    "in": 0.45,
    "pk": 0.30,
    "ua": 0.50,
}

# 计划额度。in + T0 = 8/50 = 16% ≤ 20%。
PLAN: List[Dict[str, Any]] = [
    {"id": "kz_main", "country": "kz", "target": 12, "role": "preferred", "stack": "t1"},
    {"id": "ph_main", "country": "ph", "target": 12, "role": "preferred", "stack": "t1"},
    {"id": "vn_main", "country": "vn", "target": 10, "role": "preferred", "stack": "t1"},
    {"id": "id_main", "country": "id", "target": 8, "role": "preferred", "stack": "t1"},
    {"id": "in_ctrl", "country": "in", "target": 6, "role": "historical_control", "stack": "t1"},
    {"id": "kz_t0", "country": "kz", "target": 2, "role": "handshake_control", "stack": "t0"},
]
PROBE_N = 4
FALLBACK_COUNTRIES = ["pk", "ua"]


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def provider_balance(balances: Dict[str, Any], provider: str) -> Optional[float]:
    if provider == "smsbower":
        return smsbower_balance(balances)
    block = balances.get(provider) or {}
    return _as_float(block.get("balance"))


def country_stock(client: ApiClient, provider: str, codes: List[str]) -> Dict[str, Any]:
    try:
        data = client.request(
            "GET",
            f"/api/sms/available-countries?provider={provider}&refresh=true",
        )
    except Exception as exc:
        return {"error": str(exc)[:240], "provider": provider}
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


def stock_price(stock: Dict[str, Any], country: str) -> Optional[float]:
    block = stock.get(country) or {}
    return _as_float(block.get("price"))


def stock_count(stock: Dict[str, Any], country: str) -> int:
    block = stock.get(country) or {}
    return _as_int(block.get("count"))


def bid_for(country: str, listed: Optional[float]) -> float:
    floor = PRICE_FLOOR.get(country, 0.35)
    cap = PRICE_CAP.get(country, 1.6)
    if listed is None:
        return min(cap, max(floor, 0.5))
    bumped = max(floor, round(listed * 1.15 + 0.05, 2), round(listed + 0.12, 2))
    return min(bumped, cap)


def t1_apply() -> Dict[str, Any]:
    return {
        **T1_STACK,
        "hunt_device_max_uses": 8,
        "hunt_proxy_max_uses": 8,
    }


def spec_for(arm: Dict[str, Any], count: int, wave: str) -> Dict[str, Any]:
    country = arm["country"]
    stack = arm["stack"]
    tz = COUNTRY_TZ.get(country)
    if stack == "t0":
        apply = t0_apply()
        expect = {"lang_pack_empty": True, "tz_written": False}
        label = f"{arm['id']}_{wave}_t0"
        desc = f"{country} T0 握手对照：api_id=4+Push，不写 lang_pack/tz"
    else:
        apply = t1_apply()
        expect = {"lang_pack": "android", "tz_written": True}
        if tz is not None:
            expect["tz"] = tz
        label = f"{arm['id']}_{wave}_t1"
        desc = f"{country} T1 主栈：vault+lang_pack=android+号国 tz+Push+emu=false"
    return {
        "hypothesis": arm["role"],
        "label": label,
        "description": desc,
        "country": country,
        "count": count,
        "attempts": 1,
        "apply": apply,
        "expect_init": expect,
        "role": arm["role"],
        "stack": stack,
        "arm_id": arm["id"],
        "wave": wave,
    }


def pick_provider(
    stocks: Dict[str, Dict[str, Any]],
    country: str,
    preferred: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """有库存且标价不超过出价上限的源里：优先 smsbower（失败常能 cancel 不扣），否则取最便宜。"""
    order = list(PROVIDER_ORDER)
    if preferred and preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    cap = PRICE_CAP.get(country, 1.6)
    scanned = []
    viable: List[Dict[str, Any]] = []
    for provider in order:
        stock = stocks.get(provider) or {}
        n = stock_count(stock, country)
        price = stock_price(stock, country)
        over_cap = bool(price is not None and price > cap + 0.01)
        row = {
            "provider": provider,
            "count": n,
            "price": price,
            "bid": bid_for(country, price) if n > 0 and not over_cap else None,
            "over_cap": over_cap,
        }
        scanned.append(row)
        if n > 0 and not over_cap:
            viable.append(row)
    if not viable:
        return None, {"chosen": None, "count": 0, "price": None, "all": scanned, "none_in_stock": True}
    smsbower_hit = next((v for v in viable if v["provider"] == "smsbower"), None)
    if preferred:
        pref_hit = next((v for v in viable if v["provider"] == preferred), None)
        chosen = pref_hit or min(viable, key=lambda v: (v.get("price") is None, v.get("price") or 99))
    elif smsbower_hit:
        chosen = smsbower_hit
    else:
        chosen = min(viable, key=lambda v: (v.get("price") is None, v.get("price") or 99, v["provider"]))
    return chosen["provider"], {
        "chosen": chosen["provider"],
        "count": chosen["count"],
        "price": chosen["price"],
        "bid": chosen["bid"] or bid_for(country, chosen["price"]),
        "all": scanned,
    }


def estimate_cost(plan: List[Dict[str, Any]], stocks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    total = 0.0
    missing = []
    for arm in plan:
        country = arm["country"]
        provider, info = pick_provider(stocks, country)
        bid = info.get("bid") or bid_for(country, None)
        n = int(arm["target"])
        if provider is None:
            missing.append(country)
            cost = None
        else:
            cost = round(bid * n, 2)
            total += cost
        rows.append({
            "id": arm["id"],
            "country": country,
            "target": n,
            "provider": provider,
            "listed": info.get("price"),
            "bid": bid,
            "stock": info.get("count") or 0,
            "est_cost": cost,
        })
    return {
        "rows": rows,
        "est_gross_if_all_charged": round(total, 2),
        "note": "失败号会 cancel；实际扣费通常远低于毛估",
        "missing_stock": missing,
    }


def country_signal(rounds: List[Dict[str, Any]], country: str, stack: str = "t1") -> Dict[str, int]:
    app = flood = sms = success = leased = 0
    for item in rounds:
        if item.get("country") != country or item.get("stack") != stack:
            continue
        leased += int((item.get("analysis") or {}).get("leased_numbers") or 0)
        app += int(item.get("app_count") or 0)
        flood += int(item.get("actual_flood_tasks") or 0)
        sms += int(item.get("sms_type_count") or 0)
        success += int(item.get("success_count") or 0)
    return {
        "leased": leased,
        "app": app,
        "flood": flood,
        "sms": sms,
        "success": success,
    }


def flood_dead(sig: Dict[str, int], *, min_leased: int = 3) -> bool:
    if sig["leased"] < min_leased:
        return False
    if sig["app"] or sig["sms"] or sig["success"]:
        return False
    return sig["flood"] >= min_leased and sig["flood"] >= sig["leased"] - 1


def dump_report(path: Path, report: Dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def min_ok(provider: str, live: Optional[float], floors: Dict[str, float]) -> bool:
    floor = floors.get(provider)
    if floor is None or live is None:
        return True
    return live >= floor


def run_wave(
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
    concurrency: int,
) -> Dict[str, Any]:
    # run_variant 把 concurrency 写死成 min(count, 2)；大样本改用局部覆盖。
    applied = apply_experiment_config(client, snapshot, spec)
    country = spec["country"]
    count = int(spec["count"])
    attempts = int(spec["attempts"])
    app_type = applied.get("active_app_type") or "telegram_android_public"
    conc = max(1, min(count, concurrency, BATCH_COUNT_MAX))
    print(
        f"\n=== [{exp_id}] {spec['label']} === {spec['description']}\n"
        f"    country={country} count={count} conc={conc} provider={sms_provider} "
        f"bid={max_price} app_type={app_type} @ {utc_now()}",
        flush=True,
    )
    started = time.time()
    batch = client.start_batch(
        country=country,
        app_type=app_type,
        count=count,
        concurrency=conc,
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
    result = {
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
        "max_price": max_price,
        "stack": spec.get("stack"),
        "role": spec.get("role"),
        "arm_id": spec.get("arm_id"),
        "wave": spec.get("wave"),
    }
    result = annotate_result(result)
    print(
        f"    -> 租号={result['analysis']['leased_numbers']} 发码={result['analysis']['sendcode_samples']} "
        f"App={result.get('app_count')} SMS类型={result.get('sms_type_count')} "
        f"FLOOD={result.get('actual_flood_tasks')} 收码={result.get('sms_received')} "
        f"success={result.get('success_count')} handshake_ok={handshake_ok} "
        f"耗时={result['elapsed_seconds']}s",
        flush=True,
    )
    return result


def aggregate_table(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for item in rounds:
        key = f"{item.get('country')}:{item.get('stack')}"
        slot = by_key.setdefault(key, {
            "country": item.get("country"),
            "stack": item.get("stack"),
            "role": item.get("role"),
            "leased": 0,
            "sendcode": 0,
            "app": 0,
            "sms": 0,
            "flood": 0,
            "success": 0,
            "sms_received": 0,
            "payment": 0,
            "nonumber": 0,
            "push_fail": 0,
            "providers": [],
        })
        a = item.get("analysis") or {}
        slot["leased"] += int(a.get("leased_numbers") or 0)
        slot["sendcode"] += int(a.get("sendcode_samples") or 0)
        slot["app"] += int(item.get("app_count") or 0)
        slot["sms"] += int(item.get("sms_type_count") or 0)
        slot["flood"] += int(item.get("actual_flood_tasks") or 0)
        slot["success"] += int(item.get("success_count") or 0)
        slot["sms_received"] += int(item.get("sms_received") or 0)
        slot["payment"] += int(a.get("payment_task_count") or 0)
        slot["nonumber"] += int(item.get("nonumber") or 0)
        slot["push_fail"] += int(item.get("push_fail") or 0)
        prov = item.get("sms_provider")
        if prov and prov not in slot["providers"]:
            slot["providers"].append(prov)
    return list(by_key.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=900.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    parser.add_argument("--lease-cap", type=int, default=50)
    parser.add_argument("--min-smsbower", type=float, default=4.0)
    parser.add_argument("--min-grizzly", type=float, default=5.0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--probe-n", type=int, default=PROBE_N)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--skip-t0", action="store_true")
    parser.add_argument(
        "--plan-override",
        default="",
        help="覆盖计划，逗号分隔 id:country:n:role:stack，如 pk_fb:pk:12:fallback:t1",
    )
    parser.add_argument(
        "--bid-floor",
        default="",
        help="覆盖最低出价，如 kz=1.2,in=1.2",
    )
    args = parser.parse_args()

    if args.bid_floor:
        for pair in args.bid_floor.split(","):
            if "=" not in pair:
                continue
            cc, val = pair.split("=", 1)
            PRICE_FLOOR[cc.strip().lower()] = float(val)

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    client = ApiClient(args.base, args.username, password)
    snapshot = client.get_config()
    vault_meta = load_vault_api4_meta()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"country_passrate_50_{stamp}.json"

    plan = [dict(arm) for arm in PLAN]
    if args.plan_override.strip():
        plan = []
        for part in args.plan_override.split(","):
            bits = [x.strip() for x in part.split(":")]
            if len(bits) != 5:
                raise SystemExit(f"plan-override 项格式应为 id:country:n:role:stack，收到 {part}")
            plan.append({
                "id": bits[0],
                "country": bits[1].lower(),
                "target": int(bits[2]),
                "role": bits[3],
                "stack": bits[4],
            })
    if args.skip_t0:
        plan = [arm for arm in plan if arm["stack"] != "t0"]

    balances_before = snapshot_balances(client)
    stocks = {
        provider: country_stock(client, provider, CANDIDATE_COUNTRIES)
        for provider in PROVIDER_ORDER
    }
    cost = estimate_cost(plan, stocks)
    bower = smsbower_balance(balances_before)
    grizzly = provider_balance(balances_before, "grizzlysms")
    floors = {"smsbower": args.min_smsbower, "grizzlysms": args.min_grizzly}

    print(
        f"smsbower={bower} min={args.min_smsbower} grizzly={grizzly} min={args.min_grizzly} "
        f"lease_cap={args.lease_cap} vault_samples={len(vault_meta)}",
        flush=True,
    )
    for row in cost["rows"]:
        print(
            f"  plan {row['id']:<8} {row['country']} N={row['target']} "
            f"provider={row['provider'] or '-'} stock={row['stock']} "
            f"listed={row['listed']} bid={row['bid']} est={row['est_cost']}",
            flush=True,
        )
    print(
        f"  est_gross={cost['est_gross_if_all_charged']} missing={cost['missing_stock']}",
        flush=True,
    )

    report: Dict[str, Any] = {
        "started_at": utc_now(),
        "title": "country passrate ~50（俄语选国，api_id=4 + Push）",
        "plan": plan,
        "cli": {
            "lease_cap": args.lease_cap,
            "min_smsbower": args.min_smsbower,
            "min_grizzly": args.min_grizzly,
            "concurrency": args.concurrency,
            "probe_n": args.probe_n,
        },
        "constraints": {
            "api_id": 4,
            "official_emulation": False,
            "no_payment": True,
            "no_api_id_6": True,
            "control_budget_pct_cap": 20,
        },
        "vault_fingerprint_samples": [
            {k: v for k, v in row.items() if k not in {"app_hash"}}
            for row in vault_meta
        ],
        "balances_before": balances_before,
        "stock": stocks,
        "cost_estimate": cost,
        "stopped_need_topup": False,
        "need_topup_amount": None,
        "rounds": [],
        "skipped": [],
        "reallocations": [],
    }

    if bower is None:
        print("ERROR: 无法读取 smsbower 余额，停止。", flush=True)
        report["error"] = "smsbower_balance_unreadable"
        dump_report(report_path, report)
        return 2

    spendable_bower = max(0.0, bower - args.min_smsbower)
    spendable_grizzly = max(0.0, (grizzly or 0.0) - args.min_grizzly)
    spendable = spendable_bower + spendable_grizzly
    est = cost["est_gross_if_all_charged"] or 0.0
    if spendable + 0.01 < min(est * 0.25, 8.0) and est > 0:
        # 连探针都未必跑完：先提示，但仍允许 check-only。
        report["need_topup_amount"] = round(max(0.0, est - spendable + 5.0), 2)
        print(
            f"WARNING: 可动用余额约 {spendable:.2f} USD，50 号毛估 {est:.2f}。 "
            f"若实际扣费接近标价，需充值约 {report['need_topup_amount']} USD。",
            flush=True,
        )

    if args.check_only:
        report["check_only"] = True
        report["finished_at"] = utc_now()
        dump_report(report_path, report)
        print(f"check-only: 未租号。report -> {report_path}", flush=True)
        return 0

    leased_total = 0
    remaining_by_arm = {arm["id"]: int(arm["target"]) for arm in plan}
    flood_stopped: List[str] = []
    exit_code = 0

    def remaining_cap() -> int:
        return max(0, args.lease_cap - leased_total)

    def live_balances() -> Dict[str, Any]:
        return snapshot_balances(client)

    def provider_ok(provider: str, bals: Dict[str, Any]) -> bool:
        live = provider_balance(bals, provider)
        if not min_ok(provider, live, floors):
            print(f"STOP provider {provider}: live={live} < floor={floors.get(provider)}", flush=True)
            return False
        return True

    def take(exp_id: str, arm: Dict[str, Any], n: int, wave: str) -> Optional[Dict[str, Any]]:
        nonlocal leased_total
        n = min(n, remaining_cap(), remaining_by_arm.get(arm["id"], 0), BATCH_COUNT_MAX)
        if n <= 0:
            return None
        bals = live_balances()
        stocks_now = {
            provider: country_stock(client, provider, [arm["country"]])
            for provider in PROVIDER_ORDER
        }
        report.setdefault("stock_live", []).append({"at": utc_now(), "country": arm["country"], "stock": stocks_now})
        provider, info = pick_provider(stocks_now, arm["country"])
        if provider is None:
            print(f"skip {exp_id}: {arm['country']} 无库存", flush=True)
            report["skipped"].append({"id": exp_id, "reason": "no_stock", "country": arm["country"]})
            remaining_by_arm[arm["id"]] = 0
            return None
        if not provider_ok(provider, bals):
            alt = "grizzlysms" if provider == "smsbower" else "smsbower"
            provider2, info2 = pick_provider(stocks_now, arm["country"], preferred=alt)
            if provider2 is None or not provider_ok(provider2, bals):
                report["stopped_need_topup"] = True
                live_b = provider_balance(bals, "smsbower")
                live_g = provider_balance(bals, "grizzlysms")
                need = 0.0
                if live_b is not None and live_b < args.min_smsbower:
                    need += args.min_smsbower - live_b + 8
                if live_g is not None and live_g < args.min_grizzly:
                    need += args.min_grizzly - live_g + 5
                report["need_topup_amount"] = round(max(need, 8.0), 2)
                return None
            provider, info = provider2, info2
        spec = spec_for(arm, n, wave)
        try:
            result = run_wave(
                client,
                exp_id=exp_id,
                spec=spec,
                snapshot=snapshot,
                sms_provider=provider,
                max_price=float(info.get("bid") or bid_for(arm["country"], info.get("price"))),
                proxy_mode=args.proxy_mode,
                poll=args.poll,
                batch_timeout=args.batch_timeout,
                concurrency=args.concurrency,
            )
        except Exception as exc:
            print(f"ERROR wave {exp_id}: {exc}", flush=True)
            report.setdefault("errors", []).append({"id": exp_id, "error": str(exc)[:400]})
            return None
        leased = int((result.get("analysis") or {}).get("leased_numbers") or 0)
        leased_total += leased
        remaining_by_arm[arm["id"]] = max(0, remaining_by_arm.get(arm["id"], 0) - n)
        report["rounds"].append(result)
        print(f"    cumulative leased={leased_total}/{args.lease_cap}", flush=True)
        dump_report(report_path, report)
        return result

    try:
        # 1) 探针：每优选/对照国 4 号（T0 整段一次跑完）
        for arm in plan:
            if remaining_cap() <= 0:
                break
            if arm["stack"] == "t0":
                take(f"{arm['id']}_all", arm, remaining_by_arm[arm["id"]], "ctrl")
                continue
            n = min(args.probe_n, remaining_by_arm[arm["id"]])
            take(f"{arm['id']}_probe", arm, n, "probe")
            sig = country_signal(report["rounds"], arm["country"], "t1")
            if flood_dead(sig) and arm["role"] == "preferred":
                leftover = remaining_by_arm[arm["id"]]
                remaining_by_arm[arm["id"]] = 0
                flood_stopped.append(arm["id"])
                report["skipped"].append({
                    "id": arm["id"],
                    "reason": "flood_dead_after_probe",
                    "country": arm["country"],
                    "signal": sig,
                    "leftover": leftover,
                })
                print(
                    f"STOP fill {arm['country']}: probe FLOOD-dead {sig} leftover={leftover}",
                    flush=True,
                )

        # 2) 把 FLOOD 国剩下的名额匀给仍有 App/SMS 的优选国
        leftover_pool = 0
        live_pref: List[str] = []
        for arm in plan:
            if arm["role"] != "preferred":
                continue
            sig = country_signal(report["rounds"], arm["country"], "t1")
            if arm["id"] in flood_stopped or flood_dead(sig):
                leftover_pool += remaining_by_arm.get(arm["id"], 0)
                remaining_by_arm[arm["id"]] = 0
            elif sig["app"] or sig["sms"] or sig["success"] or not flood_dead(sig):
                live_pref.append(arm["id"])
        if leftover_pool and live_pref:
            extra = leftover_pool // len(live_pref)
            rem = leftover_pool % len(live_pref)
            for i, arm_id in enumerate(live_pref):
                add = extra + (1 if i < rem else 0)
                remaining_by_arm[arm_id] = remaining_by_arm.get(arm_id, 0) + add
                report["reallocations"].append({"to": arm_id, "add": add})
                print(f"reallocate +{add} -> {arm_id}", flush=True)

        # 3) 若优选全死，尝试候补国 pk/ua（仍走 T1，计入剩余 cap）
        all_pref_dead = all(
            flood_dead(country_signal(report["rounds"], arm["country"], "t1"))
            for arm in plan
            if arm["role"] == "preferred"
        )
        if all_pref_dead and remaining_cap() > 0:
            print("所有优选国探针 FLOOD-dead，尝试候补 pk/ua，不再加码 iq/in。", flush=True)
            for fb in FALLBACK_COUNTRIES:
                if remaining_cap() <= 0:
                    break
                stocks_fb = {
                    p: country_stock(client, p, [fb]) for p in PROVIDER_ORDER
                }
                provider, info = pick_provider(stocks_fb, fb)
                if provider is None:
                    report["skipped"].append({"id": f"fb_{fb}", "reason": "no_stock", "country": fb})
                    continue
                arm = {
                    "id": f"fb_{fb}",
                    "country": fb,
                    "target": min(8, remaining_cap()),
                    "role": "fallback",
                    "stack": "t1",
                }
                remaining_by_arm[arm["id"]] = arm["target"]
                plan.append(arm)
                take(f"{arm['id']}_probe", arm, min(args.probe_n, arm["target"]), "probe")
                sig = country_signal(report["rounds"], fb, "t1")
                if flood_dead(sig):
                    remaining_by_arm[arm["id"]] = 0
                    report["skipped"].append({
                        "id": arm["id"],
                        "reason": "flood_dead_after_probe",
                        "country": fb,
                        "signal": sig,
                    })

        # 4) 填满仍活着的臂，直到 cap
        safety = 0
        while remaining_cap() > 0 and safety < 40:
            safety += 1
            progressed = False
            for arm in plan:
                left = remaining_by_arm.get(arm["id"], 0)
                if left <= 0 or remaining_cap() <= 0:
                    continue
                if arm["role"] == "preferred":
                    sig = country_signal(report["rounds"], arm["country"], "t1")
                    if flood_dead(sig):
                        remaining_by_arm[arm["id"]] = 0
                        continue
                n = min(6, left, remaining_cap())
                got = take(f"{arm['id']}_fill{safety}", arm, n, "fill")
                progressed = progressed or got is not None
                if report.get("stopped_need_topup"):
                    break
            if report.get("stopped_need_topup") or not progressed:
                break

        if leased_total < 45 and not report.get("stopped_need_topup"):
            short = 50 - leased_total
            print(
                f"NOTE: 实际租号 {leased_total} < 45。差额 {short} 来自 FLOOD 早停 / 无库存 / 余额安全线。",
                flush=True,
            )
            report["short_of_50"] = short
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
    report["hypothesis_table"] = followup_table(report["rounds"])
    report["country_table"] = aggregate_table(report["rounds"])
    report["remaining_by_arm"] = remaining_by_arm
    if leased_total < 50 and report.get("need_topup_amount") is None:
        # 余额还在安全线以上、只是样本没满：估算补满 50 的充值（若因 FLOOD 早停则不强迫充值）
        if report.get("stopped_need_topup"):
            pass
        elif any(s.get("reason") == "flood_dead_after_probe" for s in report.get("skipped") or []):
            report["need_topup_amount"] = 0
            report["need_topup_reason"] = "不是余额问题：优选国探针 FLOOD，不建议为同一窗口充值加码"
    dump_report(report_path, report)
    print(f"\nreport -> {report_path}", flush=True)
    print(f"leased_total={leased_total}", flush=True)
    for row in report["country_table"]:
        print(
            f"  {row['country']:<3} stack={row['stack']:<3} leased={row['leased']} "
            f"send={row['sendcode']} App={row['app']} SMS={row['sms']} "
            f"FLOOD={row['flood']} recv={row['sms_received']} ok={row['success']} "
            f"pay={row['payment']}",
            flush=True,
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
