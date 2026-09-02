"""官方 api_id/api_hash 配对校验与 telegram_x hash 修复回归。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.chdir(REPO_ROOT)

from backend.app.services.device_profile import (  # noqa: E402
    DEFAULT_PROFILES,
    OFFICIAL_API_CREDENTIALS,
    DeviceProfileManager,
    apply_official_api_id,
    normalize_official_api_credentials,
)


class OfficialApiCredentialsTests(unittest.TestCase):
    def test_telegram_x_hash_matches_opentele(self):
        tg_x = DEFAULT_PROFILES["telegram_x"]
        self.assertEqual(tg_x["api_id"], 21724)
        self.assertEqual(
            tg_x["api_hash"],
            OFFICIAL_API_CREDENTIALS[21724],
        )

    def test_normalize_fixes_api4_with_api6_hash(self):
        wrong = {
            "api_id": 4,
            "api_hash": OFFICIAL_API_CREDENTIALS[6],
        }
        fixed = normalize_official_api_credentials(wrong)
        self.assertEqual(fixed["api_hash"], OFFICIAL_API_CREDENTIALS[4])
        self.assertTrue(fixed.get("api_hash_corrected"))

    def test_normalize_leaves_matching_pair(self):
        ok = {"api_id": 4, "api_hash": OFFICIAL_API_CREDENTIALS[4]}
        out = normalize_official_api_credentials(ok)
        self.assertNotIn("api_hash_corrected", out)

    def test_resolve_effective_credentials_custom_mode_fixes_mismatch(self):
        profile = {"api_id": 4, "api_hash": OFFICIAL_API_CREDENTIALS[6]}
        config = type("Cfg", (), {
            "official_client_emulation": False,
            "api_credential_mode": "custom",
            "custom_api_id": 4,
            "custom_api_hash": OFFICIAL_API_CREDENTIALS[6],
        })()
        resolved = DeviceProfileManager.resolve_effective_credentials(
            profile, config, has_push_token=True
        )
        self.assertEqual(resolved["api_hash"], OFFICIAL_API_CREDENTIALS[4])
        self.assertTrue(resolved.get("api_hash_corrected"))

    def test_apply_official_api_id_4_uses_014b_hash(self):
        out = apply_official_api_id({"api_id": 6, "api_hash": OFFICIAL_API_CREDENTIALS[6]}, 4)
        self.assertEqual(out["api_id"], 4)
        self.assertEqual(out["api_hash"], "014b35b6184100b085b0d0572f9b5103")
        self.assertTrue(out.get("api_hash_corrected"))

    def test_telegram_android_public_template_hash(self):
        pub = DEFAULT_PROFILES["telegram_android_public"]
        self.assertEqual(pub["api_id"], 4)
        self.assertEqual(pub["api_hash"], OFFICIAL_API_CREDENTIALS[4])


if __name__ == "__main__":
    unittest.main()
