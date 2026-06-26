from __future__ import annotations

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import load_watchlist, save_watchlist
from collectors.stock_profile import lookup_stock_profile
from database import init_db, sync_stocks
from main import run_daily_pipeline
from reports.daily_report import markdown_to_basic_html
from reports.llm_analysis import DEFAULT_LLM_PROMPT
from utils import BUNDLE_ROOT, DB_PATH, ENV_EXAMPLE_PATH, ENV_PATH, REPORT_DIR


FONT = ("PingFang SC", 13)
FONT_SMALL = ("PingFang SC", 11)
FONT_TITLE = ("PingFang SC", 22, "bold")
BG = "#f6f7f9"
PANEL = "#ffffff"
TEXT = "#172033"
MUTED = "#667085"
GREEN = "#1f9d55"
AMBER = "#d97706"
RED = "#dc2626"
BLUE = "#2563eb"
STOCKS_QUERY = """
WITH latest_snapshot AS (
    SELECT * FROM analysis_snapshots
    WHERE id IN (SELECT MAX(id) FROM analysis_snapshots GROUP BY code)
),
latest_market AS (
    SELECT m.* FROM market_data m
    JOIN (SELECT code, MAX(trade_date) AS max_date FROM market_data GROUP BY code) x
    ON x.code = m.code AND x.max_date = m.trade_date
)
SELECT s.name, s.code, COALESCE(a.risk_level, '未评分') AS risk_level,
       COALESCE(a.risk_score, 0) AS risk_score,
       COALESCE(a.relevance_avg, 0) AS relevance_avg,
       m.close, m.change_pct, COALESCE(a.created_at, s.updated_at) AS updated_at
FROM stocks s
LEFT JOIN latest_snapshot a ON a.code = s.code
LEFT JOIN latest_market m ON m.code = s.code
ORDER BY a.risk_score DESC, s.code
"""
NEWS_QUERY = """
SELECT n.published_at, COALESCE(s.name, sn.code, '') AS stock_name, n.source,
       COALESCE(sn.sentiment, '') AS sentiment,
       COALESCE(sn.relevance_score, 0) AS relevance_score,
       n.title
FROM news n
LEFT JOIN stock_news sn ON sn.news_id = n.id
LEFT JOIN stocks s ON s.code = sn.code
ORDER BY n.id DESC
LIMIT 200
"""
TREND_QUERY = """
SELECT a.run_date, a.created_at, s.name, a.code, a.risk_level, a.risk_score, a.relevance_avg
FROM analysis_snapshots a
LEFT JOIN stocks s ON s.code = a.code
ORDER BY a.id DESC
LIMIT 200
"""
REPORT_PREVIEW_LIMIT = 200_000


class StockWatchDesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        init_db()
        self.title("Stock Watch Assistant")
        self._set_window_icon()
        self.geometry("1280x820")
        self.minsize(1080, 680)
        self.configure(bg=BG)
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.email_var = tk.BooleanVar(value=False)
        self.config = load_config_safe()
        self.env_values = load_env_values()
        self.selected_stock_index: int | None = None
        self.stock_vars: dict[str, tk.Variable] = {}
        self.setting_vars: dict[str, tk.Variable] = {}
        self.env_vars: dict[str, tk.Variable] = {}
        self.preferred_report_path: str | None = None
        self.refresh_in_progress = False
        self._closing = False
        self._refresh_after_id: str | None = None
        self._poll_after_id: str | None = None
        self._snapshot_cache: dict[str, object] = {}
        self._dirty_tabs: set[str] = set()
        self._setup_style()
        self._build_layout()
        self.load_settings_into_forms()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_after_id = self.after(2800, self.refresh_all)
        self._poll_after_id = self.after(350, self._poll_worker_queue)

    def _set_window_icon(self) -> None:
        icon_path = BUNDLE_ROOT / "assets" / "app_icon_1024.png"
        if not icon_path.exists():
            return
        try:
            self._window_icon = tk.PhotoImage(file=str(icon_path))
            self.iconphoto(True, self._window_icon)
        except tk.TclError:
            pass

    def close(self) -> None:
        self._closing = True
        for after_id in (self._refresh_after_id, self._poll_after_id):
            if after_id:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
        self.destroy()

    def _setup_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=FONT)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
        style.configure("TButton", font=FONT_SMALL, padding=(12, 8))
        style.configure("Compact.TButton", font=FONT_SMALL, padding=(8, 5))
        style.configure("Primary.TButton", font=FONT_SMALL, padding=(14, 9))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("PingFang SC", 11), padding=(10, 5))
        style.map(
            "TNotebook.Tab",
            font=[("selected", ("PingFang SC", 11, "bold"))],
            padding=[("selected", (12, 6))],
        )
        style.configure("Treeview", font=FONT_SMALL, rowheight=30, background=PANEL, fieldbackground=PANEL)
        style.configure("Treeview.Heading", font=("PingFang SC", 12, "bold"))

    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(22, 18, 22, 10))
        header.pack(fill="x")
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="Stock Watch Assistant", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="自选股信息雷达：行情、新闻、风险分、情景和日报都在这里看。", style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(header)
        actions.pack(side="right")
        ttk.Checkbutton(actions, text="发送邮件", variable=self.email_var).pack(side="left", padx=(0, 8))
        self.run_button = ttk.Button(actions, text="立即运行雷达", style="Primary.TButton", command=self.run_pipeline)
        self.run_button.pack(side="left", padx=4)
        self.refresh_button = ttk.Button(actions, text="刷新", command=self.refresh_all)
        self.refresh_button.pack(side="left", padx=4)
        ttk.Button(actions, text="设置中心", command=self.show_settings).pack(side="left", padx=4)
        ttk.Button(actions, text="报告目录", command=self.open_report_directory).pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", padding=(22, 0, 22, 8)).pack(fill="x")

        self.kpi_frame = ttk.Frame(self, padding=(22, 4, 22, 8))
        self.kpi_frame.pack(fill="x")
        self.kpi_labels: dict[str, ttk.Label] = {}
        for key, label in [("stocks", "自选股"), ("high", "高风险"), ("medium", "中风险"), ("reports", "报告数")]:
            card = ttk.Frame(self.kpi_frame, style="Panel.TFrame", padding=(18, 14))
            card.pack(side="left", fill="x", expand=True, padx=(0, 12))
            ttk.Label(card, text=label, style="Panel.TLabel").pack(anchor="w")
            value = ttk.Label(card, text="0", style="Panel.TLabel", font=("PingFang SC", 24, "bold"))
            value.pack(anchor="w", pady=(6, 0))
            self.kpi_labels[key] = value

        body = ttk.Frame(self, padding=(22, 0, 22, 18))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=260, style="Panel.TFrame", padding=(14, 14))
        left.pack(side="left", fill="y", padx=(0, 14))
        left.pack_propagate(False)
        ttk.Label(left, text="风险分布", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).pack(anchor="w")
        self.risk_canvas = tk.Canvas(left, bg=PANEL, highlightthickness=0, height=320)
        self.risk_canvas.pack(fill="x", pady=(12, 16))
        ttk.Label(left, text="最新风险提醒", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).pack(anchor="w")
        self.risk_text = tk.Text(left, height=12, wrap="word", bd=0, bg=PANEL, fg=TEXT, font=FONT_SMALL)
        self.risk_text.pack(fill="both", expand=True, pady=(8, 0))

        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        self._build_stock_tab()
        self._build_news_tab()
        self._build_report_tab()
        self._build_trend_tab()
        self._build_settings_tab()
        self.tabs.bind("<<NotebookTabChanged>>", self.on_main_tab_changed)

    def _build_stock_tab(self) -> None:
        frame = ttk.Frame(self.tabs, style="Panel.TFrame", padding=10)
        self.tabs.add(frame, text="股票评分")
        columns = ("名称", "代码", "风险", "分数", "相关性", "收盘", "涨跌幅", "更新时间")
        self.stock_tree = make_tree(frame, columns)

    def _build_news_tab(self) -> None:
        frame = ttk.Frame(self.tabs, style="Panel.TFrame", padding=10)
        self.tabs.add(frame, text="新闻雷达")
        columns = ("时间", "股票", "来源", "情绪", "相关性", "标题")
        self.news_tree = make_tree(frame, columns)

    def _build_report_tab(self) -> None:
        frame = ttk.Frame(self.tabs, style="Panel.TFrame", padding=10)
        self.tabs.add(frame, text="日报预览")
        top = ttk.Frame(frame, style="Panel.TFrame")
        top.pack(fill="x")
        self.report_choice = ttk.Combobox(top, state="readonly", font=FONT_SMALL)
        self.report_choice.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.report_choice.bind("<<ComboboxSelected>>", lambda _event: self.load_selected_report())
        ttk.Button(top, text="加载预览", command=self.load_selected_report).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="打开Markdown", command=self.open_selected_report).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="浏览器可视化", style="Primary.TButton", command=self.open_visual_report).pack(side="left")
        self.report_text = tk.Text(frame, wrap="word", bd=0, bg=PANEL, fg=TEXT, font=FONT_SMALL)
        self.report_text.pack(fill="both", expand=True, pady=(10, 0))

    def _build_trend_tab(self) -> None:
        frame = ttk.Frame(self.tabs, style="Panel.TFrame", padding=10)
        self.tabs.add(frame, text="趋势对比")
        top = ttk.Frame(frame, style="Panel.TFrame")
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="近几份日报横向对比：风险分与新闻相关性", style="Panel.TLabel", font=("PingFang SC", 15, "bold")).pack(side="left")
        self.trend_canvas = tk.Canvas(frame, bg=PANEL, highlightthickness=0, height=280)
        self.trend_canvas.pack(fill="x", pady=(0, 10))
        columns = ("生成时间", "名称", "代码", "风险", "风险分", "平均相关性")
        self.trend_tree = make_tree(frame, columns)

    def _build_settings_tab(self) -> None:
        frame = ttk.Frame(self.tabs, style="Panel.TFrame", padding=10)
        self.tabs.add(frame, text="设置中心")
        settings_tabs = ttk.Notebook(frame)
        settings_tabs.pack(fill="both", expand=True)
        self._build_watchlist_settings(settings_tabs)
        self._build_runtime_settings(settings_tabs)
        self._build_report_llm_settings(settings_tabs)
        self._build_env_settings(settings_tabs)

    def _build_watchlist_settings(self, parent: ttk.Notebook) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        parent.add(frame, text="自选股")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)
        left = ttk.Frame(frame, style="Panel.TFrame", width=240)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.pack_propagate(False)
        columns = ("名称", "代码", "市场", "持仓")
        self.config_stock_tree = make_tree(left, columns)
        self.config_stock_tree.bind("<<TreeviewSelect>>", self.on_config_stock_select)
        buttons = ttk.Frame(left, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="新增", command=self.new_stock_form).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="删除", command=self.delete_selected_stock).pack(side="left", padx=6)

        form_area = ttk.Frame(frame, style="Panel.TFrame")
        form_area.grid(row=0, column=1, sticky="nsew")
        form = make_scrollable_frame(form_area, padding=(12, 0))
        self.stock_vars = {
            "code": tk.StringVar(),
            "name": tk.StringVar(),
            "market": tk.StringVar(value="A股"),
            "themes": tk.StringVar(),
            "holding": tk.BooleanVar(value=False),
            "cost": tk.StringVar(),
            "shares": tk.StringVar(),
            "support": tk.StringVar(),
            "resistance": tk.StringVar(),
            "stop_watch": tk.StringVar(),
        }
        ttk.Label(form, text="代码", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=5, padx=(0, 10))
        code_row = ttk.Frame(form, style="Panel.TFrame")
        code_row.grid(row=0, column=1, sticky="ew", pady=5)
        self.stock_code_entry = ttk.Entry(code_row, textvariable=self.stock_vars["code"], font=FONT_SMALL, width=22)
        self.stock_code_entry.pack(side="left", fill="x", expand=True)
        self.autofill_button = ttk.Button(code_row, text="补全", style="Compact.TButton", width=6, command=self.autofill_stock_profile)
        self.autofill_button.pack(side="left", padx=(8, 0))
        add_labeled_entry(form, "名称", self.stock_vars["name"], 1)
        ttk.Label(form, text="市场", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=5)
        market = ttk.Combobox(form, textvariable=self.stock_vars["market"], values=["A股", "ETF", "基金", "港股", "美股"], state="readonly", font=FONT_SMALL)
        market.grid(row=2, column=1, sticky="ew", pady=5)
        add_labeled_entry(form, "主题，逗号分隔", self.stock_vars["themes"], 3)
        ttk.Checkbutton(form, text="当前持仓", variable=self.stock_vars["holding"]).grid(row=4, column=1, sticky="w", pady=5)
        add_labeled_entry(form, "成本价", self.stock_vars["cost"], 5)
        add_labeled_entry(form, "股数/份额", self.stock_vars["shares"], 6)
        add_labeled_entry(form, "支撑位，逗号分隔", self.stock_vars["support"], 7)
        add_labeled_entry(form, "压力位，逗号分隔", self.stock_vars["resistance"], 8)
        add_labeled_entry(form, "风控观察位", self.stock_vars["stop_watch"], 9)
        ttk.Label(form, text="备注", style="Panel.TLabel").grid(row=10, column=0, sticky="nw", pady=5)
        self.stock_notes = tk.Text(form, height=5, width=28, wrap="word", bd=1, relief="solid", font=FONT_SMALL)
        self.stock_notes.grid(row=10, column=1, sticky="nsew", pady=5)
        ttk.Button(form, text="保存自选股", style="Primary.TButton", command=self.save_stock_form).grid(row=11, column=1, sticky="w", pady=(12, 0))
        form.columnconfigure(0, minsize=132)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(1, minsize=220)
        form.rowconfigure(10, weight=1)

    def _build_runtime_settings(self, parent: ttk.Notebook) -> None:
        outer = ttk.Frame(parent, style="Panel.TFrame")
        parent.add(outer, text="运行/数据源")
        frame = make_scrollable_frame(outer, padding=(16, 16))
        self.setting_vars = {
            "scheduler_enabled": tk.BooleanVar(value=True),
            "timezone": tk.StringVar(value="Asia/Shanghai"),
            "times": tk.StringVar(value="08:30, 12:30, 16:00"),
            "news_akshare": tk.BooleanVar(value=True),
            "news_max": tk.StringVar(value="8"),
            "news_search_fallback": tk.BooleanVar(value=True),
            "news_search_provider": tk.StringVar(value="bing,google"),
            "news_min_before_search": tk.StringVar(value="2"),
            "news_sector_enabled": tk.BooleanVar(value=True),
            "news_sector_max": tk.StringVar(value="4"),
            "report_output_dir": tk.StringVar(value=""),
            "llm_enabled": tk.BooleanVar(value=False),
        }
        ttk.Label(frame, text="定时任务", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(frame, text="启用定时任务", variable=self.setting_vars["scheduler_enabled"]).grid(row=1, column=1, sticky="w", pady=5)
        add_labeled_entry(frame, "时区", self.setting_vars["timezone"], 2)
        add_labeled_entry(frame, "运行时间，逗号分隔", self.setting_vars["times"], 3)
        ttk.Label(frame, text="新闻数据", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).grid(row=4, column=0, columnspan=2, sticky="w", pady=(18, 8))
        ttk.Checkbutton(frame, text="优先使用 AKShare 东方财富个股新闻", variable=self.setting_vars["news_akshare"]).grid(row=5, column=1, sticky="w", pady=5)
        add_labeled_entry(frame, "每只股票新闻数", self.setting_vars["news_max"], 6)
        ttk.Checkbutton(frame, text="默认源不足时启用搜索引擎新闻兜底", variable=self.setting_vars["news_search_fallback"]).grid(row=7, column=1, sticky="w", pady=5)
        add_labeled_entry(frame, "搜索源，逗号分隔", self.setting_vars["news_search_provider"], 8)
        add_labeled_entry(frame, "少于几条新闻时搜索", self.setting_vars["news_min_before_search"], 9)
        ttk.Checkbutton(frame, text="抓取相关板块动态作为综合参考", variable=self.setting_vars["news_sector_enabled"]).grid(row=10, column=1, sticky="w", pady=5)
        add_labeled_entry(frame, "每只股票板块动态数", self.setting_vars["news_sector_max"], 11)
        ttk.Label(frame, text="RSS 源，每行格式：名称 | URL", style="Panel.TLabel").grid(row=12, column=0, sticky="nw", pady=5)
        self.rss_text = tk.Text(frame, height=5, wrap="word", bd=1, relief="solid", font=FONT_SMALL)
        self.rss_text.grid(row=12, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="宏观事件", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).grid(row=13, column=0, columnspan=2, sticky="w", pady=(18, 8))
        ttk.Label(frame, text="宏观 API URL，每行一个", style="Panel.TLabel").grid(row=14, column=0, sticky="nw", pady=5)
        self.macro_endpoint_text = tk.Text(frame, height=4, wrap="word", bd=1, relief="solid", font=FONT_SMALL)
        self.macro_endpoint_text.grid(row=14, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="保存运行配置", style="Primary.TButton", command=self.save_runtime_settings).grid(row=15, column=1, sticky="w", pady=(14, 0))
        frame.columnconfigure(1, weight=1)

    def _build_report_llm_settings(self, parent: ttk.Notebook) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        parent.add(frame, text="报告/大模型")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="日报", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="日报保存目录", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=5, padx=(0, 10))
        report_dir_row = ttk.Frame(frame, style="Panel.TFrame")
        report_dir_row.grid(row=1, column=1, sticky="ew", pady=5)
        report_dir_row.columnconfigure(0, weight=1)
        ttk.Entry(report_dir_row, textvariable=self.setting_vars["report_output_dir"], font=FONT_SMALL, width=38).grid(row=0, column=0, sticky="ew")
        ttk.Button(report_dir_row, text="选择目录", style="Compact.TButton", command=self.choose_report_output_dir).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(frame, text="打开当前报告目录", command=self.open_report_directory).grid(row=2, column=1, sticky="w", pady=(4, 12))

        ttk.Label(frame, text="大模型分析", style="Panel.TLabel", font=("PingFang SC", 16, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 8))
        ttk.Checkbutton(frame, text="启用 API 大模型分析日报", variable=self.setting_vars["llm_enabled"]).grid(row=4, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="预设提示词", style="Panel.TLabel").grid(row=5, column=0, sticky="nw", pady=5)
        self.llm_prompt_text = tk.Text(frame, height=14, wrap="word", bd=1, relief="solid", font=FONT_SMALL)
        self.llm_prompt_text.grid(row=5, column=1, sticky="nsew", pady=5)
        ttk.Button(frame, text="保存报告配置", style="Primary.TButton", command=self.save_runtime_settings).grid(row=6, column=1, sticky="w", pady=(14, 0))
        frame.rowconfigure(5, weight=1)

    def _build_env_settings(self, parent: ttk.Notebook) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        parent.add(frame, text="邮件/推送/API")
        keys = [
            ("LLM_API_KEY", "大模型 API Key"),
            ("LLM_BASE_URL", "大模型 Base URL"),
            ("LLM_MODEL", "大模型模型名"),
            ("SMTP_HOST", "SMTP服务器"),
            ("SMTP_PORT", "SMTP端口"),
            ("SMTP_USER", "SMTP用户名"),
            ("SMTP_PASSWORD", "SMTP密码/授权码"),
            ("SMTP_FROM", "发件人"),
            ("SMTP_TO", "收件人"),
            ("SMTP_USE_SSL", "使用SSL true/false"),
            ("NEWS_API_KEY", "新闻API Key"),
            ("ALPHA_VANTAGE_API_KEY", "Alpha Vantage API Key"),
            ("FMP_API_KEY", "Financial Modeling Prep API Key"),
            ("FINNHUB_API_KEY", "Finnhub API Key"),
            ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token"),
            ("TELEGRAM_CHAT_ID", "Telegram Chat ID"),
            ("WECHAT_WEBHOOK_URL", "企业微信 Webhook"),
            ("FEISHU_WEBHOOK_URL", "飞书 Webhook"),
        ]
        self.env_vars = {}
        for row, (key, label) in enumerate(keys):
            self.env_vars[key] = tk.StringVar(value=self.env_values.get(key, ""))
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            show = "*" if key in {"SMTP_PASSWORD", "LLM_API_KEY"} else ""
            entry = ttk.Entry(frame, textvariable=self.env_vars[key], font=FONT_SMALL, show=show)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="保存 API/邮件/推送配置", style="Primary.TButton", command=self.save_env_settings).grid(row=len(keys), column=1, sticky="e", pady=(14, 0))
        frame.columnconfigure(1, weight=1)

    def refresh_all(self, reload_forms: bool = False) -> None:
        if self._closing:
            return
        if self.refresh_in_progress:
            self.status_var.set("正在刷新，稍等一下...")
            return
        self.refresh_in_progress = True
        self.refresh_button.configure(state="disabled")
        self.status_var.set("正在后台刷新数据...")
        thread = threading.Thread(target=self._refresh_worker, args=(reload_forms,), daemon=True)
        thread.start()

    def _refresh_worker(self, reload_forms: bool) -> None:
        try:
            init_db()
            config = load_config_safe()
            sync_stocks(config.get("stocks", []))
            snapshot = {
                "reload_forms": reload_forms,
                "config": config,
                "env_values": load_env_values() if reload_forms else None,
                "stocks": fetch_rows(STOCKS_QUERY),
                "news": fetch_rows(NEWS_QUERY),
                "reports": fetch_rows("SELECT path, created_at FROM reports ORDER BY id DESC LIMIT 50"),
                "trend": fetch_rows(TREND_QUERY),
            }
            self.worker_queue.put(("refresh_ok", snapshot))
        except Exception as exc:
            self.worker_queue.put(("refresh_error", exc))

    def _apply_refresh_snapshot(self, snapshot: dict[str, object]) -> None:
        self.config = snapshot["config"]
        if snapshot.get("env_values") is not None:
            self.env_values = snapshot["env_values"]
        if snapshot.get("reload_forms"):
            self.load_settings_into_forms()
        stocks = snapshot["stocks"]
        reports = snapshot["reports"]
        self._snapshot_cache = snapshot
        self._fill_stocks(stocks)
        self._draw_risk_chart(stocks)
        self._fill_risk_text(stocks)
        self._fill_kpis(stocks, reports)
        self._dirty_tabs.update({"新闻雷达", "日报预览", "趋势对比"})
        self.after_idle(self.refresh_visible_tab_from_cache)
        self.status_var.set(f"已刷新：{len(stocks)} 只股票，{len(snapshot['news'])} 条新闻，{len(reports)} 份报告")

    def on_main_tab_changed(self, _event: tk.Event) -> None:
        if self._closing:
            return
        self.after_idle(self.refresh_visible_tab_from_cache)

    def refresh_visible_tab_from_cache(self) -> None:
        if self._closing or not self._snapshot_cache:
            return
        selected = self.tabs.tab(self.tabs.select(), "text")
        if selected not in self._dirty_tabs:
            return
        if selected == "新闻雷达":
            self._fill_news(self._snapshot_cache.get("news", []))
        elif selected == "日报预览":
            self._fill_reports(self._snapshot_cache.get("reports", []))
        elif selected == "趋势对比":
            self._fill_trends(self._snapshot_cache.get("trend", []))
        self._dirty_tabs.discard(selected)

    def load_settings_into_forms(self) -> None:
        self.fill_config_stock_tree()
        scheduler = self.config.get("scheduler", {})
        news = self.config.get("news", {})
        macro = self.config.get("macro", {})
        reports = self.config.get("reports", {})
        llm = self.config.get("llm", {})
        if self.setting_vars:
            self.setting_vars["scheduler_enabled"].set(bool(scheduler.get("enabled", True)))
            self.setting_vars["timezone"].set(str(scheduler.get("timezone", "Asia/Shanghai")))
            self.setting_vars["times"].set(", ".join(scheduler.get("times", ["08:30", "12:30", "16:00"])))
            self.setting_vars["news_akshare"].set(bool(news.get("use_akshare_stock_news", True)))
            self.setting_vars["news_max"].set(str(news.get("max_items_per_stock", 8)))
            self.setting_vars["news_search_fallback"].set(bool(news.get("enable_search_fallback", True)))
            self.setting_vars["news_search_provider"].set(str(news.get("search_provider", "bing,google")))
            self.setting_vars["news_min_before_search"].set(str(news.get("min_items_before_search", 2)))
            self.setting_vars["news_sector_enabled"].set(bool(news.get("enable_sector_news", True)))
            self.setting_vars["news_sector_max"].set(str(news.get("max_sector_items_per_stock", 4)))
            self.setting_vars["report_output_dir"].set(str(reports.get("output_dir", "")))
            self.setting_vars["llm_enabled"].set(bool(llm.get("enabled", False)))
        if hasattr(self, "rss_text"):
            self.rss_text.delete("1.0", "end")
            for feed in news.get("rss_feeds", []) or []:
                self.rss_text.insert("end", f"{feed.get('name', '')} | {feed.get('url', '')}\n")
        if hasattr(self, "macro_endpoint_text"):
            self.macro_endpoint_text.delete("1.0", "end")
            for endpoint in macro.get("endpoints", []) or []:
                self.macro_endpoint_text.insert("end", f"{endpoint}\n")
        if hasattr(self, "llm_prompt_text"):
            self.llm_prompt_text.delete("1.0", "end")
            self.llm_prompt_text.insert("1.0", str(llm.get("prompt_template") or DEFAULT_LLM_PROMPT))
        if self.env_vars:
            self.env_values = load_env_values()
            for key, var in self.env_vars.items():
                var.set(self.env_values.get(key, ""))

    def fill_config_stock_tree(self) -> None:
        if not hasattr(self, "config_stock_tree"):
            return
        clear_tree(self.config_stock_tree)
        for index, stock in enumerate(self.config.get("stocks", [])):
            self.config_stock_tree.insert("", "end", iid=str(index), values=(stock.get("name", ""), stock.get("code", ""), stock.get("market", ""), "是" if stock.get("holding") else "否"))

    def on_config_stock_select(self, _event: tk.Event) -> None:
        selected = self.config_stock_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        stocks = self.config.get("stocks", [])
        if index >= len(stocks):
            return
        self.selected_stock_index = index
        self.populate_stock_form(stocks[index])

    def populate_stock_form(self, stock: dict[str, object]) -> None:
        key_levels = stock.get("key_levels") or {}
        self.stock_vars["code"].set(str(stock.get("code", "")))
        self.stock_vars["name"].set(str(stock.get("name", "")))
        self.stock_vars["market"].set(str(stock.get("market", "A股")))
        self.stock_vars["themes"].set(", ".join(stock.get("themes", []) or []))
        self.stock_vars["holding"].set(bool(stock.get("holding", False)))
        self.stock_vars["cost"].set("" if stock.get("cost") is None else str(stock.get("cost")))
        self.stock_vars["shares"].set("" if stock.get("shares") is None else str(stock.get("shares")))
        self.stock_vars["support"].set(", ".join(str(item) for item in key_levels.get("support", []) or []))
        self.stock_vars["resistance"].set(", ".join(str(item) for item in key_levels.get("resistance", []) or []))
        self.stock_vars["stop_watch"].set("" if key_levels.get("stop_watch") is None else str(key_levels.get("stop_watch")))
        self.stock_notes.delete("1.0", "end")
        self.stock_notes.insert("1.0", str(stock.get("notes", "")))

    def new_stock_form(self) -> None:
        self.selected_stock_index = None
        self.populate_stock_form({"market": "A股", "themes": [], "key_levels": {}})
        self.config_stock_tree.selection_remove(self.config_stock_tree.selection())

    def stock_form_to_dict(self) -> dict[str, object]:
        code = self.stock_vars["code"].get().strip()
        name = self.stock_vars["name"].get().strip()
        if not code:
            raise ValueError("代码不能为空。")
        return {
            "code": code,
            "name": name,
            "market": self.stock_vars["market"].get().strip() or "A股",
            "themes": split_text_list(self.stock_vars["themes"].get()),
            "holding": bool(self.stock_vars["holding"].get()),
            "cost": parse_optional_float(self.stock_vars["cost"].get()),
            "shares": parse_optional_float(self.stock_vars["shares"].get()),
            "key_levels": {
                "support": parse_float_list(self.stock_vars["support"].get()),
                "resistance": parse_float_list(self.stock_vars["resistance"].get()),
                "stop_watch": parse_optional_float(self.stock_vars["stop_watch"].get()),
            },
            "notes": self.stock_notes.get("1.0", "end").strip(),
        }

    def save_stock_form(self) -> None:
        try:
            stock = self.stock_form_to_dict()
            if not stock.get("name") or not stock.get("themes"):
                self.status_var.set("名称或主题为空，正在按代码自动补全后保存...")
                self.autofill_button.configure(state="disabled")
                thread = threading.Thread(target=self._profile_worker, args=(str(stock["code"]), True), daemon=True)
                thread.start()
                return
            self._save_stock_dict(stock)
            self.status_var.set("自选股配置已保存。")
        except Exception as exc:
            self.status_var.set(f"保存失败：{exc}")

    def _save_stock_dict(self, stock: dict[str, object]) -> None:
        stocks = list(self.config.get("stocks", []))
        if self.selected_stock_index is None:
            existing = next((i for i, item in enumerate(stocks) if item.get("code") == stock["code"]), None)
            if existing is None:
                stocks.append(stock)
                self.selected_stock_index = len(stocks) - 1
            else:
                stocks[existing] = stock
                self.selected_stock_index = existing
        else:
            stocks[self.selected_stock_index] = stock
        self.config["stocks"] = stocks
        save_watchlist(self.config)
        sync_stocks(stocks)
        self.refresh_all(reload_forms=True)

    def autofill_stock_profile(self) -> None:
        code = self.stock_vars["code"].get().strip()
        if not code:
            self.status_var.set("请先填写股票代码。")
            return
        self.status_var.set(f"正在按代码 {code} 自动补全名称和主题...")
        self.autofill_button.configure(state="disabled")
        thread = threading.Thread(target=self._profile_worker, args=(code, False), daemon=True)
        thread.start()

    def _profile_worker(self, code: str, save_after: bool) -> None:
        try:
            profile = lookup_stock_profile(code)
            self.worker_queue.put(("profile_ok", {"profile": profile, "save_after": save_after}))
        except Exception as exc:
            self.worker_queue.put(("profile_error", {"error": exc, "save_after": save_after}))

    def _apply_stock_profile(self, profile: dict[str, object], save_after: bool = False) -> None:
        name = str(profile.get("name") or "").strip()
        themes = profile.get("themes") or []
        if name and not self.stock_vars["name"].get().strip():
            self.stock_vars["name"].set(name)
        if themes and not split_text_list(self.stock_vars["themes"].get()):
            self.stock_vars["themes"].set(", ".join(str(item) for item in themes if str(item).strip()))
        market = str(profile.get("market") or "").strip()
        if market and self.stock_vars["market"].get() in {"", "A股"}:
            self.stock_vars["market"].set(market)
        self.autofill_button.configure(state="normal")
        if save_after:
            stock = self.stock_form_to_dict()
            if not stock.get("name"):
                raise ValueError("自动补全没有取得股票名称，请手动填写名称后再保存。")
            self._save_stock_dict(stock)
            self.status_var.set("已按代码自动补全并保存自选股。")
        else:
            if name or themes:
                self.status_var.set(f"自动补全完成：{name or '名称未取得'} / {', '.join(themes) if themes else '主题未取得'}")
            else:
                errors = "；".join(profile.get("errors", []) or [])
                self.status_var.set(f"自动补全未取得结果。{errors}")

    def delete_selected_stock(self) -> None:
        selected = self.config_stock_tree.selection()
        if not selected:
            self.status_var.set("请先选择要删除的股票。")
            return
        index = int(selected[0])
        stocks = list(self.config.get("stocks", []))
        if index >= len(stocks):
            return
        stock = stocks[index]
        if not messagebox.askyesno("确认删除", f"删除 {stock.get('name')}（{stock.get('code')}）？历史报告不会删除。"):
            return
        del stocks[index]
        self.config["stocks"] = stocks
        save_watchlist(self.config)
        sync_stocks(stocks)
        self.selected_stock_index = None
        self.refresh_all(reload_forms=True)

    def save_runtime_settings(self) -> None:
        try:
            self.persist_settings_from_forms()
            self.status_var.set("运行、数据源、报告和大模型配置已保存。")
        except Exception as exc:
            self.status_var.set(f"保存失败：{exc}")

    def persist_settings_from_forms(self) -> None:
        self.config["scheduler"] = {
            "enabled": bool(self.setting_vars["scheduler_enabled"].get()),
            "timezone": self.setting_vars["timezone"].get().strip() or "Asia/Shanghai",
            "times": split_text_list(self.setting_vars["times"].get()),
        }
        self.config["news"] = {
            "use_akshare_stock_news": bool(self.setting_vars["news_akshare"].get()),
            "max_items_per_stock": int(self.setting_vars["news_max"].get() or 8),
            "enable_search_fallback": bool(self.setting_vars["news_search_fallback"].get()),
            "search_provider": self.setting_vars["news_search_provider"].get().strip() or "bing,google",
            "min_items_before_search": int(self.setting_vars["news_min_before_search"].get() or 2),
            "enable_sector_news": bool(self.setting_vars["news_sector_enabled"].get()),
            "max_sector_items_per_stock": int(self.setting_vars["news_sector_max"].get() or 4),
            "rss_feeds": parse_rss_feeds(self.rss_text.get("1.0", "end")),
        }
        self.config["macro"] = {
            "endpoints": split_lines(self.macro_endpoint_text.get("1.0", "end")),
        }
        self.config["reports"] = {
            "output_dir": self.setting_vars["report_output_dir"].get().strip(),
        }
        self.config["llm"] = {
            "enabled": bool(self.setting_vars["llm_enabled"].get()),
            "prompt_template": self.llm_prompt_text.get("1.0", "end").strip() or DEFAULT_LLM_PROMPT,
        }
        save_watchlist(self.config)

    def choose_report_output_dir(self) -> None:
        initial = self.setting_vars["report_output_dir"].get().strip() or str(REPORT_DIR)
        selected = filedialog.askdirectory(initialdir=initial, title="选择日报保存目录")
        if selected:
            self.setting_vars["report_output_dir"].set(selected)

    def save_env_settings(self) -> None:
        values = {key: var.get().strip() for key, var in self.env_vars.items()}
        save_env_values(values)
        for key, value in values.items():
            os.environ[key] = value
        self.env_values = values
        self.status_var.set("API、邮件和推送配置已保存到 .env。")

    def show_settings(self) -> None:
        self.tabs.select(self.tabs.tabs()[-1])

    def open_report_directory(self) -> None:
        output_dir = str((self.config.get("reports") or {}).get("output_dir") or "").strip()
        open_path(Path(output_dir).expanduser() if output_dir else REPORT_DIR)

    def select_tab_by_text(self, label: str) -> None:
        for tab_id in self.tabs.tabs():
            if self.tabs.tab(tab_id, "text") == label:
                self.tabs.select(tab_id)
                return

    def _fill_stocks(self, rows: list[sqlite3.Row]) -> None:
        clear_tree(self.stock_tree)
        for row in rows:
            self.stock_tree.insert(
                "",
                "end",
                values=(
                    row["name"],
                    row["code"],
                    row["risk_level"],
                    int(row["risk_score"] or 0),
                    f"{float(row['relevance_avg'] or 0):.0f}",
                    format_number(row["close"]),
                    format_pct(row["change_pct"]),
                    row["updated_at"] or "",
                ),
                tags=(risk_tag(row["risk_level"]),),
            )
        colorize_risk_rows(self.stock_tree)

    def _fill_news(self, rows: list[sqlite3.Row]) -> None:
        clear_tree(self.news_tree)
        for row in rows:
            self.news_tree.insert(
                "",
                "end",
                values=(
                    row["published_at"] or "",
                    row["stock_name"] or "",
                    row["source"] or "",
                    row["sentiment"] or "",
                    int(row["relevance_score"] or 0),
                    row["title"] or "",
                ),
            )

    def _fill_reports(self, rows: list[sqlite3.Row]) -> None:
        values = [row["path"] for row in rows if row["path"]]
        self.report_choice["values"] = values
        target = self.preferred_report_path if self.preferred_report_path in values else None
        if target:
            self.report_choice.set(target)
            self.preferred_report_path = None
            self.load_selected_report()
        elif values and (not self.report_choice.get() or self.report_choice.get() not in values):
            self.report_choice.set(values[0])
            self.report_text.delete("1.0", "end")
            self.report_text.insert("1.0", "已选中最新日报。需要查看正文时，点击“加载预览”或“浏览器可视化”。")
        elif not values:
            self.report_text.delete("1.0", "end")
            self.report_text.insert("1.0", "暂无日报。点击“立即运行雷达”生成第一份报告。")

    def _fill_trends(self, rows: list[sqlite3.Row]) -> None:
        if not hasattr(self, "trend_tree"):
            return
        clear_tree(self.trend_tree)
        for row in rows:
            self.trend_tree.insert(
                "",
                "end",
                values=(
                    row.get("created_at") or row.get("run_date") or "",
                    row.get("name") or row.get("code") or "",
                    row.get("code") or "",
                    row.get("risk_level") or "",
                    int(row.get("risk_score") or 0),
                    f"{float(row.get('relevance_avg') or 0):.0f}",
                ),
                tags=(risk_tag(row.get("risk_level", "")),),
            )
        colorize_risk_rows(self.trend_tree)
        self._draw_trend_chart(rows)

    def _fill_kpis(self, stocks: list[sqlite3.Row], reports: list[sqlite3.Row]) -> None:
        high = sum(1 for row in stocks if row["risk_level"] == "high")
        medium = sum(1 for row in stocks if row["risk_level"] == "medium")
        self.kpi_labels["stocks"].configure(text=str(len(stocks)))
        self.kpi_labels["high"].configure(text=str(high), foreground=RED)
        self.kpi_labels["medium"].configure(text=str(medium), foreground=AMBER)
        self.kpi_labels["reports"].configure(text=str(len(reports)), foreground=BLUE)

    def _draw_risk_chart(self, rows: list[sqlite3.Row]) -> None:
        canvas = self.risk_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        y = 26
        if not rows:
            canvas.create_text(18, 28, anchor="w", text="暂无评分数据", fill=MUTED, font=FONT)
            return
        for row in rows[:8]:
            name = row["name"] or row["code"]
            score = int(row["risk_score"] or 0)
            color = risk_color(row["risk_level"])
            canvas.create_text(10, y, anchor="w", text=str(name)[:12], fill=TEXT, font=FONT_SMALL)
            bar_x = 118
            bar_w = max(4, int((width - 170) * min(score, 100) / 100))
            canvas.create_rectangle(bar_x, y - 9, width - 42, y + 9, fill="#eef2f7", outline="")
            canvas.create_rectangle(bar_x, y - 9, bar_x + bar_w, y + 9, fill=color, outline="")
            canvas.create_text(width - 28, y, anchor="e", text=str(score), fill=TEXT, font=FONT_SMALL)
            y += 34

    def _fill_risk_text(self, rows: list[sqlite3.Row]) -> None:
        self.risk_text.delete("1.0", "end")
        risky = [row for row in rows if row["risk_level"] in {"medium", "high"}]
        if not risky:
            self.risk_text.insert("1.0", "暂无中高风险标的。")
            return
        lines = []
        for row in risky[:8]:
            lines.append(f"{row['name']}（{row['code']}）")
            lines.append(f"风险：{row['risk_level']} / {int(row['risk_score'] or 0)}")
            lines.append(f"涨跌幅：{format_pct(row['change_pct'])}  收盘：{format_number(row['close'])}")
            lines.append("")
        self.risk_text.insert("1.0", "\n".join(lines).strip())

    def _draw_trend_chart(self, rows: list[sqlite3.Row]) -> None:
        canvas = self.trend_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 720)
        height = max(canvas.winfo_height(), 260)
        left, right, top, bottom = 52, width - 18, 24, height - 42
        canvas.create_line(left, bottom, right, bottom, fill="#d8dee8")
        canvas.create_line(left, top, left, bottom, fill="#d8dee8")
        for score in (0, 25, 50, 75, 100):
            y = bottom - (bottom - top) * score / 100
            canvas.create_line(left, y, right, y, fill="#edf1f7")
            canvas.create_text(left - 10, y, anchor="e", text=str(score), fill=MUTED, font=FONT_SMALL)

        series: dict[str, list[dict[str, object]]] = {}
        for row in reversed(rows):
            code = str(row.get("code") or "")
            if not code:
                continue
            series.setdefault(code, []).append(row)
        if not series:
            canvas.create_text(left + 8, top + 16, anchor="w", text="暂无趋势快照。运行几次雷达后这里会显示对比趋势。", fill=MUTED, font=FONT)
            return

        colors = [BLUE, RED, AMBER, GREEN, "#7c3aed", "#0891b2", "#be123c", "#4b5563"]
        legend_x = left
        for index, (code, points) in enumerate(list(series.items())[:8]):
            points = points[-12:]
            color = colors[index % len(colors)]
            if len(points) == 1:
                x_positions = [(left + right) / 2]
            else:
                x_positions = [left + (right - left) * i / (len(points) - 1) for i in range(len(points))]
            coords = []
            for x, point in zip(x_positions, points):
                score = max(0, min(100, int(point.get("risk_score") or 0)))
                y = bottom - (bottom - top) * score / 100
                coords.extend([x, y])
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=2, smooth=True)
            name = str(points[-1].get("name") or code)[:8]
            canvas.create_rectangle(legend_x, bottom + 18, legend_x + 12, bottom + 30, fill=color, outline="")
            canvas.create_text(legend_x + 16, bottom + 24, anchor="w", text=name, fill=TEXT, font=FONT_SMALL)
            legend_x += 96

    def load_selected_report(self) -> None:
        path = Path(self.report_choice.get())
        self.report_text.delete("1.0", "end")
        if not path.exists():
            self.report_text.insert("1.0", f"日报文件不存在：{path}")
            return
        self.report_text.insert("1.0", "正在加载日报预览...")
        thread = threading.Thread(target=self._report_preview_worker, args=(path,), daemon=True)
        thread.start()

    def _report_preview_worker(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
            truncated = False
            if len(text) > REPORT_PREVIEW_LIMIT:
                text = text[:REPORT_PREVIEW_LIMIT]
                truncated = True
            self.worker_queue.put(("report_ok", {"path": path, "text": text, "truncated": truncated}))
        except Exception as exc:
            self.worker_queue.put(("report_error", exc))

    def _apply_report_preview(self, payload: dict[str, object]) -> None:
        path = Path(self.report_choice.get())
        if path != payload.get("path"):
            return
        text = str(payload.get("text", ""))
        if payload.get("truncated"):
            text += "\n\n[预览已截断。请点击“打开Markdown”或“浏览器可视化”查看完整报告。]"
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", text)

    def open_selected_report(self) -> None:
        selected = self.report_choice.get()
        if selected:
            open_path(Path(selected))

    def open_visual_report(self) -> None:
        selected = self.report_choice.get()
        if not selected:
            self.status_var.set("请先生成或选择一份日报。")
            return
        self.status_var.set("正在准备浏览器可视化报告...")
        thread = threading.Thread(target=self._visual_report_worker, args=(Path(selected),), daemon=True)
        thread.start()

    def _visual_report_worker(self, path: Path) -> None:
        try:
            self.worker_queue.put(("visual_ok", ensure_visual_report(path)))
        except Exception as exc:
            self.worker_queue.put(("visual_error", exc))

    def run_pipeline(self) -> None:
        try:
            self.persist_settings_from_forms()
        except Exception as exc:
            self.status_var.set(f"运行前保存当前设置失败：{exc}")
            return
        self.run_button.configure(state="disabled")
        self.status_var.set("正在运行信息雷达，行情接口可能需要几十秒...")
        thread = threading.Thread(target=self._run_pipeline_worker, daemon=True)
        thread.start()

    def _run_pipeline_worker(self) -> None:
        try:
            result = run_daily_pipeline(send_email=self.email_var.get())
            self.worker_queue.put(("ok", result))
        except Exception as exc:
            self.worker_queue.put(("error", exc))

    def _poll_worker_queue(self) -> None:
        if self._closing:
            return
        processed = 0
        while processed < 8:
            try:
                kind, payload = self.worker_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_worker_message(kind, payload)
            processed += 1
        if not self._closing:
            delay_ms = 80 if processed else 350
            self._poll_after_id = self.after(delay_ms, self._poll_worker_queue)

    def _handle_worker_message(self, kind: str, payload: object) -> None:
        if kind == "ok":
            self.run_button.configure(state="normal")
            result = payload if isinstance(payload, dict) else {}
            report_path = result.get("report_path")
            html_path = result.get("html_report_path")
            self.preferred_report_path = str(report_path)
            self.status_var.set(f"运行完成：{report_path}")
            self.select_tab_by_text("日报预览")
            self.report_text.delete("1.0", "end")
            self.report_text.insert(
                "1.0",
                f"日报已生成：\n{report_path}\n\n浏览器可视化报告：\n{html_path}\n\n"
                "已自动切换到日报预览。需要查看正文时，点击“加载预览”或“浏览器可视化”。",
            )
            self.after(250, self.refresh_all)
        elif kind == "refresh_ok":
            self.refresh_in_progress = False
            self.refresh_button.configure(state="normal")
            if isinstance(payload, dict):
                self._apply_refresh_snapshot(payload)
        elif kind == "refresh_error":
            self.refresh_in_progress = False
            self.refresh_button.configure(state="normal")
            self.status_var.set(f"刷新失败：{payload}")
        elif kind == "report_ok":
            if isinstance(payload, dict):
                self._apply_report_preview(payload)
        elif kind == "report_error":
            self.report_text.delete("1.0", "end")
            self.report_text.insert("1.0", f"日报读取失败：{payload}")
        elif kind == "visual_ok":
            self.status_var.set(f"已打开可视化报告：{payload}")
            open_path(payload)
        elif kind == "visual_error":
            self.status_var.set(f"打开可视化报告失败：{payload}")
        elif kind == "profile_ok":
            try:
                result = payload if isinstance(payload, dict) else {}
                self._apply_stock_profile(result["profile"], save_after=bool(result.get("save_after")))
            except Exception as exc:
                self.autofill_button.configure(state="normal")
                self.status_var.set(f"自动补全失败：{exc}")
        elif kind == "profile_error":
            self.autofill_button.configure(state="normal")
            result = payload if isinstance(payload, dict) else {}
            self.status_var.set(f"自动补全失败：{result.get('error', payload)}")
        else:
            self.run_button.configure(state="normal")
            self.status_var.set(f"运行失败：{payload}")


def make_tree(parent: ttk.Frame, columns: tuple[str, ...]) -> ttk.Treeview:
    wrapper = ttk.Frame(parent, style="Panel.TFrame")
    wrapper.pack(fill="both", expand=True)
    tree = ttk.Treeview(wrapper, columns=columns, show="headings")
    y_scroll = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
    x_scroll = ttk.Scrollbar(wrapper, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll.grid(row=1, column=0, sticky="ew")
    wrapper.columnconfigure(0, weight=1)
    wrapper.rowconfigure(0, weight=1)
    for column in columns:
        tree.heading(column, text=column)
        width = 240 if column in {"标题", "更新时间"} else 110
        if column in {"名称", "股票", "来源"}:
            width = 140
        tree.column(column, width=width, minwidth=70, anchor="w")
    return tree


def make_scrollable_frame(parent: ttk.Frame, padding: tuple[int, int] = (0, 0)) -> ttk.Frame:
    wrapper = ttk.Frame(parent, style="Panel.TFrame")
    wrapper.pack(fill="both", expand=True)
    canvas = tk.Canvas(wrapper, bg=PANEL, highlightthickness=0)
    y_scroll = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas, style="Panel.TFrame", padding=padding)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def resize_inner(_event: tk.Event) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def resize_window(event: tk.Event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    def on_mousewheel(event: tk.Event) -> str:
        if sys.platform == "darwin":
            delta = -1 if event.delta > 0 else 1
        else:
            delta = -1 * int(event.delta / 120) if event.delta else 0
        if delta:
            canvas.yview_scroll(delta, "units")
        return "break"

    def on_button_scroll(event: tk.Event) -> str:
        canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
        return "break"

    inner.bind("<Configure>", resize_inner)
    canvas.bind("<Configure>", resize_window)
    canvas.bind("<MouseWheel>", on_mousewheel)
    inner.bind("<MouseWheel>", on_mousewheel)
    canvas.bind("<Button-4>", on_button_scroll)
    canvas.bind("<Button-5>", on_button_scroll)
    inner.bind("<Button-4>", on_button_scroll)
    inner.bind("<Button-5>", on_button_scroll)
    canvas.configure(yscrollcommand=y_scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    y_scroll.pack(side="right", fill="y")
    return inner


def add_labeled_entry(parent: ttk.Frame, label: str, variable: tk.Variable, row: int) -> ttk.Entry:
    ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
    entry = ttk.Entry(parent, textvariable=variable, font=FONT_SMALL, width=32)
    entry.grid(row=row, column=1, sticky="ew", pady=5)
    return entry


def clear_tree(tree: ttk.Treeview) -> None:
    for item in tree.get_children():
        tree.delete(item)


def fetch_rows(query: str) -> list[dict[str, object]]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query).fetchall()]


def load_config_safe() -> dict[str, object]:
    try:
        return load_watchlist()
    except Exception:
        return {
            "scheduler": {"enabled": True, "timezone": "Asia/Shanghai", "times": ["08:30", "12:30", "16:00"]},
            "news": {"use_akshare_stock_news": True, "max_items_per_stock": 8, "rss_feeds": []},
            "macro": {"endpoints": []},
            "reports": {"output_dir": ""},
            "llm": {"enabled": False, "prompt_template": DEFAULT_LLM_PROMPT},
            "stocks": [],
        }


def split_text_list(text: str) -> list[str]:
    normalized = text.replace("，", ",").replace("；", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_optional_float(text: str) -> float | None:
    text = str(text).strip()
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"数字格式不正确：{text}") from exc


def parse_float_list(text: str) -> list[float]:
    values = []
    for item in split_text_list(text):
        try:
            values.append(float(item))
        except ValueError as exc:
            raise ValueError(f"价位格式不正确：{item}") from exc
    return values


def parse_rss_feeds(text: str) -> list[dict[str, str]]:
    feeds = []
    for line in split_lines(text):
        if "|" in line:
            name, url = [part.strip() for part in line.split("|", 1)]
        else:
            url = line.strip()
            name = url
        if url:
            feeds.append({"name": name or url, "url": url})
    return feeds


def load_env_values() -> dict[str, str]:
    path = ENV_PATH
    if not path.exists():
        path = ENV_EXAMPLE_PATH
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def save_env_values(values: dict[str, str]) -> None:
    ordered_keys = [
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_TO",
        "SMTP_USE_SSL",
        "NEWS_API_KEY",
        "ALPHA_VANTAGE_API_KEY",
        "FMP_API_KEY",
        "FINNHUB_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "WECHAT_WEBHOOK_URL",
        "FEISHU_WEBHOOK_URL",
    ]
    lines = [
        "# Generated by Stock Watch Assistant GUI.",
        "# API keys and passwords stay in this local .env file.",
    ]
    for key in ordered_keys:
        lines.append(f"{key}={quote_env_value(values.get(key, ''))}")
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def quote_env_value(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_number(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def format_pct(value: object) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def risk_color(level: str) -> str:
    if level == "high":
        return RED
    if level == "medium":
        return AMBER
    if level == "low":
        return GREEN
    return BLUE


def risk_tag(level: str) -> str:
    return {"high": "risk_high", "medium": "risk_medium", "low": "risk_low"}.get(level, "risk_unknown")


def colorize_risk_rows(tree: ttk.Treeview) -> None:
    tree.tag_configure("risk_high", foreground=RED)
    tree.tag_configure("risk_medium", foreground=AMBER)
    tree.tag_configure("risk_low", foreground=GREEN)
    tree.tag_configure("risk_unknown", foreground=MUTED)


def open_path(path: Path) -> None:
    try:
        subprocess.Popen(["open", str(path)])
    except Exception as exc:
        print(f"Unable to open path {path}: {exc}", file=sys.stderr)


def ensure_visual_report(markdown_path: Path) -> Path:
    html_path = markdown_path.with_suffix(".html")
    if html_path.exists():
        return html_path
    if not markdown_path.exists():
        raise FileNotFoundError(f"日报文件不存在：{markdown_path}")
    html = markdown_to_basic_html(markdown_path.read_text(encoding="utf-8"), title=markdown_path.stem)
    html_path.write_text(html, encoding="utf-8")
    return html_path


def main() -> None:
    app = StockWatchDesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
