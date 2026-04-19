"""ui/dialogs/new_session_dialog.py — 新增連線對話框。

結論先寫:
    - NewSessionDialog 提供 Serial / SSH 分頁設定。
    - Serial tab: port 掃描、baudrate、8N1、flow control、encoding。
    - SSH tab: host / port / username / auth method / key path。
    - result_config() 回傳 SerialConfig 或 SshConfig。
    - 雙語 label (繁中 + English)。

分層原則:本模組位於 ui/,可 import core/ 與 moxa/。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from moxaterm.core.session import SerialConfig, SessionConfig, SshConfig

__all__ = ["NewSessionDialog"]

_BAUDRATES = [
    "300", "1200", "2400", "4800", "9600", "19200", "38400",
    "57600", "115200", "230400", "460800", "921600",
]
_PARITIES = ["N", "E", "O", "M", "S"]
_BYTESIZE = ["5", "6", "7", "8"]
_STOPBITS = ["1", "1.5", "2"]
_ENCODINGS = ["utf-8", "big5", "latin-1", "shift_jis"]

_QUICK_PROFILES: dict[str, dict[str, Any]] = {
    "Moxa V3400 Console (115200 8N1)": {
        "baudrate": "115200", "bytesize": "8", "parity": "N", "stopbits": "1",
    },
    "Moxa V1200 U-Boot Console (921600 8N1)": {
        "baudrate": "921600", "bytesize": "8", "parity": "N", "stopbits": "1",
    },
    "Moxa V2406C Console (115200 8N1)": {
        "baudrate": "115200", "bytesize": "8", "parity": "N", "stopbits": "1",
    },
}


class NewSessionDialog(QDialog):
    """新增連線對話框。

    結論:
        - QTabWidget 包含 Serial / SSH 兩個分頁。
        - 確定後呼叫 result_config() 取得設定物件。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增連線 / New Session")
        self.setMinimumWidth(480)

        self._tabs = QTabWidget()
        self._serial_tab = self._build_serial_tab()
        self._ssh_tab = self._build_ssh_tab()
        self._tabs.addTab(self._serial_tab, "序列埠 Serial")
        self._tabs.addTab(self._ssh_tab, "SSH / SFTP")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Serial 分頁
    # ------------------------------------------------------------------

    def _build_serial_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        # Session name
        self._serial_name = QLineEdit("新序列埠連線")
        form.addRow("連線名稱 / Name:", self._serial_name)

        # Port
        port_row = QWidget()
        port_layout = QHBoxLayout(port_row)
        port_layout.setContentsMargins(0, 0, 0, 0)
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.addItems(self._scan_ports())
        btn_refresh = QPushButton("掃描 Refresh")
        btn_refresh.clicked.connect(self._refresh_ports)
        btn_moxa = QPushButton("Moxa UPort")
        btn_moxa.clicked.connect(self._scan_moxa)
        port_layout.addWidget(self._port_combo, 1)
        port_layout.addWidget(btn_refresh)
        port_layout.addWidget(btn_moxa)
        form.addRow("Port:", port_row)

        # Baudrate
        self._baud_combo = QComboBox()
        self._baud_combo.setEditable(True)
        self._baud_combo.addItems(_BAUDRATES)
        self._baud_combo.setCurrentText("115200")
        form.addRow("Baudrate:", self._baud_combo)

        # Bytesize / Parity / Stopbits
        frame_8n1 = QGroupBox("資料格式 / Data Format")
        h = QHBoxLayout(frame_8n1)
        self._bytesize_combo = QComboBox()
        self._bytesize_combo.addItems(_BYTESIZE)
        self._bytesize_combo.setCurrentText("8")
        self._parity_combo = QComboBox()
        self._parity_combo.addItems(_PARITIES)
        self._stopbits_combo = QComboBox()
        self._stopbits_combo.addItems(_STOPBITS)
        h.addWidget(QLabel("Bytesize:"))
        h.addWidget(self._bytesize_combo)
        h.addWidget(QLabel("Parity:"))
        h.addWidget(self._parity_combo)
        h.addWidget(QLabel("Stopbits:"))
        h.addWidget(self._stopbits_combo)
        form.addRow(frame_8n1)

        # Flow control
        flow_frame = QGroupBox("流量控制 / Flow Control")
        fh = QHBoxLayout(flow_frame)
        self._xonxoff_cb = QCheckBox("XON/XOFF")
        self._rtscts_cb = QCheckBox("RTS/CTS")
        fh.addWidget(self._xonxoff_cb)
        fh.addWidget(self._rtscts_cb)
        form.addRow(flow_frame)

        # Encoding
        self._encoding_combo = QComboBox()
        self._encoding_combo.addItems(_ENCODINGS)
        form.addRow("編碼 / Encoding:", self._encoding_combo)

        # Quick Profile
        qp_combo = QComboBox()
        qp_combo.addItem("-- 選擇預設 / Quick Profile --")
        qp_combo.addItems(list(_QUICK_PROFILES.keys()))
        qp_combo.currentTextChanged.connect(self._apply_quick_profile)
        form.addRow("快速設定 / Quick Profile:", qp_combo)

        return widget

    # ------------------------------------------------------------------
    # SSH 分頁
    # ------------------------------------------------------------------

    def _build_ssh_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._ssh_name = QLineEdit("新 SSH 連線")
        form.addRow("連線名稱 / Name:", self._ssh_name)

        self._ssh_host = QLineEdit()
        self._ssh_host.setPlaceholderText("192.168.1.1")
        form.addRow("主機 / Host:", self._ssh_host)

        self._ssh_port = QLineEdit("22")
        form.addRow("Port:", self._ssh_port)

        self._ssh_user = QLineEdit()
        self._ssh_user.setPlaceholderText("root")
        form.addRow("帳號 / Username:", self._ssh_user)

        # Auth method
        auth_group = QGroupBox("認證方式 / Authentication")
        auth_layout = QVBoxLayout(auth_group)
        self._auth_password = QRadioButton("密碼 / Password")
        self._auth_password.setChecked(True)
        self._auth_pubkey = QRadioButton("公鑰 / Public Key")
        auth_layout.addWidget(self._auth_password)
        auth_layout.addWidget(self._auth_pubkey)
        form.addRow(auth_group)

        # Key path
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self._key_path = QLineEdit()
        self._key_path.setPlaceholderText("~/.ssh/id_rsa")
        btn_browse = QPushButton("瀏覽 Browse")
        btn_browse.clicked.connect(self._browse_key)
        key_layout.addWidget(self._key_path, 1)
        key_layout.addWidget(btn_browse)
        form.addRow("私鑰路徑 / Key Path:", key_row)

        return widget

    # ------------------------------------------------------------------
    # 輔助
    # ------------------------------------------------------------------

    def _scan_ports(self) -> list[str]:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]

    def _refresh_ports(self) -> None:
        self._port_combo.clear()
        self._port_combo.addItems(self._scan_ports())

    def _scan_moxa(self) -> None:
        from moxaterm.moxa.uport_info import scan_moxa_uport
        self._port_combo.clear()
        for info in scan_moxa_uport():
            self._port_combo.addItem(
                f"{info.device} ({info.model})",
                userData=info.device,
            )
        if self._port_combo.count() == 0:
            self._port_combo.addItem("未找到 Moxa UPort / No Moxa UPort found")

    def _apply_quick_profile(self, name: str) -> None:
        profile = _QUICK_PROFILES.get(name)
        if not profile:
            return
        self._baud_combo.setCurrentText(profile.get("baudrate", "115200"))
        self._bytesize_combo.setCurrentText(profile.get("bytesize", "8"))
        self._parity_combo.setCurrentText(profile.get("parity", "N"))
        self._stopbits_combo.setCurrentText(profile.get("stopbits", "1"))

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇私鑰 / Select Private Key",
            str(Path.home() / ".ssh"),
        )
        if path:
            self._key_path.setText(path)

    # ------------------------------------------------------------------
    # 結果
    # ------------------------------------------------------------------

    def result_config(self) -> SessionConfig:
        """回傳使用者填寫的 SessionConfig。

        回傳:
            SerialConfig 或 SshConfig,視目前選中的分頁而定。
        """
        if self._tabs.currentIndex() == 0:
            port = self._port_combo.currentData() or self._port_combo.currentText()
            return SerialConfig(
                name=self._serial_name.text() or port,
                port=port,
                baudrate=int(self._baud_combo.currentText()),
                bytesize=int(self._bytesize_combo.currentText()),  # type: ignore[arg-type]
                parity=self._parity_combo.currentText(),  # type: ignore[arg-type]
                stopbits=float(self._stopbits_combo.currentText()),
                xonxoff=self._xonxoff_cb.isChecked(),
                rtscts=self._rtscts_cb.isChecked(),
                encoding=self._encoding_combo.currentText(),
            )

        auth = "pubkey" if self._auth_pubkey.isChecked() else "password"
        return SshConfig(
            name=self._ssh_name.text() or self._ssh_host.text(),
            host=self._ssh_host.text(),
            port=int(self._ssh_port.text() or "22"),
            username=self._ssh_user.text(),
            auth_method=auth,  # type: ignore[arg-type]
            key_path=self._key_path.text() or None,
        )
