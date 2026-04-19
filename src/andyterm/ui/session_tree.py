"""ui/session_tree.py — 左側 Session Tree (QTreeView + Model/View)。

結論先寫:
    - SessionTreeModel 實作 QAbstractItemModel,資料來自 SessionStore。
    - SessionTreeView 包裝 QTreeView,處理雙擊 / 右鍵選單。
    - session_activated(session_id) signal 觸發時 MainWindow 開新分頁。
    - 資料夾為第一層節點;session 為第二層節點。

分層原則:本模組位於 ui/,可 import core/ 與 moxa/。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
    QTreeView,
    QWidget,
)

from andyterm.core.session_store import SessionStore

__all__ = ["SessionTreeView"]

_ROLE_SESSION_ID = Qt.ItemDataRole.UserRole
_ROLE_IS_FOLDER = Qt.ItemDataRole.UserRole + 1


class _Node:
    """Tree node (資料夾或 session)。"""

    def __init__(
        self,
        label: str,
        session_id: str | None = None,
        parent: _Node | None = None,
    ) -> None:
        self.label = label
        self.session_id = session_id
        self.is_folder = session_id is None
        self.parent: _Node | None = parent
        self.children: list[_Node] = []

    def row(self) -> int:
        if self.parent:
            return self.parent.children.index(self)
        return 0


class SessionTreeModel(QAbstractItemModel):
    """Session Tree 資料模型。

    結論:
        - 第一層:資料夾名稱 (None → "未分類 / Unfiled")。
        - 第二層:session 名稱。
        - 唯讀 (編輯由對話框處理)。
    """

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._root = _Node("root")
        self._rebuild()

    def _rebuild(self) -> None:
        self._root.children.clear()
        tree = self._store.as_tree()
        # 先有名稱的資料夾,再 None 分組
        sorted_folders: list[str | None] = sorted(
            (k for k in tree if k is not None),
            key=lambda x: (x or ""),
        )
        if None in tree:
            sorted_folders.append(None)

        for folder in sorted_folders:
            folder_label = folder if folder else "未分類 / Unfiled"
            folder_node = _Node(folder_label, parent=self._root)
            self._root.children.append(folder_node)
            for s in tree[folder]:
                child = _Node(s.get("name", s["id"]), session_id=s["id"], parent=folder_node)
                folder_node.children.append(child)

    def refresh(self) -> None:
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    # ------------------------------------------------------------------
    # QAbstractItemModel 必要實作
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802,B008
        node = self._node(parent)
        return len(node.children)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802,B008
        return 1

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: B008
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = self._node(parent)
        if row < len(parent_node.children):
            child = parent_node.children[row]
            return self.createIndex(row, column, child)
        return QModelIndex()

    def parent(self, index: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]  # noqa: B008
        if not index.isValid():
            return QModelIndex()
        node: _Node = index.internalPointer()
        parent = node.parent
        if parent is None or parent is self._root:
            return QModelIndex()
        return self.createIndex(parent.row(), 0, parent)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        node: _Node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.label
        if role == _ROLE_SESSION_ID:
            return node.session_id
        if role == _ROLE_IS_FOLDER:
            return node.is_folder
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ------------------------------------------------------------------
    # 內部輔助
    # ------------------------------------------------------------------

    def _node(self, index: QModelIndex | QPersistentModelIndex) -> _Node:
        if index.isValid():
            return index.internalPointer()  # type: ignore[no-any-return]
        return self._root


class SessionTreeView(QTreeView):
    """Session Tree View。

    Signals:
        session_activated(str): 使用者雙擊 session 時觸發,參數為 session_id。
        session_delete_requested(str): 使用者選擇刪除時觸發。
    """

    session_activated = Signal(str)
    session_delete_requested = Signal(str)

    def __init__(self, store: SessionStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._model = SessionTreeModel(store, self)
        self.setModel(self._model)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.doubleClicked.connect(self._on_double_click)
        self.expandAll()

    def refresh(self) -> None:
        self._model.refresh()
        self.expandAll()

    def _on_double_click(self, index: QModelIndex) -> None:
        session_id: str | None = self._model.data(index, _ROLE_SESSION_ID)
        if session_id:
            self.session_activated.emit(session_id)

    def _show_context_menu(self, pos: Any) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        session_id: str | None = self._model.data(index, _ROLE_SESSION_ID)
        if not session_id:
            return

        menu = QMenu(self)
        connect_action = menu.addAction("連線 / Connect")
        menu.addSeparator()
        delete_action = menu.addAction("刪除 / Delete")

        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == connect_action:
            self.session_activated.emit(session_id)
        elif action == delete_action:
            self.session_delete_requested.emit(session_id)
