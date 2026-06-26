from __future__ import annotations

from typing import Any

import pandas as pd

from utils import fmt_pct, safe_float


def analyze_technical(frame: pd.DataFrame, key_levels: dict[str, Any] | None = None) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {
            "summary": "行情数据不足，无法判断技术面。",
            "signals": ["行情缺失"],
            "latest": {},
            "uncertainties": ["AKShare 或公开行情接口可能失败、延迟或字段变化。"],
        }
    latest = frame.iloc[-1].to_dict()
    close = safe_float(latest.get("close"))
    ma5 = safe_float(latest.get("ma5"))
    ma20 = safe_float(latest.get("ma20"))
    ma60 = safe_float(latest.get("ma60"))
    change_pct = safe_float(latest.get("change_pct"))
    signals = [latest.get("volume_signal", "成交量信号不足"), latest.get("price_signal", "价格信号不足")]

    if close is not None and ma5 is not None and close > ma5:
        signals.append("收盘价强于5日均线")
    if close is not None and ma20 is not None and close > ma20:
        signals.append("收盘价强于20日均线")
    if close is not None and ma60 is not None and close < ma60:
        signals.append("仍弱于60日均线")

    key_note = _key_level_note(close, key_levels or {})
    if key_note:
        signals.append(key_note)

    summary = f"最新收盘 {close if close is not None else 'N/A'}，涨跌幅 {fmt_pct(change_pct)}；" + "；".join(signals[:5])
    return {
        "summary": summary,
        "signals": [signal for signal in signals if signal],
        "latest": latest,
        "uncertainties": [],
    }


def _key_level_note(close: float | None, key_levels: dict[str, Any]) -> str:
    if close is None:
        return ""
    supports = [safe_float(item) for item in key_levels.get("support", []) or []]
    resistances = [safe_float(item) for item in key_levels.get("resistance", []) or []]
    supports = [item for item in supports if item is not None]
    resistances = [item for item in resistances if item is not None]
    next_support = max([item for item in supports if item <= close], default=None)
    next_resistance = min([item for item in resistances if item >= close], default=None)
    parts = []
    if next_support:
        parts.append(f"下方观察支撑 {next_support:g}")
    if next_resistance:
        parts.append(f"上方观察压力 {next_resistance:g}")
    return "，".join(parts)

