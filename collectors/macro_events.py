from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from utils import LOGGER


def fetch_macro_events(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings or {}
    endpoints = settings.get("endpoints") or []
    events: list[dict[str, Any]] = []
    for endpoint in endpoints:
        try:
            events.extend(_fetch_json_endpoint(endpoint))
        except Exception as exc:
            LOGGER.warning("macro endpoint failed %s: %s", endpoint, exc)
    return events


def _fetch_json_endpoint(endpoint: str) -> list[dict[str, Any]]:
    response = httpx.get(endpoint, timeout=10)
    response.raise_for_status()
    data = response.json()
    raw_events = data.get("events", data if isinstance(data, list) else [])
    results = []
    for item in raw_events[:20]:
        results.append(
            {
                "event_date": str(item.get("date") or item.get("event_date") or datetime.now().date()),
                "title": str(item.get("title") or item.get("name") or "宏观事件"),
                "source": str(item.get("source") or endpoint),
                "importance": str(item.get("importance") or "medium"),
                "summary": str(item.get("summary") or item.get("description") or ""),
            }
        )
    return results


