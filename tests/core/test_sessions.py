"""tests/core/test_sessions.py — L1 單元測試:SerialSession、SshSession、SftpSession、UPortInfo。

全部 mock transport — 不需要實際序列埠或 SSH 連線。
asyncio_mode = "auto" 已在 pyproject.toml 設定,不需 @pytest.mark.asyncio。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from andyterm.core.ansi_parser import AnsiTerminal
from andyterm.core.serial_session import SerialSession
from andyterm.core.session import SerialConfig, SshConfig
from andyterm.core.sftp_session import SftpSession
from andyterm.core.ssh_session import SshSession
from andyterm.moxa.uport_info import MOXA_PID_MAP, UPortInfo, scan_moxa_uport

# ---------------------------------------------------------------------------
# Helpers / 共用 fixtures
# ---------------------------------------------------------------------------


def make_serial_config(**kwargs) -> SerialConfig:
    defaults = dict(name="test-serial", port="COM1")
    defaults.update(kwargs)
    return SerialConfig(**defaults)


def make_ssh_config(**kwargs) -> SshConfig:
    defaults = dict(name="test-ssh", host="127.0.0.1", username="user")
    defaults.update(kwargs)
    return SshConfig(**defaults)


# ---------------------------------------------------------------------------
# TestSerialSession (10 tests)
# ---------------------------------------------------------------------------


class TestSerialSession:
    """SerialSession 的 L1 單元測試;transport 全 mock。"""

    @pytest.fixture
    def mock_transport(self, mocker):
        """patch SerialTransport 建構子,回傳 MagicMock。"""
        mock = MagicMock()
        mocker.patch(
            "andyterm.core.serial_session.SerialTransport",
            return_value=mock,
        )
        return mock

    @pytest.fixture
    def session(self, mock_transport) -> SerialSession:
        return SerialSession(make_serial_config())

    # --- 1 ---
    def test_is_connected_delegates_to_transport(self, mock_transport):
        mock_transport.is_open = False
        session = SerialSession(make_serial_config())
        assert session.is_connected is False

    # --- 2 ---
    def test_connect_calls_transport_open(self, session, mock_transport):
        session.connect()
        mock_transport.open.assert_called_once()

    # --- 3 ---
    def test_disconnect_calls_transport_close(self, session, mock_transport):
        session.disconnect()
        mock_transport.close.assert_called_once()

    # --- 4 ---
    def test_write_calls_transport_write(self, session, mock_transport):
        session.write(b"hello")
        mock_transport.write.assert_called_once_with(b"hello")

    # --- 5 ---
    def test_read_once_empty_returns_empty(self, session, mock_transport):
        mock_transport.read.return_value = b""
        callback = MagicMock()
        session.register_callback(callback)
        result = session.read_once()
        assert result == b""
        callback.assert_not_called()

    # --- 6 ---
    def test_read_once_feeds_terminal_and_calls_callback(self, session, mock_transport):
        mock_transport.read.return_value = b"hello"
        callback = MagicMock()
        session.register_callback(callback)
        result = session.read_once()
        assert result == b"hello"
        callback.assert_called_once_with(b"hello")
        # terminal 應已被餵入資料
        assert "hello" in session.terminal.get_line(0)

    # --- 7 ---
    def test_send_break_delegates(self, session, mock_transport):
        session.send_break(0.5)
        mock_transport.send_break.assert_called_once_with(0.5)

    # --- 8 ---
    def test_register_callback_replaces(self, session, mock_transport):
        old_cb = MagicMock()
        new_cb = MagicMock()
        session.register_callback(old_cb)
        session.register_callback(new_cb)
        mock_transport.read.return_value = b"x"
        session.read_once()
        old_cb.assert_not_called()
        new_cb.assert_called_once_with(b"x")

    # --- 9 ---
    def test_resize_terminal_updates_terminal(self, session):
        session.resize_terminal(40, 12)
        display = session.terminal.get_display()
        assert len(display) == 12

    # --- 10 ---
    def test_terminal_property_returns_ansi_terminal(self, session):
        assert isinstance(session.terminal, AnsiTerminal)


# ---------------------------------------------------------------------------
# TestSshSession (10 tests)
# ---------------------------------------------------------------------------


class TestSshSession:
    """SshSession 的 L1 單元測試;SshShellTransport 全 mock。"""

    @pytest.fixture
    def mock_transport(self, mocker):
        mock = MagicMock()
        mock.is_connected = False
        mocker.patch(
            "andyterm.core.ssh_session.SshShellTransport",
            return_value=mock,
        )
        return mock

    @pytest.fixture
    def session(self, mock_transport) -> SshSession:
        return SshSession(make_ssh_config())

    @pytest.fixture
    def connected_session(self, mock_transport) -> SshSession:
        """建立已連線狀態的 SshSession:transport.is_connected=True + channel mock。"""
        mock_channel = MagicMock()
        mock_channel.closed = False
        mock_channel.recv_ready.return_value = False
        mock_transport.is_connected = True
        mock_transport.invoke_shell.return_value = mock_channel
        sess = SshSession(make_ssh_config())
        sess.connect()
        return sess

    # --- 1 ---
    def test_is_connected_false_before_connect(self, session, mock_transport):
        mock_transport.is_connected = False
        assert session.is_connected is False

    # --- 2 ---
    def test_connect_calls_transport_then_invoke_shell(self, session, mock_transport):
        mock_channel = MagicMock()
        mock_channel.closed = False
        mock_transport.invoke_shell.return_value = mock_channel
        session.connect()
        mock_transport.connect.assert_called_once()
        mock_transport.invoke_shell.assert_called_once()

    # --- 3 ---
    def test_disconnect_closes_transport(self, session, mock_transport):
        session.disconnect()
        mock_transport.disconnect.assert_called_once()

    # --- 4 ---
    def test_write_sends_to_channel(self, connected_session):
        connected_session.write(b"ls\r")
        connected_session._channel.send.assert_called_once_with(b"ls\r")

    # --- 5 ---
    def test_write_raises_if_not_connected(self, session):
        # session._channel is None by default
        with pytest.raises(RuntimeError):
            session.write(b"data")

    # --- 6 ---
    def test_read_once_returns_empty_if_no_data(self, connected_session):
        connected_session._channel.recv_ready.return_value = False
        result = connected_session.read_once()
        assert result == b""

    # --- 7 ---
    def test_read_once_feeds_terminal_and_callback(self, connected_session):
        connected_session._channel.recv_ready.return_value = True
        connected_session._channel.recv.return_value = b"$ "
        cb = MagicMock()
        connected_session.register_callback(cb)
        result = connected_session.read_once()
        assert result == b"$ "
        cb.assert_called_once_with(b"$ ")

    # --- 8 ---
    def test_resize_updates_terminal_and_pty(self, session, mock_transport):
        session.resize(120, 40)
        mock_transport.resize_pty.assert_called_once_with(120, 40)
        assert len(session.terminal.get_display()) == 40

    # --- 9 ---
    def test_register_callback(self, connected_session):
        connected_session._channel.recv_ready.return_value = True
        connected_session._channel.recv.return_value = b"data"
        cb = MagicMock()
        connected_session.register_callback(cb)
        connected_session.read_once()
        cb.assert_called_once_with(b"data")

    # --- 10 ---
    def test_accept_host_key_delegates(self, session, mock_transport):
        fake_key = MagicMock()
        session.accept_host_key(fake_key, save=True)
        mock_transport.accept_host_key.assert_called_once_with(fake_key, True)


# ---------------------------------------------------------------------------
# TestSftpSession (12 tests)
# ---------------------------------------------------------------------------


class TestSftpSession:
    """SftpSession 的 L1 單元測試;SftpTransport 全 mock。"""

    @pytest.fixture
    def mock_sftp_transport(self, mocker):
        mock = MagicMock()
        mock.is_connected = False
        mock.connect = AsyncMock()
        mock.close = AsyncMock()
        mock.listdir = AsyncMock(return_value=[])
        mock.get = AsyncMock()
        mocker.patch(
            "andyterm.core.sftp_session.SftpTransport",
            return_value=mock,
        )
        return mock

    @pytest.fixture
    def session(self, mock_sftp_transport) -> SftpSession:
        return SftpSession(make_ssh_config())

    # --- 1 ---
    def test_is_connected_false_initially(self, session, mock_sftp_transport):
        mock_sftp_transport.is_connected = False
        assert session.is_connected is False

    # --- 2 ---
    def test_connect_raises_runtime_error(self, session):
        with pytest.raises(RuntimeError):
            session.connect()

    # --- 3 ---
    def test_disconnect_raises_runtime_error(self, session):
        with pytest.raises(RuntimeError):
            session.disconnect()

    # --- 4 ---
    def test_write_raises_not_implemented(self, session):
        with pytest.raises(NotImplementedError):
            session.write(b"x")

    # --- 5 ---
    def test_remote_cwd_starts_as_dot(self, session):
        assert session.remote_cwd == "."

    # --- 6 ---
    async def test_async_connect_calls_sftp_connect(self, session, mock_sftp_transport):
        await session.async_connect()
        mock_sftp_transport.connect.assert_called_once()

    # --- 7 ---
    async def test_async_close_calls_sftp_close(self, session, mock_sftp_transport):
        await session.async_close()
        mock_sftp_transport.close.assert_called_once()

    # --- 8 ---
    async def test_navigate_absolute_path(self, session, mock_sftp_transport):
        mock_sftp_transport.listdir.return_value = []
        await session.navigate("/home")
        assert session.remote_cwd == "/home"

    # --- 9 ---
    async def test_navigate_parent(self, session, mock_sftp_transport):
        mock_sftp_transport.listdir.return_value = []
        # 先切進去再返回上層
        session._remote_cwd = "/home/user"
        await session.navigate("..")
        # str(Path("/home/user").parent) — on Windows produces "\\home"
        expected = str(Path("/home/user").parent)
        assert session.remote_cwd == expected

    # --- 10 ---
    async def test_navigate_relative_path(self, session, mock_sftp_transport):
        mock_sftp_transport.listdir.return_value = []
        session._remote_cwd = "/home"
        await session.navigate("docs")
        # str(Path("/home") / "docs") — on Windows produces "\\home\\docs"
        expected = str(Path("/home") / "docs")
        assert session.remote_cwd == expected

    # --- 11 ---
    async def test_download_constructs_correct_path(self, session, mock_sftp_transport):
        session._remote_cwd = "/home/user"
        local_dir = Path("/tmp")
        result = await session.download("file.txt", local_dir)
        # Production code uses str(Path(cwd) / name); on Windows that gives backslashes.
        expected_remote = str(Path("/home/user") / "file.txt")
        mock_sftp_transport.get.assert_called_once_with(
            expected_remote,
            Path("/tmp/file.txt"),
            None,
        )
        assert result == Path("/tmp/file.txt")

    # --- 12 ---
    async def test_list_current_calls_sftp_listdir_with_cwd(
        self, session, mock_sftp_transport
    ):
        session._remote_cwd = "/some/dir"
        mock_sftp_transport.listdir.return_value = []
        await session.list_current()
        mock_sftp_transport.listdir.assert_called_with("/some/dir")


# ---------------------------------------------------------------------------
# TestUPortInfo + scan_moxa_uport (8 tests)
# ---------------------------------------------------------------------------


class TestUPortInfoAndScan:
    """UPortInfo dataclass 與 scan_moxa_uport() 的 L1 測試。"""

    # --- 1 ---
    def test_moxa_pid_map_has_expected_keys(self):
        model, port_count = MOXA_PID_MAP[0x1410]
        assert model == "UPort 1410"
        assert port_count == 4
        model2, port_count2 = MOXA_PID_MAP[0x1610]
        assert model2 == "UPort 1610-8"
        assert port_count2 == 8

    # --- 2 ---
    def test_uport_info_repr(self):
        info = UPortInfo(
            device="COM3",
            description="Moxa UPort 1410",
            pid=0x1410,
            model="UPort 1410",
            port_count=4,
        )
        r = repr(info)
        assert "UPort 1410" in r
        assert "4" in r

    # --- 3 ---
    def test_scan_empty_when_no_ports(self, mocker):
        mocker.patch(
            "andyterm.moxa.uport_info.list_ports.comports",
            return_value=[],
        )
        assert scan_moxa_uport() == []

    # --- 4 ---
    def test_scan_filters_non_moxa(self, mocker):
        fake_port = MagicMock()
        fake_port.vid = 0x1234
        fake_port.pid = 0x0001
        mocker.patch(
            "andyterm.moxa.uport_info.list_ports.comports",
            return_value=[fake_port],
        )
        assert scan_moxa_uport() == []

    # --- 5 ---
    def test_scan_returns_moxa_uport(self, mocker):
        fake_port = MagicMock()
        fake_port.vid = 0x110A
        fake_port.pid = 0x1410
        fake_port.device = "COM3"
        fake_port.description = "Moxa UPort 1410"
        mocker.patch(
            "andyterm.moxa.uport_info.list_ports.comports",
            return_value=[fake_port],
        )
        result = scan_moxa_uport()
        assert len(result) == 1
        assert result[0].model == "UPort 1410"
        assert result[0].port_count == 4

    # --- 6 ---
    def test_scan_unknown_pid_uses_default(self, mocker):
        fake_port = MagicMock()
        fake_port.vid = 0x110A
        fake_port.pid = 0x9999
        fake_port.device = "COM9"
        fake_port.description = "Unknown Moxa device"
        mocker.patch(
            "andyterm.moxa.uport_info.list_ports.comports",
            return_value=[fake_port],
        )
        result = scan_moxa_uport()
        assert len(result) == 1
        assert result[0].model == "Unknown Moxa UPort"
        assert result[0].port_count == 1

    # --- 7 ---
    def test_scan_multiple_ports(self, mocker):
        moxa1 = MagicMock()
        moxa1.vid = 0x110A
        moxa1.pid = 0x1410
        moxa1.device = "COM3"
        moxa1.description = "Moxa UPort 1410"

        moxa2 = MagicMock()
        moxa2.vid = 0x110A
        moxa2.pid = 0x1610
        moxa2.device = "COM4"
        moxa2.description = "Moxa UPort 1610-8"

        non_moxa = MagicMock()
        non_moxa.vid = 0xABCD
        non_moxa.pid = 0x0001

        mocker.patch(
            "andyterm.moxa.uport_info.list_ports.comports",
            return_value=[moxa1, moxa2, non_moxa],
        )
        result = scan_moxa_uport()
        assert len(result) == 2
        models = {r.model for r in result}
        assert "UPort 1410" in models
        assert "UPort 1610-8" in models

    # --- 8 ---
    def test_scan_none_pid_handled(self, mocker):
        fake_port = MagicMock()
        fake_port.vid = 0x110A
        fake_port.pid = None  # pid 可能為 None
        fake_port.device = "COM5"
        fake_port.description = None
        mocker.patch(
            "andyterm.moxa.uport_info.list_ports.comports",
            return_value=[fake_port],
        )
        # 不應該 raise;未知 PID 使用預設值
        result = scan_moxa_uport()
        assert len(result) == 1
        assert result[0].model == "Unknown Moxa UPort"
        assert result[0].port_count == 1
