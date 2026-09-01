"""pytest 会话隔离：DATA_DIR 指向临时目录，用例不再读写仓库 data/。

``backend.app.config`` 在 **导入时** 就把 DATA_DIR / DEVICE_DBS_DIR 解析成模块常量，
``banned_phones.DEFAULT_CACHE_PATH``、``push_token_vault.VAULT_FILE``、
``auth.password_file_path()`` 全部挂在上面。因此环境变量必须在任何 backend 模块被
导入之前改掉，否则用例会命中生产 data/：既让结果依赖上一轮残留的
banned_phones_cache.json / edgenode_auth_password，也会把 config.json 回写一遍。
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("EDGENODE_AUTH_DISABLED", "1")
os.environ.setdefault("EDGENODE_SKIP_PUSH_REFUND_WAIT", "1")

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="xxxtg-pytest-data-"))
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DEVICE_DBS_DIR"] = str(_TEST_DATA_DIR / "device_dbs")


def test_data_dir() -> Path:
    """本轮 pytest 专属的数据目录，需要落盘的用例可以直接往里写。"""
    return _TEST_DATA_DIR


@atexit.register
def _cleanup_test_data_dir() -> None:
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
