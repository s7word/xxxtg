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
                    return self._config
            except Exception as e:
                logger.warning(f"读取本地配置异常，正在回退至初始默认配置: {e}")
        
        self._config = AppConfigModel()
        self.save_config(self._config)
        return self._config

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
