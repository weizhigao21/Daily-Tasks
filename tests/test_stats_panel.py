# -*- coding: utf-8 -*-
"""打卡统计面板的表头观感守卫。

表头是 QTableView 里的**独立子控件**，不在 QTableWidget 的绘制范围内：表体那层
`QTableWidget { background: rgba(255,255,255,0.06) }` 管不到它。而
`QHeaderView::section { background: transparent }` 一旦缺了宿主那层的
`QHeaderView { background: transparent }`，section 透明后露出来的就是
**QHeaderView 自己的系统调色板底色**——本机实测 `#EFEFEF`，深色玻璃面板顶上
就是一条刺眼的白带（用户反馈的"统计界面头一行"）。

两条守卫都走**真实渲染**：把表头渲染到已知底色上量像素，而不是查样式表字符串
——后者会绑死实现（改成"给 section 一个显式深色底"同样是对的修法）。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QTableWidget

from src.core.models import TASK_DAILY, VERIFY_MANUAL, Task
from src.ui.stats_panel import StatsPanel

# 洋红：既不会被系统浅色底蒙混过关，也能一眼看出"这里什么都没画"
BACKDROP = "#FF00FF"
BACKDROP_RGB = QColor(BACKDROP).rgb()

# 量墨迹时避开的行：上边留给 section 的 padding，下边那 1px 是分隔线
INK_Y0, INK_Y1 = 6, -4


def _panel(tmp_db, qtbot) -> StatsPanel:
    for name in ("workbuddy-签到", "TRAE SOLO CN-签到"):
        tmp_db.add_task(Task(name=name, task_type=TASK_DAILY, verify_mode=VERIFY_MANUAL))
    panel = StatsPanel(tmp_db)
    qtbot.addWidget(panel)
    panel.resize(640, 420)
    panel.show()
    QApplication.processEvents()
    return panel


def _render_header(panel: StatsPanel) -> QImage:
    """把表头渲染到洋红底上。底色由 Qt 自己在那里画不画决定。"""
    header = panel.findChild(QTableWidget).horizontalHeader()
    pixmap = QPixmap(header.size())
    pixmap.fill(QColor(BACKDROP))
    header.render(pixmap)
    return pixmap.toImage()


def test_table_header_paints_no_light_band(tmp_db, qtbot):
    """表头不得露出系统调色板的浅色底。"""
    img = _render_header(_panel(tmp_db, qtbot))
    is_ink = [(x, y) for y in range(img.height()) for x in range(img.width())
              if img.pixel(x, y) != BACKDROP_RGB]
    assert is_ink, "表头什么都没画（是不是压根没量到控件？）"

    light = [(x, y) for x, y in is_ink
             if min(QColor(img.pixel(x, y)).red(),
                    QColor(img.pixel(x, y)).green(),
                    QColor(img.pixel(x, y)).blue()) > 200]
    assert not light, (
        f"表头露出了 {len(light)} 个浅色像素（如 {light[0]}）——"
        "section 透明后露的是 QHeaderView 的系统调色板底色，宿主那层也要一起去底色"
    )


def test_table_header_text_starts_at_the_left_edge(tmp_db, qtbot):
    """表头文字与左对齐的数据同起一条竖线。

    QHeaderView 默认逐列居中（`defaultAlignment()` 就是 `AlignCenter`），宽列里
    标题会飘到正中间，与左对齐的任务名隔着一大片空白——"歪"就是这么来的。
    """
    panel = _panel(tmp_db, qtbot)
    img = _render_header(panel)
    header = panel.findChild(QTableWidget).horizontalHeader()

    widths = [header.sectionSize(i) for i in range(header.count())]
    widest = max(range(len(widths)), key=lambda i: widths[i])
    # 守卫的守卫：列太窄时居中与左对齐的墨迹起点只差几个像素，这条断言就"永远绿"。
    assert widths[widest] >= 120, f"最宽的列只有 {widths[widest]}px，本条守卫已失去区分力"

    x0 = sum(widths[:widest])
    ink = [x - x0
           for y in range(INK_Y0, img.height() + INK_Y1)
           for x in range(x0 + 1, x0 + widths[widest])
           if img.pixel(x, y) != BACKDROP_RGB]
    assert ink, "最宽列的表头没有量到墨迹"
    # 左侧内边距是 10px，留一点抗锯齿余量。
    assert min(ink) <= 16, (
        f"最宽列（{widths[widest]}px）的表头文字从 {min(ink)}px 处才开始，"
        "看着像居中了 —— 表头应当与数据一样左对齐"
    )
