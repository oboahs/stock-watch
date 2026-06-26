from __future__ import annotations

import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


APP_NAME = "stock_watch_assistant"
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


PROJECT_ROOT = user_data_root() if is_frozen() else Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
REPORT_DIR = PROJECT_ROOT / "reports" / "generated"
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "stock_assistant.db"
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


def ensure_dirs() -> None:
    for path in [CONFIG_DIR, DATA_DIR, REPORT_DIR, LOG_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    seed_user_files()


def seed_user_files() -> None:
    default_watchlist = BUNDLE_ROOT / "config" / "watchlist.yaml"
    if not default_watchlist.exists():
        default_watchlist = BUNDLE_ROOT / "packaging" / "default_config" / "watchlist.yaml"
    seeds = [
        (default_watchlist, CONFIG_DIR / "watchlist.yaml"),
        (BUNDLE_ROOT / ".env.example", ENV_EXAMPLE_PATH),
    ]
    for source, target in seeds:
        if target.exists() or not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def load_environment() -> None:
    ensure_dirs()
    load_dotenv(ENV_PATH)


def setup_logging(name: str = "stock_watch_assistant") -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    file_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


LOGGER = setup_logging()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_compact() -> str:
    return datetime.now().strftime("%Y%m%d")


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    parsed = safe_float(value)
    return int(parsed) if parsed is not None else default


def normalize_code(code: str | int) -> str:
    text = str(code).strip().upper()
    if "." in text:
        text = text.split(".")[0]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def fmt_pct(value: Any) -> str:
    number = safe_float(value)
    return "N/A" if number is None else f"{number:.2f}%"


def fmt_num(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "N/A"
    if abs(number) >= 10000:
        return f"{number:,.0f}"
    return f"{number:.2f}"
