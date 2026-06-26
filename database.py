from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from utils import DB_PATH, ensure_dirs, now_text


def get_conn(path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stocks (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                themes TEXT,
                holding INTEGER DEFAULT 0,
                cost REAL,
                shares REAL,
                key_levels TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                change_pct REAL,
                turnover_rate REAL,
                ma5 REAL,
                ma10 REAL,
                ma20 REAL,
                ma60 REAL,
                volume_signal TEXT,
                price_signal TEXT,
                source TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(code, trade_date)
            );

            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source TEXT,
                published_at TEXT,
                url TEXT,
                summary TEXT,
                keywords TEXT,
                fetched_at TEXT NOT NULL,
                UNIQUE(url),
                UNIQUE(title, source, published_at)
            );

            CREATE TABLE IF NOT EXISTS stock_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                news_id INTEGER NOT NULL,
                relevance_score INTEGER,
                sentiment TEXT,
                sentiment_reason TEXT,
                risk_level TEXT,
                risk_score INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(code, news_id)
            );

            CREATE TABLE IF NOT EXISTS macro_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT,
                title TEXT NOT NULL,
                source TEXT,
                importance TEXT,
                summary TEXT,
                fetched_at TEXT NOT NULL,
                UNIQUE(event_date, title, source)
            );

            CREATE TABLE IF NOT EXISTS analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                run_date TEXT NOT NULL,
                relevance_avg REAL,
                sentiment_summary TEXT,
                risk_level TEXT,
                risk_score INTEGER,
                technical_summary TEXT,
                scenario_json TEXT,
                uncertainties TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                title TEXT NOT NULL,
                path TEXT NOT NULL,
                content TEXT NOT NULL,
                sent_email INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )


def upsert_stocks(stocks: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        for stock in stocks:
            conn.execute(
                """
                INSERT INTO stocks(code, name, market, themes, holding, cost, shares, key_levels, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name,
                    market=excluded.market,
                    themes=excluded.themes,
                    holding=excluded.holding,
                    cost=excluded.cost,
                    shares=excluded.shares,
                    key_levels=excluded.key_levels,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    stock["code"],
                    stock["name"],
                    stock["market"],
                    json.dumps(stock.get("themes", []), ensure_ascii=False),
                    1 if stock.get("holding") else 0,
                    stock.get("cost"),
                    stock.get("shares"),
                    json.dumps(stock.get("key_levels", {}), ensure_ascii=False),
                    stock.get("notes", ""),
                    now_text(),
                ),
            )


def sync_stocks(stocks: list[dict[str, Any]]) -> None:
    upsert_stocks(stocks)
    codes = [stock["code"] for stock in stocks if stock.get("code")]
    with get_conn() as conn:
        if codes:
            placeholders = ",".join("?" for _ in codes)
            conn.execute(f"DELETE FROM stocks WHERE code NOT IN ({placeholders})", codes)
        else:
            conn.execute("DELETE FROM stocks")


def save_market_data(code: str, frame: pd.DataFrame, source: str) -> None:
    if frame.empty:
        return
    rows = frame.tail(180).to_dict("records")
    with get_conn() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO market_data(
                    code, trade_date, open, high, low, close, volume, amount, change_pct, turnover_rate,
                    ma5, ma10, ma20, ma60, volume_signal, price_signal, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    amount=excluded.amount,
                    change_pct=excluded.change_pct,
                    turnover_rate=excluded.turnover_rate,
                    ma5=excluded.ma5,
                    ma10=excluded.ma10,
                    ma20=excluded.ma20,
                    ma60=excluded.ma60,
                    volume_signal=excluded.volume_signal,
                    price_signal=excluded.price_signal,
                    source=excluded.source,
                    created_at=excluded.created_at
                """,
                (
                    code,
                    str(row.get("date")),
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                    row.get("amount"),
                    row.get("change_pct"),
                    row.get("turnover_rate"),
                    row.get("ma5"),
                    row.get("ma10"),
                    row.get("ma20"),
                    row.get("ma60"),
                    row.get("volume_signal"),
                    row.get("price_signal"),
                    source,
                    now_text(),
                ),
            )


def load_cached_market_data(code: str, limit: int = 180) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trade_date AS date, open, high, low, close, volume, amount, change_pct, turnover_rate
            FROM market_data
            WHERE code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([dict(row) for row in rows])
    return frame.sort_values("date").reset_index(drop=True)


def save_news(news_items: list[dict[str, Any]]) -> dict[str, int]:
    ids: dict[str, int] = {}
    if not news_items:
        return ids
    with get_conn() as conn:
        for item in news_items:
            conn.execute(
                """
                INSERT OR IGNORE INTO news(title, source, published_at, url, summary, keywords, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("title", ""),
                    item.get("source", ""),
                    item.get("published_at", ""),
                    item.get("url") or None,
                    item.get("summary", ""),
                    json.dumps(item.get("keywords", []), ensure_ascii=False),
                    now_text(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM news WHERE (url IS NOT NULL AND url = ?) OR (title = ? AND source = ? AND published_at = ?)",
                (item.get("url"), item.get("title", ""), item.get("source", ""), item.get("published_at", "")),
            ).fetchone()
            if row:
                ids[item.get("dedup_key") or item.get("url") or item.get("title", "")] = int(row["id"])
    return ids


def load_cached_news_for_stock(code: str, limit: int = 8, include_mock: bool = False) -> list[dict[str, Any]]:
    query = """
        SELECT n.title, n.source, n.published_at, n.url, n.summary, n.keywords
        FROM news n
        JOIN stock_news sn ON sn.news_id = n.id
        WHERE sn.code = ?
    """
    params: list[Any] = [code]
    if not include_mock:
        query += " AND n.source != ?"
        params.append("MVP模拟新闻")
    query += " ORDER BY n.published_at DESC, n.id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["keywords"] = json.loads(item.get("keywords") or "[]")
        except json.JSONDecodeError:
            item["keywords"] = []
        item["dedup_key"] = item.get("url") or item.get("title", "")
        item["source"] = f"{item.get('source', '')}（缓存）"
        items.append(item)
    return items


def save_stock_news(code: str, news_id: int, analysis: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO stock_news(code, news_id, relevance_score, sentiment, sentiment_reason, risk_level, risk_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, news_id) DO UPDATE SET
                relevance_score=excluded.relevance_score,
                sentiment=excluded.sentiment,
                sentiment_reason=excluded.sentiment_reason,
                risk_level=excluded.risk_level,
                risk_score=excluded.risk_score,
                created_at=excluded.created_at
            """,
            (
                code,
                news_id,
                analysis.get("relevance_score"),
                analysis.get("sentiment"),
                analysis.get("sentiment_reason"),
                analysis.get("risk_level"),
                analysis.get("risk_score"),
                now_text(),
            ),
        )


def save_macro_events(events: list[dict[str, Any]]) -> None:
    if not events:
        return
    with get_conn() as conn:
        for item in events:
            conn.execute(
                """
                INSERT OR IGNORE INTO macro_events(event_date, title, source, importance, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("event_date", ""),
                    item.get("title", ""),
                    item.get("source", ""),
                    item.get("importance", "medium"),
                    item.get("summary", ""),
                    now_text(),
                ),
            )


def save_analysis_snapshot(code: str, run_date: str, snapshot: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO analysis_snapshots(
                code, run_date, relevance_avg, sentiment_summary, risk_level, risk_score,
                technical_summary, scenario_json, uncertainties, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                run_date,
                snapshot.get("relevance_avg"),
                snapshot.get("sentiment_summary", ""),
                snapshot.get("risk_level", ""),
                snapshot.get("risk_score"),
                snapshot.get("technical_summary", ""),
                json.dumps(snapshot.get("scenarios", {}), ensure_ascii=False),
                "\n".join(snapshot.get("uncertainties", [])),
                now_text(),
            ),
        )


def save_report(report_date: str, title: str, path: Path, content: str, sent_email: bool = False) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reports(report_date, title, path, content, sent_email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (report_date, title, str(path), content, 1 if sent_email else 0, now_text()),
        )


def read_sql(query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)
