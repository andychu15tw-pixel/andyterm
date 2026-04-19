"""core/sftp_session.py — SFTP 檔案傳輸 Session 實作。

結論先寫:
    - SftpSession 包裝 SftpTransport,提供有狀態的 SFTP 操作介面。
    - remote_cwd 追蹤目前遠端工作目錄 (預設 ".")。
    - navigate(path) 切換目錄並列出內容。
    - list_current() 列出 remote_cwd 的檔案。
    - download(remote_name, local_dir) 下載單一檔案到本機目錄。
    - 全部 async — 搭配 qasync / asyncio 使用。

分層原則:本模組位於 core/,不得 import 任何 Qt 模組。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

import asyncssh

from moxaterm.core.session import Session, SessionConfig, SshConfig
from moxaterm.protocols.ssh_transport import SftpTransport

__all__ = ["SftpSession"]


class SftpSession(Session):
    """SFTP 檔案傳輸 Session。

    結論:
        - 建構子接受 SshConfig + 選用的 password / passphrase。
        - connect() / disconnect() 為 async,必須在 asyncio event loop 中呼叫。
        - remote_cwd 追蹤遠端目前目錄;navigate() 切換目錄。
        - download / upload 支援 throttled progress callback。
        - is_connected 為同步屬性,可安全在 UI thread 查詢。

    參數:
        config: SshConfig (SFTP 共用 SSH 連線設定)。
        password: SSH 密碼 (從 keyring 取得)。
        passphrase: 私鑰 passphrase。
    """

    def __init__(
        self,
        config: SshConfig,
        password: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        super().__init__(config)
        self._sftp = SftpTransport(config, password, passphrase)
        self._remote_cwd: str = "."

    # ------------------------------------------------------------------
    # Session ABC 實作 (同步部分)
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._sftp.is_connected

    def connect(self) -> None:
        """同步版 connect 禁用;請呼叫 async_connect()。"""
        raise RuntimeError(
            "SftpSession 使用 async_connect() / Use async_connect() for SFTP"
        )

    def disconnect(self) -> None:
        """同步版 disconnect 禁用;請呼叫 async_close()。"""
        raise RuntimeError(
            "SftpSession 使用 async_close() / Use async_close() for SFTP"
        )

    def write(self, data: bytes) -> None:
        """SFTP 不支援 write();請使用 upload()。"""
        raise NotImplementedError(
            "SFTP session 不支援 write() / Use upload() for SFTP file transfer"
        )

    # ------------------------------------------------------------------
    # 非同步連線管理
    # ------------------------------------------------------------------

    async def async_connect(self) -> None:
        """建立 SFTP 連線 (async)。失敗時 TransportError 向上傳播。"""
        await self._sftp.connect()
        # 取得遠端 home 目錄作為初始 cwd
        with contextlib.suppress(Exception):
            await self._sftp.listdir(".")

    async def async_close(self) -> None:
        """關閉 SFTP 連線 (async)。"""
        await self._sftp.close()

    # ------------------------------------------------------------------
    # 目錄導航
    # ------------------------------------------------------------------

    @property
    def remote_cwd(self) -> str:
        """目前遠端工作目錄。"""
        return self._remote_cwd

    async def navigate(self, path: str) -> Sequence[asyncssh.SFTPName]:
        """切換遠端目錄並回傳目錄內容。

        結論:若 path 是相對路徑,以目前 remote_cwd 為基準組合。
        切換成功後更新 remote_cwd。

        參數:
            path: 目標目錄 (絕對或相對路徑)。

        回傳:
            目錄內容 (asyncssh.SFTPName 列表)。
        """
        if path == "..":
            target = str(Path(self._remote_cwd).parent)
        elif path.startswith("/"):
            target = path
        else:
            target = str(Path(self._remote_cwd) / path)

        entries = await self._sftp.listdir(target)
        self._remote_cwd = target
        return entries

    async def list_current(self) -> Sequence[asyncssh.SFTPName]:
        """列出目前目錄 (remote_cwd) 的內容。

        回傳:
            asyncssh.SFTPName 物件列表。
        """
        return await self._sftp.listdir(self._remote_cwd)

    # ------------------------------------------------------------------
    # 檔案傳輸
    # ------------------------------------------------------------------

    async def download(
        self,
        remote_name: str,
        local_dir: Path,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> Path:
        """下載 remote_cwd 下的 remote_name 到 local_dir。

        結論:remote_name 為檔名 (非完整路徑),自動組合 remote_cwd。

        參數:
            remote_name: 遠端檔名 (相對於 remote_cwd)。
            local_dir: 本機目的目錄 (Path)。
            progress_cb: (current_bytes, total_bytes) 進度回呼。

        回傳:
            本機目的完整路徑。
        """
        remote_path = str(Path(self._remote_cwd) / remote_name)
        local_path = local_dir / remote_name
        await self._sftp.get(remote_path, local_path, progress_cb)
        return local_path

    async def upload(
        self,
        local_path: Path,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> None:
        """上傳 local_path 到目前 remote_cwd。

        參數:
            local_path: 本機來源路徑。
            progress_cb: (current_bytes, total_bytes) 進度回呼。
        """
        remote_path = str(Path(self._remote_cwd) / local_path.name)
        await self._sftp.put(local_path, remote_path, progress_cb)

    async def mkdir(self, name: str) -> None:
        """在 remote_cwd 下建立目錄。"""
        await self._sftp.mkdir(str(Path(self._remote_cwd) / name))

    async def remove(self, remote_name: str) -> None:
        """刪除 remote_cwd 下的檔案。"""
        await self._sftp.remove(str(Path(self._remote_cwd) / remote_name))

    async def stat(self, remote_name: str) -> asyncssh.SFTPAttrs:
        """取得 remote_cwd 下指定名稱的 stat 屬性。"""
        return await self._sftp.stat(str(Path(self._remote_cwd) / remote_name))

    # ------------------------------------------------------------------
    # 屬性
    # ------------------------------------------------------------------

    @property
    def config(self) -> SessionConfig:
        return self._config

    @property
    def _ssh_config(self) -> SshConfig:
        return self._config  # type: ignore[return-value]
