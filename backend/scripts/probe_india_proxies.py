#!/usr/bin/env python3
"""Probe Proxy-Seller India residential nodes (SOCKS5 + HTTP)."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.proxyseller import (  # noqa: E402
    STATIC_INDIA_PASSWORD,
    STATIC_INDIA_PORTS,
    STATIC_INDIA_USERNAME,
    STATIC_RESIDENTIAL_HOST,
    ProxySellerService,
)

PROTOCOLS = ("socks5", "http")


def _node(protocol: str, port: int) -> Dict[str, Any]:
    return {
        "proxy_type": protocol,
        "addr": STATIC_RESIDENTIAL_HOST,
        "port": port,
        "username": STATIC_INDIA_USERNAME,
        "password": STATIC_INDIA_PASSWORD,
        "country": "India",
        "country_code": "in",
    }


async def _probe_one(protocol: str, port: int) -> Dict[str, Any]:
    started = time.perf_counter()
    result = await ProxySellerService.test_proxy_connectivity(_node(protocol, port), timeout=18.0)
    row = {
        "host": STATIC_RESIDENTIAL_HOST,
        "port": port,
        "protocol": protocol,
        "success": bool(result.get("success")),
        "ip": result.get("ip"),
        "country": result.get("country"),
        "country_code": result.get("country_code"),
        "city": result.get("city"),
        "region": result.get("region"),
        "org": result.get("org"),
        "latency_ms": result.get("latency_ms"),
        "total_ms": result.get("total_ms") or round((time.perf_counter() - started) * 1000, 1),
        "error": result.get("error"),
        "india": str(result.get("country_code") or "").upper() == "IN"
        or "india" in str(result.get("country") or "").lower(),
    }
    return row


async def main() -> int:
    semaphore = asyncio.Semaphore(4)

    async def _guarded(protocol: str, port: int) -> Dict[str, Any]:
        async with semaphore:
            return await _probe_one(protocol, port)

    tasks = [_guarded(protocol, port) for port in STATIC_INDIA_PORTS for protocol in PROTOCOLS]
    rows: List[Dict[str, Any]] = await asyncio.gather(*tasks)
    rows.sort(key=lambda item: (item["port"], item["protocol"]))
    summary = {
        "host": STATIC_RESIDENTIAL_HOST,
        "username": STATIC_INDIA_USERNAME,
        "ports": list(STATIC_INDIA_PORTS),
        "tested": len(rows),
        "healthy": sum(1 for row in rows if row["success"]),
        "india_confirmed": sum(1 for row in rows if row.get("india")),
        "results": rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
