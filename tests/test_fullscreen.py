# -*- coding: utf-8 -*-
"""全屏免打扰：判定边界、误判排除、防抖、按屏过滤、让位与回位。

判定逻辑是纯函数 + 注入式探针，所以这里能全量离屏覆盖；真正的 Win32 调用
（`foreground_info` / `monitor_for_window`）只做"不抛异常"的冒烟测试。
"""
from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QPoint
from PySide6.QtWidgets import QWidget

from src import config
from src.ui import fullscreen

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

FULL_HD = (0, 0, 1920, 1080)
# 工作区（底部留出任务栏）——最大化窗口按工作区摆放，不该被当成全屏
WORK_AREA = (0, 0, 1920, 1040)


def _info(rect=FULL_HD, monitor_rect=FULL_HD, monitor=77, pid=999999,
          class_name="GameWindow", minimized=False, maximized=False, hwnd=4242,
          work_rect=None, has_caption=True):
    return fullscreen.ForegroundInfo(
        hwnd=hwnd, pid=pid, class_name=class_name, rect=rect,
        monitor=monitor, monitor_rect=monitor_rect,
        minimized=minimized, maximized=maximized,
        work_rect=work_rect, has_caption=has_caption,
    )


def _dock_left(bar):
    """停靠到左缘并返回 (屏幕几何, 收起 x, 展开 x)。"""
    geo = bar.screen().geometry()
    bar._dock("left", geo)
    collapsed_x, expanded_x = bar._docked_positions()
    return geo, collapsed_x, expanded_x


class _FakeWatcher:
    """按脚本返回结论的假观察者：用来隔离"taskbar 怎么响应结论"。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.seen = {}

    def update(self, **kwargs):
        self.seen.update(kwargs)
        return self.answers.pop(0) if self.answers else False


# ---------- 纯判定 ----------
def test_rect_covers_exact():
    assert fullscreen.rect_covers(FULL_HD, FULL_HD)


def test_rect_covers_within_tolerance():
    # DWM 扩展边框 / DPI 取整会带来 1~2px 偏差，必须容忍
    assert fullscreen.rect_covers((-8, -8, 1928, 1088), FULL_HD)     # 外扩（最大化窗口长这样）
    assert fullscreen.rect_covers((2, 2, 1918, 1078), FULL_HD)       # 内缩 2px，仍在容差内
    assert not fullscreen.rect_covers((3, 3, 1917, 1077), FULL_HD)   # 内缩 3px，超出容差


def test_rect_covers_rejects_work_area():
    """按工作区摆放的窗口（底下露着任务栏）不算全屏。"""
    assert not fullscreen.rect_covers(WORK_AREA, FULL_HD)


def test_should_yield_for_borderless_fullscreen():
    assert fullscreen.should_yield(_info(), own_pid=1) is True


def test_should_yield_rejects_maximized():
    """关键守卫：最大化窗口的 DWM 扩展边框会"覆盖"整屏。

    漏掉这条，用户日常开着最大化窗口时面板会永久不出现——本功能最容易踩的坑。
    """
    assert fullscreen.should_yield(_info(rect=(-8, -8, 1928, 1088), maximized=True),
                                   own_pid=1) is False


def test_should_yield_rejects_minimized():
    assert fullscreen.should_yield(_info(minimized=True), own_pid=1) is False


def test_should_yield_rejects_shell_windows():
    """桌面 / 任务栏本身恰好等于整屏，不排除会让面板永远不显示。"""
    for cls in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        assert fullscreen.should_yield(_info(class_name=cls), own_pid=1) is False


def test_should_yield_rejects_own_process():
    """自己的编辑框 / 菜单 / 提示不该让自己消失。"""
    assert fullscreen.should_yield(_info(), own_pid=999999) is False


def test_should_yield_handles_missing_info():
    assert fullscreen.should_yield(None, own_pid=1) is False
    assert fullscreen.should_yield(_info(hwnd=0), own_pid=1) is False


# ---------- 伪全屏（无边框铺满工作区，底下仍露着任务栏） ----------
def test_should_yield_for_borderless_workarea_window():
    """用户实报：无边框窗口铺满工作区、底下还露着任务栏时，面板照样弹出来。

    这类窗口的矩形等于 rcWork 而不是整屏，只判"覆盖整屏"必然漏掉。
    场景就是游戏的无边框窗口模式、某些播放器/客户端的"全屏"。
    """
    assert fullscreen.should_yield(
        _info(rect=WORK_AREA, work_rect=WORK_AREA, has_caption=False),
        own_pid=1) is True


def test_should_yield_rejects_bordered_workarea_window():
    """**伪全屏判据里最要命的一条**：标题栏条件不能省。

    最大化窗口恰好也覆盖工作区。放宽成"覆盖工作区就算全屏"，用户日常开着的
    最大化窗口会把面板永久赶走——比漏判伪全屏难查得多。
    只有 `WS_CAPTION` 能把"用户自己最大化的普通窗口"和"伪装成全屏的无边框窗口"
    分开：Windows 的最大化窗口必须有标题栏，否则用户既拖不动也关不掉。
    """
    assert fullscreen.should_yield(
        _info(rect=WORK_AREA, work_rect=WORK_AREA, has_caption=True),
        own_pid=1) is False
    # 最大化窗口（DWM 扩展边框，矩形比工作区还大）同样不让位
    assert fullscreen.should_yield(
        _info(rect=(-8, -8, 1928, 1048), work_rect=WORK_AREA,
              has_caption=True, maximized=True), own_pid=1) is False


def test_should_yield_borderless_requires_work_rect_and_caption_bit():
    """两个新字段的默认值都取"保守不让位"那侧，漏传不许静默放宽。

    `work_rect=None` / `has_caption=True` 就是这两条守卫本身——将来谁把默认值改成
    激进的（None 当"任意"、caption 默认 False），面板会在大面积场景下莫名消失。
    """
    assert fullscreen.should_yield(
        _info(rect=WORK_AREA, has_caption=False), own_pid=1) is False
    assert fullscreen.should_yield(
        _info(rect=WORK_AREA, work_rect=WORK_AREA), own_pid=1) is False
    # 直接构造（不走 _info）时默认值同样保守
    bare = fullscreen.ForegroundInfo(
        hwnd=1, pid=9, class_name="GameWindow", rect=WORK_AREA, monitor=77,
        monitor_rect=FULL_HD, minimized=False, maximized=False,
    )
    assert (bare.work_rect, bare.has_caption) == (None, True)
    assert fullscreen.should_yield(bare, own_pid=1) is False


def test_should_yield_borderless_tolerates_dpi_rounding():
    assert fullscreen.should_yield(
        _info(rect=(2, 2, 1918, 1038), work_rect=WORK_AREA, has_caption=False),
        own_pid=1) is True
    assert fullscreen.should_yield(
        _info(rect=(3, 3, 1917, 1037), work_rect=WORK_AREA, has_caption=False),
        own_pid=1) is False


def test_should_yield_borderless_does_not_leak_small_windows():
    """无边框的小窗口（浮层、工具窗）绝不能被当成全屏。"""
    assert fullscreen.should_yield(
        _info(rect=(100, 100, 900, 700), work_rect=WORK_AREA, has_caption=False),
        own_pid=1) is False


def test_should_yield_borderless_switch_can_be_turned_off(monkeypatch):
    """托盘没为它单开开关，但 config 常量是可用的逃生口。"""
    monkeypatch.setattr(config, "FULLSCREEN_INCLUDE_BORDERLESS", False)
    assert fullscreen.should_yield(
        _info(rect=WORK_AREA, work_rect=WORK_AREA, has_caption=False),
        own_pid=1) is False
    # 真全屏不受这个开关影响
    assert fullscreen.should_yield(_info(), own_pid=1) is True
    # 显式传参优先于 config（watcher 不传，所以现读 config 能即时生效）
    assert fullscreen.should_yield(
        _info(rect=WORK_AREA, work_rect=WORK_AREA, has_caption=False),
        own_pid=1, include_borderless=True) is True


def test_should_yield_rejects_more_shell_windows():
    """补了伪全屏判据后新能命中的系统壳层：Win11 桌面、任务视图 / Alt+Tab 宿主。"""
    for cls in ("WinUIDesktopWin32WindowClass", "MultitaskingViewFrame",
                "XamlExplorerHostIslandWindow"):
        assert fullscreen.should_yield(
            _info(rect=WORK_AREA, work_rect=WORK_AREA, class_name=cls,
                  has_caption=False), own_pid=1) is False


def test_uwp_core_window_is_not_excluded():
    """`Windows.UI.Core.CoreWindow` **不能**进排除表。

    开始菜单是它，但 UWP 应用（电影和电视 / Netflix 客户端）**全屏放视频时也是
    它**——排掉就再也认不出 UWP 视频全屏了。开始菜单只占屏幕左下一角（约
    640×800），本来就不会命中矩形判据，不需要靠类名排除。
    """
    assert fullscreen.should_yield(
        _info(class_name="Windows.UI.Core.CoreWindow"), own_pid=1) is True


# ---------- 防抖状态机 ----------
def test_watcher_requires_consecutive_hits():
    w = fullscreen.FullscreenWatcher(hold=2, probe=lambda: _info())
    assert w.update(own_monitor=77, own_pid=1) is False    # 第一次不算数
    assert w.update(own_monitor=77, own_pid=1) is True     # 连续第二次才让位


def test_watcher_requires_consecutive_misses():
    w = fullscreen.FullscreenWatcher(hold=2, probe=lambda: _info())
    w.update(own_monitor=77, own_pid=1)
    assert w.update(own_monitor=77, own_pid=1) is True
    w._probe = lambda: None                                 # 全屏退出
    assert w.update(own_monitor=77, own_pid=1) is True      # 一次不算数
    assert w.update(own_monitor=77, own_pid=1) is False     # 连续两次才回位


def test_watcher_single_blip_does_not_flip():
    """游戏启动/切分辨率时前景会短暂变成覆盖整屏的空壳，不能据此反转。"""
    frames = [_info(), None, None]
    w = fullscreen.FullscreenWatcher(hold=2, probe=lambda: frames.pop(0))
    assert w.update(own_monitor=77, own_pid=1) is False
    assert w.update(own_monitor=77, own_pid=1) is False
    assert w.update(own_monitor=77, own_pid=1) is False


def test_watcher_filters_by_monitor():
    """副屏全屏放视频时，主屏的面板不该消失。"""
    w = fullscreen.FullscreenWatcher(hold=1, probe=lambda: _info(monitor=88))
    assert w.update(own_monitor=77, own_pid=1) is False
    assert w.update(own_monitor=88, own_pid=1) is True


def test_watcher_without_monitor_info_does_not_filter():
    """查不到自己属于哪块屏（返回 0）时不做过滤：宁可显示，不要莫名消失。"""
    w = fullscreen.FullscreenWatcher(hold=1, probe=lambda: _info(monitor=88))
    assert w.update(own_monitor=0, own_pid=1) is True


def test_watcher_swallows_probe_failure():
    def _boom():
        raise OSError("探测挂掉")

    w = fullscreen.FullscreenWatcher(hold=1, probe=_boom)
    assert w.update(own_monitor=77, own_pid=1) is False


# ---------- Win32 适配层（只做不抛异常的冒烟） ----------
def test_probe_never_raises():
    info = fullscreen.foreground_info()
    assert info is None or info.hwnd > 0


def test_monitor_for_window_tolerates_bad_handle():
    assert fullscreen.monitor_for_window(0) == 0
    # 假句柄可能拿到 0 或默认屏句柄，反正不能抛
    assert isinstance(fullscreen.monitor_for_window(123456, "left"), int)


# ---------- 面板接线 ----------
def test_poll_passes_own_hwnd_and_dock_edge(taskbar):
    taskbar._quiet_watcher = _FakeWatcher([False])
    taskbar._dock("left", taskbar.screen().geometry())
    taskbar._poll_fullscreen()
    assert taskbar._quiet_watcher.seen["own_hwnd"] == int(taskbar.winId())
    assert taskbar._quiet_watcher.seen["dock_edge"] == "left"


def test_poll_fullscreen_drives_transitions(taskbar):
    taskbar._quiet_watcher = _FakeWatcher([True, True, False, False])
    taskbar._poll_fullscreen()
    assert taskbar._quiet and not taskbar.isVisible()
    taskbar._poll_fullscreen()                  # 结论没变 → 不该有什么变化
    assert taskbar._quiet and not taskbar.isVisible()
    taskbar._poll_fullscreen()
    assert not taskbar._quiet and taskbar.isVisible()
    taskbar._poll_fullscreen()
    assert not taskbar._quiet and taskbar.isVisible()


def test_enter_quiet_hides_and_stops_cursor_poll(taskbar):
    taskbar._dock("left", taskbar.screen().geometry())
    assert taskbar._cursor_timer.isActive()
    taskbar._enter_quiet()
    assert not taskbar.isVisible(), "全屏时连 6px 细条也不该压在游戏上"
    assert not taskbar._cursor_timer.isActive(), "全屏应用会把光标锁在边缘，轮询必须停"


def test_leave_quiet_returns_to_collapsed_dock(taskbar):
    """回位走"收起"而不是"展开"：不擅自展开挡住别人。"""
    geo, collapsed_x, _ = _dock_left(taskbar)
    taskbar._dock_slide_out()
    assert taskbar._dock_expanded
    taskbar._enter_quiet()
    taskbar._leave_quiet()
    assert taskbar.isVisible()
    assert not taskbar._dock_expanded
    assert taskbar.x() == collapsed_x
    assert taskbar._cursor_timer.isActive()


def test_enter_quiet_stops_running_animation(taskbar):
    _dock_left(taskbar)
    taskbar._dock_slide_out()
    assert taskbar._anim is not None
    taskbar._enter_quiet()
    assert taskbar._anim.state() != QAbstractAnimation.State.Running


def test_quiet_does_not_touch_window_flags(taskbar, monkeypatch):
    """让位只 hide()，绝不改 window flags。

    Qt 改 flag 会重建原生窗口 → HWND 变 → Acrylic 状态、事件过滤器、
    `_glass_hwnd`/`_glass_surface_alpha` 的幂等前提全部脱节。
    """
    calls = []
    monkeypatch.setattr(QWidget, "setWindowFlags",
                        lambda self, *a, **k: calls.append(a))
    hwnd_before = int(taskbar.winId())
    taskbar._enter_quiet()
    taskbar._leave_quiet()
    assert calls == []
    assert int(taskbar.winId()) == hwnd_before, "HWND 必须保持不变"


def test_quiet_keeps_manually_hidden_panel_hidden(taskbar):
    taskbar.hide()
    taskbar._enter_quiet()
    taskbar._leave_quiet()
    assert not taskbar.isVisible()


def test_toggle_visible_during_quiet_is_listened_to(taskbar):
    """让位期间的手动显隐要"说了算"，回位时不得擅自改回去。

    让位期间面板一定是隐藏的，所以第一次按热键 = 唤出、第二次按 = 收起。
    """
    # 1) 让位期间唤出 → 回位后仍然可见
    taskbar.hide()
    taskbar._enter_quiet()
    taskbar.toggle_visible()
    assert taskbar.isVisible()
    taskbar._leave_quiet()
    assert taskbar.isVisible()

    # 2) 让位期间收起 → 回位后仍然隐藏（不能因为"让位前是显示的"又弹出来）
    taskbar._enter_quiet()
    taskbar.toggle_visible()
    assert taskbar.isVisible()
    taskbar.toggle_visible()
    assert not taskbar.isVisible()
    taskbar._leave_quiet()
    assert not taskbar.isVisible()


def test_dragging_defers_quiet(taskbar):
    """拖动中不让位：窗口一隐藏，鼠标还按着的那次拖动就卡在半途了。"""
    taskbar._quiet_watcher = _FakeWatcher([True, True, True])
    taskbar._drag_pos = QPoint(10, 10)
    taskbar._poll_fullscreen()
    assert not taskbar._quiet
    assert taskbar.isVisible()


def test_disable_setting_while_quiet_restores_panel(taskbar):
    assert taskbar.act_quiet.isChecked()        # 默认开启
    taskbar._enter_quiet()
    assert not taskbar.isVisible()
    taskbar.act_quiet.setChecked(False)
    assert not taskbar._quiet
    assert taskbar.isVisible()
    assert not taskbar._quiet_timer.isActive()
    assert taskbar.db.get_setting("fullscreen_quiet") == "0"
    taskbar.act_quiet.setChecked(True)
    assert taskbar._quiet_timer.isActive()


def test_quiet_timer_runs_by_default(taskbar):
    assert taskbar._quiet_timer.isActive()
    assert taskbar._quiet_timer.interval() == config.FULLSCREEN_POLL_MS


# ---------- 静态守卫 ----------
def test_fullscreen_module_has_no_qt_dependency():
    """判定模块必须保持"纯 ctypes + 纯函数"，否则离屏测试覆盖不到。

    （`src/**` 里 Qt 窗口改造/flag 设置的坑已经够多了，这里把"能不能离屏测"
    这件事本身钉住。）
    """
    src = (SRC_ROOT / "ui" / "fullscreen.py").read_text(encoding="utf-8")
    offenders = [
        f"{i}: {line}" for i, line in enumerate(src.splitlines(), 1)
        if ("PySide6" in line or "PyQt" in line) and not line.lstrip().startswith("#")
    ]
    assert not offenders, f"fullscreen.py 不得依赖 Qt: {offenders}"
