"""代理用途角色隔离、凭证库探针激活与显式配对路由单元测试。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.models.schemas import (  # noqa: E402
    AppConfigModel,
    CustomProxyItem,
    CustomProxyUpdateItemRequest,
    PhonePrecheckStatusResponse,
    RegisterTaskRequest,
    ToggleVaultProbeRequest,
    ToggleVaultProbeResponse,
    VaultAccountItem,
    VaultAccountListResponse,
)
from backend.app.services.account_vault import (  # noqa: E402
    AccountVaultService,
    is_account_probe_active,
)
from backend.app.services.phone_precheck import PhonePrecheckService  # noqa: E402
from backend.app.services.proxy_manager import (  # noqa: E402
    parse_proxy_line,
    parse_proxy_text,
    select_proxy_for_precheck,
    select_proxy_for_registration,
    to_persist_item,
    update_custom_proxy_item,
)
from backend.app.services.proxyseller import normalize_custom_proxy_item  # noqa: E402
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)


class FakeConfigManager:
    def __init__(self, **overrides):
        payload = {
            "target_country": "cl",
            "use_proxy_seller_auto": False,
            "proxy_seller_key": "",
            "custom_proxies": [],
            "active_precheck_probe_ids": [],
            "precheck_probes_configured": False,
        }
        payload.update(overrides)
        self._config = AppConfigModel(**payload)

    @property
    def config(self):
        return self._config

    def save_config(self, new_config):
        self._config = new_config
        return self._config


def _proxy(addr, port, role="all", assigned_country=None, healthy=True, latency_ms=20, **extra):
    item = to_persist_item({
        "addr": addr,
        "port": port,
        "username": extra.get("username"),
        "password": extra.get("password", "p"),
        "role": role,
        "assigned_country": assigned_country,
        "healthy": healthy,
        "latency_ms": latency_ms,
        "country_code": extra.get("country_code") or assigned_country,
        "source": "custom",
    })
    return normalize_custom_proxy_item(item) or item


class TestProxyRoleParsing(unittest.TestCase):
    def test_hash_role_suffix_and_country(self):
        reg = parse_proxy_line("10.0.0.1:1080:u:p#registration")
        self.assertEqual(reg["role"], "registration")
        self.assertEqual(reg["addr"], "10.0.0.1")

        pre = parse_proxy_line("10.0.0.2:1080:u:p#precheck:cl")
        self.assertEqual(pre["role"], "precheck")
        self.assertEqual(pre["assigned_country"], "cl")

        generic = parse_proxy_line("10.0.0.3:1080")
        self.assertEqual(generic["role"], "all")
        self.assertIsNone(generic["assigned_country"])

    def test_unknown_hash_is_not_stripped_as_role(self):
        item = parse_proxy_line("10.0.0.4:1080:u:p#ssword")
        self.assertEqual(item["password"], "p#ssword")
        self.assertEqual(item["role"], "all")

    def test_default_role_applies_when_no_tag(self):
        item = parse_proxy_line("10.0.0.5:1080:u:p", default_role="precheck", default_country="in")
        self.assertEqual(item["role"], "precheck")
        self.assertEqual(item["assigned_country"], "in")

    def test_schema_defaults_and_validators(self):
        item = CustomProxyItem(addr="1.1.1.1", port=1080, role="REGISTRATION", assigned_country="CL")
        self.assertEqual(item.role, "registration")
        self.assertEqual(item.assigned_country, "cl")
        req = RegisterTaskRequest(proxy_mode="EXPLICIT", proxy_id="custom-abc")
        self.assertEqual(req.proxy_mode, "explicit")
        self.assertEqual(req.proxy_id, "custom-abc")
        cfg = AppConfigModel()
        self.assertEqual(cfg.active_precheck_probe_ids, [])
        self.assertFalse(cfg.precheck_probes_configured)


class TestProxyRoleIsolation(unittest.TestCase):
    def test_registration_skips_precheck_only_nodes(self):
        items = [
            _proxy("1.1.1.1", 1080, role="precheck", assigned_country="cl", latency_ms=5),
            _proxy("2.2.2.2", 1080, role="registration", assigned_country="cl", latency_ms=40),
            _proxy("3.3.3.3", 1080, role="all", assigned_country=None, latency_ms=10),
        ]
        with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=items):
            chosen = select_proxy_for_registration("cl")
        self.assertEqual(chosen["addr"], "2.2.2.2")
        self.assertNotEqual(chosen["role"], "precheck")

    def test_precheck_skips_registration_only_nodes(self):
        items = [
            _proxy("1.1.1.1", 1080, role="registration", assigned_country="cl", latency_ms=5),
            _proxy("2.2.2.2", 1080, role="precheck", assigned_country="cl", latency_ms=80),
            _proxy("3.3.3.3", 1080, role="all", assigned_country=None, latency_ms=10),
        ]
        with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=items):
            chosen = select_proxy_for_precheck("cl")
        self.assertEqual(chosen["addr"], "2.2.2.2")
        self.assertEqual(chosen["role"], "precheck")

    def test_registration_prefers_bound_country_then_global(self):
        items = [
            _proxy("9.9.9.9", 1080, role="registration", assigned_country="in", latency_ms=1),
            _proxy("8.8.8.8", 1080, role="all", assigned_country=None, latency_ms=50),
        ]
        with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=items):
            chile = select_proxy_for_registration("cl")
            india = select_proxy_for_registration("in")
        self.assertEqual(chile["addr"], "8.8.8.8")
        self.assertEqual(india["addr"], "9.9.9.9")

    def test_explicit_proxy_id_ignores_role_and_country(self):
        items = [
            _proxy("1.1.1.1", 1080, role="precheck", assigned_country="in", extra_id=True),
        ]
        items[0]["id"] = "custom-explicit-1"
        with patch("backend.app.services.proxy_manager.list_custom_proxies", return_value=items):
            chosen = select_proxy_for_registration("cl", proxy_id="custom-explicit-1")
        self.assertEqual(chosen["addr"], "1.1.1.1")
        self.assertEqual(chosen["role"], "precheck")

    def test_update_item_persists_role_and_country(self):
        fake = FakeConfigManager(custom_proxies=[
            {"addr": "10.0.0.8", "port": 1080, "username": "u", "password": "p", "role": "all"},
        ])

        def _load():
            return [item.model_dump() for item in fake.config.custom_proxies]

        with patch("backend.app.services.proxy_manager._config_manager", return_value=fake), \
             patch("backend.app.services.proxy_manager.load_custom_proxy_items", side_effect=_load):
            result = update_custom_proxy_item(
                addr="10.0.0.8",
                port=1080,
                username="u",
                role="precheck",
                assigned_country="id",
            )
        self.assertTrue(result["success"])
        saved = fake.config.custom_proxies[0]
        self.assertEqual(saved.role, "precheck")
        self.assertEqual(saved.assigned_country, "id")

    def test_batch_parse_mixed_roles(self):
        parsed = parse_proxy_text(
            "10.1.1.1:1080:u:p#registration\n"
            "10.1.1.2:1080:u:p#precheck:in\n"
            "10.1.1.3:1080:u:p\n"
        )
        roles = {item["addr"]: item["role"] for item in parsed["proxies"]}
        self.assertEqual(roles["10.1.1.1"], "registration")
        self.assertEqual(roles["10.1.1.2"], "precheck")
        self.assertEqual(roles["10.1.1.3"], "all")


class TestVaultProbeActivation(unittest.TestCase):
    def test_default_activates_session_accounts_until_configured(self):
        self.assertTrue(is_account_probe_active("acc-1", True, config=AppConfigModel()))
        self.assertFalse(is_account_probe_active("acc-1", False, config=AppConfigModel()))
        configured = AppConfigModel(
            precheck_probes_configured=True,
            active_precheck_probe_ids=["acc-keep"],
        )
        self.assertTrue(is_account_probe_active("acc-keep", True, config=configured))
        self.assertFalse(is_account_probe_active("acc-other", True, config=configured))

    def test_toggle_probe_persists_allowlist(self):
        fake = FakeConfigManager()
        session_acc = VaultAccountItem(
            account_id="probe-a",
            source="lod_user",
            phone="+918310013712",
            has_session=True,
            app_id=4,
            app_hash="hash",
            is_probe_active=True,
        )
        other = VaultAccountItem(
            account_id="probe-b",
            source="lod_user",
            phone="+56911112222",
            has_session=True,
            app_id=4,
            app_hash="hash",
            is_probe_active=True,
        )

        with patch.object(AccountVaultService, "get_account", return_value=session_acc), \
             patch.object(AccountVaultService, "scan_accounts", return_value=[session_acc, other]), \
             patch("backend.app.services.account_vault.ConfigManager.get_instance", return_value=fake):
            disabled = AccountVaultService.toggle_probe("probe-a", False)
            self.assertTrue(disabled.success)
            self.assertFalse(disabled.is_probe_active)
            self.assertTrue(fake.config.precheck_probes_configured)
            self.assertNotIn("probe-a", fake.config.active_precheck_probe_ids)
            self.assertIn("probe-b", fake.config.active_precheck_probe_ids)

            enabled = AccountVaultService.toggle_probe("probe-a", True)
            self.assertTrue(enabled.is_probe_active)
            self.assertIn("probe-a", fake.config.active_precheck_probe_ids)

    def test_toggle_rejects_account_without_session(self):
        fake = FakeConfigManager()
        acc = VaultAccountItem(
            account_id="json-only",
            source="lod_user",
            phone="+56900",
            has_session=False,
        )
        with patch.object(AccountVaultService, "get_account", return_value=acc), \
             patch("backend.app.services.account_vault.ConfigManager.get_instance", return_value=fake):
            result = AccountVaultService.toggle_probe("json-only", True)
        self.assertFalse(result.success)
        self.assertIn("session", result.message.lower())

    def test_list_response_exposes_probe_fields(self):
        item = VaultAccountItem(account_id="x", source="lod_user", is_probe_active=True, has_session=True)
        listing = VaultAccountListResponse(
            total=1,
            lod_user_dir="/tmp",
            sessions_dir="/tmp",
            accounts=[item],
            active_probe_count=1,
            precheck_probes_configured=True,
        )
        self.assertEqual(listing.active_probe_count, 1)
        self.assertTrue(listing.accounts[0].is_probe_active)
        req = ToggleVaultProbeRequest(account_id="x", active=False)
        self.assertFalse(req.active)
        payload = ToggleVaultProbeResponse(success=True, message="ok", account_id="x", active=True)
        self.assertTrue(payload.success)


class TestExplicitAndPoolPairing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RegistrationTaskManager._instance = None

    async def test_explicit_proxy_is_used_verbatim(self):
        manager = RegistrationTaskManager.get_instance()
        task_id = manager.create_task()
        explicit = _proxy("77.77.77.77", 41080, role="all", assigned_country="in")
        explicit["id"] = "custom-user-pick"
        config = SimpleNamespace(
            target_country="cl",
            active_app_type="telegram_android",
            vak_sms_api_key="vak",
            sms_provider="vaksms",
            grizzly_sms_api_key="",
            use_proxy_seller_auto=True,
            fallback_proxy=SimpleNamespace(model_dump=lambda: {
                "proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808,
            }),
            custom_proxies=[explicit],
            phone_precheck_enabled=False,
        )
        cfg_mgr = SimpleNamespace(config=config)
        with patch("backend.app.services.registrar.ConfigManager.get_instance", return_value=cfg_mgr), \
             patch("backend.app.services.proxy_manager.find_custom_proxy", return_value=explicit), \
             patch.object(RegistrationOrchestrator, "_resolve_custom_proxy", new=AsyncMock(return_value=None)) as custom, \
             patch.object(RegistrationOrchestrator, "_resolve_proxy_seller_auto", new=AsyncMock(return_value=None)) as auto, \
             patch("backend.app.services.registrar.VakSmsService") as sms_cls, \
             patch("backend.app.services.registrar.AttestationGatewayService") as gw_cls, \
             patch("backend.app.services.registrar.DeviceProfileManager.get_resolved_profile", return_value={
                 "name": "t", "aid": "a", "device_model": "x", "system_version": "1",
                 "app_version": "1", "system_lang_code": "es", "tz_offset": 0,
             }), \
             patch("backend.app.services.registrar.TelegramClient"):
            sms = sms_cls.return_value
            sms.get_number = AsyncMock(side_effect=RuntimeError("stop-after-proxy"))
            sms.close = AsyncMock()
            gw_cls.return_value.close = AsyncMock()
            await RegistrationOrchestrator.run_registration(
                task_id=task_id,
                country="cl",
                proxy_id="custom-user-pick",
                proxy_mode="explicit",
            )
        custom.assert_not_awaited()
        auto.assert_not_awaited()
        logs = "\n".join(manager.get_task(task_id)["logs"])
        self.assertIn("100% 遵从用户指定节点", logs)
        self.assertIn("77.77.77.77", logs)

    async def test_custom_pool_uses_assigned_country_match(self):
        manager = RegistrationTaskManager.get_instance()
        task_id = manager.create_task()
        bound = _proxy("55.55.55.55", 1080, role="registration", assigned_country="id")
        config = SimpleNamespace(custom_proxies=[bound])
        with patch("backend.app.services.proxy_manager.select_proxy_for_registration", return_value=bound), \
             patch("backend.app.services.proxy_manager.custom_pool_summary", return_value={
                 "total": 1, "regional": 1, "healthy": 1, "countries": ["ID"],
             }):
            resolved = await RegistrationOrchestrator._resolve_custom_proxy(
                config, "id", task_id, manager
            )
        self.assertEqual(resolved["addr"], "55.55.55.55")
        logs = "\n".join(manager.get_task(task_id)["logs"])
        self.assertIn("注册通道", logs)
        self.assertIn("registration", logs)


class TestPrecheckUsesActiveProbesAndDedicatedProxy(unittest.IsolatedAsyncioTestCase):
    async def test_list_probe_accounts_requires_activation(self):
        active = SimpleNamespace(
            account_id="on",
            has_session=True,
            is_probe_active=True,
            app_id=4,
            app_hash="h",
            phone="+9183",
        )
        inactive = SimpleNamespace(
            account_id="off",
            has_session=True,
            is_probe_active=False,
            app_id=4,
            app_hash="h",
            phone="+5690",
        )
        with patch(
            "backend.app.services.phone_precheck.AccountVaultService.resolve_session_file",
            return_value=Path("/tmp/fake.session"),
        ):
            probes = PhonePrecheckService.list_probe_accounts(
                [active, inactive],
                config=AppConfigModel(precheck_probes_configured=True, active_precheck_probe_ids=["on"]),
            )
        self.assertEqual(len(probes), 1)
        self.assertEqual(probes[0].account_id, "on")

    async def test_check_phone_forces_precheck_role_proxy(self):
        dedicated = _proxy("66.66.66.66", 1080, role="precheck", assigned_country="cl")
        captured = {}

        async def _fake_check(account, phone, proxy=None):
            captured["proxy"] = proxy
            return PhonePrecheckService.result_from_user(phone, None, method="resolve_phone")

        acc = SimpleNamespace(
            account_id="on",
            has_session=True,
            is_probe_active=True,
            app_id=4,
            app_hash="h",
            phone="+9183",
        )
        with patch.object(PhonePrecheckService, "list_probe_accounts", return_value=[acc]), \
             patch.object(PhonePrecheckService, "resolve_precheck_proxy", return_value=dedicated), \
             patch.object(PhonePrecheckService, "_check_with_account", new=_fake_check):
            result = await PhonePrecheckService.check_phone(
                "+56911112222",
                proxy={"addr": "1.1.1.1", "port": 1, "role": "registration"},
                enabled=True,
            )
        self.assertFalse(result.intercept)
        self.assertEqual(captured["proxy"]["addr"], "66.66.66.66")
        self.assertEqual(captured["proxy"]["role"], "precheck")

    def test_status_exposes_active_probes_and_proxy(self):
        acc = SimpleNamespace(
            account_id="on",
            has_session=True,
            is_probe_active=True,
            app_id=4,
            app_hash="h",
            phone="+918310013712",
            source="lod_user",
        )
        proxy = _proxy("66.66.66.66", 1080, role="precheck")
        with patch.object(PhonePrecheckService, "list_probe_accounts", return_value=[acc]), \
             patch.object(PhonePrecheckService, "resolve_precheck_proxy", return_value=proxy):
            status = PhonePrecheckService.describe_status(
                config=SimpleNamespace(phone_precheck_enabled=True),
                accounts=[acc],
            )
        self.assertTrue(status.active)
        self.assertEqual(len(status.active_probes), 1)
        self.assertEqual(status.precheck_proxy["addr"], "66.66.66.66")
        payload = PhonePrecheckStatusResponse(**status.to_dict())
        self.assertEqual(payload.precheck_proxy["role"], "precheck")
        self.assertEqual(payload.active_probes[0]["account_id"], "on")


class TestUpdateItemSchema(unittest.TestCase):
    def test_update_request_normalizes_role(self):
        req = CustomProxyUpdateItemRequest(proxy_id="custom-1", role="PRECHECK", assigned_country="IN")
        self.assertEqual(req.role, "precheck")
        self.assertEqual(req.assigned_country, "in")


if __name__ == "__main__":
    unittest.main(verbosity=2)
