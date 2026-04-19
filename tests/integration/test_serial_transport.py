"""tests/integration/test_serial_transport.py — SerialTransport 整合測試。

結論先寫:
    - 全部使用 pytest-mock 模擬 serial.Serial,不開啟真實序列埠。
    - Windows CI 環境友好,無 pty 或硬體依賴。
    - 覆蓋 TransportError、list_serial_ports、SerialTransport 的
      open/close/read/write/send_break/set_control_lines/newline_bytes。
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest
import serial
import serial.serialutil

from andyterm.core.session import SerialConfig
from andyterm.protocols.serial_transport import (
    SerialTransport,
    TransportError,
    list_serial_ports,
)

# ---------------------------------------------------------------------------
# 輔助 fixture
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> SerialConfig:
    """建立最小可用的 SerialConfig,允許覆蓋任意欄位。"""
    defaults: dict[str, object] = {
        "name": "test-serial",
        "port": "COM99",
        "baudrate": 115200,
        "newline": "CR",
    }
    defaults.update(overrides)
    return SerialConfig(**defaults)  # type: ignore[arg-type]


def _make_mock_serial(mocker: object) -> MagicMock:
    """建立 mock serial.Serial 實例,預設 is_open=True。"""
    mock_serial = MagicMock(spec=serial.Serial)
    type(mock_serial).is_open = PropertyMock(return_value=True)
    return mock_serial


# ---------------------------------------------------------------------------
# TransportError
# ---------------------------------------------------------------------------


class TestTransportError:
    def test_transport_error_is_oserror(self) -> None:
        """TransportError 必須是 OSError 的子類,讓上層可用 except OSError 捕捉。"""
        assert issubclass(TransportError, OSError)

    def test_transport_error_can_be_raised_and_caught_as_oserror(self) -> None:
        """實際拋出 TransportError,確認可用 OSError 接住。"""
        with pytest.raises(OSError):
            raise TransportError("test error")


# ---------------------------------------------------------------------------
# list_serial_ports
# ---------------------------------------------------------------------------


class TestListSerialPorts:
    def test_list_serial_ports_returns_list(self, mocker: MagicMock) -> None:
        """mock comports() 回傳 2 個假 port:一個 Moxa (VID=0x110A),一個非 Moxa。
        驗證回傳 list 中每個 dict 包含必要欄位,且 is_moxa 旗標正確。
        """
        # 建立兩個假 port 物件
        moxa_port = MagicMock()
        moxa_port.device = "COM3"
        moxa_port.description = "Moxa UPort 1150"
        moxa_port.vid = 0x110A
        moxa_port.pid = 0x1250

        non_moxa_port = MagicMock()
        non_moxa_port.device = "COM4"
        non_moxa_port.description = "USB Serial Device"
        non_moxa_port.vid = 0x0403  # FTDI
        non_moxa_port.pid = 0x6001

        mocker.patch(
            "andyterm.protocols.serial_transport.list_ports.comports",
            return_value=[moxa_port, non_moxa_port],
        )

        result = list_serial_ports()

        assert len(result) == 2

        # 必要欄位驗證
        required_keys = {"device", "description", "vid", "pid", "is_moxa"}
        for entry in result:
            assert required_keys.issubset(entry.keys())

        # Moxa 旗標驗證
        moxa_entry = next(e for e in result if e["device"] == "COM3")
        non_moxa_entry = next(e for e in result if e["device"] == "COM4")

        assert moxa_entry["is_moxa"] is True
        assert moxa_entry["vid"] == 0x110A
        assert non_moxa_entry["is_moxa"] is False

    def test_list_serial_ports_empty_when_no_ports(self, mocker: MagicMock) -> None:
        """系統無序列埠時回傳空 list。"""
        mocker.patch(
            "andyterm.protocols.serial_transport.list_ports.comports",
            return_value=[],
        )
        assert list_serial_ports() == []

    def test_list_serial_ports_none_vid_is_not_moxa(self, mocker: MagicMock) -> None:
        """VID 為 None 的 port 不應被標記為 Moxa。"""
        port = MagicMock()
        port.device = "COM5"
        port.description = None
        port.vid = None
        port.pid = None

        mocker.patch(
            "andyterm.protocols.serial_transport.list_ports.comports",
            return_value=[port],
        )

        result = list_serial_ports()
        assert result[0]["is_moxa"] is False
        assert result[0]["description"] == ""  # None 轉換為 ""


# ---------------------------------------------------------------------------
# newline_bytes property
# ---------------------------------------------------------------------------


class TestNewlineBytes:
    def test_newline_bytes_cr(self) -> None:
        """newline='CR' 應回傳 b'\\r'。"""
        transport = SerialTransport(_make_config(newline="CR"))
        assert transport.newline_bytes == b"\r"

    def test_newline_bytes_lf(self) -> None:
        """newline='LF' 應回傳 b'\\n'。"""
        transport = SerialTransport(_make_config(newline="LF"))
        assert transport.newline_bytes == b"\n"

    def test_newline_bytes_crlf(self) -> None:
        """newline='CRLF' 應回傳 b'\\r\\n'。"""
        transport = SerialTransport(_make_config(newline="CRLF"))
        assert transport.newline_bytes == b"\r\n"


# ---------------------------------------------------------------------------
# open()
# ---------------------------------------------------------------------------


class TestOpen:
    def test_open_calls_serial(self, mocker: MagicMock) -> None:
        """open() 呼叫 serial.Serial() 並帶正確的 port 與 baudrate。"""
        mock_instance = _make_mock_serial(mocker)
        mock_cls = mocker.patch("serial.Serial", return_value=mock_instance)

        config = _make_config(port="COM3", baudrate=9600)
        transport = SerialTransport(config)
        transport.open()

        mock_cls.assert_called_once()
        call_args = mock_cls.call_args
        # 第一個位置參數為 port
        assert call_args.args[0] == "COM3"
        assert call_args.kwargs["baudrate"] == 9600

    def test_open_rfc2217_uses_serial_for_url(self, mocker: MagicMock) -> None:
        """port 以 rfc2217:// 開頭時,應呼叫 serial.serial_for_url 而非 serial.Serial。"""
        mock_instance = _make_mock_serial(mocker)
        mock_for_url = mocker.patch(
            "serial.serial_for_url", return_value=mock_instance
        )
        mocker.patch("serial.Serial")  # 確認 Serial 不被呼叫

        config = _make_config(port="rfc2217://192.168.0.1:4001")
        transport = SerialTransport(config)
        transport.open()

        mock_for_url.assert_called_once()
        call_args = mock_for_url.call_args
        assert call_args.args[0] == "rfc2217://192.168.0.1:4001"

    def test_open_idempotent(self, mocker: MagicMock) -> None:
        """連續呼叫 open() 兩次,只有第一次真正開啟序列埠 (is_open=True 時跳過)。"""
        mock_instance = _make_mock_serial(mocker)
        mock_cls = mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        transport.open()  # 第二次應被忽略

        mock_cls.assert_called_once()

    def test_open_raises_transport_error_on_serial_exception(
        self, mocker: MagicMock
    ) -> None:
        """serial.Serial() 拋出 SerialException 時,open() 應包裝成 TransportError。"""
        mocker.patch(
            "serial.Serial",
            side_effect=serial.serialutil.SerialException("port not found"),
        )

        transport = SerialTransport(_make_config())
        with pytest.raises(TransportError):
            transport.open()


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_not_open_noop(self) -> None:
        """未開啟就呼叫 close() 不應拋出任何例外。"""
        transport = SerialTransport(_make_config())
        transport.close()  # 不應拋出

    def test_close_calls_serial_close(self, mocker: MagicMock) -> None:
        """已開啟的 transport 呼叫 close() 應呼叫底層 serial.close()。"""
        mock_instance = _make_mock_serial(mocker)
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        transport.close()

        mock_instance.close.assert_called_once()


# ---------------------------------------------------------------------------
# is_open property
# ---------------------------------------------------------------------------


class TestIsOpen:
    def test_is_open_false_before_open(self) -> None:
        """未開啟時 is_open 應回傳 False。"""
        transport = SerialTransport(_make_config())
        assert transport.is_open is False

    def test_is_open_true_after_open_false_after_close(
        self, mocker: MagicMock
    ) -> None:
        """開啟後 is_open=True,close() 後 is_open=False。"""
        mock_instance = _make_mock_serial(mocker)
        # 第一次 is_open=True (開啟後),之後 close 會把 self._serial = None
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        assert transport.is_open is True

        transport.close()
        assert transport.is_open is False


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


class TestRead:
    def test_read_raises_if_not_open(self) -> None:
        """未開啟時 read() 應拋出 TransportError。"""
        transport = SerialTransport(_make_config())
        with pytest.raises(TransportError):
            transport.read()

    def test_read_returns_bytes(self, mocker: MagicMock) -> None:
        """mock serial.read 回傳 b'hello',驗證 read() 透傳結果。"""
        mock_instance = _make_mock_serial(mocker)
        mock_instance.read.return_value = b"hello"
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        result = transport.read()

        assert result == b"hello"

    def test_read_raises_on_serial_exception(self, mocker: MagicMock) -> None:
        """serial.read() 拋出 SerialException 時,read() 應包裝成 TransportError。"""
        mock_instance = _make_mock_serial(mocker)
        mock_instance.read.side_effect = serial.serialutil.SerialException("read error")
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()

        with pytest.raises(TransportError):
            transport.read()


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


class TestWrite:
    def test_write_raises_if_not_open(self) -> None:
        """未開啟時 write() 應拋出 TransportError。"""
        transport = SerialTransport(_make_config())
        with pytest.raises(TransportError):
            transport.write(b"x")

    def test_write_returns_count(self, mocker: MagicMock) -> None:
        """mock serial.write 回傳 5,驗證 write() 回傳相同計數。"""
        mock_instance = _make_mock_serial(mocker)
        mock_instance.write.return_value = 5
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        count = transport.write(b"hello")

        assert count == 5

    def test_write_raises_on_serial_exception(self, mocker: MagicMock) -> None:
        """serial.write() 拋出 SerialException 時,write() 應包裝成 TransportError。"""
        mock_instance = _make_mock_serial(mocker)
        mock_instance.write.side_effect = serial.serialutil.SerialException("write error")
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()

        with pytest.raises(TransportError):
            transport.write(b"hello")


# ---------------------------------------------------------------------------
# send_break()
# ---------------------------------------------------------------------------


class TestSendBreak:
    def test_send_break_raises_if_not_open(self) -> None:
        """未開啟時 send_break() 應拋出 TransportError。"""
        transport = SerialTransport(_make_config())
        with pytest.raises(TransportError):
            transport.send_break()

    def test_send_break_called(self, mocker: MagicMock) -> None:
        """open 後呼叫 send_break(0.1),應呼叫底層 serial.send_break(duration=0.1)。"""
        mock_instance = _make_mock_serial(mocker)
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        transport.send_break(0.1)

        mock_instance.send_break.assert_called_once_with(duration=0.1)


# ---------------------------------------------------------------------------
# set_control_lines()
# ---------------------------------------------------------------------------


class TestSetControlLines:
    def test_set_control_lines_raises_if_not_open(self) -> None:
        """未開啟時 set_control_lines() 應拋出 TransportError。"""
        transport = SerialTransport(_make_config())
        with pytest.raises(TransportError):
            transport.set_control_lines(dtr=True, rts=False)

    def test_set_control_lines_sets_dtr_rts(self, mocker: MagicMock) -> None:
        """dtr=True, rts=False 時應分別對 serial.dtr / serial.rts 賦值。"""
        mock_instance = _make_mock_serial(mocker)
        # 讓 dtr 與 rts 可以被 set (MagicMock 預設支援)
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()
        transport.set_control_lines(dtr=True, rts=False)

        assert mock_instance.dtr == True  # noqa: E712
        assert mock_instance.rts == False  # noqa: E712

    def test_set_control_lines_none_no_change(self, mocker: MagicMock) -> None:
        """dtr=None, rts=None 時不應對 serial.dtr / serial.rts 做任何賦值。"""
        mock_instance = _make_mock_serial(mocker)
        mocker.patch("serial.Serial", return_value=mock_instance)

        transport = SerialTransport(_make_config())
        transport.open()

        # 清除 open() 階段對 dtr/rts 的賦值記錄
        mock_instance.reset_mock()

        transport.set_control_lines(dtr=None, rts=None)

        # 驗證沒有透過 __setattr__ 設定 dtr 或 rts
        # MagicMock 記錄所有屬性寫入於 mock_calls
        attr_sets = [
            call
            for call in mock_instance.mock_calls
            if "dtr" in str(call) or "rts" in str(call)
        ]
        assert len(attr_sets) == 0
