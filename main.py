from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from typing import Any

from analyzers.relevance import score_relevance
from analyzers.risk_score import compute_risk_score
from analyzers.scenario import build_scenarios
from analyzers.sentiment import classify_sentiment
from analyzers.technical import analyze_technical
from collectors.announcements import fetch_announcements
from collectors.macro_events import fetch_macro_events
from collectors.market_data import fetch_market_data
from collectors.news_data import fetch_news_for_stock
from config_loader import load_watchlist
from database import (
    init_db,
    load_latest_snapshot_before,
    load_latest_snapshot_for_date,
    prune_reports,
    save_analysis_snapshot,
    save_macro_events,
    save_market_data,
    save_news,
    save_report,
    save_stock_news,
    upsert_stocks,
)
from reports.alert_report import send_email_report
from reports.daily_report import build_daily_report, build_html_report, parse_llm_payload, save_html_report, save_markdown_report
from reports.llm_analysis import run_llm_analysis
from utils import LOGGER, load_environment, safe_float, today_compact


def run_daily_pipeline(send_email: bool = False) -> dict[str, Any]:
    load_environment()
    init_db()
    config = load_watchlist()
    stocks = config.get("stocks", [])
    upsert_stocks(stocks)

    stock_results = []
    warnings: list[str] = []
    macro_events = fetch_macro_events(config.get("macro", {}))
    save_macro_events(macro_events)

    for stock in stocks:
        if not stock.get("code") or not stock.get("name"):
            continue
        LOGGER.info("processing %s %s", stock["code"], stock["name"])
        market = fetch_market_data(stock)
        if market.get("warning"):
            warnings.append(market["warning"])
        save_market_data(stock["code"], market.get("data"), market.get("source", ""))

        news_items = fetch_news_for_stock(stock, config.get("news", {}))
        news_items.extend(fetch_announcements(stock))
        news_ids = save_news(news_items)
        technical = analyze_technical(market.get("data"), stock.get("key_levels", {}))

        news_analyses = []
        for item in news_items:
            relevance = score_relevance(stock, item)
            sentiment = classify_sentiment(item)
            analysis = {
                **item,
                "stock_code": stock["code"],
                "stock_name": stock["name"],
                "relevance_score": relevance["score"],
                "relevance_reasons": relevance["reasons"],
                "sentiment": sentiment["sentiment"],
                "sentiment_reason": sentiment["reason"],
            }
            news_analyses.append(analysis)

        risk = compute_risk_score(stock, news_analyses, technical)
        for analysis in news_analyses:
            analysis["risk_level"] = risk["level"]
            analysis["risk_score"] = risk["score"]
            news_id = news_ids.get(analysis.get("dedup_key") or analysis.get("url") or analysis.get("title"))
            if news_id:
                save_stock_news(stock["code"], news_id, analysis)

        scenarios = build_scenarios(stock, technical, risk)
        observation_signals = build_observation_signals(stock, technical, risk, news_analyses)
        uncertainties = build_uncertainties(market, news_analyses)
        sentiment_summary = summarize_sentiment(news_analyses)
        relevance_avg = round(sum(item["relevance_score"] for item in news_analyses) / len(news_analyses), 2) if news_analyses else 0

        result = {
            "stock": stock,
            "market": market,
            "technical": technical,
            "news_analyses": news_analyses,
            "risk": risk,
            "scenarios": scenarios,
            "observation_signals": observation_signals,
            "uncertainties": uncertainties,
            "sentiment_summary": sentiment_summary,
            "relevance_avg": relevance_avg,
        }
        stock_results.append(result)

    run_date = today_compact()
    for item in stock_results:
        stock = item.get("stock", {})
        code = stock.get("code")
        previous_day = load_latest_snapshot_before(str(code), run_date) if code else None
        same_day_previous = load_latest_snapshot_for_date(str(code), run_date) if code else None
        item["_previous_snapshot"] = previous_day
        item["_same_day_previous"] = same_day_previous
        item["watch_review"] = build_watch_review(previous_day, item)
        item["intraday_change"] = build_intraday_change(same_day_previous, item)

    llm_analyses, llm_warnings = run_llm_analysis(stock_results, macro_events, config.get("llm", {}))
    warnings.extend(llm_warnings)
    for item in stock_results:
        stock = item.get("stock", {})
        code = stock.get("code")
        if code in llm_analyses:
            item["llm_analysis"] = llm_analyses[code]
        llm_payload = parse_llm_payload(str(item.get("llm_analysis", ""))) if item.get("llm_analysis") else {}
        item["llm_payload"] = llm_payload
        previous_day = item.pop("_previous_snapshot", None)
        same_day_previous = item.pop("_same_day_previous", None)
        item["watch_review"] = build_watch_review(previous_day, item)
        item["intraday_change"] = build_intraday_change(same_day_previous, item)
        latest = item.get("technical", {}).get("latest") or {}
        save_analysis_snapshot(
            str(code),
            run_date,
            {
                "relevance_avg": item.get("relevance_avg", 0),
                "sentiment_summary": item.get("sentiment_summary", ""),
                "risk_level": item.get("risk", {}).get("level", ""),
                "risk_score": item.get("risk", {}).get("score"),
                "technical_summary": item.get("technical", {}).get("summary"),
                "scenarios": item.get("scenarios", {}),
                "uncertainties": item.get("uncertainties", []),
                "latest_close": latest.get("close"),
                "change_pct": latest.get("change_pct"),
                "volume_signal": latest.get("volume_signal", ""),
                "price_signal": latest.get("price_signal", ""),
                "news_count": len(item.get("news_analyses", [])),
                "news_digest": build_news_digest(item.get("news_analyses", [])),
                "llm_direction": llm_payload.get("direction", ""),
                "llm_confidence": llm_payload.get("confidence"),
                "llm_payload": llm_payload,
                "watch_tomorrow": llm_payload.get("watch_tomorrow", []),
                "key_levels": llm_payload.get("key_levels", []),
                "watch_review": item.get("watch_review", []),
                "intraday_change": item.get("intraday_change", {}),
            },
        )

    pruned_reports = prune_reports(parse_retention_days((config.get("reports") or {}).get("retention_days")))
    if pruned_reports:
        warnings.append(f"已按保留天数自动清理 {pruned_reports} 份旧日报。")

    output_dir = (config.get("reports") or {}).get("output_dir") or None
    report = build_daily_report(stock_results, macro_events, warnings)
    report_path = save_markdown_report(report, output_dir=output_dir)
    html_report = build_html_report(stock_results, macro_events, warnings)
    html_report_path = save_html_report(html_report, markdown_path=report_path, output_dir=output_dir)
    emailed = False
    if send_email:
        emailed = send_email_report(f"每日投资观察报告 {datetime.now():%Y-%m-%d}", report, report_path)
    save_report(today_compact(), "每日投资观察报告", report_path, report, sent_email=emailed)
    LOGGER.info("daily pipeline finished: %s", report_path)
    return {
        "report_path": report_path,
        "html_report_path": html_report_path,
        "report": report,
        "html_report": html_report,
        "stock_results": stock_results,
        "warnings": warnings,
        "emailed": emailed,
    }


def build_watch_review(previous_snapshot: dict[str, Any] | None, item: dict[str, Any]) -> list[dict[str, Any]]:
    if not previous_snapshot:
        return []
    points = load_json_list(previous_snapshot.get("watch_tomorrow_json"))
    key_levels = load_json_list(previous_snapshot.get("key_levels_json"))
    for level in key_levels:
        if isinstance(level, dict):
            label = level.get("label") or "价位"
            value = level.get("value") or ""
            meaning = level.get("meaning") or ""
            if value:
                points.append(f"{label} {value} {meaning}".strip())
    latest = item.get("technical", {}).get("latest") or {}
    close = safe_float(latest.get("close"))
    volume_signal = str(latest.get("volume_signal") or "")
    price_signal = str(latest.get("price_signal") or "")
    news_count = len(item.get("news_analyses", []))
    results = []
    seen = set()
    for raw_point in points[:8]:
        point = str(raw_point).strip()
        if not point or point in seen:
            continue
        seen.add(point)
        status = "待确认"
        evidence = "需要继续观察"
        numbers = [safe_float(value) for value in re.findall(r"\d+(?:\.\d+)?", point)]
        numbers = [value for value in numbers if value is not None]
        if close is not None and numbers:
            nearest = min(numbers, key=lambda value: abs(close - value))
            distance_pct = abs(close - nearest) / nearest * 100 if nearest else None
            if distance_pct is not None and distance_pct <= 1.5:
                status = "接近"
                evidence = f"收盘 {close:.2f}，距 {nearest:g} 约 {distance_pct:.1f}%"
            elif ("突破" in point or "站上" in point or "压力" in point) and close >= nearest:
                status = "触发"
                evidence = f"收盘 {close:.2f} 已高于 {nearest:g}"
            elif ("跌破" in point or "支撑" in point or "风控" in point) and close <= nearest:
                status = "触发"
                evidence = f"收盘 {close:.2f} 已低于或触及 {nearest:g}"
            else:
                status = "未触发"
                evidence = f"收盘 {close:.2f}，观察价位 {nearest:g}"
        elif "放量" in point and volume_signal == "放量":
            status = "触发"
            evidence = "成交量信号为放量"
        elif "缩量" in point and volume_signal == "缩量":
            status = "触发"
            evidence = "成交量信号为缩量"
        elif any(word in point for word in ["新闻", "公告", "事件"]) and news_count:
            status = "有更新"
            evidence = f"本次取得 {news_count} 条新闻/公告样本"
        elif price_signal and price_signal != "区间震荡":
            evidence = price_signal
        results.append({"point": point, "status": status, "evidence": evidence, "previous_run": previous_snapshot.get("created_at", "")})
    return results


def build_intraday_change(previous_snapshot: dict[str, Any] | None, item: dict[str, Any]) -> dict[str, Any]:
    if not previous_snapshot:
        return {"has_previous": False, "items": [], "new_news": []}
    latest = item.get("technical", {}).get("latest") or {}
    items = []
    current_close = safe_float(latest.get("close"))
    previous_close = safe_float(previous_snapshot.get("latest_close"))
    if current_close is not None and previous_close is not None:
        delta = current_close - previous_close
        delta_pct = delta / previous_close * 100 if previous_close else 0
        if abs(delta_pct) >= 0.2:
            items.append({"label": "价格变化", "value": f"{delta:+.2f} / {delta_pct:+.2f}%", "level": "up" if delta > 0 else "down"})
    current_risk = safe_float(item.get("risk", {}).get("score"))
    previous_risk = safe_float(previous_snapshot.get("risk_score"))
    if current_risk is not None and previous_risk is not None:
        delta = current_risk - previous_risk
        if abs(delta) >= 3:
            items.append({"label": "风险分变化", "value": f"{delta:+.0f}", "level": "down" if delta > 0 else "up"})
    payload = item.get("llm_payload") or {}
    previous_direction = str(previous_snapshot.get("llm_direction") or "")
    current_direction = str(payload.get("direction") or "")
    if previous_direction and current_direction and previous_direction != current_direction:
        items.append({"label": "大模型方向变化", "value": f"{previous_direction} -> {current_direction}", "level": "neutral"})
    previous_conf = safe_float(previous_snapshot.get("llm_confidence"))
    current_conf = safe_float(payload.get("confidence"))
    if previous_conf is not None and current_conf is not None:
        delta = current_conf - previous_conf
        if abs(delta) >= 8:
            items.append({"label": "置信度变化", "value": f"{delta:+.0f}", "level": "neutral"})
    previous_news = load_json_list(previous_snapshot.get("news_digest_json"))
    previous_titles = {str(news.get("title", "")) for news in previous_news if isinstance(news, dict)}
    current_news = build_news_digest(item.get("news_analyses", []))
    new_news = [news for news in current_news if news.get("title") not in previous_titles][:5]
    if new_news:
        items.append({"label": "新增新闻", "value": f"{len(new_news)} 条", "level": "neutral"})
    return {"has_previous": True, "previous_run": previous_snapshot.get("created_at", ""), "items": items, "new_news": new_news}


def build_news_digest(news_analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for news in sorted(news_analyses, key=lambda item: item.get("relevance_score", 0), reverse=True)[:12]:
        rows.append(
            {
                "title": news.get("title", ""),
                "source": news.get("source", ""),
                "url": news.get("url", ""),
                "sentiment": news.get("sentiment", ""),
                "relevance_score": news.get("relevance_score", 0),
            }
        )
    return rows


def load_json_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def parse_retention_days(value: Any) -> int | None:
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def build_observation_signals(stock: dict[str, Any], technical: dict[str, Any], risk: dict[str, Any], news_analyses: list[dict[str, Any]]) -> list[str]:
    signals = []
    signals.extend(technical.get("signals") or [])
    if risk.get("level") in {"medium", "high"}:
        signals.append(f"风险分 {risk.get('score')}，需跟踪风险理由：{'；'.join(risk.get('reasons', [])[:3])}")
    high_relevance = [item for item in news_analyses if item.get("relevance_score", 0) >= 70]
    if high_relevance:
        signals.append(f"出现 {len(high_relevance)} 条高相关新闻，需核对原文")
    key_levels = stock.get("key_levels") or {}
    support = key_levels.get("support") or []
    resistance = key_levels.get("resistance") or []
    if support or resistance:
        signals.append(f"关键价位：支撑 {support or '未设置'}，压力 {resistance or '未设置'}")
    return [signal for signal in signals if signal]


def build_uncertainties(market: dict[str, Any], news_analyses: list[dict[str, Any]]) -> list[str]:
    uncertainties = []
    if not market.get("ok"):
        uncertainties.append("本次行情未成功取得，技术判断可靠性下降。")
    company_news = [item for item in news_analyses if item.get("scope") != "sector"]
    sector_news = [item for item in news_analyses if item.get("scope") == "sector"]
    if not company_news:
        uncertainties.append("未抓到相关新闻，不能推断为没有事件；需改用公告、行情异动、板块表现和交易所披露做替代核验。")
    if sector_news and not company_news:
        uncertainties.append("本次只有板块/行业动态，没有公司直接新闻；板块信息只能作为综合参考。")
    return uncertainties or ["公开数据源可能延迟、失败或重复，重要结论需人工复核。"]


def summarize_sentiment(news_analyses: list[dict[str, Any]]) -> str:
    if not news_analyses:
        return "暂无新闻样本，无法判断事件方向。"
    counts = {}
    for item in news_analyses:
        counts[item["sentiment"]] = counts.get(item["sentiment"], 0) + 1
    dominant = max(counts, key=counts.get)
    reasons = [item["sentiment_reason"] for item in news_analyses[:3]]
    return f"新闻方向以 {dominant} 为主；理由样本：{'；'.join(reasons)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stock watch assistant daily pipeline")
    parser.add_argument("--send-email", action="store_true", help="send markdown report by SMTP after generation")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = run_daily_pipeline(send_email=args.send_email)
    print(result["report_path"])
