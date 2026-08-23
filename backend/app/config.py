import os
import json
import logging
from pathlib import Path
from typing import Dict, Any
from backend.app.models.schemas import AppConfigModel

logger = logging.getLogger("NodeSimulationConfig")

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
CONFIG_FILE = DATA_DIR / "config.json"
SESSIONS_DIR = DATA_DIR / "sessions"
SESSION_ARTIFACTS_DIR = SESSIONS_DIR  # 学术化规范别名


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
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._config = AppConfigModel(**data)
                    logger.info("已从持久化存储区载入系统配置快照")
                    # 旧配置可能把 api.reghelp.net 混进 antisafety_base_urls（或反过来），
                    # Pydantic 校验器会隔离地址；若发生清洗则回写，避免下次仍读到污染列表。
                    if self._persist_if_urls_sanitized(data, self._config):
                        logger.warning(
                            "已自动清洗交叉污染的 Attestation 网关地址："
                            "AntiSafety 仅保留 antisafety.net，REGHelp 仅保留 reghelp.net"
                        )
                    return self._config
            except Exception as e:
                logger.warning(f"读取本地配置异常，正在回退至初始默认配置: {e}")
        
        self._config = AppConfigModel()
        self.save_config(self._config)
        return self._config

    @staticmethod
    def _persist_if_urls_sanitized(raw: Dict[str, Any], config: AppConfigModel) -> bool:
        keys = ("antisafety_base_urls", "antisafety_reporting_base_urls", "reghelp_base_urls")
        changed = False
        for key in keys:
            before = [str(item).rstrip("/") for item in (raw.get(key) or []) if item]
            after = [str(item).rstrip("/") for item in (getattr(config, key, None) or [])]
            if before != after:
                changed = True
                break
        if changed:
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
            except Exception as exc:
                logger.warning("回写清洗后的网关地址失败: %s", exc)
                return False
        return changed

    def save_config(self, new_config: AppConfigModel) -> AppConfigModel:
        self._config = new_config
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config.model_dump(), f, ensure_ascii=False, indent=2)
            logger.info("系统配置快照已成功持久化至磁盘")
        except Exception as e:
            logger.error(f"持久化配置文件失败: {e}")
        return self._config

    @property
    def config(self) -> AppConfigModel:
        if self._config is None:
            return self.load_config()
        return self._config
