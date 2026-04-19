"""core/session.py — Session 型別定義與抽象基類。

結論先寫:
    - SessionType enum 定義支援的連線協定。
    - SessionConfig / SerialConfig / SshConfig 以 pydantic v2 BaseModel 實作,
      支援 model_dump_json() 序列化;不含密碼欄位 (密碼走 keyring)。
    - Session ABC 定義 connect / disconnect / write / is_connected 介面,
      讓 SerialSession / SshSession 繼承實作。

分層原則:本模組位於 core/,不得 import 任何 Qt 模組。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "SerialConfig",
    "Session",
    "SessionConfig",
    "SessionType",
    "SshConfig",
]


# ---------------------------------------------------------------------------
# SessionType
# ---------------------------------------------------------------------------


class SessionType(StrEnum):
    """支援的連線協定類型。

    結論:繼承 str 讓 pydantic 可直接序列化為 JSON 字串,亦可直接比較字串。
    """

    SERIAL = "serial"
    SSH = "ssh"
    SFTP = "sftp"
    RFC2217 = "rfc2217"
    TCP_RAW = "tcp_raw"


# ---------------------------------------------------------------------------
# Base SessionConfig
# ---------------------------------------------------------------------------


class SessionConfig(BaseModel):
    """所有 session 共用的基底設定。

    結論:以 pydantic v2 BaseModel 實作,支援 model_dump_json() 序列化。
    id 預設以 uuid4 自動產生;created_at / last_used_at 記錄時間戳。
    密碼欄位不在此模型內 — 密碼統一走 keyring。

    欄位:
        id: 唯一識別碼 (UUID4 字串)。
        name: 顯示名稱,不可空字串。
        type: SessionType,決定子類別應用哪個 Config。
        encoding: 終端機編碼,預設 UTF-8。
        created_at: 建立時間 (UTC)。
        last_used_at: 最後使用時間 (UTC),None 表示從未使用。
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: SessionType
    encoding: str = "utf-8"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# SerialConfig
# ---------------------------------------------------------------------------

SerialParity = Literal["N", "E", "O", "M", "S"]
SerialStopbits = float  # 1, 1.5, or 2 (pyserial STOPBITS_* constants)
SerialBytesize = Literal[5, 6, 7, 8]
SerialNewline = Literal["CR", "LF", "CRLF"]


class SerialConfig(SessionConfig):
    """RS-232/422/485 序列埠連線設定。

    結論:欄位對齊 pyserial.Serial 建構子參數,讓 protocols/serial_transport.py
    可直接解包使用。newline 控制 Enter 鍵送出的行結束字元序列。

    欄位:
        port: 序列埠名稱 (Windows: "COM3", Linux: "/dev/ttyUSB0")。
        baudrate: 鮑率,預設 115200 (Moxa 現場最常見)。
        bytesize: 資料位元數 (5-8),預設 8。
        parity: 同位位元 N/E/O/M/S,預設 N (None)。
        stopbits: 停止位元 1/1.5/2,預設 1。
        xonxoff: 軟體流量控制 (XON/XOFF),預設 False。
        rtscts: 硬體流量控制 (RTS/CTS),預設 False。
        dtr_on_open: 開啟連線時是否拉高 DTR,預設 True。
        rts_on_open: 開啟連線時是否拉高 RTS,預設 True。
        newline: Enter 鍵送出的行結束字元,預設 CR (\\r)。
    """

    type: SessionType = SessionType.SERIAL

    port: str
    baudrate: int = 115200
    bytesize: SerialBytesize = 8
    parity: SerialParity = "N"
    stopbits: SerialStopbits = 1
    xonxoff: bool = False

    @field_validator("stopbits")
    @classmethod
    def _validate_stopbits(cls, v: float) -> float:
        if v not in {1, 1.5, 2}:
            raise ValueError(f"stopbits must be 1, 1.5, or 2; got {v}")
        return v
    rtscts: bool = False
    dtr_on_open: bool = True
    rts_on_open: bool = True
    newline: SerialNewline = "CR"


# ---------------------------------------------------------------------------
# SshConfig
# ---------------------------------------------------------------------------

SshAuthMethod = Literal["password", "pubkey", "interactive"]


class SshConfig(SessionConfig):
    """SSH 終端機連線設定。

    結論:密碼與 passphrase 不在此模型,走 keyring。key_path 儲存金鑰
    檔案路徑 (str 或 None),由上層在建立連線前從磁碟載入。

    欄位:
        host: 遠端主機 IP 或 hostname。
        port: SSH 服務埠號,預設 22。
        username: 登入帳號。
        auth_method: 認證方式 password / pubkey / interactive,預設 password。
        key_path: 私鑰檔案路徑 (pubkey 認證時使用),None 表示不使用。
        known_hosts_path: known_hosts 檔路徑,None 則用預設 ~/.ssh/known_hosts。
        cols: 終端機欄寬,預設 80。
        rows: 終端機行高,預設 24。
        term_type: TERM 環境變數值,預設 xterm-256color。
    """

    type: SessionType = SessionType.SSH

    host: str
    port: int = 22
    username: str
    auth_method: SshAuthMethod = "password"
    key_path: str | None = None
    known_hosts_path: str | None = None
    cols: int = 80
    rows: int = 24
    term_type: str = "xterm-256color"

    def key_file_path(self) -> Path | None:
        """回傳 key_path 轉換後的 Path 物件;None 表示不使用金鑰。"""
        return Path(self.key_path) if self.key_path else None

    def known_hosts_file_path(self) -> Path:
        """回傳 known_hosts 路徑;未設定時回傳 ~/.ssh/known_hosts。"""
        if self.known_hosts_path:
            return Path(self.known_hosts_path)
        return Path.home() / ".ssh" / "known_hosts"


# ---------------------------------------------------------------------------
# Session ABC
# ---------------------------------------------------------------------------


class Session(ABC):
    """Session 抽象基類,定義所有連線類型的共用介面。

    結論:子類別 (SerialSession, SshSession, SftpSession) 必須實作
    connect / disconnect / write 三個方法及 is_connected 屬性。
    read 事件以 callback 或 asyncio Queue 傳遞 (由子類別決定)。
    本類別不持有任何 Qt 物件,確保 core/ 層無 Qt 依賴。

    使用方式:
        session = SerialSession(config)
        session.connect()
        session.write(b"\\r")
        session.disconnect()
    """

    def __init__(self, config: SessionConfig) -> None:
        self._config = config

    @property
    def config(self) -> SessionConfig:
        """回傳此 session 的設定物件 (唯讀)。"""
        return self._config

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """回傳目前是否已連線。"""

    @abstractmethod
    def connect(self) -> None:
        """建立連線。失敗時 raise TransportError (或其子類)。"""

    @abstractmethod
    def disconnect(self) -> None:
        """斷開連線;若未連線則 no-op。"""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """送出 bytes 到遠端;未連線時 raise RuntimeError。"""
