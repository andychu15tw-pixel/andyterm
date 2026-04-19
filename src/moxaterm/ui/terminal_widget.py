"""ui/terminal_widget.py — VT100/ANSI 終端機顯示元件。

結論先寫:
    - TerminalWidget 繼承 QPlainTextEdit,實作 VT100 終端機顯示與鍵盤輸入。
    - feed(bytes) 把收到的資料傳給 AnsiTerminal 解析後更新畫面。
    - keyPressEvent 把鍵盤輸入轉成 bytes,emit data_to_send signal。
    - 不本地回顯 — 等待 echo 回來再顯示。
    - 有選取時 Ctrl+C 複製;無選取時送 0x03 (SIGINT)。

分層原則:本模組位於 ui/,可 import core/。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QFontMetrics,
    QKeyEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from moxaterm.core.ansi_parser import AnsiTerminal

__all__ = ["TerminalWidget"]

# VT100 序列對應表
_VT100_KEYS: dict[int, bytes] = {
    Qt.Key.Key_Up: b"\x1b[A",
    Qt.Key.Key_Down: b"\x1b[B",
    Qt.Key.Key_Right: b"\x1b[C",
    Qt.Key.Key_Left: b"\x1b[D",
    Qt.Key.Key_Home: b"\x1b[H",
    Qt.Key.Key_End: b"\x1b[F",
    Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~",
    Qt.Key.Key_Insert: b"\x1b[2~",
    Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_F1: b"\x1bOP",
    Qt.Key.Key_F2: b"\x1bOQ",
    Qt.Key.Key_F3: b"\x1bOR",
    Qt.Key.Key_F4: b"\x1bOS",
    Qt.Key.Key_F5: b"\x1b[15~",
    Qt.Key.Key_F6: b"\x1b[17~",
    Qt.Key.Key_F7: b"\x1b[18~",
    Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~",
    Qt.Key.Key_F10: b"\x1b[21~",
    Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
}

# ANSI 16 色 palette (標準 VT100 配色)
_ANSI_16: list[str] = [
    "#000000", "#CC0000", "#00CC00", "#CCCC00",
    "#0000EE", "#CC00CC", "#00CCCC", "#D4D4D4",
    "#808080", "#FF0000", "#00FF00", "#FFFF00",
    "#5C5CFF", "#FF00FF", "#00FFFF", "#FFFFFF",
]

_ANSI_COLOR_NAMES = {
    "black": 0, "red": 1, "green": 2, "brown": 3, "yellow": 3,
    "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
}

_DEFAULT_FG = "#D4D4D4"
_DEFAULT_BG = "#1E1E1E"


def _color_to_hex(name: str, default: str) -> str:
    """pyte 顏色名稱/hex → Qt #RRGGBB。"""
    if not name or name == "default":
        return default
    if name in _ANSI_COLOR_NAMES:
        return _ANSI_16[_ANSI_COLOR_NAMES[name]]
    if len(name) == 6 and all(c in "0123456789abcdefABCDEF" for c in name):
        return f"#{name}"
    return default


class TerminalWidget(QPlainTextEdit):
    """VT100 終端機顯示元件。

    結論:
        - 繼承 QPlainTextEdit,等寬字型,深色背景。
        - feed(bytes): 資料進來 → AnsiTerminal 解析 → 重繪畫面。
        - keyPressEvent: 鍵盤 → bytes → data_to_send signal。
        - maximumBlockCount = 10000,避免記憶體無限增長。

    Signals:
        data_to_send(bytes): 使用者鍵盤輸入的 bytes,供 worker 送出。
    """

    data_to_send = Signal(bytes)

    def __init__(self, cols: int = 80, rows: int = 24, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._terminal = AnsiTerminal(cols=cols, rows=rows)
        self._cols = cols
        self._rows = rows

        self._setup_appearance()
        self.setMaximumBlockCount(10_000)
        self.setReadOnly(False)

    # ------------------------------------------------------------------
    # 外觀設定
    # ------------------------------------------------------------------

    def _setup_appearance(self) -> None:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(11)
        self.setFont(font)

        self.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #1e1e1e;"
            "  color: #d4d4d4;"
            "  border: none;"
            "  selection-background-color: #264f78;"
            "}"
        )

        # 設定固定字元尺寸,讓外部可以計算正確的視窗大小
        fm = QFontMetrics(self.font())
        self.setMinimumSize(
            fm.horizontalAdvance("W") * self._cols,
            fm.height() * self._rows,
        )

    # ------------------------------------------------------------------
    # 資料輸入 (從 worker 來的)
    # ------------------------------------------------------------------

    @Slot(bytes)
    def feed(self, data: bytes) -> None:
        """接收 serial/SSH 資料,更新終端機狀態並重繪。"""
        self._terminal.feed(data)
        self._render()

    # ------------------------------------------------------------------
    # 畫面渲染
    # ------------------------------------------------------------------

    def _render(self) -> None:
        """從 AnsiTerminal 狀態重繪 QPlainTextEdit 內容。"""
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()

        # 清空現有內容
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.removeSelectedText()

        display = self._terminal.get_display()
        for row_idx, line in enumerate(display):
            if row_idx > 0:
                cursor.insertText("\n")
            self._render_row(cursor, row_idx, line)

        cursor.endEditBlock()

        # 把可見游標移到 terminal cursor 位置
        self._move_display_cursor()

    def _render_row(self, cursor: QTextCursor, row: int, line: str) -> None:
        """渲染單行,套用每個 cell 的顏色屬性。"""
        for col, ch in enumerate(line):
            fmt = QTextCharFormat()
            try:
                fg_name = self._terminal.get_char_fg(row, col)
                bg_name = self._terminal.get_char_bg(row, col)
                fmt.setForeground(QColor(_color_to_hex(fg_name, _DEFAULT_FG)))
                fmt.setBackground(QColor(_color_to_hex(bg_name, _DEFAULT_BG)))
            except (IndexError, KeyError):
                fmt.setForeground(QColor(_DEFAULT_FG))
                fmt.setBackground(QColor(_DEFAULT_BG))
            cursor.insertText(ch, fmt)

    def _move_display_cursor(self) -> None:
        cx = self._terminal.cursor_x
        cy = self._terminal.cursor_y
        doc_cursor = self.textCursor()
        doc_cursor.movePosition(
            QTextCursor.MoveOperation.Start,
            QTextCursor.MoveMode.MoveAnchor,
        )
        # 移到對應行
        for _ in range(cy):
            doc_cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.MoveAnchor,
            )
        # 移到對應欄
        doc_cursor.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.MoveAnchor,
            cx,
        )
        self.setTextCursor(doc_cursor)

    # ------------------------------------------------------------------
    # 鍵盤輸入
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """鍵盤輸入轉 bytes → emit data_to_send;不做本地回顯。"""
        data = self._encode_key(event)
        if data:
            self.data_to_send.emit(data)
        # 不呼叫 super(),避免 QPlainTextEdit 本地回顯

    def _encode_key(self, event: QKeyEvent) -> bytes | None:
        key = event.key()
        mods = event.modifiers()

        # Ctrl+C: 有選取時複製,否則送 SIGINT
        if key == Qt.Key.Key_C and mods & Qt.KeyboardModifier.ControlModifier:
            if self.textCursor().hasSelection():
                self.copy()
                return None
            return b"\x03"

        # Ctrl+V: 不攔截 — 讓 Qt 處理貼上 (之後貼到 data_to_send 要另外處理)
        if key == Qt.Key.Key_V and mods & Qt.KeyboardModifier.ControlModifier:
            clipboard_text = self._get_clipboard_text()
            if clipboard_text:
                return clipboard_text.encode("utf-8", errors="replace")
            return None

        # 其他 Ctrl 組合鍵 (Ctrl+A~Z)
        if mods & Qt.KeyboardModifier.ControlModifier:
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                return bytes([key - Qt.Key.Key_A + 1])
            if key == Qt.Key.Key_BracketRight:
                return b"\x1b"  # Ctrl+]
            if key == Qt.Key.Key_Backslash:
                return b"\x1c"  # Ctrl+\

        # VT100 特殊鍵
        if key in _VT100_KEYS:
            # DECCKM 模式下 arrow keys 改格式
            if key in (
                Qt.Key.Key_Up, Qt.Key.Key_Down,
                Qt.Key.Key_Left, Qt.Key.Key_Right,
            ):
                import pyte
                if pyte.modes.LNM in getattr(self._terminal._screen, "mode", set()):
                    if key == Qt.Key.Key_Up:
                        return b"\x1bOA"
                    if key == Qt.Key.Key_Down:
                        return b"\x1bOB"
                    if key == Qt.Key.Key_Right:
                        return b"\x1bOC"
                    if key == Qt.Key.Key_Left:
                        return b"\x1bOD"
            return _VT100_KEYS[key]

        # 特殊鍵
        if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            return b"\r"
        if key == Qt.Key.Key_Backspace:
            return b"\x7f"
        if key == Qt.Key.Key_Tab:
            return b"\t"
        if key == Qt.Key.Key_Escape:
            return b"\x1b"

        # 一般可列印字元
        text = event.text()
        if text:
            return text.encode("utf-8", errors="replace")

        return None

    def _get_clipboard_text(self) -> str:
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        return clipboard.text() if clipboard else ""

    # ------------------------------------------------------------------
    # 公開輔助
    # ------------------------------------------------------------------

    def resize_terminal(self, cols: int, rows: int) -> None:
        """調整終端機尺寸並重繪。"""
        self._cols = cols
        self._rows = rows
        self._terminal.resize(cols, rows)
        self._render()

    @property
    def terminal(self) -> AnsiTerminal:
        return self._terminal
