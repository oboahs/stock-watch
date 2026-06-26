# 打包说明

本项目桌面版使用 `tkinter` + PyInstaller 打包。

## macOS app

在 macOS 上运行：

```bash
cd /Users/bobo/Desktop/股票投资交易/stock_watch_assistant
./scripts/build_macos_app.sh
```

输出：

- `dist/Stock Watch Assistant.app`
- `dist/StockWatchAssistant-macOS-x86_64.zip`

打包版运行数据目录：

- `~/Library/Application Support/stock_watch_assistant`

## Windows exe

PyInstaller 不能在 macOS 上可靠交叉编译 Windows `.exe`。请在 Windows 电脑或 Windows CI 上运行：

```powershell
cd stock_watch_assistant
py -3.11 --version
.\scripts\build_windows_exe.ps1
```

输出：

- `dist\StockWatchAssistant\StockWatchAssistant.exe`
- `dist\StockWatchAssistant-Windows-x86_64.zip`

打包版运行数据目录：

- `%APPDATA%\stock_watch_assistant`

### Windows 打包前准备

1. 安装 Windows 版 Python 3.11，并勾选 `Add python.exe to PATH`。
2. 打开 PowerShell，进入项目目录。
3. 不要把 `.env`、`data\stock_assistant.db`、`reports\generated` 复制给别人。
4. 运行 `.\scripts\build_windows_exe.ps1`。
5. 发送给别人时，只发送 `dist\StockWatchAssistant-Windows-x86_64.zip`。

## 注意

- `.env` 不会被打进发布包；首次启动后可在图形界面里填写 API Key、SMTP 等配置。
- 发布包内默认自选股配置来自 `packaging/default_config/watchlist.yaml`，不会打包你本机的自选股、持仓、成本价。
- Windows 打包脚本会在构建前后做隐私保护检查：禁止打包本机 `.env`，禁止打包本机 `config/watchlist.yaml`。
- 打包版不会把配置、数据库和日报写入 `.app` 或 `.exe` 内部。
