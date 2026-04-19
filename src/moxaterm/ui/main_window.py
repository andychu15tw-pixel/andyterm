"""ui/main_window.py — MoxaTerm 主視窗 (最小可用版)。

結論先寫:
    - MainWindow 包含 QTabWidget 多 session 分頁。
    - File → New Serial Session:hardcode 連預設 serial port,開啟終端機分頁。
    - 狀態列顯示目前連線狀態。
    - 關閉視窗時正確停止所有 worker 與 QThread。

分層原則:本模組位於 ui/,可 import core/,不可 import protocols/。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from moxaterm.core.serial_session import SerialSession
from moxaterm.core.session import SerialConfig
from moxaterm.ui.terminal_widget import TerminalWidget
from moxaterm.ui.workers.serial_worker import SerialWorker

__all__ = ["MainWindow"]


@dataclass
class _SerialTab:
    """一個序列埠 session 分頁的資源集合。"""

    widget: TerminalWidget
    session: SerialSession
    worker: SerialWorker
    thread: QThread
    status_label: QLabel = field(default_factory=lambda: QLabel("連線中…"))


class MainWindow(QMainWindow):
    """MoxaTerm 主視窗。

    結論:
        - QTabWidget 為核心,每個 session 一個 tab。
        - Serial Worker 在獨立 QThread 中執行讀取迴圈。
        - closeEvent 確保所有 thread 正確停止。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MoxaTerm")
        self.resize(1024, 680)

        self._tabs: QTabWidget = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)
        self.setCentralWidget(central)

        self._status_label = QLabel("準備就緒 / Ready")
        self.statusBar().addPermanentWidget(self._status_label)

        self._active_tabs: dict[int, _SerialTab] = {}

        self._build_menu()

    # ------------------------------------------------------------------
    # 選單
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("檔案(&F)")

        new_serial = file_menu.addAction("新增序列埠連線 / &New Serial Session")
        new_serial.setShortcut("Ctrl+N")
        new_serial.triggered.connect(self._new_serial_session)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("離開 / E&xit")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

    # ------------------------------------------------------------------
    # 新增序列埠 session
    # ------------------------------------------------------------------

    def _new_serial_session(self) -> None:
        """顯示簡單 port 輸入對話框並開啟新的序列埠分頁。"""
        default_port = "COM1" if sys.platform == "win32" else "/dev/ttyUSB0"
        port, ok = QInputDialog.getText(
            self,
            "新增序列埠連線 / New Serial Session",
            "Port:",
            text=default_port,
        )
        if not ok or not port.strip():
            return

        try:
            config = SerialConfig(name=port.strip(), port=port.strip())
        except Exception as exc:
            QMessageBox.critical(
                self,
                "設定錯誤 / Config Error",
                f"無效設定 / Invalid config:\n{exc}",
            )
            return

        self._open_serial_tab(config)

    def _open_serial_tab(self, config: SerialConfig) -> None:
        """建立 SerialSession + Worker + QThread 並加入新分頁。"""
        term = TerminalWidget(cols=80, rows=24)
        session = SerialSession(config)
        worker = SerialWorker(session)
        thread = QThread(self)

        worker.moveToThread(thread)

        # 連線 signals — moveToThread 之後才 connect
        thread.started.connect(worker.start)
        worker.data_received.connect(term.feed)
        worker.connected.connect(
            lambda: self._on_serial_connected(config.name)
        )
        worker.disconnected.connect(
            lambda: self._on_serial_disconnected(config.name)
        )
        worker.error_occurred.connect(self._on_serial_error)
        term.data_to_send.connect(worker.write)

        # 包成 container widget
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(term)

        tab_idx = self._tabs.addTab(container, config.name)
        self._tabs.setCurrentIndex(tab_idx)

        status_label = QLabel(f"開啟中 / Opening: {config.port}")
        self.statusBar().addWidget(status_label)

        self._active_tabs[tab_idx] = _SerialTab(
            widget=term,
            session=session,
            worker=worker,
            thread=thread,
            status_label=status_label,
        )

        thread.start()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_serial_connected(self, name: str) -> None:
        self._status_label.setText(f"已連線 / Connected: {name}")

    def _on_serial_disconnected(self, name: str) -> None:
        self._status_label.setText(f"已斷線 / Disconnected: {name}")

    def _on_serial_error(self, msg: str) -> None:
        QMessageBox.warning(self, "序列埠錯誤 / Serial Error", msg)
        self._status_label.setText(f"錯誤 / Error: {msg[:60]}")

    def _close_tab(self, index: int) -> None:
        """關閉分頁並停止對應的 worker thread。"""
        if index in self._active_tabs:
            tab = self._active_tabs.pop(index)
            self._stop_tab(tab)

        # 重建 index 對應表 (tabs shift after removal)
        self._tabs.removeTab(index)
        self._rebuild_tab_index()

    def _stop_tab(self, tab: _SerialTab) -> None:
        tab.worker.stop()
        tab.thread.quit()
        tab.thread.wait(2000)
        tab.status_label.deleteLater()

    def _rebuild_tab_index(self) -> None:
        """重新建立 tab index → _SerialTab 的對應表。"""
        new_map: dict[int, _SerialTab] = {}
        for i in range(self._tabs.count()):
            container = self._tabs.widget(i)
            for tab in self._active_tabs.values():
                if container and tab.widget.parent() == container:
                    new_map[i] = tab
                    break
        self._active_tabs = new_map

    # ------------------------------------------------------------------
    # 視窗關閉
    # ------------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        """關閉視窗前停止所有 worker thread。"""
        for tab in list(self._active_tabs.values()):
            self._stop_tab(tab)
        self._active_tabs.clear()
        event.accept()
