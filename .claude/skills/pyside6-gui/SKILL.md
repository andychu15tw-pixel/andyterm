---
name: pyside6-gui
description: Use this skill whenever writing, reviewing, or debugging PySide6/Qt6 UI code in MoxaTerm — including QWidget subclassing, layouts (QHBoxLayout/QVBoxLayout/QSplitter/QTabWidget), custom widgets (terminal emulator, session tree, SFTP dual-pane), Qt Signal/Slot wiring, QThread vs asyncio integration with qasync, QSS (Qt Style Sheets) theming, model/view (QAbstractItemModel, QTreeView), high-DPI handling, and preventing UI thread blocking. Also trigger when user mentions "卡住", "lag", "UI freeze", "QThread", "signal", "widget", "佈局", "PySide6", "Qt".
---

# PySide6 GUI Development for MoxaTerm

## Golden Rules

1. **UI thread never blocks**. 任何 > 16ms 的操作都要丟 worker。
2. **Signal/Slot for cross-thread**. 絕不從 worker thread 直接改 QWidget 狀態。
3. **Layout over fixed geometry**. 所有視窗必須能縮放,不寫 `setGeometry(x, y, w, h)`。
4. **Model/View for lists**. Session tree、SFTP 檔案清單一律用 `QAbstractItemModel`,不用 `QTreeWidget`。
5. **Resource via Qt Resource System**. 圖示走 `.qrc`,路徑用 `:/icons/xxx.png`。

---

## Threading Model

MoxaTerm 有三條 thread:

```
┌─────────────────┐      Signal/Slot      ┌──────────────────┐
│   UI Thread     │ ◄────(queued conn)───► │  Serial Worker   │
│   (QApplication)│                        │  QThread/pyserial│
│                 │ ◄────(queued conn)───► │                  │
│                 │                        └──────────────────┘
│                 │      asyncio task      ┌──────────────────┐
│                 │ ◄────(qasync bridge)─► │  SSH/SFTP Async  │
└─────────────────┘                        │  asyncssh        │
                                           └──────────────────┘
```

### Pattern: Serial Worker (QThread)

```python
from PySide6.QtCore import QObject, QThread, Signal, Slot
import serial

class SerialWorker(QObject):
    data_received = Signal(bytes)
    error_occurred = Signal(str)
    connected = Signal()
    disconnected = Signal()

    def __init__(self, port: str, baudrate: int):
        super().__init__()
        self._port = port
        self._baudrate = baudrate
        self._serial: serial.Serial | None = None
        self._running = False

    @Slot()
    def start(self) -> None:
        try:
            self._serial = serial.Serial(
                self._port, self._baudrate, timeout=0.05
            )
            self.connected.emit()
            self._running = True
            while self._running:
                data = self._serial.read(4096)
                if data:
                    self.data_received.emit(data)
        except serial.SerialException as e:
            self.error_occurred.emit(f"Serial 錯誤 / Serial error: {e}")
        finally:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self.disconnected.emit()

    @Slot(bytes)
    def write(self, data: bytes) -> None:
        if self._serial and self._serial.is_open:
            self._serial.write(data)

    @Slot()
    def stop(self) -> None:
        self._running = False


# 使用端 (UI thread)
self._thread = QThread()
self._worker = SerialWorker("COM3", 115200)
self._worker.moveToThread(self._thread)
self._thread.started.connect(self._worker.start)
self._worker.data_received.connect(self._on_data)  # queued connection 自動處理
self._thread.start()
```

**關鍵**:
- `moveToThread` **之後**才 connect signals
- Worker 的 `@Slot` 是跨 thread 呼叫的入口
- 關閉: `stop()` → `thread.quit()` → `thread.wait(2000)`

### Pattern: asyncio + qasync (SFTP)

```python
import asyncio
import asyncssh
from qasync import asyncSlot

class SftpPanel(QWidget):
    @asyncSlot()
    async def on_download_clicked(self):
        self.progress_bar.setValue(0)
        try:
            async with asyncssh.connect(...) as conn:
                async with conn.start_sftp_client() as sftp:
                    await sftp.get(
                        remote_path,
                        local_path,
                        progress_handler=self._update_progress,
                    )
        except asyncssh.Error as e:
            self._show_error(f"下載失敗 / Download failed: {e}")
```

`@asyncSlot` 讓 signal/button click 可以直接連到 async function,qasync 會處理 event loop。

---

## Widget Design Patterns

### Terminal Widget (核心)

**不要**用 `QTextEdit`,它太慢。用 `QPlainTextEdit` + 自訂繪製,或考慮:

- **方案 A (推薦, v1)**: `QPlainTextEdit` + `pyte.Screen` 維護 terminal state
- **方案 B (v2 效能優化)**: `QAbstractScrollArea` 完全自訂繪製 character grid

```python
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtGui import QFont, QFontDatabase, QKeyEvent
import pyte

class TerminalWidget(QPlainTextEdit):
    data_to_send = Signal(bytes)

    def __init__(self, cols: int = 80, rows: int = 24):
        super().__init__()
        self.setReadOnly(False)
        # 等寬字型必備
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(11)
        self.setFont(font)
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.ByteStream(self._screen)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # 把鍵盤輸入轉成 bytes 丟給 serial/ssh
        text = event.text()
        key = event.key()
        data = self._encode_key(key, text, event.modifiers())
        if data:
            self.data_to_send.emit(data)
        # 注意:不呼叫 super(),避免本地回顯。靠 echo 回傳

    @Slot(bytes)
    def feed(self, data: bytes) -> None:
        """從 serial/ssh 收到的資料,餵給 pyte 解析後渲染。"""
        self._stream.feed(data)
        self._render()

    def _render(self) -> None:
        # 從 self._screen.display 拉出目前畫面重繪
        ...
```

### Session Tree (左側)

```python
from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt

class SessionTreeModel(QAbstractItemModel):
    """Model/View — 不要用 QTreeWidget,那是 convenience class 不好擴充。"""
    # 實作 rowCount / columnCount / data / index / parent
    ...
```

### Tabbed Sessions

```python
self.tabs = QTabWidget()
self.tabs.setTabsClosable(True)
self.tabs.setMovable(True)
self.tabs.tabCloseRequested.connect(self._close_tab)
# 中鍵關閉:安裝 eventFilter 處理 QEvent.MouseButtonRelease
```

---

## Layout Cheatsheet

| 需求 | 元件 |
|---|---|
| 水平分割可拖曳 (session tree ↔ 終端) | `QSplitter(Qt.Horizontal)` |
| 多 tab session | `QTabWidget` |
| SFTP 雙欄 | `QSplitter(Qt.Horizontal)` 包兩個 `QTreeView` |
| 狀態列 (連線狀態、傳輸速率) | `QStatusBar` + permanent widgets |
| 工具列可拖曳 | `QToolBar` |

**永遠不要**寫死像素數,用 `setStretchFactor` 或 `setSizePolicy`。

---

## High-DPI

```python
# 在 QApplication 之前
import os
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
# Qt6 預設已開,但 Windows 裡某些 driver 還是會糊
```

圖示一律用 SVG 或提供 `@2x` PNG。

---

## QSS Theming

深色主題是 terminal 工具標配。建議:

```python
# resources/dark.qss
QPlainTextEdit { background: #1e1e1e; color: #d4d4d4; border: none; }
QTabWidget::pane { border: 0; }
QTreeView { background: #252526; color: #cccccc; }
QTreeView::item:selected { background: #094771; }
```

載入:
```python
with open(":/qss/dark.qss", "r") as f:
    app.setStyleSheet(f.read())
```

---

## Common Pitfalls

| 症狀 | 原因 | 解法 |
|---|---|---|
| UI 卡住 | UI thread blocking I/O | 搬到 QThread / asyncio |
| `QObject::connect: Cannot queue arguments of type 'X'` | 自訂型別未註冊 | `qRegisterMetaType<X>()` 或用 `object` |
| 關閉 app 時 crash | thread 還在跑 | `closeEvent` 裡 stop worker + `thread.wait()` |
| Signal 連兩次 | connect 重複呼叫 | `Qt.UniqueConnection` 或在 disconnect 後再 connect |
| 字型不等寬 | 用了系統預設字型 | `QFontDatabase.systemFont(FixedFont)` |
| SFTP 下載卡 UI | paramiko 同步 I/O | 改用 asyncssh + qasync |

---

## Testing with pytest-qt

```python
import pytest
from pytestqt.qtbot import QtBot

def test_terminal_receives_data(qtbot: QtBot):
    term = TerminalWidget()
    qtbot.addWidget(term)
    with qtbot.waitSignal(term.data_to_send, timeout=1000):
        qtbot.keyClick(term, Qt.Key_A)
```
