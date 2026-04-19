"""AnsiTerminal — pyte 為核心的 VT100/ANSI 終端機狀態模型。

結論先寫:
    - 提供無 Qt 依賴的終端機畫面狀態。餵 raw bytes 進去,可查詢目前螢幕的
      純文字、每個 cell 的前/背景色、以及 cursor 位置。
    - UTF-8 incremental decode 由 pyte.ByteStream 內建的 IncrementalDecoder
      處理,因此多 byte 字元 (中文、emoji) 被 Serial/TCP 切成多個 chunk 時
      不會產生 U+FFFD 替代字元。
    - 所有公開方法皆為 O(rows x cols) 或更低,適合每 16 ms 呼叫一次 UI 重繪。

典型用法:
    term = AnsiTerminal(cols=80, rows=24)
    term.feed(b"\\x1b[31mhello\\x1b[0m world")
    display_lines = term.get_display()            # list[str],len == rows
    fg_of_h       = term.get_char_fg(0, 0)        # "red"
    x, y          = term.cursor_x, term.cursor_y  # 目前 cursor 位置

分層原則:本模組位於 core/,不得 import 任何 Qt 模組 (CLAUDE.md 分層規範)。
"""

from __future__ import annotations

import pyte

__all__ = ["AnsiTerminal"]


class AnsiTerminal:
    """VT100/ANSI 終端機狀態模型 (pyte wrapper)。

    結論:
        內部以 pyte.Screen 維護螢幕矩陣,pyte.ByteStream 處理位元組 → Unicode
        的 incremental decode 與 ANSI escape 序列解析。本類別只負責把 pyte 的
        原始 API 轉成對上層 UI 友善的介面。

    參數:
        cols: 螢幕欄寬 (預設 80)。
        rows: 螢幕行高 (預設 24)。
    """

    def __init__(self, cols: int = 80, rows: int = 24) -> None:
        # pyte.Screen(columns, lines) — 與我們的 (cols, rows) 參數對齊。
        self._screen: pyte.Screen = pyte.Screen(cols, rows)
        # ByteStream 預設 encoding="utf-8", errors="replace";內部以
        # codecs.getincrementaldecoder 緩衝不完整的 UTF-8 序列,直到補齊才 emit。
        self._stream: pyte.ByteStream = pyte.ByteStream(self._screen)

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """餵 raw bytes 進終端機。

        結論:空 bytes 直接 no-op (避免 pyte 不必要的呼叫);其餘交由 ByteStream
        解析 ANSI 序列並更新 Screen 狀態。呼叫端不必先做 UTF-8 邊界對齊。

        參數:
            data: 來自 Serial / SSH channel 的原始 bytes,可能含 ANSI 控制序列
                  或被切在多 byte 字元中間。
        """
        if not data:
            return
        self._stream.feed(data)

    # ------------------------------------------------------------------
    # 螢幕內容
    # ------------------------------------------------------------------

    def get_display(self) -> list[str]:
        """回傳螢幕所有行的純文字;長度恆等於目前 rows,每行已 padding 到 cols 寬。"""
        return list(self._screen.display)

    def get_line(self, row: int) -> str:
        """回傳指定行 (0-based) 的純文字。越界由 pyte 丟 IndexError。"""
        return self._screen.display[row]

    def get_char_fg(self, row: int, col: int) -> str:
        """回傳指定 cell 的前景色字串。

        結論:pyte 的顏色表示:
            - 具名色: "default", "black", "red", "green", "brown", "blue",
                      "magenta", "cyan", "white" (注意 SGR 33 在 pyte 叫 "brown")
            - 256 色 / truecolor: 6-hex 字串 (e.g. "ffff00")
        未寫入的 cell 回傳 "default"。
        """
        return self._screen.buffer[row][col].fg

    def get_char_bg(self, row: int, col: int) -> str:
        """回傳指定 cell 的背景色字串;格式同 `get_char_fg`。"""
        return self._screen.buffer[row][col].bg

    # ------------------------------------------------------------------
    # Cursor
    # ------------------------------------------------------------------

    @property
    def cursor_x(self) -> int:
        """目前 cursor 所在欄 (0-based)。"""
        return self._screen.cursor.x

    @property
    def cursor_y(self) -> int:
        """目前 cursor 所在行 (0-based)。"""
        return self._screen.cursor.y

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        """變更螢幕尺寸;既有內容由 pyte 保留或擷取,cursor 會被 clamp 到新邊界內。

        結論:pyte.Screen.resize 的原生參數順序為 (lines, columns),本方法對外
        採 (cols, rows) 以符合專案慣例並對齊建構子順序。
        pyte 不自動 clamp cursor,resize 後手動強制在新邊界內。
        """
        self._screen.resize(lines=rows, columns=cols)
        # pyte.Screen.resize 不 clamp cursor;手動確保不超出新邊界
        self._screen.cursor.x = min(self._screen.cursor.x, cols - 1)
        self._screen.cursor.y = min(self._screen.cursor.y, rows - 1)
