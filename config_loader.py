from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from utils import CONFIG_DIR, LOGGER, normalize_code


WATCHLIST_PATH = CONFIG_DIR / "watchlist.yaml"


DEFAULT_SCHEDULER = {
    "enabled": True,
    "timezone": "Asia/Shanghai",
    "times": ["08:30", "12:30", "16:00"],
}

DEFAULT_NEWS = {
    "use_akshare_stock_news": True,
    "max_items_per_stock": 8,
    "enable_search_fallback": True,
    "search_provider": "bing,google",
    "min_items_before_search": 2,
    "enable_sector_news": True,
    "max_sector_items_per_stock": 4,
    "rss_feeds": [],
}

DEFAULT_REPORTS = {
    "output_dir": "",
}

DEFAULT_LLM = {
    "enabled": False,
    "prompt_template": "",
}


def load_watchlist(path: Path = WATCHLIST_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"watchlist file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    data.setdefault("scheduler", DEFAULT_SCHEDULER)
    news = {**DEFAULT_NEWS, **(data.get("news") or {})}
    news.pop("use_mock_when_empty", None)
    news.pop("use_cached_when_empty", None)
    data["news"] = news
    data.setdefault("macro", {})
    data["macro"].pop("use_mock_when_empty", None)
    data["reports"] = {**DEFAULT_REPORTS, **(data.get("reports") or {})}
    data["llm"] = {**DEFAULT_LLM, **(data.get("llm") or {})}
    data.setdefault("stocks", [])
    data["stocks"] = [normalize_stock(item) for item in data["stocks"]]
    return data


def save_watchlist(data: dict[str, Any], path: Path = WATCHLIST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scheduler": data.get("scheduler", DEFAULT_SCHEDULER),
        "news": data.get("news", DEFAULT_NEWS),
        "macro": data.get("macro", {}),
        "reports": data.get("reports", DEFAULT_REPORTS),
        "llm": data.get("llm", DEFAULT_LLM),
        "stocks": [normalize_stock(item) for item in data.get("stocks", [])],
    }
    payload["news"].pop("use_mock_when_empty", None)
    payload["news"].pop("use_cached_when_empty", None)
    payload["macro"].pop("use_mock_when_empty", None)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False, default_flow_style=False)


def normalize_stock(item: dict[str, Any]) -> dict[str, Any]:
    stock = {
        "code": normalize_code(item.get("code", "")),
        "name": str(item.get("name", "")).strip(),
        "market": str(item.get("market", "A股")).strip() or "A股",
        "themes": item.get("themes") or [],
        "holding": bool(item.get("holding", False)),
        "cost": item.get("cost"),
        "shares": item.get("shares"),
        "key_levels": item.get("key_levels") or {},
        "notes": str(item.get("notes", "")).strip(),
    }
    if isinstance(stock["themes"], str):
        stock["themes"] = [part.strip() for part in stock["themes"].split(",") if part.strip()]
    if not stock["code"] or not stock["name"]:
        LOGGER.warning("watchlist entry missing code or name: %s", item)
    return stock
