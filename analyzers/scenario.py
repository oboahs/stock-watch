from __future__ import annotations

from typing import Any

from utils import fmt_num, safe_float


def build_scenarios(stock: dict[str, Any], technical: dict[str, Any], risk: dict[str, Any]) -> dict[str, dict[str, str]]:
    latest = technical.get("latest") or {}
    close = safe_float(latest.get("close"))
    key_levels = stock.get("key_levels") or {}
    supports = [safe_float(item) for item in key_levels.get("support", []) or []]
    resistances = [safe_float(item) for item in key_levels.get("resistance", []) or []]
    supports = [item for item in supports if item is not None]
    resistances = [item for item in resistances if item is not None]
    nearest_support = max([item for item in supports if close is None or item <= close], default=(supports[0] if supports else None))
    nearest_resistance = min([item for item in resistances if close is None or item >= close], default=None)
    highest_resistance = max(resistances, default=None)

    support_text = fmt_num(nearest_support) if nearest_support is not None else "未设置"
    resistance_value = nearest_resistance if nearest_resistance is not None else highest_resistance
    resistance_text = fmt_num(resistance_value) if resistance_value is not None else "未设置"
    close_text = fmt_num(close)
    risk_level = risk.get("level", "medium")
    support_break = f"跌破支撑 {support_text}" if nearest_support is not None else "跌破短期均线"
    support_recover = f"价格快速收复 {support_text}" if nearest_support is not None else "价格快速收复短期均线"
    neutral_range = (
        f"价格在支撑 {support_text} 与压力 {resistance_text} 区间内震荡"
        if nearest_support is not None or resistance_value is not None
        else "价格围绕当前价位和短期均线震荡"
    )
    if nearest_resistance is not None:
        optimistic_trigger = f"价格站稳 {close_text} 附近并向上突破/接近压力位 {resistance_text}，同时新闻保持中性偏正面、成交量不异常萎缩。"
    elif highest_resistance is not None:
        optimistic_trigger = f"价格站稳 {close_text} 附近并已高于已设置压力位 {resistance_text}，观察前压力位能否转为支撑，同时新闻保持中性偏正面、成交量不异常萎缩。"
    else:
        optimistic_trigger = f"价格站稳 {close_text} 附近，短期均线方向改善并出现量价配合，同时新闻保持中性偏正面、成交量不异常萎缩。"

    return {
        "optimistic": {
            "title": "乐观情景",
            "trigger": optimistic_trigger,
            "invalid": f"重新跌回关键均线下方，或{support_break}，且出现高相关利空。",
            "watch": "观察突破是否有成交量配合，以及是否有公告或行业事件验证上涨逻辑。",
        },
        "neutral": {
            "title": "中性情景",
            "trigger": f"{neutral_range}，新闻没有明确方向。",
            "invalid": "出现放量突破、放量跌破、重大公告或宏观风险事件。",
            "watch": "观察区间边界、20日均线方向和新闻相关性分数是否抬升。",
        },
        "pessimistic": {
            "title": "悲观情景",
            "trigger": f"风险等级维持 {risk_level} 或升高，{support_break}，并伴随利空新闻或放量下跌。",
            "invalid": f"{support_recover}并重新站上20日均线，利空因素被公告或后续数据证伪。",
            "watch": "观察跌破后的成交量、反抽力度、持仓浮亏和风控观察位。",
        },
    }
