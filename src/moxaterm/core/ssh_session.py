"""core/ssh_session.py — SSH 終端機 Session 實作。

結論先寫:
    - SshSession 持有 SshShellTransport + AnsiTerminal + paramiko.Channel,
      組合成可直接使用的 SSH 互動 shell 連線物件。
    - connect() 建立 SSH 連線並開啟 shell channel;channel 為 non-blocking。
    - read_once() 供讀取迴圈單次呼叫 (由 SshWorker / QThread 執行)。
    - resize() 同時更新 AnsiTerminal 與遠端 PTY 尺寸。
    - 密碼由呼叫端傳入 (從 keyring 取得),不在本類別中存取 keyring。

分層原則:本模組位於 core/,不得 import 任何 Qt 模組。
"""

from __future__ import annotations

from collections.abc import Callable

import paramiko

from moxaterm.core.ansi_parser import AnsiTerminal
from moxaterm.core.session import Session, SessionConfig, SshConfig
from moxaterm.protocols.ssh_transport import HostKeyMissingError, SshShellTransport

__all__ = ["SshSession"]


class SshSession(Session):
    """SSH 互動終端機 Session。

    結論:
        - 建構子接受 SshConfig + 選用的 password / passphrase。
        - connect() 後呼叫 invoke_shell(),channel 存為 self._channel。
        - read_once() 從 channel recv,餵給 AnsiTerminal,觸發 on_data_callback。
        - resize(cols, rows) 同步更新 AnsiTerminal 與 PTY。
        - on_host_key_missing 供 UI 層在首次連線時確認 host key。

    參數:
        config: SshConfig。
        password: SSH 登入密碼 (從 keyring 取得);pubkey 認證時傳 None。
        passphrase: 私鑰 passphrase;無 passphrase 時傳 None。
        on_data_callback: (data: bytes) -> None,UI 層收到資料時的回呼 (可選)。
    """

    RECV_BUFFER = 4096

    def __init__(
        self,
        config: SshConfig,
        password: str | None = None,
        passphrase: str | None = None,
        on_data_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(config)
        self._transport = SshShellTransport(config, password, passphrase)
        self._terminal = AnsiTerminal(cols=config.cols, rows=config.rows)
        self._channel: paramiko.Channel | None = None
        self._on_data = on_data_callback

    # ------------------------------------------------------------------
    # Session ABC 實作
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return (
            self._transport.is_connected
            and self._channel is not None
            and not self._channel.closed
        )

    def connect(self) -> None:
        """建立 SSH 連線並開啟 shell channel。

        結論:HostKeyMissingError 不在此攔截 — 直接向上傳播讓 UI 層處理。
        """
        self._transport.connect()
        ssh_cfg = self._ssh_config
        self._channel = self._transport.invoke_shell(
            cols=ssh_cfg.cols,
            rows=ssh_cfg.rows,
            term=ssh_cfg.term_type,
        )

    def disconnect(self) -> None:
        """關閉 channel 與 SSH 連線;若未連線則 no-op。"""
        self._channel = None
        self._transport.disconnect()

    def write(self, data: bytes) -> None:
        """送出 bytes 到 SSH channel。未連線時 raise RuntimeError。"""
        if not self._channel or self._channel.closed:
            raise RuntimeError(
                "SSH 未連線 / SSH session not connected"
            )
        self._channel.send(data)

    # ------------------------------------------------------------------
    # 讀取迴圈 (由 SshWorker 在 QThread 中呼叫)
    # ------------------------------------------------------------------

    def read_once(self) -> bytes:
        """從 channel 讀取一批資料 (non-blocking)。

        結論:channel 為 non-blocking (timeout=0),無資料時回 b""。
        有資料時更新 AnsiTerminal 並觸發 on_data_callback。

        回傳:
            讀到的原始 bytes (可能為空)。
        """
        if not self._channel or self._channel.closed:
            return b""
        if not self._channel.recv_ready():
            return b""
        data = self._channel.recv(self.RECV_BUFFER)
        if data:
            self._terminal.feed(data)
            if self._on_data:
                self._on_data(data)
        return data

    # ------------------------------------------------------------------
    # 額外操作
    # ------------------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        """同步調整 AnsiTerminal 與遠端 PTY 尺寸。

        參數:
            cols: 新欄寬。
            rows: 新行高。
        """
        self._terminal.resize(cols, rows)
        self._transport.resize_pty(cols, rows)

    def accept_host_key(self, key: paramiko.PKey, save: bool = True) -> None:
        """使用者確認信任 host key 後呼叫,儲存至 known_hosts。

        結論:呼叫後再次呼叫 connect() 即可完成連線。

        參數:
            key: HostKeyMissingError.key 欄位。
            save: 是否立即寫回 known_hosts 檔案。
        """
        self._transport.accept_host_key(key, save)

    def register_callback(self, callback: Callable[[bytes], None]) -> None:
        """替換資料回呼;供 UI 層在建構後延遲綁定。"""
        self._on_data = callback

    # ------------------------------------------------------------------
    # 屬性
    # ------------------------------------------------------------------

    @property
    def terminal(self) -> AnsiTerminal:
        """回傳內部 AnsiTerminal 實例。"""
        return self._terminal

    @property
    def config(self) -> SessionConfig:
        return self._config

    @property
    def _ssh_config(self) -> SshConfig:
        return self._config  # type: ignore[return-value]

    # 重新 export 讓 UI 層可以 except HostKeyMissingError
    HostKeyMissingError = HostKeyMissingError
