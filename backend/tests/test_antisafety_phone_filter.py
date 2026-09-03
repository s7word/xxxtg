"""AntiSafety 号码过滤与 REGHelp Push 路径解耦。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.services.attestation_gateway import AttestationGatewayService


def _cfg(**overrides):
    base = dict(
        reghelp_enabled=True,
        reghelp_api_key="reghelp-key-ok",
        reghelp_base_urls=["https://api.reghelp.net"],
        reghelp_connect_timeout=6.0,
        reghelp_total_timeout=20.0,
        antisafety_enabled=False,  # Push 不用 AntiSafety
        antisafety_api_key="antisafety-key-ok",
        antisafety_base_urls=["https://api.antisafety.net"],
        antisafety_reporting_base_urls=["https://reporting.antisafety.net"],
        antisafety_connect_timeout=6.0,
        antisafety_total_timeout=20.0,
        antisafety_phone_filter_enabled=True,
        antisafety_phone_filter_statuses=["BANNED", "ALREADY_REGISTERED", "FLOOD_WAIT"],
        attestation_provider_mode="reghelp_only",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestAntiSafetyPhoneFilterDecoupling(unittest.IsolatedAsyncioTestCase):
    @patch("backend.app.services.attestation_gateway.AntiSafetyService")
    @patch("backend.app.services.attestation_gateway.RegHelpService")
    async def test_reghelp_only_still_builds_antisafety_for_phone_filter(
        self, reghelp_cls, antisafety_cls
    ):
        reghelp_cls.return_value = MagicMock()
        antisafety_cls.return_value = MagicMock()
        gw = AttestationGatewayService(_cfg())
        self.assertIsNotNone(gw.reghelp)
        self.assertIsNotNone(gw.antisafety)
        # Push 调度不应包含 AntiSafety
        order_names = [name for name, _ in gw._provider_order()]
        self.assertEqual(order_names, ["reghelp"])

    @patch("backend.app.services.attestation_gateway.AntiSafetyService")
    @patch("backend.app.services.attestation_gateway.RegHelpService")
    async def test_phone_filter_calls_antisafety_check(self, reghelp_cls, antisafety_cls):
        reghelp_cls.return_value = MagicMock()
        as_svc = MagicMock()
        as_svc.check_phone_history = AsyncMock(
            return_value={"id": "c1", "statuses": ["BANNED", "FLOOD_WAIT"]}
        )
        antisafety_cls.return_value = as_svc
        gw = AttestationGatewayService(_cfg())
        logs = []

        async def log_cb(msg):
            logs.append(msg)

        data = await gw.check_phone_history("56912345678", "aid-1", log_callback=log_cb)
        self.assertEqual(data["id"], "c1")
        as_svc.check_phone_history.assert_awaited_once()
        self.assertTrue(any("[AntiSafety 号码过滤]" in m and "/check" in m for m in logs))
        self.assertTrue(any("响应 ok" in m and "BANNED" in m for m in logs))

        hits = AttestationGatewayService.matched_phone_filter_statuses(
            data, gw.phone_filter_reject_statuses()
        )
        self.assertEqual(hits, ["BANNED", "FLOOD_WAIT"])

    @patch("backend.app.services.attestation_gateway.AntiSafetyService")
    @patch("backend.app.services.attestation_gateway.RegHelpService")
    async def test_missing_key_logs_and_skips(self, reghelp_cls, antisafety_cls):
        reghelp_cls.return_value = MagicMock()
        gw = AttestationGatewayService(_cfg(antisafety_api_key=""))
        self.assertIsNone(gw.antisafety)
        logs = []

        async def log_cb(msg):
            logs.append(msg)

        data = await gw.check_phone_history("56912345678", "aid-1", log_callback=log_cb)
        self.assertIsNone(data)
        self.assertTrue(any("[AntiSafety 号码过滤]" in m and "antisafety_api_key" in m for m in logs))
        antisafety_cls.assert_not_called()

    def test_matched_statuses_already_registered(self):
        hits = AttestationGatewayService.matched_phone_filter_statuses(
            {"statuses": ["already_registered", "OTHER"]},
            ["BANNED", "ALREADY_REGISTERED", "FLOOD_WAIT"],
        )
        self.assertEqual(hits, ["ALREADY_REGISTERED"])


if __name__ == "__main__":
    unittest.main()
