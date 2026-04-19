"""protocols/ — 純 I/O 傳輸層,無業務邏輯,無 Qt 依賴。

分層原則:
    - 不得 import Qt 模組
    - 不得 import core/ (避免循環依賴)
    - 只做連線建立、bytes 收發、連線關閉
"""
