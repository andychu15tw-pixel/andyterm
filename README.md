# MoxaTerm

類似 Xshell / Xftp 的跨平台 Serial + SFTP GUI 工具,專為工業嵌入式電腦 console 存取與檔案傳輸設計。

## 特色

- 🔌 Serial console (RS-232/422/485) + SSH Terminal 統一介面
- 📁 SFTP 檔案瀏覽與傳輸 (雙欄式)
- 🏷️ Tabbed multi-session,Session Manager 樹狀管理
- 🔍 原生支援 Moxa NPort LAN discovery 與 UPort USB 識別
- 🚂 內建 Moxa 裝置 profile (V3400/V2406C/V1200/NPort/...)
- 🔒 密碼走 OS keyring,不明文儲存
- 🌏 繁中/英雙語 UI,錯誤訊息雙語顯示

## 技術棧

Python 3.11+ / PySide6 / pyserial / paramiko / asyncssh / pyte

## 快速開始

```bash
uv venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
python -m moxaterm
```

## 專案結構

```
moxaterm/
├── CLAUDE.md                # Claude Code 專案指令
├── .claude/
│   ├── skills/              # 自動觸發的 domain skills
│   │   ├── pyside6-gui/
│   │   ├── serial-terminal/
│   │   ├── sftp-client/
│   │   └── moxa-device-support/
│   └── agents/              # 專業 subagents
│       ├── protocol-engineer.md
│       ├── ui-reviewer.md
│       ├── python-reviewer.md
│       └── test-engineer.md
├── src/moxaterm/
│   ├── ui/                  # PySide6 View 層
│   ├── core/                # 業務邏輯 (無 Qt 依賴)
│   ├── protocols/           # 純 I/O 層
│   ├── moxa/                # Moxa 整合
│   └── utils/
├── tests/
│   ├── core/                # L1 unit
│   ├── integration/         # L2 integration
│   └── ui/                  # L2 pytest-qt
└── pyproject.toml
```

## Claude Code 使用

```bash
cd moxaterm
claude                       # 自動讀取 CLAUDE.md

# 呼叫特定 agent
> 用 protocol-engineer 檢查為什麼 SSH handshake 失敗
> 用 ui-reviewer review 我剛寫的 terminal_widget.py
> 用 test-engineer 幫 ansi_parser 加測試
```

Skills 會在符合描述的任務自動觸發,不需要明確呼叫。

## 授權

(待定 — PySide6 LGPL 允許商業使用,要揭露連結方式)
