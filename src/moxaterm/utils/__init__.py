"""utils/ — 跨層共用工具 (加密、logging)。

分層原則:
    - 不得 import Qt 模組
    - 可被 core/、protocols/、moxa/ 任意層 import
"""
