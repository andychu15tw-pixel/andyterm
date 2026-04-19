"""ui/main_window.py — MoxaTerm 主視窗。

結論先寫:
    - MainWindow 包含左側 SessionTreeView + 右側 QTabWidget。
    - File → New Serial/SSH Session → NewSessionDialog。
    - session tree 雙擊 → 開對應 session 分頁。
    - 狀態列顯示目前連線狀態。
    - 關閉視窗時正確停止所有 worker 與 QThread。

分層原則:本模組位於 ui/,可 import core/,不可 import protocols/。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from moxaterm.core.serial_session import SerialSession
from moxaterm.core.session import SerialConfig, SessionConfig, SshConfig
from moxaterm.core.session_store import SessionStore
from moxaterm.ui.dialogs.new_session_dialog import NewSessionDialog
from moxaterm.ui.session_tree import SessionTreeView
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
    status_label: QLabel = field(default_factory=lambda: QLabel())


class MainWindow(QMainWindow):
    """MoxaTerm 主視窗。

    結論:
        - 左側 SessionTreeView + 右側 QTabWidget (QSplitter)。
        - Serial Worker 在獨立 QThread 中執行讀取迴圈。
        - closeEvent 確保所有 thread 正確停止。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MoxaTerm")
        self.resize(1200, 720)

        self._store = SessionStore()

        # 左側 session tree
        self._session_tree = SessionTreeView(self._store)
        self._session_tree.setMinimumWidth(180)
        self._session_tree.setMaximumWidth(300)
        self._session_tree.session_activated.connect(self._open_session_by_id)
        self._session_tree.session_delete_requested.connect(self._delete_session)

        # 右側 tabs
        self._tabs: QTabWidget = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)

        # Splitter 組合
        splitter = QSplitter()
        splitter.addWidget(self._session_tree)
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
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

        new_serial = file_menu.addAction("新增序列埠 / &New Serial Session")
        new_serial.setShortcut("Ctrl+N")
        new_serial.triggered.connect(self._new_session_dialog)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("離開 / E&xit")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

        session_menu = menu_bar.addMenu("連線(&S)")
        close_tab = session_menu.addAction("關閉分頁 / &Close Tab")
        close_tab.setShortcut("Ctrl+W")
        close_tab.triggered.connect(self._close_current_tab)

    # ------------------------------------------------------------------
    # 新增 session
    # ------------------------------------------------------------------

    def _new_session_dialog(self) -> None:
        dlg = NewSessionDialog(self)
        if dlg.exec() != NewSessionDialog.DialogCode.Accepted:
            return
        try:
            config = dlg.result_config()
        except Exception as exc:
            QMessageBox.critical(
                self, "設定錯誤 / Config Error",
                f"無效設定 / Invalid config:\n{exc}",
            )
            return

        session_id = self._store.add(config)
        self._session_tree.refresh()
        self._open_session_by_config(config, session_id)

    def _open_session_by_id(self, session_id: str) -> None:
        """從 session store 讀取設定並開啟分頁。"""
        data = self._store.get(session_id)
        if not data:
            return
        try:
            config = SessionStore.config_from_dict(data)
        except Exception as exc:
            QMessageBox.critical(
                self, "設定錯誤 / Config Error",
                f"無法載入 / Cannot load:\n{exc}",
            )
            return
        self._open_session_by_config(config, session_id)

    def _open_session_by_config(self, config: SessionConfig, session_id: str) -> None:
        if isinstance(config, SerialConfig):
            self._open_serial_tab(config)
        elif isinstance(config, SshConfig):
            QMessageBox.information(
                self, "SSH",
                f"SSH 連線 {config.host} — Day 5+ 實作 / SSH tab coming in Day 5+",
            )

    def _delete_session(self, session_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "確認刪除 / Confirm Delete",
            "確定要刪除這個連線設定嗎? / Delete this session?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._store.remove(session_id)
            self._session_tree.refresh()

    # ------------------------------------------------------------------
    # Serial tab
    # ------------------------------------------------------------------

    def _open_serial_tab(self, config: SerialConfig) -> None:
        term = TerminalWidget(cols=80, rows=24)
        session = SerialSession(config)
        worker = SerialWorker(session)
        thread = QThread(self)

        worker.moveToThread(thread)

        thread.started.connect(worker.start)
        worker.data_received.connect(term.feed)
        worker.connected.connect(lambda: self._on_serial_connected(config.name))
        worker.disconnected.connect(lambda: self._on_serial_disconnected(config.name))
        worker.error_occurred.connect(self._on_serial_error)
        term.data_to_send.connect(worker.write)

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

    def _close_current_tab(self) -> None:
        idx = self._tabs.currentIndex()
        if idx >= 0:
            self._close_tab(idx)

    def _close_tab(self, index: int) -> None:
        if index in self._active_tabs:
            tab = self._active_tabs.pop(index)
            self._stop_tab(tab)

        self._tabs.removeTab(index)
        self._rebuild_tab_index()

    def _stop_tab(self, tab: _SerialTab) -> None:
        tab.worker.stop()
        tab.thread.quit()
        tab.thread.wait(2000)
        tab.status_label.deleteLater()

    def _rebuild_tab_index(self) -> None:
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
        for tab in list(self._active_tabs.values()):
            self._stop_tab(tab)
        self._active_tabs.clear()
        event.accept()
