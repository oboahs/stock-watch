from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from utils import LOGGER, normalize_code


def fetch_announcements(stock: dict[str, Any], days: int = 7) -> list[dict[str, Any]]:
    code = normalize_code(stock["code"])
    try:
        import akshare as ak

        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        frame = ak.stock_notice_report(symbol="全部", date=end)
        if frame is None or frame.empty:
            return []
        title_col = "公告标题" if "公告标题" in frame.columns else "title"
        code_col = "代码" if "代码" in frame.columns else "code"
        date_col = "公告日期" if "公告日期" in frame.columns else None
        rows = frame[frame[code_col].astype(str).str.zfill(6) == code]
        results = []
        for _, row in rows.iterrows():
            title = str(row.get(title_col, "")).strip()
            if not title:
                continue
            event_date = str(row.get(date_col, end)) if date_col else end
            if event_date.replace("-", "") < start:
                continue
            results.append(
                {
                    "title": title,
                    "source": "AKShare公告",
                    "published_at": event_date,
                    "url": str(row.get("公告链接", "")),
                    "summary": title,
                    "keywords": [code, stock.get("name", "")],
                    "dedup_key": f"announcement:{code}:{event_date}:{title}",
                }
            )
        return results
    except Exception as exc:
        LOGGER.warning("announcement fetch failed for %s: %s", code, exc)
        return []

