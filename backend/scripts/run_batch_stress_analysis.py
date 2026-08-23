#!/usr/bin/env python3
"""20-round x 10-thread live batch stress test + attribution report.

Calls the local backend:
  POST /api/register/batch   (count=10, concurrency=10)
  GET  /api/register/batches/{batch_id}
  GET  /api/register/tasks?batch_id=...
  POST /api/test/grizzlysms  (balance + per-country stock)

Default plan: 20 sequential rounds, rotating IQ / ID / BR / CL / KZ
(user-requested in-stock countries), totaling 200 attempts.

Safety: abort remaining rounds when Grizzly balance falls below --min-balance
or cannot cover the next country's reference cost.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_BASE = os.environ.get("REGHELP_API_BASE", "http://127.0.0.1:8000")
DEFAULT_COUNTRIES = ["iq", "id", "br", "cl", "kz"]
DEFAULT_ROUNDS = 20
DEFAULT_COUNT = 10
DEFAULT_CONCURRENCY = 10
DEFAULT_MAX_PRICE = 1.0
DEFAULT_MIN_BALANCE = 0.80
DEFAULT_POLL_SECONDS = 3.0
DEFAULT_BATCH_TIMEOUT = 720.0

REASON_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("PRECHECK_PHONE_ALREADY_REGISTERED", ("PRECHECK_PHONE_ALREADY_REGISTERED",)),
    ("PHONE_NUMBER_BANNED", ("PHONE_NUMBER_BANNED", "LOCAL_BANNED", "PHONE_PREAUDIT_BANNED")),
    ("SENT_CODE_TYPE_APP", ("SENT_CODE_TYPE_APP", "SentCodeTypeApp")),
    ("NO_NUMBERS", ("NO_NUMBERS", "noNumber", "no_number", "NO_NUMBER")),
    ("FLOOD_WAIT", ("FLOOD_WAIT",)),
    ("EARLY_CANCEL_DENIED", ("EARLY_CANCEL_DENIED", "EARLY_CANCEL")),
    ("WRONG_CODE", ("WRONG_CODE",)),
    ("NO_CODE", ("NO_CODE",)),
    ("RECAPTCHA_CHECK", ("RECAPTCHA_CHECK", "RECAPTCHA")),
    ("API_ID_PUBLISHED_FLOOD", ("API_ID_PUBLISHED_FLOOD", "API_ID_PUBLISHED")),
    ("NETWORK_TIMEOUT", ("Timeout", "timeout", "timed out", "ConnectError", "Connection", "ProxyError", "Network")),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"detail": body}
        raise RuntimeError(f"HTTP {exc.code} {url}: {parsed}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error {url}: {exc}") from exc


def mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return phone
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 6:
        return "***"
    return f"+{digits[:4]}****{digits[-4:]}"


def classify_error(error: Optional[str], logs: Optional[Iterable[str]] = None) -> str:
    blob = str(error or "")
    if blob:
        for reason, tokens in REASON_RULES:
            if any(token.lower() in blob.lower() for token in tokens):
                return reason
    joined = "\n".join(logs or [])
    if joined:
        for reason, tokens in REASON_RULES:
            if any(token.lower() in joined.lower() for token in tokens):
                return reason
    if not blob:
        return "UNKNOWN_EMPTY"
    return "OTHER"


def refund_outcome(logs: Optional[Iterable[str]]) -> str:
    text = "\n".join(logs or [])
    if "自动退订/撤销信道句柄完成" in text or "status=8" in text:
        return "refunded"
    if "EARLY_CANCEL_DENIED" in text:
        return "early_cancel_denied"
    if "自动退订/撤销信道句柄未成功" in text:
        return "refund_failed"
    if not text:
        return "unknown"
    return "no_refund_attempt"


def terminal_statuses() -> set:
    return {"success", "failed", "filtered"}


class StressClient:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def health(self) -> Any:
        return http_json("GET", f"{self.base}/api/health")

    def grizzly(self, country: str) -> Any:
        return http_json("POST", f"{self.base}/api/test/grizzlysms", {"country": country})

    def start_batch(self, country: str, count: int, concurrency: int, max_price: float) -> Any:
        return http_json(
            "POST",
            f"{self.base}/api/register/batch",
            {
                "country": country,
                "app_type": "telegram_android",
                "count": count,
                "concurrency": concurrency,
                "sms_provider": "grizzlysms",
                "max_price": max_price,
                "proxy_mode": "custom_pool",
            },
            timeout=90.0,
        )

    def get_batch(self, batch_id: str) -> Any:
        return http_json("GET", f"{self.base}/api/register/batches/{batch_id}")

    def list_tasks(self, batch_id: str) -> List[Dict[str, Any]]:
        data = http_json("GET", f"{self.base}/api/register/tasks?batch_id={batch_id}")
        if isinstance(data, list):
            return data
        return data.get("tasks") or []


def parse_balance(probe: Dict[str, Any]) -> Optional[float]:
    data = probe.get("data") or {}
    raw = data.get("balance")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def wait_batch(
    client: StressClient,
    batch_id: str,
    poll: float,
    timeout: float,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    started = time.time()
    last_batch: Dict[str, Any] = {}
    last_tasks: List[Dict[str, Any]] = []
    while True:
        last_batch = client.get_batch(batch_id)
        last_tasks = client.list_tasks(batch_id)
        running = int(last_batch.get("running") or 0)
        pending = int(last_batch.get("pending") or 0)
        statuses = {str(t.get("status") or "") for t in last_tasks}
        unfinished = [t for t in last_tasks if t.get("status") not in terminal_statuses()]
        if running == 0 and pending == 0 and not unfinished and statuses <= (terminal_statuses() | {""}):
            if last_tasks and all(t.get("status") in terminal_statuses() for t in last_tasks):
                return last_batch, last_tasks, False
        if last_tasks and all(t.get("status") in terminal_statuses() for t in last_tasks):
            return last_batch, last_tasks, False
        if time.time() - started >= timeout:
            return last_batch, last_tasks, True
        time.sleep(poll)


def summarize_tasks(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    reasons: Counter = Counter()
    statuses: Counter = Counter()
    refunds: Counter = Counter()
    rows = []
    for task in tasks:
        logs = task.get("logs") or []
        reason = classify_error(task.get("error"), logs)
        refund = refund_outcome(logs)
        status = str(task.get("status") or "unknown")
        if status == "success":
            reason = "SUCCESS"
        reasons[reason] += 1
        statuses[status] += 1
        refunds[refund] += 1
        rows.append(
            {
                "task_id": task.get("task_id"),
                "status": status,
                "reason": reason,
                "refund": refund,
                "phone": mask_phone(task.get("phone")),
                "precheck_intercepted": bool(task.get("precheck_intercepted")),
                "no_number": bool(task.get("no_number")),
                "banned_cache_hit": bool(task.get("banned_cache_hit")),
                "error": task.get("error"),
                "user_id": task.get("user_id"),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
            }
        )
    return {
        "statuses": dict(statuses),
        "reasons": dict(reasons),
        "refunds": dict(refunds),
        "success": statuses.get("success", 0),
        "failed": statuses.get("failed", 0),
        "filtered": statuses.get("filtered", 0),
        "tasks": rows,
    }


def pct(part: int, total: int) -> str:
    if total <= 0:
        return "0.00%"
    return f"{(part / total) * 100:.2f}%"


def render_report(run: Dict[str, Any]) -> str:
    totals = run["totals"]
    n = totals["attempts"]
    lines = [
        "# 200 次批量注册压力测试归因报告",
        "",
        f"- 生成时间: {run.get('finished_at_local') or iso_local()}",
        f"- API: `{run.get('base')}`",
        f"- 规划: {run.get('planned_rounds')} 轮 × {run.get('count')} 任务 / 并发 {run.get('concurrency')} = {run.get('planned_attempts')} 次尝试",
        f"- 实际完成: {run.get('completed_rounds')} 轮 / {n} 次尝试",
        f"- 国家轮换: {', '.join(c.upper() for c in run.get('countries') or [])}",
        f"- 最高出价 max_price: {run.get('max_price')}",
        f"- 起始余额: {run.get('balance_start')} USD",
        f"- 结束余额: {run.get('balance_end')} USD",
        f"- 余额变动: {run.get('balance_delta')} USD",
        "",
        "## 1. 执行漏斗",
        "",
        f"| 指标 | 数量 | 占比 |",
        f"| --- | ---: | ---: |",
        f"| Total Attempts | {n} | 100.00% |",
        f"| Success (session 落盘) | {totals['success']} | {pct(totals['success'], n)} |",
        f"| Failed | {totals['failed']} | {pct(totals['failed'], n)} |",
        f"| Filtered | {totals['filtered']} | {pct(totals['filtered'], n)} |",
        f"| Unfinished / timeout | {totals['unfinished']} | {pct(totals['unfinished'], n)} |",
        "",
        "## 2. 失败原因精确分布",
        "",
        "| 原因 | 数量 | 占比 |",
        "| --- | ---: | ---: |",
    ]
    for reason in [
        "SUCCESS",
        "SENT_CODE_TYPE_APP",
        "PHONE_NUMBER_BANNED",
        "PRECHECK_PHONE_ALREADY_REGISTERED",
        "NO_NUMBERS",
        "FLOOD_WAIT",
        "EARLY_CANCEL_DENIED",
        "WRONG_CODE",
        "NO_CODE",
        "RECAPTCHA_CHECK",
        "API_ID_PUBLISHED_FLOOD",
        "NETWORK_TIMEOUT",
        "OTHER",
        "UNKNOWN_EMPTY",
    ]:
        count = int(totals["reasons"].get(reason, 0))
        lines.append(f"| `{reason}` | {count} | {pct(count, n)} |")
    extra = sorted(
        (k, v) for k, v in totals["reasons"].items() if k not in {
            "SUCCESS", "SENT_CODE_TYPE_APP", "PHONE_NUMBER_BANNED",
            "PRECHECK_PHONE_ALREADY_REGISTERED", "NO_NUMBERS", "FLOOD_WAIT",
            "EARLY_CANCEL_DENIED", "WRONG_CODE", "NO_CODE", "RECAPTCHA_CHECK",
            "API_ID_PUBLISHED_FLOOD", "NETWORK_TIMEOUT", "OTHER", "UNKNOWN_EMPTY",
        }
    )
    for reason, count in extra:
        lines.append(f"| `{reason}` | {count} | {pct(count, n)} |")

    lines.extend([
        "",
        "## 3. 资金与退款核算",
        "",
        f"- 成功订单 (status=success): **{totals['success']}**",
        f"- 退款成功 (cancel status=8): **{totals['refunds'].get('refunded', 0)}**",
        f"- 退号被拒 EARLY_CANCEL_DENIED: **{totals['refunds'].get('early_cancel_denied', 0)}**",
        f"- 其他退款失败: **{totals['refunds'].get('refund_failed', 0)}**",
        f"- 未见退订动作: **{totals['refunds'].get('no_refund_attempt', 0)}**",
        f"- 起始余额: **{run.get('balance_start')}**",
        f"- 结束余额: **{run.get('balance_end')}**",
        f"- 净消耗: **{run.get('balance_delta')}**",
        "",
        "## 4. 分国家对照",
        "",
        "| 国家 | 尝试 | 成功 | SENT_CODE_TYPE_APP | 预检已注册 | 封禁 | 无库存 | FLOOD | 退款成功 | 退号拒绝 | 耗时(s) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    by_country = run.get("by_country") or {}
    for country, row in by_country.items():
        lines.append(
            "| {c} | {a} | {s} | {app} | {pre} | {ban} | {nn} | {fw} | {rf} | {ed} | {sec:.1f} |".format(
                c=country.upper(),
                a=row["attempts"],
                s=row["success"],
                app=row["reasons"].get("SENT_CODE_TYPE_APP", 0),
                pre=row["reasons"].get("PRECHECK_PHONE_ALREADY_REGISTERED", 0),
                ban=row["reasons"].get("PHONE_NUMBER_BANNED", 0),
                nn=row["reasons"].get("NO_NUMBERS", 0),
                fw=row["reasons"].get("FLOOD_WAIT", 0),
                rf=row["refunds"].get("refunded", 0),
                ed=row["refunds"].get("early_cancel_denied", 0),
                sec=row["elapsed_sec"],
            )
        )

    lines.extend(["", "## 5. 分轮次明细", "", "| 轮次 | batch_id | 国家 | 耗时(s) | 成功 | 失败 | 过滤 | 主因 Top | 余额前 | 余额后 |", "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |"])
    for rnd in run.get("rounds") or []:
        top = ", ".join(f"{k}:{v}" for k, v in Counter(rnd.get("reasons") or {}).most_common(3)) or "-"
        lines.append(
            "| {i} | `{bid}` | {c} | {sec:.1f} | {s} | {f} | {fl} | {top} | {b0} | {b1} |".format(
                i=rnd.get("round"),
                bid=rnd.get("batch_id") or "-",
                c=str(rnd.get("country") or "").upper(),
                sec=float(rnd.get("elapsed_sec") or 0),
                s=rnd.get("success", 0),
                f=rnd.get("failed", 0),
                fl=rnd.get("filtered", 0),
                top=top,
                b0=rnd.get("balance_before"),
                b1=rnd.get("balance_after"),
            )
        )

    lines.extend([
        "",
        "## 6. 结论：为什么老是不成功",
        "",
        run.get("narrative") or "_（运行结束后自动生成）_",
        "",
        "## 7. 提高注册成功率的建议",
        "",
        run.get("recommendations") or "_（运行结束后自动生成）_",
        "",
    ])
    if run.get("stop_reason"):
        lines.extend(["## 8. 未跑满 200 次的原因", "", run["stop_reason"], ""])
    return "\n".join(lines) + "\n"


def build_narrative(run: Dict[str, Any]) -> str:
    totals = run["totals"]
    n = max(1, totals["attempts"])
    reasons = totals["reasons"]
    app = reasons.get("SENT_CODE_TYPE_APP", 0)
    pre = reasons.get("PRECHECK_PHONE_ALREADY_REGISTERED", 0)
    banned = reasons.get("PHONE_NUMBER_BANNED", 0)
    none = reasons.get("NO_NUMBERS", 0)
    flood = reasons.get("FLOOD_WAIT", 0)
    success = totals["success"]
    denied = totals["refunds"].get("early_cancel_denied", 0)
    refunded = totals["refunds"].get("refunded", 0)
    parts = [
        f"本次共发起 **{totals['attempts']}** 次尝试，成功落盘 session **{success}** 个（{pct(success, n)}）。",
        f"最大阻断是 `SENT_CODE_TYPE_APP`：**{app}** 次（{pct(app, n)}）。"
        "这不是接码平台“没货”，而是 Telegram 判定该号已有/曾有客户端会话，验证码只走站内信，"
        "Grizzly 等短信网关永远收不到码。流水线会快速退订，但平台经常回 `EARLY_CANCEL_DENIED`，"
        f"本轮退号被拒 **{denied}** 次、退款成功 **{refunded}** 次。",
        f"白号预检拦截 `PRECHECK_PHONE_ALREADY_REGISTERED`：**{pre}** 次（{pct(pre, n)}）。"
        "说明号池大量是已被注册过的二手卡；预检避免了继续烧 Push Token / sendCode，但租号费用仍可能因过早 cancel 无法退。",
        f"`PHONE_NUMBER_BANNED` **{banned}** 次，`NO_NUMBERS` **{none}** 次，`FLOOD_WAIT` **{flood}** 次。",
        f"Grizzly 余额从 **{run.get('balance_start')}** 变为 **{run.get('balance_end')}**，净消耗 **{run.get('balance_delta')}**。",
    ]
    by_country = run.get("by_country") or {}
    if by_country:
        ranked = sorted(by_country.items(), key=lambda kv: (-kv[1]["success"], kv[1]["attempts"]))
        best = ", ".join(
            f"{c.upper()} {row['success']}/{row['attempts']}" for c, row in ranked
        )
        parts.append(f"分国家成功率对照：{best}。")
    if success == 0:
        parts.append(
            "在当前号池与风控条件下，**注册成功率被压到接近 0** 的根因不是调度并发不够，"
            "而是：① Telegram 对虚拟号大量走站内信；② 号池复用率高（已注册/封禁）；"
            "③ 失败后过早退号被平台拒绝，资金无法回收，进一步限制可重试次数。"
        )
    return "\n\n".join(parts)


def build_recommendations(run: Dict[str, Any]) -> str:
    totals = run["totals"]
    reasons = totals["reasons"]
    recs = [
        "1. **避开 SENT_CODE_TYPE_APP 高发国家**：若某国该比例 >60%，立即从调度轮盘剔除，不要用堆并发硬刚。伊拉克 IQ 热门号池当前就是典型。",
        "2. **优先低复用、低单价且预检拦截率低的国家**：结合本报告分国家表，只把预算打到预检通过率高、且 sendCode 能落到 SMS 的国家。",
        "3. **保持白号预检开启，但要接受“预检拦截仍可能扣费”**：EARLY_CANCEL_DENIED 说明平台对刚租出的号限制立即退订。可考虑：命中已注册后等待平台允许 cancel 的窗口再退，或改用支持即时退的供应商。",
        "4. **并发不要超过号池新鲜度**：10 线程会在同一秒打进同一国家号段，容易拿到同一批二手卡并触发平台频控。热门国家建议降到 3–5 并发，拉长轮次间隔。",
        "5. **资金策略**：max_price 保持贴近网页价（本轮 1.0 覆盖 IQ/ID/BR/CL/KZ）。余额不足时宁可停跑，也不要盲目加价抢更脏的号。",
        "6. **成功路径检查清单**：国家有货 + 预检未注册 + sendCode 通道为 SMS + 代理出口与国家拓扑一致 + 非公开 api_id + 有效 Push Token。任一环节失败都不会出号。",
    ]
    if reasons.get("SENT_CODE_TYPE_APP", 0) >= max(1, totals["attempts"] // 3):
        recs.append(
            "7. **针对站内信主导号池**：继续堆 200 次尝试不会提高成功率。必须换号源/换国家，或只接收平台标注为“新卡/非重号”的 SKU。"
        )
    return "\n".join(recs)


def aggregate(run: Dict[str, Any]) -> None:
    totals = {
        "attempts": 0,
        "success": 0,
        "failed": 0,
        "filtered": 0,
        "unfinished": 0,
        "reasons": Counter(),
        "refunds": Counter(),
        "statuses": Counter(),
    }
    by_country: Dict[str, Dict[str, Any]] = {}
    for rnd in run.get("rounds") or []:
        country = str(rnd.get("country") or "?").lower()
        bucket = by_country.setdefault(
            country,
            {
                "attempts": 0,
                "success": 0,
                "elapsed_sec": 0.0,
                "reasons": Counter(),
                "refunds": Counter(),
            },
        )
        for task in rnd.get("tasks") or []:
            totals["attempts"] += 1
            bucket["attempts"] += 1
            status = task.get("status") or "unknown"
            totals["statuses"][status] += 1
            if status == "success":
                totals["success"] += 1
                bucket["success"] += 1
            elif status == "filtered":
                totals["filtered"] += 1
            elif status == "failed":
                totals["failed"] += 1
            else:
                totals["unfinished"] += 1
            reason = task.get("reason") or "OTHER"
            totals["reasons"][reason] += 1
            bucket["reasons"][reason] += 1
            refund = task.get("refund") or "unknown"
            totals["refunds"][refund] += 1
            bucket["refunds"][refund] += 1
        bucket["elapsed_sec"] += float(rnd.get("elapsed_sec") or 0)
    run["totals"] = {
        "attempts": totals["attempts"],
        "success": totals["success"],
        "failed": totals["failed"],
        "filtered": totals["filtered"],
        "unfinished": totals["unfinished"],
        "reasons": dict(totals["reasons"]),
        "refunds": dict(totals["refunds"]),
        "statuses": dict(totals["statuses"]),
    }
    run["by_country"] = {
        country: {
            "attempts": row["attempts"],
            "success": row["success"],
            "elapsed_sec": row["elapsed_sec"],
            "reasons": dict(row["reasons"]),
            "refunds": dict(row["refunds"]),
        }
        for country, row in by_country.items()
    }
    run["narrative"] = build_narrative(run)
    run["recommendations"] = build_recommendations(run)


def write_outputs(out_dir: Path, run: Dict[str, Any]) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "run.json"
    report_path = out_dir / "BATCH_STRESS_REPORT.md"
    raw_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_report(run), encoding="utf-8")
    latest = out_dir.parent / "BATCH_STRESS_REPORT.md"
    latest.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    return raw_path, latest


def persist(out_dir: Path, run: Dict[str, Any]) -> None:
    aggregate(run)
    write_outputs(out_dir, run)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="20x10 live batch stress + attribution")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--countries", default=",".join(DEFAULT_COUNTRIES))
    parser.add_argument("--max-price", type=float, default=DEFAULT_MAX_PRICE)
    parser.add_argument("--min-balance", type=float, default=DEFAULT_MIN_BALANCE)
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_BATCH_TIMEOUT)
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between rounds")
    parser.add_argument(
        "--out-dir",
        default="",
        help="output directory; default data/stress_reports/<timestamp>",
    )
    args = parser.parse_args(argv)

    countries = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    if not countries:
        print("no countries configured", file=sys.stderr)
        return 2
    if args.count < 1 or args.count > 10 or args.concurrency < 1 or args.concurrency > 10:
        print("count/concurrency must be 1..10 (API constraint)", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[2]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else repo_root / "data" / "stress_reports" / stamp

    client = StressClient(args.base)
    try:
        health = client.health()
    except Exception as exc:
        print(f"backend unreachable: {exc}", file=sys.stderr)
        return 3

    start_probe = client.grizzly(countries[0])
    balance_start = parse_balance(start_probe)
    run: Dict[str, Any] = {
        "base": args.base,
        "health": health,
        "planned_rounds": args.rounds,
        "planned_attempts": args.rounds * args.count,
        "completed_rounds": 0,
        "count": args.count,
        "concurrency": args.concurrency,
        "countries": countries,
        "max_price": args.max_price,
        "min_balance": args.min_balance,
        "balance_start": balance_start,
        "balance_end": balance_start,
        "balance_delta": 0.0,
        "started_at": utc_now(),
        "started_at_local": iso_local(),
        "stop_reason": "",
        "rounds": [],
        "preflight": {"grizzly": start_probe},
    }
    persist(out_dir, run)
    print(f"[preflight] health={health.get('status')} balance={balance_start} out={out_dir}", flush=True)

    stop_reason = ""
    for index in range(1, args.rounds + 1):
        country = countries[(index - 1) % len(countries)]
        try:
            probe = client.grizzly(country)
        except Exception as exc:
            stop_reason = f"第 {index} 轮前余额/库存探针失败: {exc}"
            print(f"[round {index}] probe failed: {exc}", flush=True)
            break
        balance_before = parse_balance(probe)
        stock = (probe.get("data") or {}).get("telegram_stock")
        cost = (probe.get("data") or {}).get("ref_cost")
        try:
            cost_f = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_f = None
        print(
            f"[round {index}/{args.rounds}] country={country.upper()} "
            f"balance={balance_before} stock={stock} cost={cost} "
            f"count={args.count} concurrency={args.concurrency}",
            flush=True,
        )
        if balance_before is not None and balance_before < args.min_balance:
            stop_reason = (
                f"第 {index} 轮前余额 {balance_before} 低于安全线 {args.min_balance}，停止继续扣费。"
            )
            break
        if (
            balance_before is not None
            and cost_f is not None
            and balance_before < cost_f * args.count * 0.15
        ):
            stop_reason = (
                f"第 {index} 轮前余额 {balance_before} 相对 {country.upper()} "
                f"参考价 {cost_f} x {args.count} 过低，停止以免大面积 NO_NUMBERS/扣费失败。"
            )
            break

        t0 = time.time()
        try:
            created = client.start_batch(country, args.count, args.concurrency, args.max_price)
        except Exception as exc:
            elapsed = time.time() - t0
            run["rounds"].append(
                {
                    "round": index,
                    "country": country,
                    "batch_id": None,
                    "submit_error": str(exc),
                    "elapsed_sec": elapsed,
                    "balance_before": balance_before,
                    "balance_after": balance_before,
                    "success": 0,
                    "failed": 0,
                    "filtered": 0,
                    "reasons": {"SUBMIT_ERROR": args.count},
                    "tasks": [],
                    "timed_out": False,
                }
            )
            persist(out_dir, run)
            print(f"[round {index}] submit failed: {exc}", flush=True)
            stop_reason = f"第 {index} 轮提交失败: {exc}"
            break

        batch_id = created.get("batch_id")
        print(f"[round {index}] submitted batch_id={batch_id} tasks={created.get('task_ids')}", flush=True)
        batch, tasks, timed_out = wait_batch(client, batch_id, args.poll, args.timeout)
        elapsed = time.time() - t0
        summary = summarize_tasks(tasks)
        try:
            after_probe = client.grizzly(country)
            balance_after = parse_balance(after_probe)
        except Exception:
            after_probe = None
            balance_after = None

        row = {
            "round": index,
            "country": country,
            "batch_id": batch_id,
            "created": created,
            "batch": {
                k: batch.get(k)
                for k in (
                    "batch_id", "status", "success", "failed", "running", "pending",
                    "precheck_intercepted", "no_number", "created_at", "updated_at",
                )
            },
            "elapsed_sec": elapsed,
            "timed_out": timed_out,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "stock_before": stock,
            "ref_cost": cost,
            "success": summary["success"],
            "failed": summary["failed"],
            "filtered": summary["filtered"],
            "reasons": summary["reasons"],
            "refunds": summary["refunds"],
            "statuses": summary["statuses"],
            "tasks": summary["tasks"],
        }
        run["rounds"].append(row)
        run["completed_rounds"] = index
        run["balance_end"] = balance_after if balance_after is not None else run.get("balance_end")
        if run.get("balance_start") is not None and run.get("balance_end") is not None:
            run["balance_delta"] = round(float(run["balance_end"]) - float(run["balance_start"]), 6)
        persist(out_dir, run)
        print(
            f"[round {index}] done in {elapsed:.1f}s success={summary['success']} "
            f"failed={summary['failed']} filtered={summary['filtered']} "
            f"reasons={summary['reasons']} refunds={summary['refunds']} "
            f"balance {balance_before} -> {balance_after} timed_out={timed_out}",
            flush=True,
        )
        if index < args.rounds and args.pause > 0:
            time.sleep(args.pause)

    run["finished_at"] = utc_now()
    run["finished_at_local"] = iso_local()
    run["stop_reason"] = stop_reason
    if run.get("balance_start") is not None and run.get("balance_end") is not None:
        run["balance_delta"] = round(float(run["balance_end"]) - float(run["balance_start"]), 6)
    persist(out_dir, run)
    print(f"[done] rounds={run['completed_rounds']} attempts={run['totals']['attempts']} "
          f"success={run['totals']['success']} report={out_dir / 'BATCH_STRESS_REPORT.md'}", flush=True)
    if stop_reason:
        print(f"[stop] {stop_reason}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
