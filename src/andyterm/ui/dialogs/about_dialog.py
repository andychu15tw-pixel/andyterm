"""ui/dialogs/about_dialog.py — About 對話框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

__all__ = ["AboutDialog"]


class AboutDialog(QDialog):
    """AndyTerm About 對話框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("關於 AndyTerm / About AndyTerm")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        title = QLabel("<h2>AndyTerm v0.1.0-alpha</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "<p>Serial & SFTP GUI Tool for Moxa Industrial Computers</p>"
            "<p>工業嵌入式電腦序列埠與 SFTP 工具</p>"
            "<hr>"
            "<p>Tech Stack: Python 3.11 · PySide6 · paramiko · asyncssh · pyte</p>"
            "<p>© 2024 Moxa FAE Team</p>"
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setOpenExternalLinks(True)
        layout.addWidget(desc)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
