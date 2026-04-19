"""ui/sftp_panel.py — SFTP 雙欄檔案瀏覽面板。

結論先寫:
    - SftpPanel 提供本機 (左) ↔ 遠端 (右) 雙欄瀏覽。
    - 本機用 QFileSystemModel + QTreeView。
    - 遠端用自訂 RemoteFileModel (asyncssh 後端)。
    - 下載 / 上傳使用 @asyncSlot (qasync),不阻塞 UI thread。
    - 進度透過 ThrottledProgressSignal 每 100ms 更新 QProgressBar。
    - 支援多個並行傳輸任務。

分層原則:本模組位於 ui/,可 import core/,不可 import protocols/。
"""

from __future__ import annotations

import time
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from andyterm.core.sftp_session import SftpSession

__all__ = ["SftpPanel"]

# ---------------------------------------------------------------------------
# 進度 throttle (100ms)
# ---------------------------------------------------------------------------


class _ProgressEmitter:
    """Throttled 2-arg progress callback (current, total),限制頻率 100ms。

    SftpSession.download/upload 接受 Callable[[int, int], None];
    此類別在更新 UI 前節流,避免訊號洪水。
    """

    def __init__(self, callback: Any, min_interval: float = 0.1) -> None:
        self._cb = callback
        self._min = min_interval
        self._last = 0.0

    def __call__(self, current: int, total: int) -> None:
        now = time.monotonic()
        if now - self._last >= self._min or current == total:
            self._last = now
            self._cb(current, total)


# ---------------------------------------------------------------------------
# 遠端檔案清單 Model
# ---------------------------------------------------------------------------



@dataclass
class _RemoteEntry:
    name: str
    size: int
    mtime: int
    permissions: str
    is_dir: bool


class RemoteFileModel(QAbstractTableModel):
    """遠端目錄清單的 Table Model。

    欄位: 名稱 / 大小 / 修改時間 / 權限
    """

    _HEADERS: typing.ClassVar[list[str]] = [
        "名稱 / Name", "大小 / Size", "修改時間 / Modified", "權限 / Perm",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[_RemoteEntry] = []

    def load(self, names: list[Any]) -> None:
        self.beginResetModel()
        self._entries = []
        for n in names:
            attrs = n.attrs
            size = attrs.size or 0
            mtime = int(attrs.mtime or 0)
            perm = oct(attrs.permissions or 0)[-4:] if attrs.permissions else "????"
            is_dir = bool(attrs.permissions and (attrs.permissions & 0o040000))
            fname = n.filename
            name = fname.decode("utf-8", "replace") if isinstance(fname, bytes) else fname
            self._entries.append(
                _RemoteEntry(
                    name=name,
                    size=size,
                    mtime=mtime,
                    permissions=perm,
                    is_dir=is_dir,
                )
            )
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._entries = []
        self.endResetModel()

    def entry_at(self, row: int) -> _RemoteEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    # ------------------------------------------------------------------
    # QAbstractTableModel
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802,B008
        return len(self._entries)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802,B008
        return len(self._HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        entry = self._entries[index.row()]
        col = index.column()
        if col == 0:
            return ("📁 " if entry.is_dir else "📄 ") + entry.name
        if col == 1:
            return "" if entry.is_dir else _human_size(entry.size)
        if col == 2:
            import datetime
            return datetime.datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M")
        if col == 3:
            return entry.permissions
        return None


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n:.0f} TB"


# ---------------------------------------------------------------------------
# 傳輸任務列表 entry
# ---------------------------------------------------------------------------

@dataclass
class _TransferJob:
    label: str
    progress_bar: QProgressBar = field(default_factory=QProgressBar)


# ---------------------------------------------------------------------------
# SftpPanel
# ---------------------------------------------------------------------------

class SftpPanel(QWidget):
    """SFTP 雙欄檔案瀏覽面板。

    結論:
        - 左側本機 QFileSystemModel;右側遠端 RemoteFileModel。
        - 下載 / 上傳按鈕使用 @asyncSlot。
        - 進度列在底部,每個任務一條。
    """

    session_closed = Signal()

    def __init__(self, session: SftpSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = session
        self._jobs: list[_TransferJob] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # UI 建構
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # 上半:雙欄分割
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左側:本機
        local_panel = self._build_local_panel()
        splitter.addWidget(local_panel)

        # 中間:傳輸按鈕
        btn_panel = self._build_btn_panel()
        splitter.addWidget(btn_panel)

        # 右側:遠端
        remote_panel = self._build_remote_panel()
        splitter.addWidget(remote_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 2)

        main_layout.addWidget(splitter, 1)

        # 下半:進度區域
        self._progress_layout = QVBoxLayout()
        self._progress_label = QLabel("傳輸進度 / Transfer Progress")
        self._progress_layout.addWidget(self._progress_label)
        main_layout.addLayout(self._progress_layout)

        # 連線按鈕列
        conn_row = QHBoxLayout()
        self._connect_btn = QPushButton("連線 / Connect")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._disconnect_btn = QPushButton("中斷 / Disconnect")
        self._disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        self._disconnect_btn.setEnabled(False)
        conn_row.addWidget(self._connect_btn)
        conn_row.addWidget(self._disconnect_btn)
        conn_row.addStretch()
        main_layout.addLayout(conn_row)

    def _build_local_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("本機 / Local"))

        self._local_model = QFileSystemModel()
        self._local_model.setRootPath(str(Path.home()))

        self._local_view = QTreeView()
        self._local_view.setModel(self._local_model)
        self._local_view.setRootIndex(self._local_model.index(str(Path.home())))
        self._local_view.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._local_view)

        return panel

    def _build_remote_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("遠端 / Remote"))

        # 位址列
        addr_row = QHBoxLayout()
        self._remote_path = QLineEdit(".")
        btn_go = QPushButton("Go")
        btn_go.clicked.connect(self._on_navigate_clicked)
        btn_up = QPushButton("↑ Up")
        btn_up.clicked.connect(self._on_navigate_up)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._on_refresh_clicked)
        addr_row.addWidget(self._remote_path, 1)
        addr_row.addWidget(btn_go)
        addr_row.addWidget(btn_up)
        addr_row.addWidget(btn_refresh)
        layout.addLayout(addr_row)

        # 遠端檔案清單
        self._remote_model = RemoteFileModel(self)
        self._remote_view = QTableView()
        self._remote_view.setModel(self._remote_model)
        self._remote_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._remote_view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self._remote_view.doubleClicked.connect(self._on_remote_double_click)
        layout.addWidget(self._remote_view)

        return panel

    def _build_btn_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(80)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addStretch()

        btn_download = QPushButton("← 下載\nDownload")
        btn_download.clicked.connect(self._on_download_clicked)
        layout.addWidget(btn_download)

        btn_upload = QPushButton("→ 上傳\nUpload")
        btn_upload.clicked.connect(self._on_upload_clicked)
        layout.addWidget(btn_upload)

        layout.addStretch()
        return panel

    # ------------------------------------------------------------------
    # 連線管理
    # ------------------------------------------------------------------

    def _on_connect_clicked(self) -> None:
        self._async_connect()

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def _async_connect(self) -> None:
        try:
            await self._session.async_connect()
            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(True)
            await self._refresh_remote()
        except Exception as exc:
            QMessageBox.critical(
                self, "SFTP 連線失敗 / Connection Failed",
                f"{exc}",
            )

    def _on_disconnect_clicked(self) -> None:
        self._async_disconnect()

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def _async_disconnect(self) -> None:
        await self._session.async_close()
        self._remote_model.clear()
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self.session_closed.emit()

    # ------------------------------------------------------------------
    # 遠端導航
    # ------------------------------------------------------------------

    def _on_navigate_clicked(self) -> None:
        self._async_navigate(self._remote_path.text())

    def _on_navigate_up(self) -> None:
        self._async_navigate("..")

    def _on_refresh_clicked(self) -> None:
        self._async_navigate(self._session.remote_cwd)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def _async_navigate(self, path: str) -> None:
        try:
            entries = await self._session.navigate(path)
            self._remote_path.setText(self._session.remote_cwd)
            self._remote_model.load(list(entries))
        except Exception as exc:
            QMessageBox.warning(self, "導航失敗 / Navigate Failed", str(exc))

    async def _refresh_remote(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            entries = await self._session.list_current()
            self._remote_model.load(list(entries))
            self._remote_path.setText(self._session.remote_cwd)

    def _on_remote_double_click(self, index: QModelIndex) -> None:
        entry = self._remote_model.entry_at(index.row())
        if entry and entry.is_dir:
            self._async_navigate(entry.name)

    # ------------------------------------------------------------------
    # 傳輸
    # ------------------------------------------------------------------

    def _on_download_clicked(self) -> None:
        index = self._remote_view.currentIndex()
        if not index.isValid():
            return
        entry = self._remote_model.entry_at(index.row())
        if not entry or entry.is_dir:
            return

        local_dir = Path(self._local_model.filePath(self._local_view.currentIndex()))
        if not local_dir.is_dir():
            local_dir = local_dir.parent

        self._async_download(entry.name, local_dir)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def _async_download(self, name: str, local_dir: Path) -> None:
        job = self._add_job(f"下載 / Download: {name}")
        try:
            progress = _ProgressEmitter(
                lambda cur, tot: self._update_job(job, cur, tot)
            )
            await self._session.download(name, local_dir, progress)
            job.progress_bar.setValue(100)
        except Exception as exc:
            QMessageBox.warning(
                self, "下載失敗 / Download Failed", str(exc)
            )
        finally:
            self._remove_job(job)

    def _on_upload_clicked(self) -> None:
        local_index = self._local_view.currentIndex()
        if not local_index.isValid():
            return
        local_path = Path(self._local_model.filePath(local_index))
        if not local_path.is_file():
            return
        self._async_upload(local_path)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def _async_upload(self, local_path: Path) -> None:
        job = self._add_job(f"上傳 / Upload: {local_path.name}")
        try:
            progress = _ProgressEmitter(
                lambda cur, tot: self._update_job(job, cur, tot)
            )
            await self._session.upload(local_path, progress)
            job.progress_bar.setValue(100)
            await self._refresh_remote()
        except Exception as exc:
            QMessageBox.warning(
                self, "上傳失敗 / Upload Failed", str(exc)
            )
        finally:
            self._remove_job(job)

    # ------------------------------------------------------------------
    # 進度管理
    # ------------------------------------------------------------------

    def _add_job(self, label: str) -> _TransferJob:
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFormat(f"{label}  %p%")
        self._progress_layout.addWidget(bar)
        job = _TransferJob(label=label, progress_bar=bar)
        self._jobs.append(job)
        return job

    def _update_job(self, job: _TransferJob, current: int, total: int) -> None:
        if total > 0:
            pct = int(current * 100 / total)
            job.progress_bar.setValue(pct)
        QApplication.processEvents()

    def _remove_job(self, job: _TransferJob) -> None:
        if job in self._jobs:
            self._jobs.remove(job)
        job.progress_bar.deleteLater()
