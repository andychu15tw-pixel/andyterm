"""moxa/ — Moxa 裝置特定整合 (NPort 探索、UPort 識別)。

分層原則:
    - 不得 import Qt 模組
    - 不得 import core/ (避免循環依賴)
    - 純裝置探索與識別邏輯
"""
