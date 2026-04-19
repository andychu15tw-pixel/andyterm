# tests/conftest.py
# 共用 fixtures — 分層原則:ui/ 只能 import core/,core/ 不可 import Qt。
# 此檔案放 L1/L2 通用的 fixtures;UI 相關 fixtures 由 pytest-qt 的 qapp 自動提供。



# 未來可在此新增:
#   fake_ssh_server (asyncssh)
#   mock_serial_port (pty pair)
#   tmp_session_store (tmpdir + SessionStore)
