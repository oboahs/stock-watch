from __future__ import annotations

from typing import Any

from utils import safe_float


def compute_risk_score(
    stock: dict[str, Any],
    news_analyses: list[dict[str, Any]],
    technical: dict[str, Any],
) -> dict[str, Any]:
    score = 20
    reasons: list[str] = []

    for item in news_analyses:
        relevance = int(item.get("relevance_score") or 0)
        sentiment = item.get("sentiment")
        if relevance >= 70:
            score += 8
            reasons.append("存在高相关新闻")
        elif relevance >= 40:
            score += 4
        if sentiment == "bearish":
            score += 12
            reasons.append("新闻偏利空")
        elif sentiment == "uncertain":
            score += 6
            reasons.append("新闻不确定性较高")
        elif sentiment == "bullish":
            score -= 3

    latest = technical.get("latest") or {}
    change_pct = safe_float(latest.get("change_pct"))
    if change_pct is not None and abs(change_pct) >= 4:
        score += 10
        reasons.append("日内波动较大")
    if "放量" in "；".join(technical.get("signals") or []):
        score += 6
        reasons.append("成交量放大")
    if any("跌破" in signal or "风控" in signal for signal in technical.get("signals") or []):
        score += 14
        reasons.append("触发跌破或风控观察信号")

    if stock.get("holding"):
        score += 8
        reasons.append("当前为持仓标的")
        cost = safe_float(stock.get("cost"))
        close = safe_float(latest.get("close"))
        if cost and close:
            pnl_pct = (close / cost - 1) * 100
            if pnl_pct <= -8:
                score += 10
                reasons.append("持仓浮亏超过8%")
            elif pnl_pct >= 20:
                score += 5
                reasons.append("持仓浮盈较大，需防回撤")

    score = max(0, min(100, int(score)))
    if score >= 70:
        level = "high"
    elif score >= 40:
        level = "medium"
    else:
        level = "low"
    if not reasons:
        reasons.append("暂无重大负面事件或异常盘面信号")
    return {"score": score, "level": level, "reasons": sorted(set(reasons))}

