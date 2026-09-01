#!/usr/bin/env python3
"""code_delivery_mode A/B：对比两种模式下 sendCode 走 SMS 还是 App。

每一轮把 ``code_delivery_mode`` 临时改成指定值，提交一批注册任务，跑完再改下一轮，
最后无条件把 config.json 恢复成脚本启动时的快照。

``--max-number-attempts`` 默认 2：本地黑名单里已有上千个「只投 App」的号，取到就
直接退订，不产生 sendCode 样本；给一次换号机会才能保证每一路都真的发过一次码。

样本从任务日志解析，只认服务端真实回包：

- ``挑战已由服务端下发! 分发通道类型: X (type=X next_type=Y timeout=Z)``
- ``[验证码通道] 通道策略=...`` 用来交叉校验本轮真的按预期模式发的码

没走到 sendCode 的号（无库存 / 预检拦截 / 本地黑名单 / 代理故障）单独归类，
不混进 SMS-vs-App 的分母。

用法::

    python3 backend/scripts/run_code_delivery_ab.py \
        --country iq --count 10 --concurrency 5 \
        --sms-provider smsbower --max-price 0.4 \
        --modes balanced,push_required --out-dir data/ab_reports
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BASE = os.environ.get("EDGENODE_API_BASE", "http://127.0.0.1:8000")
TERMINAL_STATUSES = {"success", "failed", "filtered", "cancelled", "canceled"}

DELIVERY_RE = re.compile(
    r"分发通道类型:\s*(?P<name>\S+)\s*\(type=(?P<type>\S+)\s+next_type=(?P<next>\S+)\s+timeout=(?P<timeout>\S+)\)"
)
RESEND_RE = re.compile(r"auth\.resendCode 已返回，新分发通道类型:\s*(?P<name>\S+)")
PLAN_RE = re.compile(r"\[验证码通道\]\s*通道策略=(?P<mode>\w+)，申请Push=(?P<req>\S)，attach_token=(?P<attach>\S)")
EGRESS_RE = re.compile(r"(?:出口拓扑: IP=|出口 IP |egress_ip=)(?P<ip>[0-9a-fA-F:.]+)")

NO_SENDCODE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("LOCAL_BANNED", ("本地黑名单", "LOCAL_BANNED")),
    ("PRECHECK_REGISTERED", ("PRECHECK_PHONE_ALREADY_REGISTERED", "预检判定该号已注册")),
    ("NO_NUMBERS", ("NO_NUMBERS", "无可用号码", "noNumber", "NO_NUMBER")),
    ("NO_BALANCE", ("NO_BALANCE", "余额不足")),
    ("PROXY_UNAVAILABLE", ("没有可用于注册的节点", "代理", "ProxyError")),
    ("ATTESTATION_FAILED", ("Attestation", "Integrity", "Push Token 申请失败")),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiClient:
    """带 Session cookie 的最小 API 客户端（控制台默认开启鉴权）。"""

    def __init__(self, base: str, username: str, password: Optional[str]):
        self.base = base.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        if password:
            self.request("POST", "/api/auth/login", {"username": username, "password": password})

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 90.0) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {body[:400]}") from exc

    def get_config(self) -> Dict[str, Any]:
        return self.request("GET", "/api/config")

    def set_delivery_mode(self, mode: str) -> str:
        config = self.get_config()
        config["code_delivery_mode"] = mode
        saved = self.request("POST", "/api/config", config)
        return str(saved.get("code_delivery_mode"))

    def put_config(self, config: Dict[str, Any]) -> str:
        saved = self.request("POST", "/api/config", config)
        return str(saved.get("code_delivery_mode"))

    def start_batch(self, **kwargs: Any) -> Dict[str, Any]:
        return self.request("POST", "/api/register/batch", kwargs)

    def get_batch(self, batch_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/api/register/batches/{batch_id}")

    def list_tasks(self, batch_id: str) -> List[Dict[str, Any]]:
        data = self.request("GET", f"/api/register/tasks?batch_id={urllib.parse.quote(batch_id)}&include_logs=true")
        if isinstance(data, list):
            return data
        return data.get("tasks") or []

    def get_task(self, task_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/api/register/tasks/{task_id}")


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """报告会随仓库留档，号码只保留国家段与尾号。"""
    if not phone:
        return phone
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 7:
        return "***"
    return f"+{digits[:4]}****{digits[-3:]}"


def classify_no_sendcode(logs: List[str], error: Optional[str]) -> str:
    blob = "\n".join(logs) + "\n" + str(error or "")
    for label, tokens in NO_SENDCODE_RULES:
        if any(token in blob for token in tokens):
            return label
    return "OTHER_NO_SENDCODE"


def bucket_for(sent_code_type: str) -> str:
    if "App" in sent_code_type:
        return "app"
    if "Sms" in sent_code_type:
        return "sms"
    return "other_type"


def parse_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """一个任务可能连续租多个号（猎号跳过黑名单号），每次 sendCode 都是一个独立样本。"""
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
    egress = EGRESS_RE.search(blob)
    leased = blob.count("成功获取端点通信句柄")
    blacklisted = blob.count("[号码黑名单拦截]")

    return {
        "task_id": task.get("task_id") or task.get("id"),
        "status": task.get("status"),
        "phone": mask_phone(task.get("phone")),
        "leased_numbers": leased,
        "blacklist_skips": blacklisted,
        "samples": samples,
        "resend_types": [m.group("name") for m in RESEND_RE.finditer(blob)],
        "plan_modes": [p["mode"] for p in plans],
        "plan_attached_token": [p["attach"] for p in plans],
        "egress_ip": egress.group("ip") if egress else None,
        "no_sendcode_reason": classify_no_sendcode(logs, task.get("error")) if not samples else None,
        "error": task.get("error") or task.get("message"),
        "log_lines": len(logs),
    }


def wait_batch(
    client: ApiClient,
    batch_id: str,
    poll: float,
    timeout: float,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    started = time.time()
    while True:
        batch = client.get_batch(batch_id)
        tasks = client.list_tasks(batch_id)
        done = tasks and all(str(t.get("status")) in TERMINAL_STATUSES for t in tasks)
        idle = int(batch.get("running") or 0) == 0 and int(batch.get("pending") or 0) == 0
        if done and idle:
            return batch, tasks, False
        if time.time() - started > timeout:
            return batch, tasks, True
        alive = sum(1 for t in tasks if str(t.get("status")) not in TERMINAL_STATUSES)
        print(
            f"    [{int(time.time() - started):>4}s] running={batch.get('running')} "
            f"pending={batch.get('pending')} unfinished={alive}/{len(tasks)}",
            flush=True,
        )
        time.sleep(poll)


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    samples = [s for r in rows for s in r["samples"]]
    buckets = Counter(s["bucket"] for s in samples)
    sms = buckets.get("sms", 0)
    app = buckets.get("app", 0)
    graded = len(samples)
    return {
        "tasks": len(rows),
        "leased_numbers": sum(r["leased_numbers"] for r in rows),
        "blacklist_skips": sum(r["blacklist_skips"] for r in rows),
        "sendcode_samples": graded,
        "sms": sms,
        "app": app,
        "other_type": buckets.get("other_type", 0),
        "tasks_without_sendcode": sum(1 for r in rows if not r["samples"]),
        "no_sendcode_reasons": dict(Counter(r["no_sendcode_reason"] for r in rows if r["no_sendcode_reason"])),
        "next_type_none": sum(1 for s in samples if s["next_type"] == "None"),
        "app_next_type_none": sum(1 for s in samples if s["bucket"] == "app" and s["next_type"] == "None"),
        "sms_rate": round(sms / graded, 4) if graded else None,
        "sent_code_types": dict(Counter(s["sent_code_type"] for s in samples)),
        "plan_modes": dict(Counter(m for r in rows for m in r["plan_modes"])),
        "attached_token": dict(Counter(a for r in rows for a in r["plan_attached_token"])),
        "statuses": dict(Counter(str(r["status"]) for r in rows)),
        "egress_ips": sorted({r["egress_ip"] for r in rows if r["egress_ip"]}),
        "success": sum(1 for r in rows if str(r["status"]) == "success"),
    }


def collect_round(client: ApiClient, mode: str, batch_id: str, elapsed: Optional[float] = None) -> Dict[str, Any]:
    """从已跑完的批次重建一轮结果，用于中断后续跑而不重复烧号。"""
    batch = client.get_batch(batch_id)
    tasks = client.list_tasks(batch_id)
    rows = [parse_task(client.get_task(t.get("task_id") or t.get("id"))) for t in tasks]
    return {
        "mode": mode,
        "batch_id": batch_id,
        "timed_out": False,
        "elapsed_seconds": elapsed,
        "batch_status": batch.get("status"),
        "summary": summarize(rows),
        "rows": rows,
    }


def run_round(client: ApiClient, mode: str, args: argparse.Namespace) -> Dict[str, Any]:
    applied = client.set_delivery_mode(mode)
    if applied != mode:
        raise RuntimeError(f"code_delivery_mode 未写入成功: 期望 {mode}，实际 {applied}")
    print(f"\n=== Round {mode} === code_delivery_mode={applied} @ {utc_now()}", flush=True)

    started = time.time()
    batch = client.start_batch(
        country=args.country,
        app_type=args.app_type,
        count=args.count,
        concurrency=args.concurrency,
        sms_provider=args.sms_provider,
        max_price=args.max_price,
        max_number_attempts=args.max_number_attempts,
        proxy_mode=args.proxy_mode,
    )
    batch_id = batch.get("batch_id")
    print(f"    batch_id={batch_id} {batch.get('message')}", flush=True)

    final_batch, tasks, timed_out = wait_batch(client, batch_id, args.poll, args.batch_timeout)
    rows = [parse_task(client.get_task(t.get("task_id") or t.get("id"))) for t in tasks]
    summary = summarize(rows)
    print(
        f"    -> SMS={summary['sms']} App={summary['app']} 发码样本={summary['sendcode_samples']} "
        f"黑名单跳过={summary['blacklist_skips']} SMS率={summary['sms_rate']} "
        f"耗时={int(time.time() - started)}s",
        flush=True,
    )
    return {
        "mode": mode,
        "batch_id": batch_id,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.time() - started, 1),
        "batch_status": final_batch.get("status"),
        "summary": summary,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--username", default=os.environ.get("EDGENODE_AUTH_USER", "s7word"))
    parser.add_argument("--password", default=os.environ.get("EDGENODE_AUTH_PASSWORD"))
    parser.add_argument("--password-file", default="data/edgenode_auth_password")
    parser.add_argument("--country", default="iq")
    parser.add_argument("--app-type", default="telegram_android")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--sms-provider", default="smsbower")
    parser.add_argument("--max-price", type=float, default=0.4)
    parser.add_argument("--proxy-mode", default="custom_pool")
    parser.add_argument(
        "--max-number-attempts",
        type=int,
        default=2,
        help="每任务最多租号次数：>1 时命中本地黑名单的号可以换一个，保证这一路能产出真实 sendCode 样本",
    )
    parser.add_argument("--modes", default="balanced,push_required")
    parser.add_argument(
        "--reuse-round",
        action="append",
        default=[],
        metavar="MODE:BATCH_ID",
        help="复用已跑完批次的一轮结果（不再租号），可重复传入；与 --modes 的轮次合并输出",
    )
    parser.add_argument("--poll", type=float, default=10.0)
    parser.add_argument("--batch-timeout", type=float, default=1800.0)
    parser.add_argument("--out-dir", default="data/ab_reports")
    args = parser.parse_args()

    password = args.password
    if not password and args.password_file and Path(args.password_file).exists():
        password = Path(args.password_file).read_text(encoding="utf-8").strip()

    client = ApiClient(args.base, args.username, password)
    original_config = client.get_config()
    original_mode = str(original_config.get("code_delivery_mode"))
    print(f"启动快照 code_delivery_mode={original_mode}", flush=True)

    rounds: List[Dict[str, Any]] = []
    for spec in args.reuse_round:
        mode, _, batch_id = spec.partition(":")
        if not mode or not batch_id:
            raise SystemExit(f"--reuse-round 需要 MODE:BATCH_ID 形式，收到 {spec!r}")
        print(f"复用已有批次 {batch_id} 作为 {mode} 轮", flush=True)
        rounds.append(collect_round(client, mode.strip(), batch_id.strip()))

    modes = [m.strip() for m in args.modes.split(",") if m.strip()] if args.modes else []
    try:
        for mode in modes:
            rounds.append(run_round(client, mode, args))
    finally:
        restored = client.put_config(original_config)
        print(f"\n已恢复 config.json code_delivery_mode={restored}", flush=True)

    report = {
        "generated_at": utc_now(),
        "country": args.country,
        "sms_provider": args.sms_provider,
        "max_price": args.max_price,
        "count_per_round": args.count,
        "concurrency": args.concurrency,
        "proxy_mode": args.proxy_mode,
        "original_mode": original_mode,
        "rounds": rounds,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"code_delivery_ab_{args.country}_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 84)
    print(
        f"{'mode':<16}{'租号':>6}{'发码样本':>10}{'SMS':>6}{'App':>6}"
        f"{'黑名单跳过':>12}{'next=None':>11}{'SMS率':>9}"
    )
    for item in rounds:
        s = item["summary"]
        rate = "-" if s["sms_rate"] is None else f"{s['sms_rate'] * 100:.1f}%"
        print(
            f"{item['mode']:<16}{s['leased_numbers']:>6}{s['sendcode_samples']:>10}"
            f"{s['sms']:>6}{s['app']:>6}{s['blacklist_skips']:>12}{s['next_type_none']:>11}{rate:>9}"
        )
    print("=" * 84)
    print(f"报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
