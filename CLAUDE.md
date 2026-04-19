# AndyTerm — Serial & SFTP GUI Tool

類似 Xshell / Xftp 的跨平台 GUI 工具,專注於工業嵌入式電腦的 console 存取與檔案傳輸。主要服務對象為 Moxa FAE/AE 工程師與客戶現場除錯情境。

---

## Project Vision

| Feature | 對應 Xshell/Xftp |
|---|---|
| Serial Console (RS-232/422/485) | Xshell 的 Serial session |
| SSH Terminal | Xshell 的 SSH session |
| SFTP File Browser | Xftp |
| Tabbed multi-session | Xshell 主視窗 |
| Session tree / favorites | Xshell Session Manager |
| Moxa NPort/UPort 整合 | 加值功能,Xshell 沒有 |

---

## Tech Stack (MUST respect)

- **Python**: 3.11+ (使用 `match`、`typing.Self`、PEP 695 generics)
- **GUI**: PySide6 (Qt6 LGPL) — 不要使用 PyQt6 (GPL)
- **Serial**: `pyserial` 3.5+
- **SSH/SFTP**: `paramiko` 3.x (同步) / `asyncssh` (非同步檔案傳輸)
- **Terminal Emulation**: `pyte` (VT100/ANSI parser)
- **Async Bridge**: `qasync` (Qt event loop ↔ asyncio)
- **Packaging**: PyInstaller (主) / Nuitka (選配)
- **Lint/Type/Test**: `ruff`、`mypy --strict`、`pytest`、`pytest-qt`

> ⚠️ 在 suggesting 第三方套件前,先檢查是否已在 `pyproject.toml`。若要新增,必須先列出理由並等使用者確認。

---

## Project Structure

```
andyterm/
├── src/andyterm/
│   ├── __main__.py              # entry point
│   ├── app.py                   # QApplication bootstrap
│   ├── ui/                      # Qt widgets (View 層)
│   │   ├── main_window.py
│   │   ├── terminal_widget.py   # QPlainTextEdit + pyte
│   │   ├── sftp_panel.py        # 雙欄檔案瀏覽
│   │   ├── session_tree.py      # 左側 session 列表
│   │   └── dialogs/             # New Session、Preferences
│   ├── core/                    # 業務邏輯 (無 Qt 依賴)
│   │   ├── session.py           # Session abstract base
│   │   ├── serial_session.py
│   │   ├── ssh_session.py
│   │   ├── sftp_session.py
│   │   ├── session_store.py     # 持久化 (加密)
│   │   └── ansi_parser.py       # pyte wrapper
│   ├── protocols/               # 純協定層
│   │   ├── serial_transport.py
│   │   └── ssh_transport.py
│   ├── moxa/                    # Moxa 特殊整合
│   │   ├── nport_discovery.py   # LAN Moxa NPort 掃描
│   │   └── uport_info.py        # USB UPort 識別
│   └── utils/
│       ├── crypto.py            # 密碼加密 (keyring 優先)
│       └── logger.py
├── tests/
├── resources/                   # icons、qss、translations
├── .claude/
│   ├── skills/
│   └── agents/
├── pyproject.toml
└── README.md
```

**分層原則 (嚴格遵守)**:
1. `ui/` 只能 import `core/`,不能 import `protocols/`
2. `core/` 不可 import 任何 Qt 模組
3. `protocols/` 為純 I/O,無業務邏輯
4. 跨層通訊一律使用 Qt Signals 或 asyncio Queue

---

## Coding Conventions

### 風格
- 繁體中文註解 + 英文 identifier
- docstring 採 **conclusions-first** (結論先寫,再列參數)
- 所有 public function 必須有 type hints
- 禁用 `from X import *`
- 路徑用 `pathlib.Path`,不用 `os.path`

### 非同步規則
- Serial I/O → QThread + `QSerialPort` 或 `pyserial` 獨立 thread
- SSH/SFTP I/O → asyncio + qasync
- **絕不**在 UI thread 做 blocking I/O
- Worker thread 回 UI 一律透過 `Signal.emit`

### 錯誤處理
- 客戶現場使用,錯誤訊息**必須雙語** (繁中 + English)
- 網路/序列埠錯誤要區分 recoverable vs fatal
- 所有例外都要寫進 `~/.andyterm/logs/` (rotating)

### 安全
- 密碼優先用 `keyring` (Windows Credential Manager / macOS Keychain / Secret Service)
- keyring 失敗時 fallback 到 AES-GCM + user-derived key (`cryptography` 套件)
- **絕不**明文儲存密碼到 session file
- SSH private key 路徑可儲存,passphrase 走 keyring

---

## Common Commands

```bash
# 開發環境
uv venv && source .venv/bin/activate   # 或 .venv\Scripts\activate
uv pip install -e ".[dev]"

# 執行
python -m andyterm

# 檢查
ruff check src tests
ruff format src tests
mypy src
pytest -v --cov=andyterm

# 打包 (Windows)
pyinstaller --onefile --windowed --icon=resources/app.ico \
  --name AndyTerm src/andyterm/__main__.py

# Moxa NPort 掃描測試 (需實機)
python -m andyterm.moxa.nport_discovery
```

---

## Moxa Domain Context

Andy 是 Moxa FAE/AE,這個工具最終會在客戶現場使用,以下情境必須能處理:

1. **V3400/V2406C/V1200 console debug** — 透過 USB-to-TTL 或前面板 console port
2. **Moxa NPort (serial-over-ethernet)** — Real COM mode / TCP Server mode
3. **Moxa UPort USB adapter** — 多埠 (1250I、1410、1610 等) 識別
4. **嵌入式 Linux 登入** — Debian 11 MIL-3.x、Yocto、Ubuntu 22.04
5. **Bootloader 除錯** — U-Boot prompt 需要可以 Ctrl+C 及時中斷
6. **長時間連線** — 客戶可能連續跑 24h+ stress test,記憶體不能洩漏

常見 baud rate: 9600、115200 (最常見)、921600 (Moxa bootloader)

---

## Ask Before Acting

以下行為**必須先徵詢使用者**:
- 新增 runtime dependency (非 dev)
- 變動公開 API 簽章
- 修改資料夾結構
- 引入新的檔案格式或序列化方案
- 跨 OS 的路徑/編碼處理方式改變

---

## Skills & Agents

專案內建以下 skills (見 `.claude/skills/`) 與 agents (見 `.claude/agents/`):

**Skills** (自動觸發):
- `pyside6-gui` — PySide6 元件設計模式、signal/slot、threading
- `serial-terminal` — pyserial + pyte 終端機模擬
- `sftp-client` — paramiko/asyncssh SFTP 操作與安全
- `moxa-device-support` — Moxa NPort/UPort 整合細節

**Agents** (明確呼叫):
- `protocol-engineer` — 深度協定除錯 (serial timing、SSH handshake)
- `ui-reviewer` — UI/UX 審查與 Qt 佈局優化
- `python-reviewer` — Python 程式碼品質審查
- `test-engineer` — pytest + pytest-qt 測試產生

---

## Out of Scope (v1.0 不做)

- Telnet (安全性差,企業客戶不需要)
- RDP/VNC (與 Xshell 分工不同)
- 雲端同步 session (隱私問題)
- 巨集腳本錄製 (v2.0 再議)
