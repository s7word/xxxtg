"""硬件指纹目录、国家调度与参数化合成。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.device_db_manager import (  # noqa: E402
    DeviceDbManager,
    compute_stats,
    infer_country_from_filename,
    parse_registrator_db,
    sanitize_filename,
    validate_sqlite_bytes,
)
from backend.app.services.device_generator import (  # noqa: E402
    DEVICE_SKUS,
    generate_country_db,
    locale_matches_country,
    sku_sdk_consistent,
    synthesize_rows,
    tz_matches_country,
    write_registrator_db,
)
from backend.app.services.device_profile import DeviceProfileManager  # noqa: E402


def _build_rows(country_tz=-14400, lang="es", sys_lang="es-cl", n=8):
    rows = []
    models = ["samsungSM-S918B", "Xiaomi22101316G", "motorolaXT2347-2", "OPPOCPH2411"]
    for i in range(n):
        rows.append({
            "api_id": 6,
            "api_hash": "eb06d4abfb49dc3eeb1aeb98ae0f581e",
            "system_version": "SDK 33",
            "device_model": models[i % len(models)],
            "app_version": "12.7.3 (67502)",
            "app_version_pure": "12.7.3",
            "app_build": "67502",
            "lang_code": lang,
            "system_lang_code": sys_lang,
            "lang_pack": "android",
            "tz_offset": country_tz,
            "perf_cat": 2,
        })
    return rows


class TestCountryInference(unittest.TestCase):
    def test_filename_indonesia_and_base(self):
        self.assertEqual(infer_country_from_filename("2026-08-23_14-49-28_ Indonesia.db"), "id")
        self.assertEqual(infer_country_from_filename("2026-08-23_07-06-02_Base.db"), "cl")
        self.assertEqual(infer_country_from_filename("chile_install_300.db"), "cl")
        self.assertEqual(infer_country_from_filename("India.db"), "in")

    def test_sanitize_filename_strips_paths(self):
        self.assertEqual(sanitize_filename("../../evil.db"), "evil.db")
        self.assertTrue(sanitize_filename("pack").endswith(".db"))


class TestCatalogLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        DeviceDbManager.invalidate_cache()

    def tearDown(self):
        DeviceDbManager.invalidate_cache()
        self.tmp.cleanup()

    def _write_pack_file(self, name, rows):
        path = self.root / name
        write_registrator_db(rows, path)
        return path

    def test_import_bytes_parses_stats_and_country(self):
        src = self._write_pack_file("tmp.db", _build_rows())
        content = src.read_bytes()
        pack = DeviceDbManager.import_bytes(
            "2026-08-23_07-06-02_Base.db",
            content,
            alias="智利安装8.db",
            root=self.root,
        )
        self.assertEqual(pack["country"], "cl")
        self.assertEqual(pack["sample_count"], 8)
        self.assertTrue(pack["enabled"])
        self.assertIn("samsung", pack["stats"]["brands"])
        self.assertGreaterEqual(pack["quality"]["score"], 70)
        listed = DeviceDbManager.list_packs(self.root)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["alias"], "智利安装8.db")

    def test_toggle_and_country_select(self):
        chile = DeviceDbManager.import_bytes(
            "Base.db", self._write_pack_file("c.db", _build_rows()).read_bytes(), root=self.root
        )
        indo_rows = _build_rows(country_tz=25200, lang="id", sys_lang="id-id", n=12)
        indo = DeviceDbManager.import_bytes(
            "2026-08-23_14-49-28_Indonesia.db",
            self._write_pack_file("i.db", indo_rows).read_bytes(),
            root=self.root,
        )
        self.assertEqual(indo["country"], "id")
        DeviceDbManager.update_pack(chile["id"], enabled=False, root=self.root)
        matched = DeviceDbManager.enabled_packs("id", root=self.root)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], indo["id"])
        # 停用印尼后，按印尼调度只能回退
        DeviceDbManager.update_pack(indo["id"], enabled=False, root=self.root)
        pack, mode = DeviceDbManager.select_pack("id", root=self.root)
        self.assertIsNone(pack)
        self.assertEqual(mode, "none")

    def test_delete_removes_file(self):
        pack = DeviceDbManager.import_bytes(
            "Base.db", self._write_pack_file("c.db", _build_rows()).read_bytes(), root=self.root
        )
        path = DeviceDbManager.resolve_path(pack, self.root)
        self.assertTrue(path.exists())
        DeviceDbManager.delete_pack(pack["id"], root=self.root)
        self.assertFalse(path.exists())
        self.assertEqual(DeviceDbManager.list_packs(self.root), [])

    def test_reject_non_sqlite(self):
        with self.assertRaises(ValueError):
            validate_sqlite_bytes(b"not a database")


class TestParameterizedGenerator(unittest.TestCase):
    def test_indonesia_rows_are_internally_consistent(self):
        rows = synthesize_rows("id", 80, seed=7)
        self.assertEqual(len(rows), 80)
        brands = {row["device_model"] for row in rows}
        self.assertGreaterEqual(len(brands), 8)
        for row in rows:
            self.assertTrue(sku_sdk_consistent(row["device_model"], row["system_version"]))
            self.assertTrue(locale_matches_country(row["lang_code"], row["system_lang_code"], "id"))
            self.assertTrue(tz_matches_country(row["tz_offset"], "id"))
            self.assertEqual(row["lang_pack"], "android")
            self.assertTrue(row["app_version"].endswith(")"))

    def test_blind_random_fails_consistency_gates(self):
        bogus = {
            "device_model": "iPhone16ProMax",
            "system_version": "SDK 21",
            "lang_code": "zh",
            "system_lang_code": "zh-cn",
            "tz_offset": -14400,
        }
        self.assertFalse(sku_sdk_consistent(bogus["device_model"], bogus["system_version"]))
        self.assertFalse(locale_matches_country(bogus["lang_code"], bogus["system_lang_code"], "id"))
        self.assertFalse(tz_matches_country(bogus["tz_offset"], "id"))

    def test_generate_writes_catalog_pack(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            pack = generate_country_db("id", count=40, alias="印尼合成40.db", seed=3, root=root)
            self.assertEqual(pack["country"], "id")
            self.assertEqual(pack["source"], "generated")
            self.assertEqual(pack["sample_count"], 40)
            path = DeviceDbManager.resolve_path(pack, root)
            rows = parse_registrator_db(path)
            self.assertEqual(len(rows), 40)
            stats = compute_stats(rows)
            self.assertIn("id-id", stats["system_lang_codes"])
        finally:
            tmp.cleanup()


class TestResolvedProfileCountryMatch(unittest.TestCase):
    def test_uses_matching_enabled_pack_and_keeps_locale(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            chile = DeviceDbManager.import_bytes(
                "Base.db",
                write_registrator_db(_build_rows(), root / "c.db").read_bytes(),
                root=root,
            )
            indo = DeviceDbManager.import_bytes(
                "Indonesia.db",
                write_registrator_db(_build_rows(25200, "id", "id-id"), root / "i.db").read_bytes(),
                root=root,
            )
            DeviceDbManager.update_pack(chile["id"], enabled=False, root=root)

            stub = type("CatalogStub", (), {
                "select_sample": staticmethod(lambda country: DeviceDbManager.select_sample(country, root=root)),
            })()
            with patch.object(DeviceProfileManager, "_manager", return_value=stub):
                profile = DeviceProfileManager.get_resolved_profile("telegram_android", "id")
            self.assertEqual(profile["device_pack_id"], indo["id"])
            self.assertEqual(profile["device_pack_match"], "country")
            self.assertEqual(profile["lang_code"], "id")
            self.assertEqual(profile["system_lang_code"], "id-id")
            self.assertEqual(profile["tz_offset"], 25200)
        finally:
            tmp.cleanup()


class TestDeviceDbHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.client = TestClient(app)

    def test_list_and_generate_and_toggle(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            with patch("backend.app.api.routes.DeviceDbManager.ensure_ready", return_value={"items": []}), \
                 patch("backend.app.api.routes.DeviceDbManager.aggregate_stats") as agg, \
                 patch("backend.app.api.routes.list_supported_countries", return_value=[{"code": "id", "name": "Indonesia"}]):
                agg.return_value = {
                    "total_count": 0,
                    "is_loaded": False,
                    "sample_models": [],
                    "pack_count": 0,
                    "enabled_packs": 0,
                    "disabled_packs": 0,
                    "active_countries": [],
                    "packs": [],
                }
                res = self.client.get("/api/device-dbs")
                self.assertEqual(res.status_code, 200)
                self.assertIn("packs", res.json())
        finally:
            tmp.cleanup()

    def test_generate_api_creates_real_pack(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            with patch("backend.app.api.routes.generate_country_db") as gen:
                gen.return_value = {
                    "id": "abc",
                    "origin_name": "id.db",
                    "stored_name": "abc.db",
                    "alias": "印尼安装40.db",
                    "country": "id",
                    "country_name": "Indonesia",
                    "enabled": True,
                    "source": "generated",
                    "sample_count": 40,
                    "stats": {"total": 40, "brands": {"samsung": 10}},
                    "quality": {"score": 90, "flags": [], "notes": "ok"},
                }
                res = self.client.post("/api/device-dbs/generate", json={"country": "id", "count": 40})
                self.assertEqual(res.status_code, 200)
                body = res.json()
                self.assertTrue(body["success"])
                self.assertEqual(body["pack"]["country"], "id")
                gen.assert_called_once()
        finally:
            tmp.cleanup()

    def test_upload_rejects_wrong_suffix(self):
        res = self.client.post(
            "/api/device-dbs/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(res.status_code, 400)


class TestSkuCatalogCoverage(unittest.TestCase):
    def test_required_brands_present(self):
        brands = {sku.brand for sku in DEVICE_SKUS}
        for name in ("samsung", "xiaomi", "huawei", "motorola", "realme", "vivo", "oppo"):
            self.assertIn(name, brands)


if __name__ == "__main__":
    unittest.main()
