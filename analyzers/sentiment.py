from __future__ import annotations

from typing import Any


BULLISH_WORDS = ["中标", "增持", "回购", "净利润增长", "业绩预增", "上调评级", "盈利增长", "订单", "涨价", "景气", "利好", "涨停"]
BEARISH_WORDS = ["减持", "亏损", "处罚", "诉讼", "业绩下滑", "净利润下降", "暴跌", "违约", "跌停", "退市", "利空", "立案", "调查", "监管函", "债务逾期"]
UNCERTAIN_WORDS = ["传闻", "网传", "未经证实", "可能", "预计", "模拟"]
WEAK_CONTEXT_WORDS = ["风险", "关注", "突破", "跌破"]


def classify_sentiment(news_item: dict[str, Any]) -> dict[str, str]:
    if str(news_item.get("source", "")).startswith("MVP模拟"):
        return {
            "sentiment": "uncertain",
            "reason": "该条为 MVP 模拟新闻，只用于验证流程，不能当作真实利好或利空。",
        }
    if news_item.get("scope") == "sector":
        return {
            "sentiment": "neutral",
            "reason": "该条为板块/行业动态，只作为综合参考，不单独推断个股利好或利空。",
        }

    text = f"{news_item.get('title', '')} {news_item.get('summary', '')}"
    bullish_hits = [word for word in BULLISH_WORDS if word in text]
    bearish_hits = [word for word in BEARISH_WORDS if word in text]
    uncertain_hits = [word for word in UNCERTAIN_WORDS if word in text]
    weak_hits = [word for word in WEAK_CONTEXT_WORDS if word in text]

    if bearish_hits and len(bearish_hits) >= len(bullish_hits):
        label = "bearish"
        reason = f"标题或摘要出现明确负面事件词：{'、'.join(bearish_hits[:4])}"
    elif bullish_hits:
        label = "bullish"
        reason = f"标题或摘要出现明确正面事件词：{'、'.join(bullish_hits[:4])}"
    elif uncertain_hits:
        label = "uncertain"
        reason = f"信息含不确定表达：{'、'.join(uncertain_hits[:4])}"
    else:
        label = "neutral"
        if weak_hits:
            reason = f"仅出现 {'、'.join(weak_hits[:3])} 等泛化词，不能据此判断方向，暂按中性处理"
        else:
            reason = "未发现明确利好或利空事件词，暂按中性处理"

    if uncertain_hits and label in {"bullish", "bearish"}:
        reason = f"{reason}；同时含不确定表达：{'、'.join(uncertain_hits[:3])}"
    return {"sentiment": label, "reason": reason}
