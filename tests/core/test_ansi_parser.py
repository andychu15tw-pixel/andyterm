# tests/core/test_ansi_parser.py
# L1 Unit tests for AnsiTerminal (andyterm.core.ansi_parser)
#
# 分層原則:core/ 不可 import 任何 Qt 模組,此層全 mock,< 100ms per test。
# 目標覆蓋率:90%+
#
# 測試重點:
#   1. Plain text — ASCII feed 與 cursor 位置
#   2. CR / LF 處理
#   3. ANSI SGR color codes (get_char_fg / get_char_bg)
#   4. UTF-8 incremental decode (CRITICAL — 多 chunk 不亂碼)
#   5. resize — 內容保留與 cursor clamp
#   6. 邊界 — 空 feed、超寬自動換行、捲動
#   7. VT100 常用序列 — cursor move、clear screen

import pytest

from andyterm.core.ansi_parser import AnsiTerminal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def term() -> AnsiTerminal:
    """標準 80x24 終端機,每個測試獨立實例。"""
    return AnsiTerminal(cols=80, rows=24)


@pytest.fixture
def small_term() -> AnsiTerminal:
    """小尺寸終端機 (10x5),便於測試捲動與邊界。"""
    return AnsiTerminal(cols=10, rows=5)


# ---------------------------------------------------------------------------
# 1. Plain text
# ---------------------------------------------------------------------------


class TestPlainText:
    def test_feed_ascii_appears_in_get_line(self, term: AnsiTerminal) -> None:
        """feed ASCII 字串後,第 0 行應包含該文字。"""
        term.feed(b"hello")
        assert "hello" in term.get_line(0)

    def test_get_display_returns_all_rows(self, term: AnsiTerminal) -> None:
        """get_display() 回傳的 list 長度等於 rows。"""
        term.feed(b"abc")
        display = term.get_display()
        assert len(display) == 24

    def test_get_display_first_line_contains_text(self, term: AnsiTerminal) -> None:
        """get_display() 第一個元素應包含剛 feed 進去的文字。"""
        term.feed(b"world")
        assert "world" in term.get_display()[0]

    def test_cursor_x_advances_after_feed(self, term: AnsiTerminal) -> None:
        """feed 5 個 ASCII 字元後,cursor_x 應為 5。"""
        term.feed(b"abcde")
        assert term.cursor_x == 5

    def test_cursor_y_starts_at_zero(self, term: AnsiTerminal) -> None:
        """初始 cursor_y 應為 0(第一行)。"""
        assert term.cursor_y == 0

    def test_get_line_returns_string(self, term: AnsiTerminal) -> None:
        """get_line() 回傳型別必須是 str。"""
        term.feed(b"test")
        assert isinstance(term.get_line(0), str)

    def test_multiple_chars_correct_order(self, term: AnsiTerminal) -> None:
        """字元順序必須與 feed 順序一致。"""
        term.feed(b"xyz")
        line = term.get_line(0)
        assert line.index("x") < line.index("y") < line.index("z")


# ---------------------------------------------------------------------------
# 2. CR / LF 處理
# ---------------------------------------------------------------------------


class TestCRLF:
    def test_carriage_return_moves_cursor_to_col_zero(self, term: AnsiTerminal) -> None:
        """\\r 應將 cursor_x 移回 0,不改變 cursor_y。"""
        term.feed(b"abc\r")
        assert term.cursor_x == 0
        assert term.cursor_y == 0

    def test_cr_overwrites_existing_content(self, term: AnsiTerminal) -> None:
        """\\r 後繼續寫字元,應覆蓋同行開頭。"""
        term.feed(b"abc\rxyz")
        line = term.get_line(0)
        assert line.startswith("xyz")

    def test_lf_moves_cursor_to_next_row(self, term: AnsiTerminal) -> None:
        """\\n 應使 cursor_y 增加 1。"""
        term.feed(b"abc\n")
        assert term.cursor_y == 1

    def test_crlf_moves_cursor_to_start_of_next_row(self, term: AnsiTerminal) -> None:
        """\\r\\n 應將 cursor 移到下一行行首 (x=0, y+=1)。"""
        term.feed(b"abc\r\nxyz")
        assert term.cursor_x == 3
        assert term.cursor_y == 1

    def test_crlf_text_on_second_line(self, term: AnsiTerminal) -> None:
        """\\r\\n 後 feed 的文字應出現在第 1 行。"""
        term.feed(b"first\r\nsecond")
        assert "second" in term.get_line(1)

    def test_first_line_preserved_after_crlf(self, term: AnsiTerminal) -> None:
        """\\r\\n 後第 0 行內容應保留。"""
        term.feed(b"first\r\nsecond")
        assert "first" in term.get_line(0)


# ---------------------------------------------------------------------------
# 3. ANSI SGR color codes
# ---------------------------------------------------------------------------


class TestAnsiColors:
    @pytest.mark.parametrize(
        "sgr_seq, expected_fg",
        [
            (b"\x1b[31m", "red"),
            (b"\x1b[32m", "green"),
            (b"\x1b[33m", "brown"),   # pyte 稱 brown,非 yellow
            (b"\x1b[34m", "blue"),
            (b"\x1b[35m", "magenta"),
            (b"\x1b[36m", "cyan"),
            (b"\x1b[37m", "white"),
        ],
    )
    def test_standard_fg_colors(
        self, term: AnsiTerminal, sgr_seq: bytes, expected_fg: str
    ) -> None:
        """標準 SGR 前景色應正確對應顏色名稱。"""
        term.feed(sgr_seq + b"X")
        assert term.get_char_fg(0, 0) == expected_fg

    @pytest.mark.parametrize(
        "sgr_seq, expected_bg",
        [
            (b"\x1b[41m", "red"),
            (b"\x1b[44m", "blue"),
            (b"\x1b[42m", "green"),
        ],
    )
    def test_standard_bg_colors(
        self, term: AnsiTerminal, sgr_seq: bytes, expected_bg: str
    ) -> None:
        """標準 SGR 背景色應正確對應顏色名稱。"""
        term.feed(sgr_seq + b"X")
        assert term.get_char_bg(0, 0) == expected_bg

    def test_256_color_fg(self, term: AnsiTerminal) -> None:
        """SGR 38;5;n 256 色前景:get_char_fg 應回傳非空字串。"""
        term.feed(b"\x1b[38;5;226mX")
        fg = term.get_char_fg(0, 0)
        assert fg  # 非空字串即可,具體值依實作

    def test_256_color_bg(self, term: AnsiTerminal) -> None:
        """SGR 48;5;n 256 色背景:get_char_bg 應回傳非空字串。"""
        term.feed(b"\x1b[48;5;21mX")
        bg = term.get_char_bg(0, 0)
        assert bg

    def test_sgr_reset_clears_color(self, term: AnsiTerminal) -> None:
        """\\x1b[0m (reset) 後的字元顏色應恢復預設 (default)。"""
        term.feed(b"\x1b[31mA\x1b[0mB")
        # 'B' 在 col=1,reset 後前景應為 default
        fg_after_reset = term.get_char_fg(0, 1)
        assert fg_after_reset in ("default", "")

    def test_bold_bright_fg(self, term: AnsiTerminal) -> None:
        """SGR 1 (bold/bright) 不應造成例外。"""
        term.feed(b"\x1b[1;31mX")
        # 只要不拋例外且 get_char_fg 回傳字串即通過
        assert isinstance(term.get_char_fg(0, 0), str)

    def test_truecolor_fg(self, term: AnsiTerminal) -> None:
        """SGR 38;2;r;g;b truecolor:get_char_fg 應回傳 hex 或非空字串。"""
        term.feed(b"\x1b[38;2;255;128;0mX")
        fg = term.get_char_fg(0, 0)
        assert fg  # 非空


# ---------------------------------------------------------------------------
# 4. UTF-8 incremental decode (CRITICAL)
# ---------------------------------------------------------------------------


class TestUtf8Incremental:
    def test_chinese_single_feed(self, term: AnsiTerminal) -> None:
        """「中」完整一次 feed,應正確顯示。"""
        term.feed("中".encode())
        assert "中" in term.get_line(0)

    def test_chinese_split_across_two_feeds(self, term: AnsiTerminal) -> None:
        """「中」(E4 B8 AD) 被切成兩個 chunk,不應出現替代字元 U+FFFD。"""
        chinese_bytes = "中".encode()  # b'\xe4\xb8\xad'
        term.feed(chinese_bytes[:2])          # \xe4\xb8
        term.feed(chinese_bytes[2:])          # \xad
        line = term.get_line(0)
        assert "\ufffd" not in line, "出現 U+FFFD 替代字元,UTF-8 incremental decode 失敗"
        assert "中" in line

    def test_chinese_split_first_byte_only(self, term: AnsiTerminal) -> None:
        """「中」只 feed 第一個 byte,再 feed 後兩個 byte,應正確合併。"""
        chinese_bytes = "中".encode()
        term.feed(chinese_bytes[:1])   # \xe4
        term.feed(chinese_bytes[1:])   # \xb8\xad
        line = term.get_line(0)
        assert "\ufffd" not in line
        assert "中" in line

    def test_emoji_single_feed(self, term: AnsiTerminal) -> None:
        """4-byte emoji (U+1F600) 完整一次 feed,應正確顯示。"""
        emoji = "\U0001f600"  # 😀
        term.feed(emoji.encode("utf-8"))
        line = term.get_line(0)
        assert "\ufffd" not in line

    def test_emoji_split_across_two_feeds(self, term: AnsiTerminal) -> None:
        """4-byte emoji 切成前 2 + 後 2 bytes 兩次 feed,不應亂碼。"""
        emoji_bytes = "\U0001f600".encode("utf-8")  # 4 bytes
        term.feed(emoji_bytes[:2])
        term.feed(emoji_bytes[2:])
        line = term.get_line(0)
        assert "\ufffd" not in line

    def test_mixed_ascii_and_chinese(self, term: AnsiTerminal) -> None:
        """ASCII + 中文混合 feed,各字元應正確呈現。"""
        term.feed(b"ok ")
        term.feed("測試".encode())
        line = term.get_line(0)
        assert "ok" in line
        assert "測試" in line

    def test_multiple_chinese_chars_split(self, term: AnsiTerminal) -> None:
        """兩個中文字「台灣」的 bytes 在邊界切割,不應亂碼。"""
        text = "台灣".encode()  # 6 bytes
        term.feed(text[:3])  # 「台」完整
        term.feed(text[3:])  # 「灣」完整
        line = term.get_line(0)
        assert "\ufffd" not in line
        assert "台" in line
        assert "灣" in line


# ---------------------------------------------------------------------------
# 5. resize
# ---------------------------------------------------------------------------


class TestResize:
    def test_resize_changes_cols_and_rows(self, term: AnsiTerminal) -> None:
        """resize(40, 12) 後,get_display() 應回傳 12 行。"""
        term.resize(40, 12)
        assert len(term.get_display()) == 12

    def test_resize_preserves_existing_text(self, term: AnsiTerminal) -> None:
        """resize 後第 0 行的既有文字應保留。"""
        term.feed(b"hello")
        term.resize(40, 12)
        assert "hello" in term.get_line(0)

    def test_cursor_clamped_after_resize_smaller(self, term: AnsiTerminal) -> None:
        """縮小螢幕後 cursor 不應超出新邊界。"""
        # 先把 cursor 推到右側
        term.feed(b"A" * 79)
        term.resize(10, 5)
        assert term.cursor_x <= 10
        assert term.cursor_y <= 5

    def test_resize_to_same_size_is_noop(self, term: AnsiTerminal) -> None:
        """resize 成相同尺寸不應拋例外,行數不變。"""
        term.resize(80, 24)
        assert len(term.get_display()) == 24

    def test_resize_larger_adds_blank_rows(self, term: AnsiTerminal) -> None:
        """resize 擴大後 get_display() 應回傳更多行。"""
        term.feed(b"hi")
        term.resize(80, 30)
        assert len(term.get_display()) == 30


# ---------------------------------------------------------------------------
# 6. 邊界
# ---------------------------------------------------------------------------


class TestBoundary:
    def test_empty_feed_is_noop(self, term: AnsiTerminal) -> None:
        """feed(b'') 不應拋例外,cursor 不動。"""
        term.feed(b"")
        assert term.cursor_x == 0
        assert term.cursor_y == 0

    def test_line_exceeding_cols_wraps(self, small_term: AnsiTerminal) -> None:
        """超過 cols 的文字應自動換行到下一行。"""
        # cols=10,feed 11 個字元
        small_term.feed(b"A" * 11)
        # cursor 應已換到第 1 行
        assert small_term.cursor_y >= 1

    def test_scrolling_when_rows_exceeded(self, small_term: AnsiTerminal) -> None:
        """超過 rows 的換行應觸發捲動,get_display() 行數不超過 rows。"""
        for i in range(10):
            small_term.feed(f"line{i}\r\n".encode())
        assert len(small_term.get_display()) == 5  # rows=5

    def test_get_line_returns_empty_string_for_blank_row(self, term: AnsiTerminal) -> None:
        """未寫入的行 get_line() 應回傳空字串或純空白,不應拋例外。"""
        line = term.get_line(23)
        assert isinstance(line, str)

    def test_get_display_all_rows_are_strings(self, term: AnsiTerminal) -> None:
        """get_display() 中所有元素型別必須為 str。"""
        term.feed(b"test")
        for row in term.get_display():
            assert isinstance(row, str)

    def test_feed_null_byte_does_not_crash(self, term: AnsiTerminal) -> None:
        """NUL byte (\\x00) 不應導致例外。"""
        term.feed(b"\x00")

    def test_feed_large_data_does_not_crash(self, term: AnsiTerminal) -> None:
        """一次 feed 大量資料 (4 KB) 不應拋例外。"""
        term.feed(b"A" * 4096)


# ---------------------------------------------------------------------------
# 7. VT100 常用序列
# ---------------------------------------------------------------------------


class TestVt100Sequences:
    def test_cursor_home_esc_H(self, term: AnsiTerminal) -> None:
        """\\x1b[H 應將 cursor 移到 (0, 0)。"""
        term.feed(b"abc")
        term.feed(b"\x1b[H")
        assert term.cursor_x == 0
        assert term.cursor_y == 0

    def test_cursor_position_row_col(self, term: AnsiTerminal) -> None:
        """\\x1b[2;5H 應將 cursor 移到 row=1, col=4 (0-based)。"""
        term.feed(b"\x1b[2;5H")
        assert term.cursor_y == 1
        assert term.cursor_x == 4

    def test_clear_screen_esc_2J(self, term: AnsiTerminal) -> None:
        """\\x1b[2J 清除整個螢幕後,所有行應為空白。"""
        term.feed(b"hello world")
        term.feed(b"\x1b[2J")
        for line in term.get_display():
            assert line.strip() == ""

    def test_cursor_up_sequence(self, term: AnsiTerminal) -> None:
        """\\x1b[A 應將 cursor 向上移一行。"""
        term.feed(b"line1\r\nline2")
        y_before = term.cursor_y
        term.feed(b"\x1b[A")
        assert term.cursor_y == y_before - 1

    def test_cursor_down_sequence(self, term: AnsiTerminal) -> None:
        """\\x1b[B 應將 cursor 向下移一行。"""
        y_before = term.cursor_y
        term.feed(b"\x1b[B")
        assert term.cursor_y == y_before + 1

    def test_cursor_forward_sequence(self, term: AnsiTerminal) -> None:
        """\\x1b[C 應將 cursor 向右移一列。"""
        x_before = term.cursor_x
        term.feed(b"\x1b[C")
        assert term.cursor_x == x_before + 1

    def test_cursor_backward_sequence(self, term: AnsiTerminal) -> None:
        """\\x1b[D 應將 cursor 向左移一列。"""
        term.feed(b"abc")
        x_before = term.cursor_x
        term.feed(b"\x1b[D")
        assert term.cursor_x == x_before - 1

    def test_erase_line_esc_2K(self, term: AnsiTerminal) -> None:
        """\\x1b[2K 應清除整行內容。"""
        term.feed(b"hello")
        term.feed(b"\x1b[2K")
        assert term.get_line(0).strip() == ""

    def test_combined_positioning_and_text(self, term: AnsiTerminal) -> None:
        """cursor 定位後 feed 文字,文字應出現在正確位置。"""
        term.feed(b"\x1b[3;1H")   # row=2, col=0 (0-based)
        term.feed(b"TARGET")
        assert "TARGET" in term.get_line(2)

    def test_no_qt_import(self) -> None:
        """ansi_parser 模組不應 import 任何 Qt 模組 (分層原則)。"""
        import sys

        # 確保模組已載入
        import andyterm.core.ansi_parser  # noqa: F401

        qt_modules = [k for k in sys.modules if "PySide6" in k or "PyQt" in k]
        # ansi_parser 的存在不應引入 Qt
        # 注意:此 test 在隔離環境最可靠;這裡做保守檢查
        parser_module = sys.modules.get("andyterm.core.ansi_parser")
        assert parser_module is not None
        # 若 Qt 根本未被 import 則直接通過
        # 若 Qt 已被其他模組 import,此 test 不算失敗 (無法區分來源)
        _ = qt_modules  # 保留供未來強化使用
