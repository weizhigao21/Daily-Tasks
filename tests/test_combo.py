# -*- coding: utf-8 -*-
"""下拉框观感守卫（对应 src/ui/combo.py）。

守的两条都是真机暴露过、且**离屏渲染就能测出来**的问题：
① 下拉箭头被画成小方块；
② 弹出列表圆角外一圈黑色直角。

单独强调第 ① 条为什么值得守：它的失败模式不止一种 —— 用 CSS 那套 border 拼三角形
会画成方块；而只删 down-arrow、留着 drop-down 规则，箭头会**直接消失**。所以守卫要
同时钉住「箭头存在」和「箭头是三角而不是方块」。
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap

from src.ui import theme
from src.ui.combo import ThemedComboBox, _ComboArrowStyle

SRC_UI = Path(__file__).resolve().parents[1] / "src" / "ui"

# 洋红底：任何「没被画到」的地方都会留下这个颜色，一眼可辨（见 test_theme 同款手法）
_MAGENTA = "#ff00ff"


def _strip_qss_comments(qss: str) -> str:
    """把 /* ... */ 注释剥掉再判定。

    本项目喜欢在 QSS 里写大段中文注释解释「为什么禁止某条规则」，注释里难免出现
    规则名本身。不剥掉的话，一条讲解性的注释就能把静态守卫骗成失败。
    """
    return re.sub(r"/\*.*?\*/", "", qss, flags=re.S)


# ---------------- 静态守卫 ----------------

def test_no_bare_qcombobox_in_ui_sources():
    """下拉框一律走 ThemedComboBox。

    裸 QComboBox 会同时丢掉两处修复：自绘箭头（画成方块）和弹出列表的透明容器
    （圆角外露黑角）。这两处都在 ThemedComboBox 的构造里，绕不过去。
    """
    offenders = []
    for f in sorted(SRC_UI.glob("*.py")):
        if f.name == "combo.py":  # 定义处：它自己就是 QComboBox 的子类
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bQComboBox\s*\(", ln):
                offenders.append(f"{f.name}:{i}: {ln.strip()}")
    assert not offenders, (
        "请改用 combo.ThemedComboBox，别直接 new QComboBox：\n  " + "\n  ".join(offenders))


def test_theme_never_styles_the_arrows():
    """⚠️ 这两条规则一旦回到样式表，箭头会变方块（只留 drop-down 的话则直接消失）。

    详细成因见 combo.py 模块说明，别觉得「加个 border 三角挺方便」就加回来。
    """
    qss = _strip_qss_comments(theme.app_qss())
    for rule in ("QComboBox::down-arrow", "QComboBox::drop-down",
                 "QComboBox::up-arrow"):
        assert rule not in qss, f"{rule} 不该出现在样式表里（见 combo.py 说明）"


# ---------------- 渲染守卫 ----------------

def _render_arrow(qtbot) -> QImage:
    """渲染下拉框，返回落在洋红底上的图像。"""
    combo = ThemedComboBox()
    qtbot.addWidget(combo)
    combo.addItem("测试")
    combo.setStyleSheet(theme.app_qss())
    combo.resize(160, 34)
    combo.show()

    pm = QPixmap(combo.size())
    pm.fill(QColor(_MAGENTA))
    combo.render(pm)
    return pm.toImage()


def _is_ink(c: QColor) -> bool:
    """箭头用的 TEXT_SECONDARY 是浅灰，比输入框底色（近黑）亮得多。

    实测：底色混在洋红上是 #760076（min 通道 = 0），箭头像素 min 通道 ≥ 168。
    阈值取 140 是为了**排除控件边框**：获得焦点时边框是亮蓝色，它的抗锯齿像素
    （实测 #66a1e7、min 通道 102）够亮，用 100 的阈值会被误当成墨迹。
    """
    return min(c.red(), c.green(), c.blue()) > 140


def _arrow_ink_rows(img: QImage):
    """只看右侧 drop-down 区，并且**躲开四周 1px 边框**。

    左边的文字不进来（"测试" 只占左边一小段）；上下左右各留 2px 是因为焦点边框的
    抗锯齿像素会落在最外圈，不躲开会把 `rows[0]` / `rows[-1]` 顶替成边框行。
    踩过一次：那样 `top_w` 和 `bot_w` 都取到边框宽度，差值为 0，守卫失去区分力。
    """
    x0 = max(0, img.width() - 26)
    x1 = img.width() - 2
    rows = []
    for y in range(2, img.height() - 2):
        xs = [x for x in range(x0, x1) if _is_ink(QColor(img.pixel(x, y)))]
        if xs:
            rows.append((y, min(xs), max(xs)))
    return rows


def test_combo_installs_the_self_drawn_arrow_style(qtbot):
    """必须挂上自绘箭头 style。

    ⚠️ 这条**不能**靠渲染结果反推，注入验证时实测过：把 `setStyle(...)` 去掉之后
    箭头**不会消失**，原生 style 照样会画一个三角，本机看着也正常，所以下面那两条
    渲染守卫完全区分不出来。但那时"箭头长什么样"就交给平台了（箭头色不再取自
    `theme.TEXT_SECONDARY`，换 Windows 主题或 Qt style 后可能变成深色、在深色底上
    看不见），所以这里直接钉住"装了"。
    """
    combo = ThemedComboBox()
    qtbot.addWidget(combo)
    assert isinstance(combo.style(), _ComboArrowStyle), \
        "ThemedComboBox 没装自绘箭头 style —— 箭头外观会变成由平台决定"


def test_arrow_is_actually_drawn(qtbot):
    """箭头必须画出来。

    这条防的是「把样式表里的 down-arrow 删了、却忘了自绘」—— 那样箭头会彻底消失，
    界面上看不出错误、只是少了个提示，靠肉眼 review 很容易漏。
    """
    rows = _arrow_ink_rows(_render_arrow(qtbot))
    assert rows, "右侧找不到箭头墨迹 —— 箭头没画出来（样式表删了规则又没自绘？）"


def test_arrow_is_a_triangle_not_a_square(qtbot):
    """箭头必须上宽下窄。

    这是「方块 vs 三角」唯一可靠的区分点：两者高度接近，靠面积或高度都分不开。
    """
    rows = _arrow_ink_rows(_render_arrow(qtbot))
    assert rows, "没有箭头墨迹，先看 test_arrow_is_actually_drawn"
    top_w = rows[0][2] - rows[0][1]
    bot_w = rows[-1][2] - rows[-1][1]
    assert top_w >= bot_w + 3, (
        f"箭头不是三角形（首行宽 {top_w}px、末行宽 {bot_w}px）—— "
        "上下等宽说明又画成方块了")


def test_popup_container_is_translucent_and_frameless(qtbot):
    """弹出列表的顶层容器必须「透明 + 无边框」，否则圆角外是一圈黑色。

    ⚠️ 两条缺一不可：实测只设 WA_TranslucentBackground 时四角仍是纯黑 (0,0,0)，
    必须同时给 FramelessWindowHint 才真正透出桌面。
    """
    combo = ThemedComboBox()
    qtbot.addWidget(combo)
    box = combo.view().parentWidget()
    assert box is not None, "拿不到弹出列表的容器，round_popup 的逻辑要重新对齐"
    assert box.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground), \
        "容器没设 WA_TranslucentBackground -> 圆角外会露黑角"
    assert box.windowFlags() & Qt.WindowType.FramelessWindowHint, \
        "容器没有 FramelessWindowHint -> 只设透明不够，四角仍是黑的"


def test_popup_survives_show_popup(qtbot):
    """构造时设的属性要能扛过 showPopup，否则就得改成每次弹出重挂。

    Qt 在 showPopup 时有机会重建弹出窗口；实测这里不会（属性保持），所以布局成
    「构造时设一次」。这条守卫把这个前提钉住 —— 哪天 Qt 改了行为，它会红。
    """
    combo = ThemedComboBox()
    qtbot.addWidget(combo)
    combo.addItem("甲")
    combo.addItem("乙")
    combo.show()
    combo.showPopup()
    box = combo.view().window()
    assert box.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert box.windowFlags() & Qt.WindowType.FramelessWindowHint
    combo.hidePopup()
