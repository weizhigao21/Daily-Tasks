# -*- coding: utf-8 -*-
"""Windows 毛玻璃后端：原生 Acrylic/Mica 模糊 + 失败自动降级。

调用方约定：
    enabled = apply_glass(hwnd)
    enabled 为 True → 原生模糊已生效，Qt 侧只需半透明底板；
    enabled 为 False → 原生不可用，Qt 侧改用自绘玻璃（theme 的不透明底板）。

实现层级（自上而下依次尝试）：
    1. Win11 22H2+  DWMWA_SYSTEMBACKDROP_TYPE = 3 (Acrylic)
    2. Win11 21H2    DWMWA_MICA_EFFECT      = 1
    3. Win10 1803+   SetWindowCompositionAttribute ACCENT_ENABLE_ACRYLICBLURBEHIND
    4. 任意失败 → apply_glass() 返回 False，由调用方降级为自绘玻璃

Win10 那条通路（第 3 级）在窗口移动/缩放时会严重迟滞，属于 OS bug，
缓解办法是移动期临时**撤掉**它（`suspend()`）。但撤掉之后窗口不能只剩一块纯色
——那正是用户反馈的"移动的时候毛玻璃效果直接没有了，也只有颜色"。
所以移动期由 `capture_window_surface()` 在撤之前拍一张**定格快照**（模糊后的
桌面 + 深色 tint + 控件本身），移动全程把这一张图当作背景画回去，观感与静止时
一致；详见该函数的说明。

抓屏一律用**一次分区 BitBlt**（`sct.grab`），绝不逐像素读——那会把 GUI 线程
钉死成"程序卡死"（单次调用≈一帧，实测数据见 `capture_window_surface` 内注释）。
不要试图改用 ACCENT_ENABLE_BLURBEHIND "降级" —— 本机实测它输出纯白，见文件末尾。

依赖：ctypes（Win32）+ `core.screen`（复用项目统一的截屏入口，不直接引 mss）。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import NamedTuple

_IS_WINDOWS = sys.platform == "win32"

# ---- DWM 属性常量 ----
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWA_MICA_EFFECT = 1029  # Win11 21H2 私有属性

# DWMSBT_* 背景类型（Win11 22H2+）
_DWMSBT_AUTO = 0
_DWMSBT_NONE = 1
_DWMSBT_MICA2 = 2      # Mica（主窗背景采样）
_DWMSBT_ACRYLIC3 = 3   # Acrylic（桌面背景采样，悬浮窗用这个）

# SetWindowCompositionAttribute（Win10 兜底）
_WCA_ACCENT_POLICY = 19
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_ACCENT_ENABLE_BLURBEHIND = 3   # ⚠️ 见文件末尾说明：本机实测不可用，勿启用


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),   # AABBGGRR
        ("AnimationId", ctypes.c_int),
    ]


class _WinCompAttrData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _dwm_set(hwnd: int, attr: int, value: int) -> bool:
    """写入一个 DWORD 型 DWM 属性，成功返回 True。"""
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        val = ctypes.c_int(value)
        res = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attr),
            ctypes.byref(val), ctypes.sizeof(val),
        )
        return res == 0
    except Exception:
        return False


def _apply_win11_acrylic(hwnd: int) -> bool:
    """Win11 22H2+：系统级 Acrylic 背景。"""
    return _dwm_set(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_ACRYLIC3)


def _apply_win11_mica(hwnd: int) -> bool:
    """Win11 21H2：Mica 效果（早期版本）。"""
    return _dwm_set(hwnd, _DWMWA_MICA_EFFECT, 1)


def _set_win10_accent(hwnd: int, state: int, tint_abgr: int = 0) -> bool:
    """写一次 SetWindowCompositionAttribute(ACCENT_POLICY)，成功返回 True。

    两种 AccentState 共用这一条通路：
        _ACCENT_ENABLE_ACRYLICBLURBEHIND 硬模糊（静止时用，移动会迟滞）
        0 (ACCENT_DISABLED)              完全关闭（移动期撤掉时用）
    """
    try:
        user32 = ctypes.WinDLL("user32")
        set_attr = user32.SetWindowCompositionAttribute
        set_attr.argtypes = [wintypes.HWND, ctypes.POINTER(_WinCompAttrData)]
        set_attr.restype = ctypes.c_int

        policy = _AccentPolicy()
        policy.AccentState = state
        policy.AccentFlags = 0x20 | 0x40 | 0x80 | 0x100  # 四边都生效，避免边框描边
        policy.GradientColor = tint_abgr

        data = _WinCompAttrData()
        data.Attribute = _WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)
        return bool(set_attr(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def _apply_win10_acrylic(hwnd: int, tint_abgr: int = 0xB31B1D21) -> bool:
    """Win10 1803+：SetWindowCompositionAttribute 亚克力模糊（硬模糊）。

    tint_abgr 为 AABBGGRR 格式，默认深灰 #1B1D21 带 0xB3(70%) 不透明度。
    """
    return _set_win10_accent(hwnd, _ACCENT_ENABLE_ACRYLICBLURBEHIND, tint_abgr)


def _disable_win10_acrylic(hwnd: int) -> None:
    """关闭 Win10 亚克力（拖动期间临时关闭用）。"""
    _set_win10_accent(hwnd, 0)


def apply_dark_titlebar(hwnd: int) -> bool:
    """让原生标题栏（对话框用）跟随暗色。"""
    return _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1)


def _client_rect(hwnd: int):
    """取窗口**客户区**（不含标题栏与边框）在屏幕上的矩形，物理像素。

    用客户区而不是 `GetWindowRect`：快照要贴回 Qt 控件自己的坐标系，
    带上标题栏/阴影边框就对不齐了。

    必须显式写 argtypes —— 不写的话 ctypes 会把手柄当 32 位 C int 传，
    64 位下句柄被截断，调用直接失败。
    """
    try:
        user32 = ctypes.WinDLL("user32")
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL

        box = wintypes.RECT()
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(box)):
            return None
        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
            return None
        return origin.x, origin.y, box.right - box.left, box.bottom - box.top
    except Exception:
        return None


class SurfaceShot(NamedTuple):
    """窗口客户区的一次屏幕快照（移动期冒充原生模糊用）。"""

    width: int
    height: int
    pixels: bytes        # BGRA，可直接喂 QImage.Format_RGB32
    color: str | None    # 边缘采样得到的中位色；取不到为 None


# 采样既有下限也有上限：太低会让"磨砂灰"直接 fallback 成纯黑，太高则移动的
# 瞬间会闪出一块浅色，两者都偏离"暗色玻璃"的设计。
_SAMPLE_MIN = 28
_SAMPLE_MAX = 110

# 边缘采样竖带距客户区左右边的距离，以及竖带宽度。取边缘是因为那里没有被
# 控件盖住，是纯底板；用户看到的"磨砂灰"就是这个值。
_EDGE_INSET = 8
_EDGE_BAND = 3

# 复用的截屏实例。不要每次采图都新建：`open_screen()` 的构造有实打实的代价
# （本机实测首次 44ms、复用后稳定 12ms），而这段代码跑在拖动路径上。
# 更进一步：`warm_up_screen()` 在启动时就构造好，把这个代价彻底挪出拖动路径。
_sct = None


def _screen():
    """懒加载并复用截屏实例（失败则返回 None，由调用方回退）。"""
    global _sct
    if _sct is None:
        from ..core.screen import open_screen

        _sct = open_screen()
    return _sct


def warm_up_screen() -> None:
    """提前构造截屏实例，把它的代价从"拖动的第一帧"上挪开。

    只有 `capture_window_surface()` 用到这个实例，而它只在窗口**开始移动**时被
    调用 —— 首次调用要额外付出 mss 构造的代价（本机实测首次 44ms、之后稳定
    12ms），偏偏这段构造还发生在 Windows 的 modal move loop 里面。启动时预热
    一次，之后每次抓图都只剩一次 BitBlt 的固定开销。

    失败一律吞掉：拿不到屏幕不应该妨碍程序启动（真正的截屏路径各自有兜底）。
    """
    try:
        _screen()
    except Exception:
        pass


def _edge_color(pixels: bytes, width: int, height: int) -> str | None:
    """从快照里取左右内侧窄竖带的中位色，即"用户眼中的静止底板色"。

    静止观感 = Acrylic 的 tint(70% 深色) 叠在**模糊后的桌面**上，所以它随壁纸
    而变，写死主题深色会让移动瞬间从"磨砂灰"跳成"纯黑"（实测静止
    `RGB(85,82,81)`，而主题色只有 `RGB(27,29,33)`）。用中位数而不是均值来抵抗
    个别像素（描边、圆角过渡）的干扰。
    """
    if not pixels or width <= 0 or height <= 0:
        return None
    # 左右各一条 _EDGE_BAND 宽的竖带（不是单列）：带内取中位数才能如注释所说
    # 抵抗描边、圆角过渡像素的干扰，单列采样一被 1px 描边命中就整体偏色。
    xs = [x for x in range(_EDGE_INSET, _EDGE_INSET + _EDGE_BAND) if 0 <= x < width]
    xs += [
        x
        for x in range(width - _EDGE_BAND - _EDGE_INSET, width - _EDGE_INSET)
        if 0 <= x < width
    ]
    if not xs:
        return None
    samples = []
    for y in range(height):
        row = y * width
        for x in xs:
            i = (row + x) * 4          # BGRA
            samples.append((pixels[i + 2], pixels[i + 1], pixels[i]))
    if not samples:
        return None

    def median(channel: int) -> int:
        vals = sorted(p[channel] for p in samples)
        return vals[len(vals) // 2]

    rgb = [max(_SAMPLE_MIN, min(_SAMPLE_MAX, median(i))) for i in range(3)]
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def capture_window_surface(hwnd: int) -> SurfaceShot | None:
    """趁原生模糊还在，抓一张窗口客户区的**真实屏幕像素**。

    为什么需要它：移动期必须撤掉会迟滞的 Win10 Acrylic（见下方 OS bug 说明），
    而 Qt 自己补的底板只能是一块**纯色** —— 用户看到的就是"毛玻璃效果直接没有
    了，也只有颜色"。所以这里在撤之前把窗口此刻的样子整体拍下来（模糊后的桌面
    + 深色 tint + 控件本身），移动期间由 Qt 把这一张**定格图**画回窗口：拖动全程
    与静止时看起来一模一样，而系统那边只是在合成一个普通窗口（跟手、无迟滞）。

    为什么定格不会露馅：Acrylic 的模糊是极低频的，定格的模糊背景与实时模糊在
    视觉上无法区分；Windows 自己对付"卡住的窗口"用的也是同一招（ghost window）。

    代价：一次 BitBlt + 一次中位色统计，本机实测约 12ms，且**每次拖动只做一次**、
    不是每帧（拖动过程中不需要再拍）。

    抓屏一律分区一次 BitBlt（下面这一句 `sct.grab`），绝不逐像素读 —— 细节见
    函数体内的注释与 `tests/test_screen.py` 的静态守卫。

    失败一律返回 None，由调用方回退到主题底板色。
    """
    rect = _client_rect(hwnd)
    if rect is None:
        return None
    left, top, width, height = rect
    if width < 40 or height < 40:
        return None
    # ⚠️ 绝不要改成逐像素读（GetPixel）：屏幕 DC 上每次调用都会强制同步一次桌面
    # 合成，实测**恰好一帧**（本机 12.12ms，中位=最大）。逐像素读一块 3×36 的窄带
    # 就是 108 × 12ms ≈ 1.3s，而这段跑在 GUI 线程上，现象就是整个程序卡死。
    # 分区一次 BitBlt（下面这句）约 12ms。静态守卫同上。
    try:
        sct = _screen()
        if sct is None:
            return None
        raw = bytes(sct.grab({
            "left": left, "top": top, "width": width, "height": height,
        }).raw)
    except Exception:
        # 实例可能已失效（分辨率变化等）：丢掉缓存，下次重建。
        global _sct
        _sct = None
        return None
    if len(raw) < width * height * 4:
        return None
    return SurfaceShot(width, height, raw, _edge_color(raw, width, height))


def shot_to_pixmap(shot: SurfaceShot):
    """把快照转成可绘制的 QPixmap（失败返回 None）。

    直接复用 BGRA 字节：Qt 的 `Format_RGB32` 在 little-endian 上的内存布局就是
    B,G,R,X，与 mss 的输出一致，不需要逐像素转换。必须 `copy()` —— QImage 只是
    引用这块内存，而 bytes 随时可能被回收。
    """
    try:
        from PySide6.QtGui import QImage, QPixmap
    except Exception:
        return None
    if not shot.pixels:
        return None
    image = QImage(shot.pixels, shot.width, shot.height,
                   QImage.Format.Format_RGB32).copy()
    if image.isNull():
        return None
    return QPixmap.fromImage(image)


def apply_glass(hwnd: int) -> bool:
    """给窗口应用原生毛玻璃。

    返回 True 表示原生模糊生效；False 表示环境不支持，调用方应降级自绘玻璃。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    # Win11 22H2 Acrylic → Win11 Mica → Win10 Acrylic
    if _apply_win11_acrylic(hwnd):
        return True
    if _apply_win11_mica(hwnd):
        return True
    return _apply_win10_acrylic(hwnd)


# ---------- Windows 10 的"移动期 Acrylic 迟滞" ----------
# 这是**未修复的 OS bug**，不是本项目的代码问题：Win10 1903+ 上用
# SetWindowCompositionAttribute 挂 ACCENT_ENABLE_ACRYLICBLURBEHIND 之后，
# 窗口被拖动/缩放时会严重滞后于鼠标（高轮询率鼠标更明显，松手后窗口还会
# "追尾"，期间 CPU 飙升），观感就是"拖不动、有奶油感"。
# 三个独立来源可复现：EarTrumpet #349（该方案原始文档作者本人确认是 OS bug）、
# FluentWPF #42、DevToys #1258（Win10 19045 上甚至完全不渲染却照样卡）。
#
# 缓解办法是移动期**把硬模糊整个撤掉**（`suspend()`），同时由调用方把撤之前
# 拍下的**定格快照**画回窗口（`capture_window_surface()`）——只撤不补就是用户
# 反馈的"移动时毛玻璃效果直接没有了，也只有颜色"。
#
# ⚠️ 别用 ACCENT_ENABLE_BLURBEHIND"降级"（这是 FluentWPF 推荐的做法，但在本机无效）。
# 实测（Win10 19041 / 纯白衬底 / 抓真实屏幕，窗口底板样区）：
#     ACRYLICBLURBEHIND(4) + tint       → RGB(85,82,81)  深灰磨砂（正常）
#     BLURBEHIND(3)  tint=0             → RGB(255,255,255) 纯白
#     BLURBEHIND(3)  tint=0xB31B1D21    → RGB(255,255,255) 纯白（不吃 GradientColor）
#     BLURBEHIND(3)  tint=0xE61B1D21    → RGB(255,255,255) 纯白
#     之后重新 apply_glass()            → RGB(255,255,255) 切不回 Acrylic
# 即：BLURBEHIND 在本机会把窗口整块变成纯白、且恢复不了（用户反馈的"拖动时变纯白"）。
# 撤掉(ACCENT_DISABLED) + 不透明底板的实测值是 RGB(27,29,33)，稳定不发白——所以走它。
_MOVE_SETTLE_MS = 260   # 最后一次移动/缩放过去多久之后恢复硬模糊
_ARM_DELAY_MS = 450     # 开窗落位后多久才启用移动期让路（否则开窗时会闪一下）


class GlassDialogMixin:
    """给任意 QDialog 提供一致的毛玻璃观感（原生成功则透背景，否则降级）。

    用法：
        class MyDialog(GlassDialogMixin, QDialog):
            def __init__(self):
                super().__init__()
                self.setup_glass()     # 内部会设置样式表

            # 需要记住窗口位置时（跨次打开 / 跨次启动）：
                self.setup_glass(db=db, state_key=window_state.WINDOW_TASK_DIALOG)

    为什么对话框不能只靠 QSS 实现玻璃：原生 Acrylic 需要窗口半透明
    （边框去掉 + 背景透出），而一旦透明，QSS 的不透明底板又会盖住模糊效果，
    两者不能同时存在。所以由本 mixin 按"是否真的拿到原生模糊"二选一地决定，
    并在移动/缩放期间临时撤掉 Acrylic（见上方 Win10 迟滞说明），同时把撤之前
    拍下的**定格快照**画回窗口，让拖动过程中的玻璃观感不断裂。
    """

    def setup_glass(self, db=None, state_key: str = "") -> None:
        """在 __init__ 末尾调用：装好降级样式，首次显示时再尝试原生模糊。

        传 `db` + `state_key` 即启用**窗口位置记忆**（见 `ui.window_state`）：
        显示时还原上次的位置，隐藏时记下当前位置。
        """
        from PySide6.QtCore import QTimer

        from . import theme

        self.setObjectName("glassDialog")
        # 先按"无原生模糊"着色：即使原生模糊永远失败，观感也一致且文字可读
        self.setStyleSheet(theme.dialog_qss("opaque"))
        # ---- 毛玻璃状态机 ----
        # `_glass_qss_mode` 是**唯一**的底板样式状态（"glass" / "opaque"），不拆成
        # 多个布尔量：第五轮"收回背景消失"就是状态漏组合造成的，用一个变量表示
        # 可以让非法组合根本无法表达。定格快照是画在对话框自身 paintEvent 上的
        # 一层，不参与这个状态。
        self._glass_resolved = False    # 是否已尝试过原生模糊（只做一次）
        self._glass_on = False          # 原生模糊当前是否生效
        self._glass_qss_mode = "opaque"
        self._glass_qss_bg = None       # 当前底板用的色（None = 主题默认）
        self._glass_freeze = None       # 移动期的定格快照（QPixmap）
        self._glass_move_armed = False  # 开窗落位前不响应 move/resize
        self._glass_shown_at = 0.0
        self._glass_geo = None          # 上次处理过的几何，挡掉纯重排事件
        self._glass_settle = QTimer(self)
        self._glass_settle.setSingleShot(True)
        self._glass_settle.setInterval(_MOVE_SETTLE_MS)
        self._glass_settle.timeout.connect(self._restore_glass)
        # ---- 窗口位置记忆 ----
        self._state_db = db
        self._state_key = state_key if db is not None else ""
        self._state_restored = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        import time

        from . import window_state

        self._glass_shown_at = time.monotonic()
        self._glass_geo = None
        if self._state_key and not self._state_restored:
            # 首次显示时布局已经定型，此刻还原位置最准（构造期尺寸还没定）
            self._state_restored = True
            window_state.restore(self._state_db, self._state_key, self)
            self._glass_geo = None      # 还原动作会引发 moveEvent，别让它算作用户拖动
        if self._glass_resolved:
            return
        self._glass_resolved = True
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        apply_dark_titlebar(hwnd)   # 原生标题栏跟随暗色，否则顶部一条白很扎眼
        if apply_glass(hwnd):
            # 原生模糊已在窗口后方生效 → 底板转透明，让模糊透出来
            self._glass_on = True
            self._apply_dialog_qss("glass")
            self._glass_move_armed = True

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        from . import window_state

        if self._state_key:
            window_state.remember(self._state_db, self._state_key, self)

    def paintEvent(self, event) -> None:  # noqa: N802
        """画底板；移动期把定格快照铺满客户区。

        画在 `paintEvent` 里（而不是走样式表）是因为 QSS 没法引用一块内存里的
        pixmap。子控件在此之后绘制，位置与快照里的一致，观感即"静止时的样子"。
        """
        super().paintEvent(event)
        pixmap = self._glass_freeze
        if pixmap is None:
            return
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

    def _apply_dialog_qss(self, mode: str, bg: str | None = None) -> None:
        """幂等切换底板：transparent（透原生模糊）/ 不透明（自己画底）。

        `bg` 是移动期用的底板色（来自定格快照的边缘采样），所以幂等键必须同时
        含颜色——否则"模式没变但颜色该换"会被漏掉。它只是快照之下的兜底：
        万一快照没画上，窗口也不会变成透明。

        必须幂等：本方法会被 moveEvent 频繁调用，而 `setStyleSheet` 会重新
        polish 整个对话框子树，重复调用既浪费又可能触发连锁重排。
        """
        if self._glass_qss_mode == mode and self._glass_qss_bg == bg:
            return
        from . import theme

        self._glass_qss_mode = mode
        self._glass_qss_bg = bg
        self.setStyleSheet(theme.dialog_qss(mode, bg))

    def _set_freeze(self, shot: SurfaceShot | None) -> None:
        """设置 / 清除移动期的定格快照。"""
        pixmap = shot_to_pixmap(shot) if shot is not None else None
        if pixmap is not None:
            # 快照按物理像素抓的，换算成逻辑尺寸铺满控件
            pixmap.setDevicePixelRatio(self.devicePixelRatioF() or 1.0)
        self._glass_freeze = pixmap
        self.update()

    def _freeze_stale(self) -> bool:
        """快照尺寸是否已经和当前客户区对不上（拖动中被缩放）。"""
        pixmap = self._glass_freeze
        if pixmap is None:
            return False
        ratio = pixmap.devicePixelRatio() or 1.0
        return (round(pixmap.width() / ratio), round(pixmap.height() / ratio)) \
            != (self.width(), self.height())

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._throttle_glass()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._throttle_glass(resized=True)

    def _throttle_glass(self, *, resized: bool = False) -> None:
        """窗口被移动/缩放时撤掉 Acrylic —— Win10 上它是迟滞元凶。

        撤之前先拍一张定格快照（`capture_window_surface`）并把它画回窗口，
        这样拖动过程中玻璃观感仍在（用户反馈的"移动时毛玻璃直接没了、只剩颜色"
        就是缺了这一步）。每次几何变化都重置计时，因此只有真正停下来之后才会
        恢复 Acrylic。

        不要试图改成 ACCENT_ENABLE_BLURBEHIND（FluentWPF 的做法）——本机实测
        它输出纯白且切不回来，见文件上方实测记录。
        """
        import time

        from . import theme

        if not self._glass_on or not self._glass_move_armed:
            return
        geo = self.geometry()
        if geo == self._glass_geo:
            return      # 纯重排（样式表变更引起的）不算"在移动"，否则会来回抖
        self._glass_geo = geo
        if time.monotonic() - self._glass_shown_at < _ARM_DELAY_MS / 1000:
            return      # 开窗落位阶段：此时撤模糊会看见"闪一下不透明"
        if self._glass_qss_mode == "glass" or self._freeze_stale():
            try:
                hwnd = int(self.winId())
            except Exception:
                return
            # 拖动中被缩放 → 旧快照尺寸对不上，先丢掉（宁可退成纯色，也不画歪）。
            # 缩放得不到可用快照，所以只画纯色；等本轮拖动结束、下次拖动再重拍。
            self._set_freeze(None)
            shot = None if resized else capture_window_surface(hwnd)
            # ⚠️ 必须赶在 suspend() 之前拍：此刻系统还在画模糊，快照里才是
            # "用户眼中的静止观感"。撤掉之后就只能拍到自绘底板了。
            bg = (shot.color if shot else None) or theme.BG_BASE
            # ⚠️ 顺序是正确性的一部分，与主面板一致：先把底板/快照画上去并同步
            # 落地，**再**撤系统那层 Acrylic。反过来就是"系统不画 + Qt 还没画"
            # 的一帧 —— 对话框会露出窗口底色（黑）。
            self._set_freeze(shot)
            self._apply_dialog_qss("opaque", bg)
            self.repaint()
            try:
                suspend(hwnd)
            except Exception:
                pass
        self._glass_settle.start()

    def _restore_glass(self) -> None:
        """窗口停止移动后恢复硬模糊，并撤掉定格快照。"""
        if not self._glass_on or self._glass_qss_mode == "glass":
            return      # 未启用原生模糊，或本来就在玻璃态（重复恢复）→ 无事可做
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            # 拖动中途只是手停下来（鼠标仍按住）：不能恢复模糊，否则 Win10
            # 迟滞回归，要等下一次 moveEvent 才重新撤掉+重拍快照（还带一次闪烁）。
            # 延后到松手后的 settle 再恢复。
            self._glass_settle.start()
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if resume(hwnd):
            # 先把系统背景装回来，再撤快照/底板转透明：此刻顶多一帧"双背景"，
            # 远轻于"无背景"。
            self._set_freeze(None)
            self._apply_dialog_qss("glass")
            self.repaint()
        # 恢复失败 → 保持当前底板与快照：宁可少一层模糊，也不能让底板消失。
        # 把几何基线推到当前值：Windows 的拖动循环退出后还会补投一批 moveEvent，
        # 它们携带的已经是最终几何，跟基线一比就成了"又移动了"——不更新的话刚恢复
        # 就被再次撤掉，窗口会一直停在无模糊的纯色底板上。
        self._glass_geo = self.geometry()


def suspend(hwnd: int) -> None:
    """移动/拖动开始：临时关闭原生模糊。

    关掉的是 Win10 上那个会导致拖动迟滞的 ACCENT_ENABLE_ACRYLICBLURBEHIND
    （见本文件顶部的 OS bug 说明）。**撤背景之前，调用方必须先让 Qt 把不透明
    底板画上去并同步落地**，否则会出现"系统不画、Qt 也没画"的透明帧。
    """
    if not _IS_WINDOWS or not hwnd:
        return
    if _dwm_set(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_NONE):
        return
    _disable_win10_acrylic(hwnd)


def resume(hwnd: int) -> bool:
    """移动/拖动结束：恢复原生模糊。返回 True 表示已恢复。"""
    return apply_glass(hwnd)
