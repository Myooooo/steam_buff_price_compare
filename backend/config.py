"""应用配置：dataclass + JSON 持久化。

配置存放于 ``data/config.json``（仓库根目录下 data/，已 gitignore）。
首次启动自动生成默认配置；保存时保留用户填写的字段。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("config")

# 仓库根目录（backend/config.py 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 运行时数据目录
DATA_DIR = PROJECT_ROOT / "data"
# 前端静态目录
FRONTEND_DIR = PROJECT_ROOT / "frontend"

CONFIG_PATH = DATA_DIR / "config.json"
BUFF_SESSION_PATH = DATA_DIR / "buff_session.json"
DB_PATH = DATA_DIR / "prices.db"

# 默认的 Buff 浏览器标识，可在配置文件中覆盖。
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class DeepScanConfig:
    enabled: bool = False
    # 全市场深度扫描的额外间隔（分钟）；未完成时触发会从 SQLite 检查点续跑
    interval_minutes: int = 240


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8080

    # Buff / Steam 目标游戏
    game: str = "csgo"
    steam_appid: int = 730
    currency: int = 23  # Steam currency=23 即 CNY

    # 候选池：关键词完整分页（主）+ 全市场持久化深度扫描（可选）
    keywords: list[str] = field(default_factory=lambda: ["ak-47", "usp", "蝴蝶刀", "胶囊"])
    page_size: int = 80  # BUFF 每页请求量；扫描会继续请求后续所有分页

    deep_scan: DeepScanConfig = field(default_factory=DeepScanConfig)

    # 扫描周期
    scan_interval_minutes: int = 15
    auto_scan: bool = True

    # Steam 手续费模型（CS2 现为 5% + 10% = 15%，规则若变只需改这两个数字）
    steam_fee_steam_pct: float = 5.0
    steam_fee_game_pct: float = 10.0
    fee_min: float = 0.01
    fee_round: str = "cent"  # "cent"（到分，官方口径） | "yuan"（到元）

    # 请求节流，防止被 Buff/Steam 风控
    request_delay_sec: float = 1.0  # Buff 每个搜索请求之间
    steam_delay_sec: float = 0.5  # Steam 每个价格请求之间
    steam_buff_fallback: bool = True  # Steam 直查失败时使用 BUFF 同步的 Steam CNY 参考价
    # wait_retry: 429 后暂停至少 60 秒并重试当前商品；buff_fallback: 冷却期直接使用参考价
    steam_rate_limit_mode: str = "wait_retry"

    # 数据保留
    history_keep_days: int = 30

    # Buff 访问标识
    user_agent: str = DEFAULT_USER_AGENT

    # 日志
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if isinstance(self.deep_scan, dict):
            known = DeepScanConfig.__dataclass_fields__
            self.deep_scan = DeepScanConfig(**{k: v for k, v in self.deep_scan.items() if k in known})
        if self.steam_rate_limit_mode not in {"wait_retry", "buff_fallback"}:
            self.steam_rate_limit_mode = "wait_retry"

    @property
    def session_path(self) -> Path:
        return BUFF_SESSION_PATH

    @property
    def db_path(self) -> Path:
        return DB_PATH


def _config_from_dict(raw: dict) -> Config:
    """读取已知字段；旧版本或手工添加的未知字段会被忽略。"""
    known = Config.__dataclass_fields__
    values = {key: value for key, value in raw.items() if key in known}
    deep_scan = values.get("deep_scan")
    if not isinstance(deep_scan, (dict, DeepScanConfig)):
        values["deep_scan"] = DeepScanConfig()
    return Config(**values)


def load_config(path: Optional[Path] = None) -> Config:
    """加载配置；文件不存在或损坏时回退默认并尝试重建。"""
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        cfg = Config()
        save_config(cfg, path)
        logger.info("已生成默认配置: %s", path)
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("配置文件读取失败(%s)，使用默认配置", e)
        return Config()
    if not isinstance(raw, dict):
        return Config()
    return _config_from_dict(raw)


def save_config(cfg: Config, path: Optional[Path] = None) -> None:
    """保存配置（原子写）。"""
    path = Path(path) if path else CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
