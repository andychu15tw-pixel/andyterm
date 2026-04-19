"""protocols/serial_transport.py — pyserial 序列埠傳輸層。

結論先寫:
    - 提供 SerialTransport:封裝 pyserial.Serial,暴露 open/close/read/write/
      send_break/set_control_lines 等操作介面。
    - 支援 rfc2217:// URL (pyserial 原生),讓 Moxa NPort TCP Server mode 透明使用。
    - 所有 pyserial 例外統一包裝成 TransportError,含雙語錯誤訊息。
    - 無任何 Qt 依賴;適合在 QThread 或 asyncio executor 中呼叫。

分層原則:本模組位於 protocols/,不得 import core/ 或任何 Qt 模組。
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import serial
import serial.serialutil
from serial.tools import list_ports

if TYPE_CHECKING:
    from moxaterm.core.session import SerialConfig

__all__ = ["SerialTransport", "TransportError", "list_serial_ports"]


# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------


class TransportError(OSError):
    """序列埠 / 網路傳輸層例外,包裝底層 pyserial 或 socket 錯誤。

    結論:繼承 OSError 讓上層可以用 `except OSError` 捕捉,
    同時保留原始例外於 `__cause__`。
    """


# ---------------------------------------------------------------------------
# Port 探索
# ---------------------------------------------------------------------------


def list_serial_ports() -> list[dict[str, object]]:
    """列出目前系統所有序列埠,標記 Moxa UPort 裝置。

    結論:回傳 dict list,每個 dict 含 device / description / vid / pid /
    is_moxa 欄位;Moxa VID = 0x110A。

    回傳:
        list of {"device": str, "description": str, "vid": int|None,
                 "pid": int|None, "is_moxa": bool}
    """
    results: list[dict[str, object]] = []
    for p in list_ports.comports():
        is_moxa = p.vid == 0x110A
        results.append(
            {
                "device": p.device,
                "description": p.description or "",
                "vid": p.vid,
                "pid": p.pid,
                "is_moxa": is_moxa,
            }
        )
    return results


# ---------------------------------------------------------------------------
# SerialTransport
# ---------------------------------------------------------------------------

_NEWLINE_MAP: dict[str, bytes] = {
    "CR": b"\r",
    "LF": b"\n",
    "CRLF": b"\r\n",
}


class SerialTransport:
    """pyserial 序列埠傳輸層 (非 QObject,適合任何執行緒)。

    結論:
        - 建構子只做設定存放,不開啟 port;呼叫 open() 才建立連線。
        - 支援 rfc2217:// URL — pyserial 內部自動走 TCP,對外 API 完全相同。
        - read() 使用 50ms timeout (non-blocking 語義),呼叫端需自行 loop。
        - write() 回傳實際寫出的 byte 數。
        - 所有例外統一轉成 TransportError 並附雙語訊息。

    參數:
        config: SerialConfig,含 port / baudrate / bytesize / parity 等設定。
    """

    READ_TIMEOUT = 0.05  # 50 ms — 平衡回應速度與 CPU 使用率
    WRITE_TIMEOUT = 1.0

    def __init__(self, config: SerialConfig) -> None:
        self._config = config
        self._serial: serial.Serial | None = None

    # ------------------------------------------------------------------
    # 連線管理
    # ------------------------------------------------------------------

    def open(self) -> None:
        """開啟序列埠連線。

        結論:根據 config.port 是否以 rfc2217:// 開頭,決定呼叫
        serial.serial_for_url() 或 serial.Serial()。
        DTR / RTS 依 config.dtr_on_open / rts_on_open 設定。
        """
        if self._serial and self._serial.is_open:
            return

        port = self._config.port
        kwargs = {
            "baudrate": self._config.baudrate,
            "bytesize": self._config.bytesize,
            "parity": self._config.parity,
            "stopbits": self._config.stopbits,
            "xonxoff": self._config.xonxoff,
            "rtscts": self._config.rtscts,
            "timeout": self.READ_TIMEOUT,
            "write_timeout": self.WRITE_TIMEOUT,
        }

        try:
            if port.startswith("rfc2217://"):
                self._serial = serial.serial_for_url(port, **kwargs)
            else:
                self._serial = serial.Serial(port, **kwargs)

            self._serial.dtr = self._config.dtr_on_open
            self._serial.rts = self._config.rts_on_open
        except serial.serialutil.SerialException as exc:
            raise TransportError(
                f"無法開啟序列埠 {port} / Cannot open serial port {port}: {exc}"
            ) from exc

    def close(self) -> None:
        """關閉序列埠;若未開啟則 no-op。"""
        if self._serial and self._serial.is_open:
            with contextlib.suppress(serial.serialutil.SerialException):
                self._serial.close()
        self._serial = None

    @property
    def is_open(self) -> bool:
        """回傳序列埠是否已開啟。"""
        return self._serial is not None and self._serial.is_open

    # ------------------------------------------------------------------
    # 資料傳輸
    # ------------------------------------------------------------------

    def read(self, max_bytes: int = 4096) -> bytes:
        """從序列埠讀取最多 max_bytes 個 bytes (50ms timeout)。

        結論:timeout 內無資料回 b"",不代表斷線;呼叫端需自行判斷 is_open。

        回傳:
            讀到的 bytes,可能為空。
        """
        if not self._serial or not self._serial.is_open:
            raise TransportError(
                "序列埠未開啟 / Serial port is not open"
            )
        try:
            data: bytes = self._serial.read(max_bytes)
            return data
        except serial.serialutil.SerialException as exc:
            raise TransportError(
                f"讀取序列埠失敗 / Serial read error: {exc}"
            ) from exc

    def write(self, data: bytes) -> int:
        """寫入 data 到序列埠,回傳實際寫出的 byte 數。

        回傳:
            實際寫出的 byte 數。
        """
        if not self._serial or not self._serial.is_open:
            raise TransportError(
                "序列埠未開啟 / Serial port is not open"
            )
        try:
            n = self._serial.write(data)
            return n if n is not None else len(data)
        except serial.serialutil.SerialException as exc:
            raise TransportError(
                f"寫入序列埠失敗 / Serial write error: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # 控制訊號
    # ------------------------------------------------------------------

    def send_break(self, duration: float = 0.25) -> None:
        """送出 break 信號 (嵌入式 Linux SysRq / U-Boot 中斷常用)。

        參數:
            duration: break 持續秒數,預設 250ms。
        """
        if not self._serial or not self._serial.is_open:
            raise TransportError(
                "序列埠未開啟 / Serial port is not open"
            )
        try:
            self._serial.send_break(duration=duration)
        except serial.serialutil.SerialException as exc:
            raise TransportError(
                f"送出 break 失敗 / Send break error: {exc}"
            ) from exc

    def set_control_lines(self, dtr: bool | None, rts: bool | None) -> None:
        """設定 DTR / RTS 控制線路。None 表示不改動。

        參數:
            dtr: DTR 狀態,None = 不變。
            rts: RTS 狀態,None = 不變。
        """
        if not self._serial or not self._serial.is_open:
            raise TransportError(
                "序列埠未開啟 / Serial port is not open"
            )
        if dtr is not None:
            self._serial.dtr = dtr
        if rts is not None:
            self._serial.rts = rts

    # ------------------------------------------------------------------
    # 輔助
    # ------------------------------------------------------------------

    @property
    def newline_bytes(self) -> bytes:
        """依 config.newline 回傳 Enter 鍵應送出的 bytes。"""
        return _NEWLINE_MAP.get(self._config.newline, b"\r")
