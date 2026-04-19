# MoxaTerm 第一週開發 Playbook

> 使用方式:
> - 每天開工前 `/clear` 清 context
> - 開 Claude Code 後先 `@docs/WEEK1_PLAYBOOK.md @CLAUDE.md` 把當天 context 帶入
> - 每個 ❝ ❞ 區塊可直接 copy-paste
> - 每個 Day 結束時 `git add . && git commit -m "Day N: ..."`

---

## Day 1 — Core Foundation (≈ 3 小時)

**目標**: 把最底層、無依賴的 `core/` 檔案立起來,奠定分層基礎。

**產出**:
- `src/moxaterm/core/__init__.py`
- `src/moxaterm/core/ansi_parser.py` — pyte wrapper
- `src/moxaterm/core/session.py` — Session abstract base + 型別定義
- `tests/core/test_ansi_parser.py`

### 1-A 建立 package skeleton

```
請依照 CLAUDE.md 的 Project Structure 章節,建立 src/moxaterm/ 下所有
package 目錄 (ui, core, protocols, moxa, utils) 與對應的 __init__.py 空檔。
tests/ 下也建 core/, integration/, ui/, conftest.py。
建完後列出 tree,等我確認再繼續。
```

✅ 確認 tree OK 後再進下一步。

### 1-B 實作 ansi_parser.py

```
請實作 src/moxaterm/core/ansi_parser.py。

需求 (conclusions-first):
- class AnsiTerminal,建構子 cols=80, rows=24
- 公開 API:
    - feed(data: bytes) -> None
    - get_display() -> list[str]         # 所有行的純文字
    - get_line(row: int) -> str
    - get_char_fg(row, col) -> str        # ANSI 顏色名或 hex
    - get_char_bg(row, col) -> str
    - cursor_x / cursor_y properties
    - resize(cols: int, rows: int) -> None
- 內部用 pyte.Screen + pyte.ByteStream
- UTF-8 incremental decode:多 byte 字元被切成兩 chunk 時不能亂碼
  (pyte.ByteStream 其實已處理,但請確認並寫測試驗證)
- 無任何 Qt 依賴
- 繁中註解,英文 identifier
- docstring conclusions-first

寫完等我 review,再叫 test-engineer 補測試。
```

### 1-C 實作 session.py

```
請實作 src/moxaterm/core/session.py,包含:

1. SessionType enum: SERIAL, SSH, SFTP, RFC2217, TCP_RAW
2. SessionConfig: pydantic BaseModel,所有 session 共用欄位
   (id, name, type, encoding, created_at, last_used_at)
3. SerialConfig(SessionConfig): port, baudrate, bytesize, parity,
   stopbits, xonxoff, rtscts, dtr_on_open, rts_on_open, newline
4. SshConfig(SessionConfig): host, port, username, auth_method
   (password/pubkey/interactive), key_path, known_hosts_path,
   cols, rows, term_type
5. Session(ABC): 抽象基類,定義 connect(), disconnect(), is_connected,
   write(data: bytes), async read events

規則:
- pydantic v2 語法
- 不含密碼欄位 (密碼走 keyring)
- 所有欄位有 default 或明確 required
- 支援 model_dump_json() 序列化

寫完列出 interface,等我確認。
```

### 1-D 生測試

```
用 test-engineer 幫 core/ansi_parser.py 與 core/session.py 生測試。
目標 coverage 90%+,重點:
- ansi_parser: plain text / CR LF / color codes / UTF-8 跨 chunk / resize
- session: pydantic 驗證 / serialization / enum 轉換

寫完跑 pytest -v --cov=moxaterm.core,目標 pass + coverage 達標。
```

### 1-E 收尾

```bash
ruff check src tests && ruff format src tests
mypy src
pytest -v
git add . && git commit -m "Day 1: core foundation (ansi_parser, session)"
```

---

## Day 2 — Protocol Layer (≈ 3 小時)

**目標**: 純 I/O 層完成,之後 core session 可以呼叫它們。

**產出**:
- `src/moxaterm/protocols/serial_transport.py`
- `src/moxaterm/protocols/ssh_transport.py`
- `tests/integration/test_serial_transport.py` (pty-based, Linux only)
- `tests/integration/test_ssh_transport.py` (asyncssh fake server)

### 2-A

```
/clear
@CLAUDE.md @docs/WEEK1_PLAYBOOK.md
今天做 Day 2。

先實作 src/moxaterm/protocols/serial_transport.py:

- class SerialTransport (非 QObject,純 Python,讓 core/ 可用):
    - 建構子吃 SerialConfig
    - open() / close() / is_open
    - read(max_bytes=4096) -> bytes  (non-blocking, 50ms timeout)
    - write(data: bytes) -> int
    - send_break(duration: float = 0.25)
    - set_control_lines(dtr: bool | None, rts: bool | None)
    - 支援 "rfc2217://" URL (pyserial 原生)
- 錯誤轉成專案自訂 TransportError (包裝 pyserial 例外)
- 雙語錯誤訊息

寫完等我 review,再做 ssh_transport.py。
```

### 2-B

```
現在實作 src/moxaterm/protocols/ssh_transport.py:

- class SshShellTransport (paramiko 同步版):
    - 吃 SshConfig + password (從 keyring 傳進來)
    - connect() / disconnect()
    - invoke_shell(cols, rows, term="xterm-256color") -> channel
    - channel 上 recv/send/resize
    - host key 策略:載入 known_hosts,missing 時 raise HostKeyMissing
      (由上層 UI 決定要不要 accept)
    - keepalive 30s

- class SftpTransport (asyncssh 非同步版):
    - async connect() / async close()
    - listdir / stat / get / put / mkdir / remove
    - get/put 支援 throttled progress callback

參考 .claude/skills/sftp-client/SKILL.md 的範例。
不實作 jump host,先留 TODO。
```

### 2-C 測試

```
用 test-engineer 生 protocols 的測試。

serial_transport: 用 pty pair (Linux-only, Windows skip),驗證 roundtrip。
ssh_transport: 用 asyncssh.create_server 起假 server,驗證 shell 回音。
sftp_transport: 同上,驗證 listdir / get / put。

測試完跑 pytest -v,必須全 pass。
```

### 2-D 收尾

```bash
pytest -v
git commit -am "Day 2: protocols (serial, ssh, sftp transports)"
```

---

## Day 3 — Core Sessions (≈ 2-3 小時)

**目標**: 把 protocols 包成 Session,加上非同步資料流 + 事件。

**產出**:
- `src/moxaterm/core/serial_session.py`
- `src/moxaterm/core/ssh_session.py`
- `src/moxaterm/core/sftp_session.py`
- `src/moxaterm/moxa/uport_info.py` — UPort 識別 (簡單,直接可用)

### 3-A

```
/clear
@CLAUDE.md @docs/WEEK1_PLAYBOOK.md

實作 core session 三個檔 + moxa/uport_info.py。

1. core/serial_session.py:
   - class SerialSession(Session):
     - 內部持有 SerialTransport + AnsiTerminal
     - 提供 on_data_callback (bytes -> None) 給 UI 層註冊
     - 提供 write(bytes),直接轉給 transport
     - 不含 threading (交給 UI 層決定用 QThread 還是 asyncio)
     - 結構上要能讓 UI 層用 QThread 包起來跑

2. core/ssh_session.py:
   - 類似 SerialSession,用 SshShellTransport
   - 額外 resize(cols, rows) 方法 → 通知 transport

3. core/sftp_session.py:
   - 包 SftpTransport,提供 async API
   - remote_cwd 狀態追蹤
   - navigate(path), list_current(), download(remote_name, local_dir)

4. moxa/uport_info.py:
   - 直接實作 .claude/skills/moxa-device-support/SKILL.md 裡的
     scan_moxa_uport() 與 MOXA_PID_MAP

分層檢查: core/ 與 moxa/ 不能 import Qt,不能 import protocols 以外
的 I/O 套件。

寫完用 python-reviewer review。
```

### 3-B

```
用 test-engineer 補測試,重點:
- SerialSession/SshSession: mock transport,驗證資料流與 callback
- moxa/uport_info: mock list_ports.comports() 回假的 Moxa 裝置
  (VID=0x110A, PID=0x1410 → UPort 1410, 4 ports)
```

### 3-C 收尾

```bash
pytest -v --cov=moxaterm --cov-report=term-missing
git commit -am "Day 3: core sessions + moxa uport info"
```

---

## Day 4 — UI Basics + Terminal Widget (≈ 4 小時, **重頭戲**)

**目標**: 能看到視窗、能跑空終端機、能點按鈕連 serial loopback。

**產出**:
- `src/moxaterm/app.py`
- `src/moxaterm/__main__.py`
- `src/moxaterm/ui/main_window.py`
- `src/moxaterm/ui/terminal_widget.py`
- `src/moxaterm/ui/workers/serial_worker.py` (QThread wrapper)

### 4-A Bootstrap

```
/clear
@CLAUDE.md @.claude/skills/pyside6-gui/SKILL.md

實作 src/moxaterm/app.py 與 __main__.py:

- app.py: create_app() 函式,設定 high-DPI、載入 QSS、註冊
  QMetaType for bytes,回傳 QApplication
- __main__.py: def main(),build MainWindow, show, app.exec()
- 用 qasync 整合 asyncio event loop (給後續 SFTP 用)

確保 python -m moxaterm 能開一個空視窗。
```

### 4-B TerminalWidget

```
實作 src/moxaterm/ui/terminal_widget.py。

參考 .claude/skills/pyside6-gui/SKILL.md 的 Terminal Widget 章節,以及
.claude/skills/serial-terminal/SKILL.md 的 Keyboard Input Encoding 章節。

關鍵:
- 繼承 QPlainTextEdit,等寬字型 (QFontDatabase.systemFont(FixedFont))
- signal: data_to_send(bytes)
- slot: feed(bytes) - 餵給內部 AnsiTerminal 後重繪
- keyPressEvent: 按鍵 → bytes,emit data_to_send
  - 一般字元用 event.text().encode()
  - Ctrl+A~Z → 0x01~0x1A
  - Arrow/Function keys → VT100 序列 (見 skill)
  - Enter/Backspace 可設定 (先 hardcode CR + DEL)
- 不本地回顯 (不呼叫 super().keyPressEvent 對文字輸入)
- 複製行為: 有選取時 Ctrl+C 複製,無選取時送 0x03
- maximumBlockCount = 10000

寫完用 ui-reviewer review。
```

### 4-C SerialWorker + 最小可用 MainWindow

```
實作:

1. src/moxaterm/ui/workers/serial_worker.py:
   - QObject,moveToThread 用
   - 接 SerialSession,在 QThread 裡跑 read loop,emit data_received signal
   - slots: start(), stop(), write(bytes)
   - 參考 pyside6-gui skill 的 Pattern: Serial Worker

2. src/moxaterm/ui/main_window.py (最小版):
   - 主視窗 + 一個 QTabWidget (空的)
   - File menu: New Serial Session (暫時 hardcode 連 /tmp/ttyV0 或 COM1)
   - 連上後開一個 tab,裡面放 TerminalWidget + SerialWorker
   - 狀態列顯示連線狀態

讓我能 python -m moxaterm,點選 New Serial Session,看到 terminal 出現。
先不做 session dialog,下一天做。
```

### 4-D 手動測試 + 收尾

Linux/macOS 手動測試:

```bash
# 在另一個 terminal 先建 pty pair
socat -d -d pty,raw,echo=0,link=/tmp/ttyV0 pty,raw,echo=0,link=/tmp/ttyV1 &
# 另一個 terminal 手動送資料到 /tmp/ttyV1
echo "Hello from fake serial" > /tmp/ttyV1
# 然後 MoxaTerm 連 /tmp/ttyV0 應該能看到訊息
```

Windows 用 com0com 類似方式。

```bash
git commit -am "Day 4: UI bootstrap + terminal widget + serial tab"
```

---

## Day 5 — Session Dialog + Session Tree (≈ 3 小時)

**目標**: 從 hardcode 進化到有像樣的「新增連線」對話框 + 左側 session 樹。

### 5-A

```
/clear
@CLAUDE.md @.claude/skills/pyside6-gui/SKILL.md @.claude/skills/moxa-device-support/SKILL.md

實作 src/moxaterm/ui/dialogs/new_session_dialog.py:

- QDialog,分頁: Serial / SSH / SFTP
- Serial tab:
    - Port: QComboBox,按鈕 [Refresh] 重新 scan,[Scan Moxa] 高亮 Moxa
      (call moxa.uport_info.scan_moxa_uport)
    - Baudrate: QComboBox (常用值 + 可輸入),預設 115200
    - 8/N/1 下拉 (預設 8 N 1)
    - Flow control checkboxes: XON/XOFF, RTS/CTS
    - Encoding: UTF-8 / Big5 / Latin-1
    - [Quick Profile] 下拉: "Moxa V1200 Console (921600)",
      "Moxa V3400 Console (115200)" 等,選了自動填欄位
- SSH tab:
    - Host / Port / Username
    - Auth: Password / Public Key (radio)
    - key path (file picker)
    - [Save password to keyring] checkbox
- 雙語 label

寫完用 ui-reviewer review。
```

### 5-B

```
實作 src/moxaterm/ui/session_tree.py + src/moxaterm/core/session_store.py:

session_store.py:
- class SessionStore,JSON 檔案儲存在:
    - Win: %APPDATA%/MoxaTerm/sessions.json
    - Linux/macOS: ~/.config/moxaterm/sessions.json
- API: add / update / remove / get / list / as_tree()
- 密碼/passphrase 不進 JSON,走 keyring
- 資料夾分組 (folder: str | None)

session_tree.py:
- QTreeView + 自訂 QAbstractItemModel (SessionTreeModel)
- 顯示資料夾 + session (以 icon 區分 serial/ssh/sftp)
- double-click session → emit session_activated(session_id)
- 右鍵 menu: Connect / Edit / Delete / Duplicate

整合到 MainWindow: 左 QSplitter 放 session_tree,右邊 tab widget。

寫完用 python-reviewer + ui-reviewer 雙審。
```

### 5-C

```
git commit -am "Day 5: session dialog + tree + persistent store"
```

---

## Day 6 — SFTP Panel (≈ 3 小時)

**目標**: SFTP 分頁能開,雙欄檔案瀏覽,能上下傳。

```
/clear
@CLAUDE.md @.claude/skills/sftp-client/SKILL.md @.claude/skills/pyside6-gui/SKILL.md

實作 src/moxaterm/ui/sftp_panel.py:

- QWidget,QSplitter(Horizontal),左右各一個檔案 view
- 左邊:本機 (QFileSystemModel + QTreeView)
- 右邊:遠端 (自訂 RemoteFileModel,asyncssh 後端)
    - 位址列 QLineEdit + [Go] / [Up] / [Refresh]
    - 檔案列表 (name / size / mtime / permissions)
- 中間:傳輸按鈕 [→] 上傳 [←] 下載
- 下方:傳輸進度列 (QProgressBar per job)

非同步用 @asyncSlot (qasync),參考 sftp-client skill。

進度回報 throttle 100ms (見 skill)。

整合進 MainWindow: session tree 裡 SFTP session 雙擊 → 開 SFTP tab。

寫完用 ui-reviewer 重點 review:
- 非同步是否正確掛到 qasync loop?
- 取消傳輸的路徑?
- 大檔案 (1GB+) 不會卡 UI?
```

```bash
git commit -am "Day 6: SFTP dual-pane file browser"
```

---

## Day 7 — Moxa 整合 + Polish (≈ 2-3 小時)

**目標**: 第一個完整可 demo 的版本,能對真實 Moxa 裝置使用。

### 7-A NPort (保守版)

```
/clear
@CLAUDE.md @.claude/skills/moxa-device-support/SKILL.md

實作 src/moxaterm/moxa/nport_discovery.py。

採取 **保守策略**:
1. 主要走 RFC2217,不自幹 NPort search protocol
2. 提供 scan_nport_by_network(subnet: str) 用 ARP + port 4001 probe 找
   可能的 NPort (有 4001 port 開就疑似)
3. 讓使用者手動輸入 NPort IP
4. Session dialog 新增 RFC2217 分頁,底層用 pyserial 的
   serial.serial_for_url("rfc2217://...")

不要做:packet-level discovery protocol (文件不穩)。
```

### 7-B Quick Connect Profiles

```
在 session_store 裡內建唯讀的 Quick Connect profiles:
- Moxa V3400 Console (Serial, 115200 8N1)
- Moxa V1200 U-Boot/Linux Console (Serial, 921600 8N1)
- Moxa V2406C Console (Serial, 115200 8N1)
- Moxa NPort RFC2217 Template

UI: File → Quick Connect 下拉菜單,選了直接開 dialog 並預填。
```

### 7-C U-Boot Prompt 偵測

```
@.claude/skills/moxa-device-support/SKILL.md 的 U-Boot Prompt 偵測章節

在 SerialSession 加一個 optional feature:
- class UBootDetector, 餵 bytes 給它,回傳偵測到的狀態
- TerminalWidget 看到 autoboot countdown → 顯示 banner
  「偵測到 U-Boot autoboot 倒數,點擊中斷?」
- 點擊 → 送任意鍵 (b"\r")
```

### 7-D 最終 polish

```
請做最後收尾:
1. 加 app icon (resources/app.svg,任意 terminal-ish icon 先佔位)
2. About dialog (版本、授權、Moxa 連結)
3. Preferences dialog (字型大小、顏色主題、滾動 buffer 行數)
4. 鍵盤快捷:
   - Ctrl+N: New Session
   - Ctrl+W: Close Tab
   - Ctrl+Tab: Next Tab
   - F11: Toggle fullscreen
5. 確保關閉視窗時所有 session 都正確 disconnect (QCloseEvent)

用 python-reviewer 做最終 review。
```

### 7-E 打包與 release

```bash
ruff check src tests
ruff format src tests
mypy src
pytest -v --cov=moxaterm --cov-report=html

pyinstaller --onefile --windowed --icon=resources/app.ico \
  --name MoxaTerm src/moxaterm/__main__.py

git tag v0.1.0-alpha
git commit -am "Day 7: Moxa integration + polish + v0.1.0-alpha"
```

---

## 指令速查卡

### Claude Code 內建
| 指令 | 功能 |
|---|---|
| `/clear` | 清空 context (換 topic 必用) |
| `/model` | 切換模型 |
| `/effort` | 切推理深度 (low/medium/high) |
| `/cost` | 查本次對話 token 花費 |
| `/agents` | 列出/管理 subagents |
| `/mcp` | 管理 MCP 伺服器 |

### 專案內語法
| 寫法 | 行為 |
|---|---|
| `@檔名` | 塞檔案進 context |
| `用 <agent-name> 做 X` | 呼叫 subagent |
| `請先列計畫等我確認` | 觸發 ask-before-acting |
| `寫完跑 pytest 確認 pass` | 讓 Claude 自己驗證 |

### 常用 agent 呼叫時機

| 場景 | agent |
|---|---|
| 剛寫完新功能想審 | python-reviewer |
| UI 寫完或改 layout | ui-reviewer |
| 要補/改測試 | test-engineer |
| SSH/Serial bug 莫名其妙 | protocol-engineer |

---

## 心態提醒

1. **一次專注一層** — 寫 UI 就不要分心改 core,反之亦然
2. **每天 /clear 2-3 次** — context 不膨脹,回答才準
3. **每個 Day 都 commit** — 爛掉可以 rollback
4. **看到 Claude 要改 > 3 個檔** — 喊停,先讓它列計畫
5. **UI 卡卡的** — 第一嫌疑犯 = blocking I/O 跑在 UI thread,叫 ui-reviewer
6. **週末做 HIL test** — 把 Moxa V1200 / UPort 接上,真正驗證

第一週預計產出 ~3500-4500 行 Python (含測試)。不要趕,品質 > 速度。
