"""APKMirror 真实版本矩阵 + Integrity versionCode 与显示 build 分离。"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.device_generator import (  # noqa: E402
    pick_app_version,
    synthesize_rows,
    validate_android_rows,
    write_registrator_db,
)
from backend.app.services.device_db_manager import parse_registrator_db  # noqa: E402
from backend.app.services.registrar import RegistrationOrchestrator  # noqa: E402
from backend.app.services.telegram_android_releases import (  # noqa: E402
    PUBLIC_ANDROID_RELEASE,
    TELEGRAM_9_RELEASE,
    TELEGRAM_ANDROID_RELEASES,
    TELEGRAM_X_RELEASE,
    attach_apk_version_code,
    is_fake_official_build,
    resolve_apk_version_code,
)


class TestOfficialReleaseCatalog(unittest.TestCase):
    def test_catalog_has_multiple_real_builds_and_no_fakes(self):
        builds = {item.app_build for item in TELEGRAM_ANDROID_RELEASES}
        versions = {item.version for item in TELEGRAM_ANDROID_RELEASES}
        self.assertGreaterEqual(len(TELEGRAM_ANDROID_RELEASES), 12)
        self.assertIn("12.8.3", versions)
        self.assertIn("12.7.3", versions)
        self.assertIn("12.9.1", versions)
        self.assertIn("12.9.2", versions)
        self.assertIn("11.14.1", versions)
        self.assertIn("10.14.5", versions)
        self.assertIn("69222", builds)
        self.assertIn("67502", builds)
        self.assertIn("69792", builds)
        self.assertNotIn("68420", builds)
        self.assertNotIn("60118", builds)
        self.assertNotIn("42207", builds)
        for item in TELEGRAM_ANDROID_RELEASES:
            self.assertFalse(is_fake_official_build(item.app_build), item.app_build)
            self.assertGreater(item.apk_version_code, 0)

    def test_synth_mainline_only_uses_catalog(self):
        known = {item.app_version for item in TELEGRAM_ANDROID_RELEASES}
        rows = synthesize_rows("pt", 40, seed=21)
        validate_android_rows(rows, "pt")
        seen = {row["app_version"] for row in rows}
        self.assertTrue(seen <= known)
        self.assertTrue(all(int(row["apk_version_code"]) > 0 for row in rows))
        self.assertTrue(all(not is_fake_official_build(row["app_build"]) for row in rows))

    def test_public_and_x_and_nine_store_integrity_code(self):
        public = synthesize_rows("pt", 10, seed=3, app_type="telegram_android_public")
        self.assertTrue(all(row["app_version"] == PUBLIC_ANDROID_RELEASE.app_version for row in public))
        self.assertTrue(all(int(row["apk_version_code"]) == PUBLIC_ANDROID_RELEASE.apk_version_code for row in public))
        x_rows = synthesize_rows("pt", 10, seed=4, app_type="telegram_x")
        self.assertTrue(all(row["app_version"] == TELEGRAM_X_RELEASE.app_version for row in x_rows))
        self.assertTrue(all(int(row["apk_version_code"]) == 1692020 for row in x_rows))
        self.assertTrue(all(str(row["app_build"]) == "1692" for row in x_rows))
        nine = synthesize_rows("pt", 10, seed=5, app_type="telegram_9")
        self.assertTrue(all(row["app_version"] == TELEGRAM_9_RELEASE.app_version for row in nine))
        self.assertTrue(all(int(row["apk_version_code"]) == 33632 for row in nine))

    def test_sqlite_persists_apk_version_code(self):
        rows = synthesize_rows("pt", 10, seed=8, app_type="telegram_x")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x.db"
            write_registrator_db(rows, dest)
            parsed = parse_registrator_db(dest)
        self.assertEqual(len(parsed), 10)
        self.assertTrue(all(int(item["apk_version_code"]) == 1692020 for item in parsed))


class TestIntegrityVersionCode(unittest.TestCase):
    def test_official_display_build_maps_to_same_apk_code(self):
        self.assertEqual(
            resolve_apk_version_code({
                "app_version": "12.8.3 (69222)",
                "app_version_pure": "12.8.3",
                "app_build": "69222",
                "app_name": "tg",
                "lang_pack": "android",
            }),
            69222,
        )
        self.assertEqual(
            RegistrationOrchestrator._app_version_code({
                "app_version": "12.9.1 (69792)",
                "app_version_pure": "12.9.1",
                "app_build": "69792",
                "app_name": "tg",
            }),
            69792,
        )

    def test_telegram_x_does_not_send_display_build(self):
        profile = {
            "app_type": "telegram_x",
            "app_name": "tg_x",
            "lang_pack": "android_x",
            "app_version": "0.26.5.1692",
            "app_version_pure": "0.26.5",
            "app_build": "1692",
        }
        self.assertEqual(resolve_apk_version_code(profile), 1692020)
        self.assertEqual(RegistrationOrchestrator._app_version_code(profile), 1692020)
        wrong = attach_apk_version_code({**profile, "apk_version_code": 1692})
        self.assertEqual(wrong["apk_version_code"], 1692020)

    def test_legacy_telegram_9_alias(self):
        self.assertEqual(
            resolve_apk_version_code({
                "app_type": "telegram_9",
                "app_version": "9.6.7 (33219)",
                "app_version_pure": "9.6.7",
                "app_build": "33219",
                "app_name": "tg",
            }),
            33632,
        )

    def test_pick_app_version_never_returns_fake_build(self):
        rng = random.Random(99)
        for sdk in (29, 31, 33):
            for _ in range(20):
                app_version, _pure, app_build, apk_code = pick_app_version(sdk, rng)
                self.assertFalse(is_fake_official_build(app_build), app_version)
                self.assertGreater(apk_code, 0)


if __name__ == "__main__":
    unittest.main()
