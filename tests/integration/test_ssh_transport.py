"""tests/integration/test_ssh_transport.py — SshShellTransport / SftpTransport 整合測試。

結論先寫:
    - 全部使用 pytest-mock 模擬 paramiko / asyncssh,不建立真實 SSH 連線。
    - asyncio_mode = "auto" (pyproject.toml),async def test_ 無需 @pytest.mark.asyncio。
    - 覆蓋 HostKeyMissingError、ThrottledProgress、SshShellTransport、SftpTransport。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import paramiko
import pytest

from andyterm.core.session import SshConfig
from andyterm.protocols.serial_transport import TransportError
from andyterm.protocols.ssh_transport import (
    HostKeyMissingError,
    SftpTransport,
    SshShellTransport,
    ThrottledProgress,
)

# ---------------------------------------------------------------------------
# 輔助 fixture
# ---------------------------------------------------------------------------


def _make_ssh_config(**overrides: object) -> SshConfig:
    """建立最小可用的 SshConfig,允許覆蓋任意欄位。"""
    defaults: dict[str, object] = {
        "name": "test-ssh",
        "host": "192.168.0.1",
        "port": 22,
        "username": "admin",
        "auth_method": "password",
        "known_hosts_path": "/nonexistent/known_hosts",  # 確保不載入真實檔案
    }
    defaults.update(overrides)
    return SshConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HostKeyMissingError
# ---------------------------------------------------------------------------


class TestHostKeyMissingError:
    def test_host_key_missing_error_is_transport_error(self) -> None:
        """HostKeyMissingError 必須是 TransportError 的子類。"""
        assert issubclass(HostKeyMissingError, TransportError)

    def test_host_key_missing_error_has_hostname_and_key(self) -> None:
        """HostKeyMissingError 實例應正確設置 hostname 與 key 屬性。"""
        mock_key = MagicMock(spec=paramiko.PKey)
        mock_key.get_fingerprint.return_value = b"\xde\xad\xbe\xef"

        err = HostKeyMissingError("192.168.0.1", mock_key)

        assert err.hostname == "192.168.0.1"
        assert err.key is mock_key


# ---------------------------------------------------------------------------
# ThrottledProgress
# ---------------------------------------------------------------------------


class TestThrottledProgress:
    def test_throttled_progress_calls_cb_immediately(self) -> None:
        """第一次呼叫 (last=0) 應立即觸發 callback,因為距上次已超過 min_interval。"""
        cb = MagicMock()
        # min_interval 設很大,但第一次 last=0 距 now 必定超過 min_interval
        progress = ThrottledProgress(cb, min_interval=0.1)

        progress(b"src", b"dst", 100, 1000)

        cb.assert_called_once_with(100, 1000)

    def test_throttled_progress_throttles(self) -> None:
        """min_interval 內連續呼叫兩次,callback 只被呼叫一次。"""
        cb = MagicMock()
        progress = ThrottledProgress(cb, min_interval=10.0)  # 設定超大間隔

        # 第一次呼叫 (從 last=0 起算必定超過 10s,故觸發)
        progress(b"", b"", 100, 1000)
        cb.reset_mock()

        # 第二次立即呼叫,距第一次不到 10s,且非完成,不應觸發
        progress(b"", b"", 200, 1000)

        cb.assert_not_called()

    def test_throttled_progress_calls_on_completion(self) -> None:
        """current == total 時,無論 interval 多大都必定呼叫 callback。"""
        cb = MagicMock()
        progress = ThrottledProgress(cb, min_interval=10.0)

        # 第一次觸發 (初始化)
        progress(b"", b"", 500, 1000)
        cb.reset_mock()

        # 立即呼叫完成事件 (current == total),必須觸發
        progress(b"", b"", 1000, 1000)

        cb.assert_called_once_with(1000, 1000)

    def test_throttled_progress_ignores_src_dst(self) -> None:
        """callback 只接收 current 與 total,src/dst bytes 不應傳入。"""
        received: list[tuple[int, int]] = []

        def cb(current: int, total: int) -> None:
            received.append((current, total))

        progress = ThrottledProgress(cb, min_interval=0.0)
        progress(b"source_path", b"dest_path", 42, 100)

        assert received == [(42, 100)]


# ---------------------------------------------------------------------------
# SshShellTransport
# ---------------------------------------------------------------------------


class TestSshShellTransport:
    def test_is_connected_false_before_connect(self) -> None:
        """connect() 前 is_connected 應回傳 False。"""
        transport = SshShellTransport(_make_ssh_config())
        assert transport.is_connected is False

    def test_disconnect_before_connect_noop(self) -> None:
        """未連線就呼叫 disconnect() 不應拋出任何例外。"""
        transport = SshShellTransport(_make_ssh_config())
        transport.disconnect()  # 不應拋出

    def test_connect_raises_transport_error_on_auth_failure(
        self, mocker: MagicMock
    ) -> None:
        """paramiko 拋出 AuthenticationException 時,connect() 應包裝成 TransportError。"""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = paramiko.AuthenticationException("bad creds")
        mocker.patch("paramiko.SSHClient", return_value=mock_client)

        # known_hosts 不存在,跳過載入
        mocker.patch.object(Path, "exists", return_value=False)

        transport = SshShellTransport(_make_ssh_config(), password="wrong")
        with pytest.raises(TransportError):
            transport.connect()

    def test_connect_raises_transport_error_on_oserror(
        self, mocker: MagicMock
    ) -> None:
        """網路 OSError 時,connect() 應包裝成 TransportError。"""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = OSError("connection refused")
        mocker.patch("paramiko.SSHClient", return_value=mock_client)
        mocker.patch.object(Path, "exists", return_value=False)

        transport = SshShellTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            transport.connect()

    def test_connect_raises_host_key_missing(self, mocker: MagicMock) -> None:
        """connect() 遇到未知 host key 時應傳播 HostKeyMissingError (不被 OSError 吞掉)。

        實作細節:except HostKeyMissingError: raise 排在 except OSError 之前,
        確保呼叫端能區分「未知 host key」與一般網路錯誤。
        """
        mock_key = MagicMock(spec=paramiko.PKey)
        mock_key.get_fingerprint.return_value = b"\x00\x01\x02\x03"

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.side_effect = HostKeyMissingError("192.168.0.1", mock_key)
        mocker.patch("paramiko.SSHClient", return_value=mock_client)
        mocker.patch.object(Path, "exists", return_value=False)

        transport = SshShellTransport(_make_ssh_config())
        with pytest.raises(HostKeyMissingError) as exc_info:
            transport.connect()
        assert exc_info.value.hostname == "192.168.0.1"

    def test_is_connected_after_successful_connect(self, mocker: MagicMock) -> None:
        """成功連線後,mock transport.is_active()=True,is_connected 應回傳 True。"""
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.connect.return_value = None
        mock_client.get_transport.return_value = mock_transport
        mocker.patch("paramiko.SSHClient", return_value=mock_client)
        mocker.patch.object(Path, "exists", return_value=False)

        transport = SshShellTransport(_make_ssh_config())
        transport.connect()

        assert transport.is_connected is True

    def test_invoke_shell_raises_if_not_connected(self) -> None:
        """未連線時呼叫 invoke_shell() 應拋出 TransportError。"""
        transport = SshShellTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            transport.invoke_shell()

    def test_resize_pty_noop_if_no_channel(self) -> None:
        """無 channel 時呼叫 resize_pty() 不應拋出任何例外。"""
        transport = SshShellTransport(_make_ssh_config())
        transport.resize_pty(80, 24)  # 不應拋出


# ---------------------------------------------------------------------------
# SftpTransport
# ---------------------------------------------------------------------------


class TestSftpTransport:
    def test_is_connected_false_initially(self) -> None:
        """初始狀態 is_connected 應回傳 False。"""
        transport = SftpTransport(_make_ssh_config())
        assert transport.is_connected is False

    async def test_connect_raises_transport_error_on_permission_denied(
        self, mocker: MagicMock
    ) -> None:
        """asyncssh.PermissionDenied 時,connect() 應包裝成 TransportError。"""
        import asyncssh

        mocker.patch(
            "asyncssh.connect",
            side_effect=asyncssh.PermissionDenied(reason="bad password"),
        )
        mocker.patch.object(Path, "exists", return_value=False)

        transport = SftpTransport(_make_ssh_config(), password="wrong")
        with pytest.raises(TransportError):
            await transport.connect()

    async def test_connect_raises_on_oserror(self, mocker: MagicMock) -> None:
        """asyncssh.connect 拋出 OSError 時,connect() 應包裝成 TransportError。"""
        mocker.patch(
            "asyncssh.connect",
            side_effect=OSError("network unreachable"),
        )
        mocker.patch.object(Path, "exists", return_value=False)

        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.connect()

    async def test_listdir_raises_if_not_connected(self) -> None:
        """未連線時 listdir() 應拋出 TransportError。"""
        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.listdir("/")

    async def test_stat_raises_if_not_connected(self) -> None:
        """未連線時 stat() 應拋出 TransportError。"""
        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.stat("/etc/hostname")

    async def test_get_raises_if_not_connected(self) -> None:
        """未連線時 get() 應拋出 TransportError。"""
        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.get("/remote/file", Path("/tmp/local_file"))

    async def test_put_raises_if_not_connected(self) -> None:
        """未連線時 put() 應拋出 TransportError。"""
        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.put(Path("/tmp/local_file"), "/remote/file")

    async def test_mkdir_raises_if_not_connected(self) -> None:
        """未連線時 mkdir() 應拋出 TransportError。"""
        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.mkdir("/remote/newdir")

    async def test_remove_raises_if_not_connected(self) -> None:
        """未連線時 remove() 應拋出 TransportError。"""
        transport = SftpTransport(_make_ssh_config())
        with pytest.raises(TransportError):
            await transport.remove("/remote/file.txt")
