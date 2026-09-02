#!/usr/bin/env python3
"""俄语选国通过率第二轮：约 50 号，探针→信号加码 / FLOOD 则等待。

相对 R1 的硬改动::

    * 优选 ph/vn/id（kz 有货加入）≥70%；对照 in（无号则 iq）≤15%
    * 禁止 api_id=6 / Payment / 假收据 / 把预算倒进 pk
    * 每国先 4 号；出现 App/SMS 才加码该国（填满块 ≤4）
    * 开局全 FLOOD：等 30–60 分钟再探针，禁止 50 连射
    * 记录号段前缀 / 猜运营商 / sent_code / UTC 时间戳

用法::

    python3 backend/scripts/run_country_passrate_50_r2.py --check-only
    python3 backend/scripts/run_country_passrate_50_r2.py --lease-cap 50
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import backend.scripts.run_country_passrate_50 as r1  # noqa: E402
from backend.scripts.run_code_delivery_ab import utc_now  # noqa: E402
from backend.scripts.run_country_passrate_50 import (  # noqa: E402
    BATCH_COUNT_MAX,
    aggregate_table,
    bid_for,
    country_signal,
    country_stock,
    dump_report,
    estimate_cost,
    flood_dead,
    min_ok,
    pick_provider,
    provider_balance,
    run_wave,
    smsbower_balance,
    spec_for,
)
from backend.scripts.run_api4_followup_ab import followup_table  # noqa: E402
from backend.scripts.run_registration_sprint import (  # noqa: E402
    parse_task_evidence,
    snapshot_balances,
)
from backend.scripts.run_vault_mode_sprint import load_vault_api4_meta  # noqa: E402

DIGITS_RE = re.compile(r"\D")

# 预算：优选 16+14+12+8=50，对照另计但 cap 内只跑 4。实际 lease_cap=50 时
# kz 无货则 16+14+12+4=46，优选仍 ≥70%。
PLAN_R2: List[Dict[str, Any]] = [
    {"id": "ph_r2", "country": "ph", "target": 16, "role": "preferred", "stack": "t1"},
    {"id": "vn_r2", "country": "vn", "target": 14, "role": "preferred", "stack": "t1"},
    {"id": "id_r2", "country": "id", "target": 12, "role": "preferred", "stack": "t1"},
    {"id": "kz_r2", "country": "kz", "target": 8, "role": "preferred", "stack": "t1"},
    {"id": "in_r2", "country": "in", "target": 4, "role": "historical_control", "stack": "t1"},
    {"id": "iq_r2", "country": "iq", "target": 4, "role": "alt_control", "stack": "t1"},
]
CANDIDATES = ["kz", "ph", "vn", "id", "in", "iq"]


def _patch_r1_tables() -> None:
    r1.COUNTRY_TZ["iq"] = 10800
    r1.PRICE_CAP["iq"] = 1.4
    r1.PRICE_FLOOR["iq"] = 0.40
    r1.PRICE_CAP["kz"] = 1.8
    r1.CANDIDATE_COUNTRIES = list(CANDIDATES)


def _digits(phone: Optional[str]) -> str:
    """只取掩码星号前的号头，避免把尾号拼进前缀。"""
    raw = str(phone or "")
    if "****" in raw:
        raw = raw.split("****", 1)[0]
    return DIGITS_RE.sub("", raw)


def classify_phone(country: str, phone: Optional[str]) -> Dict[str, Any]:
    """从完整号或掩码号抽出国内前缀并猜运营商。不回传完整 MSISDN。"""
    cc = (country or "").lower()
    digits = _digits(phone)
    local = ""
    if cc == "ph" and digits.startswith("63"):
        local = "0" + digits[2:5]
    elif cc == "vn" and digits.startswith("84"):
        local = "0" + digits[2:4]
    elif cc == "id" and digits.startswith("62"):
        local = "0" + digits[2:5]
    elif cc == "kz" and digits.startswith("7"):
        local = digits[1:4]
    elif cc == "in" and digits.startswith("91"):
        local = digits[2:6]
    elif cc == "iq" and digits.startswith("964"):
        local = digits[3:6]
    elif digits:
        local = digits[:4]
    return {
        "phone_prefix": local or None,
        "operator_guess": guess_operator(cc, local),
        "e164_head": (digits[:6] if len(digits) >= 6 else digits) or None,
    }


def guess_operator(country: str, local: str) -> Optional[str]:
    if not local:
        return None
    if country == "ph":
        p3, p4 = local[:3], local[:4]
        if p4 in {"0895", "0896", "0897", "0898"} or p3 == "089":
            return "DITO"
        if p4 in {"0991", "0992", "0993", "0994"}:
            return "DITO"
        if p3 in {"097", "095", "093", "092", "091", "090"} and p4:
            globe = {
                "0915", "0916", "0917", "0926", "0927", "0935", "0936", "0937",
                "0945", "0953", "0954", "0955", "0956", "0965", "0966", "0967",
                "0975", "0976", "0977", "0978", "0979", "0995", "0996", "0997",
            }
            smart = {
                "0908", "0918", "0919", "0920", "0921", "0928", "0929", "0939",
                "0947", "0949", "0951", "0961", "0998", "0999",
            }
            if p4 in globe or p4 == "0976":
                return "Globe/TM/GOMO"
            if p4 in smart:
                return "Smart"
            if p3 == "097":
                return "Globe/TM/GOMO?"
            if p3 == "096":
                return "Globe/Smart/GOMO?"
            if p3 == "094":
                return "Globe/Smart/Sun?"
            if p3 == "099":
                return "DITO/Globe/Smart?"
        return "PH-unknown"
    if country == "vn":
        p2 = local[:3] if local.startswith("0") else local[:2]
        if local.startswith("0"):
            p2 = local[1:3]
        try:
            n = int(p2)
        except ValueError:
            return "VN-unknown"
        if 32 <= n <= 39 or n in {86, 96, 97, 98}:
            return "Viettel"
        if n in {81, 82, 83, 84, 85, 88, 91, 94}:
            return "Vinaphone"
        if n in {70, 76, 77, 78, 79, 89, 90, 93}:
            return "Mobifone"
        if n in {52, 56, 58, 92}:
            return "Vietnamobile"
        return "VN-unknown"
    if country == "id":
        p3 = local[:4] if local.startswith("0") else local[:3]
        if local.startswith("0"):
            head = local[1:4]
        else:
            head = local[:3]
        telkomsel = {"811", "812", "813", "821", "822", "823", "851", "852", "853", "854"}
        indosat = {"814", "815", "816", "855", "856", "857", "858", "894", "895", "896", "897", "898", "899"}
        xl = {"817", "818", "819", "859", "877", "878"}
        if head in telkomsel:
            return "Telkomsel"
        if head in indosat:
            return "Indosat"
        if head in xl:
            return "XL"
        if head.startswith("85"):
            return "Telkomsel-or-Indosat"
        if head.startswith("81"):
            return "Telkomsel/Indosat/XL?"
        return "ID-unknown"
    if country == "kz":
        table = {
            "700": "Altel", "708": "Altel",
            "701": "Kcell/Activ", "702": "Kcell/Activ", "775": "Kcell/Activ", "778": "Kcell/Activ",
            "705": "Beeline KZ", "771": "Beeline KZ", "776": "Beeline KZ", "777": "Beeline KZ",
            "707": "Tele2", "747": "Tele2",
            "706": "izi/Beeline",
        }
        return table.get(local[:3], "KZ-7xx")
    if country == "in":
        return "IN-pool"
    if country == "iq":
        return "IQ-pool"
    return None


def attach_prefixes(result: Dict[str, Any]) -> Dict[str, Any]:
    country = str(result.get("country") or "")
    prefixes: Dict[str, int] = {}
    guesses: Dict[str, int] = {}
    for row in result.get("rows") or []:
        if row.get("phone_prefix") and row.get("operator_guess"):
            meta = {
                "phone_prefix": row.get("phone_prefix"),
                "operator_guess": row.get("operator_guess"),
                "e164_head": row.get("e164_head"),
            }
        else:
            meta = classify_phone(country, row.get("phone"))
            row.update(meta)
        if meta.get("phone_prefix"):
            prefixes[meta["phone_prefix"]] = prefixes.get(meta["phone_prefix"], 0) + 1
        if meta.get("operator_guess"):
            guesses[meta["operator_guess"]] = guesses.get(meta["operator_guess"], 0) + 1
    result["prefix_counts"] = prefixes
    result["operator_guess_counts"] = guesses
    return result


_ORIG_PARSE = parse_task_evidence


def parse_task_evidence_prefixed(task: Dict[str, Any], phone_country: str) -> Dict[str, Any]:
    base = _ORIG_PARSE(task, phone_country)
    base.update(classify_phone(phone_country, task.get("phone")))
    return base


def sleep_with_log(seconds: float, why: str) -> None:
    seconds = max(0, int(seconds))
    if seconds <= 0:
        return
    print(f"WAIT {seconds}s ({seconds/60:.1f} min): {why} @ {utc_now()}", flush=True)
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            break
        chunk = min(60.0, left)
        time.sleep(chunk)
        if left - chunk > 1:
            print(f"    ... still waiting {int(left - chunk)}s", flush=True)
    print(f"WAIT done @ {utc_now()}", flush=True)


def wave_is_live(result: Optional[Dict[str, Any]]) -> bool:
    if not result:
        return False
    return bool(result.get("app_count") or result.get("sms_type_count") or result.get("success_count"))


def wave_is_flood(result: Optional[Dict[str, Any]]) -> bool:
    if not result:
        return False
    leased = int((result.get("analysis") or {}).get("leased_numbers") or 0)
    flood = int(result.get("actual_flood_tasks") or 0)
    if leased < 3:
        return False
    if wave_is_live(result):
        return False
    return flood >= leased - 1


def preferred_share(rounds: List[Dict[str, Any]]) -> Tuple[int, int, float]:
    pref = ctrl = 0
    for item in rounds:
        n = int((item.get("analysis") or {}).get("leased_numbers") or 0)
        if item.get("role") in {"historical_control", "alt_control", "handshake_control"}:
            ctrl += n
        else:
            pref += n
    total = pref + ctrl
    pct = (100.0 * pref / total) if total else 0.0
    return pref, ctrl, pct


def combine_prefix_table(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    acc: Dict[str, Dict[str, Any]] = {}
    for item in rounds:
        country = item.get("country")
        for row in item.get("rows") or []:
            prefix = row.get("phone_prefix") or "?"
            key = f"{country}:{prefix}"
            slot = acc.setdefault(key, {
                "country": country,
                "prefix": prefix,
                "operator_guess": row.get("operator_guess"),
                "leased_rows": 0,
                "app": 0,
                "sms": 0,
                "flood": 0,
                "success": 0,
                "providers": [],
            })
            slot["leased_rows"] += 1 if row.get("phone") else 0
            samples = row.get("samples") or []
            if any("App" in str(s.get("sent_code_type") or "") for s in samples):
                slot["app"] += 1
            if any("Sms" in str(s.get("sent_code_type") or "") for s in samples):
                slot["sms"] += 1
            if row.get("api_id_published_flood"):
                slot["flood"] += 1
            if str(row.get("status") or "").lower() in {"success", "completed"}:
                slot["success"] += 1
            prov = item.get("sms_provider")
            if prov and prov not in slot["providers"]:
                slot["providers"].append(prov)
    return list(acc.values())


def main() -> int:
    _patch_r1_tables()
    r1.parse_task_evidence = parse_task_evidence_prefixed

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
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--probe-n", type=int, default=4)
    parser.add_argument("--fill-chunk", type=int, default=4)
    parser.add_argument("--probe-gap-sec", type=float, default=45.0)
    parser.add_argument("--fill-gap-sec", type=float, default=90.0)
    parser.add_argument("--flood-wait-sec", type=float, default=2100.0,
                        help="开局全 FLOOD 后等待秒数（默认 35 分钟，范围建议 30–60）")
    parser.add_argument("--flood-retries", type=int, default=2)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--bid-floor", default="")
    args = parser.parse_args()

    if args.bid_floor:
        for pair in args.bid_floor.split(","):
            if "=" not in pair:
                continue
            cc, val = pair.split("=", 1)
            r1.PRICE_FLOOR[cc.strip().lower()] = float(val)

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    client = r1.ApiClient(args.base, args.username, password)
    snapshot = client.get_config()
    vault_meta = load_vault_api4_meta()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"country_passrate_50_r2_{stamp}.json"

    plan = [dict(arm) for arm in PLAN_R2]
    balances_before = snapshot_balances(client)
    stocks = {p: country_stock(client, p, CANDIDATES) for p in r1.PROVIDER_ORDER}
    cost = estimate_cost(plan, stocks)
    bower = smsbower_balance(balances_before)
    grizzly = provider_balance(balances_before, "grizzlysms")
    floors = {"smsbower": args.min_smsbower, "grizzlysms": args.min_grizzly}

    print(
        f"R2 smsbower={bower} min={args.min_smsbower} grizzly={grizzly} min={args.min_grizzly} "
        f"lease_cap={args.lease_cap} flood_wait={args.flood_wait_sec}s retries={args.flood_retries}",
        flush=True,
    )
    for row in cost["rows"]:
        print(
            f"  plan {row['id']:<8} {row['country']} N={row['target']} "
            f"provider={row['provider'] or '-'} stock={row['stock']} "
            f"listed={row['listed']} bid={row['bid']} est={row['est_cost']}",
            flush=True,
        )

    report: Dict[str, Any] = {
        "started_at": utc_now(),
        "title": "country passrate ~50 round 2（俄语选国，api_id=4 + Push，FLOOD 则等待）",
        "round": 2,
        "plan": plan,
        "cli": vars(args) | {"password": None, "password_file": args.password_file},
        "constraints": {
            "api_id": 4,
            "official_emulation": False,
            "no_payment": True,
            "no_api_id_6": True,
            "preferred_budget_pct_min": 70,
            "control_budget_pct_cap": 15,
            "no_pk_dump": True,
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
        "waits": [],
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
    # 对照预算：最多 15%，且 in/iq 互斥，默认只跑 4。
    control_cap = min(int(args.lease_cap * 0.15), 6)
    control_leased = 0
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
        nonlocal leased_total, control_leased
        is_ctrl = arm["role"] in {"historical_control", "alt_control"}
        hard = remaining_cap()
        if is_ctrl:
            hard = min(hard, max(0, control_cap - control_leased))
        n = min(n, hard, remaining_by_arm.get(arm["id"], 0), BATCH_COUNT_MAX, args.fill_chunk if wave != "probe" else args.probe_n)
        if n <= 0:
            return None
        bals = live_balances()
        stocks_now = {p: country_stock(client, p, [arm["country"]]) for p in r1.PROVIDER_ORDER}
        report.setdefault("stock_live", []).append({"at": utc_now(), "country": arm["country"], "stock": stocks_now})
        provider, info = pick_provider(stocks_now, arm["country"])
        if provider is None:
            print(f"skip {exp_id}: {arm['country']} 无库存", flush=True)
            report["skipped"].append({"id": exp_id, "reason": "no_stock", "country": arm["country"], "at": utc_now()})
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
        wave_started = utc_now()
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
            report.setdefault("errors", []).append({"id": exp_id, "error": str(exc)[:400], "at": utc_now()})
            return None
        result = attach_prefixes(result)
        result["wave_started_at"] = wave_started
        result["wave_finished_at"] = utc_now()
        leased = int((result.get("analysis") or {}).get("leased_numbers") or 0)
        leased_total += leased
        if is_ctrl:
            control_leased += leased
        remaining_by_arm[arm["id"]] = max(0, remaining_by_arm.get(arm["id"], 0) - n)
        report["rounds"].append(result)
        pref, ctrl, pct = preferred_share(report["rounds"])
        print(
            f"    prefixes={result.get('prefix_counts')} ops={result.get('operator_guess_counts')} "
            f"cumulative leased={leased_total}/{args.lease_cap} preferred={pref} ctrl={ctrl} ({pct:.0f}%)",
            flush=True,
        )
        dump_report(report_path, report)
        return result

    pref_arms = [arm for arm in plan if arm["role"] == "preferred"]
    in_arm = next(arm for arm in plan if arm["id"] == "in_r2")
    iq_arm = next(arm for arm in plan if arm["id"] == "iq_r2")

    try:
        flood_round = 0
        got_live = False
        while remaining_cap() > 0 and flood_round <= args.flood_retries:
            print(
                f"\n--- probe pass {flood_round + 1}/{args.flood_retries + 1} cap_left={remaining_cap()} @ {utc_now()} ---",
                flush=True,
            )
            pass_live: List[str] = []
            pass_flood: List[str] = []
            pass_empty: List[str] = []
            for i, arm in enumerate(pref_arms):
                if remaining_cap() <= 0 or remaining_by_arm.get(arm["id"], 0) <= 0:
                    continue
                if i and args.probe_gap_sec:
                    sleep_with_log(args.probe_gap_sec, f"probe gap before {arm['country']}")
                got = take(f"{arm['id']}_probe{flood_round}", arm, min(args.probe_n, remaining_by_arm[arm["id"]]), "probe")
                if wave_is_live(got):
                    pass_live.append(arm["country"])
                    got_live = True
                elif wave_is_flood(got):
                    pass_flood.append(arm["country"])
                else:
                    pass_empty.append(arm["country"])
                    if got is None:
                        remaining_by_arm[arm["id"]] = 0

            print(f"probe pass live={pass_live} flood={pass_flood} empty={pass_empty}", flush=True)

            if pass_live:
                # 有信号：只加码有 App/SMS 的国，按信号强弱。
                ranked = sorted(
                    [a for a in pref_arms if a["country"] in pass_live],
                    key=lambda a: (
                        country_signal(report["rounds"], a["country"], "t1").get("sms", 0),
                        country_signal(report["rounds"], a["country"], "t1").get("app", 0),
                    ),
                    reverse=True,
                )
                for arm in ranked:
                    if remaining_cap() <= 0:
                        break
                    while remaining_by_arm.get(arm["id"], 0) > 0 and remaining_cap() > 0:
                        sig = country_signal(report["rounds"], arm["country"], "t1")
                        # 累计已无 App/SMS 且最近在 FLOOD：停该国
                        last = next(
                            (x for x in reversed(report["rounds"]) if x.get("country") == arm["country"]),
                            None,
                        )
                        if last and wave_is_flood(last) and not (sig.get("sms") or sig.get("success")):
                            print(f"STOP fill {arm['country']}: last wave FLOOD {sig}", flush=True)
                            remaining_by_arm[arm["id"]] = 0
                            break
                        if args.fill_gap_sec:
                            sleep_with_log(args.fill_gap_sec, f"fill gap {arm['country']}")
                        fill = take(
                            f"{arm['id']}_fill{flood_round}_{int(time.time())}",
                            arm,
                            min(args.fill_chunk, remaining_by_arm[arm["id"]]),
                            "fill",
                        )
                        if fill is None:
                            break
                        if wave_is_flood(fill):
                            print(f"STOP fill {arm['country']}: fill wave flipped FLOOD", flush=True)
                            remaining_by_arm[arm["id"]] = 0
                            break
                break

            # 无 App/SMS：不要填满。真 FLOOD / 已租到号才等 30–60 分钟再探针。
            all_quiet = not pass_live
            leased_so_far = leased_total
            if all_quiet and flood_round < args.flood_retries and remaining_cap() > 0:
                if not pass_flood and leased_so_far == 0:
                    print("所有优选国无库存且 0 租号，不等待窗口。", flush=True)
                    break
                wait_s = args.flood_wait_sec
                report["waits"].append({
                    "at": utc_now(),
                    "seconds": wait_s,
                    "reason": "probe_all_flood_or_empty",
                    "flood": pass_flood,
                    "empty": pass_empty,
                })
                sleep_with_log(wait_s, "开局无 App/SMS，等待窗口，不连射")
                flood_round += 1
                continue
            break

        # 对照：仅在优选已跑过、且对照额度仍在 15% 内时跑 4 号。in 无号才 iq。
        if remaining_cap() > 0 and control_leased < control_cap and not report.get("stopped_need_topup"):
            if args.probe_gap_sec:
                sleep_with_log(min(args.probe_gap_sec, 60), "control gap")
            ctrl = take(f"{in_arm['id']}_probe", in_arm, min(args.probe_n, remaining_by_arm[in_arm["id"]], control_cap), "probe")
            in_leased = int((ctrl.get("analysis") or {}).get("leased_numbers") or 0) if ctrl else 0
            if in_leased == 0 and remaining_cap() > 0 and control_leased < control_cap:
                print("in 对照租不到，改 iq（仍 api_id=4，禁止 Payment）", flush=True)
                take(f"{iq_arm['id']}_probe", iq_arm, min(args.probe_n, remaining_by_arm[iq_arm["id"]], control_cap - control_leased), "probe")
            elif ctrl and wave_is_live(ctrl) and remaining_by_arm[in_arm["id"]] > 0:
                take(f"{in_arm['id']}_fill", in_arm, min(args.fill_chunk, remaining_by_arm[in_arm["id"]], control_cap - control_leased), "fill")

        pref, ctrl_n, pct = preferred_share(report["rounds"])
        report["budget_share"] = {
            "preferred_leased": pref,
            "control_leased": ctrl_n,
            "preferred_pct": round(pct, 1),
            "meets_70": pct >= 70.0 if (pref + ctrl_n) else None,
            "control_pct_cap": 15,
        }
        if leased_total < 45 and not report.get("stopped_need_topup"):
            report["short_of_50"] = 50 - leased_total
            print(
                f"NOTE: 实际租号 {leased_total} < 45。差额来自 FLOOD 等待后仍无窗口 / 无库存 / 安全线，"
                f"不是为了凑 50 去连射。",
                flush=True,
            )
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
    report["prefix_table"] = combine_prefix_table(report["rounds"])
    report["remaining_by_arm"] = remaining_by_arm
    if leased_total < 50 and report.get("need_topup_amount") is None:
        if report.get("stopped_need_topup"):
            pass
        elif report.get("waits"):
            report["need_topup_amount"] = 0
            report["need_topup_reason"] = "不是余额问题：窗口 FLOOD/无号，不建议为同一窗口充值加码"
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
