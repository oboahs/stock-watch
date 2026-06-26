from __future__ import annotations

import re
import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from utils import REPORT_DIR, fmt_num, fmt_pct, today_compact


def build_daily_report(
    stock_results: list[dict[str, Any]],
    macro_events: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    high_risk = [item for item in stock_results if item.get("risk", {}).get("level") == "high"]
    medium_risk = [item for item in stock_results if item.get("risk", {}).get("level") == "medium"]
    all_news = [news for item in stock_results for news in item.get("news_analyses", [])]
    company_news = [news for news in all_news if news.get("scope") != "sector"]
    sector_news = [news for news in all_news if news.get("scope") == "sector"]
    top_news = sorted(all_news, key=lambda x: (x.get("relevance_score", 0), x.get("risk_score", 0)), reverse=True)[:10]
    top_sector_news = sorted(sector_news, key=lambda x: x.get("relevance_score", 0), reverse=True)[:10]
    llm_items = [item for item in stock_results if item.get("llm_analysis")]
    llm_warnings = [warning for warning in warnings if "大模型" in warning or "LLM" in warning]

    lines = [
        "# 每日投资观察报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 覆盖标的：{len(stock_results)} 只",
        f"- 高风险标的：{len(high_risk)} 只",
        f"- 中风险标的：{len(medium_risk)} 只",
        "- 使用边界：本报告只输出事件影响、风险等级、走势情景和观察价位，不构成绝对买卖指令。",
        "",
        "## 今日总览",
    ]
    if not stock_results:
        lines.append("- 自选股列表为空。")
    else:
        for item in stock_results:
            stock = item["stock"]
            risk = item["risk"]
            latest = item.get("technical", {}).get("latest") or {}
            lines.append(
                f"- {stock['name']}（{stock['code']}）：风险 {risk['level']} / {risk['score']}，"
                f"收盘 {fmt_num(latest.get('close'))}，涨跌幅 {fmt_pct(latest.get('change_pct'))}，"
                f"观察：{'; '.join(item.get('observation_signals', [])[:3]) or '暂无'}"
            )

    if llm_items or llm_warnings:
        lines.extend(["", "## AI 大模型分析结论"])
        if llm_items:
            lines.append("- 说明：以下内容来自已配置的大模型接口；系统规则分析、新闻过滤和技术信号作为输入依据和辅助校验。")
            for item in llm_items:
                stock = item["stock"]
                lines.extend(["", f"### {stock['name']}（{stock['code']}）"])
                lines.extend(render_llm_markdown_summary(str(item["llm_analysis"])))
        else:
            lines.append("- 已勾选大模型分析，但本次没有取得大模型输出。报告仍使用本地规则引擎生成。")
        for warning in llm_warnings:
            lines.append(f"- 调用状态：{warning}")

    if not llm_items:
        lines.extend(["", "## 重大新闻"])
        if company_news:
            for news in sorted(company_news, key=lambda x: (x.get("relevance_score", 0), x.get("risk_score", 0)), reverse=True)[:10]:
                lines.append(
                    f"- [{news.get('sentiment', 'neutral')}] {news.get('stock_name', '')}：{news.get('title', '')} "
                    f"（相关性 {news.get('relevance_score', 0)}，风险 {news.get('risk_level', 'N/A')}）"
                )
                lines.append(f"  - 事实：{news.get('summary') or news.get('title', '')}")
                lines.append(f"  - 推断：{news.get('sentiment_reason', '未给出。')}")
                lines.append("  - 不确定性：公开新闻可能存在延迟、转载重复或标题党，需要核对原文。")
        else:
            lines.append("- 暂无重大新闻。本次不会使用模拟新闻替代；请结合公告、行情异动、板块表现和交易所披露做替代核验。")

        lines.extend(["", "## 相关板块动态"])
        if top_sector_news:
            for news in top_sector_news:
                lines.append(
                    f"- {news.get('stock_name', '')} 相关板块：{news.get('title', '')}（来源：{news.get('source', 'N/A')}，相关性 {news.get('relevance_score', 0)}）"
                )
                lines.append(f"  - 事实：{news.get('summary') or news.get('title', '')}")
                lines.append("  - 推断：板块动态只能作为综合参考，不能直接等同于个股趋势。")
                lines.append("  - 不确定性：搜索结果可能包含转载、旧闻或泛行业内容，需要核对发布时间和原文。")
        else:
            lines.append("- 暂无可用板块动态。")

    lines.extend(["", "## 每只股票影响分析"])
    for item in stock_results:
        stock = item["stock"]
        risk = item["risk"]
        technical = item["technical"]
        direct_news = [news for news in item.get("news_analyses", []) if news.get("scope") != "sector"]
        related_sector_news = [news for news in item.get("news_analyses", []) if news.get("scope") == "sector"]
        lines.extend(
            [
                "",
                f"### {stock['name']}（{stock['code']}）",
                f"- 风险等级：{risk['level']}（{risk['score']}）",
                f"- 风险理由：{'；'.join(risk.get('reasons', []))}",
            ]
        )
        if item.get("llm_analysis"):
            lines.append("- 大模型分析后的主结论：")
            lines.extend(indent_block(line, "  ") for line in render_llm_markdown_summary(str(item["llm_analysis"])))
            lines.append("- 系统规则辅助信息：")
        lines.append("- 事实：")
        latest = technical.get("latest") or {}
        if latest:
            lines.append(
                f"  - 最新交易日 {latest.get('date')}，收盘 {fmt_num(latest.get('close'))}，"
                f"涨跌幅 {fmt_pct(latest.get('change_pct'))}，成交量信号 {latest.get('volume_signal', 'N/A')}。"
            )
        else:
            lines.append("  - 行情数据本次未成功取得。")
        for news in direct_news[:5]:
            lines.append(f"  - 新闻：{news['title']}（来源：{news.get('source', 'N/A')}，相关性 {news['relevance_score']}）")
        if related_sector_news:
            for news in related_sector_news[:3]:
                lines.append(f"  - 板块动态：{news['title']}（来源：{news.get('source', 'N/A')}，相关性 {news['relevance_score']}）")
        if not direct_news:
            lines.append("  - 新闻：本次未取得相关新闻；这只代表抓取源缺失，不代表公司没有事件。")
            lines.extend(news_fallback_markdown(stock))

        lines.append("- 推断：")
        sentiment_summary = item.get("sentiment_summary") or "新闻方向暂不明确。"
        lines.append(f"  - {sentiment_summary}")
        lines.append(f"  - 技术面：{technical.get('summary')}")
        lines.append("- 不确定性：")
        for uncertainty in item.get("uncertainties", ["公开接口数据可能延迟或失败。"]):
            lines.append(f"  - {uncertainty}")
        lines.append("- 观察信号：")
        for signal in item.get("observation_signals", ["暂无"]):
            lines.append(f"  - {signal}")

        lines.append("- 走势情景：")
        for scenario in item.get("scenarios", {}).values():
            lines.append(f"  - {scenario['title']}：触发条件：{scenario['trigger']} 失效条件：{scenario['invalid']}")

    lines.extend(["", "## 技术面观察"])
    for item in stock_results:
        stock = item["stock"]
        lines.append(f"- {stock['name']}：{item.get('technical', {}).get('summary', '数据不足')}")

    lines.extend(["", "## 风险提醒"])
    if high_risk or medium_risk:
        for item in high_risk + medium_risk:
            stock = item["stock"]
            risk = item["risk"]
            lines.append(f"- {stock['name']}（{stock['code']}）：{risk['level']}，{'；'.join(risk.get('reasons', []))}")
    else:
        lines.append("- 暂无高/中风险标的，但仍需关注行情接口延迟和公告突发。")

    lines.extend(["", "## 明日重点观察价位"])
    for item in stock_results:
        stock = item["stock"]
        key_levels = stock.get("key_levels") or {}
        lines.append(
            f"- {stock['name']}：支撑 {key_levels.get('support', []) or '未设置'}；"
            f"压力 {key_levels.get('resistance', []) or '未设置'}；"
            f"风控观察 {key_levels.get('stop_watch') if key_levels.get('stop_watch') is not None else '未设置'}"
        )

    lines.extend(["", "## 宏观事件"])
    for event in macro_events:
        lines.append(f"- [{event.get('importance', 'medium')}] {event.get('event_date')} {event.get('title')}：{event.get('summary')}")

    lines.extend(["", "## 不确定性说明"])
    base_uncertainties = [
        "AKShare、RSS、网页公开数据源可能延迟、失败或字段变化。",
        "新闻情绪为关键词规则，不等同于人工研判或大模型深度阅读。",
        "新闻抓取失败时不再使用缓存新闻或模拟新闻；缺口会作为不确定性呈现。",
    ]
    for item in base_uncertainties + sorted(set(warnings)):
        lines.append(f"- {item}")

    if llm_items:
        lines.extend(["", "## 相关新闻与原文链接"])
        evidence_news = sorted(all_news, key=lambda x: (x.get("scope") == "sector", -float(x.get("relevance_score", 0) or 0)))
        if evidence_news:
            seen_news = set()
            for news in evidence_news[:80]:
                key = news.get("url") or f"{news.get('title')}|{news.get('source')}"
                if key in seen_news:
                    continue
                seen_news.add(key)
                scope = "板块动态" if news.get("scope") == "sector" else "公司新闻"
                lines.append(
                    f"- [{scope}] {news.get('stock_name', '')}：{markdown_news_title(news)}"
                    f"（来源：{news.get('source', 'N/A')}，相关性 {news.get('relevance_score', 0)}）"
                )
                lines.append(f"  - 摘要：{news.get('summary') or news.get('title', '')}")
        else:
            lines.append("- 暂无相关新闻或板块动态。")
    return "\n".join(lines) + "\n"


def save_markdown_report(content: str, report_date: str | None = None, output_dir: str | Path | None = None) -> Path:
    target_dir = resolve_report_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    name = f"daily_report_{report_date or today_compact()}_{stamp}.md"
    path = target_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def build_html_report(
    stock_results: list[dict[str, Any]],
    macro_events: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    if any(item.get("llm_analysis") for item in stock_results):
        return build_llm_first_html_report(stock_results, macro_events, warnings)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    high_risk = [item for item in stock_results if item.get("risk", {}).get("level") == "high"]
    medium_risk = [item for item in stock_results if item.get("risk", {}).get("level") == "medium"]
    all_news = [news for item in stock_results for news in item.get("news_analyses", [])]
    company_news = [news for news in all_news if news.get("scope") != "sector"]
    sector_news = [news for news in all_news if news.get("scope") == "sector"]
    top_news = sorted(company_news, key=lambda x: (x.get("relevance_score", 0), x.get("risk_score", 0)), reverse=True)[:12]
    top_sector_news = sorted(sector_news, key=lambda x: x.get("relevance_score", 0), reverse=True)[:12]
    llm_warnings = [warning for warning in warnings if "大模型" in warning or "LLM" in warning]

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日投资观察报告</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #182033;
      --muted: #667085;
      --line: #e5e7eb;
      --green: #168a4a;
      --amber: #c77700;
      --red: #d92d20;
      --blue: #2563eb;
      --shadow: 0 12px 34px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; }}
    header {{ padding: 30px 32px 18px; background: var(--panel); border-bottom: 1px solid var(--line); }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 24px 24px 56px; }}
    h1 {{ margin: 0; font-size: 34px; letter-spacing: 0; }}
    h2 {{ margin: 34px 0 14px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    .subtitle {{ color: var(--muted); margin-top: 8px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 20px; }}
    .kpi, .card, .section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }}
    .kpi {{ padding: 16px; }}
    .kpi span {{ display: block; color: var(--muted); font-size: 13px; }}
    .kpi strong {{ display: block; margin-top: 8px; font-size: 30px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .card {{ padding: 18px; }}
    .risk-pill {{ display: inline-flex; align-items: center; height: 28px; padding: 0 10px; border-radius: 999px; color: #fff; font-weight: 700; font-size: 12px; }}
    .risk-high {{ background: var(--red); }}
    .risk-medium {{ background: var(--amber); }}
    .risk-low {{ background: var(--green); }}
    .risk-unknown {{ background: var(--blue); }}
    .bar {{ height: 12px; background: #edf1f7; border-radius: 999px; overflow: hidden; margin: 10px 0 12px; }}
    .bar > i {{ display: block; height: 100%; border-radius: 999px; }}
    .meta {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }}
    .meta div {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 6px; padding: 10px; }}
    .meta span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .list {{ margin: 8px 0 0; padding-left: 18px; }}
    .news {{ display: grid; gap: 10px; }}
    .news-item {{ padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .news-title {{ font-weight: 700; }}
    .news-meta {{ color: var(--muted); font-size: 13px; margin: 4px 0 8px; }}
    .scenario-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }}
    .scenario {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .llm {{ white-space: pre-wrap; background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; line-height: 1.7; }}
    .section {{ padding: 18px; }}
    .muted {{ color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 11px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ background: #f8fafc; font-size: 13px; color: var(--muted); }}
    @media (max-width: 900px) {{ .kpis, .grid, .meta, .scenario-grid {{ grid-template-columns: 1fr; }} header {{ padding: 22px 18px; }} main {{ padding: 16px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>每日投资观察报告</h1>
    <div class="subtitle">生成时间：{escape(generated_at)}。本报告只输出事件影响、风险等级、走势情景和观察价位，不构成绝对买卖指令。</div>
    <div class="kpis">
      {kpi("覆盖标的", str(len(stock_results)))}
      {kpi("高风险", str(len(high_risk)), "var(--red)")}
      {kpi("中风险", str(len(medium_risk)), "var(--amber)")}
      {kpi("公司新闻", str(len(company_news)), "var(--blue)")}
    </div>
  </header>
  <main>
    <h2>今日总览</h2>
    {render_overview_table(stock_results)}
    {render_llm_summary(stock_results, llm_warnings)}
    <h2>风险分布</h2>
    <div class="grid">{''.join(render_risk_card(item) for item in stock_results)}</div>
    <h2>重大新闻</h2>
    <div class="news">{render_news_items(top_news)}</div>
    <h2>相关板块动态</h2>
    <div class="news">{render_news_items(top_sector_news, sector=True)}</div>
    <h2>每只股票影响分析</h2>
    {''.join(render_stock_detail(item) for item in stock_results)}
    <h2>宏观事件</h2>
    <div class="section">{render_macro_events(macro_events)}</div>
    <h2>不确定性说明</h2>
    <div class="section"><ul class="list">{render_uncertainties(warnings)}</ul></div>
  </main>
</body>
</html>
"""


LLM_SECTION_TITLES = [
    "今日核心结论",
    "重要事实",
    "可能影响路径",
    "利好/利空/中性判断",
    "短线走势情景",
    "中线逻辑是否变化",
    "风险点",
    "明日重点观察",
    "对持仓者的操作框架",
]


def build_llm_first_html_report(
    stock_results: list[dict[str, Any]],
    macro_events: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    llm_items = [item for item in stock_results if item.get("llm_analysis")]
    llm_warnings = [warning for warning in warnings if "大模型" in warning or "LLM" in warning]
    all_news = [news for item in stock_results for news in item.get("news_analyses", [])]
    company_news = [news for news in all_news if news.get("scope") != "sector"]
    sector_news = [news for news in all_news if news.get("scope") == "sector"]
    macro_summary = render_macro_events(macro_events)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日投资观察报告 - 大模型视图</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #182033;
      --muted: #667085;
      --line: #e5e7eb;
      --green: #168a4a;
      --amber: #c77700;
      --red: #d92d20;
      --blue: #2563eb;
      --purple: #6d28d9;
      --shadow: 0 12px 34px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.65; }}
    header {{ padding: 30px 32px 18px; background: var(--panel); border-bottom: 1px solid var(--line); }}
    main {{ max-width: 1320px; margin: 0 auto; padding: 24px 24px 56px; }}
    h1 {{ margin: 0; font-size: 34px; letter-spacing: 0; }}
    h2 {{ margin: 34px 0 14px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    .subtitle {{ color: var(--muted); margin-top: 8px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-top: 20px; }}
    .kpi, .card, .section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }}
    .kpi {{ padding: 16px; }}
    .kpi span {{ display: block; color: var(--muted); font-size: 13px; }}
    .kpi strong {{ display: block; margin-top: 8px; font-size: 30px; }}
    .section {{ padding: 18px; }}
    .card {{ padding: 18px; margin-bottom: 16px; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .llm-card-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }}
    .llm-card {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; min-height: 132px; }}
    .llm-card strong {{ display: block; margin-bottom: 6px; }}
    .visual-grid {{ display: grid; grid-template-columns: 1.15fr .85fr; gap: 12px; margin-top: 12px; }}
    .signal-panel {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .signal-panel ul {{ margin: 8px 0 0; padding-left: 18px; }}
    .gauge {{ height: 12px; background: #e8edf5; border-radius: 999px; overflow: hidden; margin: 8px 0 4px; }}
    .gauge > i {{ display: block; height: 100%; border-radius: 999px; }}
    .meter-label {{ display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; }}
    .level-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .level-tag {{ border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 6px 10px; font-size: 13px; }}
    .scenario-strip {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }}
    .scenario-box {{ border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 12px; }}
    .scenario-box p {{ margin: 6px 0 0; }}
    details {{ margin-top: 12px; }}
    summary {{ cursor: pointer; color: var(--blue); font-weight: 700; }}
    .chip {{ display: inline-flex; align-items: center; min-height: 26px; padding: 0 9px; border-radius: 999px; color: #fff; font-size: 12px; font-weight: 700; }}
    .chip-bullish {{ background: var(--green); }}
    .chip-bearish {{ background: var(--red); }}
    .chip-neutral {{ background: var(--blue); }}
    .chip-uncertain {{ background: var(--amber); }}
    .chip-llm {{ background: var(--purple); }}
    .compare-table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; box-shadow: var(--shadow); }}
    .compare-table th, .compare-table td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    .compare-table th {{ background: #f8fafc; font-size: 13px; color: var(--muted); }}
    .compare-table td {{ font-size: 14px; }}
    .llm-text {{ white-space: pre-wrap; background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 14px; line-height: 1.75; }}
    .source-grid {{ display: grid; gap: 10px; }}
    .source-item {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .source-title {{ font-weight: 700; }}
    .source-title a {{ color: var(--blue); text-decoration: none; }}
    .source-title a:hover {{ text-decoration: underline; }}
    .source-meta {{ color: var(--muted); font-size: 13px; margin: 4px 0 8px; }}
    .list {{ margin: 8px 0 0; padding-left: 18px; }}
    @media (max-width: 980px) {{ .kpis, .grid, .llm-card-grid, .visual-grid, .scenario-strip {{ grid-template-columns: 1fr; }} header {{ padding: 22px 18px; }} main {{ padding: 16px; }} .compare-table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <header>
    <h1>每日投资观察报告</h1>
    <div class="subtitle">生成时间：{escape(generated_at)}。当前可视化视图以大模型返回的日报分析为主，原始新闻只作为末尾证据链接，不构成绝对买卖指令。</div>
    <div class="kpis">
      {kpi("覆盖标的", str(len(stock_results)))}
      {kpi("大模型覆盖", str(len(llm_items)), "var(--purple)")}
      {kpi("公司新闻", str(len(company_news)), "var(--blue)")}
      {kpi("板块动态", str(len(sector_news)), "var(--amber)")}
    </div>
  </header>
  <main>
    <h2>大模型横向对比</h2>
    {render_llm_comparison_table(llm_items)}
    <h2>大模型核心结论卡片</h2>
    <div class="grid">{''.join(render_llm_summary_card(item) for item in llm_items)}</div>
    <h2>逐股大模型分析</h2>
    {''.join(render_llm_stock_detail(item) for item in llm_items)}
    <h2>宏观事件</h2>
    <div class="section">{macro_summary}</div>
    <h2>调用状态与不确定性</h2>
    <div class="section"><ul class="list">{render_uncertainties(llm_warnings or warnings)}</ul></div>
    <h2>相关新闻与原文链接</h2>
    <div class="section muted">以下新闻和板块动态是大模型分析的输入材料之一，只放在最后作为核验入口；请优先阅读上方大模型结论，再核对原文。</div>
    {render_news_evidence(stock_results)}
  </main>
</body>
</html>
"""


def render_llm_markdown_summary(text: str) -> list[str]:
    payload = parse_llm_payload(text)
    lines = [
        f"- 核心结论：{payload['core_conclusion']}",
        f"- 方向：{payload['direction']}；置信度：{payload['confidence']}",
    ]
    for label, key in [
        ("重要事实", "important_facts"),
        ("影响路径", "impact_path"),
        ("风险点", "risk_points"),
        ("明日观察", "watch_tomorrow"),
        ("持仓框架", "holding_framework"),
        ("不确定性", "uncertainties"),
    ]:
        values = payload.get(key) or []
        lines.append(f"- {label}：{'；'.join(values) if values else '未明确'}")
    for label, key in [("短线", "short_term"), ("中线", "mid_term"), ("长期", "long_term")]:
        period = payload.get(key) or {}
        lines.append(f"- {label}：{period.get('view', '未明确')}；触发：{period.get('trigger', '未明确')}；失效：{period.get('invalid', '未明确')}")
    return lines


def parse_llm_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "完整分析"
    heading_pattern = re.compile(r"^(?:#{1,6}\s*)?(?:\d+[.、)]\s*)?(.+?)(?:[:：]\s*(.*))?$")
    for raw_line in str(text).splitlines():
        stripped = raw_line.strip()
        normalized = stripped.strip("* ")
        matched_title = ""
        trailing = ""
        match = heading_pattern.match(normalized)
        if match:
            candidate = match.group(1).strip()
            for title in LLM_SECTION_TITLES:
                if candidate == title or candidate.startswith(title):
                    matched_title = title
                    trailing = (match.group(2) or "").strip()
                    break
        if matched_title:
            current = matched_title
            sections.setdefault(current, [])
            if trailing:
                sections[current].append(trailing)
            continue
        sections.setdefault(current, []).append(raw_line)
    return {key: "\n".join(value).strip() for key, value in sections.items() if "\n".join(value).strip()}


def parse_llm_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    data = extract_json_object(raw)
    if isinstance(data, dict):
        return normalize_llm_payload(data, raw)
    sections = parse_llm_sections(raw)
    direction_text = sections.get("利好/利空/中性判断", "")
    return normalize_llm_payload(
        {
            "core_conclusion": sections.get("今日核心结论") or sections.get("完整分析") or short_text(raw, 120),
            "direction": infer_direction(direction_text),
            "confidence": 55 if direction_text else 45,
            "important_facts": split_compact_lines(sections.get("重要事实", "")),
            "impact_path": split_compact_lines(sections.get("可能影响路径", "")),
            "short_term": {"view": sections.get("短线走势情景", ""), "trigger": "见短线情景", "invalid": sections.get("风险点", "")},
            "mid_term": {"view": sections.get("中线逻辑是否变化", ""), "trigger": "中线逻辑验证", "invalid": sections.get("风险点", "")},
            "long_term": {"view": sections.get("长期影响", "未明确"), "trigger": "长期逻辑验证", "invalid": sections.get("风险点", "")},
            "risk_points": split_compact_lines(sections.get("风险点", "")),
            "watch_tomorrow": split_compact_lines(sections.get("明日重点观察", "")),
            "key_levels": [],
            "holding_framework": split_compact_lines(sections.get("对持仓者的操作框架", "")),
            "uncertainties": split_compact_lines(sections.get("不确定性", "")),
        },
        raw,
    )


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def normalize_llm_payload(data: dict[str, Any], raw: str) -> dict[str, Any]:
    direction = infer_direction(str(data.get("direction") or data.get("方向") or data.get("利好/利空/中性判断") or ""))
    confidence = int(max(0, min(100, safe_intish(data.get("confidence") or data.get("置信度"), 50))))
    return {
        "raw": raw,
        "core_conclusion": short_text(data.get("core_conclusion") or data.get("核心结论") or data.get("今日核心结论") or "未明确", 90),
        "direction": direction,
        "confidence": confidence,
        "important_facts": ensure_list(data.get("important_facts") or data.get("重要事实")),
        "impact_path": ensure_list(data.get("impact_path") or data.get("可能影响路径")),
        "short_term": ensure_period(data.get("short_term") or data.get("短线")),
        "mid_term": ensure_period(data.get("mid_term") or data.get("中线")),
        "long_term": ensure_period(data.get("long_term") or data.get("长期")),
        "risk_points": ensure_list(data.get("risk_points") or data.get("风险点")),
        "watch_tomorrow": ensure_list(data.get("watch_tomorrow") or data.get("明日重点观察")),
        "key_levels": ensure_key_levels(data.get("key_levels") or data.get("关键价位")),
        "holding_framework": ensure_list(data.get("holding_framework") or data.get("持仓框架") or data.get("对持仓者的操作框架")),
        "uncertainties": ensure_list(data.get("uncertainties") or data.get("不确定性")),
    }


def infer_direction(text: str) -> str:
    value = str(text).lower()
    if "bearish" in value or "利空" in value or "偏空" in value:
        return "bearish"
    if "bullish" in value or "利好" in value or "偏多" in value:
        return "bullish"
    if "neutral" in value or "中性" in value:
        return "neutral"
    return "uncertain"


def ensure_list(value: Any, limit: int = 4) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = split_compact_lines(str(value))
    return [short_text(item, 46) for item in items if str(item).strip()][:limit]


def ensure_period(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "view": short_text(value.get("view") or value.get("情景") or value.get("观点") or "未明确", 70),
            "trigger": short_text(value.get("trigger") or value.get("触发条件") or "未明确", 70),
            "invalid": short_text(value.get("invalid") or value.get("失效条件") or "未明确", 70),
        }
    text = short_text(value or "未明确", 70)
    return {"view": text, "trigger": "未明确", "invalid": "未明确"}


def ensure_key_levels(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value[:6]:
        if isinstance(item, dict):
            rows.append(
                {
                    "label": short_text(item.get("label") or item.get("名称") or "价位", 12),
                    "value": short_text(item.get("value") or item.get("价格") or item.get("信号") or "", 18),
                    "meaning": short_text(item.get("meaning") or item.get("含义") or "", 36),
                }
            )
        else:
            rows.append({"label": "价位", "value": short_text(item, 18), "meaning": ""})
    return rows


def split_compact_lines(text: str) -> list[str]:
    rows = []
    for line in str(text).splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.、)])\s*", "", line).strip()
        if cleaned:
            rows.append(cleaned)
    if not rows and text:
        rows = [item.strip() for item in re.split(r"[；;。]\s*", str(text)) if item.strip()]
    return rows


def safe_intish(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace("%", "").strip()))
    except (TypeError, ValueError):
        return default


def short_text(text: str, max_len: int = 140) -> str:
    cleaned = re.sub(r"\s+", " ", str(text).replace("**", "")).strip(" -")
    if len(cleaned) <= max_len:
        return cleaned or "未明确"
    return cleaned[: max_len - 1].rstrip() + "…"


def direction_chip(text: str) -> str:
    value = infer_direction(str(text))
    if value == "bearish":
        return '<span class="chip chip-bearish">偏利空</span>'
    if value == "bullish":
        return '<span class="chip chip-bullish">偏利好</span>'
    if value == "neutral":
        return '<span class="chip chip-neutral">中性</span>'
    return '<span class="chip chip-uncertain">不确定</span>'


def render_llm_comparison_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<div class="section muted">本次没有取得大模型输出。</div>'
    rows = []
    for item in items:
        stock = item.get("stock", {})
        payload = parse_llm_payload(str(item.get("llm_analysis", "")))
        rows.append(
            "<tr>"
            f"<td><strong>{escape(stock.get('name', ''))}</strong><br><span class=\"muted\">{escape(stock.get('code', ''))}</span></td>"
            f"<td>{escape(payload['core_conclusion'])}</td>"
            f"<td>{direction_chip(payload['direction'])}<br>{render_confidence(payload['confidence'])}</td>"
            f"<td>{escape(payload['short_term']['view'])}</td>"
            f"<td>{escape(payload['mid_term']['view'])}</td>"
            f"<td>{escape('；'.join(payload['watch_tomorrow']) or '未明确')}</td>"
            f"<td>{escape('；'.join(payload['risk_points']) or '未明确')}</td>"
            "</tr>"
        )
    return (
        '<table class="compare-table"><thead><tr><th>标的</th><th>今日核心结论</th><th>判断</th>'
        "<th>短线情景</th><th>中线逻辑</th><th>明日观察</th><th>风险点</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_llm_summary_card(item: dict[str, Any]) -> str:
    stock = item.get("stock", {})
    payload = parse_llm_payload(str(item.get("llm_analysis", "")))
    levels = payload["key_levels"]
    level_html = "".join(
        f"<span class=\"level-tag\"><strong>{escape(row['label'])}</strong> {escape(row['value'])} {escape(row['meaning'])}</span>"
        for row in levels
    )
    return f"""<section class="card">
      <h3>{escape(stock.get('name', ''))} <span class="muted">({escape(stock.get('code', ''))})</span></h3>
      {direction_chip(payload['direction'])} <span class="chip chip-llm">精简LLM</span>
      {render_confidence(payload['confidence'])}
      <div class="visual-grid">
        <div class="signal-panel"><strong>核心结论</strong><p>{escape(payload['core_conclusion'])}</p></div>
        <div class="signal-panel"><strong>明日观察</strong>{render_compact_list(payload['watch_tomorrow'], '未明确')}</div>
      </div>
      <div class="llm-card-grid">
        <div class="llm-card"><strong>关键事实</strong>{render_compact_list(payload['important_facts'], '未明确')}</div>
        <div class="llm-card"><strong>影响路径</strong>{render_compact_list(payload['impact_path'], '未明确')}</div>
        <div class="llm-card"><strong>风险点</strong>{render_compact_list(payload['risk_points'], '未明确')}</div>
      </div>
      <div class="level-row">{level_html or '<span class="level-tag">暂无明确关键价位</span>'}</div>
    </section>"""


def render_llm_stock_detail(item: dict[str, Any]) -> str:
    stock = item.get("stock", {})
    payload = parse_llm_payload(str(item.get("llm_analysis", "")))
    scenario_html = "".join(
        render_period_box(label, payload[key])
        for label, key in [("短线", "short_term"), ("中线", "mid_term"), ("长期", "long_term")]
    )
    raw = payload.get("raw", "")
    details = ""
    if raw and not extract_json_object(raw):
        details = f"<details><summary>查看原始大模型文本</summary><div class=\"llm-text\">{escape(raw)}</div></details>"
    return f"""<section class="card">
      <h3>{escape(stock.get('name', ''))} <span class="muted">({escape(stock.get('code', ''))})</span></h3>
      {direction_chip(payload['direction'])} <span class="chip chip-llm">结构化摘要</span>
      {render_confidence(payload['confidence'])}
      <div class="visual-grid">
        <div class="signal-panel"><strong>重要事实</strong>{render_compact_list(payload['important_facts'], '未明确')}</div>
        <div class="signal-panel"><strong>不确定性</strong>{render_compact_list(payload['uncertainties'], '未明确')}</div>
      </div>
      <div class="scenario-strip">{scenario_html}</div>
      <div class="visual-grid">
        <div class="signal-panel"><strong>持仓框架</strong>{render_compact_list(payload['holding_framework'], '未明确')}</div>
        <div class="signal-panel"><strong>明日重点观察</strong>{render_compact_list(payload['watch_tomorrow'], '未明确')}</div>
      </div>
      {details}
    </section>"""


def render_confidence(confidence: int) -> str:
    value = max(0, min(100, int(confidence or 0)))
    color = "var(--green)" if value >= 70 else "var(--amber)" if value >= 45 else "var(--red)"
    return (
        f'<div class="meter-label"><span>置信度</span><span>{value}</span></div>'
        f'<div class="gauge"><i style="width:{value}%; background:{color}"></i></div>'
    )


def render_compact_list(items: list[str], empty: str = "暂无") -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        values = [empty]
    return "<ul>" + "".join(f"<li>{escape(short_text(item, 54))}</li>" for item in values[:4]) + "</ul>"


def render_period_box(label: str, period: dict[str, str]) -> str:
    return f"""<div class="scenario-box">
      <strong>{escape(label)}：{escape(period.get('view', '未明确'))}</strong>
      <p><span class="muted">触发：</span>{escape(period.get('trigger', '未明确'))}</p>
      <p><span class="muted">失效：</span>{escape(period.get('invalid', '未明确'))}</p>
    </div>"""


def render_news_evidence(stock_results: list[dict[str, Any]]) -> str:
    news_items = [news for item in stock_results for news in item.get("news_analyses", [])]
    if not news_items:
        return '<div class="section muted">暂无相关新闻或板块动态。</div>'
    news_items = sorted(news_items, key=lambda x: (x.get("scope") == "sector", -float(x.get("relevance_score", 0) or 0)))
    cards = []
    seen = set()
    for news in news_items[:80]:
        key = news.get("url") or f"{news.get('title')}|{news.get('source')}"
        if key in seen:
            continue
        seen.add(key)
        title = escape(news.get("title", ""))
        url = str(news.get("url") or "").strip()
        title_html = f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>' if url.startswith(("http://", "https://")) else title
        scope = "板块动态" if news.get("scope") == "sector" else "公司新闻"
        cards.append(
            f"""<div class="source-item">
              <div class="source-title">{escape(news.get('stock_name', ''))}：{title_html}</div>
              <div class="source-meta">{escape(scope)} · {escape(news.get('source', 'N/A'))} · {escape(str(news.get('published_at', '')))} · 相关性 {escape(str(news.get('relevance_score', 0)))}</div>
              <div>{escape(news.get('summary') or news.get('title', ''))}</div>
            </div>"""
        )
    content = "".join(cards) or '<div class="section muted">暂无可展示新闻。</div>'
    return f'<div class="source-grid">{content}</div>'


def save_html_report(
    content: str,
    markdown_path: Path | None = None,
    report_date: str | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    target_dir = resolve_report_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if markdown_path:
        path = markdown_path.with_suffix(".html")
    else:
        stamp = datetime.now().strftime("%H%M%S")
        path = target_dir / f"daily_report_{report_date or today_compact()}_{stamp}.html"
    path.write_text(content, encoding="utf-8")
    return path


def markdown_to_basic_html(markdown: str, title: str = "每日投资观察报告") -> str:
    body = []
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                body.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h1>{escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.startswith("- ") or line.startswith("  - "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{escape(line.lstrip('- ').strip())}</li>")
        else:
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<p>{escape(line)}</p>")
    if in_list:
        body.append("</ul>")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body {{ margin: 0; background: #f5f7fb; color: #182033; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.65; }}
main {{ max-width: 960px; margin: 0 auto; padding: 32px 24px 60px; }}
h1, h2, h3 {{ letter-spacing: 0; }}
h1 {{ font-size: 34px; }}
h2 {{ margin-top: 30px; border-top: 1px solid #e5e7eb; padding-top: 22px; }}
ul {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px 22px 16px 34px; box-shadow: 0 10px 28px rgba(15,23,42,.07); }}
li {{ margin: 6px 0; }}
p {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 14px; }}
</style></head><body><main>{''.join(body)}</main></body></html>"""


def kpi(label: str, value: str, color: str = "var(--text)") -> str:
    return f'<div class="kpi"><span>{escape(label)}</span><strong style="color:{color}">{escape(value)}</strong></div>'


def render_overview_table(stock_results: list[dict[str, Any]]) -> str:
    if not stock_results:
        return '<div class="section muted">自选股列表为空。</div>'
    rows = []
    for item in stock_results:
        stock = item["stock"]
        risk = item.get("risk", {})
        latest = item.get("technical", {}).get("latest") or {}
        rows.append(
            "<tr>"
            f"<td>{escape(stock.get('name', ''))}</td>"
            f"<td>{escape(stock.get('code', ''))}</td>"
            f"<td>{risk_pill(risk.get('level', 'unknown'))}</td>"
            f"<td>{escape(str(risk.get('score', 0)))}</td>"
            f"<td>{escape(fmt_num(latest.get('close')))}</td>"
            f"<td>{escape(fmt_pct(latest.get('change_pct')))}</td>"
            f"<td>{escape('；'.join(item.get('observation_signals', [])[:2]) or '暂无')}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>名称</th><th>代码</th><th>风险</th><th>分数</th><th>收盘</th><th>涨跌幅</th><th>观察信号</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def render_llm_summary(stock_results: list[dict[str, Any]], warnings: list[str]) -> str:
    llm_items = [item for item in stock_results if item.get("llm_analysis")]
    if not llm_items and not warnings:
        return ""
    if not llm_items:
        warning_items = "".join(f"<li>{escape(warning)}</li>" for warning in warnings)
        return f"""<h2>AI 大模型分析结论</h2>
        <div class="section"><strong>已勾选大模型分析，但本次没有取得大模型输出。</strong>
        <ul class="list">{warning_items or '<li>未返回明确失败原因。</li>'}</ul></div>"""
    cards = []
    for item in llm_items:
        stock = item.get("stock", {})
        cards.append(
            f"""<section class="card" style="margin-bottom:12px">
              <h3>{escape(stock.get('name', ''))} <span class="muted">({escape(stock.get('code', ''))})</span></h3>
              <div class="llm">{escape(str(item.get('llm_analysis', '')))}</div>
            </section>"""
        )
    warning_block = ""
    if warnings:
        warning_block = "<div class=\"section\"><strong>调用状态</strong><ul class=\"list\">" + "".join(f"<li>{escape(warning)}</li>" for warning in warnings) + "</ul></div>"
    return "<h2>AI 大模型分析结论</h2><div class=\"section muted\">以下内容来自已配置的大模型接口；系统规则分析、新闻过滤和技术信号作为输入依据和辅助校验。</div>" + "".join(cards) + warning_block


def render_risk_card(item: dict[str, Any]) -> str:
    stock = item["stock"]
    risk = item.get("risk", {})
    latest = item.get("technical", {}).get("latest") or {}
    score = int(risk.get("score", 0) or 0)
    width = max(0, min(100, score))
    color = risk_color(risk.get("level", "unknown"))
    reasons = "".join(f"<li>{escape(reason)}</li>" for reason in risk.get("reasons", [])[:4])
    return f"""<div class="card">
      <h3>{escape(stock.get('name', ''))} <span class="muted">({escape(stock.get('code', ''))})</span></h3>
      {risk_pill(risk.get('level', 'unknown'))}
      <div class="bar"><i style="width:{width}%; background:{color}"></i></div>
      <div class="meta">
        <div><span>风险分</span>{score}</div>
        <div><span>收盘</span>{escape(fmt_num(latest.get('close')))}</div>
        <div><span>涨跌幅</span>{escape(fmt_pct(latest.get('change_pct')))}</div>
        <div><span>成交量</span>{escape(str(latest.get('volume_signal', 'N/A')))}</div>
      </div>
      <ul class="list">{reasons or '<li>暂无显著风险理由</li>'}</ul>
    </div>"""


def render_news_items(news_items: list[dict[str, Any]], sector: bool = False) -> str:
    if not news_items:
        return '<div class="section muted">暂无板块动态。</div>' if sector else '<div class="section muted">暂无重大新闻。</div>'
    cards = []
    for news in news_items:
        fact_label = "板块事实" if sector or news.get("scope") == "sector" else "事实"
        inference = "板块动态只能作为综合参考，不能直接等同于个股趋势。" if sector or news.get("scope") == "sector" else news.get("sentiment_reason", "")
        cards.append(
            f"""<div class="news-item">
              <div class="news-title">{escape(news.get('stock_name', ''))}：{escape(news.get('title', ''))}</div>
              <div class="news-meta">{escape(news.get('source', 'N/A'))} · {escape(news.get('sentiment', 'neutral'))} · 相关性 {escape(str(news.get('relevance_score', 0)))} · 风险 {escape(str(news.get('risk_level', 'N/A')))}</div>
              <div><strong>{fact_label}：</strong>{escape(news.get('summary') or news.get('title', ''))}</div>
              <div><strong>推断：</strong>{escape(inference)}</div>
              <div class="muted"><strong>不确定性：</strong>公开新闻可能存在延迟、转载重复或标题党，需要核对原文。</div>
            </div>"""
        )
    return "".join(cards)


def render_stock_detail(item: dict[str, Any]) -> str:
    stock = item["stock"]
    risk = item.get("risk", {})
    technical = item.get("technical", {})
    latest = technical.get("latest") or {}
    direct_news = [news for news in item.get("news_analyses", []) if news.get("scope") != "sector"]
    sector_news = [news for news in item.get("news_analyses", []) if news.get("scope") == "sector"]
    facts = [
        f"最新交易日 {latest.get('date', 'N/A')}，收盘 {fmt_num(latest.get('close'))}，涨跌幅 {fmt_pct(latest.get('change_pct'))}，成交量信号 {latest.get('volume_signal', 'N/A')}。"
        if latest else "行情数据本次未成功取得。"
    ]
    facts.extend(f"新闻：{news.get('title', '')}（来源：{news.get('source', 'N/A')}，相关性 {news.get('relevance_score', 0)}）" for news in direct_news[:5])
    facts.extend(f"板块动态：{news.get('title', '')}（来源：{news.get('source', 'N/A')}，相关性 {news.get('relevance_score', 0)}）" for news in sector_news[:3])
    if not direct_news:
        facts.append("新闻：本次未取得相关新闻；这只代表抓取源缺失，不代表公司没有事件。")
        facts.extend(news_fallback_html(stock))
    uncertainties = item.get("uncertainties") or ["公开数据源可能延迟、失败或重复，重要结论需人工复核。"]
    signals = item.get("observation_signals") or ["暂无"]
    scenarios = item.get("scenarios", {})
    llm_block = ""
    if item.get("llm_analysis"):
        llm_block = f"<h3>大模型分析后的主结论</h3><div class=\"llm\">{escape(str(item['llm_analysis']))}</div><h3>系统规则辅助信息</h3>"
    return f"""<section class="card" style="margin-bottom:16px">
      <h3>{escape(stock.get('name', ''))} <span class="muted">({escape(stock.get('code', ''))})</span> {risk_pill(risk.get('level', 'unknown'))}</h3>
      <div class="meta">
        <div><span>风险分</span>{escape(str(risk.get('score', 0)))}</div>
        <div><span>收盘</span>{escape(fmt_num(latest.get('close')))}</div>
        <div><span>涨跌幅</span>{escape(fmt_pct(latest.get('change_pct')))}</div>
        <div><span>公司新闻</span>{len(direct_news)}</div>
      </div>
      {llm_block}
      <h3>事实</h3><ul class="list">{''.join(f'<li>{escape(fact)}</li>' for fact in facts)}</ul>
      <h3>推断</h3><ul class="list"><li>{escape(item.get('sentiment_summary', '新闻方向暂不明确。'))}</li><li>{escape(technical.get('summary', '技术面数据不足。'))}</li></ul>
      <h3>不确定性</h3><ul class="list">{''.join(f'<li>{escape(text)}</li>' for text in uncertainties)}</ul>
      <h3>观察信号</h3><ul class="list">{''.join(f'<li>{escape(text)}</li>' for text in signals)}</ul>
      <div class="scenario-grid">{''.join(render_scenario_card(scenario) for scenario in scenarios.values())}</div>
    </section>"""


def render_scenario_card(scenario: dict[str, str]) -> str:
    return f"""<div class="scenario">
      <strong>{escape(scenario.get('title', '情景'))}</strong>
      <p><strong>触发：</strong>{escape(scenario.get('trigger', ''))}</p>
      <p><strong>失效：</strong>{escape(scenario.get('invalid', ''))}</p>
      <p class="muted"><strong>观察：</strong>{escape(scenario.get('watch', ''))}</p>
    </div>"""


def render_macro_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<span class="muted">暂无宏观事件。</span>'
    return "<ul class=\"list\">" + "".join(
        f"<li>[{escape(event.get('importance', 'medium'))}] {escape(str(event.get('event_date', '')))} {escape(event.get('title', ''))}：{escape(event.get('summary', ''))}</li>"
        for event in events
    ) + "</ul>"


def render_uncertainties(warnings: list[str], has_mock_news: bool = False) -> str:
    base = [
        "AKShare、RSS、网页公开数据源可能延迟、失败或字段变化。",
        "新闻情绪为关键词规则，不等同于人工研判或大模型深度阅读。",
        "新闻抓取失败时不再使用缓存新闻或模拟新闻；缺口会作为不确定性呈现。",
    ]
    return "".join(f"<li>{escape(text)}</li>" for text in base + sorted(set(warnings)))


def risk_pill(level: str) -> str:
    cls = {"high": "risk-high", "medium": "risk-medium", "low": "risk-low"}.get(level, "risk-unknown")
    return f'<span class="risk-pill {cls}">{escape(str(level))}</span>'


def risk_color(level: str) -> str:
    return {"high": "var(--red)", "medium": "var(--amber)", "low": "var(--green)"}.get(level, "var(--blue)")


def resolve_report_dir(output_dir: str | Path | None = None) -> Path:
    if output_dir:
        path = Path(output_dir).expanduser()
        return path if path.is_absolute() else REPORT_DIR.parent / path
    return REPORT_DIR


def news_fallback_markdown(stock: dict[str, Any]) -> list[str]:
    code = stock.get("code", "")
    name = stock.get("name", code)
    return [
        f"  - 替代核验：检查交易所/巨潮/公司公告中是否有 {name}（{code}）最新披露。",
        "  - 替代核验：观察今日成交量、换手率、关键支撑/压力位是否出现异常。",
        "  - 替代核验：对照所属主题板块和同业个股表现，避免把个股异动误判为孤立事件。",
    ]


def markdown_news_title(news: dict[str, Any]) -> str:
    title = str(news.get("title") or "").strip()
    url = str(news.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        safe_title = title.replace("[", "［").replace("]", "］")
        safe_url = url.replace(")", "%29")
        return f"[{safe_title}]({safe_url})"
    return title


def news_fallback_html(stock: dict[str, Any]) -> list[str]:
    code = stock.get("code", "")
    name = stock.get("name", code)
    return [
        f"替代核验：检查交易所/巨潮/公司公告中是否有 {name}（{code}）最新披露。",
        "替代核验：观察今日成交量、换手率、关键支撑/压力位是否出现异常。",
        "替代核验：对照所属主题板块和同业个股表现，避免把个股异动误判为孤立事件。",
    ]


def indent_block(text: str, prefix: str) -> str:
    lines = text.splitlines() or [""]
    return prefix + ("\n" + prefix).join(line if line.strip() else "" for line in lines)
