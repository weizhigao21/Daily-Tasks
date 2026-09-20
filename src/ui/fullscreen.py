# -*- coding: utf-8 -*-
"""全屏免打扰：判断"前台应用是否正占满面板所在的那块屏"，供面板自动让位。

**为什么要做**：全屏游戏 / 视频 / 演示时，悬浮面板既不该压在上面，更不该还在
轮询光标——全屏应用常把光标锁在屏幕边缘或中央，`Taskbar._poll_cursor` 会把它
判成"用户靠近"，于是面板反复弹出、又被全屏窗口盖住，动画白跑还占着 GUI 线程。
所以让位的动作是**两条**：收走窗口 + 停掉轮询（由调用方 `_enter_quiet` 完成）。

**为什么按屏判定**：多屏下用户经常在一个屏干活、另一个屏全屏放视频。只问
"存在全屏应用吗"会让干活那块屏的面板无故消失。

⚠️ 这里**故意不用** `SHQueryUserNotificationState()`（Windows 抑制通知用的那套，
看起来是标准答案）。它只看**全局前台**：副屏全屏放视频时它返回 `QUNS_BUSY`，
于是主屏上正在输入的任务编辑框会凭空消失——比不做这个功能更难查。
本模块改成看"前景窗口的矩形是否覆盖**它自己那块屏**"，再把这块屏与面板所在屏
比对（`monitor_for_window`）。

⚠️ `maximized` 必须排除。`GetWindowRect` 给出的是 DWM 扩展边框，**最大化窗口的
矩形会在四边各外扩若干像素，足以覆盖整个显示器矩形**；不排除的话，用户日常开着
最大化窗口时面板会永久不出现——这是本功能最容易踩的坑，测试里单独钉死。

**两级判据**（第二级是后补的，见下）：

1. 矩形覆盖**整块显示器** → 让位。这是"真全屏"（独占全屏、F11、无边框铺满整屏）。
2. 矩形覆盖**工作区**（整屏减掉任务栏）**且窗口没有标题栏样式** → 让位。
   这是"伪全屏"：游戏的无边框窗口模式、某些播放器/客户端的"全屏"——它们铺满的是
   工作区，底下还露着任务栏。只判第 1 级会漏掉它们（用户实报：面板照样弹出来）。

⚠️ 第 2 级的**标题栏条件是绝对前提**，不是锦上添花。最大化窗口恰好就覆盖工作区，
放宽度量时它必然一起被卷进来 → 用户日常开着最大化窗口，面板永久消失。只有
`WS_CAPTION` 能把"用户自己最大化的普通窗口"和"伪装成全屏的无边框窗口"分开：
Windows 的最大化窗口必须有标题栏（否则用户既拖不动也关不掉）。
**放宽到"覆盖工作区就算"是最危险的改法**，`test_should_yield_rejects_maximized`
与 `test_should_yield_rejects_bordered_workarea_window` 两条一起钉住这个边界。

⚠️ 收起态的窗口不能问 `MonitorFromWindow`。停靠收起后窗口大半滑到屏幕外，它按
"重叠面积最大"会返回**隔壁**那块屏（项目里 `_target_screen()` 踩过同一个坑），
所以收起态一律用"露出的那条边"上的点去问 `MonitorFromPoint`。

依赖：仅 ctypes + Win32，**不引入 Qt**——判定逻辑保持可离屏测试（有静态守卫）。
"""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from ctypes import wintypes
from typing import NamedTuple

from .. import config

_IS_WINDOWS = sys.platform == "win32"

_SW_SHOWMINIMIZED = 2
_SW_SHOWMAXIMIZED = 3
_MONITOR_DEFAULTTONEAREST = 2
_MAX_CLASS_NAME = 256
_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000     # WS_BORDER | WS_DLGFRAME：窗口"有标题栏"的标志

# 每块显示器上都铺着一个"恰好等于整屏矩形"的系统窗口（桌面 / 任务栏等）。
# 它们不是"用户在用的全屏应用"，不排除的话面板永远不显示。
_EXCLUDED_CLASSES = frozenset({
    "Progman",                          # 桌面（Win10 全屏桌面窗口）
    "WorkerW",                          # 壁纸宿主（启用幻灯片/动态壁纸时在前台）
    "WinUIDesktopWin32WindowClass",     # Win11 桌面（Progman 的继任者）
    "Shell_TrayWnd",                    # 主任务栏
    "Shell_SecondaryTrayWnd",
    "TaskListThumbnailWnd",
    # 下面几个都是"铺满屏但没有标题栏"的系统壳层：补了伪全屏判据后它们同样能命中，
    # 而它们显然不是"用户在用的全屏应用"（任务视图按一下就没了，让面板闪一下很蠢）。
    "MultitaskingViewFrame",            # Win10 任务视图
    "XamlExplorerHostIslandWindow",     # Win11 任务视图 / Alt+Tab 宿主
    "ApplicationManager_DesktopShellWindow",
})

# ⚠️ `Windows.UI.Core.CoreWindow` 故意**不**排除：开始菜单 / 通知中心是它，而 UWP
# 应用（电影和电视、Netflix 客户端）**全屏播放视频时也是它**——排除掉就再也认不出
# 视频全屏了。开始菜单只占屏幕左下方一小块，本来就不会命中矩形判据。

# 允许矩形与显示器边界差几像素：DWM 边框与 DPI 取整都会带来 1~2px 偏差。
_RECT_TOLERANCE_PX = 2

# 取"露出的那条边"上的点时向窗口内侧缩进的像素（必须小于 DOCK_STRIP_PX）。
_EDGE_INSET_PX = 3


class ForegroundInfo(NamedTuple):
    """一次前景窗口采样（全是 Win32 原生值，不含 Qt 逻辑坐标，避免混算 DPI）。"""

    hwnd: int
    pid: int
    class_name: str
    rect: tuple[int, int, int, int]              # left, top, right, bottom（物理像素）
    monitor: int                                 # HMONITOR
    monitor_rect: tuple[int, int, int, int]      # 整屏矩形（rcMonitor）
    minimized: bool
    maximized: bool
    # 下面是伪全屏判据要用的。两个默认值都取"保守不让位"那一侧：漏传时宁可面板
    # 多显示，也不要因为它莫名消失（那是本功能最难查的故障）。
    work_rect: tuple[int, int, int, int] | None = None   # 工作区（rcWork，None=未知）
    has_caption: bool = True                             # 有标题栏样式（取不到时按有算）


# ---------- 纯判定（无 Win32 依赖，可全量离屏测试） ----------
def rect_covers(win: tuple[int, int, int, int],
                mon: tuple[int, int, int, int],
                tol: int = _RECT_TOLERANCE_PX) -> bool:
    """窗口矩形是否覆盖了整个显示器矩形（允许 tol 像素误差）。"""
    return (
        win[0] <= mon[0] + tol
        and win[1] <= mon[1] + tol
        and win[2] >= mon[2] - tol
        and win[3] >= mon[3] - tol
    )


def should_yield(info: ForegroundInfo | None, own_pid: int,
                 include_borderless: bool | None = None) -> bool:
    """这个前景窗口是否意味着"面板该让位"。

    不含按屏判断——那是 `FullscreenWatcher.update(own_monitor=…)` 的事。

    `include_borderless` 传 None 时现读 `config.FULLSCREEN_INCLUDE_BORDERLESS`
    （不在参数默认值里求值，这样测试 monkeypatch config 能立刻生效）。
    """
    if info is None or not info.hwnd:
        return False
    if info.pid == own_pid:
        return False        # 自己的编辑框/菜单/提示：不该让自己消失
    if info.minimized or info.maximized:
        return False        # 最大化窗口的 DWM 扩展边框会"覆盖"整屏，必须排除
    if info.class_name in _EXCLUDED_CLASSES:
        return False
    if rect_covers(info.rect, info.monitor_rect):
        return True         # 真全屏：覆盖整块显示器
    if include_borderless is None:
        include_borderless = config.FULLSCREEN_INCLUDE_BORDERLESS
    # 伪全屏：覆盖工作区（底下还露着任务栏）**且没有标题栏**。
    # 标题栏条件不能省——最大化窗口同样覆盖工作区，它靠 `maximized` 与这里双保险。
    return bool(
        include_borderless
        and info.work_rect
        and not info.has_caption
        and rect_covers(info.rect, info.work_rect)
    )


class FullscreenWatcher:
    """「该让位吗」的防抖状态机：连续 hold 次采样一致才翻转。

    为什么要防抖：游戏启动、切换分辨率/独占模式的那几帧里，前景窗口会短暂变成
    一个覆盖整屏的"空壳"；没有防抖就会让面板闪一下（隐藏又显示）。
    代价是进入与退出各滞后约 `hold × FULLSCREEN_POLL_MS`。
    """

    def __init__(self, hold: int = config.FULLSCREEN_HOLD_POLLS,
                 probe: Callable[[], ForegroundInfo | None] | None = None) -> None:
        self._hold = max(1, int(hold))
        self._probe = probe if probe is not None else foreground_info
        self._streak = 0
        self._last: bool | None = None
        self._quiet = False

    @property
    def quiet(self) -> bool:
        return self._quiet

    def update(self, own_hwnd: int = 0, dock_edge: str | None = None,
               own_monitor: int | None = None,
               own_pid: int | None = None) -> bool:
        """采一次并返回**防抖后**的结论。

        `own_monitor` 显式传入可跳过 Win32 查询（测试用）；传 0 表示"分不出是哪块
        屏"，此时不做按屏过滤——宁可多显示，也不要因为查不到而让面板莫名消失。
        """
        if own_monitor is None:
            own_monitor = monitor_for_window(own_hwnd, dock_edge) if own_hwnd else 0
        pid = os.getpid() if own_pid is None else own_pid
        try:
            info = self._probe()
        except Exception:
            info = None     # 探测失败一律当作"没有全屏"：宁可显示，不要消失
        raw = bool(
            info is not None
            and (not own_monitor or info.monitor == own_monitor)
            and should_yield(info, pid)
        )
        if raw == self._last:
            self._streak += 1
        else:
            self._last = raw
            self._streak = 1
        if self._streak >= self._hold:
            self._quiet = raw
        return self._quiet


# ---------- Win32 薄适配层 ----------
class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


class _Win32:
    """user32 入口集合，argtypes / restype 只在这里设一次。

    ⚠️ 必须显式声明句柄类型的 argtypes：不声明时 ctypes 会把 HWND / HMONITOR
    当 32 位 C int 传递，64 位下高位被截断（glass.py 里踩过同一个坑）。
    """

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32")
        self.GetForegroundWindow = user32.GetForegroundWindow
        self.GetForegroundWindow.restype = wintypes.HWND

        # GetWindowLongPtrW 只存在于 64 位 user32（32 位系统上是 GetWindowLongW），
        # 两者语义一致，取不到就退回 32 位版本。
        get_window_long = getattr(user32, "GetWindowLongPtrW", None) \
            or user32.GetWindowLongW
        self.GetWindowLongPtrW = get_window_long
        self.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.GetWindowLongPtrW.restype = ctypes.c_ssize_t

        self.GetClassNameW = user32.GetClassNameW
        self.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.GetClassNameW.restype = ctypes.c_int

        self.GetWindowRect = user32.GetWindowRect
        self.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.GetWindowRect.restype = wintypes.BOOL

        self.IsIconic = user32.IsIconic
        self.IsIconic.argtypes = [wintypes.HWND]
        self.IsIconic.restype = wintypes.BOOL

        self.GetWindowPlacement = user32.GetWindowPlacement
        self.GetWindowPlacement.argtypes = [
            wintypes.HWND, ctypes.POINTER(_WINDOWPLACEMENT)
        ]
        self.GetWindowPlacement.restype = wintypes.BOOL

        self.GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        self.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self.GetWindowThreadProcessId.restype = wintypes.DWORD

        self.MonitorFromWindow = user32.MonitorFromWindow
        self.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.MonitorFromWindow.restype = wintypes.HANDLE

        self.MonitorFromPoint = user32.MonitorFromPoint
        self.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        self.MonitorFromPoint.restype = wintypes.HANDLE

        self.GetMonitorInfoW = user32.GetMonitorInfoW
        self.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        self.GetMonitorInfoW.restype = wintypes.BOOL


_win32_cache: _Win32 | None = None
_win32_ready = False


def _win32() -> _Win32 | None:
    """惰性初始化 user32 入口；不可用时返回 None（调用方一律降级为"没有全屏"）。"""
    global _win32_cache, _win32_ready
    if not _win32_ready:
        _win32_ready = True
        try:
            _win32_cache = _Win32()
        except Exception:
            _win32_cache = None
    return _win32_cache


def _monitor_rects(monitor: int) -> tuple[
        tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    """某块屏的 (整屏矩形 rcMonitor, 工作区矩形 rcWork)；查不到返回 None。

    工作区 = 整屏减掉任务栏（以及用户自己留出的停靠区）。伪全屏判据要靠它：
    铺满工作区的无边框窗口在 rcMonitor 上是不成立的，只有 rcWork 认得出。
    """
    api = _win32()
    if api is None or not monitor:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not api.GetMonitorInfoW(wintypes.HANDLE(monitor), ctypes.byref(info)):
        return None
    m, w = info.rcMonitor, info.rcWork
    return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)


def _window_style(api: _Win32, hwnd: int) -> int:
    """窗口样式位；取不到返回 0（调用方据此按"有标题栏"保守处理）。"""
    try:
        return int(api.GetWindowLongPtrW(wintypes.HWND(hwnd), _GWL_STYLE)) & 0xFFFFFFFF
    except Exception:
        return 0


def monitor_for_window(hwnd: int, dock_edge: str | None = None) -> int:
    """窗口所在那块屏的句柄（分不出时返回 0）。

    `dock_edge` 为 "left" / "right" 时**必须**用露出的那条边上的点去问
    `MonitorFromPoint`：收起态窗口大半滑到屏幕外，`MonitorFromWindow` 按重叠
    面积会返回隔壁那块屏（`_target_screen()` 的同款坑）。
    """
    api = _win32()
    if api is None or not hwnd:
        return 0
    try:
        if dock_edge in ("left", "right"):
            rect = wintypes.RECT()
            if not api.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return 0
            mid_y = (rect.top + rect.bottom) // 2
            edge_x = rect.right - _EDGE_INSET_PX if dock_edge == "left" \
                else rect.left + _EDGE_INSET_PX
            point = wintypes.POINT(edge_x, mid_y)
            return int(api.MonitorFromPoint(point, _MONITOR_DEFAULTTONEAREST) or 0)
        return int(api.MonitorFromWindow(wintypes.HWND(hwnd),
                                         _MONITOR_DEFAULTTONEAREST) or 0)
    except Exception:
        return 0


def foreground_info() -> ForegroundInfo | None:
    """采一次前景窗口；任何失败都返回 None（当作"没有全屏"）。"""
    api = _win32()
    if api is None:
        return None
    try:
        hwnd = int(api.GetForegroundWindow() or 0)
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(_MAX_CLASS_NAME)
        if not api.GetClassNameW(wintypes.HWND(hwnd), buf, _MAX_CLASS_NAME):
            return None
        rect = wintypes.RECT()
        if not api.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        monitor = monitor_for_window(hwnd)
        rects = _monitor_rects(monitor)
        if rects is None:
            return None
        mon_rect, work_rect = rects
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        placement = _WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
        show_cmd = 0
        if api.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(placement)):
            show_cmd = int(placement.showCmd)
        style = _window_style(api, hwnd)
        return ForegroundInfo(
            hwnd=hwnd,
            pid=int(pid.value),
            class_name=buf.value,
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            monitor=monitor,
            monitor_rect=mon_rect,
            minimized=show_cmd == _SW_SHOWMINIMIZED
            or bool(api.IsIconic(wintypes.HWND(hwnd))),
            maximized=show_cmd == _SW_SHOWMAXIMIZED,
            work_rect=work_rect,
            # style == 0 意味着取值失败（任何真实窗口都至少有 WS_VISIBLE），
            # 此时按"有标题栏"算 → 不触发伪全屏让位，宁可多显示也不要莫名消失。
            has_caption=style == 0 or bool(style & _WS_CAPTION),
        )
    except Exception:
        return None
