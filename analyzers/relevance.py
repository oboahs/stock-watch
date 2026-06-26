from __future__ import annotations

from typing import Any


def score_relevance(stock: dict[str, Any], news_item: dict[str, Any]) -> dict[str, Any]:
    title = str(news_item.get("title", ""))
    summary = str(news_item.get("summary", ""))
    text = f"{title} {summary}".lower()
    score = 0
    reasons: list[str] = []

    code = str(stock.get("code", "")).lower()
    name = str(stock.get("name", "")).lower()
    if code and code in text:
        score += 45
        reasons.append("标题或摘要包含股票代码")
    if name and name in text:
        score += 45
        reasons.append("标题或摘要包含股票名称")

    theme_hits = []
    for theme in stock.get("themes") or []:
        theme_text = str(theme).lower()
        if theme_text and theme_text in text:
            score += 12
            theme_hits.append(str(theme))
    if theme_hits:
        reasons.append(f"命中主题：{'、'.join(theme_hits[:4])}")

    if news_item.get("source", "").startswith("MVP模拟"):
        score = max(score, 55)
        reasons.append("MVP模拟新闻按自选股生成，默认中等相关")

    score = min(score, 100)
    if score == 0:
        reasons.append("未命中代码、名称或主题关键词")
    return {"score": score, "reasons": reasons}

