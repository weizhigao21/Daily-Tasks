# -*- coding: utf-8 -*-
"""贴边自动隐藏：吸附判定、设置存取、停靠几何、靠近展开/收回。"""
from PySide6.QtCore import QEasingCurve, QEvent, QPoint

from src import config
from src.core.db import TaskDB
from src.ui import theme
from src.ui.taskbar import Taskbar


def _pin_cursor(bar, x, y):
    """把"光标位置"钉在指定坐标，避免测试依赖真实鼠标。"""
    bar._cursor_pos = lambda: QPoint(x, y)


def _dock_left(bar):
    """停靠到左缘并返回 (屏幕几何, 收起 x, 展开 x)。"""
    geo = bar.screen().geometry()
    bar._dock("left", geo)
    collapsed_x, expanded_x = bar._docked_positions()
    return geo, collapsed_x, expanded_x


def test_snap_edge_left():
    # 窗口左缘贴屏幕左缘（380 宽窗口，屏幕 1920）
    assert Taskbar._snap_edge(0, 380, 0, 1920, 48) == "left"


def test_snap_edge_right():
    assert Taskbar._snap_edge(1920 - 380 + 10, 380, 0, 1920, 48) == "right"


def test_snap_none_when_middle():
    assert Taskbar._snap_edge(800, 380, 0, 1920, 48) is None


def test_snap_none_when_disabled():
    assert Taskbar._snap_edge(0, 380, 0, 1920, 0) is None


def test_snap_left_priority_when_both():
    # 极窄屏幕同时命中两侧时取更近的一侧
    assert Taskbar._snap_edge(10, 380, 0, 400, 48) == "left"


def test_settings_roundtrip(tmp_path):
    db = TaskDB(tmp_path / "t.db")
    assert db.get_setting("autohide") == ""
    db.set_setting("autohide", "1")
    assert db.get_setting("autohide") == "1"
    db.set_setting("autohide", "0")  # 覆盖更新
    assert db.get_setting("autohide") == "0"


def test_dock_geometry_left(taskbar):
    """停靠左缘：窗口移出屏幕，只留 6px 细条。"""
    taskbar._dock_edge = "left"
    taskbar._dock_expanded = False
    taskbar._dock("left", taskbar.screen().geometry())
    x = taskbar.x()
    strip = x + taskbar.width() - taskbar.screen().geometry().x()
    assert strip == 6  # 屏幕外部分 = 宽度 - 露出的细条
    assert taskbar.db.get_setting("dock_edge") == "left"


def test_dock_undock_restores(taskbar):
    taskbar._dock("right", taskbar.screen().geometry())
    assert taskbar._dock_edge == "right"
    taskbar._undock()
    assert taskbar._dock_edge is None
    assert taskbar.db.get_setting("dock_edge") == ""


def test_toggle_visible_with_dock_state(taskbar):
    """托盘切换在停靠状态下应是 展开↔收回，而不是 隐藏↔显示。"""
    taskbar._dock("right", taskbar.screen().geometry())
    assert taskbar._dock_expanded is False
    taskbar.toggle_visible()  # 收起中 → 展开
    assert taskbar._dock_expanded is True
    assert taskbar.isVisible()
    taskbar.toggle_visible()  # 展开中 → 收回
    assert taskbar._dock_expanded is False
    assert taskbar.isVisible()  # 不应整个隐藏


def test_toggle_visible_floating_hides(taskbar):
    assert taskbar._dock_edge is None
    taskbar.toggle_visible()
    assert not taskbar.isVisible()


def test_restore_dock_vertical_position(taskbar):
    """重启后停靠态必须还原垂直位置 dock_y。

    历史缺陷：_dock 存了 dock_y 却没有任何地方读它，_restore_dock 只还原
    边缘，细条每次启动都跳回默认 y，用户停靠的高度被丢掉。
    """
    db = taskbar.db
    db.set_setting("autohide", "1")
    db.set_setting("dock_edge", "right")
    db.set_setting("dock_y", "200")

    taskbar._restore_dock()

    assert taskbar._dock_edge == "right"
    assert taskbar.y() == 200
    collapsed_x, _ = taskbar._docked_positions()
    assert taskbar.x() == collapsed_x


# ---------- 拖动：内容区按下也能移动窗口（回归） ----------
def _mouse(etype, gpos):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        etype, QPoint(5, 5), gpos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_drag_from_content_area_moves_window(taskbar):
    """回归：内容区（卡片/文字所在处）按住拖动必须能移动窗口。

    此前 `_surface` 铺满整窗且默认接收鼠标事件，按住内容区时事件停在子控件里，
    Taskbar 的 mousePressEvent 永远收不到，只有窗口边缘几像素能拖。
    """
    taskbar.move(500, 400)
    host = taskbar.task_list_widget
    anchor = taskbar.mapToGlobal(QPoint(120, 100))

    taskbar.eventFilter(host, _mouse(QEvent.Type.MouseButtonPress, anchor))
    taskbar.eventFilter(host, _mouse(QEvent.Type.MouseMove, anchor + QPoint(80, 40)))
    taskbar.eventFilter(host, _mouse(QEvent.Type.MouseButtonRelease, anchor + QPoint(80, 40)))

    assert (taskbar.x(), taskbar.y()) == (580, 440), "内容区拖动应移动窗口"


def test_content_widgets_are_mouse_transparent(taskbar, tmp_db):
    """叶子控件穿透（拖动落回卡片）、卡片本身不穿透（按钮才点得到）。

    这条用例曾被写成"card 也要穿透"，正是那个错误不变量让「完成」按钮
    彻底失效：`WA_TransparentForMouseEvents` 会连**子树**一起退出命中链，
    卡片穿透 = 里面的按钮也穿透。真正的可点性由
    `test_ui.py::test_row_buttons_are_hit_testable` 用 widgetAt 兜底。
    """
    from PySide6.QtCore import Qt

    from src.core.models import TASK_DAILY, VERIFY_MANUAL, Task

    tmp_db.add_task(Task(name="任务", task_type=TASK_DAILY, verify_mode=VERIFY_MANUAL))
    taskbar.refresh_all()
    row = next(iter(taskbar._rows.values()))
    for key in ("dot", "name", "meta", "status"):
        assert row[key].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
            f"{key} 应鼠标穿透，按在文字上也能拖窗口"
    assert not row["card"].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
        "卡片是容器，穿透会连带废掉里面的按钮"
    assert not row["btn"].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
        "完成按钮必须可点击"


def test_small_jitter_does_not_start_drag(taskbar):
    """按下后轻微抖动（小于阈值）不应进入拖动，否则按钮点不动。"""
    from PySide6.QtCore import QEvent, QPoint

    taskbar.move(500, 400)
    host = taskbar.task_list_widget
    anchor = taskbar.mapToGlobal(QPoint(120, 100))

    taskbar.eventFilter(host, _mouse(QEvent.Type.MouseButtonPress, anchor))
    taskbar.eventFilter(host, _mouse(QEvent.Type.MouseMove, anchor + QPoint(1, 1)))

    assert taskbar._drag_pos is None, "抖动 1px 不应进入拖动"
    assert (taskbar.x(), taskbar.y()) == (500, 400), "窗口不应移动"



# ---------- 靠近展开 / 移开收回（轮询光标判定） ----------
def test_pointer_on_collapsed_strip_expands_immediately(taskbar):
    """回归：鼠标移到收起的细条上必须立刻展开（原来根本不弹出）。"""
    geo, collapsed_x, expanded_x = _dock_left(taskbar)
    assert taskbar.x() == collapsed_x
    assert taskbar._dock_expanded is False

    _pin_cursor(taskbar, geo.x() + 2, taskbar.y() + 80)   # 细条内
    taskbar._poll_cursor()

    assert taskbar._dock_expanded is True, "光标压到细条应立即展开"
    assert taskbar._anim is not None
    assert taskbar._anim.endValue().x() == expanded_x


def test_pointer_in_hot_zone_expands_after_delay(taskbar):
    """光标落在细条外侧的热区（窗口之外）→ 先计时，超时才展开。"""
    geo, _, expanded_x = _dock_left(taskbar)
    _pin_cursor(taskbar, geo.x() + config.DOCK_HOT_ZONE_PX, taskbar.y() + 80)

    taskbar._poll_cursor()
    assert taskbar._dock_expanded is False, "热区应留犹豫期，不立即弹出"
    assert taskbar._expand_timer.isActive()

    taskbar._on_expand_timeout()          # 犹豫期结束
    assert taskbar._dock_expanded is True
    assert taskbar._anim.endValue().x() == expanded_x


def test_pointer_far_away_cancels_pending_expand(taskbar):
    """扫过边缘又离开时，不得残留展开计时。"""
    geo, _, _ = _dock_left(taskbar)
    _pin_cursor(taskbar, geo.x() + config.DOCK_HOT_ZONE_PX, taskbar.y() + 80)
    taskbar._poll_cursor()
    assert taskbar._expand_timer.isActive()

    _pin_cursor(taskbar, geo.x() + 400, taskbar.y() + 80)
    taskbar._poll_cursor()
    assert not taskbar._expand_timer.isActive()
    assert taskbar._dock_expanded is False


def test_repeated_poll_does_not_starve_expand_timer(taskbar, monkeypatch):
    """回归：轮询不得反复重置计时器，否则犹豫期永远到不了点。"""
    geo, _, _ = _dock_left(taskbar)
    _pin_cursor(taskbar, geo.x() + config.DOCK_HOT_ZONE_PX, taskbar.y() + 80)

    starts = []
    original = taskbar._expand_timer.start
    monkeypatch.setattr(taskbar._expand_timer, "start",
                        lambda *a: (starts.append(1), original(*a))[1])

    for _ in range(5):
        taskbar._poll_cursor()
    assert len(starts) == 1, "连续轮询只应启动一次计时，不能反复重置"


def test_expanded_collapses_after_pointer_leaves(taskbar):
    """展开后光标移开 → 缓冲期结束再收回（不隐藏窗口）。"""
    geo, collapsed_x, expanded_x = _dock_left(taskbar)
    taskbar._dock_expanded = True
    taskbar.move(QPoint(expanded_x, taskbar.y()))

    _pin_cursor(taskbar, geo.x() + 500, taskbar.y() + 80)
    taskbar._poll_cursor()
    assert taskbar._collapse_timer.isActive()

    taskbar._on_collapse_timeout()
    assert taskbar._dock_expanded is False
    assert taskbar._anim.endValue().x() == collapsed_x
    assert taskbar.isVisible(), "收回只应缩到细条，不能隐藏窗口"


def test_pointer_near_panel_does_not_collapse(taskbar):
    """光标只是移到面板外一点点（缓冲区内）不应收回。"""
    geo, _, expanded_x = _dock_left(taskbar)
    taskbar._dock_expanded = True
    taskbar.move(QPoint(expanded_x, taskbar.y()))

    _pin_cursor(taskbar, geo.x() - config.DOCK_LEAVE_MARGIN_PX // 2, taskbar.y() + 80)
    taskbar._poll_cursor()
    assert not taskbar._collapse_timer.isActive()


def test_panel_stays_expanded_while_pointer_on_it(taskbar):
    """光标停在面板上时，展开不应被反复重置或误收回。"""
    geo, _, expanded_x = _dock_left(taskbar)
    taskbar._dock_expanded = True
    taskbar.move(QPoint(expanded_x, taskbar.y()))

    _pin_cursor(taskbar, geo.x() + 100, taskbar.y() + 80)
    for _ in range(3):
        taskbar._poll_cursor()
    assert taskbar._dock_expanded is True
    assert not taskbar._collapse_timer.isActive()

    # 光标在面板上时，已进入倒计时也必须取消收回。
    # 注意 `_dock_slide_out` 在"已展开"时是**先 return 再停计时器**的，
    # 所以这条命令必须由 _poll_cursor 自己发——漏掉就会 320ms 后缩回去。
    taskbar._collapse_timer.start()
    taskbar._poll_cursor()
    assert not taskbar._collapse_timer.isActive(), "光标回到面板应取消收回倒计时"


def test_manual_collapse_not_reopened_while_pointer_stays(taskbar):
    """托盘/热键手动收回后，光标还停在面板上时不应被自动弹回。"""
    geo, _, expanded_x = _dock_left(taskbar)
    taskbar._dock_expanded = True
    taskbar.move(QPoint(expanded_x, taskbar.y()))
    _pin_cursor(taskbar, geo.x() + 100, taskbar.y() + 80)

    taskbar.toggle_visible()                    # 手动收回
    assert taskbar._dock_expanded is False

    taskbar._poll_cursor()
    assert taskbar._dock_expanded is False, "手动收回后不应立刻自动弹回"

    _pin_cursor(taskbar, geo.x() + 500, taskbar.y() + 80)   # 离开面板
    taskbar._poll_cursor()
    assert taskbar._manual_hold is False

    _pin_cursor(taskbar, geo.x() + 2, taskbar.y() + 80)     # 再次靠近
    taskbar._poll_cursor()
    assert taskbar._dock_expanded is True


def test_pointer_on_partially_collapsed_panel_reverses(taskbar):
    """收回动画进行中光标又回到面板 → 立刻折返展开。"""
    geo, collapsed_x, expanded_x = _dock_left(taskbar)
    taskbar._dock_expanded = True
    taskbar.move(QPoint(expanded_x, taskbar.y()))
    taskbar._dock_slide_in()                    # 开始收回，_dock_expanded 已为 False
    taskbar.move(QPoint(expanded_x - 60, taskbar.y()))

    _pin_cursor(taskbar, geo.x() + 20, taskbar.y() + 80)    # 光标还在面板上
    taskbar._poll_cursor()
    assert taskbar._dock_expanded is True
    assert taskbar._anim.endValue().x() == expanded_x


# ---------- 动效 ----------
def test_collapse_eases_in_and_expand_eases_out(taskbar):
    """收回用 ease-in（平滑加速离场），展开用 ease-out（落位有吸附感）。"""
    _, collapsed_x, expanded_x = _dock_left(taskbar)
    y = taskbar.y()

    taskbar.move(QPoint(expanded_x, y))     # 必须真的在展开位，否则无从动画
    taskbar._dock_expanded = True
    taskbar._dock_slide_in()
    assert taskbar._anim.easingCurve().type() == QEasingCurve.Type.InCubic

    taskbar.move(QPoint(collapsed_x, y))
    taskbar._dock_expanded = False
    taskbar._dock_slide_out()
    assert taskbar._anim.easingCurve().type() == QEasingCurve.Type.OutCubic


def test_animation_duration_scales_with_remaining_distance(taskbar):
    """折返时按剩余行程缩短时长：反向不需要等一个满行程。"""
    _, collapsed_x, expanded_x = _dock_left(taskbar)
    travel = expanded_x - collapsed_x
    assert travel > 0
    y = taskbar.y()

    taskbar.move(QPoint(expanded_x, y))
    taskbar._animate_to(QPoint(collapsed_x, y))
    full = taskbar._anim.duration()

    taskbar.move(QPoint(expanded_x - travel // 4, y))       # 只剩 1/4 行程
    taskbar._animate_to(QPoint(collapsed_x, y))
    part = taskbar._anim.duration()

    assert full == config.DOCK_ANIM_MS
    assert config.DOCK_ANIM_MIN_MS <= part < full


def test_slide_animation_keeps_native_blur(taskbar):
    """回归（闪烁）：滑动期间**不得**挂起原生模糊。

    曾为"省掉 Acrylic 逐帧重算"而在动画期间挂起，实测帧间隔只差 0.02ms/帧
    （纯过早优化），代价却是每次挂起/恢复都留下一帧背景错配 —— 展开与收回
    各闪一次，正是用户报的问题。
    """
    _, collapsed_x, expanded_x = _dock_left(taskbar)
    taskbar._glass_on = True                     # 假装原生模糊可用
    taskbar._apply_surface_style(None)
    taskbar.move(QPoint(expanded_x, taskbar.y()))
    taskbar._dock_expanded = True

    taskbar._dock_slide_in()
    assert taskbar._anim is not None and taskbar._anim.endValue().x() == collapsed_x
    assert taskbar._glass_suspended is False, "滑动期间不应挂起原生模糊"
    assert not _has_backdrop(taskbar), "原生模糊仍在，底板应保持透明"
    assert not taskbar._collapse_timer.isActive(), "滑动时应清掉待触发的计时器"

    taskbar._on_anim_finished()
    assert taskbar._glass_suspended is False


def test_expand_animation_keeps_native_blur(taskbar):
    """展开方向同理：全程不碰原生模糊。"""
    _, collapsed_x, expanded_x = _dock_left(taskbar)
    taskbar._glass_on = True
    taskbar._apply_surface_style(None)
    taskbar._dock_expanded = False

    taskbar._dock_slide_out()
    assert taskbar._glass_suspended is False
    assert not _has_backdrop(taskbar)


def test_cursor_poll_only_runs_while_docked(taskbar):
    """未停靠时不该有轮询开销；停靠后启动、取消停靠即停止。"""
    assert not taskbar._cursor_timer.isActive()

    _dock_left(taskbar)
    assert taskbar._cursor_timer.isActive()

    taskbar._undock()
    assert not taskbar._cursor_timer.isActive()


# ---------- 底板必须始终存在（回归：动画/拖动时背景整块消失） ----------
def _has_backdrop(bar) -> bool:
    """底板是否真的画了颜色（false = 全透明，窗口后面什么都没有）。"""
    return "background: transparent" not in bar._surface.styleSheet()


def test_suspended_glass_keeps_backdrop(taskbar):
    """回归：关闭原生模糊时必须补回底板。

    `glass.suspend()` 撤掉的是**系统画在窗口后面的那层背景**；而原生模糊
    生效时底板样式是 `transparent`。两者相加就是"背景整个消失、只剩按钮
    和文字悬空"——用户报的收回动画 bug 就是它。
    """
    taskbar._glass_on = True
    taskbar._apply_surface_style(None)
    assert not _has_backdrop(taskbar)          # 原生模糊生效 → 透明是对的

    taskbar._suspend_glass()
    assert taskbar._glass_suspended is True
    assert _has_backdrop(taskbar), "系统背景已撤掉，底板必须由 Qt 补上"


def test_resumed_glass_returns_to_transparent(taskbar, monkeypatch):
    """恢复原生模糊后底板要交还给系统，否则会盖住真模糊。"""
    from src.ui import glass as glass_mod

    monkeypatch.setattr(glass_mod, "resume", lambda _hwnd: True)
    taskbar._glass_on = True
    taskbar._suspend_glass()
    taskbar._resume_glass()
    assert taskbar._glass_suspended is False
    assert not _has_backdrop(taskbar)


def test_drag_keeps_backdrop_visible(taskbar, monkeypatch):
    """拖动期间同样关模糊，底板也必须留着（与滑行动画同一根因）。"""
    from src.ui import glass as glass_mod

    monkeypatch.setattr(glass_mod, "resume", lambda _hwnd: True)
    taskbar._glass_on = True
    taskbar._apply_surface_style(None)

    taskbar._begin_drag(taskbar.mapToGlobal(QPoint(120, 100)))
    assert _has_backdrop(taskbar), "拖动中底板不能消失"

    taskbar._end_drag()
    assert not _has_backdrop(taskbar)


def test_failed_resume_keeps_backdrop(taskbar, monkeypatch):
    """原生模糊恢复失败时宁可少一层模糊，也不能切回透明让面板消失。"""
    from src.ui import glass as glass_mod

    taskbar._glass_on = True
    taskbar._suspend_glass()
    monkeypatch.setattr(glass_mod, "resume", lambda _hwnd: (_ for _ in ()).throw(
        RuntimeError("boom")))

    taskbar._resume_glass()
    assert _has_backdrop(taskbar), "恢复失败时必须保留底板"


def test_resume_returning_false_keeps_backdrop(taskbar, monkeypatch):
    """`resume()` 静默失败（返回 False 而不抛异常）时同样必须保留底板。

    只看"有没有抛异常"是不够的：`apply_glass()` 底层是靠 DWM 调用的返回值
    判断成功与否的，失败时并不抛异常。
    """
    from src.ui import glass as glass_mod

    taskbar._glass_on = True
    taskbar._suspend_glass()
    monkeypatch.setattr(glass_mod, "resume", lambda _hwnd: False)

    taskbar._resume_glass()
    assert taskbar._glass_suspended is True, "没恢复成功就不能清挂起标记"
    assert _has_backdrop(taskbar), "恢复失败时必须保留底板"


def test_suspend_paints_backdrop_before_removing_system_bg(taskbar, monkeypatch):
    """回归（闪烁）：挂起时必须**先补 Qt 底板，再撤系统背景**。

    反序会在两者之间留下"系统背景已撤、Qt 底板还没落"的空窗，屏幕上就是
    一次闪烁 —— 实测面板区均值从 41.6 跳到 82.7（透出桌面）。所以要断言：
    在 `glass.suspend()` 被调用的那一刻，底板已经画上去了。
    """
    from src.ui import glass as glass_mod

    taskbar._glass_on = True
    taskbar._apply_surface_style(None)
    seen = {}

    def fake_suspend(_hwnd):
        seen["backdrop"] = _has_backdrop(taskbar)

    monkeypatch.setattr(glass_mod, "suspend", fake_suspend)
    taskbar._suspend_glass()

    assert seen["backdrop"] is True, "撤系统背景的那一刻，Qt 底板必须已经在位"


def test_resume_restores_system_bg_before_dropping_backdrop(taskbar, monkeypatch):
    """回归（闪烁）：恢复时必须**先装回系统背景，再把底板撤成透明**。

    反序会让窗口短暂"两边都没背景"，与上一例同源。
    """
    from src.ui import glass as glass_mod

    taskbar._glass_on = True
    taskbar._suspend_glass()
    assert _has_backdrop(taskbar)
    seen = {}

    def fake_resume(_hwnd):
        seen["backdrop"] = _has_backdrop(taskbar)
        return True

    monkeypatch.setattr(glass_mod, "resume", fake_resume)
    taskbar._resume_glass()

    assert seen["backdrop"] is True, "装回系统背景时，Qt 底板不能已经撤掉"
    assert not _has_backdrop(taskbar), "恢复完成后应把背景交还给系统"


def test_slide_animation_does_not_touch_glass_state(taskbar):
    """动画全程不得变更玻璃相关状态（这是闪烁的全部来源）。"""
    _, _, expanded_x = _dock_left(taskbar)
    taskbar._glass_on = True
    taskbar._apply_surface_style(None)
    taskbar._dock_expanded = True
    taskbar.move(QPoint(expanded_x, taskbar.y()))

    before = (taskbar._glass_suspended, taskbar._surface_alpha,
              taskbar._surface.styleSheet())
    taskbar._dock_slide_in()
    during = (taskbar._glass_suspended, taskbar._surface_alpha,
              taskbar._surface.styleSheet())
    taskbar._on_anim_finished()
    after = (taskbar._glass_suspended, taskbar._surface_alpha,
             taskbar._surface.styleSheet())

    assert before == during == after


def test_surface_style_is_idempotent(taskbar, monkeypatch):
    """底板状态没变就不该再动样式表。

    `setStyleSheet` 会重新 polish `_surface` 的整个子树（滚动区 + 所有任务
    卡片），每次 showEvent 都做一遍就是可见的闪烁。
    """
    calls = []
    original = taskbar._surface.setStyleSheet
    monkeypatch.setattr(taskbar._surface, "setStyleSheet",
                        lambda css: (calls.append(css), original(css))[1])

    for _ in range(3):
        taskbar._apply_surface_style(None)
    assert len(calls) == 1, "状态未变时不应重复设置样式表"

    taskbar._apply_surface_style(0.5)
    assert len(calls) == 2, "状态变了才应该重设"

    taskbar._apply_surface_style(0.5)
    assert len(calls) == 2


def test_repeated_show_does_not_rebuild_native_glass(taskbar, monkeypatch):
    """回归（闪烁）：同一个窗口不该反复应用原生模糊。

    `DwmSetWindowAttribute` 每次都触发窗口背景重建；托盘隐藏后再显示会走
    showEvent，若不幂等就每次闪一下。
    """
    from src.ui import glass as glass_mod

    calls = []
    monkeypatch.setattr(glass_mod, "apply_glass",
                        lambda hwnd: (calls.append(hwnd), True)[1])

    taskbar._glass_hwnd = 0                   # 模拟"尚未为任何窗口应用过"
    taskbar._apply_glass_once()
    taskbar._apply_glass_once()
    taskbar._apply_glass_once()

    assert len(calls) == 1, "同一个 hwnd 不应重复应用原生模糊"


def test_suspended_state_still_rebuilds_on_show(taskbar, monkeypatch):
    """挂起态下再显示应恢复模糊与底板，不能被幂等判定跳过。"""
    from src.ui import glass as glass_mod

    calls = []
    monkeypatch.setattr(glass_mod, "apply_glass",
                        lambda hwnd: (calls.append(hwnd), True)[1])

    taskbar._glass_hwnd = 0                   # 模拟"尚未为任何窗口应用过"
    taskbar._apply_glass_once()               # 首次：建立 _glass_hwnd
    assert len(calls) == 1
    taskbar._glass_suspended = True           # 假装挂着就隐藏了

    taskbar._apply_glass_once()
    assert len(calls) == 2, "挂起态下必须重新应用，否则面板会一直没背景"
    assert taskbar._glass_suspended is False
    assert not _has_backdrop(taskbar)


def test_degraded_path_unaffected_by_suspend(taskbar):
    """原生模糊不可用时（本就自带不透明底板）不受挂起逻辑影响。"""
    taskbar._glass_on = False
    taskbar._apply_surface_style(theme.BG_BASE_ALPHA)
    before = taskbar._surface.styleSheet()

    taskbar._suspend_glass()
    assert taskbar._glass_suspended is False, "没开原生模糊就无需挂起"
    assert taskbar._surface.styleSheet() == before


def test_reshow_clears_stale_suspend_flag(taskbar, monkeypatch):
    """隐藏时动画还没跑完就再显示：apply_glass 已把系统背景装回，
    挂起标记必须同步清掉，否则底板会与真实状态脱节。"""
    from src.ui import glass as glass_mod

    monkeypatch.setattr(glass_mod, "apply_glass", lambda _hwnd: True)
    taskbar._glass_suspended = True          # 假装上次是挂着隐藏的

    taskbar._apply_glass_once()

    assert taskbar._glass_on is True
    assert taskbar._glass_suspended is False
    assert not _has_backdrop(taskbar), "系统背景已恢复，底板应交还系统"

