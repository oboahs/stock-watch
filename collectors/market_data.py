from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from collectors.stock_profile import clean_stock_name
from database import load_cached_market_data
from utils import LOGGER, normalize_code, safe_float


MARKET_ALIASES = {
    "A股": "a_share",
    "A": "a_share",
    "ETF": "etf",
    "A股ETF": "etf",
    "基金": "fund",
    "FUND": "fund",
    "美股": "us_stock",
    "US": "us_stock",
    "USA": "us_stock",
    "US_STOCK": "us_stock",
    "NASDAQ": "us_stock",
    "NYSE": "us_stock",
    "AMEX": "us_stock",
}


def fetch_market_data(stock: dict[str, Any], days: int = 180) -> dict[str, Any]:
    market_type = MARKET_ALIASES.get(str(stock.get("market", "A股")).upper(), "a_share")
    code = normalize_us_symbol(stock["code"]) if market_type == "us_stock" else normalize_code(stock["code"])
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")

    try:
        if market_type == "us_stock":
            raw, source = _fetch_us_history(code, days=days)
        else:
            raw = _fetch_akshare_history(code, market_type, start_date, end_date)
            source = f"AKShare {market_type}"
        frame = normalize_history(raw)
        if frame.empty:
            raise RuntimeError(f"{source} returned empty history")
        frame = add_indicators(frame, stock.get("key_levels", {}))
        latest = frame.iloc[-1].to_dict()
        return {
            "ok": True,
            "source": source,
            "data": frame,
            "latest": latest,
            "warning": "",
        }
    except Exception as exc:
        LOGGER.exception("market data fetch failed for %s %s", code, stock.get("name"))
        cached = load_cached_market_data(code, limit=days)
        if not cached.empty:
            cached = add_indicators(cached, stock.get("key_levels", {}))
            latest = cached.iloc[-1].to_dict()
            return {
                "ok": True,
                "source": "SQLite缓存行情",
                "data": cached,
                "latest": latest,
                "stale": True,
                "warning": f"{code} AKShare 行情抓取失败，已使用本地缓存行情：{exc}",
            }
        realtime = _fetch_us_realtime_frame(code) if market_type == "us_stock" else _fetch_realtime_frame(code)
        if not realtime.empty:
            realtime = add_indicators(realtime, stock.get("key_levels", {}))
            latest = realtime.iloc[-1].to_dict()
            return {
                "ok": True,
                "source": latest.get("source", "实时行情兜底"),
                "data": realtime,
                "latest": latest,
                "stale": False,
                "realtime_only": True,
                "warning": f"{code} AKShare 行情抓取失败，且无本地历史缓存；已使用实时行情兜底，均线可靠性不足：{exc}",
            }
        return {
            "ok": False,
            "source": "美股公开行情源" if market_type == "us_stock" else "AKShare",
            "data": pd.DataFrame(),
            "latest": {},
            "stale": False,
            "warning": f"{code} 行情抓取失败：{exc}",
        }


def _fetch_akshare_history(code: str, market_type: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    if market_type == "etf":
        return ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    if market_type == "fund":
        try:
            return ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        except Exception:
            return ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    return ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")


def _fetch_us_history(code: str, days: int = 180) -> tuple[pd.DataFrame, str]:
    errors = []
    for source_name, fetcher in [
        ("Nasdaq Historical API", _fetch_nasdaq_history),
        ("Yahoo Finance Chart", _fetch_yahoo_history),
        ("Stooq Daily CSV", _fetch_stooq_history),
        ("Alpha Vantage Daily", _fetch_alpha_vantage_history),
        ("Financial Modeling Prep Daily", _fetch_fmp_history),
    ]:
        try:
            frame = fetcher(code, days)
            if not frame.empty:
                return frame, source_name
            errors.append(f"{source_name}: empty")
        except Exception as exc:
            LOGGER.warning("US market source failed for %s via %s: %s", code, source_name, exc)
            errors.append(f"{source_name}: {exc}")
    raise RuntimeError("；".join(errors) or "美股行情源均无数据")


def _fetch_nasdaq_history(code: str, days: int = 180) -> pd.DataFrame:
    symbol = normalize_us_symbol(code)
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    response = requests.get(
        f"https://api.nasdaq.com/api/quote/{symbol}/historical",
        params={"assetclass": "stocks", "fromdate": from_date, "todate": to_date, "limit": days * 3},
        timeout=15,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Origin": "https://www.nasdaq.com",
            "Referer": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/historical",
        },
    )
    response.raise_for_status()
    rows = (((response.json().get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    records = []
    for row in rows:
        close = money_float(row.get("close"))
        records.append(
            {
                "date": row.get("date"),
                "open": money_float(row.get("open")),
                "high": money_float(row.get("high")),
                "low": money_float(row.get("low")),
                "close": close,
                "volume": safe_float(row.get("volume")),
                "amount": None,
                "change_pct": None,
                "turnover_rate": None,
            }
        )
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], format="%m/%d/%Y", errors="coerce").dt.strftime("%Y-%m-%d")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").tail(days).reset_index(drop=True)
    frame["change_pct"] = pd.to_numeric(frame["close"], errors="coerce").pct_change() * 100
    return frame


def _fetch_yahoo_history(code: str, days: int = 180) -> pd.DataFrame:
    symbol = normalize_us_symbol(code)
    period1 = int((datetime.now() - timedelta(days=days * 2)).timestamp())
    period2 = int((datetime.now() + timedelta(days=1)).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = requests.get(
        url,
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        },
        timeout=12,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    data = response.json()
    results = ((data.get("chart") or {}).get("result") or [])
    if not results:
        return pd.DataFrame()
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjclose = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    rows = []
    previous_close = None
    for index, ts in enumerate(timestamps):
        close = value_at(quote.get("close"), index)
        if close is None:
            continue
        adjusted_close = value_at(adjclose, index)
        change_pct = ((close / previous_close - 1) * 100) if previous_close else None
        rows.append(
            {
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                "open": value_at(quote.get("open"), index),
                "high": value_at(quote.get("high"), index),
                "low": value_at(quote.get("low"), index),
                "close": adjusted_close if adjusted_close is not None else close,
                "volume": value_at(quote.get("volume"), index),
                "amount": None,
                "change_pct": change_pct,
                "turnover_rate": None,
            }
        )
        previous_close = close
    return pd.DataFrame(rows)


def _fetch_stooq_history(code: str, days: int = 180) -> pd.DataFrame:
    symbol = normalize_us_symbol(code).lower()
    response = requests.get(
        f"https://stooq.com/q/d/l/?s={symbol}.us&i=d",
        timeout=12,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    if "Date,Open,High,Low,Close,Volume" not in response.text:
        return pd.DataFrame()
    from io import StringIO

    frame = pd.read_csv(StringIO(response.text))
    if frame.empty:
        return pd.DataFrame()
    frame = frame.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    frame["amount"] = None
    frame["turnover_rate"] = None
    frame["change_pct"] = pd.to_numeric(frame["close"], errors="coerce").pct_change() * 100
    return frame.tail(days)


def _fetch_alpha_vantage_history(code: str, days: int = 180) -> pd.DataFrame:
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        return pd.DataFrame()
    symbol = normalize_us_symbol(code)
    response = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": api_key,
        },
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    data = response.json()
    series = data.get("Time Series (Daily)") or {}
    rows = []
    for date_text, row in series.items():
        close = safe_float(row.get("5. adjusted close") or row.get("4. close"))
        previous_close = None
        rows.append(
            {
                "date": date_text,
                "open": safe_float(row.get("1. open")),
                "high": safe_float(row.get("2. high")),
                "low": safe_float(row.get("3. low")),
                "close": close,
                "volume": safe_float(row.get("6. volume")),
                "amount": None,
                "change_pct": previous_close,
                "turnover_rate": None,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.sort_values("date").tail(days).reset_index(drop=True)
    frame["change_pct"] = pd.to_numeric(frame["close"], errors="coerce").pct_change() * 100
    return frame


def _fetch_fmp_history(code: str, days: int = 180) -> pd.DataFrame:
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        return pd.DataFrame()
    symbol = normalize_us_symbol(code)
    response = requests.get(
        f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}",
        params={"serietype": "line", "apikey": api_key},
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    rows = response.json().get("historical") or []
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.rename(columns={"date": "date", "close": "close"})
    for column in ["open", "high", "low", "volume"]:
        if column not in frame.columns:
            frame[column] = None
    frame["amount"] = None
    frame["turnover_rate"] = None
    frame["change_pct"] = pd.to_numeric(frame["close"], errors="coerce").pct_change(periods=-1) * -100
    return frame[["date", "open", "high", "low", "close", "volume", "amount", "change_pct", "turnover_rate"]].sort_values("date").tail(days)


def _fetch_us_realtime_frame(code: str) -> pd.DataFrame:
    for fetcher in (_fetch_finnhub_quote, _fetch_yahoo_quote):
        try:
            frame = fetcher(code)
            if not frame.empty:
                return frame
        except Exception as exc:
            LOGGER.warning("US realtime fallback failed for %s via %s: %s", code, fetcher.__name__, exc)
    return pd.DataFrame()


def _fetch_finnhub_quote(code: str) -> pd.DataFrame:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return pd.DataFrame()
    symbol = normalize_us_symbol(code)
    response = requests.get(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": symbol, "token": api_key},
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    data = response.json()
    close = safe_float(data.get("c"))
    previous_close = safe_float(data.get("pc"))
    if close is None:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "date": datetime.fromtimestamp(int(data.get("t") or datetime.now().timestamp())).strftime("%Y-%m-%d"),
                "open": safe_float(data.get("o")),
                "high": safe_float(data.get("h")),
                "low": safe_float(data.get("l")),
                "close": close,
                "volume": None,
                "amount": None,
                "change_pct": ((close / previous_close - 1) * 100) if previous_close else None,
                "turnover_rate": None,
                "source": "Finnhub实时报价",
            }
        ]
    )


def _fetch_yahoo_quote(code: str) -> pd.DataFrame:
    frame = _fetch_yahoo_history(code, days=5)
    if frame.empty:
        return frame
    frame = frame.tail(1).copy()
    frame["source"] = "Yahoo Finance最新报价"
    return frame


def _fetch_realtime_frame(code: str) -> pd.DataFrame:
    for fetcher in (_fetch_sina_realtime, _fetch_tencent_realtime):
        try:
            frame = fetcher(code)
            if not frame.empty:
                return frame
        except Exception as exc:
            LOGGER.warning("realtime fallback failed for %s via %s: %s", code, fetcher.__name__, exc)
    return pd.DataFrame()


def _fetch_sina_realtime(code: str) -> pd.DataFrame:
    prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
    response = requests.get(
        f"https://hq.sinajs.cn/list={prefix}{code}",
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
    )
    response.raise_for_status()
    text = response.text
    if '="' not in text:
        return pd.DataFrame()
    payload = text.split('="', 1)[1].rsplit('"', 1)[0]
    parts = payload.split(",")
    if len(parts) < 32 or not parts[0]:
        return pd.DataFrame()
    open_price = safe_float(parts[1])
    previous_close = safe_float(parts[2])
    close = safe_float(parts[3])
    change_pct = ((close / previous_close - 1) * 100) if close is not None and previous_close else None
    return pd.DataFrame(
        [
            {
                "date": parts[30],
                "open": open_price,
                "high": safe_float(parts[4]),
                "low": safe_float(parts[5]),
                "close": close,
                "volume": safe_float(parts[8]),
                "amount": safe_float(parts[9]),
                "change_pct": change_pct,
                "turnover_rate": None,
                "name": clean_stock_name(parts[0]),
                "source": "新浪实时行情",
            }
        ]
    )


def _fetch_tencent_realtime(code: str) -> pd.DataFrame:
    prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
    response = requests.get(
        f"https://qt.gtimg.cn/q={prefix}{code}",
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    text = response.text
    if '="' not in text:
        return pd.DataFrame()
    payload = text.split('="', 1)[1].rsplit('"', 1)[0]
    parts = payload.split("~")
    if len(parts) < 40:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "date": str(parts[30])[:8],
                "open": safe_float(parts[5]),
                "high": safe_float(parts[33]),
                "low": safe_float(parts[34]),
                "close": safe_float(parts[3]),
                "volume": safe_float(parts[6]),
                "amount": safe_float(parts[37]),
                "change_pct": safe_float(parts[32]),
                "turnover_rate": safe_float(parts[38]),
                "name": clean_stock_name(parts[1]),
                "source": "腾讯实时行情",
            }
        ]
    )


def normalize_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "change_pct",
        "换手率": "turnover_rate",
    }
    frame = raw.rename(columns=rename_map).copy()
    needed = ["date", "open", "high", "low", "close", "volume", "amount", "change_pct", "turnover_rate"]
    for column in needed:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[needed]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in needed[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return frame


def add_indicators(frame: pd.DataFrame, key_levels: dict[str, Any] | None = None) -> pd.DataFrame:
    result = frame.copy()
    for window in [5, 10, 20, 60]:
        result[f"ma{window}"] = result["close"].rolling(window=window, min_periods=1).mean().round(4)

    result["volume_ma5"] = result["volume"].rolling(window=5, min_periods=1).mean()
    result["volume_signal"] = result.apply(_volume_signal, axis=1)
    result["price_signal"] = result.apply(lambda row: _price_signal(row, key_levels or {}), axis=1)
    result = result.drop(columns=["volume_ma5"])
    return result


def _volume_signal(row: pd.Series) -> str:
    volume = safe_float(row.get("volume"))
    avg = safe_float(row.get("volume_ma5"))
    if not volume or not avg:
        return "数据不足"
    if volume >= avg * 1.5:
        return "放量"
    if volume <= avg * 0.7:
        return "缩量"
    return "正常"


def _price_signal(row: pd.Series, key_levels: dict[str, Any]) -> str:
    close = safe_float(row.get("close"))
    ma20 = safe_float(row.get("ma20"))
    if close is None:
        return "数据不足"
    signals = []
    if ma20 is not None:
        if close > ma20:
            signals.append("站上20日线")
        elif close < ma20:
            signals.append("跌破20日线")

    for level in key_levels.get("resistance", []) or []:
        value = safe_float(level)
        if value and close >= value:
            signals.append(f"突破压力位{value:g}")
    for level in key_levels.get("support", []) or []:
        value = safe_float(level)
        if value and close <= value:
            signals.append(f"跌破支撑位{value:g}")
    stop_watch = safe_float(key_levels.get("stop_watch"))
    if stop_watch and close <= stop_watch:
        signals.append(f"触及风控观察位{stop_watch:g}")
    return "；".join(signals) if signals else "区间震荡"


def normalize_us_symbol(code: str) -> str:
    return str(code).strip().upper().replace("-", ".")


def value_at(values: Any, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    return safe_float(values[index])


def money_float(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    return safe_float(value)
