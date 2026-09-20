# -*- coding: utf-8 -*-
"""遮挡探测：采样、判据、比例阈值、节流、降级，以及"被挡就不弹"的接线。

判定是纯函数 + 注入式命中测试，所以这里能全量离屏覆盖；真正的 Win32 调用
（`root_window_at` / `window_info` / `window_rect`）只做"不抛异常"的冒烟测试，
真机行为由双进程探针验证（见 CHANGELOG 的"未发布"段）。
"""
from pathlib import Path

from PySide6.QtCore import QPoint

from src.ui import occlusion

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# 模拟：面板停靠屏幕右缘、滑出后要占的物理矩形
PANEL = (3140, 500, 3440, 940)


def _hit(hwnd=4242, pid=999999, cls="Chrome_WidgetWin_1", title="微信",
         click_through=False, cloaked=False, ok=True):
    return occlusion.HitInfo(hwnd=hwnd, pid=pid, class_name=cls, title=title,
                             click_through=click_through, cloaked=cloaked, ok=ok)


def _const_hit(hit, calls=None):
    """所有采样点都命中同一个窗口。"""
    def window_at(x, y):
        if calls is not None:
            calls.append((x, y))
        return hit.hwnd
    return window_at


def _const_describe(hit):
    def describe(hwnd):
        describe.calls += 1
        return hit
    describe.calls = 0
    return describe


# ---------- 采样 ----------
def test_sample_points_count_and_inside():
    pts = occlusion.sample_points(PANEL, 3, 3)
    assert len(pts) == 9
    assert all(PANEL[0] <= x < PANEL[2] and PANEL[1] <= y < PANEL[3] for x, y in pts)


def test_sample_points_are_cell_centers():
    """取格心：外圈自然内缩半格，边缘"只是擦到一点"的窗口不会被当成阻挡。"""
    assert occlusion.sample_points((0, 0, 90, 90), 3, 3) == [
        (15, 15), (15, 45), (15, 75),
        (45, 15), (45, 45), (45, 75),
        (75, 15), (75, 45), (75, 75),
    ]


def test_sample_points_rejects_degenerate_rect_and_counts():
    assert occlusion.sample_points((10, 10, 10, 100), 3, 3) == []
    assert occlusion.sample_points((10, 10, 100, 10), 3, 3) == []
    assert occlusion.sample_points((0, 0, 10, 10), 0, 3) == []
    assert len(occlusion.sample_points((0, 0, 10, 10), 5, 2)) == 10


def test_shift_rect_moves_only_horizontally():
    assert occlusion.shift_rect((100, 200, 400, 700), -380) == (-280, 200, 20, 700)


# ---------- 判据（纯函数） ----------
def test_foreign_window_blocks():
    assert occlusion.is_blocker(_hit(), own_pid=1) is True


def test_plain_bordered_window_blocks():
    """⚠️ 与"全屏让位"里的排除规则**故意相反**，别照抄那边。

    那边必须排除最大化/带标题栏的窗口（否则面板永久消失）；这里正是要算挡——
    用户的原话是"旁边有东西的时候它不会弹出来干扰工作"。
    """
    assert occlusion.is_blocker(_hit(cls="Notepad", title="无标题 - 记事本"),
                                own_pid=1) is True


def test_own_process_never_blocks():
    """面板本体 / 提示条 / 自己的对话框都在这个位置上，不能自己挡自己。"""
    assert occlusion.is_blocker(_hit(pid=1), own_pid=1) is False


def test_shell_classes_never_block():
    """桌面与系统任务栏：不排除的话面板永远不弹（它们天然压着屏幕边缘）。"""
    for cls in ("Progman", "WorkerW", "WinUIDesktopWin32WindowClass",
                "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        assert occlusion.is_blocker(_hit(cls=cls), own_pid=1) is False


def test_click_through_and_cloaked_never_block():
    """穿透层（NVIDIA 覆盖层那种）与 DWM 隐藏层（UWP 挂起残留的壳窗口）不算挡。"""
    assert occlusion.is_blocker(_hit(click_through=True), own_pid=1) is False
    assert occlusion.is_blocker(_hit(cloaked=True), own_pid=1) is False


def test_missing_info_never_blocks():
    """查不到信息一律按"不挡"：宁可面板多显示，也不要它莫名不弹。"""
    assert occlusion.is_blocker(None, own_pid=1) is False
    assert occlusion.is_blocker(_hit(ok=False), own_pid=1) is False
    assert occlusion.is_blocker(_hit(hwnd=0), own_pid=1) is False


def test_ratio_of_handles_empty():
    assert occlusion.ratio_of([]) == 0.0
    assert occlusion.ratio_of([True, False]) == 0.5


def test_thing_name_prefers_title_then_class_and_truncates():
    assert occlusion.thing_name(_hit(title="微信", cls="Chrome_WidgetWin_1")) == "微信"
    assert occlusion.thing_name(_hit(title="", cls="CEF-OSC-WIDGET")) == "CEF-OSC-WIDGET"
    assert occlusion.thing_name(None) == ""
    assert len(occlusion.thing_name(_hit(title="x" * 60))) == 18


# ---------- 探测 ----------
def test_probe_all_points_same_window_queried_once():
    """同一个窗口盖住多个采样点只查询一次（9 个点 1 次 dwmapi + 1 次进程查询）。"""
    describe = _const_describe(_hit())
    v = occlusion.probe(PANEL, own_pid=1, window_at=_const_hit(_hit()),
                        describe=describe)
    assert v.blocked is True
    assert v.ratio == 1.0
    assert v.sampled == 9
    assert describe.calls == 1


def test_probe_ratio_threshold_boundary():
    """比例阈值：点名覆盖 6/9 算挡、3/9 不算（避免"擦到一角"就拦住面板）。"""
    def region(right_x, hwnd=5001):
        def window_at(x, y):
            return hwnd if x < right_x else 0
        return window_at

    near = occlusion.probe((0, 0, 300, 300), own_pid=1, window_at=region(200),
                           describe=_const_describe(_hit(hwnd=5001)))
    far = occlusion.probe((0, 0, 300, 300), own_pid=1, window_at=region(100),
                          describe=_const_describe(_hit(hwnd=5001)))
    assert near.ratio == 6 / 9 and near.blocked is True
    assert far.ratio == 3 / 9 and far.blocked is False
    assert near.thing == "微信"
    assert far.thing == ""


def test_probe_empty_rect_is_never_blocked():
    assert occlusion.probe(None, own_pid=1) == occlusion._EMPTY
    assert occlusion.probe((5, 5, 5, 5), own_pid=1).blocked is False


def test_probe_degrades_when_hit_raises():
    def boom(x, y):
        raise OSError("命中测试炸了")
    v = occlusion.probe(PANEL, own_pid=1, window_at=boom, describe=_const_describe(_hit()))
    assert v.blocked is False and v.ratio == 0.0
    assert v.sampled == 9


def test_probe_degrades_when_describe_raises():
    def boom(hwnd):
        raise OSError("查询炸了")
    v = occlusion.probe(PANEL, own_pid=1, window_at=_const_hit(_hit()), describe=boom)
    assert v.blocked is False


def test_probe_zero_hwnd_counts_as_free():
    v = occlusion.probe(PANEL, own_pid=1, window_at=lambda x, y: 0,
                        describe=_const_describe(_hit()))
    assert v.blocked is False and v.sampled == 9


# ---------- 节流 ----------
def test_probe_is_throttled_and_reruns_after_cache_window():
    t = [0.0]
    p = occlusion.BlockerProbe(cache_ms=350, window_at=_const_hit(_hit()),
                               describe=_const_describe(_hit()), clock=lambda: t[0])
    assert p.check(PANEL, own_pid=1).blocked is True
    t[0] = 0.1
    p.check(PANEL, own_pid=1)
    t[0] = 0.3
    p.check(PANEL, own_pid=1)
    assert p.probes == 1, "同一矩形在 350ms 内应复用结论"
    t[0] = 0.4
    p.check(PANEL, own_pid=1)
    assert p.probes == 2


def test_probe_reruns_immediately_for_other_rect_or_pid():
    t = [0.0]
    p = occlusion.BlockerProbe(window_at=_const_hit(_hit()),
                               describe=_const_describe(_hit()), clock=lambda: t[0])
    p.check(PANEL, own_pid=1)
    p.check(occlusion.shift_rect(PANEL, -380), own_pid=1)
    p.check(PANEL, own_pid=2)
    assert p.probes == 3


def test_probe_invalidate_forces_rerun():
    p = occlusion.BlockerProbe(window_at=_const_hit(_hit()),
                               describe=_const_describe(_hit()), clock=lambda: 0.0)
    p.check(PANEL, own_pid=1)
    p.invalidate()
    p.check(PANEL, own_pid=1)
    assert p.probes == 2


def test_probe_empty_rect_is_not_cached_or_counted():
    p = occlusion.BlockerProbe(window_at=_const_hit(_hit()),
                               describe=_const_describe(_hit()), clock=lambda: 0.0)
    assert p.check(None, own_pid=1).blocked is False
    assert p.probes == 0


# ---------- 真机调用：只验"不抛异常、形状正确" ----------
def test_win32_helpers_are_safe():
    assert isinstance(occlusion.root_window_at(10, 10), int)
    assert isinstance(occlusion.root_window_at(0, 0), int)
    hwnd = occlusion.root_window_at(10, 10)
    if hwnd:
        info = occlusion.window_info(hwnd)
        assert info.ok is True and isinstance(info.class_name, str)
    assert occlusion.window_rect(0) is None
    assert occlusion.window_rect(0xFFFFFFFF) is None


# ---------- 静态守卫 ----------
def test_occlusion_module_has_no_qt():
    """判定层必须保持"纯 ctypes + 纯函数"，否则离屏覆盖不到它。"""
    src = (SRC_ROOT / "ui" / "occlusion.py").read_text(encoding="utf-8")
    for bad in ("PySide6", "from .theme", "QTimer"):
        assert bad not in src, f"occlusion.py 不得引入 {bad}"


def test_no_zorder_walk_in_src():
    """静态守卫：不许自己走 z-order 数矩形，必须用系统命中测试。

    本机实测：z-order 顶端躺着 NVIDIA GeForce Overlay、输入法宿主、桌面 WorkerW
    这些"排在最上面却什么都不画"的层，矩形走查会把它们算成 100% 遮挡 → 面板永远
    不弹。命中测试天然跳过分层 α=0 与鼠标穿透层，是不必维护特例的唯一写法。
    """
    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for api in ("GetTopWindow", "GW_HWNDNEXT", "GW_HWNDPREV"):
                if api in line:
                    offenders.append(f"{path.relative_to(SRC_ROOT)}:{lineno} {api}")
    assert not offenders, f"发现 z-order 走查（应按行改回 WindowFromPoint）: {offenders}"


# ---------- 接线：被挡就不弹 ----------
class _FakeProbe:
    """按脚本返回结论的假探测：用来隔离"taskbar 怎么响应结论"。"""

    def __init__(self, blocked=True, thing="微信"):
        self.blocked = blocked
        self.thing = thing
        self.calls = 0

    def check(self, rect, own_pid):
        self.calls += 1
        return occlusion.Verdict(
            self.blocked, 1.0 if self.blocked else 0.0,
            self.thing if self.blocked else "", 9)


def _docked(bar, monkeypatch, blocked=True):
    """把面板停到左缘、光标钉在边缘热区，并把探测换成假的。"""
    geo = bar.screen().geometry()
    bar._dock("left", geo)
    bar._cursor_timer.stop()        # 只用手动 poll，避免 60ms 计时器掺进来
    probe = _FakeProbe(blocked)
    bar._occlude_probe = probe
    monkeypatch.setattr(bar, "_expanded_physical_rect", lambda: PANEL)
    bar._cursor_pos = lambda: QPoint(geo.x() + 8, bar.y() + bar.height() // 2)
    return geo, probe


def _hint(bar):
    return bar._occlude_hint


def test_blocked_holds_panel_and_shows_hint(taskbar, monkeypatch):
    """被挡：不展开、起强行展开计时器、弹提示、**并且不启动犹豫期计时器**。"""
    _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    assert taskbar._dock_expanded is False
    assert taskbar._force_timer.isActive() is True
    assert taskbar._expand_timer.isActive() is False
    assert _hint(taskbar) is not None and _hint(taskbar).isVisible()
    assert _hint(taskbar)._main.text() == "被「微信」挡住"


def test_unblocked_keeps_original_hesitation_path(taskbar, monkeypatch):
    """没被挡：完全走原来的路（犹豫期后展开），且不为提示条开窗口。"""
    _docked(taskbar, monkeypatch, blocked=False)
    taskbar._poll_cursor()
    assert taskbar._dock_expanded is False
    assert taskbar._expand_timer.isActive() is True
    assert taskbar._occlude_hint is None
    taskbar._on_expand_timeout()
    assert taskbar._dock_expanded is True


def test_hesitation_timeout_also_respects_blocker(taskbar, monkeypatch):
    """⚠️ 展开有**两条**路径，必须都拦：犹豫期到点时冒出来的窗口同样拦住。"""
    _docked(taskbar, monkeypatch, blocked=False)
    taskbar._poll_cursor()                      # 先把犹豫期计时器起起来
    taskbar._occlude_probe.blocked = True       # 这 100ms 里窗口冒出来了
    taskbar._on_expand_timeout()
    assert taskbar._dock_expanded is False
    assert taskbar._force_timer.isActive() is True
    assert _hint(taskbar).isVisible()


def test_force_expand_after_dwell(taskbar, monkeypatch):
    """逃生口：停够时间就照常展开（不必记热键）。"""
    _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    taskbar._on_force_expand()
    assert taskbar._dock_expanded is True
    assert taskbar._force_timer.isActive() is False
    assert _hint(taskbar).isVisible() is False


def test_force_expand_requires_cursor_still_at_edge(taskbar, monkeypatch):
    """中途移开就不改主意：回来时重新计时。（提前展开会平白挡住别人。）"""
    geo, _ = _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    taskbar._cursor_pos = lambda: QPoint(geo.x() + 4000, geo.y() + 4000)
    taskbar._on_force_expand()
    assert taskbar._dock_expanded is False


def test_hint_shown_once_per_approach(taskbar, monkeypatch):
    """光标贴在边缘时轮询每秒跑十几次，提示条不能跟着闪。"""
    _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    shown_at = _hint(taskbar)
    taskbar._poll_cursor()
    taskbar._poll_cursor()
    assert _hint(taskbar) is shown_at
    assert _hint(taskbar).isVisible()


def test_hint_and_timer_cleared_when_cursor_leaves(taskbar, monkeypatch):
    geo, _ = _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    assert taskbar._force_timer.isActive() is True
    taskbar._cursor_pos = lambda: QPoint(geo.x() + 4000, geo.y() + 4000)
    taskbar._poll_cursor()
    assert taskbar._force_timer.isActive() is False
    assert _hint(taskbar).isVisible() is False


def test_no_probe_when_panel_expanded(taskbar, monkeypatch):
    """展开态不探测：面板自己就占着那块地方，探了也是白花 2ms。"""
    _, probe = _docked(taskbar, monkeypatch, blocked=True)
    taskbar._dock_slide_out()
    probe.calls = 0
    taskbar._poll_cursor()
    assert probe.calls == 0


def test_toggle_off_skips_probe_entirely(taskbar, monkeypatch):
    _, probe = _docked(taskbar, monkeypatch, blocked=True)
    taskbar.act_occlude.setChecked(False)
    taskbar._poll_cursor()
    assert probe.calls == 0
    assert taskbar._expand_timer.isActive() is True     # 回到原来的行为
    assert taskbar.db.get_setting("occlude_aware") == "0"


def test_toggle_off_clears_hint(taskbar, monkeypatch):
    _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    taskbar.act_occlude.setChecked(False)
    assert taskbar._force_timer.isActive() is False
    assert _hint(taskbar).isVisible() is False
    taskbar.act_occlude.setChecked(True)
    assert taskbar.db.get_setting("occlude_aware") == "1"


def test_expanding_clears_occlude_state(taskbar, monkeypatch):
    """展开走 `stop_dock_timers()`，它必须顺手把探测的临时状态也清干净。"""
    _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    taskbar._on_force_expand()
    assert taskbar._force_timer.isActive() is False
    assert taskbar._occlude_notified is False


def test_quiet_enter_clears_occlude_state(taskbar, monkeypatch):
    """让位（全屏）时提示条不能留在屏幕上。"""
    _docked(taskbar, monkeypatch, blocked=True)
    taskbar._poll_cursor()
    taskbar._enter_quiet()
    assert taskbar._force_timer.isActive() is False
    assert _hint(taskbar).isVisible() is False
    taskbar._leave_quiet()
