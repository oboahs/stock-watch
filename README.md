# stock_watch_assistant

自选股自动化信息雷达 MVP。系统每天抓取自选股行情、新闻、公告和宏观事件，输出事件影响、风险等级、走势情景和关键观察价位，不给绝对买卖指令。

## 功能

- 主界面第一项维护自选股，后台持久化到 `config/watchlist.yaml`
- SQLite 存储：`data/stock_assistant.db`
- AKShare 获取 A股、ETF、基金行情；美股支持 Nasdaq Historical API、Yahoo Finance、Stooq，并可通过 Alpha Vantage、FMP、Finnhub API Key 增强兜底
- 行情接口失败时会明确标注数据缺口；历史缓存只用于趋势对照，不作为实时判断依据
- 新闻优先使用 AKShare 东方财富个股新闻，RSS/公开网页新闻作为补充；仍抓不到时不使用缓存或模拟新闻，而是输出数据缺口和替代核验方案
- pandas 计算 5/10/20/60 日均线、放量/缩量、突破/跌破
- 规则引擎输出相关性、情绪、风险分、三种走势情景
- Markdown 日报默认落盘到 `reports/generated/`，也可在图形界面自定义保存目录
- 同步生成浏览器可视化 HTML 日报；启用大模型后，页面以大模型精简 JSON 结论为基准，使用方向标签、置信度条、关键事实、风险点、价位信号和周期情景卡展示
- 可选接入 OpenAI-compatible 大模型接口，对日报逐股生成谨慎分析，并自动压缩为适合可视化的结构化摘要
- 自动记录大模型“明日重点观察”，次日会复盘是否触发、接近或仍待确认，并在可视化日报中展示
- 同一天多次运行雷达时，后续日报会对比上一份同日日报，提示价格、风险分、大模型方向和新增新闻变化
- 日报支持图形界面删除，也可设置只保留最近多少天的日报
- 趋势对比页展示近几份日报的风险分和新闻相关性变化
- 原生桌面图形界面：`python3.11 gui.py`
- 图形界面内置设置中心，可维护运行时间、新闻源、宏观源、邮件和推送 API Key
- Streamlit 网页界面
- 每天 08:30、12:30、16:00 定时运行，时间可在图形界面“设置中心”修改
- SMTP 邮件发送日报，API Key 和密码只从 `.env` 读取

## 安装

```bash
cd stock_watch_assistant
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

也可以不手动创建 `.env`，直接在图形界面“设置中心”填写 SMTP 配置；不开启邮件可以留空。

## 图形界面配置

启动图形界面后：

- “自选股设定”：主界面第一项，新增、修改、删除股票，维护主题、持仓、成本、股数、支撑位、压力位、风控观察位和备注。
- “运行/数据源”：维护定时任务、运行时间、RSS 新闻源、搜索兜底、板块动态和宏观 API URL。
- “报告/大模型”：维护日报保存目录、日报保留天数、大模型分析开关和提示词。启用后，程序会要求大模型返回精简 JSON，浏览器日报优先展示结构化结论；如果调用失败，会在报告顶部显示失败原因。
- “邮件/推送/API”：维护大模型 API、SMTP 邮件、新闻 API Key、美股增强行情 API Key，以及后续 Telegram、企业微信、飞书接口参数。
- “日报预览”：查看 Markdown 原文，点击“浏览器可视化”打开同名 HTML 报告，也可删除选中的日报。
- “趋势对比”：横向查看近几份日报中各股票风险分、相关性的变化。

这些设置会由界面写入 `config/watchlist.yaml` 和 `.env`，日常使用不需要手动编辑配置文件。`market` 当前支持：`A股`、`ETF`、`基金`、`美股`；`港股` 保留后续扩展。

## 运行

启动本地图形界面：

```bash
cd stock_watch_assistant
python3.11 gui.py
```

这个入口是 `tkinter` 原生桌面窗口，不依赖 Streamlit；可以先用它做日常操作。

macOS 也可以双击：

```bash
launcher/start_gui_macos.command
```

立即生成日报：

```bash
cd stock_watch_assistant
source .venv/bin/activate
python main.py
```

生成并发送邮件：

```bash
python main.py --send-email
```

启动网页界面：

```bash
.venv/bin/python -m streamlit run ui/app.py
```

启动定时任务：

```bash
python scheduler.py
```

测试定时任务入口但只运行一次：

```bash
python scheduler.py --run-once
```

## 打包

macOS `.app`：

```bash
./scripts/build_macos_app.sh
```

输出在 `dist/Stock Watch Assistant.app`，同时可压缩成 `dist/StockWatchAssistant-macOS-x86_64.zip`。

Windows `.exe` 需要在 Windows 环境构建：

```powershell
.\scripts\build_windows_exe.ps1
```

输出在 `dist/StockWatchAssistant/StockWatchAssistant.exe`。详细说明见 `PACKAGING.md`。

## 输出结构

日报包含：

- 今日总览
- 重大新闻
- 每只股票影响分析
- 技术面观察
- 风险提醒
- 明日重点观察价位
- 宏观事件
- 不确定性说明

每只股票分析会明确区分：

- 事实：行情、新闻、公告、宏观事件
- 推断：规则引擎根据关键词、相关性和盘面给出的影响解释
- 不确定性：数据源延迟、新闻缺口、公开接口失败等
- 观察信号：关键价位、均线、成交量、风险分变化

## 注意

本项目是投资观察辅助工具，不是自动交易系统，不连接券商，不生成绝对买卖指令。公开数据接口可能延迟或变更，重要公告和交易决策需要人工复核。
