"""tests/core/test_session.py — Session 型別與 ABC 的單元測試。

結論先寫:
    - 所有測試為純 Python/pydantic,不依賴任何 Qt 模組或 mock。
    - 目標覆蓋率 90%+,涵蓋 happy path、邊界值、ValidationError 情境。
    - 採用 L1 策略:每個 test < 100ms,無 I/O、無網路。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from andyterm.core.session import (
    SerialConfig,
    Session,
    SessionConfig,
    SessionType,
    SshConfig,
)

# ---------------------------------------------------------------------------
# TestSessionType
# ---------------------------------------------------------------------------


class TestSessionType:
    """SessionType enum 的完整性與 str 繼承行為驗證。"""

    def test_all_five_values_exist(self) -> None:
        """確認五種協定類型都有定義,不遺漏。"""
        members = {m.name for m in SessionType}
        assert members == {"SERIAL", "SSH", "SFTP", "RFC2217", "TCP_RAW"}

    def test_serial_str_value(self) -> None:
        """SERIAL 的 str 值應為 'serial' (小寫)。"""
        assert SessionType.SERIAL == "serial"
        assert SessionType.SERIAL.value == "serial"

    def test_ssh_str_value(self) -> None:
        """SSH 的 str 值應為 'ssh'。"""
        assert SessionType.SSH == "ssh"

    def test_sftp_str_value(self) -> None:
        """SFTP 的 str 值應為 'sftp'。"""
        assert SessionType.SFTP == "sftp"

    def test_rfc2217_str_value(self) -> None:
        """RFC2217 的 str 值應為 'rfc2217'。"""
        assert SessionType.RFC2217 == "rfc2217"

    def test_tcp_raw_str_value(self) -> None:
        """TCP_RAW 的 str 值應為 'tcp_raw'。"""
        assert SessionType.TCP_RAW == "tcp_raw"

    def test_construct_from_string_serial(self) -> None:
        """SessionType('serial') 應等於 SessionType.SERIAL (str enum 反查)。"""
        assert SessionType("serial") is SessionType.SERIAL

    def test_construct_from_string_ssh(self) -> None:
        """SessionType('ssh') 應等於 SessionType.SSH。"""
        assert SessionType("ssh") is SessionType.SSH

    def test_invalid_value_raises(self) -> None:
        """不合法的字串應 raise ValueError。"""
        with pytest.raises(ValueError):
            SessionType("telnet")

    def test_is_str_subclass(self) -> None:
        """SessionType 繼承 str,可直接用在需要 str 的場景。"""
        assert isinstance(SessionType.SERIAL, str)


# ---------------------------------------------------------------------------
# TestSessionConfig
# ---------------------------------------------------------------------------


class TestSessionConfig:
    """SessionConfig 基底設定的欄位驗證與序列化測試。"""

    def test_required_name_field(self) -> None:
        """name 為必填欄位,可正常建立。"""
        cfg = SessionConfig(name="my-session", type=SessionType.SERIAL)
        assert cfg.name == "my-session"

    def test_auto_generated_id_is_uuid4(self) -> None:
        """id 預設應自動產生合法的 UUID4 字串。"""
        cfg = SessionConfig(name="x", type=SessionType.SSH)
        # 若解析失敗則 raise ValueError
        parsed = uuid.UUID(cfg.id, version=4)
        assert str(parsed) == cfg.id

    def test_two_instances_have_different_ids(self) -> None:
        """兩個獨立建立的 SessionConfig 的 id 不應相同。"""
        cfg1 = SessionConfig(name="a", type=SessionType.SSH)
        cfg2 = SessionConfig(name="b", type=SessionType.SSH)
        assert cfg1.id != cfg2.id

    def test_encoding_defaults_to_utf8(self) -> None:
        """encoding 未指定時預設為 'utf-8'。"""
        cfg = SessionConfig(name="x", type=SessionType.SERIAL)
        assert cfg.encoding == "utf-8"

    def test_encoding_can_be_overridden(self) -> None:
        """encoding 可被覆寫為其他編碼 (如 'big5')。"""
        cfg = SessionConfig(name="x", type=SessionType.SERIAL, encoding="big5")
        assert cfg.encoding == "big5"

    def test_created_at_is_utc_datetime(self) -> None:
        """created_at 應為帶時區的 UTC datetime 物件。"""
        cfg = SessionConfig(name="x", type=SessionType.SSH)
        assert isinstance(cfg.created_at, datetime)
        # 必須帶有 UTC tzinfo
        assert cfg.created_at.tzinfo is not None
        assert cfg.created_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_last_used_at_defaults_to_none(self) -> None:
        """last_used_at 未指定時應為 None,表示從未使用。"""
        cfg = SessionConfig(name="x", type=SessionType.SERIAL)
        assert cfg.last_used_at is None

    def test_last_used_at_can_be_set(self) -> None:
        """last_used_at 可接受 UTC datetime 值。"""
        now = datetime.now(UTC)
        cfg = SessionConfig(name="x", type=SessionType.SSH, last_used_at=now)
        assert cfg.last_used_at == now

    def test_json_round_trip(self) -> None:
        """model_dump_json() 序列化後可以 model_validate_json() 完整還原。"""
        original = SessionConfig(name="round-trip", type=SessionType.SFTP)
        json_str = original.model_dump_json()
        restored = SessionConfig.model_validate_json(json_str)
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.type == original.type
        assert restored.encoding == original.encoding

    def test_missing_name_raises_validation_error(self) -> None:
        """缺少 name 欄位應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SessionConfig(type=SessionType.SERIAL)  # type: ignore[call-arg]

    def test_missing_type_raises_validation_error(self) -> None:
        """缺少 type 欄位應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SessionConfig(name="no-type")  # type: ignore[call-arg]

    def test_custom_id_is_accepted(self) -> None:
        """允許手動指定 id (例如從持久化存儲還原)。"""
        custom_id = str(uuid.uuid4())
        cfg = SessionConfig(id=custom_id, name="x", type=SessionType.SSH)
        assert cfg.id == custom_id


# ---------------------------------------------------------------------------
# TestSerialConfig
# ---------------------------------------------------------------------------


class TestSerialConfig:
    """SerialConfig RS-232/422/485 序列埠設定的欄位驗證測試。"""

    def test_type_is_always_serial(self) -> None:
        """SerialConfig.type 固定為 SessionType.SERIAL,不得被覆寫。"""
        cfg = SerialConfig(name="com3", port="COM3")
        assert cfg.type is SessionType.SERIAL

    def test_type_field_accepts_explicit_serial_value(self) -> None:
        """明確傳入 type=SessionType.SERIAL 時,SerialConfig 應正常建立。

        pydantic v2 的欄位宣告為 `type: SessionType = SessionType.SERIAL`,
        接受任何合法的 SessionType 值 (不強制 Literal 限制)。
        """
        cfg = SerialConfig(name="x", port="COM1", type=SessionType.SERIAL)
        assert cfg.type is SessionType.SERIAL

    def test_required_port_field(self) -> None:
        """port 為必填欄位,缺少時應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SerialConfig(name="no-port")  # type: ignore[call-arg]

    def test_port_value_stored_correctly(self) -> None:
        """port 值應原樣儲存。"""
        cfg = SerialConfig(name="usb0", port="/dev/ttyUSB0")
        assert cfg.port == "/dev/ttyUSB0"

    def test_baudrate_defaults_to_115200(self) -> None:
        """baudrate 預設值應為 115200 (Moxa 現場最常見)。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.baudrate == 115200

    def test_baudrate_can_be_set_to_9600(self) -> None:
        """baudrate 可設為 9600。"""
        cfg = SerialConfig(name="x", port="COM1", baudrate=9600)
        assert cfg.baudrate == 9600

    def test_baudrate_can_be_set_to_921600(self) -> None:
        """baudrate 可設為 921600 (Moxa bootloader 常用)。"""
        cfg = SerialConfig(name="x", port="COM1", baudrate=921600)
        assert cfg.baudrate == 921600

    def test_parity_defaults_to_none(self) -> None:
        """parity 預設值應為 'N' (None)。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.parity == "N"

    def test_valid_parities_are_accepted(self) -> None:
        """N/E/O/M/S 五種合法 parity 應全部通過驗證。"""
        for parity in ("N", "E", "O", "M", "S"):
            cfg = SerialConfig(name="x", port="COM1", parity=parity)  # type: ignore[arg-type]
            assert cfg.parity == parity

    def test_invalid_parity_raises_validation_error(self) -> None:
        """不合法的 parity 值 (如 'X') 應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SerialConfig(name="x", port="COM1", parity="X")  # type: ignore[arg-type]

    def test_bytesize_defaults_to_8(self) -> None:
        """bytesize 預設值應為 8。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.bytesize == 8

    def test_stopbits_defaults_to_1(self) -> None:
        """stopbits 預設值應為 1。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.stopbits == 1

    def test_xonxoff_defaults_to_false(self) -> None:
        """xonxoff 軟體流量控制預設關閉。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.xonxoff is False

    def test_rtscts_defaults_to_false(self) -> None:
        """rtscts 硬體流量控制預設關閉。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.rtscts is False

    def test_dtr_on_open_defaults_to_true(self) -> None:
        """dtr_on_open 預設為 True。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.dtr_on_open is True

    def test_rts_on_open_defaults_to_true(self) -> None:
        """rts_on_open 預設為 True。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.rts_on_open is True

    def test_newline_defaults_to_cr(self) -> None:
        """newline 預設為 'CR' (\\r),對應 Enter 鍵。"""
        cfg = SerialConfig(name="x", port="COM1")
        assert cfg.newline == "CR"

    def test_valid_newlines_are_accepted(self) -> None:
        """CR/LF/CRLF 三種合法 newline 應全部通過驗證。"""
        for nl in ("CR", "LF", "CRLF"):
            cfg = SerialConfig(name="x", port="COM1", newline=nl)  # type: ignore[arg-type]
            assert cfg.newline == nl

    def test_json_contains_serial_type_string(self) -> None:
        """model_dump_json() 輸出的 JSON 應包含 'serial' 字串。"""
        cfg = SerialConfig(name="com3", port="COM3")
        json_str = cfg.model_dump_json()
        assert '"serial"' in json_str

    def test_invalid_baudrate_type_raises_validation_error(self) -> None:
        """baudrate 傳入非數字型別應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SerialConfig(name="x", port="COM1", baudrate="fast")  # type: ignore[arg-type]

    def test_invalid_bytesize_raises_validation_error(self) -> None:
        """bytesize 傳入不合法的值 (如 9) 應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SerialConfig(name="x", port="COM1", bytesize=9)  # type: ignore[arg-type]

    def test_invalid_stopbits_raises_validation_error(self) -> None:
        """stopbits 傳入不合法的值 (如 3) 應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SerialConfig(name="x", port="COM1", stopbits=3)  # type: ignore[arg-type]

    def test_serial_config_json_round_trip(self) -> None:
        """SerialConfig 可完整 JSON 序列化再還原。"""
        original = SerialConfig(
            name="loop",
            port="COM3",
            baudrate=9600,
            parity="E",
            newline="CRLF",
        )
        json_str = original.model_dump_json()
        restored = SerialConfig.model_validate_json(json_str)
        assert restored.port == original.port
        assert restored.baudrate == original.baudrate
        assert restored.parity == original.parity
        assert restored.newline == original.newline
        assert restored.type is SessionType.SERIAL


# ---------------------------------------------------------------------------
# TestSshConfig
# ---------------------------------------------------------------------------


class TestSshConfig:
    """SshConfig SSH 終端機連線設定的欄位驗證與路徑方法測試。"""

    def test_type_is_always_ssh(self) -> None:
        """SshConfig.type 固定為 SessionType.SSH。"""
        cfg = SshConfig(name="dev-server", host="192.168.1.1", username="root")
        assert cfg.type is SessionType.SSH

    def test_type_field_accepts_explicit_ssh_value(self) -> None:
        """明確傳入 type=SessionType.SSH 時,SshConfig 應正常建立。

        pydantic v2 的欄位宣告為 `type: SessionType = SessionType.SSH`,
        接受任何合法的 SessionType 值 (不強制 Literal 限制)。
        """
        cfg = SshConfig(
            name="x",
            host="192.168.1.1",
            username="root",
            type=SessionType.SSH,
        )
        assert cfg.type is SessionType.SSH

    def test_required_host_field(self) -> None:
        """host 為必填欄位,缺少時應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SshConfig(name="x", username="root")  # type: ignore[call-arg]

    def test_required_username_field(self) -> None:
        """username 為必填欄位,缺少時應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SshConfig(name="x", host="192.168.1.1")  # type: ignore[call-arg]

    def test_port_defaults_to_22(self) -> None:
        """SSH port 預設為 22。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.port == 22

    def test_port_can_be_overridden(self) -> None:
        """port 可設為自訂值 (如 2222)。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin", port=2222)
        assert cfg.port == 2222

    def test_auth_method_defaults_to_password(self) -> None:
        """auth_method 預設為 'password'。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.auth_method == "password"

    def test_term_type_defaults_to_xterm_256color(self) -> None:
        """term_type 預設為 'xterm-256color'。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.term_type == "xterm-256color"

    def test_cols_defaults_to_80(self) -> None:
        """cols 預設為 80。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.cols == 80

    def test_rows_defaults_to_24(self) -> None:
        """rows 預設為 24。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.rows == 24

    def test_key_path_defaults_to_none(self) -> None:
        """key_path 未指定時應為 None。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.key_path is None

    def test_known_hosts_path_defaults_to_none(self) -> None:
        """known_hosts_path 未指定時應為 None。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.known_hosts_path is None

    def test_key_file_path_returns_none_when_not_set(self) -> None:
        """key_path 為 None 時,key_file_path() 應回傳 None。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert cfg.key_file_path() is None

    def test_key_file_path_returns_path_when_set(self) -> None:
        """key_path 有值時,key_file_path() 應回傳對應的 Path 物件。"""
        cfg = SshConfig(
            name="x",
            host="10.0.0.1",
            username="admin",
            key_path="/home/andy/.ssh/id_rsa",
        )
        result = cfg.key_file_path()
        assert result is not None
        assert isinstance(result, Path)
        assert result == Path("/home/andy/.ssh/id_rsa")

    def test_known_hosts_file_path_returns_default_when_not_set(self) -> None:
        """known_hosts_path 未設定時,known_hosts_file_path() 應回傳 ~/.ssh/known_hosts。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        result = cfg.known_hosts_file_path()
        expected = Path.home() / ".ssh" / "known_hosts"
        assert result == expected

    def test_known_hosts_file_path_returns_custom_path_when_set(self) -> None:
        """known_hosts_path 有值時,known_hosts_file_path() 應回傳對應的 Path 物件。"""
        custom = "/etc/ssh/known_hosts"
        cfg = SshConfig(
            name="x",
            host="10.0.0.1",
            username="admin",
            known_hosts_path=custom,
        )
        result = cfg.known_hosts_file_path()
        assert isinstance(result, Path)
        assert result == Path(custom)

    def test_known_hosts_file_path_is_path_instance(self) -> None:
        """known_hosts_file_path() 的回傳值無論有無設定都是 Path 型別。"""
        cfg = SshConfig(name="x", host="10.0.0.1", username="admin")
        assert isinstance(cfg.known_hosts_file_path(), Path)

    def test_ssh_config_json_round_trip(self) -> None:
        """SshConfig 可完整 JSON 序列化再還原,含 key_path 與 term_type。"""
        original = SshConfig(
            name="prod-server",
            host="192.168.10.5",
            username="moxa",
            port=2222,
            auth_method="pubkey",
            key_path="/home/andy/.ssh/id_ed25519",
            cols=132,
            rows=50,
            term_type="xterm",
        )
        json_str = original.model_dump_json()
        restored = SshConfig.model_validate_json(json_str)
        assert restored.host == original.host
        assert restored.username == original.username
        assert restored.port == original.port
        assert restored.auth_method == original.auth_method
        assert restored.key_path == original.key_path
        assert restored.cols == original.cols
        assert restored.rows == original.rows
        assert restored.term_type == original.term_type
        assert restored.type is SessionType.SSH

    def test_invalid_auth_method_raises_validation_error(self) -> None:
        """不合法的 auth_method 值應 raise ValidationError。"""
        with pytest.raises(ValidationError):
            SshConfig(
                name="x",
                host="10.0.0.1",
                username="admin",
                auth_method="kerberos",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# TestSessionABC
# ---------------------------------------------------------------------------


class FakeSession(Session):
    """Session ABC 的最小具體實作,供測試使用。

    結論:追蹤 connect/disconnect 呼叫次數與連線狀態,
    write() 將收到的 bytes 附加至 received 列表。
    """

    def __init__(self, config: SessionConfig) -> None:
        super().__init__(config)
        self._connected: bool = False
        self.connect_call_count: int = 0
        self.disconnect_call_count: int = 0
        self.received: list[bytes] = []

    @property
    def is_connected(self) -> bool:
        """回傳目前連線狀態。"""
        return self._connected

    def connect(self) -> None:
        """模擬建立連線,設定 _connected = True。"""
        self._connected = True
        self.connect_call_count += 1

    def disconnect(self) -> None:
        """模擬斷開連線,設定 _connected = False。"""
        self._connected = False
        self.disconnect_call_count += 1

    def write(self, data: bytes) -> None:
        """模擬寫入,收集 bytes 到 received 列表。"""
        if not self._connected:
            raise RuntimeError("Not connected")
        self.received.append(data)


@pytest.fixture
def serial_cfg() -> SerialConfig:
    """提供標準 SerialConfig 給 Session ABC 相關測試使用。"""
    return SerialConfig(name="test-serial", port="COM1")


@pytest.fixture
def fake_session(serial_cfg: SerialConfig) -> FakeSession:
    """建立已持有 SerialConfig 的 FakeSession 實例。"""
    return FakeSession(serial_cfg)


class TestSessionABC:
    """Session ABC 的介面合約與具體子類行為測試。"""

    def test_fake_session_can_be_instantiated(self, serial_cfg: SerialConfig) -> None:
        """FakeSession 實作所有抽象方法後,應可正常建立實例。"""
        session = FakeSession(serial_cfg)
        assert session is not None

    def test_config_property_returns_original_config(
        self, fake_session: FakeSession, serial_cfg: SerialConfig
    ) -> None:
        """config property 應回傳建構時傳入的設定物件 (同一物件)。"""
        assert fake_session.config is serial_cfg

    def test_config_property_returns_session_config_type(
        self, fake_session: FakeSession
    ) -> None:
        """config property 回傳的物件應為 SessionConfig 的實例。"""
        assert isinstance(fake_session.config, SessionConfig)

    def test_direct_session_instantiation_raises_type_error(self) -> None:
        """直接 instantiate Session ABC 本身應 raise TypeError (抽象類別保護)。"""
        with pytest.raises(TypeError):
            Session(SessionConfig(name="x", type=SessionType.SERIAL))  # type: ignore[abstract]

    def test_is_connected_false_before_connect(self, fake_session: FakeSession) -> None:
        """connect() 呼叫前,is_connected 應為 False。"""
        assert fake_session.is_connected is False

    def test_is_connected_true_after_connect(self, fake_session: FakeSession) -> None:
        """connect() 呼叫後,is_connected 應轉為 True。"""
        fake_session.connect()
        assert fake_session.is_connected is True

    def test_is_connected_false_after_disconnect(self, fake_session: FakeSession) -> None:
        """connect() 後再 disconnect(),is_connected 應回到 False。"""
        fake_session.connect()
        fake_session.disconnect()
        assert fake_session.is_connected is False

    def test_connect_increments_call_count(self, fake_session: FakeSession) -> None:
        """connect() 呼叫次數應被正確追蹤。"""
        fake_session.connect()
        fake_session.connect()
        assert fake_session.connect_call_count == 2

    def test_disconnect_increments_call_count(self, fake_session: FakeSession) -> None:
        """disconnect() 呼叫次數應被正確追蹤。"""
        fake_session.connect()
        fake_session.disconnect()
        fake_session.disconnect()  # 第二次呼叫 (no-op 情境)
        assert fake_session.disconnect_call_count == 2

    def test_write_after_connect_stores_data(self, fake_session: FakeSession) -> None:
        """連線後 write(data) 應將 data 加入 received 列表。"""
        fake_session.connect()
        fake_session.write(b"hello\r")
        assert fake_session.received == [b"hello\r"]

    def test_write_multiple_chunks(self, fake_session: FakeSession) -> None:
        """連續多次 write() 應依序儲存所有 chunks。"""
        fake_session.connect()
        fake_session.write(b"chunk1")
        fake_session.write(b"chunk2")
        fake_session.write(b"chunk3")
        assert fake_session.received == [b"chunk1", b"chunk2", b"chunk3"]

    def test_write_without_connect_raises_runtime_error(
        self, fake_session: FakeSession
    ) -> None:
        """未連線時 write() 應 raise RuntimeError。"""
        with pytest.raises(RuntimeError):
            fake_session.write(b"should-fail")

    def test_session_with_ssh_config(self) -> None:
        """FakeSession 也可接受 SshConfig (向上相容 SessionConfig 型別)。"""
        ssh_cfg = SshConfig(name="ssh-test", host="10.0.0.1", username="admin")
        session = FakeSession(ssh_cfg)
        assert session.config is ssh_cfg
        assert session.config.type is SessionType.SSH

    def test_subclass_missing_abstract_method_raises_type_error(self) -> None:
        """未實作所有抽象方法的子類別,instantiate 時應 raise TypeError。"""

        class IncompleteSession(Session):
            # 只實作 is_connected,其餘略過
            @property
            def is_connected(self) -> bool:
                return False

            def connect(self) -> None:
                pass

            def disconnect(self) -> None:
                pass

            # write() 故意省略

        with pytest.raises(TypeError):
            IncompleteSession(SessionConfig(name="x", type=SessionType.SSH))  # type: ignore[abstract]
