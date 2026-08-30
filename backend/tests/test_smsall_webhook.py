"""SMSBazaar alert webhook：鉴权、低价过滤、自动开注册。"""
from __future__ import annotations

import hashlib
import hmac
import json
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


def _payload(items=None, service="telegram"):
    return {
        "schema": "smsall.alert.v1",
        "serviceKey": service,
        "items": items if items is not None else [{
            "type": "restock",
            "country": "IN",
            "countryName": "India",
            "priceUsd": 0.12,
            "currency": "USD",
            "stockFrom": 0,
            "stockTo": 18,
            "provider": "SMSTG",
            "providerCode": "P24",
        }],
    }


def _sniper_item(**overrides):
    base = {
        "type": "restock",
        "country": "CO",
        "countryName": "Colombia",
        "priceUsd": 0.30,
        "stockTo": 60,
        "provider": "SMSTG",
    }
    base.update(overrides)
    return base


def _cfg(**overrides):
    base = dict(
        smsall_auto_register=True,
        smsall_auto_max_price_usd=0.5,
        smsall_auto_count=3,
        smsall_auto_concurrency=3,
        smsall_auto_cooldown_seconds=600,
        smsall_auto_min_stock=1,
        smsall_auto_max_countries=2,
        smsall_sniper_enabled=True,
        smsall_sniper_count=10,
        smsall_sniper_concurrency=10,
        smsall_sniper_max_number_attempts=20,
        smsall_sniper_cooldown_seconds=60,
        smsall_sniper_max_countries=3,
        smsall_sniper_max_price_usd=None,
        smsall_sniper_use_item_price_as_max=True,
        hunt_max_total_leases=200,
        smsall_webhook_secret="unit-hook-secret",
        sms_provider="smsbower",
        sms_max_price=0.55,
        use_proxy_seller_auto=True,
        active_app_type="telegram_android",
        auto_set_2fa=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestSmsallVerifyAndDecide(unittest.TestCase):
    def setUp(self):
        from backend.app.services import smsall_webhook as mod

        mod.reset_state()
        self.mod = mod

    def test_hmac_and_bearer(self):
        raw = b'{"schema":"smsall.alert.v1"}'
        secret = "unit-hook-secret"
        digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        self.assertTrue(self.mod.verify_request(raw, "", f"sha256={digest}", secret))
        self.assertFalse(self.mod.verify_request(raw, "", "sha256=deadbeef", secret))
        self.assertTrue(self.mod.verify_request(raw, "Bearer unit-hook-secret", "", secret))
        self.assertFalse(self.mod.verify_request(raw, "Bearer other", "", secret))
        self.assertFalse(self.mod.verify_request(raw, "", "", secret))
        self.assertTrue(self.mod.verify_request(raw, "", "", ""))

    def test_cheap_restock_launches_once_per_country(self):
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            launches, records = self.mod.decide_launches(_payload([
                {"type": "restock", "country": "IN", "priceUsd": 0.12, "stockTo": 18, "provider": "A"},
                {"type": "new_listing", "country": "IN", "priceUsd": 0.13, "stockTo": 40, "provider": "B"},
                {"type": "restock", "country": "PH", "priceUsd": 0.28, "stockTo": 40, "provider": "C"},
            ]), _cfg())
        countries = [item["country"] for item in launches]
        self.assertEqual(countries, ["in", "ph"])
        self.assertEqual(launches[0]["price_usd"], 0.12)
        dup = [rec for rec in records if rec.get("reason") == "duplicate_country"]
        self.assertEqual(len(dup), 1)

    def test_expensive_and_disabled(self):
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            launches, records = self.mod.decide_launches(
                _payload([{"type": "restock", "country": "IN", "priceUsd": 0.99, "stockTo": 10}]),
                _cfg(smsall_auto_max_price_usd=0.5),
            )
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["reason"], "price_above_cap")

        with patch.object(self.mod, "_busy_task_count", return_value=0):
            launches, records = self.mod.decide_launches(_payload(), _cfg(smsall_auto_register=False))
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["action"], "received")
        self.assertEqual(records[0]["reason"], "awaiting_confirm")
        self.assertTrue(records[0].get("id"))

    def test_default_auto_is_off(self):
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            launches, records = self.mod.decide_launches(
                _payload(),
                SimpleNamespace(smsall_auto_max_price_usd=0.5, smsall_auto_min_stock=1),
            )
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["action"], "received")

    def test_delete_selected_and_clear_all(self):
        self.mod.remember_events([
            {"id": "a1", "country": "co", "action": "received"},
            {"id": "a2", "country": "in", "action": "received"},
            {"id": "a3", "country": "ph", "action": "received"},
        ])
        self.assertEqual(self.mod.event_count(), 3)
        self.assertEqual(self.mod.delete_events(event_ids=["a2"]), 1)
        ids = [item["id"] for item in self.mod.recent_events(10)]
        self.assertEqual(ids, ["a3", "a1"])
        self.assertEqual(self.mod.delete_events(clear_all=True), 2)
        self.assertEqual(self.mod.event_count(), 0)

    def test_non_telegram_ignored(self):
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            launches, records = self.mod.decide_launches(_payload(service="whatsapp"), _cfg())
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["reason"], "not_telegram")

    def test_cooldown_blocks_second_ingest(self):
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            first = self.mod.ingest(_payload(), _cfg(smsall_auto_cooldown_seconds=600))
            second = self.mod.ingest(_payload(), _cfg(smsall_auto_cooldown_seconds=600))
        self.assertEqual(len(first["launches"]), 1)
        self.assertEqual(second["launches"], [])
        self.assertEqual(second["events"][0]["reason"], "cooldown")


class TestSmsallSniper(unittest.TestCase):
    """狙击是独立通道：判定命中即按 10×20 开猎号，不看 smsall_auto_register。"""

    def setUp(self):
        from backend.app.services import smsall_webhook as mod

        mod.reset_state()
        self.mod = mod

    def _decide(self, payload, cfg=None, headers=None):
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            return self.mod.decide_launches(payload, cfg or _cfg(), headers=headers)

    def test_header_flag_triggers_sniper(self):
        for headers in (
            {"X-Smsall-Sniper": "1"},
            {"x-smsall-sniper": "true"},
            {"X-Smsall-Priority": "sniper"},
        ):
            self.mod.reset_state()
            launches, records = self._decide(_payload([_sniper_item()]), headers=headers)
            self.assertEqual(len(launches), 1, headers)
            self.assertTrue(launches[0]["sniper"])
            self.assertTrue(records[0]["sniper"])
            self.assertEqual(records[0]["source"], "sniper")

    def test_payload_source_triggers_sniper(self):
        payload = _payload([_sniper_item()])
        payload["source"] = "sniper"
        launches, records = self._decide(payload)
        self.assertEqual(len(launches), 1)
        self.assertTrue(launches[0]["sniper"])
        self.assertEqual(records[0]["action"], "launch")

    def test_item_level_markers_trigger_sniper(self):
        for marker in ({"sniper": True}, {"tags": ["hot", "sniper"]}, {"priority": "sniper"}):
            self.mod.reset_state()
            launches, _ = self._decide(_payload([_sniper_item(**marker)]))
            self.assertEqual(len(launches), 1, marker)
            self.assertTrue(launches[0]["sniper"])

    def test_sniper_launches_even_when_auto_register_off(self):
        cfg = _cfg(smsall_auto_register=False)
        launches, records = self._decide(_payload([_sniper_item(sniper=True)]), cfg)
        self.assertEqual(len(launches), 1)
        self.assertEqual(records[0]["action"], "launch")

    def test_sniper_launch_carries_hunt_params(self):
        launches, _ = self._decide(_payload([_sniper_item(sniper=True, priceUsd=0.2)]))
        launch = launches[0]
        self.assertEqual(launch["count"], 10)
        self.assertEqual(launch["concurrency"], 10)
        self.assertEqual(launch["max_number_attempts"], 20)
        # 出价 = item 单价上浮 10%
        self.assertAlmostEqual(launch["max_price"], 0.22, places=4)

    def test_sniper_disabled_only_records(self):
        cfg = _cfg(smsall_sniper_enabled=False)
        launches, records = self._decide(_payload([_sniper_item(sniper=True)]), cfg)
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["action"], "received")
        self.assertEqual(records[0]["reason"], "sniper_disabled")

    def test_sniper_price_cap_and_country_cap(self):
        cfg = _cfg(smsall_sniper_max_price_usd=0.25)
        launches, records = self._decide(_payload([_sniper_item(sniper=True, priceUsd=0.9)]), cfg)
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["reason"], "price_above_cap")

        self.mod.reset_state()
        cfg = _cfg(smsall_sniper_max_countries=2)
        launches, _ = self._decide(_payload([
            _sniper_item(sniper=True, country="CO", priceUsd=0.10),
            _sniper_item(sniper=True, country="IN", priceUsd=0.20),
            _sniper_item(sniper=True, country="PH", priceUsd=0.30),
        ]), cfg)
        self.assertEqual([item["country"] for item in launches], ["co", "in"])

    def test_mixed_payload_decides_separately(self):
        cfg = _cfg(smsall_auto_register=False)
        launches, records = self._decide(_payload([
            _sniper_item(sniper=True, country="CO"),
            {"type": "restock", "country": "IN", "priceUsd": 0.12, "stockTo": 18},
        ]), cfg)
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]["country"], "co")
        normal = [rec for rec in records if rec["country"] == "in"][0]
        self.assertEqual(normal["action"], "received")
        self.assertFalse(normal.get("sniper"))

    def test_sniper_cooldown_is_independent_from_normal(self):
        cfg = _cfg(smsall_auto_cooldown_seconds=600, smsall_sniper_cooldown_seconds=60)
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            # 普通通道先对 IN 开跑并进入 600s 冷却
            first = self.mod.ingest(_payload(), cfg)
            self.assertEqual(len(first["launches"]), 1)
            # 同国的狙击不应被普通冷却挡住
            sniper = self.mod.ingest(
                _payload([_sniper_item(country="IN", sniper=True)]),
                cfg,
            )
        self.assertEqual(len(sniper["launches"]), 1)
        self.assertTrue(sniper["launches"][0]["sniper"])

    def test_sniper_cooldown_blocks_repeat(self):
        cfg = _cfg(smsall_sniper_cooldown_seconds=60)
        payload = _payload([_sniper_item(sniper=True)])
        with patch.object(self.mod, "_busy_task_count", return_value=0):
            first = self.mod.ingest(payload, cfg)
            second = self.mod.ingest(payload, cfg)
        self.assertEqual(len(first["launches"]), 1)
        self.assertEqual(second["launches"], [])
        self.assertEqual(second["events"][0]["reason"], "cooldown")

    def test_sniper_ignores_busy_cap(self):
        with patch.object(self.mod, "_busy_task_count", return_value=90):
            launches, _ = self.mod.decide_launches(_payload([_sniper_item(sniper=True)]), _cfg())
        self.assertEqual(len(launches), 1)

    def test_non_telegram_sniper_still_ignored(self):
        launches, records = self._decide(
            _payload([_sniper_item(sniper=True)], service="whatsapp"),
        )
        self.assertEqual(launches, [])
        self.assertEqual(records[0]["reason"], "not_telegram")


class TestSmsallHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.client = TestClient(app)

    def setUp(self):
        from backend.app.services import smsall_webhook as mod

        mod.reset_state()
        self.mod = mod

    def test_unauthorized_without_signature(self):
        cfg = _cfg()
        with patch("backend.app.api.smsall_hooks.ConfigManager") as mgr, \
             patch("backend.app.api.smsall_hooks.resolve_secret", return_value="unit-hook-secret"):
            mgr.get_instance.return_value.config = cfg
            res = self.client.post("/hooks/smsall", json=_payload())
        self.assertEqual(res.status_code, 401)

    def test_hmac_accepted_and_schedules_batch(self):
        cfg = _cfg()
        raw = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"unit-hook-secret", raw, hashlib.sha256).hexdigest()
        with patch("backend.app.api.smsall_hooks.ConfigManager") as mgr, \
             patch("backend.app.api.smsall_hooks.resolve_secret", return_value="unit-hook-secret"), \
             patch("backend.app.api.smsall_hooks.RegistrationOrchestrator.run_batch", new_callable=AsyncMock) as run_batch, \
             patch.object(self.mod, "_busy_task_count", return_value=0):
            mgr.get_instance.return_value.config = cfg
            res = self.client.post(
                "/hooks/smsall",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Smsall-Signature": f"sha256={digest}",
                    "X-Smsall-Schema": "smsall.alert.v1",
                },
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("launched"), 1)
        self.assertEqual(body["launches"][0]["country"], "in")
        self.assertTrue(body["launches"][0].get("batch_id"))
        run_batch.assert_called()

    def test_hmac_semi_auto_does_not_schedule(self):
        cfg = _cfg(smsall_auto_register=False)
        raw = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"unit-hook-secret", raw, hashlib.sha256).hexdigest()
        with patch("backend.app.api.smsall_hooks.ConfigManager") as mgr, \
             patch("backend.app.api.smsall_hooks.resolve_secret", return_value="unit-hook-secret"), \
             patch("backend.app.api.smsall_hooks.RegistrationOrchestrator.run_batch", new_callable=AsyncMock) as run_batch, \
             patch.object(self.mod, "_busy_task_count", return_value=0):
            mgr.get_instance.return_value.config = cfg
            res = self.client.post(
                "/hooks/smsall",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Smsall-Signature": f"sha256={digest}",
                    "X-Smsall-Schema": "smsall.alert.v1",
                },
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("launched"), 0)
        run_batch.assert_not_called()
        events = self.mod.recent_events(10)
        self.assertEqual(events[0]["action"], "received")
        self.assertEqual(events[0]["country"], "in")

    def test_sniper_header_schedules_hunt_batch(self):
        cfg = _cfg(smsall_auto_register=False)
        body = _payload([_sniper_item()])
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"unit-hook-secret", raw, hashlib.sha256).hexdigest()
        with patch("backend.app.api.smsall_hooks.ConfigManager") as mgr, \
             patch("backend.app.api.smsall_hooks.resolve_secret", return_value="unit-hook-secret"), \
             patch("backend.app.api.smsall_hooks.RegistrationOrchestrator.run_batch", new_callable=AsyncMock) as run_batch, \
             patch.object(self.mod, "_busy_task_count", return_value=0):
            mgr.get_instance.return_value.config = cfg
            res = self.client.post(
                "/hooks/smsall",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Smsall-Signature": f"sha256={digest}",
                    "X-Smsall-Sniper": "1",
                },
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("launched"), 1)
        self.assertEqual(body.get("sniper_launched"), 1)
        self.assertTrue(body["launches"][0]["sniper"])
        self.assertEqual(body["launches"][0]["max_number_attempts"], 20)

        run_batch.assert_called_once()
        kwargs = run_batch.call_args.kwargs
        self.assertEqual(kwargs["max_number_attempts"], 20)
        self.assertEqual(kwargs["concurrency"], 10)
        self.assertEqual(len(kwargs["task_ids"]), 10)
        self.assertEqual(kwargs["country"], "co")
        self.assertEqual(kwargs["sms_provider"], "smsbower")
        self.assertAlmostEqual(kwargs["max_price"], 0.33, places=4)

        events = self.mod.recent_events(10)
        self.assertTrue(events[0]["sniper"])
        self.assertEqual(events[0]["action"], "launch")
        self.assertTrue(events[0]["batch_id"])

    def test_normal_auto_batch_stays_single_attempt(self):
        cfg = _cfg()
        raw = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"unit-hook-secret", raw, hashlib.sha256).hexdigest()
        with patch("backend.app.api.smsall_hooks.ConfigManager") as mgr, \
             patch("backend.app.api.smsall_hooks.resolve_secret", return_value="unit-hook-secret"), \
             patch("backend.app.api.smsall_hooks.RegistrationOrchestrator.run_batch", new_callable=AsyncMock) as run_batch, \
             patch.object(self.mod, "_busy_task_count", return_value=0):
            mgr.get_instance.return_value.config = cfg
            res = self.client.post(
                "/hooks/smsall",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Smsall-Signature": f"sha256={digest}",
                },
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("sniper_launched"), 0)
        kwargs = run_batch.call_args.kwargs
        self.assertEqual(kwargs["max_number_attempts"], 1)
        self.assertEqual(len(kwargs["task_ids"]), 3)

    def test_sniper_budget_clamped_when_over_lease_cap(self):
        cfg = _cfg(smsall_auto_register=False, hunt_max_total_leases=50)
        body = _payload([_sniper_item()])
        body["source"] = "sniper"
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"unit-hook-secret", raw, hashlib.sha256).hexdigest()
        with patch("backend.app.api.smsall_hooks.ConfigManager") as mgr, \
             patch("backend.app.api.smsall_hooks.resolve_secret", return_value="unit-hook-secret"), \
             patch("backend.app.api.smsall_hooks.RegistrationOrchestrator.run_batch", new_callable=AsyncMock) as run_batch, \
             patch.object(self.mod, "_busy_task_count", return_value=0):
            mgr.get_instance.return_value.config = cfg
            res = self.client.post(
                "/hooks/smsall",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Smsall-Signature": f"sha256={digest}",
                },
            )
        self.assertEqual(res.status_code, 200)
        # 10 路 × 20 次 = 200 > 50，attempts 被裁到 5，响应如实回报
        self.assertEqual(run_batch.call_args.kwargs["max_number_attempts"], 5)
        self.assertEqual(res.json()["launches"][0]["max_number_attempts"], 5)
        self.assertEqual(res.json()["launches"][0]["planned_leases"], 50)

    def test_trial_starts_batch_with_threads(self):
        self.mod.remember_events([{
            "id": "evt-co-1",
            "action": "received",
            "country": "co",
            "country_name": "Colombia",
            "price_usd": 0.12,
            "type": "restock",
        }])
        with patch("backend.app.api.smsall_hooks.RegistrationOrchestrator.run_batch", new_callable=AsyncMock) as run_batch:
            res = self.client.post("/api/smsall/trial", json={
                "event_id": "evt-co-1",
                "country": "CO",
                "count": 2,
                "concurrency": 2,
            })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("country"), "co")
        self.assertEqual(body.get("count"), 2)
        self.assertEqual(body.get("concurrency"), 2)
        self.assertTrue(body.get("batch_id"))
        run_batch.assert_called()
        stored = self.mod.get_event("evt-co-1")
        self.assertEqual(stored["action"], "trial")
        self.assertEqual(stored["batch_id"], body["batch_id"])

    def test_hooks_path_is_public_when_auth_enabled(self):
        from backend.app.services.auth import path_requires_auth

        self.assertFalse(path_requires_auth("/hooks/smsall"))
        self.assertTrue(path_requires_auth("/api/smsall/status"))
        self.assertTrue(path_requires_auth("/api/smsall/trial"))
        self.assertTrue(path_requires_auth("/api/smsall/events/delete"))

    def test_delete_events_http(self):
        self.mod.remember_events([
            {"id": "del-1", "country": "co", "action": "received"},
            {"id": "del-2", "country": "in", "action": "received"},
        ])
        res = self.client.post("/api/smsall/events/delete", json={"event_ids": ["del-1"]})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body.get("deleted"), 1)
        self.assertEqual(body.get("remaining"), 1)
        res = self.client.post("/api/smsall/events/delete", json={"clear_all": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("deleted"), 1)
        self.assertEqual(self.mod.event_count(), 0)
