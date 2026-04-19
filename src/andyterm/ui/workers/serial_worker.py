"""ui/workers/serial_worker.py — 序列埠讀取 Worker (QThread)。

結論先寫:
    - SerialWorker 持有 SerialSession,在 QThread 中跑讀取迴圈。
    - UI thread 呼叫 write(bytes) 送資料到序列埠。
    - 讀到資料時 emit data_received(bytes)。
    - 連線/斷線狀態透過 connected / disconnected signal 通知 UI。
    - stop() 使讀取迴圈退出;之後 QThread 自然結束。

分層原則:本模組位於 ui/,可 import core/,不可 import protocols/。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QObject, Signal, Slot

from andyterm.core.serial_session import SerialSession

__all__ = ["SerialWorker"]


class SerialWorker(QObject):
    """序列埠讀取 Worker。

    結論:
        - moveToThread 到 QThread 後,start() slot 由 QThread.started 觸發。
        - read loop 以 50ms timeout 輪詢 (SerialSession.read_once 內處理)。
        - stop() 設旗標讓 loop 退出;QThread 結束後 disconnected emit。

    Signals:
        data_received(bytes): 讀到資料時觸發。
        connected(): 序列埠成功開啟後觸發。
        disconnected(): 序列埠關閉後觸發。
        error_occurred(str): 發生錯誤時觸發 (雙語訊息)。
    """

    data_received = Signal(bytes)
    connected = Signal()
    disconnected = Signal()
    error_occurred = Signal(str)

    def __init__(self, session: SerialSession) -> None:
        super().__init__()
        self._session = session
        self._running = False

        # 讓 SerialSession 把資料推進來 → 再 emit signal
        self._session.register_callback(self.data_received.emit)

    # ------------------------------------------------------------------
    # Slots (從 UI thread 跨 thread 呼叫)
    # ------------------------------------------------------------------

    @Slot()
    def start(self) -> None:
        """開啟序列埠並進入讀取迴圈。由 QThread.started signal 觸發。"""
        try:
            self._session.connect()
            self.connected.emit()
            self._running = True
            while self._running:
                self._session.read_once()
        except Exception as exc:
            self.error_occurred.emit(
                f"序列埠錯誤 / Serial error: {exc}"
            )
        finally:
            with contextlib.suppress(Exception):
                self._session.disconnect()
            self.disconnected.emit()

    @Slot()
    def stop(self) -> None:
        """通知讀取迴圈退出。"""
        self._running = False

    @Slot(bytes)
    def write(self, data: bytes) -> None:
        """送出 bytes 到序列埠。在 UI thread 呼叫,worker 接收並執行。"""
        try:
            self._session.write(data)
        except Exception as exc:
            self.error_occurred.emit(
                f"寫入失敗 / Write failed: {exc}"
            )
