# -*- coding: utf-8 -*-
"""窗口位置记忆：把用户拖到的位置存起来，下次启动 / 下次打开时还原。

落在 settings 表里（key = `pos:<名字>`），所以"开机自启"时窗口会回到上次
停的地方，而不是回到某个默认角落。

设计约定：
- **只存 x,y**。尺寸由各自布局决定（面板是 setFixedWidth，对话框跟着内容走），
  存了也会被覆盖，反而多出一处会和布局打架的"真相"。
- **落点必须做可见性校验**：换了显示器、改了分辨率、拔掉扩展屏之后，老坐标
  很可能整块落在屏幕外，直接 `move()` 过去窗口就"消失"了（托盘还能点出来，
  但用户会以为程序坏了）。见 `clamp_to_screens()`。
- 读写一律吞异常：记不住位置不是致命错误，不该影响程序启动。
"""
from __future__ import annotations

import re

# settings 里的键前缀
_KEY_PREFIX = "pos:"

# 可记忆位置的窗口名
WINDOW_PANEL = "panel"
WINDOW_TASK_DIALOG = "task_dialog"
WINDOW_STATS_PANEL = "stats_panel"
WINDOW_SETTINGS = "settings"
WINDOW_QR_RESULT = "qr_result"

# 至少要有这么多像素落在某块屏幕里，才算"这个位置还能用"。太小等于窗口几乎
# 整个在屏幕外（只剩一条边），与消失无异。
_MIN_VISIBLE_W = 120
_MIN_VISIBLE_H = 40

_POS_RE = re.compile(r"^-?\d+,-?\d+$")


def key_for(name: str) -> str:
    return _KEY_PREFIX + name


def format_pos(x: int, y: int) -> str:
    return f"{int(x)},{int(y)}"


def parse_pos(raw: str) -> tuple[int, int] | None:
    """解析 "x,y"。任何畸形输入都返回 None（手改过的库、老数据都不该让程序崩）。"""
    if not raw:
        return None
    text = raw.strip()
    if not _POS_RE.match(text):
        return None
    x, y = text.split(",")
    return int(x), int(y)


def clamp_to_screens(x: int, y: int, w: int, h: int) -> tuple[int, int] | None:
    """校验落点；整块跑到屏幕外时把它夹回屏幕内。

    返回 None 表示拿不到任何屏幕信息（调用方应保持原位，而不是 move 到 0,0）。
    """
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    screens = [s.availableGeometry() for s in QApplication.screens()]
    if not screens:
        return None

    rect = QRect(x, y, w, h)
    best = None
    best_area = -1
    for geo in screens:
        inter = geo.intersected(rect)
        area = max(0, inter.width()) * max(0, inter.height())
        if area > best_area:
            best, best_area = geo, area
    if best is None:
        return None
    if best_area >= _MIN_VISIBLE_W * _MIN_VISIBLE_H:
        return x, y      # 看得见 → 尊重用户选的位置，不做任何"优化"
    # 夹进那块**离它最近**的屏幕（而不是搬回主屏，尽量少一点意外感）
    return (
        max(best.x(), min(x, best.x() + best.width() - w)),
        max(best.y(), min(y, best.y() + best.height() - h)),
    )


def remember(db, name: str, widget) -> None:
    """记下控件当前的左上角。"""
    if db is None or not name:
        return
    try:
        db.set_setting(key_for(name), format_pos(widget.x(), widget.y()))
    except Exception:
        pass


def restore(db, name: str, widget) -> bool:
    """把控件移到上次记住的位置。返回是否真的移动过。"""
    if db is None or not name:
        return False
    try:
        raw = db.get_setting(key_for(name))
    except Exception:
        return False
    pos = parse_pos(raw)
    if pos is None:
        return False
    fixed = clamp_to_screens(pos[0], pos[1], widget.width(), widget.height())
    if fixed is None:
        return False
    widget.move(fixed[0], fixed[1])
    return True


def forget_all(db) -> None:
    """清掉全部窗口位置（托盘里的"重置窗口位置"）。

    名册在这里集中列出：新增可记忆窗口时**必须**加进来，否则托盘那条
    「清除保存的窗口位置」会悄悄漏掉它（读文件看不出来，只有实际用才知道）。
    """
    if db is None:
        return
    for name in (WINDOW_PANEL, WINDOW_TASK_DIALOG, WINDOW_STATS_PANEL,
                 WINDOW_SETTINGS, WINDOW_QR_RESULT):
        try:
            db.set_setting(key_for(name), "")
        except Exception:
            pass
