"""protocols/ssh_transport.py — SSH Shell (paramiko) 與 SFTP (asyncssh) 傳輸層。

結論先寫:
    - SshShellTransport:以 paramiko 同步 API 實作 SSH 互動 shell。
      建立連線、invoke_shell、channel recv/send/resize。
      Host key 策略:known_hosts 載入,未知主機 raise HostKeyMissingError 讓 UI 決定。
    - SftpTransport:以 asyncssh 非同步 API 實作 SFTP 檔案傳輸。
      listdir/stat/get/put/mkdir/remove;get/put 支援 throttled progress callback。
    - 所有連線例外包裝成 TransportError (與 serial_transport 共用型別)。
    - 無任何 Qt 依賴;SSH 呼叫端自行決定用 QThread 或 asyncio executor。

分層原則:本模組位於 protocols/,不得 import core/ 或任何 Qt 模組。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import asyncssh
import paramiko

from andyterm.protocols.serial_transport import TransportError

if TYPE_CHECKING:
    from andyterm.core.session import SshConfig

__all__ = [
    "HostKeyMissingError",
    "SftpTransport",
    "SshShellTransport",
    "ThrottledProgress",
]

# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------


class HostKeyMissingError(TransportError):
    """首次連線遇到未知 host key 時拋出,讓上層 UI 決定是否信任。

    結論:包含 hostname 與 key fingerprint 供 UI 顯示。
    """

    def __init__(self, hostname: str, key: paramiko.PKey) -> None:
        self.hostname = hostname
        self.key = key
        fp = key.get_fingerprint().hex(":")
        super().__init__(
            f"未知的主機 key {hostname} / Unknown host key for {hostname}: {fp}"
        )


# ---------------------------------------------------------------------------
# Host key policy
# ---------------------------------------------------------------------------


class _RaiseOnMissingPolicy(paramiko.MissingHostKeyPolicy):
    """未知 host key 時 raise HostKeyMissingError,由上層 UI 決定是否信任並儲存。"""

    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        raise HostKeyMissingError(hostname, key)


# ---------------------------------------------------------------------------
# 輔助: 載入私鑰
# ---------------------------------------------------------------------------


def _load_pkey(path: Path, passphrase: str | None = None) -> paramiko.PKey:
    """嘗試 Ed25519 / ECDSA / RSA 依序載入私鑰。

    結論:三種格式依安全強度優先順序嘗試;全部失敗才 raise ValueError。

    回傳:
        paramiko.PKey 子類實例。
    """
    for key_class in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_class.from_private_key_file(
                str(path),
                password=passphrase,
            )
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError(
        f"不支援的私鑰格式 / Unsupported private key format: {path}"
    )


# ---------------------------------------------------------------------------
# SshShellTransport
# ---------------------------------------------------------------------------


class SshShellTransport:
    """paramiko SSH 互動 Shell 傳輸層。

    結論:
        - connect() 建立 SSHClient 連線並啟用 keepalive (30s)。
        - invoke_shell() 回傳 paramiko.Channel (non-blocking),供 read loop 使用。
        - 所有 paramiko 例外轉為 TransportError。
        - 未知 host key 拋 HostKeyMissingError;上層呼叫 accept_host_key() 後可重試。

    參數:
        config: SshConfig。
        password: 登入密碼 (從 keyring 取得);使用 pubkey 時可傳 None。
        passphrase: 私鑰 passphrase (從 keyring 取得);無 passphrase 時傳 None。
    """

    KEEPALIVE_INTERVAL = 30

    def __init__(
        self,
        config: SshConfig,
        password: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        self._config = config
        self._password = password
        self._passphrase = passphrase
        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None

    # ------------------------------------------------------------------
    # 連線管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """建立 SSH 連線。

        結論:載入 known_hosts,未知主機拋 HostKeyMissingError。
        連線成功後啟用 30s keepalive。
        """
        client = paramiko.SSHClient()

        known_hosts = self._config.known_hosts_file_path()
        if known_hosts.exists():
            with contextlib.suppress(OSError):
                client.load_host_keys(str(known_hosts))

        client.set_missing_host_key_policy(_RaiseOnMissingPolicy())

        pkey: paramiko.PKey | None = None
        if self._config.auth_method == "pubkey":
            key_path = self._config.key_file_path()
            if key_path:
                pkey = _load_pkey(key_path, self._passphrase)

        try:
            client.connect(
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._password,
                pkey=pkey,
                timeout=10,
                auth_timeout=15,
                banner_timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )
        except HostKeyMissingError:
            # 必須在 OSError 之前捕捉 — HostKeyMissingError 繼承 TransportError → OSError
            raise
        except paramiko.AuthenticationException as exc:
            raise TransportError(
                f"認證失敗 / Authentication failed for {self._config.username}"
                f"@{self._config.host}: {exc}"
            ) from exc
        except paramiko.SSHException as exc:
            raise TransportError(
                f"SSH 協定錯誤 / SSH error connecting to {self._config.host}: {exc}"
            ) from exc
        except OSError as exc:
            raise TransportError(
                f"網路錯誤 / Network error connecting to {self._config.host}: {exc}"
            ) from exc

        transport = client.get_transport()
        if transport:
            transport.set_keepalive(self.KEEPALIVE_INTERVAL)

        self._client = client

    def disconnect(self) -> None:
        """關閉 channel 與 SSH 連線;若未連線則 no-op。"""
        if self._channel:
            with contextlib.suppress(Exception):
                self._channel.close()
            self._channel = None
        if self._client:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    @property
    def is_connected(self) -> bool:
        """回傳 SSH transport 是否仍存活。"""
        if not self._client:
            return False
        t = self._client.get_transport()
        return t is not None and t.is_active()

    # ------------------------------------------------------------------
    # Shell channel
    # ------------------------------------------------------------------

    def invoke_shell(
        self,
        cols: int | None = None,
        rows: int | None = None,
        term: str = "xterm-256color",
    ) -> paramiko.Channel:
        """開啟互動 shell channel。

        結論:channel 設為 non-blocking (timeout=0.0),供呼叫端自行 select/loop。

        參數:
            cols: PTY 欄寬,預設使用 config.cols。
            rows: PTY 行高,預設使用 config.rows。
            term: TERM 環境變數值。

        回傳:
            paramiko.Channel (non-blocking)。
        """
        if not self._client:
            raise TransportError("SSH 未連線 / SSH not connected")

        w = cols if cols is not None else self._config.cols
        h = rows if rows is not None else self._config.rows

        try:
            channel = self._client.invoke_shell(term=term, width=w, height=h)
        except paramiko.SSHException as exc:
            raise TransportError(
                f"無法開啟 shell / Cannot invoke shell: {exc}"
            ) from exc

        channel.settimeout(0.0)
        self._channel = channel
        return channel

    def resize_pty(self, cols: int, rows: int) -> None:
        """通知遠端 PTY 尺寸改變 (視窗 resize 時呼叫)。

        參數:
            cols: 新欄寬。
            rows: 新行高。
        """
        if self._channel:
            with contextlib.suppress(paramiko.SSHException):
                self._channel.resize_pty(width=cols, height=rows)

    def accept_host_key(self, key: paramiko.PKey, save: bool = True) -> None:
        """使用者確認信任 host key 後呼叫,儲存至 known_hosts。

        結論:寫入後下次連線不再觸發 HostKeyMissingError。

        參數:
            key: HostKeyMissingError.key 欄位。
            save: 是否立即寫回 known_hosts 檔案 (預設 True)。
        """
        if not self._client:
            return
        hostname = self._config.host
        self._client.get_host_keys().add(hostname, key.get_name(), key)
        if save:
            known_hosts = self._config.known_hosts_file_path()
            known_hosts.parent.mkdir(parents=True, exist_ok=True)
            self._client.save_host_keys(str(known_hosts))


# ---------------------------------------------------------------------------
# ThrottledProgress
# ---------------------------------------------------------------------------


class ThrottledProgress:
    """節流進度回呼,避免 progress signal 洪水。

    結論:至少隔 min_interval 秒才呼叫一次 callback;傳輸完成時 (current==total)
    必定呼叫一次。

    參數:
        callback: (current_bytes: int, total_bytes: int) -> None。
        min_interval: 最小呼叫間隔秒數,預設 0.1 (100ms)。
    """

    def __init__(
        self,
        callback: Callable[[int, int], None],
        min_interval: float = 0.1,
    ) -> None:
        self._cb = callback
        self._min = min_interval
        self._last: float = 0.0

    def __call__(self, _src: bytes, _dst: bytes, current: int, total: int) -> None:
        now = time.monotonic()
        if now - self._last >= self._min or current == total:
            self._last = now
            self._cb(current, total)


# ---------------------------------------------------------------------------
# SftpTransport
# ---------------------------------------------------------------------------


class SftpTransport:
    """asyncssh SFTP 檔案傳輸層。

    結論:
        - connect() / close() 為 async,搭配 qasync 或 asyncio.run 使用。
        - listdir / stat / get / put / mkdir / remove 提供常用 SFTP 操作。
        - get / put 支援 ThrottledProgress callback,避免 UI 洪水更新。
        - 未跳過 host key 驗證;known_hosts 路徑由 config 決定。
        - Jump host 留 TODO,v1.0 不實作。

    參數:
        config: SshConfig。
        password: 登入密碼 (從 keyring 取得);pubkey 認證時可傳 None。
        passphrase: 私鑰 passphrase;無 passphrase 時傳 None。
    """

    def __init__(
        self,
        config: SshConfig,
        password: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        self._config = config
        self._password = password
        self._passphrase = passphrase
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None

    # ------------------------------------------------------------------
    # 連線管理
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """建立 asyncssh 連線並開啟 SFTP client。

        結論:known_hosts 存在時驗證,否則 raise TransportError 而非靜默接受。
        """
        known_hosts_path = self._config.known_hosts_file_path()
        known_hosts: str | None = (
            str(known_hosts_path) if known_hosts_path.exists() else None
        )

        client_keys: list[str] = []
        if self._config.auth_method == "pubkey" and self._config.key_path:
            client_keys = [self._config.key_path]

        try:
            self._conn = await asyncssh.connect(
                host=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._password,
                client_keys=client_keys or None,
                passphrase=self._passphrase,
                known_hosts=known_hosts,
                connect_timeout=10,
                # 大視窗加速大檔傳輸 (asyncssh 預設偏小)
                window=2**24,
            )
            self._sftp = await self._conn.start_sftp_client(
                path_encoding="utf-8",
            )
        except asyncssh.DisconnectError as exc:
            raise TransportError(
                f"SSH 連線中斷 / SSH disconnected: {exc}"
            ) from exc
        except asyncssh.PermissionDenied as exc:
            raise TransportError(
                f"認證失敗 / Authentication failed: {exc}"
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise TransportError(
                f"SFTP 連線失敗 / SFTP connect error: {exc}"
            ) from exc

    async def close(self) -> None:
        """關閉 SFTP client 與 SSH 連線。"""
        if self._sftp:
            self._sftp.exit()
            self._sftp = None
        if self._conn:
            self._conn.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._conn.wait_closed(), timeout=5)
            self._conn = None

    @property
    def is_connected(self) -> bool:
        """回傳 SFTP 連線是否仍存活。"""
        return self._conn is not None and not self._conn.is_closed()

    # ------------------------------------------------------------------
    # SFTP 操作
    # ------------------------------------------------------------------

    def _require_sftp(self) -> asyncssh.SFTPClient:
        if not self._sftp:
            raise TransportError("SFTP 未連線 / SFTP not connected")
        return self._sftp

    async def listdir(self, remote_path: str = ".") -> Sequence[asyncssh.SFTPName]:
        """列出遠端目錄內容。

        回傳:
            asyncssh.SFTPName 物件列表 (含 filename / attrs)。
        """
        sftp = self._require_sftp()
        try:
            return await sftp.readdir(remote_path)
        except asyncssh.SFTPError as exc:
            raise TransportError(
                f"列目錄失敗 / listdir error {remote_path}: {exc}"
            ) from exc

    async def stat(self, remote_path: str) -> asyncssh.SFTPAttrs:
        """取得遠端路徑的 stat 屬性 (size / mtime / permissions)。"""
        sftp = self._require_sftp()
        try:
            return await sftp.stat(remote_path)
        except asyncssh.SFTPError as exc:
            raise TransportError(
                f"stat 失敗 / stat error {remote_path}: {exc}"
            ) from exc

    async def get(
        self,
        remote_path: str,
        local_path: Path,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> None:
        """從遠端下載檔案到本機。

        參數:
            remote_path: 遠端來源路徑。
            local_path: 本機目的路徑 (Path)。
            progress_cb: (current_bytes, total_bytes) 進度回呼 (可選)。
        """
        sftp = self._require_sftp()
        handler = ThrottledProgress(progress_cb) if progress_cb else None
        try:
            await sftp.get(
                remote_path,
                str(local_path),
                progress_handler=handler,
            )
        except asyncssh.SFTPError as exc:
            raise TransportError(
                f"下載失敗 / Download error {remote_path}: {exc}"
            ) from exc

    async def put(
        self,
        local_path: Path,
        remote_path: str,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> None:
        """上傳本機檔案到遠端。

        參數:
            local_path: 本機來源路徑 (Path)。
            remote_path: 遠端目的路徑。
            progress_cb: (current_bytes, total_bytes) 進度回呼 (可選)。
        """
        sftp = self._require_sftp()
        handler = ThrottledProgress(progress_cb) if progress_cb else None
        try:
            await sftp.put(
                str(local_path),
                remote_path,
                progress_handler=handler,
            )
        except asyncssh.SFTPError as exc:
            raise TransportError(
                f"上傳失敗 / Upload error {local_path}: {exc}"
            ) from exc

    async def mkdir(self, remote_path: str) -> None:
        """在遠端建立目錄 (含 parents,類似 mkdir -p)。"""
        sftp = self._require_sftp()
        try:
            await sftp.makedirs(remote_path, exist_ok=True)
        except asyncssh.SFTPError as exc:
            raise TransportError(
                f"建立目錄失敗 / mkdir error {remote_path}: {exc}"
            ) from exc

    async def remove(self, remote_path: str) -> None:
        """刪除遠端檔案 (不支援目錄)。"""
        sftp = self._require_sftp()
        try:
            await sftp.remove(remote_path)
        except asyncssh.SFTPError as exc:
            raise TransportError(
                f"刪除失敗 / remove error {remote_path}: {exc}"
            ) from exc
