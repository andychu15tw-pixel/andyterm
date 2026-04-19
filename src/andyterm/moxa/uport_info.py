"""moxa/uport_info.py — Moxa UPort USB-to-Serial 轉換器識別。

結論先寫:
    - scan_moxa_uport() 掃描系統 serial port,回傳所有 Moxa UPort 裝置資訊。
    - MOXA_PID_MAP 對應 Moxa VID (0x110A) 下各 PID 到型號與埠數。
    - 無任何 Qt 依賴,亦不 import core/。

分層原則:本模組位於 moxa/,不得 import Qt 模組或 core/。
"""

from __future__ import annotations

from serial.tools import list_ports

__all__ = ["MOXA_PID_MAP", "UPortInfo", "scan_moxa_uport"]

MOXA_VID = 0x110A

# PID → (model_name, port_count)
MOXA_PID_MAP: dict[int, tuple[str, int]] = {
    0x1250: ("UPort 1250", 2),
    0x1251: ("UPort 1251I", 2),
    0x1252: ("UPort 1250I", 2),
    0x1410: ("UPort 1410", 4),
    0x1450: ("UPort 1450", 4),
    0x1451: ("UPort 1450I", 4),
    0x1610: ("UPort 1610-8", 8),
    0x1650: ("UPort 1650-8", 8),
    0x1611: ("UPort 1610-16", 16),
    0x1651: ("UPort 1650-16", 16),
    0x1130: ("UPort 1130", 1),
    0x1131: ("UPort 1130I", 1),
    0x1150: ("UPort 1150", 1),
    0x1151: ("UPort 1150I", 1),
    0x1110: ("UPort 1110", 1),
}


class UPortInfo:
    """Moxa UPort 裝置資訊。

    欄位:
        device: 序列埠裝置路徑 (e.g. "COM3" / "/dev/ttyUSB0")。
        description: 系統回報的裝置描述字串。
        pid: USB Product ID (整數)。
        model: 型號名稱 (e.g. "UPort 1410")。
        port_count: 該 UPort 型號的埠數。
    """

    def __init__(
        self,
        device: str,
        description: str,
        pid: int,
        model: str,
        port_count: int,
    ) -> None:
        self.device = device
        self.description = description
        self.pid = pid
        self.model = model
        self.port_count = port_count

    def __repr__(self) -> str:
        return (
            f"UPortInfo(device={self.device!r}, model={self.model!r}, "
            f"port_count={self.port_count})"
        )


def scan_moxa_uport() -> list[UPortInfo]:
    """掃描系統所有 serial port,回傳 Moxa UPort 裝置列表。

    結論:以 pyserial list_ports.comports() 列舉,過濾 VID == 0x110A。
    PID 在 MOXA_PID_MAP 中的項目回傳完整 UPortInfo;
    PID 不在 map 中的 Moxa 裝置以 "Unknown Moxa UPort" 記錄。

    回傳:
        UPortInfo 物件列表 (可能為空)。
    """
    results: list[UPortInfo] = []
    for p in list_ports.comports():
        if p.vid != MOXA_VID:
            continue
        pid = p.pid or 0
        model, port_count = MOXA_PID_MAP.get(pid, ("Unknown Moxa UPort", 1))
        results.append(
            UPortInfo(
                device=p.device,
                description=p.description or "",
                pid=pid,
                model=model,
                port_count=port_count,
            )
        )
    return results
