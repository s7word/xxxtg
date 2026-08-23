"""自定义代理池：多格式解析、导入去重持久化、测活回写与国家路由。"""
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
    CustomProxyDeleteRequest,
    CustomProxyImportRequest,
    CustomProxyItem,
    CustomProxySetFallbackRequest,
    CustomProxyTestAllRequest,
)
from backend.app.services.proxy_manager import (  # noqa: E402
    apply_probe_result,
    custom_pool_summary,
    delete_custom_proxies,
    import_proxy_text,
    import_proxy_text_async,
    list_custom_proxies,
    merge_imported_proxies,
    parse_proxy_line,
    parse_proxy_text,
    persist_custom_proxies,
    probe_custom_proxies,
    select_custom_proxy,
    to_persist_item,
)
from backend.app.services.proxyseller import (  # noqa: E402
    ProxySellerService,
    match_proxy_country,
    normalize_custom_proxy_item,
    proxy_identity,
)
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)


SAMPLE_TEXT = """
# residential chile
res.proxy-seller.com;10000;chile_user;chile_pass
181.43.10.22:50101:user_cl:pass_cl
# bare host
203.0.113.10:1080
user_in:pass_in@res.proxy-seller.com:10003
socks5://id_user:id_pass@10.8.0.21:41080
http://web_user:web_pass@198.51.100.9:8080
// comment
"""


class FakeConfigManager:
    def __init__(self, **overrides):
        payload = {
            "target_country": "cl",
            "use_proxy_seller_auto": True,
            "proxy_seller_key": "",
            "custom_proxies": [],
        }
        payload.update(overrides)
        self._config = AppConfigModel(**payload)

    @property
    def config(self):
        return self._config

    def save_config(self, new_config):
        self._config = new_config
        return self._config


class TestProxyTextParser(unittest.TestCase):
    def test_semicolon_host_port_user_pass(self):
        item = parse_proxy_line("res.proxy-seller.com;10000;chile_user;chile_pass")
        self.assertIsNotNone(item)
        self.assertEqual(item["addr"], "res.proxy-seller.com")
        self.assertEqual(item["port"], 10000)
        self.assertEqual(item["username"], "chile_user")
        self.assertEqual(item["password"], "chile_pass")
        self.assertEqual(item["proxy_type"], "socks5")
        self.assertTrue(item["id"].startswith("custom-"))

    def test_colon_host_port_user_pass_and_bare_host(self):
        auth = parse_proxy_line("181.43.10.22:50101:user_cl:pass_cl")
        self.assertEqual(auth["addr"], "181.43.10.22")
        self.assertEqual(auth["port"], 50101)
        self.assertEqual(auth["username"], "user_cl")
        self.assertEqual(auth["password"], "pass_cl")

        bare = parse_proxy_line("203.0.113.10:1080")
        self.assertEqual(bare["addr"], "203.0.113.10")
        self.assertEqual(bare["port"], 1080)
        self.assertIsNone(bare["username"])
        self.assertEqual(bare["proxy_type"], "socks5")

    def test_userinfo_and_url_schemes(self):
        userinfo = parse_proxy_line("user_in:pass_in@res.proxy-seller.com:10003")
        self.assertEqual(userinfo["username"], "user_in")
        self.assertEqual(userinfo["addr"], "res.proxy-seller.com")
        self.assertEqual(userinfo["port"], 10003)

        socks = parse_proxy_line("socks5://id_user:id_pass@10.8.0.21:41080")
        self.assertEqual(socks["proxy_type"], "socks5")
        self.assertEqual(socks["username"], "id_user")
        self.assertEqual(socks["addr"], "10.8.0.21")

        http = parse_proxy_line("http://web_user:web_pass@198.51.100.9:8080")
        self.assertEqual(http["proxy_type"], "http")
        self.assertEqual(http["port"], 8080)

    def test_password_may_contain_colon_and_default_country(self):
        item = parse_proxy_line(
            "host.example:1080:user:p@ss:word",
            default_country="in",
        )
        self.assertEqual(item["password"], "p@ss:word")
        self.assertEqual(item["country_code"], "in")
        self.assertTrue(match_proxy_country(normalize_custom_proxy_item(item), "in"))
        self.assertFalse(match_proxy_country(normalize_custom_proxy_item(item), "id"))

    def test_skips_comments_blanks_and_invalid_lines(self):
        parsed = parse_proxy_text(SAMPLE_TEXT)
        self.assertEqual(parsed["parsed"], 6)
        self.assertEqual(parsed["skipped_count"], 0)
        addrs = {item["addr"] for item in parsed["proxies"]}
        self.assertIn("res.proxy-seller.com", addrs)
        self.assertIn("10.8.0.21", addrs)
        self.assertIsNone(parse_proxy_line("# just a comment"))
        self.assertIsNone(parse_proxy_line("not-a-proxy"))
        self.assertIsNone(parse_proxy_line("host:99999"))

    def test_dedupes_identical_identities(self):
        parsed = parse_proxy_text(
            "a.example:1080:user:pass\n"
            "socks5://user:pass@a.example:1080\n"
        )
        self.assertEqual(parsed["parsed"], 1)


class TestCustomProxyPersist(unittest.TestCase):
    def test_app_config_roundtrip(self):
        cfg = AppConfigModel(
            custom_proxies=[
                CustomProxyItem(addr="10.0.0.1", port=1080, username="u", password="p", country_code="cl")
            ]
        )
        dumped = cfg.model_dump()
        restored = AppConfigModel(**dumped)
        self.assertEqual(len(restored.custom_proxies), 1)
        self.assertEqual(restored.custom_proxies[0].addr, "10.0.0.1")
        self.assertEqual(restored.custom_proxies[0].country_code, "cl")

    def test_merge_imported_does_not_drop_probe_state(self):
        existing = [to_persist_item({
            "addr": "10.0.0.1",
            "port": 1080,
            "username": "u",
            "password": "old",
            "country_code": "cl",
            "healthy": True,
            "egress_ip": "186.1.1.1",
            "latency_ms": 120,
        })]
        incoming = [parse_proxy_line("10.0.0.1:1080:u:newpass")]
        merged, stats = merge_imported_proxies(existing, incoming, replace=False)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(merged[0]["password"], "newpass")
        self.assertTrue(merged[0]["healthy"])
        self.assertEqual(merged[0]["egress_ip"], "186.1.1.1")

    def test_import_text_persists_and_lists(self):
        fake = FakeConfigManager()

        def _load():
            return [item.model_dump() for item in fake.config.custom_proxies]

        with patch("backend.app.services.proxy_manager._config_manager", return_value=fake), \
             patch("backend.app.services.proxy_manager.load_custom_proxy_items", side_effect=_load):
            result = import_proxy_text(
                "10.8.0.21:41080:id_user:id_pass\n181.43.10.22:50101:user_cl:pass_cl",
                default_country="id",
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["imported"], 2)
            self.assertEqual(len(fake.config.custom_proxies), 2)

    def test_delete_and_clear(self):
        fake = FakeConfigManager(custom_proxies=[
            {"addr": "10.0.0.1", "port": 1080, "username": "u", "password": "p"},
            {"addr": "10.0.0.2", "port": 1080},
        ])

        def _load():
            return [item.model_dump() for item in fake.config.custom_proxies]

        with patch("backend.app.services.proxy_manager._config_manager", return_value=fake), \
             patch("backend.app.services.proxy_manager.load_custom_proxy_items", side_effect=_load):
            removed = delete_custom_proxies(addr="10.0.0.1", port=1080, username="u")
            self.assertTrue(removed["success"])
            self.assertEqual(removed["remaining"], 1)
            cleared = delete_custom_proxies(clear_all=True)
            self.assertTrue(cleared["cleared"])
            self.assertEqual(len(fake.config.custom_proxies), 0)


class TestCustomProxyProbeAndMatch(unittest.IsolatedAsyncioTestCase):
    async def test_probe_writes_country_and_latency(self):
        item = parse_proxy_line("10.8.0.21:41080:id_user:id_pass")
        probed = apply_probe_result(item, {
            "success": True,
            "ip": "103.24.1.9",
            "country": "Indonesia",
            "country_code": "ID",
            "city": "Jakarta",
            "latency_ms": 88.5,
        })
        self.assertTrue(probed["healthy"])
        self.assertEqual(probed["country_code"], "id")
        self.assertEqual(probed["egress_ip"], "103.24.1.9")
        self.assertEqual(probed["city"], "Jakarta")
        self.assertEqual(probed["latency_ms"], 88.5)
        self.assertTrue(match_proxy_country(normalize_custom_proxy_item(probed), "id"))
        self.assertTrue(match_proxy_country(normalize_custom_proxy_item(probed), "indonesia"))
        self.assertFalse(match_proxy_country(normalize_custom_proxy_item(probed), "in"))

    async def test_import_async_optional_probe(self):
        fake = FakeConfigManager()

        def _load():
            return [item.model_dump() for item in fake.config.custom_proxies]

        with patch("backend.app.services.proxy_manager._config_manager", return_value=fake), \
             patch("backend.app.services.proxy_manager.load_custom_proxy_items", side_effect=_load), \
             patch(
                 "backend.app.services.proxyseller.ProxySellerService.test_proxy_connectivity",
                 new=AsyncMock(return_value={
                     "success": True,
                     "ip": "186.189.1.2",
                     "country": "Chile",
                     "country_code": "CL",
                     "city": "Santiago",
                     "latency_ms": 40,
                 }),
             ):
            result = await import_proxy_text_async(
                "res.proxy-seller.com;10000;chile_user;chile_pass",
                probe=True,
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["probe"]["healthy"], 1)
            saved = fake.config.custom_proxies[0]
            self.assertEqual(saved.country_code, "cl")
            self.assertEqual(saved.egress_ip, "186.189.1.2")
            self.assertTrue(saved.healthy)

    async def test_select_custom_proxy_prefers_healthy_low_latency(self):
        items = [
            to_persist_item({
                "addr": "1.1.1.1", "port": 1080, "country_code": "id",
                "healthy": False, "latency_ms": 10,
            }),
            to_persist_item({
                "addr": "2.2.2.2", "port": 1080, "country_code": "id",
                "healthy": True, "latency_ms": 90,
            }),
            to_persist_item({
                "addr": "3.3.3.3", "port": 1080, "country_code": "id",
                "healthy": True, "latency_ms": 30,
            }),
            to_persist_item({
                "addr": "4.4.4.4", "port": 1080, "country_code": "cl",
                "healthy": True, "latency_ms": 5,
            }),
        ]
        with patch("backend.app.services.proxy_manager.load_custom_proxy_items", return_value=items):
            chosen = select_custom_proxy("id")
            self.assertEqual(chosen["addr"], "3.3.3.3")
            self.assertIsNone(select_custom_proxy("in"))
            summary = custom_pool_summary("id")
            self.assertEqual(summary["total"], 4)
            self.assertEqual(summary["regional"], 3)

    async def test_probe_custom_proxies_persists(self):
        fake = FakeConfigManager(custom_proxies=[
            {"addr": "10.0.0.8", "port": 1080, "username": "u", "password": "p"},
        ])

        def _load():
            return [item.model_dump() for item in fake.config.custom_proxies]

        with patch("backend.app.services.proxy_manager._config_manager", return_value=fake), \
             patch("backend.app.services.proxy_manager.load_custom_proxy_items", side_effect=_load), \
             patch(
                 "backend.app.services.proxyseller.ProxySellerService.test_proxy_connectivity",
                 new=AsyncMock(return_value={
                     "success": True,
                     "ip": "49.1.2.3",
                     "country": "India",
                     "country_code": "IN",
                     "latency_ms": 70,
                 }),
             ):
            result = await probe_custom_proxies(concurrency=2)
            self.assertEqual(result["tested"], 1)
            self.assertEqual(result["healthy"], 1)
            self.assertEqual(fake.config.custom_proxies[0].country_code, "in")


class TestRegistrarCustomMatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RegistrationTaskManager._instance = None

    async def test_resolve_custom_proxy_logs_country_hit(self):
        manager = RegistrationTaskManager.get_instance()
        task_id = manager.create_task()
        custom = {
            "id": "custom-id-9",
            "proxy_type": "socks5",
            "addr": "10.8.0.21",
            "port": 41080,
            "username": "id_user",
            "password": "id_pass",
            "country": "Indonesia",
            "country_code": "id",
            "source": "custom",
            "healthy": True,
            "egress_ip": "103.24.1.9",
        }
        config = SimpleNamespace(custom_proxies=[custom])
        with patch("backend.app.services.proxy_manager.select_custom_proxy", return_value=custom), \
             patch("backend.app.services.proxy_manager.custom_pool_summary", return_value={
                 "total": 1, "regional": 1, "healthy": 1, "countries": ["ID"],
             }):
            resolved = await RegistrationOrchestrator._resolve_custom_proxy(
                config, "id", task_id, manager
            )
        self.assertEqual(resolved["addr"], "10.8.0.21")
        logs = "\n".join(manager.get_task(task_id)["logs"])
        self.assertIn("[自建代理池] 成功匹配 ID 区域代理: socks5://10.8.0.21:41080", logs)


class TestCustomProxySchemas(unittest.TestCase):
    def test_request_models(self):
        imported = CustomProxyImportRequest(text="a:1080", probe=True, default_country="cl")
        self.assertTrue(imported.probe)
        self.assertEqual(imported.default_protocol, "socks5")
        tested = CustomProxyTestAllRequest(concurrency=6)
        self.assertEqual(tested.concurrency, 6)
        fallback = CustomProxySetFallbackRequest(proxy_id="custom-abc")
        self.assertEqual(fallback.proxy_id, "custom-abc")
        deleted = CustomProxyDeleteRequest(clear_all=True)
        self.assertTrue(deleted.clear_all)


if __name__ == "__main__":
    unittest.main()
