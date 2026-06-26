from __future__ import annotations

from typing import Any

from utils import LOGGER, normalize_code


PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "600893": {
        "code": "600893",
        "name": "航发动力",
        "market": "A股",
        "themes": ["国防与装备", "航空航天装备", "航天装备", "航空发动机"],
    },
    "601138": {
        "code": "601138",
        "name": "工业富联",
        "market": "A股",
        "themes": ["电子制造", "工业互联网", "AI服务器", "算力基础设施"],
    }
}


def lookup_stock_profile(code: str, timeout: float = 8) -> dict[str, Any]:
    normalized = normalize_code(code)
    if not normalized:
        raise ValueError("股票代码不能为空")
    if normalized in PROFILE_OVERRIDES:
        result = dict(PROFILE_OVERRIDES[normalized])
        result["source"] = "本地兜底映射"
        return result

    errors: list[str] = []
    for lookup in (_lookup_tencent_quote, _lookup_sina_quote, _lookup_individual_info, _lookup_spot_info):
        try:
            result = lookup(normalized, timeout=timeout)
            if result.get("name") or result.get("themes"):
                if not result.get("themes") and normalized in PROFILE_OVERRIDES:
                    result["themes"] = PROFILE_OVERRIDES[normalized].get("themes", [])
                return result
        except Exception as exc:
            errors.append(f"{lookup.__name__}: {exc}")
            LOGGER.warning("stock profile lookup failed for %s via %s: %s", normalized, lookup.__name__, exc)
    return {"code": normalized, "name": "", "market": infer_market(normalized), "themes": [], "errors": errors}


def _lookup_tencent_quote(code: str, timeout: float = 8) -> dict[str, Any]:
    import requests

    symbol = quote_symbol(code)
    response = requests.get(
        f"https://qt.gtimg.cn/q={symbol}",
        timeout=min(timeout, 4),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.encoding = "gbk"
    text = response.text.strip()
    if '="' not in text:
        return {}
    payload = text.split('="', 1)[1].rstrip('";')
    parts = payload.split("~")
    if len(parts) < 3 or parts[0] in {"", "0"}:
        return {}
    name = clean_stock_name(parts[1])
    return {
        "code": code,
        "name": name,
        "market": infer_market(code),
        "themes": [],
        "source": "腾讯轻量行情",
        "raw": {"quote": text[:300]},
    }


def _lookup_sina_quote(code: str, timeout: float = 8) -> dict[str, Any]:
    import requests

    symbol = quote_symbol(code)
    response = requests.get(
        f"https://hq.sinajs.cn/list={symbol}",
        timeout=min(timeout, 4),
        headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"},
    )
    response.encoding = "gb18030"
    text = response.text.strip()
    if '="' not in text:
        return {}
    payload = text.split('="', 1)[1].rstrip('";')
    parts = payload.split(",")
    if not parts or not parts[0].strip():
        return {}
    return {
        "code": code,
        "name": clean_stock_name(parts[0]),
        "market": infer_market(code),
        "themes": [],
        "source": "新浪轻量行情",
        "raw": {"quote": text[:300]},
    }


def _lookup_individual_info(code: str, timeout: float = 8) -> dict[str, Any]:
    import akshare as ak

    frame = ak.stock_individual_info_em(symbol=code, timeout=timeout)
    if frame is None or frame.empty:
        return {}
    data = {}
    if {"item", "value"}.issubset(frame.columns):
        data = {str(row["item"]): row["value"] for _, row in frame.iterrows()}
    else:
        for _, row in frame.iterrows():
            if len(row) >= 2:
                data[str(row.iloc[0])] = row.iloc[1]
    name = first_present(data, ["股票简称", "简称", "名称", "股票名称"])
    industry = first_present(data, ["行业", "所属行业", "行业板块"])
    themes = [str(industry).strip()] if industry else []
    return {"code": code, "name": clean_stock_name(name), "market": infer_market(code), "themes": themes, "raw": data}


def _lookup_spot_info(code: str, timeout: float = 8) -> dict[str, Any]:
    import akshare as ak

    frame = ak.stock_zh_a_spot_em()
    if frame is None or frame.empty:
        return {}
    rows = frame[frame["代码"].astype(str).str.zfill(6) == code]
    if rows.empty:
        return {}
    row = rows.iloc[0].to_dict()
    industry = row.get("所属行业") or row.get("行业") or row.get("板块")
    themes = [str(industry).strip()] if industry else []
    return {"code": code, "name": clean_stock_name(row.get("名称")), "market": infer_market(code), "themes": themes, "raw": row}


def first_present(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in {None, ""}:
            return value
    return ""


def clean_stock_name(name: Any) -> str:
    text = str(name or "").strip()
    for prefix in ["XD", "XR", "DR", "N", "C"]:
        if text.upper().startswith(prefix) and len(text) > len(prefix) + 1:
            return text[len(prefix):].strip()
    return text


def infer_market(code: str) -> str:
    if code.startswith(("5", "15", "16", "18")):
        return "ETF"
    return "A股"


def quote_symbol(code: str) -> str:
    if code.startswith(("6", "5")):
        return f"sh{code}"
    return f"sz{code}"
