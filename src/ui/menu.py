# -*- coding: utf-8 -*-
"""弹窗菜单：与主面板同款的暗色玻璃。

托盘右键菜单、任务卡片的「更多」菜单都走这里。要解决两件事：

1. **配色**。菜单是**独立顶层窗口**（Qt::Popup），不挂在主面板那棵样式树里，
   没人给它套样式就是系统默认的浅色 —— 实测 `#F2F2F2` 底 + `#CCCCCC` 边框，
   跟暗色主 UI 摆在一起非常扎眼。统一套 `theme.menu_qss()`。

2. **磨砂**。把主面板同一套原生 Acrylic 挂到弹窗窗柄上，底板转透明，
   观感与主面板逐像素同源（同一支后端、同一个 tint）。

⚠️ 与主面板最大的不同：Qt 的 Popup 窗口在**隐藏时会被销毁**，下次显示是
**新的 HWND**，所以原生模糊必须在每次 `showEvent` 里重新挂，不能像主面板
那样"只做一次"（`taskbar._apply_glass_once`）。子菜单是另一个独立弹窗，
各挂各的 —— 所以子菜单也得是 `GlassMenu`，不能拿普通 QMenu 去 `addMenu`。

为什么玻璃态不把四角切成真圆角：见 `theme.menu_qss()` 的说明（Win10 的
Acrylic 是整块矩形模糊、不吃窗口区域，主面板同样是"方角模糊 + 圆角描边"）。
"""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from . import glass, theme

# 玻璃后端留成模块级变量，测试里换掉即可，不必真的去动窗口合成
# （offscreen 平台拿不到真 HWND，真调用必然失败，会测出"永远降级"的假结论）。
_apply_glass = glass.apply_glass


class GlassMenu(QMenu):
    """暗色玻璃弹窗菜单（配色 + 磨砂，原生模糊不可用时自动降级为不透明底板）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 先按"无原生模糊"着色：即使原生模糊永远失败，观感也一致且文字可读
        self._glass_mode = "opaque"
        self.setStyleSheet(theme.menu_qss("opaque"))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._resolve_glass()

    def _resolve_glass(self) -> bool:
        """每次弹出都重挂一次原生模糊（HWND 是新的）。返回是否挂上。

        每次弹窗只调一次 `apply_glass()`，不做"hwnd 没变就跳过"的缓存：
        弹窗隐藏即销毁窗口，句柄值有可能被系统回收复用，缓存反而会把
        "新窗口 + 旧句柄值"误判成"已经挂过了"。样式表切换仍按模式幂等 ——
        那才是真正的开销（重新 polish 整棵子树）。
        """
        try:
            hwnd = int(self.winId())
        except Exception:
            return False
        enabled = bool(_apply_glass(hwnd))
        mode = "glass" if enabled else "opaque"
        if mode != self._glass_mode:
            self._glass_mode = mode
            self.setStyleSheet(theme.menu_qss(mode))
        return enabled
