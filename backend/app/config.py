import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from backend.app.models.schemas import AppConfigModel
from backend.app.services.attestation_urls import is_antisafety_url, is_reghelp_url

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
        
        self._config = AppConfigModel()
        self.save_config(self._config)
        return self._config

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
