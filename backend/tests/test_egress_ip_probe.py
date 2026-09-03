"""发码前真实出口 IP 探测与复用告警。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from backend.app.services.proxyseller import (  # noqa: E402
    EgressIpRegistry,
    ProxySellerService,
    _parse_ip_probe_payload,
)
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.telegram_apps import to_telethon_proxy  # noqa: E402


class TestEgressIpRegistry(unittest.TestCase):
    def setUp(self):
        EgressIpRegistry.reset_for_tests()

    def test_note_reports_other_holders(self):
        self.assertEqual(EgressIpRegistry.note("1.1.1.1", "t1"), [])
        self.assertEqual(EgressIpRegistry.note("1.1.1.1", "t2"), ["t1"])
        EgressIpRegistry.release("1.1.1.1", "t1")
        self.assertEqual(EgressIpRegistry.note("1.1.1.1", "t3"), ["t2"])


class TestParseIpProbePayload(unittest.TestCase):
    def test_plain_text_ip(self):
        parsed = _parse_ip_probe_payload("203.0.113.10\n")
        self.assertEqual(parsed["ip"], "203.0.113.10")

    def test_ip_sb_geoip_json(self):
        parsed = _parse_ip_probe_payload({
            "ip": "186.189.99.200",
            "country_code": "CL",
            "country": "Chile",
            "city": "Santiago",
            "organization": "ISP",
        })
        self.assertEqual(parsed["ip"], "186.189.99.200")
        self.assertEqual(parsed["country_code"], "CL")


class TestToTelethonProxy(unittest.TestCase):
    def test_rdns_enabled(self):
        bound = to_telethon_proxy({
            "proxy_type": "socks5",
            "addr": "10.0.0.1",
            "port": 1080,
            "username": "u",
            "password": "p",
        })
        self.assertEqual(bound["proxy_type"], "socks5")
        self.assertTrue(bound["rdns"])


class TestProbeAndLogLiveEgress(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        EgressIpRegistry.reset_for_tests()

    async def test_logs_live_ip_and_reuse_warning(self):
        logs = []

        class Mgr:
            async def append_log(self, task_id, message):
                logs.append(message)

        proxy = {
            "proxy_type": "socks5",
            "addr": "res.proxy-seller.com",
            "port": 10000,
            "username": "u",
            "password": "p",
            "egress_ip": "198.51.100.1",
        }
        EgressIpRegistry.note("203.0.113.5", "other-task")

        with patch.object(
            ProxySellerService,
            "test_proxy_connectivity",
            new=AsyncMock(return_value={
                "success": True,
                "ip": "203.0.113.5",
                "country_code": "IQ",
                "country": "Iraq",
                "city": "Baghdad",
                "org": "Test-ISP",
                "latency_ms": 42.0,
                "probe_url": "https://api.ip.sb/geoip",
            }),
        ):
            ip, probe = await RegistrationOrchestrator._probe_and_log_live_egress(
                proxy, "task-A", Mgr()
            )

        self.assertEqual(ip, "203.0.113.5")
        self.assertTrue(probe["success"])
        self.assertEqual(proxy["egress_ip"], "203.0.113.5")
        blob = "\n".join(logs)
        self.assertIn("真实出口 IP=203.0.113.5", blob)
        self.assertIn("出口复用", blob)
        self.assertIn("other-task", blob)
        self.assertIn("缓存标注 IP=198.51.100.1", blob)

    async def test_missing_proxy_warns(self):
        logs = []

        class Mgr:
            async def append_log(self, task_id, message):
                logs.append(message)

        ip, probe = await RegistrationOrchestrator._probe_and_log_live_egress(
            None, "task-B", Mgr()
        )
        self.assertIsNone(ip)
        self.assertFalse(probe.get("success"))
        self.assertTrue(any("无有效代理" in x for x in logs))


if __name__ == "__main__":
    unittest.main()
