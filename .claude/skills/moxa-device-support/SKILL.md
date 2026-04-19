---
name: moxa-device-support
description: Use this skill when implementing Moxa-specific device integrations in AndyTerm — including NPort serial-over-Ethernet device discovery (UDP broadcast protocol port 4800), NPort operation modes (Real COM, TCP Server, TCP Client, UDP, RFC2217), UPort USB-to-serial adapter identification via VID 0x110A and PID mapping, Moxa industrial computer console access (V3400/V2406C/V1200), U-Boot prompt detection at Moxa-specific baudrates (921600, 115200), and common Moxa debugging workflows. Trigger whenever user mentions "Moxa", "NPort", "UPort", "V3400", "V2406", "V1200", "V2426", "MGate", "Real COM", "Moxa discovery", or works with Moxa-specific device connections.
---

# Moxa Device Integration

AndyTerm 的差異化功能:原生支援 Moxa 硬體,在 session 對話框中提供「Scan Moxa NPort」與「Identify UPort」按鈕。

---

## UPort USB-to-Serial 識別

Moxa USB VID: **`0x110A`**

### PID → 型號對照表

| PID | 型號 | Port 數 |
|---|---|---|
| `0x1250` | UPort 1250 | 2 |
| `0x1251` | UPort 1250I (隔離) | 2 |
| `0x1410` | UPort 1410 | 4 |
| `0x1450` | UPort 1450 | 4 |
| `0x1451` | UPort 1450I | 4 |
| `0x1610` | UPort 1610-8 | 8 |
| `0x1611` | UPort 1610-16 | 16 |
| `0x1650` | UPort 1650-8 | 8 |
| `0x1651` | UPort 1650-16 | 16 |
| `0x0001`~`0x0005` | 舊款 UPort 11x0 系列 | 1 |

> 最新對照建議從 Moxa 官網 driver release notes 查,本表為常見款。

### Discovery 程式碼

```python
from serial.tools import list_ports

MOXA_VID = 0x110A

MOXA_PID_MAP = {
    0x1250: ("UPort 1250", 2),
    0x1251: ("UPort 1250I", 2),
    0x1410: ("UPort 1410", 4),
    0x1450: ("UPort 1450", 4),
    0x1451: ("UPort 1450I", 4),
    0x1610: ("UPort 1610-8", 8),
    0x1611: ("UPort 1610-16", 16),
    0x1650: ("UPort 1650-8", 8),
    0x1651: ("UPort 1650-16", 16),
}

def scan_moxa_uport() -> list[dict]:
    """掃描本機 Moxa UPort 裝置。"""
    results = []
    for p in list_ports.comports():
        if p.vid != MOXA_VID:
            continue
        model, total_ports = MOXA_PID_MAP.get(p.pid, (f"Unknown (PID=0x{p.pid:04X})", 1))
        results.append({
            "device": p.device,
            "model": model,
            "pid": p.pid,
            "total_ports": total_ports,
            "serial_number": p.serial_number,
            "location": p.location,
        })
    return results
```

---

## NPort LAN Discovery

NPort 支援 UDP broadcast discovery protocol。預設 port `4800`,wire protocol 約如下:

### Request Packet (UDP broadcast to 255.255.255.255:4800)

```
Offset  Length  Content
------  ------  -----------------------------------
0       1       0x01       # command: search
1       1       0x00
2       2       0x0008     # data length
4       4       0x00000000 # reserved
```

### Response 解析

每台 NPort 會回傳包含:
- MAC address (6 bytes)
- Model name (ASCII, 常見如 "NPort 5110", "NPort 5650-8")
- Firmware version
- IP、Netmask、Gateway
- Serial number

> ⚠️ **警告**: Moxa NPort search protocol 不是完全公開文件,wire format 可能隨 firmware 版本變動。開發時建議:
> 1. 先用 Moxa 官方 `NPort Search Utility` 抓封包當作 reference
> 2. **優先建議使用 Nmap / mDNS / LLDP / SNMP** 等標準協定
> 3. 或整合 Moxa 提供的 NPort Administrator CLI (若有)
>
> 如果遇到不確定的 byte,**先徵詢使用者**是否要走 reverse-engineering 路線。

### 備援方案:MXconfig CLI

部分 Moxa 工具有 CLI 模式 (如 `mxconfig.exe -search`),AndyTerm 可以 shell out 呼叫,解析輸出。**先問使用者**要走 native socket 還是 wrap CLI。

---

## NPort Operation Modes

在 New Session 對話框中,NPort 連線應讓使用者選擇模式:

| 模式 | 連線方式 | AndyTerm 實作 |
|---|---|---|
| **Real COM** | 靠 Moxa driver 建立虛擬 COM | 當成一般 serial port 處理,選 `COMx` |
| **TCP Server** (NPort 開 port 等連線) | raw TCP socket to `NPort_IP:port` | 新 pseudo-serial 類別 wrap `socket` |
| **TCP Client** | NPort 主動連出,AndyTerm 要聽 | 開 TCP listener |
| **UDP** | datagram | `socket.SOCK_DGRAM` |
| **RFC2217** | 遠端 serial 參數控制 | pyserial 原生支援 `rfc2217://host:port` |

### RFC2217 是 NPort 的最佳模式

```python
import serial

ser = serial.serial_for_url(
    "rfc2217://192.168.127.254:4001",
    baudrate=115200,
    timeout=0.1,
)
# 可以遠端設 baudrate,NPort 會轉發到實體 serial port
```

RFC2217 讓 serial 行為幾乎等同本地 COM。**建議在 UI 推薦這個模式**。

### Pseudo-serial wrapper (TCP Server Mode)

```python
import socket

class TcpSerialAdapter:
    """包成 pyserial-like 介面,讓上層不用管 serial vs TCP。"""
    def __init__(self, host: str, port: int, timeout: float = 0.05):
        self._sock = socket.create_connection((host, port), timeout=5)
        self._sock.settimeout(timeout)

    def read(self, size: int = 4096) -> bytes:
        try:
            return self._sock.recv(size)
        except socket.timeout:
            return b""

    def write(self, data: bytes) -> int:
        return self._sock.send(data)

    def close(self):
        self._sock.close()

    @property
    def is_open(self) -> bool:
        return self._sock.fileno() != -1
```

---

## Moxa 工業電腦 Console Access

| 機型 | Console 介面 | 預設 baudrate | 備註 |
|---|---|---|---|
| V3400 (x86 Atom) | 前面板 RJ45 RS-232 或背板 DB9 | 115200 8N1 | BIOS / GRUB / Linux console |
| V2406C (x86) | DB9 console port | 115200 8N1 | |
| V2426D (x86) | Console port | 115200 8N1 | 新款 (2026 發表) |
| V1200 (ARM i.MX8M Plus) | Micro-USB console | **921600 8N1** | U-Boot **及** Linux |

> 💡 V1200 的 921600 baudrate 常讓新人踩雷。AndyTerm 應在 session 設定對話框選了「V1200」機型時自動帶 921600。

### 建議內建的「Quick Connect」Profile

給客戶/FAE 快速上手,AndyTerm 可內建:

```yaml
profiles:
  - name: "Moxa V3400 Console"
    type: serial
    baudrate: 115200
    databits: 8
    parity: N
    stopbits: 1
    newline: CR
    encoding: UTF-8

  - name: "Moxa V1200 U-Boot/Linux Console"
    type: serial
    baudrate: 921600
    databits: 8
    parity: N
    stopbits: 1
    newline: CR
    encoding: UTF-8
    notes: "V1200 uses 921600 for both U-Boot and Linux"

  - name: "Moxa NPort 5xxx RFC2217"
    type: rfc2217
    default_port: 4001
    baudrate: 115200
```

---

## U-Boot Prompt 偵測

客戶常需要進 U-Boot 改環境變數。AndyTerm 可以做個小 helper:

```python
import re

UBOOT_PROMPTS = [
    re.compile(rb"^=>\s*$", re.MULTILINE),
    re.compile(rb"^U-Boot>\s*$", re.MULTILINE),
    re.compile(rb"^MX8MP#\s*$", re.MULTILINE),  # V1200 實測
]

AUTOBOOT_PROMPT = re.compile(rb"Hit any key to stop autoboot:\s*\d+")

def detect_uboot(buffer: bytes) -> str | None:
    """Return 'uboot_prompt' / 'autoboot_countdown' / None."""
    if AUTOBOOT_PROMPT.search(buffer):
        return "autoboot_countdown"  # UI 可 highlight 並按鈕「Send Any Key」
    for pat in UBOOT_PROMPTS:
        if pat.search(buffer):
            return "uboot_prompt"
    return None
```

UI 偵測到 autoboot 倒數時,自動彈出一個 banner:
> ⏱ 偵測到 U-Boot autoboot 倒數,點擊中斷? / Detected U-Boot autoboot countdown, click to interrupt?

---

## 客戶現場 debug 輔助功能 (建議 backlog)

- **一鍵匯出 log**: 把 terminal buffer 存檔,附 session metadata (baudrate、連線時間) → 方便客戶寄給 FAE
- **Timestamp prefix**: 每行加 `[HH:MM:SS.mmm]` 時間戳,排查 boot 順序問題
- **HEX/ASCII 雙視圖**: 除錯 protocol 用
- **Pattern highlighting**: 讓使用者自訂 regex (如 `ERROR|FATAL`) 標紅
- **Session recording**: 完整二進位 log (binary-safe) 供後續 replay

這些都是 Xshell 沒做好或沒有、而 Moxa FAE 實際會用到的。
