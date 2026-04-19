"""ui/ — Qt Widget 層,只能 import core/。

分層原則:
    - 可 import PySide6
    - 只能 import core/,不能直接 import protocols/ 或 moxa/
    - 跨層通訊走 Qt Signals 或 asyncio Queue
"""
