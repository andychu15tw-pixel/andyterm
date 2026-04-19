"""ui/workers/ssh_worker.py — SSH 終端機 Worker (QThread)。

結論先寫:
    - SshWorker 持有 SshSession,在 QThread 中跑讀取迴圈。
    - UI thread 呼叫 write(bytes) 送資料到 SSH channel。
    - 讀到資料時 emit data_received(bytes)。
    - host key 未知時 emit host_key_missing(str, object),供 UI 確認。

分層原則:本模組位於 ui/,可 import core/,不可 import protocols/。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QObject, Signal, Slot

from moxaterm.core.ssh_session import SshSession

__all__ = ["SshWorker"]


class SshWorker(QObject):
    """SSH 終端機讀取 Worker。

    結論:
        - moveToThread 到 QThread 後,start() slot 由 QThread.started 觸發。
        - read loop 以 non-blocking recv 輪詢。
        - stop() 設旗標讓 loop 退出。

    Signals:
        data_received(bytes): 讀到資料時觸發。
        connected(): SSH 連線成功後觸發。
        disconnected(): 斷線後觸發。
        error_occurred(str): 發生錯誤時觸發 (雙語)。
    """

    data_received = Signal(bytes)
    connected = Signal()
    disconnected = Signal()
    error_occurred = Signal(str)

    def __init__(self, session: SshSession) -> None:
        super().__init__()
        self._session = session
        self._running = False

        self._session.register_callback(self.data_received.emit)

    @Slot()
    def start(self) -> None:
        """建立 SSH 連線並進入讀取迴圈。由 QThread.started signal 觸發。"""
        try:
            self._session.connect()
            self.connected.emit()
            self._running = True
            while self._running and self._session.is_connected:
                self._session.read_once()
        except self._session.HostKeyMissingError as exc:
            self.error_occurred.emit(
                f"未知的主機金鑰 / Unknown host key for {exc.hostname}\n"
                "請先接受 host key / Please accept the host key first"
            )
        except Exception as exc:
            self.error_occurred.emit(
                f"SSH 錯誤 / SSH error: {exc}"
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
        """送出 bytes 到 SSH channel。"""
        try:
            self._session.write(data)
        except Exception as exc:
            self.error_occurred.emit(
                f"寫入失敗 / Write failed: {exc}"
            )

    @Slot(int, int)
    def resize(self, cols: int, rows: int) -> None:
        """調整 PTY 尺寸。"""
        import contextlib
        with contextlib.suppress(Exception):
            self._session.resize(cols, rows)
