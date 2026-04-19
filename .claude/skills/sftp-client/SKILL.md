---
name: sftp-client
description: Use this skill when implementing or debugging SSH and SFTP functionality in AndyTerm — including paramiko Transport/SSHClient/SFTPClient, asyncssh async connections, public-key authentication (RSA/Ed25519), password and passphrase handling via keyring, host key verification (known_hosts), jump host / ProxyCommand, file transfer with progress callbacks, recursive directory sync, permission preservation, SFTP on resource-constrained Moxa ARM devices, and handling SSH connection issues (algorithm negotiation, handshake timeout, channel EOF, broken pipe). Trigger when user mentions "SSH", "SFTP", "paramiko", "asyncssh", "ssh key", "known_hosts", "proxy jump", "file transfer", "檔案傳輸", "上傳", "下載", or encounters authentication failures or transfer stalls.
---

# SSH / SFTP Implementation

## 套件選擇

| 情境 | 套件 | 理由 |
|---|---|---|
| SSH terminal (互動) | **paramiko** | 成熟、channel API 直觀、廣泛使用 |
| SFTP 大量傳輸 | **asyncssh** | 非同步、並行、進度回報好寫 |
| SFTP 單檔小量 | paramiko 也行 | 但要丟 QThread |

**AndyTerm 策略**: SSH shell 用 paramiko + QThread,SFTP 用 asyncssh + qasync。兩者共用 session 設定但**獨立建立連線**,避免互相影響。

---

## paramiko: SSH Shell Session

```python
import paramiko
from paramiko import SSHClient, AutoAddPolicy

class SshShellWorker(QObject):
    data_received = Signal(bytes)
    connected = Signal()
    disconnected = Signal()
    error_occurred = Signal(str)

    def __init__(self, config: SshSessionConfig):
        super().__init__()
        self._config = config
        self._client: SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._running = False

    @Slot()
    def start(self):
        try:
            self._client = SSHClient()
            # 正式版不要用 AutoAddPolicy,見下方 host key 章節
            self._client.load_host_keys(str(KNOWN_HOSTS_PATH))
            self._client.set_missing_host_key_policy(
                InteractiveHostKeyPolicy(parent=self)
            )
            self._client.connect(
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._config.password,    # 從 keyring 拿
                pkey=self._load_pkey(),
                timeout=10,
                auth_timeout=15,
                banner_timeout=15,
                look_for_keys=False,   # 只用我們指定的 key
                allow_agent=False,
            )
            self._channel = self._client.invoke_shell(
                term="xterm-256color",
                width=self._config.cols,
                height=self._config.rows,
            )
            self._channel.settimeout(0.0)  # non-blocking
            self.connected.emit()
            self._running = True
            self._read_loop()
        except paramiko.AuthenticationException:
            self.error_occurred.emit("認證失敗 / Authentication failed")
        except paramiko.SSHException as e:
            self.error_occurred.emit(f"SSH 錯誤 / SSH error: {e}")
        except OSError as e:
            self.error_occurred.emit(f"網路錯誤 / Network error: {e}")
        finally:
            self._cleanup()

    def _read_loop(self):
        while self._running and self._channel and not self._channel.exit_status_ready():
            if self._channel.recv_ready():
                data = self._channel.recv(4096)
                if data:
                    self.data_received.emit(data)
            else:
                self._channel.in_buffer.event.wait(0.05)

    @Slot(bytes)
    def write(self, data: bytes):
        if self._channel and self._channel.send_ready():
            self._channel.send(data)

    @Slot(int, int)
    def resize(self, cols: int, rows: int):
        """視窗大小改變 → 通知 server 重繪。"""
        if self._channel:
            self._channel.resize_pty(width=cols, height=rows)
```

---

## asyncssh: SFTP File Transfer

```python
import asyncssh
from pathlib import Path

class SftpClient:
    def __init__(self, config: SshSessionConfig):
        self._config = config
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None

    async def connect(self):
        self._conn = await asyncssh.connect(
            host=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
            client_keys=[self._config.key_path] if self._config.key_path else None,
            passphrase=self._config.passphrase,
            known_hosts=str(KNOWN_HOSTS_PATH),
            connect_timeout=10,
        )
        self._sftp = await self._conn.start_sftp_client()

    async def listdir(self, remote_path: str) -> list[asyncssh.SFTPName]:
        return await self._sftp.readdir(remote_path)

    async def download(
        self,
        remote: str,
        local: Path,
        progress_cb: Callable[[int, int], None] | None = None,
    ):
        await self._sftp.get(
            remote,
            str(local),
            progress_handler=progress_cb,
            # progress_cb(bytes_transferred, total_bytes)
        )

    async def upload(
        self,
        local: Path,
        remote: str,
        progress_cb: Callable[[int, int], None] | None = None,
    ):
        await self._sftp.put(
            str(local),
            remote,
            progress_handler=progress_cb,
        )

    async def download_dir(self, remote: str, local: Path):
        """遞迴下載整個目錄。"""
        await self._sftp.get(remote, str(local), recurse=True, preserve=True)

    async def close(self):
        if self._sftp:
            self._sftp.exit()
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
```

**進度回報**要 throttle,避免 signal 洪水:

```python
class ThrottledProgress:
    def __init__(self, callback, min_interval: float = 0.1):
        self._cb = callback
        self._min = min_interval
        self._last = 0.0

    def __call__(self, current: int, total: int):
        now = time.monotonic()
        if now - self._last >= self._min or current == total:
            self._last = now
            self._cb(current, total)
```

---

## Authentication

### 1. 密碼 (via keyring)

```python
import keyring

SERVICE = "andyterm"

def save_password(session_id: str, username: str, password: str):
    keyring.set_password(SERVICE, f"{session_id}:{username}", password)

def load_password(session_id: str, username: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, f"{session_id}:{username}")
    except keyring.errors.KeyringError:
        return None
```

### 2. Public key

```python
def load_pkey(path: Path, passphrase: str | None = None) -> paramiko.PKey:
    """支援 Ed25519 / RSA / ECDSA。優先 Ed25519。"""
    for key_class in (
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
        paramiko.RSAKey,
    ):
        try:
            return key_class.from_private_key_file(str(path), password=passphrase)
        except paramiko.SSHException:
            continue
    raise ValueError(f"不支援的 key 格式: {path}")
```

Passphrase 也走 keyring。

### 3. Keyboard-interactive (2FA, PAM)

```python
# paramiko 需要自訂 handler
def interactive_handler(title, instructions, prompt_list):
    # prompt_list: [(prompt_str, echo: bool)]
    # 回傳每個 prompt 的答案
    ...

transport = client.get_transport()
transport.auth_interactive(username, interactive_handler)
```

UI 層開對話框,`echo=False` 的 prompt 用 password field。

---

## Host Key Verification

**絕對不要** production 用 `AutoAddPolicy()`。正確做法:

```python
from paramiko import MissingHostKeyPolicy

class InteractiveHostKeyPolicy(MissingHostKeyPolicy):
    """首次連線彈出對話框讓使用者確認 fingerprint。"""
    def __init__(self, parent_window):
        self._parent = parent_window

    def missing_host_key(self, client, hostname, key):
        fingerprint = key.get_fingerprint().hex(":")
        # 注意:此處在 worker thread,用 QMetaObject.invokeMethod 回 UI thread
        accepted = ask_user_confirm_blocking(
            parent=self._parent,
            title="未知的主機 / Unknown Host",
            message=(
                f"主機 {hostname} 的 key fingerprint:\n"
                f"SHA256: {key.fingerprint}\n\n"
                f"是否信任並儲存? / Trust and save?"
            ),
        )
        if accepted:
            client.get_host_keys().add(hostname, key.get_name(), key)
            client.save_host_keys(str(KNOWN_HOSTS_PATH))
        else:
            raise paramiko.SSHException(f"Host key rejected: {hostname}")
```

Known hosts 檔路徑:
- Windows: `%APPDATA%/AndyTerm/known_hosts`
- Linux/macOS: `~/.config/andyterm/known_hosts`

---

## Jump Host (ProxyJump)

```python
# paramiko: 透過 Transport.open_channel
jump_client = SSHClient()
jump_client.connect(jump_host, ...)
jump_transport = jump_client.get_transport()

dest_channel = jump_transport.open_channel(
    "direct-tcpip",
    dest_addr=(target_host, target_port),
    src_addr=("127.0.0.1", 0),
)

target_client = SSHClient()
target_client.connect(target_host, sock=dest_channel, ...)
```

```python
# asyncssh: 內建 tunnel
async with asyncssh.connect(jump) as jump_conn:
    async with asyncssh.connect(target, tunnel=jump_conn) as target_conn:
        ...
```

---

## Common Issues

| 症狀 | 可能原因 | 解法 |
|---|---|---|
| `Incompatible ssh peer (no acceptable kex algorithm)` | server 太舊,用了 diffie-hellman-group1 | paramiko `disabled_algorithms={}` 打開,或升級 server |
| 連線 hang 不斷開 | 無 keepalive | `transport.set_keepalive(30)` |
| SFTP 大檔慢 | window size 預設小 | asyncssh `window=2**24` (16MB) |
| `Authentication failed` 但密碼對 | server 禁用 password,只收 key | 檢查 `/etc/ssh/sshd_config` |
| Moxa ARM device 卡在 handshake | CPU 太弱跑不完 chacha20 | 指定 `aes128-gcm@openssh.com` |
| 中文檔名亂碼 | SFTP 預設 latin-1 decode | asyncssh 用 `encoding="utf-8"`,paramiko 新版已內建 |

---

## 安全 checklist

- [ ] 密碼一律走 keyring,檔案不存明文
- [ ] Session export 時密碼欄預設**不匯出**,使用者必須明確勾選
- [ ] Host key 變動時強制使用者重新確認,警示 MITM 可能
- [ ] Private key 檔案權限檢查 (Linux 400/600)
- [ ] Log 不記錄密碼/passphrase,連帶不記錄 verbose SSH trace
- [ ] 提供「Clear all credentials」清除 keyring

---

## Moxa 嵌入式裝置注意

- V1200 (i.MX8M Plus ARM) 跑 SSH 沒問題,但 AES-NI 沒有 → GCM 比 CTR 慢,默認 `chacha20-poly1305` 較適合
- 老舊 V2406C 上 Debian 11 預設 OpenSSH 8.4,Ed25519 支援完整
- 如果客戶 device 是 2015 前的 ARMv7 + 舊 SSH,可能要降到 `aes128-ctr + hmac-sha2-256`
