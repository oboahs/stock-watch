from __future__ import annotations

import argparse
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
    save_analysis_snapshot,
    save_macro_events,
    save_market_data,
    save_news,
    save_report,
    save_stock_news,
    upsert_stocks,
)
from reports.alert_report import send_email_report
from reports.daily_report import build_daily_report, build_html_report, save_html_report, save_markdown_report
from reports.llm_analysis import run_llm_analysis
from utils import LOGGER, load_environment, today_compact


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
        }
        stock_results.append(result)
        save_analysis_snapshot(
            stock["code"],
            today_compact(),
            {
                "relevance_avg": relevance_avg,
                "sentiment_summary": sentiment_summary,
                "risk_level": risk["level"],
                "risk_score": risk["score"],
                "technical_summary": technical.get("summary"),
                "scenarios": scenarios,
                "uncertainties": uncertainties,
            },
        )

    llm_analyses, llm_warnings = run_llm_analysis(stock_results, macro_events, config.get("llm", {}))
    warnings.extend(llm_warnings)
    for item in stock_results:
        code = item.get("stock", {}).get("code")
        if code in llm_analyses:
            item["llm_analysis"] = llm_analyses[code]

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
