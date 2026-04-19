"""app.py — QApplication 初始化。

結論先寫:
    - create_app() 建立並設定 QApplication,包含 High-DPI、字型、QSS 主題。
    - 回傳 QApplication 供 __main__.py 執行。
    - 不含任何視窗或 widget 邏輯。

分層原則:本模組可 import Qt,但不得 import core/ 的業務邏輯。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

__all__ = ["create_app"]

_QSS_PATH = Path(__file__).parent.parent.parent / "resources" / "dark.qss"


def create_app(argv: list[str] | None = None) -> QApplication:
    """建立並設定 QApplication。

    結論:設定 High-DPI、應用 QSS 深色主題 (若存在)、設定預設等寬字型。
    呼叫端負責呼叫 app.exec()。

    參數:
        argv: 命令列引數 (預設使用 sys.argv)。

    回傳:
        已設定好的 QApplication 實例。
    """
    if argv is None:
        argv = sys.argv

    app = QApplication(argv)
    app.setApplicationName("AndyTerm")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("Moxa")

    _apply_font(app)
    _apply_stylesheet(app)

    return app


def _apply_font(app: QApplication) -> None:
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    fixed.setPointSize(11)
    app.setFont(fixed)


def _apply_stylesheet(app: QApplication) -> None:
    if _QSS_PATH.exists():
        app.setStyleSheet(_QSS_PATH.read_text(encoding="utf-8"))
    else:
        app.setStyleSheet(_FALLBACK_QSS)


_FALLBACK_QSS = """
QMainWindow, QDialog {
    background: #1e1e1e;
    color: #d4d4d4;
}
QPlainTextEdit {
    background: #1e1e1e;
    color: #d4d4d4;
    border: none;
    selection-background-color: #264f78;
}
QTabWidget::pane {
    border: 1px solid #3c3c3c;
}
QTabBar::tab {
    background: #2d2d2d;
    color: #cccccc;
    padding: 6px 14px;
    border: 1px solid #3c3c3c;
    border-bottom: none;
}
QTabBar::tab:selected {
    background: #1e1e1e;
    color: #ffffff;
}
QStatusBar {
    background: #007acc;
    color: #ffffff;
}
QMenuBar {
    background: #252526;
    color: #cccccc;
}
QMenuBar::item:selected {
    background: #094771;
}
QMenu {
    background: #252526;
    color: #cccccc;
    border: 1px solid #454545;
}
QMenu::item:selected {
    background: #094771;
}
"""
