from __future__ import annotations

import json
import os
from typing import Any

import requests

from utils import LOGGER, fmt_num, fmt_pct


DEFAULT_LLM_PROMPT = """你是一个谨慎的股票事件分析助手。请根据以下数据分析该股票今日情况。

要求：
1. 不要给出绝对买入或卖出指令。
2. 必须区分事实、推断和不确定性。
3. 不要因为单条新闻就直接判断趋势。
4. 必须结合新闻、公告、板块、成交量、技术位置。
5. 给出短线、中线、长期三个维度的影响。
6. 给出需要观察的关键价位或信号。
7. 给出判断失效条件。
8. 如果信息不足，必须明确说明。

股票信息：
{stock_profile}

持仓信息：
{holding_info}

今日行情：
{market_data}

相关新闻：
{news_list}

公司公告：
{announcements}

板块表现：
{sector_data}

宏观事件：
{macro_events}

请输出：
1. 今日核心结论
2. 重要事实
3. 可能影响路径
4. 利好/利空/中性判断
5. 短线走势情景
6. 中线逻辑是否变化
7. 风险点
8. 明日重点观察
9. 对持仓者的操作框架"""


STRUCTURED_COMPACT_OUTPUT_INSTRUCTION = """

请将上面的分析再压缩为适合可视化页面展示的 JSON。只输出 JSON，不要输出 Markdown、解释或代码块。
要求：
- 所有句子尽量短，保留关键事实、关键推断、关键不确定性。
- 不要给绝对买入或卖出指令。
- 方向只能使用 bullish、bearish、neutral、uncertain。
- confidence 为 0-100 的整数；信息不足时不要高于 55。
- 每个数组最多 4 条，每条不超过 38 个中文字符。
- 核心结论不超过 70 个中文字符。
- 触发条件、失效条件、观察信号都要具体。

JSON 结构必须如下：
{
  "core_conclusion": "今日核心结论",
  "direction": "bullish|bearish|neutral|uncertain",
  "confidence": 0,
  "important_facts": ["事实1", "事实2"],
  "impact_path": ["影响路径1", "影响路径2"],
  "short_term": {"view": "短线情景", "trigger": "触发条件", "invalid": "失效条件"},
  "mid_term": {"view": "中线逻辑", "trigger": "触发条件", "invalid": "失效条件"},
  "long_term": {"view": "长期影响", "trigger": "触发条件", "invalid": "失效条件"},
  "risk_points": ["风险1", "风险2"],
  "watch_tomorrow": ["观察1", "观察2"],
  "key_levels": [{"label": "支撑/压力/风控", "value": "价位或信号", "meaning": "含义"}],
  "holding_framework": ["框架1", "框架2"],
  "uncertainties": ["不确定性1", "不确定性2"]
}
"""


def run_llm_analysis(
    stock_results: list[dict[str, Any]],
    macro_events: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
) -> tuple[dict[str, str], list[str]]:
    settings = settings or {}
    if not settings.get("enabled", False):
        return {}, []

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return {}, ["大模型分析已启用，但 .env 未配置 LLM_API_KEY，因此本次未调用大模型。"]

    base_url = (settings.get("base_url") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = settings.get("model") or os.getenv("LLM_MODEL") or "gpt-4o-mini"
    prompt_template = settings.get("prompt_template") or DEFAULT_LLM_PROMPT
    timeout = int(settings.get("timeout_seconds") or 60)

    analyses: dict[str, str] = {}
    warnings: list[str] = []
    for item in stock_results:
        stock = item.get("stock", {})
        code = str(stock.get("code", "")).strip()
        if not code:
            continue
        try:
            prompt = build_prompt(prompt_template, item, macro_events)
            analyses[code] = call_chat_completion(base_url, api_key, model, prompt, timeout=timeout)
        except Exception as exc:
            LOGGER.warning("llm analysis failed for %s: %s", code, exc)
            warnings.append(f"{stock.get('name', code)} 大模型分析失败：{exc}")
    return analyses, warnings


def call_chat_completion(base_url: str, api_key: str, model: str, prompt: str, timeout: int = 60) -> str:
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "你是谨慎的股票事件分析助手，只基于输入数据输出条件化分析。优先输出紧凑 JSON，方便可视化页面解析。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("大模型接口未返回 choices。")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise ValueError("大模型接口返回内容为空。")
    return str(content).strip()


def build_prompt(template: str, item: dict[str, Any], macro_events: list[dict[str, Any]]) -> str:
    stock = item.get("stock", {})
    technical = item.get("technical", {})
    latest = technical.get("latest") or {}
    news_items = item.get("news_analyses") or []
    announcements = [news for news in news_items if "公告" in str(news.get("source", ""))]
    sector_news = [news for news in news_items if news.get("scope") == "sector"]
    non_announcement_news = [news for news in news_items if news not in announcements and news.get("scope") != "sector"]

    values = {
        "stock_profile": json.dumps(
            {
                "code": stock.get("code"),
                "name": stock.get("name"),
                "market": stock.get("market"),
                "themes": stock.get("themes"),
                "notes": stock.get("notes"),
                "key_levels": stock.get("key_levels"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        "holding_info": json.dumps(
            {
                "holding": stock.get("holding"),
                "cost": stock.get("cost"),
                "shares": stock.get("shares"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        "market_data": json.dumps(
            {
                "date": latest.get("date"),
                "close": fmt_num(latest.get("close")),
                "change_pct": fmt_pct(latest.get("change_pct")),
                "volume_signal": latest.get("volume_signal"),
                "price_signal": latest.get("price_signal"),
                "technical_summary": technical.get("summary"),
                "technical_signals": technical.get("signals"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        "news_list": format_news(non_announcement_news),
        "announcements": format_news(announcements),
        "sector_data": json.dumps(
            {
                "themes": stock.get("themes"),
                "sector_news": compact_news(sector_news),
                "note": "板块动态只作为综合参考，不能直接等同于公司事实或个股趋势。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        "macro_events": json.dumps(macro_events, ensure_ascii=False, indent=2) if macro_events else "本次未取得宏观事件数据。",
    }
    prompt = template.format(**values)
    previous_context = format_previous_context(item)
    if previous_context:
        prompt = prompt.rstrip() + "\n\n昨日观察复盘与本日新增变化：\n" + previous_context
    if "core_conclusion" not in prompt or "important_facts" not in prompt:
        prompt = prompt.rstrip() + STRUCTURED_COMPACT_OUTPUT_INSTRUCTION
    return prompt


def format_news(news_items: list[dict[str, Any]]) -> str:
    if not news_items:
        return "本次未取得。"
    return json.dumps(compact_news(news_items), ensure_ascii=False, indent=2)


def compact_news(news_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for news in news_items[:10]:
        rows.append(
            {
                "title": news.get("title"),
                "source": news.get("source"),
                "published_at": news.get("published_at"),
                "summary": news.get("summary"),
                "url": news.get("url"),
                "relevance_score": news.get("relevance_score"),
                "sentiment": news.get("sentiment"),
                "sentiment_reason": news.get("sentiment_reason"),
                "scope": news.get("scope", "stock"),
            }
        )
    return rows


def format_previous_context(item: dict[str, Any]) -> str:
    watch_review = item.get("watch_review") or []
    intraday_change = item.get("intraday_change") or {}
    if not watch_review and not intraday_change.get("has_previous"):
        return ""
    context = {
        "yesterday_watch_review": [
            {
                "point": row.get("point"),
                "status": row.get("status"),
                "evidence": row.get("evidence"),
            }
            for row in watch_review[:8]
        ],
        "same_day_change": {
            "has_previous": intraday_change.get("has_previous", False),
            "previous_run": intraday_change.get("previous_run", ""),
            "items": intraday_change.get("items", []),
            "new_news": [
                {
                    "title": news.get("title"),
                    "source": news.get("source"),
                    "relevance_score": news.get("relevance_score"),
                }
                for news in (intraday_change.get("new_news", []) or [])[:5]
            ],
        },
        "instruction": "请在结论、短线情景、明日重点观察中显式考虑这些复盘信息；未触发或信息不足时必须说明。",
    }
    return json.dumps(context, ensure_ascii=False, indent=2)
