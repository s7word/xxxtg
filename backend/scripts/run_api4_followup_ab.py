#!/usr/bin/env python3
"""api_id=4 续测：A=iq FLOOD→App 复查；B=in 号池盯 SentCodeTypeSms。

实验 A（同场对照）::

    A_treat  T4 栈：api_id=4 + hash 014b35… + Push attach + vault 机型
             + lang_pack=android + tz=10800 (Asia/Baghdad) + official_emulation=false
             3–4 个 iq
    A_ctrl   T0：同 api_id=4+Push，不写 lang_pack、不写 tz、不强制 vault 回放
             2 个 iq

实验 B（in / +91）::

    握手优先 T3（lang_pack=android、不写 tz）；若出现真实 API_ID_PUBLISHED_FLOOD
    立刻把 lang_pack 回退为空。可换 max_price / provider / 等待以模拟换窗口。
    4–6 个 in。成功标准：至少 1 次 SentCodeTypeSms 或注册成功。

禁止 api_id=6 / Payment / 假收据。总租号硬上限默认 14。

用法::

    python3 backend/scripts/run_api4_followup_ab.py --check-only
    python3 backend/scripts/run_api4_followup_ab.py --lease-cap 14
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
    VAULT_BASE,
    country_stock,
    hypothesis_table,
    run_variant,
    smsbower_balance,
)
from backend.scripts.run_code_delivery_ab import ApiClient, utc_now  # noqa: E402
from backend.scripts.run_registration_sprint import snapshot_balances  # noqa: E402
from backend.scripts.run_vault_mode_sprint import load_vault_api4_meta  # noqa: E402

ACTUAL_FLOOD_MARKERS = (
    "服务端仍返回 API_ID_PUBLISHED_FLOOD",
    "sendCode 触发 API_ID_PUBLISHED_FLOOD",
)
SMS_TYPE_MARKERS = ("SentCodeTypeSms", "auth.sentCodeTypeSms", "sentCodeTypeSms")


def t0_apply() -> Dict[str, Any]:
    return {
        **VAULT_BASE,
        "pin_app_version_substr": "",
        "vault_fingerprint_replay": False,
        "force_country_locale": False,
        "init_connection_set_lang_pack": False,
        "init_connection_set_tz_offset": False,
        "init_connection_tz_offset_override": None,
        "hunt_device_max_uses": 8,
        "hunt_proxy_max_uses": 8,
    }


def t4_apply() -> Dict[str, Any]:
    return {
        **T1_STACK,
        "hunt_device_max_uses": 8,
        "hunt_proxy_max_uses": 8,
    }


def t3_apply() -> Dict[str, Any]:
    """上次较不伤：vault + lang_pack=android，不写 tz。"""
    return {
        **T1_STACK,
        "init_connection_set_tz_offset": False,
        "hunt_device_max_uses": 8,
        "hunt_proxy_max_uses": 8,
    }


def t3_langpack_empty_apply() -> Dict[str, Any]:
    """FLOOD 回退：仍不写 tz，lang_pack 空。"""
    return {
        **T1_STACK,
        "init_connection_set_lang_pack": False,
        "init_connection_set_tz_offset": False,
        "hunt_device_max_uses": 8,
        "hunt_proxy_max_uses": 8,
    }


def spec_a_treat(count: int) -> Dict[str, Any]:
    return {
        "hypothesis": "A",
        "label": "A_treat_t4_iq",
        "description": "T4 栈复查：vault+lang_pack=android+tz=10800+Push+emu=false，国家 iq",
        "country": "iq",
        "count": count,
        "attempts": 1,
        "apply": t4_apply(),
        "expect_init": {"lang_pack": "android", "tz_written": True, "tz": 10800},
    }


def spec_a_ctrl(count: int) -> Dict[str, Any]:
    return {
        "hypothesis": "A",
        "label": "A_ctrl_t0_iq",
        "description": "对照 T0：api_id=4+Push，不写 lang_pack/tz、不回放 vault",
        "country": "iq",
        "count": count,
        "attempts": 1,
        "apply": t0_apply(),
        "expect_init": {"lang_pack_empty": True, "tz_written": False},
    }


def spec_b(count: int, *, lang_pack_android: bool, wave: int, note: str) -> Dict[str, Any]:
    apply = t3_apply() if lang_pack_android else t3_langpack_empty_apply()
    expect = (
        {"lang_pack": "android", "tz_written": False}
        if lang_pack_android
        else {"lang_pack_empty": True, "tz_written": False}
    )
    tag = "android" if lang_pack_android else "empty"
    return {
        "hypothesis": "B",
        "label": f"B_in_w{wave}_{tag}",
        "description": f"in 窗口波次{wave} lang_pack={tag} 不写tz {note}",
        "country": "in",
        "count": count,
        "attempts": 1,
        "apply": apply,
        "expect_init": expect,
    }


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


def stock_price(stock: Dict[str, Any], country: str) -> Optional[float]:
    block = stock.get(country) or {}
    return _as_float(block.get("price"))


def stock_count(stock: Dict[str, Any], country: str) -> int:
    block = stock.get(country) or {}
    return _as_int(block.get("count"))


def bid_for(country: str, listed: Optional[float], floor: float) -> float:
    if listed is None:
        return floor
    bumped = max(floor, round(listed * 1.15 + 0.05, 2), round(listed + 0.15, 2))
    cap = 2.2 if country == "in" else 1.6
    return min(bumped, cap)


def is_actual_flood_row(row: Dict[str, Any]) -> bool:
    err = str(row.get("error") or "")
    excerpt = "\n".join(str(x) for x in (row.get("log_excerpt") or []))
    blob = err + "\n" + excerpt
    return any(m in blob for m in ACTUAL_FLOOD_MARKERS)


def sent_sms_count(result: Dict[str, Any]) -> int:
    types = (result.get("analysis") or {}).get("sent_code_types") or {}
    n = 0
    for key, val in types.items():
        if any(m.lower() in str(key).lower() for m in ("sms",)) and "firebase" not in str(key).lower():
            n += int(val or 0)
    return n


def annotate_result(result: Dict[str, Any]) -> Dict[str, Any]:
    rows = result.get("rows") or []
    actual_flood = sum(1 for r in rows if is_actual_flood_row(r))
    recaptcha = sum(1 for r in rows if "RECAPTCHA" in str(r.get("error") or "").upper())
    banned = sum(1 for r in rows if "BANNED" in str(r.get("error") or "").upper())
    nonumber = sum(
        1
        for r in rows
        if "noNumber" in str(r.get("error") or "") or (r.get("no_sendcode_reason") == "NO_NUMBERS")
    )
    push_fail = sum(
        1
        for r in rows
        if "Push Token" in str(r.get("error") or "") or (r.get("no_sendcode_reason") == "ATTESTATION_FAILED")
    )
    types = (result.get("analysis") or {}).get("sent_code_types") or {}
    app = int(types.get("SentCodeTypeApp") or types.get("auth.sentCodeTypeApp") or 0)
    result["actual_flood_tasks"] = actual_flood
    result["recaptcha_fail"] = recaptcha
    result["banned"] = banned
    result["nonumber"] = nonumber
    result["push_fail"] = push_fail
    result["app_count"] = app
    result["sms_type_count"] = sent_sms_count(result)
    s = result.get("summary") or {}
    result["success_count"] = int(s.get("success") or 0)
    result["sms_received"] = int(s.get("sms_code_received") or 0)
    return result


def followup_table(rounds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = hypothesis_table(rounds)
    by_id = {item.get("experiment_id"): item for item in rounds}
    out = []
    for row in table:
        src = by_id.get(row["id"]) or {}
        row["flood"] = int(src.get("actual_flood_tasks") or row.get("flood") or 0)
        row["app"] = int(src.get("app_count") or row.get("app") or 0)
        row["sms"] = int(src.get("sms_type_count") or row.get("sms") or 0)
        row["success"] = int(src.get("success_count") or row.get("success") or 0)
        row["nonumber"] = int(src.get("nonumber") or 0)
        row["push_fail"] = int(src.get("push_fail") or 0)
        row["recaptcha"] = int(src.get("recaptcha_fail") or 0)
        row["sms_provider"] = src.get("sms_provider")
        row["max_price"] = src.get("max_price")
        out.append(row)
    return out


def pick_in_provider(
    client: ApiClient,
    preferred: List[str],
    *,
    need_stock: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    scanned: List[Dict[str, Any]] = []
    for provider in preferred:
        stock = country_stock(client, provider, ["in"])
        scanned.append({"provider": provider, "stock": stock})
        n = stock_count(stock, "in")
        if not need_stock or n > 0:
            return provider, {"chosen": provider, "in_count": n, "in_price": stock_price(stock, "in"), "all": scanned}
    fallback = preferred[0] if preferred else "smsbower"
    return fallback, {"chosen": fallback, "in_count": 0, "in_price": None, "all": scanned, "none_in_stock": True}


def dump_report(path: Path, report: Dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_one(
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
    result = run_variant(
        client,
        exp_id=exp_id,
        spec=spec,
        snapshot=snapshot,
        sms_provider=sms_provider,
        max_price=max_price,
        proxy_mode=proxy_mode,
        poll=poll,
        batch_timeout=batch_timeout,
    )
    result["max_price"] = max_price
    return annotate_result(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--max-price-iq", type=float, default=1.0)
    parser.add_argument("--max-price-in", type=float, default=1.2)
    parser.add_argument("--proxy-mode", default="auto")
    parser.add_argument("--poll", type=float, default=12.0)
    parser.add_argument("--batch-timeout", type=float, default=900.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    parser.add_argument("--lease-cap", type=int, default=14)
    parser.add_argument("--min-smsbower", type=float, default=4.0)
    parser.add_argument("--a-treat", type=int, default=4)
    parser.add_argument("--a-ctrl", type=int, default=2)
    parser.add_argument("--b-count", type=int, default=6)
    parser.add_argument("--b-wave-size", type=int, default=2)
    parser.add_argument("--window-wait", type=float, default=45.0)
    parser.add_argument("--skip-a", action="store_true")
    parser.add_argument("--skip-b", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    client = ApiClient(args.base, args.username, password)
    snapshot = client.get_config()
    vault_meta = load_vault_api4_meta()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"api4_followup_{stamp}.json"

    balances_before = snapshot_balances(client)
    bower = smsbower_balance(balances_before)
    stock_smsbower = country_stock(client, "smsbower", ["in", "iq"])
    stock_grizzly = country_stock(client, "grizzlysms", ["in", "iq"])
    stock_fivesim = country_stock(client, "fivesim", ["in", "iq"])
    print(
        f"smsbower balance={bower} min={args.min_smsbower} "
        f"stock_smsbower={stock_smsbower} stock_grizzly={stock_grizzly} "
        f"stock_fivesim={stock_fivesim} vault_samples={len(vault_meta)} "
        f"lease_cap={args.lease_cap}",
        flush=True,
    )

    report: Dict[str, Any] = {
        "started_at": utc_now(),
        "title": "api_id=4 follow-up A/B（iq FLOOD→App 复查 + in SMS 窗口）",
        "cli": {
            "sms_provider": args.sms_provider,
            "max_price_iq": args.max_price_iq,
            "max_price_in": args.max_price_in,
            "lease_cap": args.lease_cap,
            "a_treat": args.a_treat,
            "a_ctrl": args.a_ctrl,
            "b_count": args.b_count,
        },
        "constraints": {
            "api_id": 4,
            "official_emulation": False,
            "no_payment": True,
            "no_api_id_6": True,
        },
        "vault_fingerprint_samples": [
            {k: v for k, v in row.items() if k not in {"app_hash"}}
            for row in vault_meta
        ],
        "balances_before": balances_before,
        "stock": {
            "smsbower": stock_smsbower,
            "grizzlysms": stock_grizzly,
            "fivesim": stock_fivesim,
        },
        "stopped_need_topup": False,
        "rounds": [],
        "hypothesis_table": [],
    }

    if bower is None:
        print("ERROR: 无法读取 smsbower 余额，停止。", flush=True)
        report["error"] = "smsbower_balance_unreadable"
        dump_report(report_path, report)
        return 2
    if bower < args.min_smsbower:
        need = round(args.min_smsbower - bower + 5, 2)
        print(
            f"STOP: smsbower 余额 {bower} < 最低 {args.min_smsbower}，请充值约 {need} USD 后再测。",
            flush=True,
        )
        report["stopped_need_topup"] = True
        report["need_topup_amount"] = need
        dump_report(report_path, report)
        return 3
    if args.check_only:
        print("check-only: 余额充足，未租号。", flush=True)
        report["check_only"] = True
        dump_report(report_path, report)
        print(f"report -> {report_path}", flush=True)
        return 0

    leased_total = 0
    exit_code = 0
    common = dict(
        snapshot=snapshot,
        proxy_mode=args.proxy_mode,
        poll=args.poll,
        batch_timeout=args.batch_timeout,
    )

    def remaining_cap() -> int:
        return max(0, args.lease_cap - leased_total)

    def live_ok() -> bool:
        live = smsbower_balance(snapshot_balances(client))
        if live is not None and live < args.min_smsbower:
            print(f"STOP mid-run: smsbower {live} < {args.min_smsbower}", flush=True)
            report["stopped_need_topup"] = True
            report["need_topup_amount"] = round(args.min_smsbower - live + 5, 2)
            return False
        return True

    def take(exp_id: str, spec: Dict[str, Any], provider: str, price: float) -> Optional[Dict[str, Any]]:
        nonlocal leased_total
        planned = int(spec["count"]) * int(spec["attempts"])
        if planned <= 0 or remaining_cap() < planned:
            print(f"skip {exp_id}: planned={planned} remaining={remaining_cap()}", flush=True)
            report.setdefault("skipped", []).append(
                {"id": exp_id, "reason": "lease_cap", "planned": planned, "remaining": remaining_cap()}
            )
            return None
        if not live_ok():
            return None
        result = run_one(
            client,
            exp_id=exp_id,
            spec=spec,
            sms_provider=provider,
            max_price=price,
            **common,
        )
        report["rounds"].append(result)
        leased = int((result.get("analysis") or {}).get("leased_numbers") or 0)
        leased_total += leased
        result["cumulative_leased"] = leased_total
        print(
            f"    cumulative leased={leased_total}/{args.lease_cap} "
            f"flood={result.get('actual_flood_tasks')} app={result.get('app_count')} "
            f"sms_type={result.get('sms_type_count')} ok={result.get('success_count')}",
            flush=True,
        )
        dump_report(report_path, report)
        return result

    try:
        if not args.skip_a:
            iq_listed = stock_price(stock_smsbower, "iq")
            iq_bid = bid_for("iq", iq_listed, args.max_price_iq)
            print(f"\n--- 实验 A iq 复查 bid={iq_bid} listed={iq_listed} ---", flush=True)
            treat_n = min(args.a_treat, remaining_cap())
            take("A_treat", spec_a_treat(treat_n), args.sms_provider, iq_bid)
            # 处理组 sendCode 不足时用剩余额度补 1 号，仍计在 A_treat
            treat_round = report["rounds"][-1] if report["rounds"] else {}
            treat_send = int((treat_round.get("analysis") or {}).get("sendcode_samples") or 0)
            if treat_send < 3 and remaining_cap() >= 1 and live_ok():
                print("    A_treat sendCode 不足，补 1 个 iq", flush=True)
                take("A_treat_topup", spec_a_treat(1), args.sms_provider, iq_bid)
            ctrl_n = min(args.a_ctrl, remaining_cap())
            take("A_ctrl", spec_a_ctrl(ctrl_n), args.sms_provider, iq_bid)

        if not args.skip_b and remaining_cap() > 0 and live_ok():
            print("\n--- 实验 B in 号池 / 时段窗口 ---", flush=True)
            lang_pack_android = True
            in_target = min(args.b_count, remaining_cap())
            in_leased = 0
            wave = 0
            in_bid = bid_for("in", stock_price(stock_smsbower, "in"), args.max_price_in)
            provider = args.sms_provider
            preferred = [args.sms_provider, "grizzlysms", "fivesim"]
            preferred = list(dict.fromkeys([p for p in preferred if p]))
            max_waves = 6
            sms_hit = False
            while in_leased < in_target and remaining_cap() > 0 and wave < max_waves and live_ok():
                wave += 1
                live_stock = country_stock(client, provider, ["in"])
                listed = stock_price(live_stock, "in")
                have = stock_count(live_stock, "in")
                if have <= 0:
                    provider, pick = pick_in_provider(client, preferred)
                    report.setdefault("provider_switches", []).append({"wave": wave, **pick})
                    print(f"    wave{wave} 无库存，切换 provider={provider} pick={pick.get('in_count')}", flush=True)
                    live_stock = country_stock(client, provider, ["in"])
                    listed = stock_price(live_stock, "in")
                    have = stock_count(live_stock, "in")
                    in_bid = bid_for("in", listed, in_bid)
                if have <= 0:
                    in_bid = min(round(in_bid + 0.4, 2), 2.2)
                    print(f"    wave{wave} 仍无库存，抬价 max_price={in_bid} 并等待窗口", flush=True)
                    time.sleep(args.window_wait)
                    continue
                in_bid = bid_for("in", listed, in_bid)
                n = min(args.b_wave_size, in_target - in_leased, remaining_cap())
                note = f"provider={provider} bid={in_bid}"
                spec = spec_b(n, lang_pack_android=lang_pack_android, wave=wave, note=note)
                result = take(f"B_w{wave}", spec, provider, in_bid)
                if result is None:
                    break
                leased = int((result.get("analysis") or {}).get("leased_numbers") or 0)
                in_leased += leased
                if result.get("sms_type_count") or result.get("success_count") or result.get("sms_received"):
                    sms_hit = True
                    print("    B 命中 SMS/成功，提前结束 in 窗口。", flush=True)
                    break
                if result.get("actual_flood_tasks") and lang_pack_android:
                    print("    B 出现真实 FLOOD，lang_pack 回退为空。", flush=True)
                    lang_pack_android = False
                    report.setdefault("b_handshake_fallback", []).append(
                        {"after_wave": wave, "to": "lang_pack_empty"}
                    )
                if result.get("nonumber") and leased == 0:
                    in_bid = min(round(in_bid + 0.4, 2), 2.2)
                    nxt, pick = pick_in_provider(client, preferred)
                    if nxt != provider:
                        provider = nxt
                        report.setdefault("provider_switches", []).append({"wave": wave, "reason": "nonumber", **pick})
                        print(f"    noNumber → provider={provider} bid={in_bid}", flush=True)
                if in_leased < in_target and remaining_cap() > 0:
                    print(f"    等待 {args.window_wait}s 模拟换窗口…", flush=True)
                    time.sleep(args.window_wait)
            report["b_sms_hit"] = sms_hit
            report["b_in_leased"] = in_leased
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
    bower_after = smsbower_balance(report["balances_after"])
    report["smsbower_before"] = bower
    report["smsbower_after"] = bower_after
    dump_report(report_path, report)
    print(f"\nreport -> {report_path}", flush=True)
    for row in report["hypothesis_table"]:
        print(
            f"  {row['id']:<14} {row['country']:<3} leased={row['leased']} "
            f"send={row['sendcode']} App={row['app']} FLOOD={row['flood']} "
            f"SMS={row['sms']} ok={row['success']}",
            flush=True,
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
