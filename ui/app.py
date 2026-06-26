from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import load_watchlist
from database import init_db, read_sql
from main import run_daily_pipeline
from utils import DB_PATH, REPORT_DIR


st.set_page_config(page_title="Stock Watch Assistant", layout="wide")
st.title("Stock Watch Assistant")

init_db()

with st.sidebar:
    st.header("操作")
    send_email = st.checkbox("生成后发送邮件", value=False)
    if st.button("立即运行信息雷达", type="primary"):
        with st.spinner("正在抓取行情、新闻并生成报告..."):
            result = run_daily_pipeline(send_email=send_email)
        st.success(f"报告已生成：{result['report_path']}")
    st.caption(f"数据库：{DB_PATH}")


def safe_read(query: str) -> pd.DataFrame:
    try:
        return read_sql(query)
    except Exception as exc:
        st.warning(f"读取数据库失败：{exc}")
        return pd.DataFrame()


tabs = st.tabs(["自选股列表", "今日新闻", "每只股票评分", "历史报告", "风险提醒"])

with tabs[0]:
    st.subheader("自选股列表")
    try:
        config = load_watchlist()
        watchlist = pd.DataFrame(config.get("stocks", []))
        if not watchlist.empty:
            watchlist["themes"] = watchlist["themes"].apply(lambda x: "、".join(x) if isinstance(x, list) else x)
            watchlist["key_levels"] = watchlist["key_levels"].apply(lambda x: json.dumps(x, ensure_ascii=False))
        st.dataframe(watchlist, use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(f"读取 watchlist.yaml 失败：{exc}")

with tabs[1]:
    st.subheader("今日新闻")
    news = safe_read(
        """
        SELECT n.published_at, n.source, n.title, n.summary, n.url,
               sn.code, sn.relevance_score, sn.sentiment, sn.sentiment_reason, sn.risk_level
        FROM news n
        LEFT JOIN stock_news sn ON sn.news_id = n.id
        ORDER BY n.id DESC
        LIMIT 200
        """
    )
    st.dataframe(news, use_container_width=True, hide_index=True)

with tabs[2]:
    st.subheader("每只股票评分")
    scores = safe_read(
        """
        SELECT s.name, a.code, a.run_date, a.relevance_avg, a.sentiment_summary,
               a.risk_level, a.risk_score, a.technical_summary, a.created_at
        FROM analysis_snapshots a
        LEFT JOIN stocks s ON s.code = a.code
        ORDER BY a.id DESC
        LIMIT 200
        """
    )
    st.dataframe(scores, use_container_width=True, hide_index=True)
    if not scores.empty:
        latest = scores.drop_duplicates("code", keep="first")
        chart = latest[["name", "risk_score"]].dropna()
        if not chart.empty:
            st.bar_chart(chart.set_index("name"))

with tabs[3]:
    st.subheader("历史报告")
    reports = safe_read("SELECT id, report_date, title, path, sent_email, created_at FROM reports ORDER BY id DESC LIMIT 100")
    st.dataframe(reports, use_container_width=True, hide_index=True)
    report_files = sorted(REPORT_DIR.glob("*.md"), reverse=True)
    if report_files:
        selected = st.selectbox("查看报告", report_files, format_func=lambda p: p.name)
        st.markdown(selected.read_text(encoding="utf-8"))
    else:
        st.info("暂无报告。")

with tabs[4]:
    st.subheader("风险提醒")
    risks = safe_read(
        """
        SELECT s.name, a.code, a.risk_level, a.risk_score, a.sentiment_summary,
               a.technical_summary, a.created_at
        FROM analysis_snapshots a
        LEFT JOIN stocks s ON s.code = a.code
        WHERE a.risk_level IN ('medium', 'high')
        ORDER BY a.id DESC
        LIMIT 100
        """
    )
    if risks.empty:
        st.info("暂无中高风险记录。")
    else:
        for _, row in risks.iterrows():
            level = row["risk_level"]
            st.warning(
                f"{row['name']}（{row['code']}）风险 {level} / {row['risk_score']}："
                f"{row['technical_summary']}\n\n{row['sentiment_summary']}"
            )

