# -*- coding: utf-8 -*-
"""遮挡提示条：面板位置上已经有别的窗口时，浮一条小标题说明"为什么不弹"。

它替代不了面板，作用只是把"被挡"这个状态**说出来**——否则用户分不清"被别的
窗口挡着"和"程序卡死了"。两条信息：谁挡着、以及怎么才能让它出来。

生命周期与观感约定：
- **独立顶层窗口**（`Qt.Tool` + 置顶），父级是面板：面板一销毁它就跟着销毁。
- **绝不抢焦点**：`WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`，
  否则唤出提示条会把用户正在打字的窗口弄失焦。
- 底板**不透明深色**、不走原生模糊：它半秒后就消失，不值得为它再接一套
  Acrylic 挂起/恢复（那套状态组合是本项目最容易出错的地方）。
- 自动消失由自己的单发计时器兜底，同时面板在展开/收回/让位时也会主动收起它
  ——两条路都要有，否则光标一直停在边缘时它会挂在那儿。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import config
from . import theme

_EDGE_GAP_PX = 10      # 与屏幕边缘的距离（在露出的细条之外再让开一点）


class OccludeHint(QWidget):
    """停靠边附近的"被挡住"小标题条。"""

    def __init__(self, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        card = QFrame(self)
        card.setObjectName("occludeCard")
        card.setStyleSheet(
            "QFrame#occludeCard {"
            f"  background: {theme.bg_rgba(0.97)};"
            f"  border: 1px solid {theme.STROKE_HOVER};"
            "  border-radius: 9px;"
            "}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(12, 9, 14, 9)
        row.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {theme.WARN}; font-size: 9px;")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._main = QLabel()
        self._main.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: {theme.FS_SM}px;")
        self._sub = QLabel("停在这里 1 秒仍要显示")
        self._sub.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FS_XS}px;")
        col.addWidget(self._main)
        col.addWidget(self._sub)
        row.addLayout(col, 1)

        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.setInterval(config.OCCLUDE_HINT_MS)
        self._auto_hide.timeout.connect(self.hide_hint)
        self.hide()

    # ---------- 对外 ----------
    def show_for(self, edge: str, screen_geo: QRect, anchor_y: int, thing: str) -> None:
        """贴到 `edge` 那一侧、纵向对齐 `anchor_y`（全是**逻辑**坐标）。

        ⚠️ `screen_geo` 必须由调用方给出（面板收了半个身子在屏外，从窗口几何问
        "在哪块屏"会拿到隔壁那块，见 `Taskbar._target_screen`）。
        """
        self._main.setText(f"被「{thing}」挡住" if thing else "这里被窗口挡着")
        self.adjustSize()
        w, h = self.width(), self.height()
        if edge == "left":
            x = screen_geo.x() + config.DOCK_STRIP_PX + _EDGE_GAP_PX
        else:
            x = screen_geo.x() + screen_geo.width() \
                - config.DOCK_STRIP_PX - _EDGE_GAP_PX - w
        top = screen_geo.y() + _EDGE_GAP_PX
        bottom = screen_geo.y() + screen_geo.height() - _EDGE_GAP_PX - h
        y = max(top, min(anchor_y - h // 2, bottom))
        self.move(x, y)
        self.show()
        self.raise_()
        self._auto_hide.start()

    def hide_hint(self) -> None:
        self._auto_hide.stop()
        if self.isVisible():
            self.hide()
