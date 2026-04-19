"""ui/main_window.py — AndyTerm 主視窗。

結論先寫:
    - MainWindow 包含左側 SessionTreeView + 右側 QTabWidget。
    - File → New Session → NewSessionDialog (Serial / SSH / SFTP)。
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

from andyterm.core.serial_session import SerialSession
from andyterm.core.session import SerialConfig, SessionConfig, SshConfig
from andyterm.core.session_store import SessionStore
from andyterm.core.sftp_session import SftpSession
from andyterm.core.ssh_session import SshSession
from andyterm.ui.dialogs.about_dialog import AboutDialog
from andyterm.ui.dialogs.new_session_dialog import NewSessionDialog
from andyterm.ui.session_tree import SessionTreeView
from andyterm.ui.sftp_panel import SftpPanel
from andyterm.ui.terminal_widget import TerminalWidget
from andyterm.ui.workers.serial_worker import SerialWorker
from andyterm.ui.workers.ssh_worker import SshWorker

__all__ = ["MainWindow"]


@dataclass
class _SerialTab:
    widget: TerminalWidget
    session: SerialSession
    worker: SerialWorker
    thread: QThread
    status_label: QLabel = field(default_factory=lambda: QLabel())


@dataclass
class _SshTab:
    widget: TerminalWidget
    session: SshSession
    worker: SshWorker
    thread: QThread
    status_label: QLabel = field(default_factory=lambda: QLabel())


class MainWindow(QMainWindow):
    """AndyTerm 主視窗。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AndyTerm")
        self.resize(1200, 720)

        self._store = SessionStore()

        self._session_tree = SessionTreeView(self._store)
        self._session_tree.setMinimumWidth(180)
        self._session_tree.setMaximumWidth(300)
        self._session_tree.session_activated.connect(self._open_session_by_id)
        self._session_tree.session_delete_requested.connect(self._delete_session)

        self._tabs: QTabWidget = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)

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

        self._serial_tabs: dict[int, _SerialTab] = {}
        self._ssh_tabs: dict[int, _SshTab] = {}

        self._build_menu()

    # ------------------------------------------------------------------
    # 選單
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        # 檔案選單
        file_menu = menu_bar.addMenu("檔案(&F)")
        new_session = file_menu.addAction("新增連線 / &New Session")
        new_session.setShortcut("Ctrl+N")
        new_session.triggered.connect(self._new_session_dialog)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("離開 / E&xit")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

        # 連線選單
        session_menu = menu_bar.addMenu("連線(&S)")
        close_tab = session_menu.addAction("關閉分頁 / &Close Tab")
        close_tab.setShortcut("Ctrl+W")
        close_tab.triggered.connect(self._close_current_tab)

        next_tab = session_menu.addAction("下一個分頁 / Next Tab")
        next_tab.setShortcut("Ctrl+Tab")
        next_tab.triggered.connect(self._next_tab)

        # 說明選單
        help_menu = menu_bar.addMenu("說明(&H)")
        about_action = help_menu.addAction("關於 / &About")
        about_action.triggered.connect(self._show_about)

    def _next_tab(self) -> None:
        current = self._tabs.currentIndex()
        count = self._tabs.count()
        if count > 0:
            self._tabs.setCurrentIndex((current + 1) % count)

    def _show_about(self) -> None:
        AboutDialog(self).exec()

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
            session_type = getattr(config, "type", "SSH")
            if str(session_type) == "SFTP":
                self._open_sftp_tab(config)
            else:
                self._open_ssh_tab(config)

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
        worker.connected.connect(lambda: self._on_connected(config.name))
        worker.disconnected.connect(lambda: self._on_disconnected(config.name))
        worker.error_occurred.connect(self._on_error)
        term.data_to_send.connect(worker.write)

        tab_idx = self._add_tab(term, config.name)
        status_label = QLabel(f"Serial: {config.port}")
        self.statusBar().addWidget(status_label)

        self._serial_tabs[tab_idx] = _SerialTab(
            widget=term, session=session, worker=worker,
            thread=thread, status_label=status_label,
        )
        thread.start()

    # ------------------------------------------------------------------
    # SSH tab
    # ------------------------------------------------------------------

    def _open_ssh_tab(self, config: SshConfig) -> None:
        term = TerminalWidget(cols=config.cols, rows=config.rows)
        session = SshSession(config)
        worker = SshWorker(session)
        thread = QThread(self)

        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.data_received.connect(term.feed)
        worker.connected.connect(lambda: self._on_connected(config.name))
        worker.disconnected.connect(lambda: self._on_disconnected(config.name))
        worker.error_occurred.connect(self._on_error)
        term.data_to_send.connect(worker.write)

        tab_idx = self._add_tab(term, config.name)
        status_label = QLabel(f"SSH: {config.host}")
        self.statusBar().addWidget(status_label)

        self._ssh_tabs[tab_idx] = _SshTab(
            widget=term, session=session, worker=worker,
            thread=thread, status_label=status_label,
        )
        thread.start()

    # ------------------------------------------------------------------
    # SFTP tab
    # ------------------------------------------------------------------

    def _open_sftp_tab(self, config: SshConfig) -> None:
        session = SftpSession(config)
        panel = SftpPanel(session, self)
        panel.session_closed.connect(
            lambda: self._status_label.setText(f"SFTP 已斷線 / Disconnected: {config.name}")
        )

        tab_idx = self._add_tab(panel, f"SFTP: {config.name}")
        self._status_label.setText(f"SFTP: {config.host}")
        _ = tab_idx  # tab managed via close button only

    # ------------------------------------------------------------------
    # 共用 tab helpers
    # ------------------------------------------------------------------

    def _add_tab(self, widget: QWidget, title: str) -> int:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(widget)
        idx = self._tabs.addTab(container, title)
        self._tabs.setCurrentIndex(idx)
        return idx

    def _on_connected(self, name: str) -> None:
        self._status_label.setText(f"已連線 / Connected: {name}")

    def _on_disconnected(self, name: str) -> None:
        self._status_label.setText(f"已斷線 / Disconnected: {name}")

    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "連線錯誤 / Connection Error", msg)
        self._status_label.setText(f"錯誤 / Error: {msg[:60]}")

    def _close_current_tab(self) -> None:
        idx = self._tabs.currentIndex()
        if idx >= 0:
            self._close_tab(idx)

    def _close_tab(self, index: int) -> None:
        if index in self._serial_tabs:
            tab = self._serial_tabs.pop(index)
            self._stop_worker_tab(tab.worker, tab.thread, tab.status_label)
        elif index in self._ssh_tabs:
            tab2 = self._ssh_tabs.pop(index)
            self._stop_worker_tab(tab2.worker, tab2.thread, tab2.status_label)

        self._tabs.removeTab(index)
        self._rebuild_tab_index()

    def _stop_worker_tab(
        self,
        worker: SerialWorker | SshWorker,
        thread: QThread,
        status_label: QLabel,
    ) -> None:
        worker.stop()
        thread.quit()
        thread.wait(2000)
        status_label.deleteLater()

    def _rebuild_tab_index(self) -> None:
        new_serial: dict[int, _SerialTab] = {}
        new_ssh: dict[int, _SshTab] = {}
        for i in range(self._tabs.count()):
            container = self._tabs.widget(i)
            for tab in self._serial_tabs.values():
                if container and tab.widget.parent() == container:
                    new_serial[i] = tab
                    break
            for tab2 in self._ssh_tabs.values():
                if container and tab2.widget.parent() == container:
                    new_ssh[i] = tab2
                    break
        self._serial_tabs = new_serial
        self._ssh_tabs = new_ssh

    # ------------------------------------------------------------------
    # 視窗關閉
    # ------------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        for tab in list(self._serial_tabs.values()):
            self._stop_worker_tab(tab.worker, tab.thread, tab.status_label)
        for tab2 in list(self._ssh_tabs.values()):
            self._stop_worker_tab(tab2.worker, tab2.thread, tab2.status_label)
        self._serial_tabs.clear()
        self._ssh_tabs.clear()
        event.accept()
