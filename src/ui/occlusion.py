# -*- coding: utf-8 -*-
"""遮挡探测：面板滑出后要占的那块地方，现在有没有别人的窗口。

**为什么要做**：`ui/fullscreen.py` 那套问的是"前台窗口是不是全屏"——一个**全局
代理指标**：只看一个窗口，而且靠"矩形是否覆盖整屏/工作区"间接推导（用户实报：
伪全屏仍然弹）。它必然漏掉"矩形判据不成立、但视觉上确实盖着面板位置"的情况。

这里换成**就地测量**：直接问"面板要占的矩形里，现在有没有别人的窗口"。不问对方
是什么程序、是不是全屏，所以从根上不需要"全屏"这个概念。两者互补：
全屏判定管浮动态的整体让位，遮挡探测管停靠态"该不该弹出"。

⚠️ **必须用 `WindowFromPoint`，不能自己走 z-order 数矩形**。本机实测：z-order
顶端常年躺着"排在最上面却什么都不画"的层——NVIDIA GeForce Overlay
(`CEF-OSC-WIDGET`)、输入法宿主、桌面 `WorkerW`（353 个顶层窗口里一把就能抓到
好几个）。按矩形走查会把它们算成 100% 遮挡，面板于是永远不弹；而系统自己的命中
测试天然跳过分层窗口 α=0 与 `WS_EX_TRANSPARENT`（鼠标穿透）的层：

| 堆叠（后建者 z 更高，矩形完全重合） | `WindowFromPoint` 命中 |
|---|---|
| ① 普通带标题栏窗口 | ① |
| 再叠 ② 分层窗口 α=0 | ①（跳过 ②） |
| 再叠 ③ 分层 + 鼠标穿透 α=255 | ①（跳过 ③） |

所以建在命中测试上不是"更省事"，而是**唯一不必维护一长串特例的写法**。
`tests/test_occlusion.py::test_no_zorder_walk_in_src` 把这条决定钉死。

⚠️ `WindowFromPoint` 返回的是**最深一层**（实测点在我们自己窗口上拿到的是
`SysListView32` 这类子控件），必须 `GetAncestor(GA_ROOT)` 补出根窗口再比较。

⚠️ 坐标是**物理像素**：本进程是 PerMonitorV2 DPI 感知的，`WindowFromPoint` 直接
按物理像素解释入参（与项目里 mss / region 的约定一致，不混算 Qt 逻辑坐标）。

判据（纯函数 `is_blocker`）：别人的、不是桌面壳层、不是穿透层、不是 DWM 隐藏层
→ 挡。**最大化窗口也算挡**——用户要的就是"旁边有东西时别弹出来干扰工作"。它被挡
得可能很频繁，所以给了两条逃生口：停够 `OCCLUDE_HOVER_MS` 强行展开（不用记热
键），以及托盘开关整个关掉。

依赖：仅 ctypes + Win32，**不引入 Qt**——判定保持可离屏测试（有静态守卫）。
"""
from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from typing import NamedTuple

from .. import config

_IS_WINDOWS = sys.platform == "win32"

Rect = tuple[int, int, int, int]

# 桌面与系统任务栏：它们天然铺满屏或压在屏幕边缘，不是"别人挡着你"。不排除的话
# 面板永远不弹（桌面）或与系统任务栏同位时永远不弹（任务栏）。
# ⚠️ 与 fullscreen.py 的排除表**故意不同**：那边还要排除任务视图等"按下就消失的
# 壳层"（它们不算"用户在用的全屏应用"）；而这里的语义是"别盖住用户正在看的东西"，
# 任务视图正是用户在看的东西 → **不排除**，反而该拦住面板。
_SHELL_CLASSES = frozenset({
    "Progman",                          # 桌面（Win10）
    "WorkerW",                          # 壁纸宿主
    "WinUIDesktopWin32WindowClass",     # Win11 桌面（Progman 的继任者）
    "Shell_TrayWnd",                    # 系统任务栏：壳层，不是"别人挡着"
    "Shell_SecondaryTrayWnd",
})

_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_DWMWA_CLOAKED = 14
_MAX_CLASS_NAME = 256
_MAX_TITLE = 512
_THING_MAX = 18        # 提示条里"是谁挡着"的名字长度上限


class HitInfo(NamedTuple):
    """命中测试拿到的一个窗口（全是 Win32 原生值）。"""

    hwnd: int = 0
    pid: int = 0
    class_name: str = ""
    title: str = ""
    click_through: bool = False   # WS_EX_TRANSPARENT：鼠标穿透层
    cloaked: bool = False         # DWM 隐藏层（UWP 挂起后残留的壳窗口）
    ok: bool = False              # 取值是否成功；False 一律按"不挡"处理


class Verdict(NamedTuple):
    """一次探测的结论。"""

    blocked: bool
    ratio: float        # 被挡采样点占比，0.0~1.0
    thing: str          # 挡路者的名字（提示条用；空串 = 没有）
    sampled: int        # 实际采样点数


_EMPTY = Verdict(False, 0.0, "", 0)


# ---------- 纯判定（无 Win32 依赖，可全量离屏测试） ----------
def sample_points(rect: Rect, cols: int = config.OCCLUDE_COLS,
                  rows: int = config.OCCLUDE_ROWS) -> list[tuple[int, int]]:
    """在矩形内取 cols×rows 个**格心**点。

    取格心而不是网格交点/边界：外圈自然内缩半格，"只是边缘擦到一点"的窗口不会被
    当成阻挡（配合比例阈值一起用，构成一层天然容差）。矩形退化时返回空列表。
    """
    left, top, right, bottom = rect
    if right <= left or bottom <= top or cols < 1 or rows < 1:
        return []
    return [
        (left + (right - left) * (2 * i + 1) // (2 * cols),
         top + (bottom - top) * (2 * j + 1) // (2 * rows))
        for i in range(cols)
        for j in range(rows)
    ]


def shift_rect(rect: Rect, dx: int) -> Rect:
    """水平平移矩形（面板从收起位滑到展开位的位移换算）。"""
    return (rect[0] + dx, rect[1], rect[2] + dx, rect[3])


def is_blocker(hit: HitInfo | None, own_pid: int) -> bool:
    """这个窗口算不算"挡着面板"。纯函数。

    取不到信息（`ok=False`）时一律返回 False：宁可让面板多显示，也不要因为它
    莫名不弹——那是本功能最难查的故障。
    """
    if hit is None or not hit.ok or not hit.hwnd:
        return False
    if hit.pid == own_pid:
        return False        # 面板本体 / 提示条 / 自己的对话框 / 框选遮罩
    if hit.class_name in _SHELL_CLASSES:
        return False        # 桌面、系统任务栏
    # 鼠标穿透层与 DWM 隐藏层都不算挡：前者实测命中测试本来就会跳过（这里再挡一次
    # 是保险，万一真返回了也不会把面板锁死）；后者是 UWP 挂起后残留的壳窗口，仍在
    # z-order 里、矩形还停在旧位置，是最典型的"看着没人却被判挡"。
    return not (hit.click_through or hit.cloaked)


def ratio_of(flags: Sequence[bool]) -> float:
    """True 的占比；空序列返回 0.0（没采样 → 当作没被挡）。"""
    return (sum(1 for f in flags if f) / len(flags)) if flags else 0.0


def thing_name(hit: HitInfo | None) -> str:
    """提示条里给用户看的"是谁挡着"。优先窗口标题。

    `GetWindowTextW` 跨进程不会阻塞（不走 `WM_GETTEXT` 同步等对方消息循环），
    所以拿别人的标题是安全的。
    """
    if hit is None:
        return ""
    return ((hit.title or hit.class_name or "").strip())[:_THING_MAX]


def probe(rect: Rect | None, own_pid: int, *,
          window_at: Callable[[int, int], int] | None = None,
          describe: Callable[[int], HitInfo] | None = None,
          cols: int = config.OCCLUDE_COLS, rows: int = config.OCCLUDE_ROWS,
          threshold: float = config.OCCLUDE_RATIO) -> Verdict:
    """采样 + 判定。两个 Win32 调用都可注入（默认走真机）。

    同一个窗口盖住多个采样点时只查询一次（按根窗口句柄去重）。
    """
    points = sample_points(rect, cols, rows) if rect else []
    if not points:
        return _EMPTY
    if window_at is None:
        window_at = root_window_at
    if describe is None:
        describe = window_info

    seen: dict[int, HitInfo] = {}
    flags: list[bool] = []
    blocker: HitInfo | None = None
    for x, y in points:
        try:
            hwnd = int(window_at(x, y) or 0)
        except Exception:
            hwnd = 0        # 单点出错当作"那里没人"：宁可显示，不要莫名不弹
        if not hwnd:
            flags.append(False)
            continue
        hit = seen.get(hwnd)
        if hit is None:
            try:
                hit = describe(hwnd)
            except Exception:
                hit = None
            if hit is None:
                hit = HitInfo(hwnd=hwnd, ok=False)
            seen[hwnd] = hit
        if is_blocker(hit, own_pid):
            flags.append(True)
            if blocker is None:
                blocker = hit
        else:
            flags.append(False)

    ratio = ratio_of(flags)
    blocked = bool(points) and ratio >= threshold
    return Verdict(blocked, ratio, thing_name(blocker) if blocked else "", len(points))


class BlockerProbe:
    """带节流的遮挡探测：同一矩形在 `cache_ms` 内复用上次结论。

    ⚠️ 节流不是优化，是前提：`_poll_cursor` 每 60ms 跑一次，而一次 3×3 探测实测
    ≈2.3ms（单点 ≈0.26ms，比"逐像素读屏幕"便宜两个数量级，但仍不该按 60ms 花）。
    节流后最坏 ≈2.3ms / 350ms。改成"只在真要展开时才探"之后，常态开销是 0。

    时钟与探测入口都从外面注入，离屏测试不必 mock `time`。
    """

    def __init__(self, *, cache_ms: int | None = None,
                 cols: int | None = None, rows: int | None = None,
                 threshold: float | None = None,
                 window_at: Callable[[int, int], int] | None = None,
                 describe: Callable[[int], HitInfo] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # ⚠️ 用 None 哨兵在构造期读 config，而不是写进默认参数（默认参数在 import
        # 期就求值，之后再 monkeypatch config 不会生效）。
        self._cache_ms = config.OCCLUDE_CACHE_MS if cache_ms is None else cache_ms
        self._cols = config.OCCLUDE_COLS if cols is None else cols
        self._rows = config.OCCLUDE_ROWS if rows is None else rows
        self._threshold = config.OCCLUDE_RATIO if threshold is None else threshold
        self._window_at = window_at
        self._describe = describe
        self._clock = clock
        self._key: tuple[tuple[int, ...], int] | None = None
        self._at = 0.0
        self._last = _EMPTY
        self.probes = 0        # 真实探测次数，供测试断言节流生效

    def check(self, rect: Rect | None, own_pid: int, now: float | None = None) -> Verdict:
        """矩形为空（例如未停靠）时直接返回"没被挡"，不探测也不缓存。"""
        if not rect:
            return _EMPTY
        now = self._clock() if now is None else now
        key = (tuple(rect), int(own_pid))
        if self._key == key and (now - self._at) * 1000 < self._cache_ms:
            return self._last
        verdict = probe(rect, own_pid, window_at=self._window_at,
                        describe=self._describe, cols=self._cols, rows=self._rows,
                        threshold=self._threshold)
        self.probes += 1
        self._key, self._at, self._last = key, now, verdict
        return verdict

    def invalidate(self) -> None:
        """丢掉缓存（矩形变化由 key 处理；这里给"立刻重探"用）。"""
        self._key = None
        self._at = 0.0


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


class _Win32:
    """user32 / dwmapi 入口集合，argtypes / restype 只在这里设一次。

    ⚠️ 句柄类型的 argtypes 必须显式声明：不声明时 ctypes 会把 HWND 当 32 位
    C int 传递，64 位下高位被截断（glass.py 踩过同一个坑）。
    """

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32")
        self.WindowFromPoint = user32.WindowFromPoint
        self.WindowFromPoint.argtypes = [wintypes.POINT]
        self.WindowFromPoint.restype = wintypes.HWND

        self.GetAncestor = user32.GetAncestor
        self.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        self.GetAncestor.restype = wintypes.HWND

        self.GetClassNameW = user32.GetClassNameW
        self.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.GetClassNameW.restype = ctypes.c_int

        self.GetWindowTextW = user32.GetWindowTextW
        self.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.GetWindowTextW.restype = ctypes.c_int

        self.GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        self.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self.GetWindowThreadProcessId.restype = wintypes.DWORD

        get_window_long = getattr(user32, "GetWindowLongPtrW", None) \
            or user32.GetWindowLongW
        self.GetWindowLongPtrW = get_window_long
        self.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.GetWindowLongPtrW.restype = ctypes.c_ssize_t

        self.GetWindowRect = user32.GetWindowRect
        self.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.GetWindowRect.restype = wintypes.BOOL

        dwmapi = ctypes.WinDLL("dwmapi")
        self.DwmGetWindowAttribute = dwmapi.DwmGetWindowAttribute
        self.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint
        ]
        self.DwmGetWindowAttribute.restype = ctypes.c_long


_win32_cache: _Win32 | None = None
_win32_ready = False


def _win32() -> _Win32 | None:
    """惰性初始化；不可用时返回 None（调用方一律降级为"没被挡"）。"""
    global _win32_cache, _win32_ready
    if not _win32_ready:
        _win32_ready = True
        try:
            _win32_cache = _Win32()
        except Exception:
            _win32_cache = None
    return _win32_cache


def root_window_at(x: int, y: int) -> int:
    """该物理点上的**根**窗口句柄；取不到返回 0。

    ⚠️ 必须 `GetAncestor(GA_ROOT)`：`WindowFromPoint` 返回的是最深一层，实测在
    自己的窗口上会拿到 `SysListView32` 这种子控件，直接按类名判定会全错。
    """
    api = _win32()
    if api is None:
        return 0
    try:
        hwnd = int(api.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)
        if not hwnd:
            return 0
        root = int(api.GetAncestor(wintypes.HWND(hwnd), _GA_ROOT) or 0)
        return root or hwnd
    except Exception:
        return 0


def window_info(hwnd: int) -> HitInfo:
    """读一个窗口的判定所需字段；任何失败都返回 `ok=False`（→ 按"不挡"处理）。"""
    api = _win32()
    if api is None or not hwnd:
        return HitInfo()
    try:
        cls = ctypes.create_unicode_buffer(_MAX_CLASS_NAME)
        api.GetClassNameW(wintypes.HWND(hwnd), cls, _MAX_CLASS_NAME)
        title = ctypes.create_unicode_buffer(_MAX_TITLE)
        api.GetWindowTextW(wintypes.HWND(hwnd), title, _MAX_TITLE)
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        ex_style = int(api.GetWindowLongPtrW(wintypes.HWND(hwnd), _GWL_EXSTYLE)) \
            & 0xFFFFFFFF
        cloaked = ctypes.c_int(0)
        api.DwmGetWindowAttribute(wintypes.HWND(hwnd), _DWMWA_CLOAKED,
                                  ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return HitInfo(
            hwnd=int(hwnd),
            pid=int(pid.value),
            class_name=cls.value,
            title=title.value,
            click_through=bool(ex_style & _WS_EX_TRANSPARENT),
            cloaked=bool(cloaked.value),
            ok=True,
        )
    except Exception:
        return HitInfo(hwnd=int(hwnd), ok=False)


def window_rect(hwnd: int) -> Rect | None:
    """窗口的**物理**矩形 (left, top, right, bottom)；取不到返回 None。

    收起态窗口大半在屏幕外，这里的矩形会带负值/超出屏宽——这正是调用方要的：
    面板展开后的位置靠"当前矩形 + 逻辑位移×DPR"推出来（见
    `Taskbar._expanded_physical_rect`），不依赖"逻辑桌面坐标→物理坐标"的换算。
    """
    api = _win32()
    if api is None or not hwnd:
        return None
    try:
        rect = wintypes.RECT()
        if not api.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None
