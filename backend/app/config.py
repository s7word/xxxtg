import os
import json
import logging
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows / 无 fcntl 环境
    fcntl = None

from backend.app.models.schemas import AppConfigModel
from backend.app.services.attestation_urls import is_antisafety_url, is_reghelp_url

logger = logging.getLogger("NodeSimulationConfig")

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
CONFIG_FILE = DATA_DIR / "config.json"
SESSIONS_DIR = DATA_DIR / "sessions"
SESSION_ARTIFACTS_DIR = SESSIONS_DIR  # 学术化规范别名
DEVICE_DBS_DIR = Path(os.getenv("DEVICE_DBS_DIR", str(DATA_DIR / "device_dbs"))).resolve()

_CONFIG_MEM_LOCK = threading.RLock()


def _resolve_lod_user_dir() -> Path:
    """定位已有测试会话目录 (lod_user/)，兼容仓库根、容器挂载与环境变量覆盖。"""
    env = os.getenv("LOD_USER_DIR")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    candidates = [
        Path("./lod_user").resolve(),
        here.parents[2] / "lod_user",  # <repo>/lod_user
        Path("/app/lod_user"),
        Path("/workspace/lod_user"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return Path("./lod_user").resolve()


LOD_USER_DIR = _resolve_lod_user_dir()

DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
DEVICE_DBS_DIR.mkdir(parents=True, exist_ok=True)


def _config_lock_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".lock")


def _tmp_config_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".tmp")


def corrupted_config_backup_path(dest: Optional[Path] = None) -> Path:
    target = Path(dest) if dest is not None else CONFIG_FILE
    return target.with_name("config.json.corrupted.bak")


@contextmanager
def config_io_lock(dest: Optional[Path] = None):
    """进程内 RLock + 跨进程 fcntl 文件锁，保护 config.json 读写。"""
    target = Path(dest) if dest is not None else CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _config_lock_path(target)
    with _CONFIG_MEM_LOCK:
        fh = open(lock_path, "a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            fh.close()


def backup_corrupted_config(src: Optional[Path] = None) -> Optional[Path]:
    """损坏的 config.json 先备份为 config.json.corrupted.bak，再允许回退默认配置。"""
    source = Path(src) if src is not None else CONFIG_FILE
    if not source.exists():
        return None
    bak = corrupted_config_backup_path(source)
    shutil.copy2(source, bak)
    logger.warning("已将损坏的配置备份至 %s，避免直接覆盖丢失原有内容", bak)
    return bak


def _atomic_write_unlocked(payload: Dict[str, Any], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_config_path(dest)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, dest)
    return dest


def atomic_write_config(payload: Dict[str, Any], dest: Optional[Path] = None) -> Path:
    """写入 config.json.tmp 后 os.replace 原子替换，避免半截 JSON。"""
    target = Path(dest) if dest is not None else CONFIG_FILE
    with config_io_lock(target):
        return _atomic_write_unlocked(payload, target)


class ConfigManager:
    """全局仿真配置与状态持久化管理器"""
    _instance = None
    _config: AppConfigModel = None

    @classmethod
    def get_instance(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = ConfigManager()
        return cls._instance

    def __init__(self):
        self.load_config()

    def load_config(self) -> AppConfigModel:
        with config_io_lock(CONFIG_FILE):
            if CONFIG_FILE.exists():
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._config = AppConfigModel(**data)
                    logger.info("已从持久化存储区载入系统配置快照")
                    migrated = self._maybe_migrate_official_api_defaults(data)
                    if migrated is not None:
                        self._config = migrated
                        try:
                            _atomic_write_unlocked(self._config.model_dump(), CONFIG_FILE)
                            logger.warning(
                                "已迁移注册凭证策略至官方 api_id 默认值 "
                                "(official_client_emulation=true, api_credential_mode=official, "
                                "code_delivery_mode=push_required, official_api_id=6)"
                            )
                        except Exception as exc:
                            logger.warning("回写官方 api 默认策略迁移失败: %s", exc)
                    # 旧配置可能把 api.reghelp.net 混进 antisafety_base_urls（或反过来），
                    # Pydantic 校验器会隔离地址；启动时强制清洗并回写，避免日志再出现
                    # 「候选网关: https://api.antisafety.net, https://api.reghelp.net」。
                    contaminated = raw_urls_are_contaminated(data)
                    if self._persist_if_urls_sanitized(data, self._config) or contaminated:
                        logger.warning(
                            "已自动清洗交叉污染的 Attestation 网关地址："
                            "antisafety_base_urls=%s, reghelp_base_urls=%s",
                            self._config.antisafety_base_urls,
                            self._config.reghelp_base_urls,
                        )
                    logger.info(
                        "Attestation 网关隔离就绪: AntiSafety=%s REGHelp=%s mode=%s",
                        self._config.antisafety_base_urls,
                        self._config.reghelp_base_urls,
                        self._config.attestation_provider_mode,
                    )
                    return self._config
                except Exception as e:
                    logger.warning(f"读取本地配置异常，正在回退至初始默认配置: {e}")
                    try:
                        backup_corrupted_config(CONFIG_FILE)
                    except Exception as bak_exc:
                        logger.error("备份损坏配置失败: %s", bak_exc)

            self._config = AppConfigModel()
            try:
                _atomic_write_unlocked(self._config.model_dump(), CONFIG_FILE)
                logger.info("系统配置快照已成功持久化至磁盘")
            except Exception as e:
                logger.error(f"持久化配置文件失败: {e}")
            return self._config

    @staticmethod
    def _maybe_migrate_official_api_defaults(raw: Dict[str, Any]) -> Optional[AppConfigModel]:
        """一次性迁移：旧版 custom/auto 注册策略 → 官方 api_id=4/6 默认路径。"""
        if raw.get("_official_api_defaults_v2"):
            return None
        if not raw.get("official_client_emulation"):
            raw["official_client_emulation"] = True
        if str(raw.get("api_credential_mode") or "").lower() in {"", "auto", "custom"}:
            raw["api_credential_mode"] = "official"
        if str(raw.get("code_delivery_mode") or "").lower() in {"", "balanced", "sms_first"}:
            raw["code_delivery_mode"] = "push_required"
        if raw.get("official_api_id") not in (4, 6):
            raw["official_api_id"] = 6
        raw["_official_api_defaults_v2"] = True
        return AppConfigModel(**raw)

    @staticmethod
    def _persist_if_urls_sanitized(raw: Dict[str, Any], config: AppConfigModel) -> bool:
        keys = ("antisafety_base_urls", "antisafety_reporting_base_urls", "reghelp_base_urls")
        changed = raw_urls_are_contaminated(raw)
        if not changed:
            for key in keys:
                before = [str(item).rstrip("/") for item in (raw.get(key) or []) if item]
                after = [str(item).rstrip("/") for item in (getattr(config, key, None) or [])]
                if before != after:
                    changed = True
                    break
        if changed:
            try:
                _atomic_write_unlocked(config.model_dump(), CONFIG_FILE)
            except Exception as exc:
                logger.warning("回写清洗后的网关地址失败: %s", exc)
                return False
        return changed

    def save_config(self, new_config: AppConfigModel) -> AppConfigModel:
        self._config = new_config
        try:
            atomic_write_config(self._config.model_dump(), CONFIG_FILE)
            logger.info("系统配置快照已成功持久化至磁盘")
        except Exception as e:
            logger.error(f"持久化配置文件失败: {e}")
        return self._config

    @property
    def config(self) -> AppConfigModel:
        if self._config is None:
            return self.load_config()
        return self._config


def _as_url_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


def raw_urls_are_contaminated(raw: Dict[str, Any]) -> bool:
    """磁盘上的旧配置是否把对方网关混进了本提供源候选列表。"""
    if any(is_reghelp_url(url) for url in _as_url_list(raw.get("antisafety_base_urls"))):
        return True
    if any(is_reghelp_url(url) for url in _as_url_list(raw.get("antisafety_reporting_base_urls"))):
        return True
    if any(is_antisafety_url(url) for url in _as_url_list(raw.get("reghelp_base_urls"))):
        return True
    return False
