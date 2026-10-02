# -*- coding: utf-8 -*-
"""主题化下拉框 —— 把 QComboBox 上两个「QSS 管不到」的坑收在一个地方。

**坑 1：下拉箭头被画成小方块。**
`QComboBox::down-arrow` 用了 CSS 那套「三个 border 拼三角形」的写法，但
QStyleSheetStyle **不实现 CSS 的 box model** —— 那条规则的结果就是一个实心矩形，
真机上就是用户看到的「小方块」，不是箭头。

而且**光删 down-arrow 规则不够**（实测）：只要 `::drop-down` 规则还在，样式引擎就仍然
接管 `CC_ComboBox` 的绘制、不会把箭头转发给原生 style，箭头会**直接消失**（既不是方块
也不是三角，什么都没有）。必须把 `::drop-down` 和 `::down-arrow` **两条一起删掉**，
再由下面的 QProxyStyle 自绘 `PE_IndicatorArrowDown`。

**坑 2：弹出列表的圆角外侧是一圈黑色直角。**
QComboBox 的弹出列表是个**独立顶层窗口**（`QComboBoxPrivateContainer`，一个 QFrame）。
QSS 只给它内部的 QListView 画了圆角底板，**容器自己那层矩形没人画** → 圆角外是纯黑
`(0,0,0)`。

⚠️ 单设 `WA_TranslucentBackground` **不够**（实测四角仍是 `(0,0,0)`），必须同时给
`FramelessWindowHint` 才真正透出桌面（实测四角变成桌面色）。这条和窗口 flags 有关，
别只加一半。

容器在 QComboBox **构造时就已经存在**（不必等 showPopup），而且这里设的属性**能扛过
showPopup**（实测：弹出后 `WA_TranslucentBackground` 仍为 True），所以构造时设一次即可，
不需要在每次弹出时重挂 —— 这一点与 `menu.py::GlassMenu` 的原生模糊**不同**（那边必须
每次重挂，因为 Qt 的 Popup 隐藏即销毁）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QComboBox, QProxyStyle, QStyle

from . import theme


class _ComboArrowStyle(QProxyStyle):
    """自绘下拉箭头 —— 与 icons.py 同思路：QPainter 画，不依赖字体也不依赖图片资源。"""

    HALF_W = 4.0  # 半宽：箭头总宽 8px
    HALF_H = 2.6  # 半高：箭头总高 5.2px

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorArrowDown:
            r = option.rect
            cx = r.center().x() + 0.5  # +0.5 让三角形落在像素中心，避免半像素模糊
            cy = r.center().y() + 0.5
            poly = QPolygonF([
                QPointF(cx - self.HALF_W, cy - self.HALF_H),
                QPointF(cx + self.HALF_W, cy - self.HALF_H),
                QPointF(cx, cy + self.HALF_H),
            ])
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.TEXT_SECONDARY))
            painter.drawPolygon(poly)
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)


_arrow_style: _ComboArrowStyle | None = None


def _shared_arrow_style() -> _ComboArrowStyle:
    """惰性单例。

    必须惰性：QProxyStyle 构造时要去读 QApplication 当前的 style，模块导入期
    （QApplication 还没建）直接建实例会拿到空指针。多个控件共用一个实例是安全的。
    """
    global _arrow_style
    if _arrow_style is None:
        _arrow_style = _ComboArrowStyle()
    return _arrow_style


def round_popup(combo: QComboBox) -> bool:
    """把弹出列表的顶层容器改成「无边框 + 透明」，圆角外才透得出桌面。

    返回是否成功拿到容器（拿不到就保持原样，不影响功能）。
    """
    view = combo.view()
    box = view.parentWidget() if view is not None else None
    if box is None:
        return False
    box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    box.setAutoFillBackground(False)
    box.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    # 不加系统投影：Windows 上它会在圆角外多出一圈方角阴影，正好抵消刚修好的圆角。
    box.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    return True


class ThemedComboBox(QComboBox):
    """全项目统一用它，别再直接 new QComboBox（有静态守卫盯着）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyle(_shared_arrow_style())
        round_popup(self)
