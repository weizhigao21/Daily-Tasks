# -*- coding: utf-8 -*-
"""日常任务栏入口。

运行方式：
    python -m src.app        （推荐，项目根目录下）
    python src/app.py        （直跑，自动修正导入路径）
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    # 高 DPI 缩放：必须在 QApplication 创建前设置，否则界面会被系统位图拉伸而发虚。
    # 用 QGuiApplication 而非 QApplication——虽然前者最终转调同一个静态方法，
    # 但语义上缩放策略属于 GUI 应用层，不依赖 Widgets。
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    # 注意：不要再设 AA_UseHighDpiPixmaps。该属性在 Qt 6 已弃用（连读取枚举值都会
    # 触发 DeprecationWarning），因为 Qt 6 起高 DPI 像素图**恒定开启**，设置它是空操作。

    app = QApplication(sys.argv)
    app.setApplicationName("DailyTaskbar")
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口 = 最小化到托盘

    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src.ui.taskbar import Taskbar
    else:
        from .ui.taskbar import Taskbar

    from src import config
    from src.ui import glass, theme

    # 预热截屏实例：它只在"窗口移动的第一帧"被用到，而首次构造有实打实的代价
    # （实测 44ms vs 复用后 12ms），启动时先付掉，拖动就不会在那儿顿一下。
    glass.warm_up_screen()

    # 按屏幕逻辑 DPI 微调字号：100% 缩放不放大，150% 及以上适度提字号
    screen = app.primaryScreen()
    if screen is not None:
        dpi = screen.logicalDotsPerInch()
        theme.set_scale(max(1.0, round(dpi / 96.0, 2)))

    # 默认字体显式指定，避免 Qt 回退到发虚的合成字体
    base_font = QFont("Microsoft YaHei UI", 10)
    base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(base_font)

    if config.APP_ICON.exists():
        app.setWindowIcon(QIcon(str(config.APP_ICON)))

    bar = Taskbar()
    bar.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
