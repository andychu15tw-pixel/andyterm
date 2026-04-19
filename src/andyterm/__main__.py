"""__main__.py — AndyTerm 入口點。

結論先寫:
    - main() 建立 QApplication (qasync event loop),show MainWindow,執行。
    - qasync.QEventLoop 整合 asyncio 與 Qt event loop,供 SFTP async 操作使用。
    - python -m andyterm 或 andyterm CLI 皆由此進入。
"""

from __future__ import annotations

import asyncio
import sys


def main() -> None:
    """AndyTerm 主進入點。"""
    import qasync

    from andyterm.app import create_app
    from andyterm.ui.main_window import MainWindow

    app = create_app(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
