"""core/uboot_detector.py — U-Boot Prompt 偵測器。

結論先寫:
    - UBootDetector 接收 bytes stream,偵測 U-Boot prompt / autoboot 倒數。
    - detect(data) 回傳狀態字串或 None。
    - 由 SerialSession 選用性地引入,不在本類別中含 UI 邏輯。

分層原則:本模組位於 core/,不得 import Qt 模組。
"""

from __future__ import annotations

import re

__all__ = ["UBootDetector", "UBootState"]

# U-Boot prompt 模式 (Moxa 常見機型)
_UBOOT_PROMPTS: list[re.Pattern[bytes]] = [
    re.compile(rb"^=>\s*$", re.MULTILINE),
    re.compile(rb"^U-Boot>\s*$", re.MULTILINE),
    re.compile(rb"^MX8MP#\s*$", re.MULTILINE),     # V1200 (i.MX8M Plus)
    re.compile(rb"^MX8MM#\s*$", re.MULTILINE),     # i.MX8M Mini 變體
    re.compile(rb"^Marvell>>\s*$", re.MULTILINE),  # Marvell SoC 款
]

_AUTOBOOT_PATTERN = re.compile(rb"Hit any key to stop autoboot:\s*\d+")

_BUFFER_MAX = 8192  # 最多保留最近 8KB,避免記憶體無限增長


class UBootState:
    NONE = "none"
    AUTOBOOT_COUNTDOWN = "autoboot_countdown"
    UBOOT_PROMPT = "uboot_prompt"


class UBootDetector:
    """U-Boot Prompt 偵測器。

    結論:
        - 每次 detect(data) 都合併資料到滑動窗口 buffer 再匹配。
        - 一旦偵測到某狀態後 reset(),下次才會重新觸發。

    使用方式:
        detector = UBootDetector()
        state = detector.detect(incoming_bytes)
        if state == UBootState.AUTOBOOT_COUNTDOWN:
            # 彈出 UI banner
            ...
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def detect(self, data: bytes) -> str:
        """分析新到的 bytes,回傳目前狀態。

        回傳:
            UBootState.AUTOBOOT_COUNTDOWN / UBOOT_PROMPT / NONE
        """
        self._buf.extend(data)
        if len(self._buf) > _BUFFER_MAX:
            self._buf = self._buf[-_BUFFER_MAX:]

        buf = bytes(self._buf)

        if _AUTOBOOT_PATTERN.search(buf):
            return UBootState.AUTOBOOT_COUNTDOWN

        for pat in _UBOOT_PROMPTS:
            if pat.search(buf):
                return UBootState.UBOOT_PROMPT

        return UBootState.NONE

    def reset(self) -> None:
        """清除 buffer,重新開始偵測。"""
        self._buf.clear()
