"""Proxy-Seller 区域过滤、自动选择与连通性测试。"""
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
    ProxySellerAutoSelectRequest,
    ProxySellerEnsureTgRequest,
    ProxySellerListResponse,
    ProxySellerResidentListsResponse,
    ProxySellerTestAllRequest,
)
from backend.app.services.proxyseller import (  # noqa: E402
    ProxySellerService,
    STATIC_INDIA_PORTS,
    STATIC_INDIA_USERNAME,
    STATIC_RESIDENTIAL_HOST,
    STATIC_RESIDENTIAL_PORTS,
    STATIC_RESIDENTIAL_USERNAME,
    builtin_static_residential_items,
    country_code_from_tg_title,
    expand_country_aliases,
    format_proxy_endpoint,
    infer_country_from_phone,
    is_bot_list_title,
    is_resident_tg,
    is_static_residential,
    is_xxxtg_list_title,
    match_proxy_country,
    merge_proxy_pools,
    parse_export_ports,
    parse_resident_geo,
    proxy_identity,
    resident_list_to_proxies,
    static_residential_count,
    normalize_proxy_item,
    _extract_raw_items,
    _parse_ip_probe_payload,
    _pick_protocol_and_port,
)
from backend.app.services.registrar import (  # noqa: E402
    RegistrationOrchestrator,
    RegistrationTaskManager,
)


def _chile_raw(**overrides):
    item = {
        "id": "1001",
        "ip": "181.43.10.22",
        "protocol": "HTTP",
        "port_socks5": 50101,
        "port_socks": 50101,
        "port_http": 50100,
        "login": "user_cl",
        "password": "pass_cl",
        "country": "Chile",
        "country_alpha3": "CHL",
        "status": "Active",
        "status_type": "ACTIVE",
        "date_end": "23.09.2026",
        "can_prolong": True,
    }
    item.update(overrides)
    return item


def _usa_raw(**overrides):
    item = {
        "id": "2002",
        "ip": "23.81.44.9",
        "protocol": "SOCKS5",
        "port_socks": 41080,
        "port_http": 41000,
        "login": "user_us",
        "password": "pass_us",
        "country": "United States",
        "country_alpha3": "USA",
        "status": "Active",
        "date_end": "01.10.2026",
    }
    item.update(overrides)
    return item


def _kazakhstan_raw(**overrides):
    item = {
        "id": "3003",
        "ip_only": "91.201.11.8",
        "port": 1080,
        "login": "user_kz",
        "password": "pass_kz",
        "country": "kz",
        "status": "ACTIVE",
    }
    item.update(overrides)
    return item


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class TestCountryMatching(unittest.TestCase):
    def test_expand_aliases_iso2_iso3_name(self):
        chile = expand_country_aliases("cl")
        self.assertIn("cl", chile)
        self.assertIn("chl", chile)
        self.assertIn("chile", chile)
        self.assertEqual(expand_country_aliases("CHL") & chile, chile & expand_country_aliases("chile"))

        indonesia = expand_country_aliases("id")
        self.assertIn("idn", indonesia)
        self.assertIn("indonesia", indonesia)

    def test_match_proxy_country_exact_and_fuzzy(self):
        chile = normalize_proxy_item(_chile_raw())
        usa = normalize_proxy_item(_usa_raw())
        kz = normalize_proxy_item(_kazakhstan_raw())

        self.assertTrue(match_proxy_country(chile, "cl"))
        self.assertTrue(match_proxy_country(chile, "CHL"))
        self.assertTrue(match_proxy_country(chile, "Chile"))
        self.assertTrue(match_proxy_country(usa, "us"))
        self.assertTrue(match_proxy_country(usa, "USA"))
        self.assertTrue(match_proxy_country(kz, "kazakhstan"))
        self.assertFalse(match_proxy_country(chile, "ru"))
        self.assertFalse(match_proxy_country(usa, "id"))
        india = normalize_proxy_item(_usa_raw(country="India", country_alpha3="IND", ip="49.1.1.1"))
        self.assertFalse(match_proxy_country(india, "id"))
        self.assertTrue(match_proxy_country(india, "in"))
        self.assertTrue(match_proxy_country(india, "IND"))
        self.assertTrue(match_proxy_country(chile, None))

    def test_infer_country_from_phone(self):
        self.assertEqual(infer_country_from_phone("+918302332054"), "in")
        self.assertEqual(infer_country_from_phone("918310013712"), "in")
        self.assertEqual(infer_country_from_phone("+56 9 7194 8355"), "cl")
        self.assertEqual(infer_country_from_phone("56971948355"), "cl")
        self.assertEqual(infer_country_from_phone("+14165550199"), "ca")
        self.assertEqual(infer_country_from_phone("+12125550199"), "us")
        self.assertEqual(infer_country_from_phone("+212612345678"), "ma")
        self.assertEqual(infer_country_from_phone("+447911123456"), "gb")
        self.assertIsNone(infer_country_from_phone(""))
        self.assertIsNone(infer_country_from_phone(None))

    def test_morocco_resolves_and_matches(self):
        from backend.app.services.proxyseller import resolve_iso2_country, country_alpha3

        self.assertEqual(resolve_iso2_country("ma"), "MA")
        self.assertEqual(resolve_iso2_country("MAR"), "MA")
        self.assertEqual(resolve_iso2_country("Morocco"), "MA")
        self.assertEqual(country_alpha3("ma"), "MAR")
        aliases = expand_country_aliases("ma")
        self.assertIn("ma", aliases)
        self.assertIn("mar", aliases)
        morocco = normalize_proxy_item({
            "ip": "160.178.169.9",
            "port_socks5": 10000,
            "login": "u",
            "password": "p",
            "country": "MA",
            "country_code": "ma",
            "country_alpha3": "MAR",
        })
        self.assertTrue(match_proxy_country(morocco, "ma"))
        self.assertTrue(match_proxy_country(morocco, "morocco"))
        self.assertFalse(match_proxy_country(morocco, "it"))


class TestProxyNormalization(unittest.TestCase):
    def test_prefers_port_socks5(self):
        protocol, port = _pick_protocol_and_port(_chile_raw())
        self.assertEqual(protocol, "socks5")
        self.assertEqual(port, 50101)

    def test_extracts_bucketed_official_payload(self):
        payload = {
            "ipv4": [_chile_raw(), _usa_raw()],
            "ipv6": [],
            "mobile": {"items": [_kazakhstan_raw()]},
            "isp": [],
        }
        items = _extract_raw_items(payload)
        self.assertEqual(len(items), 3)

    def test_extracts_typed_items_payload(self):
        items = _extract_raw_items({"items": [_chile_raw()]})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ip"], "181.43.10.22")

    def test_normalize_fills_country_code(self):
        node = normalize_proxy_item(_chile_raw())
        self.assertEqual(node["addr"], "181.43.10.22")
        self.assertEqual(node["port"], 50101)
        self.assertEqual(node["proxy_type"], "socks5")
        self.assertEqual(node["username"], "user_cl")
        self.assertEqual(node["password"], "pass_cl")
        self.assertEqual(node["country_code"], "cl")
        self.assertEqual(node["country_alpha3"], "CHL")
        self.assertEqual(node["active_until"], "23.09.2026")


class TestProxySellerServicePool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ProxySellerService._pool_cache.clear()
        ProxySellerService._health.clear()
        ProxySellerService._rr_cursor.clear()
        self._custom_patch = patch(
            "backend.app.services.proxyseller.load_custom_proxy_items",
            return_value=[],
        )
        self._custom_patch.start()
        self.addCleanup(self._custom_patch.stop)

    async def _svc_with_payload(self, payload, include_static=False):
        svc = ProxySellerService("test-key", cache_ttl=30, include_static=include_static)
        svc.client.request = AsyncMock(return_value=DummyResponse(payload))
        return svc

    async def test_get_proxy_list_all_and_filter(self):
        svc = await self._svc_with_payload({
            "status": "success",
            "data": {
                "ipv4": [_chile_raw(), _usa_raw()],
                "isp": [_kazakhstan_raw()],
            },
            "errors": [],
        })
        try:
            all_items = await svc.get_proxy_list()
            self.assertEqual(len(all_items), 3)
            chile = await svc.get_proxy_list(country="cl")
            self.assertEqual(len(chile), 1)
            self.assertEqual(chile[0]["addr"], "181.43.10.22")
            self.assertEqual(chile[0]["port"], 50101)
            indonesia = await svc.get_proxy_list(country="id")
            self.assertEqual(indonesia, [])
            fuzzy = await svc.get_proxy_list(country="united states")
            self.assertEqual(len(fuzzy), 1)
            self.assertEqual(fuzzy[0]["country_code"], "us")
        finally:
            await svc.close()

    async def test_cache_avoids_second_fetch(self):
        svc = await self._svc_with_payload({
            "status": "success",
            "data": {"items": [_chile_raw()]},
            "errors": [],
        })
        try:
            first = await svc.get_proxy_list(country="cl")
            second = await svc.get_proxy_list(country="chile")
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(svc.client.request.await_count, 2)
            meta = svc.cache_meta()
            self.assertTrue(meta["cached"])
            self.assertGreaterEqual(meta["total_cached"], 1)
        finally:
            await svc.close()

    async def test_auto_select_regional_then_fallback(self):
        svc = await self._svc_with_payload({
            "status": "success",
            "data": {"ipv4": [_usa_raw(), _kazakhstan_raw()]},
            "errors": [],
        })
        try:
            miss = await svc.select_best_proxy(target_country="cl", allow_fallback=False)
            self.assertFalse(miss["success"])
            self.assertIn("暂无可用", miss["message"])
            self.assertIsNone(miss["proxy"])
            self.assertFalse(miss["fallback_used"])

            fallback = await svc.select_best_proxy(target_country="cl", allow_fallback=True)
            self.assertFalse(fallback["success"])
            self.assertFalse(fallback["fallback_used"])
            self.assertIsNone(fallback["proxy"])
            self.assertIn("禁止跨大区", fallback["message"])
            self.assertEqual(fallback["source"], "config_fallback_required")

            kz = await svc.select_best_proxy(target_country="kz", allow_fallback=False)
            self.assertTrue(kz["matched"])
            self.assertEqual(kz["proxy"]["addr"], "91.201.11.8")
            self.assertEqual(format_proxy_endpoint(kz["proxy"]), "socks5://91.201.11.8:1080")
        finally:
            await svc.close()

    async def test_select_skips_unhealthy_on_rotation(self):
        svc = await self._svc_with_payload({
            "status": "success",
            "data": {"items": [_chile_raw(), _chile_raw(id="1002", ip="181.43.10.99", port_socks5=50202)]},
            "errors": [],
        })
        try:
            items = await svc.get_proxy_list(country="cl", include_health=False)
            svc.record_health(items[0], {"success": False, "error": "timeout"})
            svc.record_health(items[1], {"success": True, "ip": "8.8.8.8", "country": "Chile", "country_code": "CL"})
            selected = await svc.select_best_proxy(target_country="cl", probe=False)
            self.assertEqual(selected["proxy"]["addr"], "181.43.10.99")
            self.assertTrue(selected["proxy"]["healthy"])
        finally:
            await svc.close()

    async def test_connectivity_success_and_failure(self):
        proxy = normalize_proxy_item(_chile_raw())

        class _OkClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url):
                return DummyResponse({
                    "ip": "181.43.10.22",
                    "country_name": "Chile",
                    "country_code": "CL",
                    "city": "Santiago",
                    "org": "Demo ISP",
                })

        with patch("backend.app.services.proxyseller.httpx.AsyncClient", _OkClient):
            ok = await ProxySellerService.test_proxy_connectivity(proxy)
        self.assertTrue(ok["success"])
        self.assertEqual(ok["country_code"], "CL")
        self.assertIn("latency_ms", ok)

        empty = await ProxySellerService.test_proxy_connectivity({"proxy_type": "socks5"})
        self.assertFalse(empty["success"])

    async def test_test_all_records_health(self):
        svc = await self._svc_with_payload({
            "status": "success",
            "data": {"items": [_chile_raw()]},
            "errors": [],
        })
        try:
            with patch.object(
                ProxySellerService,
                "test_proxy_connectivity",
                new=AsyncMock(return_value={"success": True, "ip": "1.1.1.1", "country": "Chile", "country_code": "CL"}),
            ):
                result = await svc.test_all(country="cl")
            self.assertEqual(result["tested"], 1)
            self.assertEqual(result["healthy"], 1)
            self.assertEqual(result["results"][0]["egress_ip"], "1.1.1.1")
        finally:
            await svc.close()

    async def test_purchase_and_renew_reserved_endpoints(self):
        svc = ProxySellerService("test-key")
        svc.client.request = AsyncMock(return_value=DummyResponse({
            "status": "success",
            "data": {"ok": True},
            "errors": [],
        }))
        try:
            await svc.get_balance()
            await svc.get_reference_list("ipv4")
            await svc.calculate_order({"countryId": 1, "periodId": "1m", "quantity": 1, "paymentId": 1})
            await svc.place_order({"countryId": 1, "periodId": "1m", "quantity": 1, "paymentId": 1})
            await svc.calculate_renewal(["1001"], "1m")
            await svc.renew_proxies(["1001"], "1m")
            paths = [call.args[1] for call in svc.client.request.await_args_list]
            self.assertTrue(any(path.endswith("balance") for path in paths))
            self.assertTrue(any("order/make" in path for path in paths))
            self.assertTrue(any("prolong/make/ipv4" in path for path in paths))
        finally:
            await svc.close()

    async def test_api_error_is_raised(self):
        svc = await self._svc_with_payload({
            "status": "error",
            "errors": [{"message": "Error api key"}],
        })
        try:
            with self.assertRaises(RuntimeError):
                await svc.get_proxy_list(refresh=True)
        finally:
            await svc.close()

    async def test_static_pool_used_when_api_blocked(self):
        svc = ProxySellerService("test-key", include_static=True)
        svc.client.request = AsyncMock(return_value=DummyResponse({
            "status": "error",
            "errors": [{"message": "IP address is not allowed"}],
        }))
        try:
            items = await svc.get_proxy_list(country="cl", refresh=True, include_health=False)
            self.assertEqual(len(items), len(STATIC_RESIDENTIAL_PORTS))
            self.assertTrue(all(is_static_residential(item) for item in items))
            self.assertTrue(all(item["addr"] == STATIC_RESIDENTIAL_HOST for item in items))
            self.assertEqual({item["port"] for item in items}, set(STATIC_RESIDENTIAL_PORTS))
            meta = svc.cache_meta()
            self.assertEqual(meta["source"], "static_residential")
            self.assertIn("IP address is not allowed", meta["api_error"] or "")

            selected = await svc.select_best_proxy(target_country="cl", allow_fallback=False)
            self.assertTrue(selected["success"])
            self.assertTrue(selected["matched"])
            self.assertEqual(selected["source"], "static_residential")
            self.assertEqual(selected["proxy"]["addr"], STATIC_RESIDENTIAL_HOST)
            self.assertIn(selected["proxy"]["port"], STATIC_RESIDENTIAL_PORTS)
        finally:
            await svc.close()

    async def test_static_pool_merges_with_api_results(self):
        svc = await self._svc_with_payload({
            "status": "success",
            "data": {"ipv4": [_usa_raw()]},
            "errors": [],
        }, include_static=True)
        try:
            items = await svc.get_proxy_list(refresh=True, include_health=False)
            self.assertEqual(len(items), 1 + static_residential_count())
            self.assertEqual(items[0]["addr"], "23.81.44.9")
            self.assertTrue(any(is_static_residential(item) for item in items))
            chile = await svc.get_proxy_list(country="cl", include_health=False)
            self.assertEqual(len(chile), len(STATIC_RESIDENTIAL_PORTS))
            india = await svc.get_proxy_list(country="in", include_health=False)
            self.assertEqual(len(india), len(STATIC_INDIA_PORTS))
        finally:
            await svc.close()

    async def test_static_pool_works_without_api_key(self):
        svc = ProxySellerService("", include_static=True)
        try:
            items = await svc.get_proxy_list(refresh=True, include_health=False)
            self.assertEqual(len(items), static_residential_count())
            self.assertEqual(svc.cache_meta()["source"], "static_residential")
        finally:
            await svc.close()

    async def test_select_best_proxy_india_from_static_pool(self):
        svc = ProxySellerService("", include_static=True)
        try:
            selected = await svc.select_best_proxy(target_country="in", allow_fallback=False)
            self.assertTrue(selected["success"])
            self.assertTrue(selected["matched"])
            self.assertEqual(selected["source"], "static_residential")
            self.assertEqual(selected["proxy"]["addr"], STATIC_RESIDENTIAL_HOST)
            self.assertEqual(selected["proxy"]["username"], STATIC_INDIA_USERNAME)
            self.assertIn(selected["proxy"]["port"], STATIC_INDIA_PORTS)
            self.assertEqual(selected["proxy"]["country_code"], "in")

            by_phone = await svc.select_best_proxy(phone="+918302332054", allow_fallback=False)
            self.assertTrue(by_phone["success"])
            self.assertEqual(by_phone["target_country"], "in")
            self.assertEqual(by_phone["proxy"]["username"], STATIC_INDIA_USERNAME)
        finally:
            await svc.close()


class TestStaticResidentialHelpers(unittest.TestCase):
    def test_builtin_static_items_are_chile_socks5(self):
        items = builtin_static_residential_items("cl")
        self.assertEqual(len(items), 5)
        self.assertEqual([item["port"] for item in items], list(STATIC_RESIDENTIAL_PORTS))
        for item in items:
            self.assertTrue(is_static_residential(item))
            self.assertEqual(item["addr"], STATIC_RESIDENTIAL_HOST)
            self.assertEqual(item["proxy_type"], "socks5")
            self.assertEqual(item["country_code"], "cl")
            self.assertEqual(item["username"], STATIC_RESIDENTIAL_USERNAME)
            self.assertTrue(item["password"])
            self.assertTrue(match_proxy_country(item, "cl"))
            self.assertTrue(match_proxy_country(item, "chile"))

    def test_builtin_static_items_include_india_pool(self):
        india = builtin_static_residential_items("in")
        self.assertEqual(len(india), 10)
        self.assertEqual([item["port"] for item in india], list(STATIC_INDIA_PORTS))
        for item in india:
            self.assertTrue(is_static_residential(item))
            self.assertEqual(item["addr"], STATIC_RESIDENTIAL_HOST)
            self.assertEqual(item["proxy_type"], "socks5")
            self.assertEqual(item["country_code"], "in")
            self.assertEqual(item["username"], STATIC_INDIA_USERNAME)
            self.assertTrue(match_proxy_country(item, "in"))
            self.assertTrue(match_proxy_country(item, "india"))
            self.assertTrue(match_proxy_country(item, "IND"))
            self.assertFalse(match_proxy_country(item, "cl"))

        all_items = builtin_static_residential_items()
        self.assertEqual(len(all_items), static_residential_count())
        self.assertEqual(static_residential_count("in"), 10)
        self.assertEqual(static_residential_count("cl"), 5)

    def test_cl_and_in_same_port_are_distinct_identities(self):
        chile = next(item for item in builtin_static_residential_items("cl") if item["port"] == 10000)
        india = next(item for item in builtin_static_residential_items("in") if item["port"] == 10000)
        self.assertNotEqual(proxy_identity(chile), proxy_identity(india))
        merged = merge_proxy_pools([chile], [india])
        self.assertEqual(len(merged), 2)

    def test_merge_proxy_pools_dedupes_identity(self):
        static = builtin_static_residential_items("cl")
        duplicate = dict(static[0])
        duplicate["id"] = "dup"
        merged = merge_proxy_pools(static, [duplicate, normalize_proxy_item(_usa_raw())])
        self.assertEqual(len(merged), 6)

    def test_parse_ip_probe_payload(self):
        ipapi = _parse_ip_probe_payload({
            "ip": "186.189.99.200",
            "country_name": "Chile",
            "country_code": "CL",
            "city": "Santiago",
            "org": "WOM SpA",
        })
        self.assertEqual(ipapi["ip"], "186.189.99.200")
        self.assertEqual(ipapi["country_code"], "CL")
        ipinfo = _parse_ip_probe_payload({
            "ip": "45.232.95.229",
            "country": "CL",
            "city": "Santiago",
            "org": "AS52341 WOM SpA",
        })
        self.assertEqual(ipinfo["country_code"], "CL")
        self.assertIsNone(_parse_ip_probe_payload({"error": True}))


class TestRegistrarProxyAutoMatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RegistrationTaskManager._instance = None
        ProxySellerService._pool_cache.clear()
        self._custom_patch = patch(
            "backend.app.services.proxyseller.load_custom_proxy_items",
            return_value=[],
        )
        self._custom_patch.start()
        self.addCleanup(self._custom_patch.stop)

    async def test_run_logs_required_region_match_line(self):
        manager = RegistrationTaskManager.get_instance()
        task_id = manager.create_task()
        proxy = normalize_proxy_item(_chile_raw())

        class FakeSvc:
            def __init__(self, api_key):
                self.api_key = api_key

            async def get_proxy_list(self, country=None, refresh=False, include_health=True):
                return [proxy] if country in (None, "cl", "chile") else []

            async def select_best_proxy(self, **kwargs):
                return {
                    "success": True,
                    "matched": True,
                    "fallback_used": False,
                    "proxy": {**proxy, "healthy": True, "egress_ip": "181.43.10.22", "egress_country": "Chile"},
                    "message": "ok",
                }

            async def close(self):
                return None

        config = SimpleNamespace(
            use_proxy_seller_auto=True,
            proxy_seller_key="demo-key",
            fallback_proxy=SimpleNamespace(model_dump=lambda: {
                "proxy_type": "socks5", "addr": "127.0.0.1", "port": 10808,
            }),
        )
        with patch("backend.app.services.proxyseller.ProxySellerService", FakeSvc):
            resolved = await RegistrationOrchestrator._resolve_proxy_seller_auto(
                config, "cl", task_id, manager
            )
        self.assertEqual(resolved["addr"], "181.43.10.22")
        logs = "\n".join(manager.get_task(task_id)["logs"])
        self.assertIn(
            "[多径中继网关] 成功从 Proxy-Seller API 自动匹配到 CL 区域代理: socks5://181.43.10.22:50101",
            logs,
        )


class TestCustomPoolRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ProxySellerService._pool_cache.clear()
        ProxySellerService._health.clear()
        ProxySellerService._rr_cursor.clear()

    async def test_select_prefers_custom_pool_for_indonesia(self):
        custom = {
            "id": "custom-id-1",
            "proxy_type": "socks5",
            "addr": "10.8.0.21",
            "port": 41080,
            "username": "id_user",
            "password": "id_pass",
            "country": "Indonesia",
            "country_code": "id",
            "source": "custom",
            "catalog_type": "custom",
            "healthy": True,
        }
        with patch("backend.app.services.proxyseller.load_custom_proxy_items", return_value=[custom]):
            svc = ProxySellerService("", include_static=True)
            try:
                selected = await svc.select_best_proxy(target_country="id", allow_fallback=False)
                self.assertTrue(selected["success"])
                self.assertTrue(selected["matched"])
                self.assertEqual(selected["source"], "custom_pool")
                self.assertEqual(selected["proxy"]["addr"], "10.8.0.21")
                self.assertEqual(selected["proxy"]["country_code"], "id")
                self.assertIn("自建", selected["message"])
            finally:
                await svc.close()

    async def test_custom_chile_does_not_steal_india_static(self):
        custom = {
            "id": "custom-cl-1",
            "proxy_type": "socks5",
            "addr": "186.1.2.3",
            "port": 1080,
            "username": "cl_user",
            "password": "cl_pass",
            "country": "Chile",
            "country_code": "cl",
            "source": "custom",
            "catalog_type": "custom",
        }
        with patch("backend.app.services.proxyseller.load_custom_proxy_items", return_value=[custom]):
            svc = ProxySellerService("", include_static=True)
            try:
                india = await svc.select_best_proxy(target_country="in", allow_fallback=False)
                self.assertTrue(india["success"])
                self.assertEqual(india["proxy"]["country_code"], "in")
                self.assertNotEqual(india["proxy"]["addr"], "186.1.2.3")
            finally:
                await svc.close()


def _resident_row(title, country, **overrides):
    ports = overrides.pop("ports", "10")
    row = {
        "id": overrides.pop("id", 1),
        "title": title,
        "login": overrides.pop("login", "tg_login"),
        "password": overrides.pop("password", "tg_pass"),
        "geo": overrides.pop("geo", [{"country": country, "region": "", "city": "", "isp": ""}]),
        "export": overrides.pop("export", {"ports": ports, "ext": "txt"}),
        "rotation": overrides.pop("rotation", 3600),
    }
    row.update(overrides)
    return row


def _path_after_key(url, api_key="test-key"):
    marker = f"/{api_key}/"
    if marker in url:
        return url.split(marker, 1)[1]
    return url


class TestResidentTgLists(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ProxySellerService._pool_cache.clear()
        ProxySellerService._health.clear()
        ProxySellerService._rr_cursor.clear()
        self._custom_patch = patch(
            "backend.app.services.proxyseller.load_custom_proxy_items",
            return_value=[],
        )
        self._custom_patch.start()
        self.addCleanup(self._custom_patch.stop)

    def _install_router(self, svc, *, lists=None, proxy_payload=None, add_payload=None, package=None):
        self.add_calls = []
        lists_payload = {"status": "success", "data": lists if lists is not None else [], "errors": []}
        proxy_payload = proxy_payload or {"status": "success", "data": {"items": []}, "errors": []}
        package_payload = {
            "status": "success",
            "data": package if package is not None else {"is_active": True},
            "errors": [],
        }

        async def handler(method, url, params=None, json=None):
            path = _path_after_key(url, svc.api_key)
            if path.startswith("resident/lists"):
                return DummyResponse(lists_payload)
            if path.startswith("resident/package"):
                return DummyResponse(package_payload)
            if path.startswith("resident/list/add"):
                self.add_calls.append({"method": method, "json": json, "path": path})
                return DummyResponse(add_payload or {"status": "success", "data": {}, "errors": []})
            return DummyResponse(proxy_payload)

        svc.client.request = AsyncMock(side_effect=handler)
        return svc

    def test_bot_and_xxxtg_title_helpers(self):
        self.assertTrue(is_bot_list_title("bot_api_IN"))
        self.assertTrue(is_bot_list_title("bot_api_US"))
        self.assertTrue(is_bot_list_title("bot_api_BR"))
        self.assertTrue(is_bot_list_title("bot_账号检查用测试"))
        self.assertTrue(is_bot_list_title("bot_api"))
        self.assertTrue(is_bot_list_title("IN_bot"))
        self.assertTrue(is_bot_list_title("foo_bot_bar"))
        self.assertFalse(is_bot_list_title("CL_tg"))
        self.assertFalse(is_bot_list_title("ZA_tg"))
        self.assertFalse(is_bot_list_title("IN_tg"))

        self.assertTrue(is_xxxtg_list_title("CL_tg"))
        self.assertTrue(is_xxxtg_list_title("cl_tg"))
        self.assertTrue(is_xxxtg_list_title("ZA_tg"))
        self.assertFalse(is_xxxtg_list_title("bot_api_IN"))
        self.assertFalse(is_xxxtg_list_title("bot_tg"))
        self.assertFalse(is_xxxtg_list_title("IN_bot"))
        self.assertFalse(is_xxxtg_list_title("bot_api"))
        self.assertEqual(country_code_from_tg_title("CL_tg"), "cl")
        self.assertEqual(country_code_from_tg_title("za_tg"), "za")

    def test_parse_geo_and_export_ports(self):
        from_list = parse_resident_geo([{"country": "IN", "region": "", "city": "", "isp": ""}])
        self.assertEqual(from_list["country"], "IN")
        from_dict = parse_resident_geo({"country": "CL"})
        self.assertEqual(from_dict["country"], "CL")
        self.assertEqual(parse_export_ports({"ports": "50"}), 50)
        self.assertEqual(parse_export_ports({"ports": 10}), 10)

        bot_proxies = resident_list_to_proxies(_resident_row("bot_api_IN", "IN"))
        self.assertEqual(bot_proxies, [])
        za = resident_list_to_proxies(_resident_row("ZA_tg", "ZA", ports="50"), max_ports=10)
        self.assertEqual(len(za), 10)
        self.assertEqual(za[0]["addr"], STATIC_RESIDENTIAL_HOST)
        self.assertEqual(za[0]["port"], 10000)
        self.assertEqual(za[-1]["port"], 10009)
        self.assertEqual(za[0]["country_code"], "za")
        self.assertEqual(za[0]["source"], "resident_tg")
        self.assertTrue(is_resident_tg(za[0]))
        self.assertNotIn("raw", za[0])
        self.assertIn("_c_ZA", za[0]["username"])
        self.assertIn("ttl_24h", za[0]["username"])

        pinned = resident_list_to_proxies(
            _resident_row("CL_tg", "CL", login="dead_list_login", password="dead_list_pass"),
            max_ports=1,
            tools_login="api2toolsuser",
            tools_password="tools_secret",
        )
        self.assertEqual(len(pinned), 1)
        self.assertTrue(pinned[0]["username"].startswith("api2toolsuser_c_CL_"))
        self.assertIn("s_tgcl10000", pinned[0]["username"])
        self.assertEqual(pinned[0]["password"], "tools_secret")
        self.assertNotIn("dead_list_login", pinned[0]["username"])

    async def test_bot_api_lists_are_never_candidates(self):
        svc = ProxySellerService("test-key", include_static=False)
        self._install_router(
            svc,
            lists=[
                _resident_row("bot_api_IN", "IN", id=11, login="bot_in"),
                _resident_row("bot_api_US", "US", id=12, login="bot_us"),
                _resident_row("ZA_tg", "ZA", id=13, login="za_login"),
            ],
        )
        try:
            items = await svc.get_proxy_list(refresh=True, include_health=False)
            tg_items = [item for item in items if is_resident_tg(item)]
            self.assertTrue(tg_items)
            self.assertTrue(all(item.get("list_title") == "ZA_tg" for item in tg_items))
            self.assertFalse(any(
                is_bot_list_title(item.get("list_title")) for item in items
            ))
            summary = await svc.summarize_resident_tg_lists()
            self.assertEqual(summary["bot_skipped"], 2)
            self.assertEqual([row["title"] for row in summary["lists"]], ["ZA_tg"])
            dumped = str(summary)
            self.assertNotIn("tg_pass", dumped)
            self.assertNotIn("bot_in", dumped)
            self.assertTrue(any("_c_ZA" in (item.get("username") or "") for item in tg_items))
        finally:
            await svc.close()

    async def test_ensure_existing_za_tg_does_not_create(self):
        svc = ProxySellerService("test-key", include_static=False)
        self._install_router(
            svc,
            lists=[_resident_row("ZA_tg", "ZA", id=3, login="za_user", password="za_pass")],
        )
        try:
            result = await svc.ensure_tg_resident_list("za", create=True)
            self.assertTrue(result["success"])
            self.assertFalse(result["created"])
            self.assertEqual(self.add_calls, [])
            self.assertTrue(result["proxies"])
            node = result["proxies"][0]
            self.assertEqual(node["addr"], STATIC_RESIDENTIAL_HOST)
            self.assertEqual(node["port"], 10000)
            self.assertEqual(node["country_code"], "za")
            self.assertNotIn("za_pass", result["message"] or "")
        finally:
            await svc.close()

    async def test_ensure_missing_cl_tg_posts_add(self):
        svc = ProxySellerService("test-key", include_static=False)
        created = _resident_row("CL_tg", "CL", id=99, login="cl_tg_user", password="cl_tg_pass")
        self._install_router(
            svc,
            lists=[_resident_row("bot_api_IN", "IN", id=1, login="bot_in")],
            add_payload={"status": "success", "data": created, "errors": []},
        )
        try:
            result = await svc.ensure_tg_resident_list("cl", create=True)
            self.assertTrue(result["success"])
            self.assertTrue(result["created"])
            self.assertEqual(result["title"], "CL_tg")
            self.assertEqual(len(self.add_calls), 1)
            body = self.add_calls[0]["json"]
            self.assertEqual(body["title"], "CL_tg")
            self.assertEqual(body["geo"]["country"], "CL")
            self.assertEqual(body["export"]["ports"], 10)
            self.assertEqual(body["rotation"], 3600)
            self.assertNotIn("cl_tg_pass", result["message"] or "")
            self.assertEqual(result["proxies"][0]["addr"], STATIC_RESIDENTIAL_HOST)
            self.assertEqual(result["proxies"][0]["country_code"], "cl")
        finally:
            await svc.close()

    async def test_ensure_create_false_returns_hint(self):
        svc = ProxySellerService("test-key", include_static=False)
        self._install_router(svc, lists=[_resident_row("bot_api_MX", "MX")])
        try:
            result = await svc.ensure_tg_resident_list("cl", create=False)
            self.assertFalse(result["success"])
            self.assertFalse(result["created"])
            self.assertEqual(result["proxies"], [])
            self.assertIn("CL_tg", result["hint"] or result["message"])
            self.assertEqual(self.add_calls, [])
        finally:
            await svc.close()

    async def test_ensure_unknown_country_does_not_invent(self):
        svc = ProxySellerService("test-key", include_static=False)
        self._install_router(svc, lists=[])
        try:
            result = await svc.ensure_tg_resident_list("xx", create=True)
            self.assertFalse(result["success"])
            self.assertEqual(self.add_calls, [])
        finally:
            await svc.close()

    async def test_refresh_pool_exposes_resident_tg_source(self):
        svc = ProxySellerService("test-key", include_static=False)
        self._install_router(
            svc,
            lists=[_resident_row("IN_tg", "IN", id=7, login="in_tg_user")],
            proxy_payload={"status": "success", "data": {"ipv4": [_usa_raw()]}, "errors": []},
        )
        try:
            items = await svc.get_proxy_list(refresh=True, include_health=False)
            tg_items = [item for item in items if item.get("source") == "resident_tg"]
            self.assertTrue(tg_items)
            self.assertEqual(tg_items[0]["list_title"], "IN_tg")
            self.assertEqual(svc.cache_meta().get("resident_count"), len(tg_items))
        finally:
            await svc.close()


class TestSchemas(unittest.TestCase):
    def test_request_models(self):
        req = ProxySellerAutoSelectRequest(target_country="cl", apply_fallback=True)
        self.assertTrue(req.apply_fallback)
        listing = ProxySellerListResponse(success=True, message="ok", total=0)
        self.assertEqual(listing.total, 0)
        batch = ProxySellerTestAllRequest(country="id", limit=5)
        self.assertEqual(batch.limit, 5)
        ensure = ProxySellerEnsureTgRequest(target_country="za", create=True, ports=10)
        self.assertTrue(ensure.create)
        resident = ProxySellerResidentListsResponse(success=True, message="ok", bot_skipped=3)
        self.assertEqual(resident.bot_skipped, 3)


if __name__ == "__main__":
    unittest.main()
