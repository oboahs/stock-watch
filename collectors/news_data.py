from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests

from utils import LOGGER, now_text


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 stock-watch-assistant/0.1",
}

LOW_VALUE_SOURCE_PATTERNS = [
    "百度百科",
    "百科",
    "baike.baidu.com",
    "wikipedia",
    "维基百科",
    "知乎",
    "知道",
    "问答",
    "股吧",
    "论坛",
    "招聘",
    "图片",
    "视频",
    "投票",
    "互动",
]

USEFUL_STOCK_EVENT_WORDS = [
    "公告",
    "财报",
    "一季报",
    "半年报",
    "年报",
    "业绩",
    "净利润",
    "营收",
    "涨停",
    "跌停",
    "主力资金",
    "融资",
    "股东",
    "减持",
    "增持",
    "回购",
    "订单",
    "中标",
    "投资",
    "合作",
    "龙虎榜",
    "评级",
    "研报",
    "目标价",
    "分红",
    "募资",
    "重组",
    "earnings",
    "revenue",
    "guidance",
    "forecast",
    "profit",
    "buyback",
    "dividend",
    "rating",
    "upgrade",
    "downgrade",
    "target price",
    "deal",
    "acquisition",
    "lawsuit",
    "sec filing",
]

USEFUL_SECTOR_EVENT_WORDS = [
    "板块",
    "行业",
    "产业",
    "指数",
    "ETF",
    "资金",
    "走强",
    "领涨",
    "回调",
    "景气",
    "需求",
    "价格",
    "政策",
    "订单",
    "产能",
    "sector",
    "industry",
    "index",
    "outperform",
    "underperform",
    "demand",
    "supply",
    "tariff",
    "rate cut",
]


def fetch_news_for_stock(stock: dict[str, Any], settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings or {}
    max_items = int(settings.get("max_items_per_stock", 8))
    min_items_before_search = int(settings.get("min_items_before_search", 2))
    max_sector_items = int(settings.get("max_sector_items_per_stock", 4))
    feeds = settings.get("rss_feeds") or []
    items: list[dict[str, Any]] = []
    us_stock = is_us_stock(stock)

    if settings.get("use_akshare_stock_news", True) and not us_stock:
        try:
            items.extend(_fetch_akshare_stock_news(stock, max_items=max_items))
        except Exception as exc:
            LOGGER.warning("akshare stock news failed for %s: %s", stock["code"], exc)
    if us_stock:
        try:
            items.extend(_fetch_nasdaq_stock_news(stock, max_items=max_items))
        except Exception as exc:
            LOGGER.warning("nasdaq stock news failed for %s: %s", stock["code"], exc)

    for feed in feeds:
        try:
            items.extend(_fetch_feed(feed, stock, max_items=max_items))
        except Exception as exc:
            LOGGER.warning("news feed failed for %s from %s: %s", stock["code"], feed.get("name"), exc)

    items = dedupe_news(items)
    if settings.get("enable_search_fallback", True) and len(items) < min_items_before_search:
        try:
            items.extend(_fetch_search_fallback_news(stock, settings, max_items=max_items))
        except Exception as exc:
            LOGGER.warning("search fallback news failed for %s: %s", stock["code"], exc)

    sector_items: list[dict[str, Any]] = []
    if settings.get("enable_sector_news", True) and max_sector_items > 0:
        try:
            sector_items = fetch_sector_news_for_stock(stock, settings, max_items=max_sector_items)
        except Exception as exc:
            LOGGER.warning("sector news failed for %s: %s", stock["code"], exc)

    items = dedupe_news(items)
    company_items = [item for item in items if item.get("scope") != "sector"][:max_items]
    sector_items = dedupe_news(sector_items)[:max_sector_items]
    return dedupe_news(company_items + sector_items)


def _fetch_akshare_stock_news(stock: dict[str, Any], max_items: int = 8) -> list[dict[str, Any]]:
    import akshare as ak

    frame = ak.stock_news_em(symbol=str(stock["code"]).zfill(6))
    if frame is None or frame.empty:
        return []
    results = []
    keywords = build_keywords(stock)
    for _, row in frame.head(max_items * 2).iterrows():
        title = str(row.get("新闻标题", "")).strip()
        if not title:
            continue
        summary = str(row.get("新闻内容", "")).strip()
        url = str(row.get("新闻链接", "")).strip()
        published = str(row.get("发布时间", "")).strip() or now_text()
        results.append(
            {
                "title": title,
                "source": f"AKShare东方财富个股新闻/{row.get('文章来源', '')}".rstrip("/"),
                "published_at": published,
                "url": url,
                "summary": summary[:500],
                "keywords": keywords,
                "scope": "stock",
                "dedup_key": _dedup_key(title, url),
            }
        )
        if len(results) >= max_items:
            break
    return results


def _fetch_nasdaq_stock_news(stock: dict[str, Any], max_items: int = 8) -> list[dict[str, Any]]:
    code = str(stock.get("code", "")).strip().upper().replace("-", ".")
    response = requests.get(
        "https://api.nasdaq.com/api/news/topic/articlebysymbol",
        params={"q": f"{code}|stocks", "offset": 0, "limit": max_items, "fallback": "false"},
        timeout=12,
        headers={
            **DEFAULT_HEADERS,
            "Accept": "application/json,text/plain,*/*",
            "Origin": "https://www.nasdaq.com",
            "Referer": f"https://www.nasdaq.com/market-activity/stocks/{code.lower()}",
        },
    )
    response.raise_for_status()
    rows = ((response.json().get("data") or {}).get("rows") or [])
    keywords = build_keywords(stock)
    results = []
    for row in rows[: max_items * 2]:
        title = clean_html(str(row.get("title", "")).strip())
        if not title:
            continue
        publisher = str(row.get("publisher", "")).strip()
        url = urljoin("https://www.nasdaq.com", str(row.get("url", "")).strip())
        summary = clean_html(str(row.get("synopsis", "") or row.get("teaser", "") or title))
        results.append(
            {
                "title": title,
                "source": f"Nasdaq新闻/{publisher}".rstrip("/"),
                "published_at": str(row.get("created", "") or row.get("ago", "") or now_text()),
                "url": url,
                "summary": summary[:500],
                "keywords": keywords,
                "scope": "stock",
                "dedup_key": _dedup_key(title, url),
            }
        )
        if len(results) >= max_items:
            break
    return results


def _fetch_feed(feed: dict[str, Any], stock: dict[str, Any], max_items: int = 8) -> list[dict[str, Any]]:
    try:
        import feedparser

        parsed = feedparser.parse(feed.get("url", ""))
        entries = parsed.entries or []
    except Exception:
        response = requests.get(feed.get("url", ""), headers=DEFAULT_HEADERS, timeout=10)
        response.raise_for_status()
        entries = []

    keywords = build_keywords(stock)
    results = []
    for entry in entries:
        title = str(getattr(entry, "title", "")).strip()
        summary = str(getattr(entry, "summary", "")).strip()
        text = f"{title} {summary}".lower()
        if not title:
            continue
        if keywords and not any(keyword.lower() in text for keyword in keywords):
            continue
        published = str(getattr(entry, "published", "") or getattr(entry, "updated", "") or now_text())
        url = str(getattr(entry, "link", "")).strip()
        results.append(
            {
                "title": title,
                "source": feed.get("name", "RSS"),
                "published_at": published,
                "url": url,
                "summary": summary[:300],
                "keywords": keywords,
                "scope": "stock",
                "dedup_key": _dedup_key(title, url),
            }
        )
        if len(results) >= max_items:
            break
    return results


def fetch_sector_news_for_stock(stock: dict[str, Any], settings: dict[str, Any] | None = None, max_items: int = 4) -> list[dict[str, Any]]:
    settings = settings or {}
    themes = [str(item).strip() for item in stock.get("themes") or [] if str(item).strip()]
    if not themes:
        return []
    providers = parse_search_providers(settings.get("search_provider", "bing,google"))
    results: list[dict[str, Any]] = []
    us_stock = is_us_stock(stock)
    for theme in themes[:4]:
        query = f'"{theme}" sector stocks news' if us_stock else f'"{theme}" A股 板块 新闻'
        keywords = [theme]
        for provider in providers:
            try:
                results.extend(_fetch_search_rss(query, provider, keywords=keywords, scope="sector", max_items=max(2, max_items)))
            except Exception as exc:
                LOGGER.warning("sector search failed for %s via %s: %s", theme, provider, exc)
            if len(dedupe_news(results)) >= max_items:
                return dedupe_news(results)[:max_items]
    return dedupe_news(results)[:max_items]


def _fetch_search_fallback_news(stock: dict[str, Any], settings: dict[str, Any], max_items: int = 8) -> list[dict[str, Any]]:
    providers = parse_search_providers(settings.get("search_provider", "bing,google"))
    code = str(stock.get("code", "")).strip()
    name = str(stock.get("name", "")).strip()
    market_terms = "stock news earnings guidance" if is_us_stock(stock) else "股票 新闻"
    query_parts = [part for part in [name, code, market_terms] if part]
    query = " ".join(query_parts)
    keywords = build_keywords(stock)
    results: list[dict[str, Any]] = []
    for provider in providers:
        try:
            results.extend(_fetch_search_rss(query, provider, keywords=keywords, scope="stock", max_items=max_items))
        except Exception as exc:
            LOGGER.warning("search fallback failed for %s via %s: %s", code, provider, exc)
        if dedupe_news(results):
            break
    return dedupe_news(results)[:max_items]


def _fetch_search_rss(
    query: str,
    provider: str,
    keywords: list[str],
    scope: str,
    max_items: int = 8,
) -> list[dict[str, Any]]:
    url = build_search_rss_url(query, provider)
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
    response.raise_for_status()
    entries = parse_rss_entries(response.content)
    results = []
    for entry in entries:
        title = clean_html(str(entry.get("title", "")).strip())
        summary = clean_html(str(entry.get("summary", "") or entry.get("description", "")).strip())
        if not title:
            continue
        text = f"{title} {summary}".lower()
        if scope == "stock" and keywords and not any(keyword.lower() in text for keyword in keywords):
            continue
        if scope == "sector" and keywords and not any(keyword.lower() in text for keyword in keywords):
            continue
        published = str(entry.get("published", "") or entry.get("updated", "") or now_text())
        link = str(entry.get("link", "")).strip()
        if not is_useful_search_result(title, summary, link, keywords, scope):
            continue
        results.append(
            {
                "title": title,
                "source": f"搜索引擎/{provider}",
                "published_at": published,
                "url": link,
                "summary": summary[:400],
                "keywords": keywords,
                "scope": scope,
                "dedup_key": _dedup_key(title, link),
            }
        )
        if len(results) >= max_items:
            break
    return results


def parse_rss_entries(content: bytes) -> list[dict[str, str]]:
    try:
        import feedparser

        parsed = feedparser.parse(content)
        return [
            {
                "title": str(getattr(entry, "title", "")),
                "summary": str(getattr(entry, "summary", "") or getattr(entry, "description", "")),
                "published": str(getattr(entry, "published", "") or getattr(entry, "updated", "")),
                "link": str(getattr(entry, "link", "")),
            }
            for entry in parsed.entries or []
        ]
    except Exception:
        root = ET.fromstring(content)
        entries: list[dict[str, str]] = []
        for item in root.findall(".//item"):
            entries.append(
                {
                    "title": xml_text(item, "title"),
                    "summary": xml_text(item, "description"),
                    "published": xml_text(item, "pubDate"),
                    "link": xml_text(item, "link"),
                }
            )
        return entries


def xml_text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    return "".join(child.itertext()).strip() if child is not None else ""


def build_search_rss_url(query: str, provider: str) -> str:
    encoded = quote_plus(query)
    if provider == "google":
        return f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    return f"https://www.bing.com/search?q={encoded}&format=rss"


def parse_search_providers(value: Any) -> list[str]:
    if isinstance(value, str):
        providers = [part.strip().lower() for part in value.split(",") if part.strip()]
    else:
        providers = [str(part).strip().lower() for part in value or [] if str(part).strip()]
    allowed = [provider for provider in providers if provider in {"bing", "google"}]
    return allowed or ["bing", "google"]


def clean_html(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_useful_search_result(title: str, summary: str, link: str, keywords: list[str], scope: str) -> bool:
    text = f"{title} {summary} {link}"
    lower_text = text.lower()
    if any(pattern.lower() in lower_text for pattern in LOW_VALUE_SOURCE_PATTERNS):
        return False

    keyword_hits = [keyword for keyword in keywords if keyword and keyword.lower() in lower_text]
    if not keyword_hits:
        return False

    useful_words = USEFUL_SECTOR_EVENT_WORDS if scope == "sector" else USEFUL_STOCK_EVENT_WORDS
    if any(word.lower() in lower_text for word in useful_words):
        return True

    # Company-level search results that directly contain both code and name are
    # often useful even when the RSS snippet omits event vocabulary.
    if scope == "stock" and len(keyword_hits) >= 2:
        return True
    return False


def build_keywords(stock: dict[str, Any]) -> list[str]:
    keywords = [stock.get("code", ""), stock.get("name", "")]
    keywords.extend(stock.get("themes") or [])
    return [str(item).strip() for item in keywords if str(item).strip()]


def is_us_stock(stock: dict[str, Any]) -> bool:
    market = str(stock.get("market", "")).strip().upper()
    return market in {"美股", "US", "USA", "US_STOCK", "NASDAQ", "NYSE", "AMEX"}


def dedupe_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = item.get("dedup_key") or _dedup_key(item.get("title", ""), item.get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        item["dedup_key"] = key
        result.append(item)
    return result


def _dedup_key(title: str, url: str) -> str:
    raw = (url or title).strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
