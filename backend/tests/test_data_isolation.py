"""守住 pytest 的数据目录隔离：跑用例不得读写仓库 data/。

历史上 banned_phones_cache.json / edgenode_auth_password / config.json 都落在真实
data/ 里，导致「本机绿、干净检出红」以及连跑两遍结果不一致。conftest 已把 DATA_DIR
改到临时目录，这里把该约束固化成断言，防止后续有人再把路径写死回仓库。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPO_DATA_DIR = REPO_ROOT / "data"


class TestDataDirIsolation(unittest.TestCase):
    def test_config_data_dir_is_not_repo_data(self):
        from backend.app.config import DATA_DIR, DEVICE_DBS_DIR, SESSIONS_DIR

        self.assertNotEqual(DATA_DIR, REPO_DATA_DIR.resolve())
        for path in (DATA_DIR, DEVICE_DBS_DIR, SESSIONS_DIR):
            self.assertFalse(
                str(path).startswith(str(REPO_DATA_DIR.resolve())),
                f"{path} 仍指向仓库 data/，用例会污染生产数据",
            )

    def test_stateful_singletons_live_under_test_data_dir(self):
        from backend.app.config import DATA_DIR
        from backend.app.services.auth import password_file_path, secret_file_path
        from backend.app.services.banned_phones import DEFAULT_CACHE_PATH
        from backend.app.services.push_token_vault import VAULT_FILE

        for path in (DEFAULT_CACHE_PATH, VAULT_FILE, password_file_path(), secret_file_path()):
            self.assertEqual(
                Path(path).parent,
                DATA_DIR,
                f"{path} 未跟随 DATA_DIR，隔离会漏",
            )

    def test_repo_banned_phones_cache_is_untouched_by_tests(self):
        from backend.app.services.banned_phones import DEFAULT_CACHE_PATH

        self.assertNotEqual(
            Path(DEFAULT_CACHE_PATH),
            REPO_DATA_DIR / "banned_phones_cache.json",
        )


if __name__ == "__main__":
    unittest.main()
