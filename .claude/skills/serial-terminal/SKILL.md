---
name: serial-terminal
description: Use this skill when implementing or debugging Serial (RS-232/422/485) communication and terminal emulation in AndyTerm — including pyserial configuration (baudrate, bytesize, parity, stopbits, flow control), port discovery on Windows/Linux, ANSI/VT100 escape sequence parsing with pyte, keyboard input encoding (arrow keys, Ctrl combos, function keys, XON/XOFF), line ending conversion (CR/LF/CRLF), binary/ASCII display modes, serial break signals, and handling common Moxa use cases (U-Boot console, Linux getty login, NPort Real COM mode, UPort multi-port adapters). Trigger when user mentions "serial", "COM port", "tty", "baudrate", "console", "VT100", "ANSI", "pyserial", "pyte", "終端機", "序列埠", "U-Boot", or encounters issues with garbled characters, missing ANSI colors, or key combinations not working.
---

# Serial Communication & Terminal Emulation

## Architecture

```
┌────────────────┐     bytes      ┌──────────────┐     chars     ┌────────────────┐
│ TerminalWidget │ ◄──────────── │  pyte.Stream │ ◄──────────── │ SerialWorker   │
│ (PySide6 UI)   │    emit       │  (VT100)     │               │ (pyserial)     │
│                │ ─────────────► │              │ ─────────────► │                │
└────────────────┘  key bytes    └──────────────┘  pass-through └────────────────┘
                                                                        │
                                                                        ▼
                                                                   COM3 / /dev/ttyUSB0
```

---

## pyserial Configuration

### 基本開啟

```python
import serial

ser = serial.Serial(
    port="COM3",          # Windows: "COM3", Linux: "/dev/ttyUSB0", "/dev/ttyS0"
    baudrate=115200,      # 最常見:9600, 115200, 921600 (Moxa bootloader)
    bytesize=serial.EIGHTBITS,       # 8
    parity=serial.PARITY_NONE,       # N
    stopbits=serial.STOPBITS_ONE,    # 1  → 俗稱 "115200 8N1"
    timeout=0.05,         # read timeout,50ms 平衡反應速度與 CPU
    write_timeout=1.0,
    xonxoff=False,        # 軟體流控 (Ctrl+S/Ctrl+Q) — 會攔截 Ctrl+S!
    rtscts=False,         # 硬體流控
    dsrdtr=False,
)
```

### ⚠️ 常見陷阱

| 陷阱 | 說明 |
|---|---|
| **XON/XOFF 吃掉 Ctrl+S** | terminal 使用者按 Ctrl+S 凍結畫面時,若開 xonxoff 會被攔截。預設關掉,UI 提供開關。 |
| **DTR/RTS 觸發重開機** | 開 port 瞬間 DTR 拉高會 reset Arduino/某些 MCU。開 port 後立刻 `ser.dtr = False; ser.rts = False`。 |
| **timeout=0 vs None** | `0` = non-blocking, `None` = blocking forever。我們用 `0.05` 平衡。 |
| **Windows COM > 9** | 路徑要寫 `\\\\.\\COM10` (`\\.\COM10`),pyserial 新版會自動處理,但舊版不會。 |
| **讀到 0 bytes ≠ 斷線** | timeout 內沒資料就是空 bytes,要靠 `ser.is_open` + 寫入失敗偵測斷線。 |

### Port Discovery

```python
from serial.tools import list_ports

def enumerate_serial_ports() -> list[dict]:
    """列出所有 serial port,標記 Moxa 裝置。

    Returns:
        list of {"device", "description", "vid", "pid", "is_moxa", "moxa_model"}
    """
    results = []
    for p in list_ports.comports():
        is_moxa = (p.vid == 0x110A)  # Moxa VID
        results.append({
            "device": p.device,
            "description": p.description,
            "vid": p.vid,
            "pid": p.pid,
            "hwid": p.hwid,
            "is_moxa": is_moxa,
            "moxa_model": _moxa_pid_to_model(p.pid) if is_moxa else None,
        })
    return results
```

Moxa VID = `0x110A`,常見 UPort PID:
- `0x1250` → UPort 1250
- `0x1251` → UPort 1251I
- `0x1410` → UPort 1410
- `0x1610` → UPort 1610-8/16

---

## VT100 / ANSI Emulation with pyte

`pyte` 是 pure-Python VT100 parser,維護一個 character grid + cursor state。

### 基本用法

```python
import pyte

screen = pyte.Screen(columns=80, lines=24)
stream = pyte.ByteStream(screen)    # bytes → pyte
# 或 pyte.Stream(screen) 吃 str

# 收到資料
stream.feed(b"\x1b[31mHello\x1b[0m World\r\n")

# 取出畫面
for line in screen.display:
    print(line)  # "Hello World" (紅色資訊在 screen.buffer)

# 取得字元屬性 (顏色、粗體等)
for y in range(screen.lines):
    for x in range(screen.columns):
        char = screen.buffer[y][x]
        # char.data, char.fg, char.bg, char.bold, char.italics...
```

### 給 TerminalWidget 的渲染策略

**方案 1 (簡單)**:只渲染 text,忽略顏色 → 適合除錯用,但 `ls --color` 會亂。

**方案 2 (推薦)**:使用 `QTextCharFormat` + 256-color ANSI palette。

```python
from PySide6.QtGui import QTextCharFormat, QColor

ANSI_16 = [
    "#000000", "#CD0000", "#00CD00", "#CDCD00",  # black red green yellow
    "#0000EE", "#CD00CD", "#00CDCD", "#E5E5E5",  # blue magenta cyan white
    "#7F7F7F", "#FF0000", "#00FF00", "#FFFF00",  # bright
    "#5C5CFF", "#FF00FF", "#00FFFF", "#FFFFFF",
]

def render_char(char: pyte.screens.Char) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fg = ANSI_16[_color_to_idx(char.fg)] if char.fg != "default" else "#D4D4D4"
    bg = ANSI_16[_color_to_idx(char.bg)] if char.bg != "default" else "#1E1E1E"
    fmt.setForeground(QColor(fg))
    fmt.setBackground(QColor(bg))
    if char.bold:
        fmt.setFontWeight(700)
    if char.italics:
        fmt.setFontItalic(True)
    if char.underscore:
        fmt.setFontUnderline(True)
    return fmt
```

### Cursor Position

```python
cursor_x = screen.cursor.x
cursor_y = screen.cursor.y
# 記得渲染時畫出 cursor (blinking block / underline / bar — 使用者可選)
```

---

## Keyboard Input Encoding

從 PySide6 `keyPressEvent` 拿到的 key,要編成 terminal 認得的 byte sequence。

### 基本鍵

| Qt Key | Bytes | 說明 |
|---|---|---|
| 可列印字元 | `event.text().encode(encoding)` | encoding 由 session 設定決定 |
| Enter | `b"\r"` (send_cr) 或 `b"\r\n"` | 視 session 設定 |
| Backspace | `b"\x7f"` (DEL) 或 `b"\x08"` (BS) | 使用者可選 |
| Tab | `b"\t"` | |
| Esc | `b"\x1b"` | |

### 控制鍵 (Ctrl+X)

```python
if event.modifiers() & Qt.ControlModifier:
    if Qt.Key_A <= event.key() <= Qt.Key_Z:
        return bytes([event.key() - Qt.Key_A + 1])  # Ctrl+A = 0x01, Ctrl+C = 0x03, ...
```

特殊:
- `Ctrl+C` = `0x03` (SIGINT,U-Boot 中斷也是它)
- `Ctrl+D` = `0x04` (EOF)
- `Ctrl+Z` = `0x1a` (SIGTSTP)
- `Ctrl+\` = `0x1c` (SIGQUIT)

### Arrow Keys / Function Keys (VT100 sequences)

```python
VT100_KEYS = {
    Qt.Key_Up:    b"\x1b[A",
    Qt.Key_Down:  b"\x1b[B",
    Qt.Key_Right: b"\x1b[C",
    Qt.Key_Left:  b"\x1b[D",
    Qt.Key_Home:  b"\x1b[H",
    Qt.Key_End:   b"\x1b[F",
    Qt.Key_PageUp:   b"\x1b[5~",
    Qt.Key_PageDown: b"\x1b[6~",
    Qt.Key_Insert:   b"\x1b[2~",
    Qt.Key_Delete:   b"\x1b[3~",
    Qt.Key_F1:  b"\x1bOP",
    Qt.Key_F2:  b"\x1bOQ",
    Qt.Key_F3:  b"\x1bOR",
    Qt.Key_F4:  b"\x1bOS",
    Qt.Key_F5:  b"\x1b[15~",
    Qt.Key_F6:  b"\x1b[17~",
    # ... F7-F12
}
```

### Application Mode vs Normal Mode

pyte 解析 `\x1b[?1h` (DECCKM) 後,arrow keys 要改成 `\x1bO*` 格式。監聽 `screen.mode` 是否含 `DECCKM`。

---

## Line Ending Handling

使用者介面提供:

| 選項 | 送出 | 接收處理 |
|---|---|---|
| Auto | `\r` (常用) | 直接顯示 |
| CR | `\r` | `\r` → 游標回行首 (預設行為) |
| LF | `\n` | `\n` → 換行 |
| CR+LF | `\r\n` | 顯示端正常處理 |

Moxa Linux console 通常 `\r` 即可,U-Boot 也接受 `\r`。

---

## Character Encoding

- **UTF-8** (預設): 多數 Linux 系統
- **Big5**: 偶爾遇到台灣舊系統 (Moxa 早期 Windows CE 產品)
- **ISO-8859-1 (Latin-1)**: binary-safe pass-through,除錯好用
- **Shift-JIS**: 日本客戶

UI 要提供下拉切換,每個 session 獨立設定。

解碼時要處理 multi-byte **跨 chunk**:

```python
class IncrementalDecoder:
    def __init__(self, encoding: str):
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")

    def decode(self, data: bytes, final: bool = False) -> str:
        return self._decoder.decode(data, final)
```

**不能**每次 `data.decode()`,因為一個 UTF-8 字元可能被切成兩個 read。

---

## Serial Break (SysRq)

嵌入式 Linux 常用 `send break` 觸發 SysRq:

```python
ser.send_break(duration=0.25)  # 250ms break
```

UI 提供菜單項「Send Break」。

---

## Moxa 特殊情境

### U-Boot Prompt

- baudrate 921600 (V1200/V3400) 或 115200 (較舊)
- 開機時要及時送 Ctrl+C (某些 image) 或任意鍵 (`autoboot` 模式)
- 提示字元通常 `=>` 或 `U-Boot> `

### Moxa NPort Real COM Mode

NPort 裝驅動後會出現虛擬 COM port,使用上與實體 COM 相同,但:
- latency 較高 (~10-50ms)
- `timeout` 適度放寬到 `0.1`
- 斷線可能要等 TCP keepalive 才偵測到 → 提供「強制重連」按鈕

### Moxa NPort TCP Server Mode

不走驅動,直接 TCP socket 到 NPort IP:port。在 AndyTerm 裡可視為一種「pseudo-serial」,用 `socket` 取代 `serial.Serial`,其餘邏輯共用。
