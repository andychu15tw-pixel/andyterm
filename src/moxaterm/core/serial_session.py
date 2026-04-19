"""core/serial_session.py — 序列埠 Session 實作。

結論先寫:
    - SerialSession 持有 SerialTransport + AnsiTerminal,組合成可直接使用的
      序列埠連線物件。
    - on_data_callback 供 UI 層 (SerialWorker / QThread) 註冊;有資料時呼叫。
    - 不含 threading 邏輯 — 讀寫迴圈由 UI 層用 QThread 包起來執行,
      本類別只負責 I/O 與終端機狀態維護。
    - write() 直接轉發給 transport;read_once() 供讀取迴圈單次呼叫。

分層原則:本模組位於 core/,不得 import 任何 Qt 模組。
"""

from __future__ import annotations

from collections.abc import Callable

from moxaterm.core.ansi_parser import AnsiTerminal
from moxaterm.core.session import SerialConfig, Session, SessionConfig
from moxaterm.protocols.serial_transport import SerialTransport

__all__ = ["SerialSession"]


class SerialSession(Session):
    """序列埠連線 Session。

    結論:
        - 建構子接受 SerialConfig,內部建立 SerialTransport 與 AnsiTerminal。
        - connect() / disconnect() 代理到 transport.open() / transport.close()。
        - read_once() 讀取一批資料,餵給 AnsiTerminal 並觸發 on_data_callback。
          呼叫端 (SerialWorker) 在 QThread 中迴圈呼叫此方法。
        - write() 直接轉發給 transport。
        - terminal 屬性供 UI 讀取螢幕內容。

    參數:
        config: SerialConfig。
        on_data_callback: (data: bytes) -> None,UI 層收到資料時的回呼 (可選)。
    """

    def __init__(
        self,
        config: SerialConfig,
        on_data_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(config)
        self._transport = SerialTransport(config)
        self._terminal = AnsiTerminal(
            cols=config.cols if hasattr(config, "cols") else 80,
            rows=config.rows if hasattr(config, "rows") else 24,
        )
        self._on_data = on_data_callback

    # ------------------------------------------------------------------
    # Session ABC 實作
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._transport.is_open

    def connect(self) -> None:
        """開啟序列埠;失敗時 TransportError 自然向上傳播。"""
        self._transport.open()

    def disconnect(self) -> None:
        """關閉序列埠;若未連線則 no-op。"""
        self._transport.close()

    def write(self, data: bytes) -> None:
        """送出 bytes 到序列埠。未連線時 TransportError 向上傳播。"""
        self._transport.write(data)

    # ------------------------------------------------------------------
    # 讀取迴圈 (由 SerialWorker 在 QThread 中呼叫)
    # ------------------------------------------------------------------

    def read_once(self) -> bytes:
        """讀取一批資料 (最多 4096 bytes, 50ms timeout)。

        結論:讀到資料後:
            1. 餵給 AnsiTerminal 更新螢幕狀態。
            2. 觸發 on_data_callback (供 UI 重繪)。
        回傳原始 bytes,讓 SerialWorker 可以選擇 emit signal。
        空 bytes 不觸發 callback。

        回傳:
            讀到的原始 bytes (可能為空)。
        """
        data = self._transport.read()
        if data:
            self._terminal.feed(data)
            if self._on_data:
                self._on_data(data)
        return data

    # ------------------------------------------------------------------
    # 輔助
    # ------------------------------------------------------------------

    @property
    def terminal(self) -> AnsiTerminal:
        """回傳內部 AnsiTerminal 實例,供 UI 查詢螢幕內容。"""
        return self._terminal

    @property
    def serial_config(self) -> SerialConfig:
        """回傳 SerialConfig (型別縮窄版 config)。"""
        return self._config  # type: ignore[return-value]

    def send_break(self, duration: float = 0.25) -> None:
        """送出 break 信號 (U-Boot 中斷 / SysRq)。"""
        self._transport.send_break(duration)

    def register_callback(self, callback: Callable[[bytes], None]) -> None:
        """替換資料回呼;供 UI 層在建構後延遲綁定。"""
        self._on_data = callback

    # SerialConfig 沒有 cols/rows,給終端機一個合理預設
    _DEFAULT_COLS = 80
    _DEFAULT_ROWS = 24

    def resize_terminal(self, cols: int, rows: int) -> None:
        """調整終端機顯示尺寸 (不影響序列埠本身)。"""
        self._terminal.resize(cols, rows)

    # 讓 Session.config 的型別仍是 SessionConfig (LSP 相容)
    @property
    def config(self) -> SessionConfig:
        return self._config
