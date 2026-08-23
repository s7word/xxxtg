#!/usr/bin/env python3
"""Merge part1+part2 live stress runs into one 20x10 attribution report."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from run_batch_stress_analysis import (
    aggregate,
    iso_local,
    render_report,
    utc_now,
    write_outputs,
)


def reclass(reason, error) -> str:
    blob = f"{reason or ''} {error or ''}"
    if "NO_BALANCE" in blob or "余额不足" in blob:
        return "NO_BALANCE"
    return reason or "OTHER"


def load_part(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    p1 = load_part(root / "data/stress_reports/live_200/run.json")
    p2 = load_part(root / "data/stress_reports/live_200_part2/run.json")
    rounds = []
    for src in (p1, p2):
        for rnd in src.get("rounds") or []:
            tasks = []
            reasons = Counter()
            refunds = Counter()
            statuses = Counter()
            for task in rnd.get("tasks") or []:
                item = dict(task)
                item["reason"] = reclass(item.get("reason"), item.get("error"))
                tasks.append(item)
                reasons[item["reason"]] += 1
                refunds[item.get("refund") or "unknown"] += 1
                statuses[item.get("status") or "unknown"] += 1
            row = dict(rnd)
            row["round"] = len(rounds) + 1
            row["tasks"] = tasks
            row["reasons"] = dict(reasons)
            row["refunds"] = dict(refunds)
            row["statuses"] = dict(statuses)
            row["success"] = statuses.get("success", 0)
            row["failed"] = statuses.get("failed", 0)
            row["filtered"] = statuses.get("filtered", 0)
            rounds.append(row)

    start = p1.get("balance_start")
    end = p2.get("balance_end")
    merged = {
        "base": p1.get("base"),
        "health": p1.get("health"),
        "planned_rounds": 20,
        "planned_attempts": 200,
        "completed_rounds": len(rounds),
        "count": 10,
        "concurrency": 10,
        "countries": ["iq", "id", "br", "cl", "kz", "ph", "pk", "ke"],
        "max_price": 1.0,
        "balance_start": start,
        "balance_end": end,
        "balance_delta": round(float(end) - float(start), 6) if start is not None and end is not None else None,
        "started_at": p1.get("started_at"),
        "started_at_local": p1.get("started_at_local"),
        "finished_at": p2.get("finished_at") or utc_now(),
        "finished_at_local": p2.get("finished_at_local") or iso_local(),
        "stop_reason": (
            "20 轮已全部发起。前 9 轮按 IQ/ID/BR/CL/KZ 轮换；"
            "第 10 轮因余额相对 KZ 参考价过低暂停后，延迟退款回笼，"
            "后 11 轮改打更便宜的有货国家 ID/CL/PH/PK/KE，合计仍为 200 次尝试。"
        ),
        "rounds": rounds,
        "parts": {
            "part1_balance": {"start": p1.get("balance_start"), "end": p1.get("balance_end")},
            "part2_balance": {"start": p2.get("balance_start"), "end": p2.get("balance_end")},
            "delayed_refund_recovered": (
                round(float(p2.get("balance_start")) - float(p1.get("balance_end")), 6)
                if p1.get("balance_end") is not None and p2.get("balance_start") is not None
                else None
            ),
        },
        "prior_baseline": {
            "note": "本调度启动前，同进程已有 3 轮 IQ×10 失败批次（不计入 200）。",
            "attempts": 30,
            "success": 0,
            "reasons": {"SENT_CODE_TYPE_APP": 25, "PRECHECK_PHONE_ALREADY_REGISTERED": 5},
        },
    }
    out = root / "data/stress_reports/merged_200"
    aggregate(merged)
    write_outputs(out, merged)
    print(f"merged attempts={merged['totals']['attempts']} success={merged['totals']['success']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
