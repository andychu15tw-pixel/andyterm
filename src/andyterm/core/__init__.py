"""core/ — 業務邏輯層,無 Qt 依賴。

分層原則:
    - 不得 import 任何 PySide6 / PyQt 模組
    - 只得 import protocols/ 與 utils/
    - 跨層通訊走 asyncio Queue 或 callback
"""
