# -*- coding: utf-8 -*-
"""悬浮任务栏主窗：玻璃卡片式设计 / 置顶 / 拖动 / 托盘 / 全局热键 / 自动验证。

视觉方案（暗色玻璃 · 克制风）：
- 原生 Acrylic/Mica 模糊生效时，QSS 半透明底板透出真实桌面；
- 不支持时玻璃后端返回 False，自动降级为深色面板（theme 已内置统一观感）；
- 每个任务是一张 46px 圆角玻璃卡片，右侧为状态胶囊 + 完成按钮。
"""
from __future__ import annotations

import os
import threading
from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..core import autostart, hotkeys
from ..core.db import TaskDB
from ..core.models import Task
from ..core.qrdecode import decode_qr
from ..core.scheduler import next_reset_text, period_key
from ..core.verifier import verify_task
from . import fullscreen, glass, occlusion, theme, window_state
from .icons import IconButton
from .menu import GlassMenu
from .occlude_hint import OccludeHint
from .qr_result import QrResultDialog, is_openable_url, open_url_in_browser
from .region_picker import pick_region_hiding_app
from .settings_dialog import SettingsDialog
from .stats_panel import StatsPanel
from .task_dialog import TaskDialog
from .version import APP_VERSION

# 底板 alpha 的"尚未设置"哨兵：与 None（= 透明，交给系统画）和任何数值都不同，
# 用于让 _apply_surface_style 具备幂等性。
_ALPHA_UNSET = object()

# 状态胶囊：(objectName, 文本)
_PILL_TODO = ("statusPillTodo", "待完成")
_PILL_DONE = ("statusPillDone", "已完成")
_PILL_BUSY = ("statusPillBusy", "验证中")
_PILL_OFF = ("statusPillOff", "已禁用")

# 判定"这是拖动而非点击"的最小位移（曼哈顿距离，px）。
# 小于它仍按点击处理，保证按钮可点；因此不能设得过小，否则轻微抖动会吞掉点击。
_DRAG_THRESHOLD_PX = 4


def _app_icon() -> QIcon:
    """应用图标：优先读取项目根目录 ico，缺失时回退到绘制的对勾图标。"""
    if config.APP_ICON.exists():
        icon = QIcon(str(config.APP_ICON))
        if not icon.isNull():
            return icon
    return _tray_icon()


def _tray_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor(theme.ACCENT))
    p = QPainter(pm)
    p.setPen(QColor(theme.ACCENT_TEXT))
    f = QFont()
    f.setPixelSize(40)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "✓")
    p.end()
    return QIcon(pm)


class _GlassSurface(QWidget):
    """面板的内层玻璃底板。

    拖动期间在自己身上多画一层**定格快照**（`glass.capture_window_surface`
    的产物），用来冒充被临时撤掉的原生模糊 —— 只撤不补的话移动时面板只剩一块
    纯色，用户看到的就是"毛玻璃效果直接没有了，也只有颜色"。

    画在控件自己的 `paintEvent` 里而不是走样式表：QSS 没法引用内存里的 pixmap。
    快照按圆角裁切 —— 它是连屏幕像素一起抓的，四角那块其实是桌面，不裁的话
    拖动时面板会短暂变成直角。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._freeze: QPixmap | None = None

    def set_freeze(self, shot) -> None:
        """设置 / 清除定格快照（传 None 清除）。"""
        pixmap = glass.shot_to_pixmap(shot) if shot is not None else None
        if pixmap is not None:
            pixmap.setDevicePixelRatio(self.devicePixelRatioF() or 1.0)
        self._freeze = pixmap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._freeze is None:
            return
        painter = QPainter(self)
        path = QPainterPath()
        radius = float(theme.R_PANEL)
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, self._freeze)
        painter.end()


class Taskbar(QWidget):
    _verify_done = Signal(int, bool, str, float)  # task_id, ok, message, score
    _toggle_requested = Signal()
    _qr_requested = Signal()

    def __init__(self, db: TaskDB | None = None) -> None:
        super().__init__()
        config.ensure_dirs()
        self.db = db or TaskDB(config.DB_PATH)
        self._drag_pos: QPoint | None = None
        self._pending_press: QPoint | None = None   # 已按下但尚未确认拖动
        self._rows: dict[int, dict] = {}
        self._glass_on = False   # 原生模糊是否生效
        # 模糊是否被临时关掉（拖动期间）。必须与 _glass_on 同处初始化：
        # 它决定底板画透明还是画深色，任何一次提前读取都不能拿到未定义状态。
        self._glass_suspended = False
        self._glass_hwnd = 0     # 已成功应用过模糊的窗口句柄（用于幂等跳过）
        self._surface_alpha = _ALPHA_UNSET   # 底板当前实际生效的 alpha，避免重复 re-polish

        # 二维码识别状态。与 _glass_* 一样必须在这里初始化：热键可能在窗口
        # 还没建完时就按下来（pynput 是另一个线程），提前读取不能拿到未定义状态。
        self._hotkeys = hotkeys.HotkeyManager()
        self._qr_busy = False            # 正在框选识别中（防重入）
        self._qr_dialog: QrResultDialog | None = None

        # 全屏免打扰（让位）状态。与 _glass_* 一样必须在这里初始化：
        # 任何一次提前读取都不能拿到未定义状态。
        self._quiet = False                  # 当前是否处于"让位"态
        self._quiet_restore_visible = True   # 让位前是否可见（用户手动隐藏过就别擅自显示）
        self._quiet_watcher = fullscreen.FullscreenWatcher()

        # 遮挡探测（面板位置上已有别人的窗口 → 不弹出）。与 _glass_* / _quiet_* 同理：
        # 这几项必须与其它状态**同处初始化**——`stop_dock_timers` 会碰到它们，任何
        # 一次提前读取都不能拿到未定义状态。
        self._occlude_probe = occlusion.BlockerProbe()
        self._occlude_hint: OccludeHint | None = None   # 懒建：不用这功能就不开窗口
        self._occlude_blocked = False      # 最近一次探测结论
        self._occlude_thing = ""           # 挡路者的名字（提示条用）
        self._occlude_notified = False     # 本次靠近是否已弹过提示
        self._force_timer = QTimer(self)   # 被挡时"停够这么久就强行弹出"
        self._force_timer.setSingleShot(True)
        self._force_timer.setInterval(config.OCCLUDE_HOVER_MS)
        self._force_timer.timeout.connect(self._on_force_expand)

        self.setWindowTitle(f"{config.APP_NAME} {APP_VERSION}")
        self.setWindowIcon(_app_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(theme.PANEL_WIDTH)
        self._build_ui()
        self._build_tray()
        self._setup_hotkeys()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_statuses)
        self._refresh_timer.start(config.REFRESH_INTERVAL_MS)

        self._verify_done.connect(self._on_verify_done)
        self._toggle_requested.connect(self.toggle_visible)
        # pynput 的回调跑在**监听线程**上，只能靠 Signal 把活儿排队到 GUI 线程
        self._qr_requested.connect(self.scan_qr)
        self.refresh_all()

        # 贴边自动隐藏：状态与定时器
        self._dock_edge: str | None = None      # None | "left" | "right"
        self._dock_expanded = False             # 停靠后是否处于展开态
        self._anim: QPropertyAnimation | None = None
        self._manual_hold = False               # 托盘/热键手动收回后，光标未离开前不自动弹出
        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.setInterval(config.DOCK_EXPAND_DELAY_MS)
        self._expand_timer.timeout.connect(self._on_expand_timeout)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(config.DOCK_COLLAPSE_DELAY_MS)
        self._collapse_timer.timeout.connect(self._on_collapse_timeout)
        # 光标轮询：停靠态的展开/收回全靠它判定，只在停靠期间运行
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(config.DOCK_POLL_MS)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._restore_dock()
        self._restore_position()

        # 全屏免打扰：低频轮询前景窗口，只在"全屏出现 / 消失"时动窗口。
        # 它必须在让位期间**继续跑**——否则全屏退出后没人把面板叫回来。
        self._quiet_timer = QTimer(self)
        self._quiet_timer.setInterval(config.FULLSCREEN_POLL_MS)
        self._quiet_timer.timeout.connect(self._poll_fullscreen)
        self._sync_quiet_timer()

    # ---------- 原生玻璃 ----------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_glass_once()

    def _apply_glass_once(self) -> None:
        """窗口拿到 winId 后应用原生模糊；结果决定底板透明度。

        幂等：同一个 hwnd 已经成功应用过、且当前没有处于挂起态，就直接返回。
        每次 `DwmSetWindowAttribute` 都会触发一次窗口背景重建，而在 showEvent
        里反复做这件事（托盘隐藏后又显示、展开动画里的 show()）就是可见的闪烁。
        """
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if hwnd == self._glass_hwnd and not self._glass_suspended:
            return
        self._glass_hwnd = hwnd
        self._glass_on = glass.apply_glass(hwnd)
        # apply_glass 已经把系统背景装回来了，挂起状态随之失效——否则标记会与
        # 真实状态脱节（隐藏时拖动没结束就再显示，就会出现"以为还挂着"）。
        self._glass_suspended = False
        self._surface.set_freeze(None)   # 定格快照同理：系统背景回来了就不该再画
        # 原生模糊生效 → 底板透明交给系统；否则 Qt 自己画半透明深色底板
        self._apply_surface_style(None if self._glass_on else theme.BG_BASE_ALPHA)

    def _apply_surface_style(self, bg_alpha: float | None) -> None:
        """设置玻璃底板样式（唯一入口，避免两处状态各写一份而漂移）。

        `bg_alpha=None` → 原生模糊已经在窗口后面画了背景，底板必须透明，
        否则会把它盖住；给数值则画一层半透明深色底。

        ⚠️ 这两个状态是**互斥且必须二选一**的：一旦系统背景被撤掉
        （`glass.suspend()`，拖动期间），底板若还保持透明，
        窗口背后就什么都没有了 —— 只剩按钮和文字悬在桌面上。

        幂等：状态没变就不碰样式表。`setStyleSheet` 会重新 polish 整个子树
        （滚动区 + 所有任务卡片），重复调用既浪费又会闪。
        """
        if bg_alpha == self._surface_alpha:
            return
        self._surface_alpha = bg_alpha
        if bg_alpha is None:
            self._surface.setStyleSheet(
                f"QWidget#glassSurface {{ background: transparent;"
                f" border: none; border-radius: {theme.R_PANEL}px; }}"
            )
        else:
            self._surface.setStyleSheet(
                f"QWidget#glassSurface {{"
                f" background: {theme.bg_rgba(bg_alpha)};"
                f" border: 1px solid {theme.STROKE};"
                f" border-radius: {theme.R_PANEL}px; }}"
            )

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.setObjectName("glassRoot")
        self.setStyleSheet(theme.app_qss())
        # 字体走完整 hinting + 抗锯齿：暗底小字清晰度的关键，比换字号更有效
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(theme.fs(theme.FS_BASE))
        f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFont(f)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # 玻璃面板本体：所有内容挂在这里
        self._surface = _GlassSurface()
        self._surface.setObjectName("glassSurface")
        self._surface.installEventFilter(self)   # 按住面板空白处即可拖动
        outer.addWidget(self._surface)

        # 降级/原生都保留一层柔和投影，让面板从桌面上"浮"起来。
        # 注意：Qt 中带 QGraphicsEffect 的控件会同时被渲染进一个离屏 pixmap，
        # 拖动时开销较大。这里只加在 _surface 上、且子控件已置为鼠标穿透，
        # 因此不影响拖动采样。
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150))
        self._surface.setGraphicsEffect(shadow)

        root = QVBoxLayout(self._surface)
        root.setContentsMargins(theme.SP_3, theme.SP_3, theme.SP_3, theme.SP_3)
        root.setSpacing(theme.SP_2)

        # ---- 标题栏 ----
        header = QHBoxLayout()
        header.setSpacing(theme.SP_2)
        title = QLabel(config.APP_NAME)
        title.setObjectName("appTitle")
        # 版本号紧跟程序名右侧：字号更小、颜色更弱（样式在 theme 的
        # `QLabel#appVersion`）。文本来源是 version.py 这个唯一来源，别写死字面量。
        self.version_label = QLabel(APP_VERSION)
        self.version_label.setObjectName("appVersion")
        self.date_label = QLabel()
        self.date_label.setObjectName("appDate")

        # 图标一律用自绘矢量图（见 ui/icons.py）。**不要**改回符号字符：
        # 以前这里的 `▦ ▩ ＋` 在中文字体里没有字形，真机上是一排豆腐块。
        self.stats_btn = IconButton("stats")
        self.stats_btn.setToolTip("打卡统计")
        self.stats_btn.clicked.connect(self._show_stats)

        # 二维码识别：热键才是主入口，但热键是"看不见的"，给一个按钮让它可被发现
        self.qr_btn = IconButton("qr")
        self.qr_btn.clicked.connect(self.scan_qr)

        add_btn = IconButton("add")
        add_btn.setToolTip("添加任务")
        add_btn.clicked.connect(self._add_task)

        # 程序名 + 版本号当成一个整体：用一个更紧的子布局包起来，否则跟着
        # header 的 SP_2(8px) 间距走，版本号看着像另一个独立条目。
        # ⚠️ **不要给版本号加 `Qt.AlignBottom`**（实测：它会沉到标题基线下方
        # 6px，看着像掉了一格）。两行文字都在这 28px 的行高里垂直居中，墨迹
        # 中心才会重合 —— 详见 tests/test_ui.py 里那条墨迹中心守卫。
        brand = QHBoxLayout()
        brand.setSpacing(theme.SP_1)
        brand.addWidget(title)
        brand.addWidget(self.version_label)
        header.addLayout(brand)
        header.addWidget(self.date_label)
        header.addStretch(1)
        header.addWidget(self.qr_btn)
        header.addWidget(self.stats_btn)
        header.addWidget(add_btn)
        root.addLayout(header)

        # 标题与日期也可拖动（否则只有面板空白处能拖，手感很差）
        self._make_drag_transparent(title)
        self._make_drag_transparent(self.version_label)
        self._make_drag_transparent(self.date_label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ---- 任务列表 ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        self.task_list_widget = QWidget()
        self.task_list_widget.setObjectName("listHost")
        self.task_list_widget.installEventFilter(self)
        self.task_list_layout = QVBoxLayout(self.task_list_widget)
        self.task_list_layout.setContentsMargins(0, 0, theme.SP_1, 0)
        self.task_list_layout.setSpacing(theme.SP_2)
        self.task_list_layout.addStretch(1)
        self.scroll.setWidget(self.task_list_widget)
        root.addWidget(self.scroll, 1)

        self.setFixedHeight(min(600, 132 + 8 * (theme.ROW_HEIGHT + theme.SP_2)))

    def _build_task_card(self, task: Task) -> QWidget:
        """构造一张任务卡片：状态点 + 名称/周期 + 状态胶囊 + 完成/菜单按钮。"""
        card = QWidget()
        card.setObjectName("taskCard")
        card.setFixedHeight(theme.ROW_HEIGHT)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(theme.SP_2 + 2, 0, theme.SP_2, 0)
        lay.setSpacing(theme.SP_2)

        dot = QLabel("●")
        dot.setFixedWidth(10)
        dot.setStyleSheet(f"color: {theme.TODO}; font-size: 10px;")

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        name = QLabel(task.name)
        name.setObjectName("taskName")
        name.setToolTip(task.name)
        meta = QLabel(task.type_name)
        meta.setObjectName("taskMeta")
        text_col.addWidget(name)
        text_col.addWidget(meta)

        status = QLabel()
        status.setObjectName(_PILL_TODO[0])
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        done_btn = QPushButton("完成")
        done_btn.setObjectName("primaryBtn")
        done_btn.setFixedHeight(28)
        done_btn.setMinimumWidth(56)
        done_btn.clicked.connect(lambda _=False, t=task: self._on_complete_clicked(t))

        menu_btn = IconButton("more", box=26, icon=15)
        menu_btn.setToolTip("更多")
        menu_btn.clicked.connect(lambda _=False, t=task, w=card: self._row_menu(t, w))

        lay.addWidget(dot)
        lay.addLayout(text_col, 1)
        lay.addWidget(status)
        lay.addWidget(done_btn)
        lay.addWidget(menu_btn)

        # ⚠️ 卡片是**容器**，绝不能对它设 WA_TransparentForMouseEvents。
        # 该属性的语义是"连同子树一起"退出鼠标命中链：一旦设在卡片上，
        # 卡片里的「完成 / ⋯」按钮会彻底点不到——即便按钮自己没设该属性。
        # 实测 QApplication.widgetAt(按钮中心) 返回的是 listHost 而不是按钮，
        # 而单元测试用 qtbot.mouseClick(btn) 是直接投递给按钮、绕过命中测试，
        # 所以这种坑测试测不出来（见 test_row_buttons_are_hit_testable）。
        # 拖动只靠事件过滤器就够了：过滤器在"接收者投递前"运行，
        # 命中到卡片或按钮时都能拿到 press/move/release。
        card.installEventFilter(self)
        # 下面这些是叶子控件（无子节点），穿透掉它们之后命中落回卡片，
        # 按在文字上照样能拖。
        for w in (dot, name, meta, status):
            self._make_drag_transparent(w)
        # 按钮要保持可点，仅挂过滤器用于拖动手势判定
        for w in (done_btn, menu_btn):
            w.installEventFilter(self)
        return card

    def _make_drag_transparent(self, w: QWidget) -> None:
        """让**叶子**控件不参与鼠标命中，点击穿透到父级（用于拖动）。

        ⚠️ 只能用在没有子控件的控件上。`WA_TransparentForMouseEvents` 会让
        该控件**及其所有后代**一起退出命中链——用在容器上会把里面的按钮
        一并废掉（卡片就是这么踩的坑）。
        """
        w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        w.installEventFilter(self)

    def _set_pill(self, label: QLabel, pill: tuple[str, str]) -> None:
        """切换状态胶囊：objectName 变化需 unpolish/polish 才会重绘样式。"""
        label.setObjectName(pill[0])
        label.setText(pill[1])
        label.style().unpolish(label)
        label.style().polish(label)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self)
        # 菜单是独立顶层窗口，不在主面板的样式树里 —— 一律用 GlassMenu，
        # 否则就是系统默认的浅色（见 ui/menu.py 顶部说明）。
        menu = GlassMenu()
        self.act_show = QAction("显示 / 隐藏", menu)
        self.act_show.triggered.connect(self.toggle_visible)
        menu.addAction(self.act_show)
        # 二维码相关的动作与开关收进子菜单。放进子菜单而不是平铺在根上是因为根菜单
        # 已经排了"打卡统计 / 开机自启 / 贴边自动隐藏 / 全屏让位 / 遮挡不弹出"这些
        # 全局开关，扫码那两个行为开关混在里面看不出归属。
        # ⚠️ 想遍历菜单找子菜单时**不要用 `action.menu()`**：PySide6 6.11.1 实测，
        # 该调用会让返回的包装带走所有权、**把子菜单的 C++ 对象当场删掉**（不用等
        # GC）。本文件从不调它，认准 `self.qr_menu` 这个引用即可。
        # 子菜单同样是独立弹窗（自己的 HWND），所以也要 GlassMenu —— 用
        # `addMenu("二维码")` 建出来的是普通 QMenu，弹出来会是不透明的另一套观感。
        self.qr_menu = GlassMenu(menu)
        self.qr_menu.setTitle("二维码")
        menu.addMenu(self.qr_menu)
        self.act_qr = QAction("识别二维码", self.qr_menu)
        self.act_qr.triggered.connect(self.scan_qr)
        self.qr_menu.addAction(self.act_qr)
        self.qr_menu.addSeparator()
        self.act_qr_autocopy = QAction("识别后自动复制", self.qr_menu)
        self.act_qr_autocopy.setCheckable(True)
        self.act_qr_autocopy.setToolTip("关掉后结果窗只显示内容，要粘贴得自己点「复制」")
        # setChecked 排在 connect 之前是全局惯例（见下面 act_quiet 的说明）：
        # 这里回调只写库、暂无构造期风险，但统一顺序省得以后加逻辑时踩坑。
        self.act_qr_autocopy.setChecked(self._qr_autocopy_enabled())
        self.act_qr_autocopy.toggled.connect(self._toggle_qr_autocopy)
        self.act_qr_open_direct = QAction("识别到链接直接打开浏览器", self.qr_menu)
        self.act_qr_open_direct.setCheckable(True)
        self.act_qr_open_direct.setToolTip(
            "扫到链接直接交给系统浏览器打开，不再弹结果窗（内容仍会复制到剪贴板）")
        self.act_qr_open_direct.setChecked(self._qr_open_direct_enabled())
        self.act_qr_open_direct.toggled.connect(self._toggle_qr_open_direct)
        self.act_qr_open_link = QAction("识别到网址显示「打开链接」", self.qr_menu)
        self.act_qr_open_link.setCheckable(True)
        self.act_qr_open_link.setToolTip(
            "关掉后无论识别到什么内容都不给打开按钮（直开开启时本项无意义，会置灰）")
        self.act_qr_open_link.setChecked(self._qr_open_link_enabled())
        self.act_qr_open_link.toggled.connect(self._toggle_qr_open_link)
        self.qr_menu.addAction(self.act_qr_autocopy)
        self.qr_menu.addAction(self.act_qr_open_direct)
        self.qr_menu.addAction(self.act_qr_open_link)
        self._sync_qr_menu_state()
        act_stats = QAction("打卡统计", menu)
        act_stats.triggered.connect(self._show_stats)
        self.act_autostart = QAction("开机自启", menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(autostart.is_enabled())
        self.act_autostart.toggled.connect(self._toggle_autostart)
        self.act_autohide = QAction("贴边自动隐藏", menu)
        self.act_autohide.setCheckable(True)
        self.act_autohide.setChecked(self.db.get_setting("autohide") == "1")
        self.act_autohide.toggled.connect(self._toggle_autohide)
        self.act_quiet = QAction("全屏应用时自动让位", menu)
        self.act_quiet.setCheckable(True)
        # ⚠️ 顺序有意义：setChecked 会发 toggled（勾选态确实变了），必须排在
        # connect **之前**，否则构造期就会回调进来，而 _quiet_timer 那时还没建。
        self.act_quiet.setChecked(self._fullscreen_quiet_enabled())
        self.act_quiet.setToolTip("全屏游戏 / 视频 / 演示期间自动隐藏面板，并暂停靠近弹出")
        self.act_quiet.toggled.connect(self._toggle_fullscreen_quiet)
        self.act_occlude = QAction("被窗口挡住时不弹出", menu)
        self.act_occlude.setCheckable(True)
        # 同样：setChecked 必须在 connect 之前（构造期不能回调进来）
        self.act_occlude.setChecked(self._occlude_enabled())
        self.act_occlude.setToolTip(
            "面板位置上已经有别的窗口时不让它自动弹出；停在那里 1 秒仍可强行唤出")
        self.act_occlude.toggled.connect(self._toggle_occlude)
        act_settings = QAction("快捷键设置…", menu)
        act_settings.triggered.connect(self._show_settings)
        act_reset_pos = QAction("清除保存的窗口位置", menu)
        act_reset_pos.setToolTip("下次启动时窗口回到默认位置")
        act_reset_pos.triggered.connect(self._reset_positions)
        act_exit = QAction("退出", menu)
        act_exit.triggered.connect(QApplication.instance().quit)
        for a in (act_stats, self.act_autostart, self.act_autohide,
                  self.act_quiet, self.act_occlude):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_settings)
        menu.addAction(act_reset_pos)
        menu.addSeparator()
        menu.addAction(act_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_visible()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    # ---------- 全局热键 ----------
    def _combo_for(self, setting_key: str, default: str) -> str:
        """当前生效的组合键：库里的值非法时回退默认（见 hotkeys.load_combo）。"""
        return hotkeys.load_combo(self.db, setting_key, default)

    def _setup_hotkeys(self) -> None:
        """注册两个全局热键（呼出面板 / 识别二维码）。

        组合键存在 settings 表里，改键后重新走这个方法就是热重载。

        两个回调只做一件事：发 Signal。pynput 在**自己的线程**里回调，直接在
        里面碰任何控件都是跨线程操作 GUI。Signal 的跨线程投递天然把它排回 GUI 线程。

        注册失败只提示不抛错：热键是锦上添花，面板、托盘入口都还在，
        不该因为按不下某个组合就让整个程序起不来。
        """
        ok = self._hotkeys.apply([
            ("呼出 / 隐藏面板",
             self._combo_for(config.SETTING_HOTKEY_TOGGLE, config.HOTKEY_TOGGLE),
             self._toggle_requested.emit),
            ("识别二维码",
             self._combo_for(config.SETTING_HOTKEY_QR, config.HOTKEY_QR),
             self._qr_requested.emit),
        ])
        if not ok:
            self._notify(f"全局热键未生效：{self._hotkeys.last_error}", warning=True)
        self._sync_hotkey_labels()

    def _sync_hotkey_labels(self) -> None:
        """把当前组合键写进按钮提示与托盘菜单。

        改键之后必须跟着重刷：菜单里还印着旧组合，用户就会以为改键没生效。
        QAction 文本里 `\\t` 之后的部分由 Qt 渲染成右侧的快捷键提示列。
        """
        qr = hotkeys.label(self._combo_for(config.SETTING_HOTKEY_QR, config.HOTKEY_QR))
        toggle = hotkeys.label(
            self._combo_for(config.SETTING_HOTKEY_TOGGLE, config.HOTKEY_TOGGLE))
        self.qr_btn.setToolTip(f"识别二维码（{qr}）")
        self.act_qr.setText(f"识别二维码\t{qr}")
        self.act_show.setText(f"显示 / 隐藏\t{toggle}")

    def _show_settings(self) -> None:
        """快捷键设置。

        打开期间**先停掉全部热键**：录制时按下的组合若还绑着动作，会当场把那个
        动作触发出来（录 Ctrl+Alt+Q 就直接弹出二维码框选，把设置窗压下去，
        用户会以为界面坏了）。关掉对话框后无论是否保存都重新注册 —— 热键是被
        我们停掉的，不重新注册它们就"正常"地消失了。
        """
        if self._refuse_when_picking():
            return
        self._hotkeys.stop()
        try:
            dlg = SettingsDialog(self.db, self)
            # 用完即毁：exec 后没有任何状态要消费，不销毁的话每个隐藏对话框
            # （连同玻璃层、settle 定时器）都会作为 Taskbar 子对象累积到退出
            dlg.finished.connect(dlg.deleteLater)
            dlg.exec()
        finally:
            self._setup_hotkeys()

    # ---------- 二维码识别 ----------
    def _notify(self, message: str, *, warning: bool = False) -> None:
        """托盘气泡。图标按语义取，省得每个调用点各写一遍枚举。"""
        self.tray.showMessage(
            config.APP_NAME, message,
            QSystemTrayIcon.MessageIcon.Warning if warning
            else QSystemTrayIcon.MessageIcon.Information,
            4000 if warning else 2000,
        )

    def _refuse_when_picking(self) -> bool:
        """正在框选识别时，拒掉会抢窗口的操作（托盘在模态/遮罩期间仍可点）。"""
        if not self._qr_busy:
            return False
        self._notify("正在框选二维码，请先完成或按 Esc 取消")
        return True

    def scan_qr(self) -> None:
        """框选屏幕区域 → 识别二维码 → 自动复制内容并弹出结果窗。

        热键、托盘、面板按钮三个入口都走这里。

        框选遮罩是铺满整块屏幕的窗口，**全屏检测会把它误判成"别的应用全屏了"**
        而让面板让位。所以整个流程期间把检测停掉，结束后再按设置恢复。
        """
        if self._refuse_when_picking():
            return
        # 模态对话框开着时不能框选：_hide 会终止 exec() 的模态循环，那个对话框
        # 会莫名自己关掉（托盘菜单不受模态限制，用户照样点得到）
        if QApplication.activeModalWidget() is not None:
            self._notify("请先关闭当前对话框，再识别二维码")
            return
        self._qr_busy = True
        self._quiet_timer.stop()
        try:
            picked = pick_region_hiding_app(
                hint="拖拽框选二维码 · 松开即识别 · Esc 取消")
            if picked is None:
                return
            _rect, crop = picked
            result = decode_qr(crop)
        finally:
            self._qr_busy = False
            self._sync_quiet_timer()
        if result.error:
            self._notify(result.error, warning=True)
            return
        if not result.hits:
            self._notify("框选区域里没识别到二维码，可以框得再贴合一点", warning=True)
            return
        if self._open_if_link(result.hits):
            return
        self._show_qr_result(result.hits)

    def _open_if_link(self, hits) -> bool:
        """结果里有链接就直接交给浏览器打开，返回"是否已经处理完"（True = 别再弹窗）。

        多码时取**第一个**链接（用户确认过：不因为多码就退回弹窗）。

        内容仍按「识别后自动复制」开关写剪贴板——直开只省掉"看一眼、再点按钮"
        这一步，不该顺带把"扫到的东西已经复制好了"这个既有行为弄没。

        失败必须出声：`openUrl` 交不出去（没装默认浏览器之类）而界面沉默，用户
        看到的就是"扫完什么都没发生"。
        """
        if not self._qr_open_direct_enabled():
            return False
        target = next((h.text for h in hits if is_openable_url(h.text)), None)
        if target is None:
            return False
        if self._qr_autocopy_enabled():
            QApplication.clipboard().setText(target)
        if not open_url_in_browser(target):
            self._notify("识别到链接，但系统没能打开它，检查一下默认浏览器",
                         warning=True)
        return True

    def _forget_qr_dialog(self, dlg: QrResultDialog) -> None:
        """结果窗销毁时清掉引用。**唯一清理入口**。

        `finished → deleteLater` 之后 C++ 对象已经没了，但 Python 侧的包装还在：
        `self._qr_dialog is not None` 依旧成立，下次扫码调 `.close()` 就是
        `RuntimeError: Internal C++ object already deleted`（真机崩过一次）。
        所以这里维持的不变量是：**`_qr_dialog is not None` ⟺ 这个对象还能用**。

        ⚠️ 必须比对身份，不能无条件置 None：关旧窗与建新窗之间**不跑事件循环**，
        而 `deleteLater` 要等下一轮才执行 —— 旧窗的销毁信号到达时，`_qr_dialog`
        早已指向新窗。无条件清会把**新窗**的引用一并抹掉，于是下次扫码
        `_show_qr_result` 以为没有旧窗要关，结果窗从此在屏幕上堆积。
        （后果不是"新窗消失"：实测有无 parent 的顶层窗口在 Python 引用消失后
        都仍存活可见 —— Qt 自己持有顶层 widget —— 但面板再也 close 不到它。）
        这个竞态有专门用例守着，别把判断删了。

        ⚠️ 只接 `destroyed`，不接更早的 `finished`：本方法维持的不变量是
        "`_qr_dialog is not None` ⟺ 这个对象还能用"，而**只有销毁**（不是关闭）
        会破坏它 —— 已关闭但未销毁的对象照样能安全 `close()`。挂在 finished 上
        是超前清理，收益为零。
        （注入验证：两个钩子各自单独都够用，实测后只留这一个。）
        """
        if self._qr_dialog is dlg:
            self._qr_dialog = None

    def _show_qr_result(self, hits) -> None:
        """弹出结果窗。

        **非模态**（`show()` 而非 `exec()`）：扫码流程要隐藏本程序所有窗口才能
        拍到干净画面，而 hide 会终止模态循环 —— 结果窗正好活在这个流程之后，
        一旦用户再扫一次，模态的结果窗会直接被隐藏动作掐死。

        两个行为开关在**每次弹窗时现读**（而不是缓存）：托盘里一改就该立刻生效，
        缓存住就得再拉一条"改设置→刷新缓存"的线，多一处会漂移的状态。
        """
        if self._qr_dialog is not None:
            self._qr_dialog.close()      # 上一次的结果不留在屏幕上
        dlg = QrResultDialog(hits, self,
                             autocopy=self._qr_autocopy_enabled(),
                             open_link=self._qr_open_link_enabled())
        self._qr_dialog = dlg
        # 留引用是为了下次扫码能 close 掉它 —— 不是为了防 GC。实测有无 parent 的
        # 顶层窗口在 Python 引用消失后都仍存活可见（Qt 自己持有顶层 widget），
        # 但目录里没它就关不掉了。清理见 _forget_qr_dialog。
        dlg.finished.connect(dlg.deleteLater)
        dlg.destroyed.connect(lambda *_: self._forget_qr_dialog(dlg))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _qr_autocopy_enabled(self) -> bool:
        """默认开启：只有显式存过 "0" 才算关（老库没有这个键）。"""
        return self.db.get_setting(config.SETTING_QR_AUTOCOPY, "1") != "0"

    def _toggle_qr_autocopy(self, checked: bool) -> None:
        self.db.set_setting(config.SETTING_QR_AUTOCOPY, "1" if checked else "0")

    def _qr_open_direct_enabled(self) -> bool:
        """默认开启：只有显式存过 "0" 才算关。"""
        return self.db.get_setting(config.SETTING_QR_OPEN_DIRECT, "1") != "0"

    def _toggle_qr_open_direct(self, checked: bool) -> None:
        self.db.set_setting(config.SETTING_QR_OPEN_DIRECT, "1" if checked else "0")
        self._sync_qr_menu_state()

    def _sync_qr_menu_state(self) -> None:
        """直开开启时，把「识别到网址显示「打开链接」」置灰。**唯一同步入口**。

        直开一旦生效，扫到链接根本不弹结果窗（多码也直开），那个按钮没有任何
        场景会出现——留一个永远无效的开关只会让人困惑。置灰而不删：老库里可能
        已经存过它的值，删掉这项用户就再也改不回来了。

        调用点只有两处：托盘构建完之后、直开开关切换时。别再加第三处。
        """
        self.act_qr_open_link.setEnabled(not self._qr_open_direct_enabled())

    def _qr_open_link_enabled(self) -> bool:
        """默认开启：只有显式存过 "0" 才算关。"""
        return self.db.get_setting(config.SETTING_QR_OPEN_LINK, "1") != "0"

    def _toggle_qr_open_link(self, checked: bool) -> None:
        self.db.set_setting(config.SETTING_QR_OPEN_LINK, "1" if checked else "0")

    # ---------- 刷新 ----------
    def refresh_all(self) -> None:
        self._rebuild_rows()
        self.refresh_statuses()

    def _refresh_date_label(self) -> None:
        now = datetime.now()
        weekday_cn = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[now.weekday()]
        self.date_label.setText(f"{now.strftime('%m-%d')} {weekday_cn}")

    def _rebuild_rows(self) -> None:
        # 必须立刻解绑父级：deleteLater() 只是排队到下一轮事件循环，
        # 期间旧控件仍会参与本帧渲染（表现为列表下方残留"暂无任务"文字）。
        while self.task_list_layout.count() > 1:
            item = self.task_list_layout.takeAt(0)
            if w := item.widget():
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()
        tasks = self.db.list_tasks()
        if not tasks:
            empty = QLabel("暂无任务\n点右上角加号添加一个")
            empty.setObjectName("emptyHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.task_list_layout.insertWidget(0, empty)
            return
        for i, task in enumerate(tasks):
            card = self._build_task_card(task)
            lay = card.layout()
            self.task_list_layout.insertWidget(i, card)
            self._rows[task.id] = {
                "card": card,
                "dot": lay.itemAt(0).widget(),
                "name": lay.itemAt(1).itemAt(0).widget(),
                "meta": lay.itemAt(1).itemAt(1).widget(),
                "status": lay.itemAt(2).widget(),
                "btn": lay.itemAt(3).widget(),
                "task": task,
            }

    def refresh_statuses(self) -> None:
        # 日期也要跟着 30s 心跳刷新：跨午夜后标题不能停在昨天
        self._refresh_date_label()
        now = datetime.now()
        for tid, row in self._rows.items():
            task = row["task"]
            done = self.db.is_completed(tid, period_key(task, now))
            if not task.enabled:
                self._set_pill(row["status"], _PILL_OFF)
                row["dot"].setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
                row["name"].setObjectName("taskName")
                row["btn"].setVisible(False)
            elif done:
                self._set_pill(row["status"], _PILL_DONE)
                row["dot"].setStyleSheet(f"color: {theme.DONE}; font-size: 10px;")
                row["name"].setObjectName("taskNameDone")
                row["btn"].setVisible(False)
            else:
                self._set_pill(row["status"], _PILL_TODO)
                row["dot"].setStyleSheet(f"color: {theme.TODO}; font-size: 10px;")
                row["name"].setObjectName("taskName")
                row["btn"].setVisible(True)
                row["btn"].setText("完成")
            row["name"].style().unpolish(row["name"])
            row["name"].style().polish(row["name"])
            # 悬停看完整任务名 + 该任务的周期重置时间（每 30s 随刷新一起更新）
            row["name"].setToolTip(f"{task.name}\n{next_reset_text(task, now)}")

    # ---------- 动作 ----------
    def _open_task_dialog(self, dlg: TaskDialog) -> None:
        """非模态打开任务编辑框。

        不能用 exec()：截图前需要隐藏对话框，而模态对话框在 exec 期间
        被 hide 会直接终止模态循环，导致截图完成后编辑框"自动退出"。
        """
        def _on_done(result: int) -> None:
            if result == QDialog.DialogCode.Accepted and dlg.task:
                if dlg.task.id is None:
                    self.db.add_task(dlg.task)          # 入库拿到 id
                    if dlg.finalize_template(dlg.task.id):
                        self.db.update_task(dlg.task)   # 回写转正后的模板路径
                else:
                    self.db.update_task(dlg.task)
                self.refresh_all()
            dlg.deleteLater()

        dlg.finished.connect(_on_done)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _add_task(self) -> None:
        self._open_task_dialog(TaskDialog(self))

    def _row_menu(self, task: Task, anchor: QWidget) -> None:
        menu = GlassMenu(self)
        act_edit = menu.addAction("编辑")
        act_toggle = menu.addAction("禁用" if task.enabled else "启用")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().topRight()))
        if chosen == act_edit:
            self._open_task_dialog(TaskDialog(self, task))
        elif chosen == act_toggle:
            task.enabled = not task.enabled
            self.db.update_task(task)
            self.refresh_all()
        elif chosen == act_del:
            ret = QMessageBox.question(self, "删除任务", f"确定删除「{task.name}」？其完成记录将一并删除。")
            if ret == QMessageBox.StandardButton.Yes:
                self.db.delete_task(task.id)
                self.refresh_all()

    def _on_complete_clicked(self, task: Task) -> None:
        now = datetime.now()
        pk = period_key(task, now)
        if self.db.is_completed(task.id, pk):
            return
        if task.is_image_verify:
            row = self._rows.get(task.id)
            if row:
                self._set_pill(row["status"], _PILL_BUSY)
                row["btn"].setEnabled(False)
            threading.Thread(
                target=self._verify_worker, args=(task, pk), daemon=True
            ).start()
        else:
            self.db.mark_completed(task.id, pk, verify_result="manual")
            self.refresh_statuses()

    def _verify_worker(self, task: Task, period: str) -> None:
        result = verify_task(task)
        if not result.ok:
            self._verify_done.emit(task.id, False, result.message, result.score)
            return
        # 记到"点完成"时所属的周期：验证只是取证，全屏搜索可能耗时数秒，
        # 跨过午夜/周界也不能把用户在上一个周期做的事记到新周期头上
        # （否则旧周期永远差一次、新周期白白多一次）。
        self.db.mark_completed(task.id, period, verify_result="image")
        # 验证通过就算完成：mark_completed 撞上同周期重复记录（并发行）时
        # 返回 False，但那也是"任务已完成"，绝不能显示成"未命中 xx"再叫人重试
        self._verify_done.emit(task.id, True, result.message, result.score)

    def _on_verify_done(self, task_id: int, ok: bool, message: str, score: float) -> None:
        self.refresh_all()
        if ok:
            self.tray.showMessage(config.APP_NAME, f"任务已完成：{message}", QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            # 卡片直接显示得分，便于判断调阈值还是调区域
            row = self._rows.get(task_id)
            if row:
                self._set_pill(row["status"], ("statusPillFail", f"未命中 {score:.2f}"))
                row["btn"].setEnabled(True)
                row["btn"].setText("重试")
            self.tray.showMessage(config.APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning, 4000)

    def _show_stats(self) -> None:
        if self._refuse_when_picking():
            return
        dlg = StatsPanel(self.db, self)
        # 同设置窗：exec 后即毁，防止反复打开累积隐藏对话框
        dlg.finished.connect(dlg.deleteLater)
        dlg.exec()

    def _reset_positions(self) -> None:
        """托盘：清掉记住的窗口位置（面板当下不动，下次启动回默认位置）。"""
        window_state.forget_all(self.db)
        self.tray.showMessage(
            config.APP_NAME,
            "已清除保存的窗口位置，下次启动回到默认位置。",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _toggle_autostart(self, checked: bool) -> None:
        if not autostart.set_enabled(checked):
            self.act_autostart.setChecked(not checked)
            QMessageBox.warning(self, "失败", "开机自启设置失败（需要注册表写权限）")

    # ---------- 全屏免打扰（自动让位） ----------
    def _fullscreen_quiet_enabled(self) -> bool:
        """默认开启：只有显式存过 "0" 才算关（老库没有这个键）。"""
        return self.db.get_setting("fullscreen_quiet", "1") != "0"

    def _sync_quiet_timer(self) -> None:
        """按设置启停检测；关掉时若正处在让位态要立刻回位。"""
        if self._fullscreen_quiet_enabled():
            if not self._quiet_timer.isActive():
                self._quiet_timer.start()
        else:
            self._quiet_timer.stop()
            self._leave_quiet()

    def _toggle_fullscreen_quiet(self, checked: bool) -> None:
        self.db.set_setting("fullscreen_quiet", "1" if checked else "0")
        self._sync_quiet_timer()

    def _poll_fullscreen(self) -> None:
        """1Hz 采一次前景窗口；只在结论翻转时动窗口（其余时间什么都不做）。"""
        if self._drag_pos is not None:
            # 拖动中不让位：窗口一旦隐藏，鼠标还按着的那次拖动就卡在半途了
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        want = self._quiet_watcher.update(own_hwnd=hwnd, dock_edge=self._dock_edge)
        if want and not self._quiet:
            self._enter_quiet()
        elif not want and self._quiet:
            self._leave_quiet()

    def _enter_quiet(self) -> None:
        """全屏应用占满面板所在屏 → 让位：收走窗口 **并** 停掉靠近轮询。

        两件事必须一起做。全屏应用常把光标锁在屏幕边缘或中央，`_poll_cursor`
        会把"光标在边缘"判成"用户靠近"——面板于是反复弹出、又被全屏窗口盖住，
        动画白跑还占着 GUI 线程。

        ⚠️ 全程**不碰 window flags**：Qt 改 flag 会重建原生窗口，HWND 一变，
        Acrylic 状态、事件过滤器、`_glass_hwnd` 的幂等前提全部脱节。这里只 hide。
        """
        if self._quiet:
            return
        self._quiet = True
        if self._anim is not None:
            # 残留动画会在隐藏期间继续改 x，回来时位置就错乱了
            self._anim.stop()
        self.stop_dock_timers()
        self._cursor_timer.stop()
        self._quiet_restore_visible = self.isVisible()
        self._dock_expanded = False     # 回来时一律先回"收起"，不擅自展开挡住别人
        self.hide()

    def _leave_quiet(self) -> None:
        """全屏结束 → 回位。停靠态回细条，自由态回原位置（位置从未被动过）。"""
        if not self._quiet:
            return
        self._quiet = False
        if self._dock_edge is not None:
            target_x, _ = self._docked_positions()
            self.move(QPoint(target_x, self.y()))
            if self._quiet_restore_visible:
                self.show()
            self._start_cursor_poll()
            return
        if self._quiet_restore_visible:
            self.show()

    # ---------- 遮挡探测（面板位置上已有别人的窗口 → 不弹出） ----------
    # 与"全屏让位"是两套**互补**机制，不是替代：那套问"前台窗口是不是全屏"（全局
    # 代理指标，只看一个窗口、靠矩形推导），这套问"我那块地方现在有没有别人"
    # （就地测量，不必知道对方是什么程序）。探测入口只在真的要展开时才被调用，
    # 所以光标没靠近时开销为 0。判定与两个 Win32 坑见 ui/occlusion.py 模块说明。
    def _occlude_enabled(self) -> bool:
        """默认开启：只有显式存过 "0" 才算关（老库没有这个键）。"""
        return self.db.get_setting("occlude_aware", "1") != "0"

    def _toggle_occlude(self, checked: bool) -> None:
        self.db.set_setting("occlude_aware", "1" if checked else "0")
        if not checked:
            self._clear_occlude_state()   # 关掉就把提示条与强行展开计时器一起清掉

    def _expanded_physical_rect(self) -> tuple[int, int, int, int] | None:
        """面板**滑出后**要占的矩形，物理像素。

        算法是"窗口当前物理矩形 + 逻辑位移×DPR"：单块屏内 DPR 恒定，位移换算精确。
        不去拼"逻辑桌面坐标→物理坐标"的多尺度换算——混合 DPI 下它没有单值解
        （`region_picker` 里那套 `geo.x()*dpr` 只在各屏 DPR 相同时成立）。
        """
        if self._dock_edge is None:
            return None
        try:
            hwnd = int(self.winId())
        except Exception:
            return None
        rect = occlusion.window_rect(hwnd)
        if rect is None:
            return None
        dpr = self._target_screen().devicePixelRatio() or 1.0
        collapsed_x, expanded_x = self._docked_positions()
        return occlusion.shift_rect(rect, int(round((expanded_x - collapsed_x) * dpr)))

    def _occluded(self) -> bool:
        """面板位置上是否已经有别人的窗口。查不出来时一律按"没有"。"""
        if not self._occlude_enabled():
            return False
        rect = self._expanded_physical_rect()
        if rect is None:
            return False    # 算不出矩形就当没人挡着：宁可显示，也不要莫名不弹
        verdict = self._occlude_probe.check(rect, os.getpid())
        self._occlude_blocked = verdict.blocked
        self._occlude_thing = verdict.thing
        return verdict.blocked

    def _handle_occluded(self) -> bool:
        """被挡就"不展开 + 起强行展开计时器 + 弹一次提示"，返回 True。

        这是**唯一**的拦截入口：`_poll_cursor`（光标已压到/靠近边缘）与
        `_on_expand_timeout`（热区犹豫期到点）都走它——两条展开路径各写一遍必漏。
        """
        if not self._occluded():
            self._clear_occlude_state()
            return False
        # ⚠️ 只停犹豫期计时器，**不能调 stop_dock_timers()**：那会把强行展开计时器
        # 一起停掉，逃生口就没了（被挡 = 永远弹不出来）。
        self._expand_timer.stop()
        if not self._force_timer.isActive():
            self._force_timer.start()
        self._notify_occluded()
        return True

    def _on_force_expand(self) -> None:
        """被挡时"停够时间" → 用户显然是刻意要它：照常展开。

        这就是逃生口：不必记热键——扫过去不弹，停下来才弹。
        """
        if self._dock_edge is None or self._dock_expanded:
            return
        if not (self._pointer_inside() or self._pointer_in_hot_zone()):
            return              # 中途移开了，不改主意（下次靠近重新计时）
        self._clear_occlude_state()
        self._dock_slide_out()

    def _notify_occluded(self) -> None:
        """弹一次小提示条。每次靠近只弹一次，光标停在边缘不会反复闪。"""
        if self._occlude_notified:
            return
        self._occlude_notified = True
        if self._occlude_hint is None:
            # 懒建：不用这个功能就不多开一个窗口。父级是面板 → 随面板销毁。
            self._occlude_hint = OccludeHint(self)
        self._occlude_hint.show_for(
            self._dock_edge or "right",
            # availableGeometry：提示条是置顶窗，不能压在系统任务栏上
            self._target_screen().availableGeometry(),
            self.y() + self.height() // 2,
            self._occlude_thing,
        )

    def _clear_occlude_state(self) -> None:
        """清掉"这一次靠近"的探测临时状态（强行展开计时器 + 提示条）。

        统一入口：展开 / 收回 / 让位 / 取消停靠 / 关开关都过这里。分开写必然漏一处
        ——本项目"收回背景消失"与第五轮"状态漏组合"都是这么来的。
        """
        self._force_timer.stop()
        self._occlude_notified = False
        self._occlude_blocked = False
        self._occlude_thing = ""
        if self._occlude_hint is not None:
            self._occlude_hint.hide_hint()

    # ---------- 贴边自动隐藏 ----------
    @staticmethod
    def _snap_edge(win_x: int, win_w: int, screen_x: int, screen_w: int,
                   threshold: int) -> str | None:
        """拖动松手时判断应吸附哪一侧边缘。返回 "left" / "right" / None。"""
        if threshold <= 0:
            return None
        dist_left = win_x - screen_x                     # 窗口左缘距屏幕左缘
        dist_right = (screen_x + screen_w) - (win_x + win_w)  # 窗口右缘距屏幕右缘
        left_hit = -threshold <= dist_left <= threshold
        right_hit = -threshold <= dist_right <= threshold
        if left_hit and (not right_hit or dist_left <= dist_right):
            return "left"
        if right_hit:
            return "right"
        return None

    def _toggle_autohide(self, checked: bool) -> None:
        self.db.set_setting("autohide", "1" if checked else "0")
        if not checked and self._dock_edge is not None:
            self._undock()

    def _maybe_snap(self) -> None:
        """拖动松手后尝试吸附边缘（仅开启自动隐藏时生效）。"""
        if self.db.get_setting("autohide") != "1":
            return
        screen = QApplication.screenAt(self.geometry().center())
        if screen is None:
            return
        geo = screen.geometry()
        edge = self._snap_edge(self.x(), self.width(), geo.x(), geo.width(),
                               config.DOCK_SNAP_PX)
        if edge:
            self._dock(edge, geo)
        elif self._dock_edge is not None:
            self._undock()

    def _dock(self, edge: str, screen_geo) -> None:
        self._dock_edge = edge
        self._dock_expanded = False
        self._manual_hold = False
        self.db.set_setting("dock_edge", edge)
        self.db.set_setting("dock_y", str(max(screen_geo.y(), min(self.y(),
                            screen_geo.y() + screen_geo.height() - self.height()))))
        # 记住停在哪块屏：副屏停靠重启后要能还原（否则 _target_screen 会按
        # 默认窗口位置定位到主屏，dock_y 还原到错误的屏上）
        screen = QApplication.screenAt(screen_geo.center()) or self.screen()
        self.db.set_setting("dock_screen", screen.name() if screen else "")
        target_x, _ = self._docked_positions(screen)
        self.move(QPoint(target_x, self.y()))
        self._start_cursor_poll()

    def _undock(self) -> None:
        self._dock_edge = None
        self._dock_expanded = False
        self._manual_hold = False
        self.db.set_setting("dock_edge", "")
        self.db.set_setting("dock_screen", "")
        self.stop_dock_timers()
        self._cursor_timer.stop()   # 离开停靠态就不必再轮询光标

    def _target_screen(self):
        """定位窗口所在的那块屏。

        收起后窗口大半在屏幕外，`geometry().center()` 会落到屏幕外的无人区，
        screenAt() 返回 None；因此改用**露出的那条边**去定位。
        """
        edge_x = self.x() + self.width() if self._dock_edge == "left" else self.x()
        return QApplication.screenAt(QPoint(edge_x, self.y() + self.height() // 2)) \
            or QApplication.primaryScreen()

    def _screen_by_name(self, name: str):
        """按 QScreen.name() 找屏（重启后还原副屏停靠用）；找不到返回 None。"""
        if not name:
            return None
        for screen in QApplication.screens():
            if screen.name() == name:
                return screen
        return None

    def _docked_positions(self, screen=None) -> tuple[int, int]:
        """返回 (收起位置, 展开位置) 的 x 坐标。

        screen 可显式指定（重启还原副屏停靠时用）；缺省按窗口当前位置推断。
        """
        geo = (screen or self._target_screen()).geometry()
        if self._dock_edge == "left":
            return geo.x() - self.width() + config.DOCK_STRIP_PX, geo.x()
        return geo.x() + geo.width() - config.DOCK_STRIP_PX, \
            geo.x() + geo.width() - self.width()

    # ---------- 展开 / 收回 ----------
    def _dock_slide_out(self) -> None:
        """展开：滑到可见位置（不抢焦点）。"""
        if self._dock_edge is None or self._dock_expanded:
            return
        self.stop_dock_timers()
        self._manual_hold = False
        self._dock_expanded = True
        _, target_x = self._docked_positions()
        self.show()
        self._start_cursor_poll()   # 从托盘重新显示后轮询要接着跑
        self.raise_()
        # 展开：快起慢停，落位有"吸附感"
        self._animate_to(QPoint(target_x, self.y()),
                         easing=QEasingCurve.Type.OutCubic)

    def _dock_slide_in(self) -> None:
        """收回：滑到只露细条的位置（不隐藏窗口，细条仍可唤起）。"""
        if self._dock_edge is None or not self._dock_expanded:
            return
        self.stop_dock_timers()
        self._dock_expanded = False
        target_x, _ = self._docked_positions()
        # 收回：慢起快走。之前两个方向都用 OutCubic，收回时"先窜后蹭"，
        # 看着像卡了一下；ease-in 让面板平滑加速离场，再配合关闭原生模糊，
        # 消除 Acrylic 逐帧重算背景带来的掉帧。
        self._animate_to(QPoint(target_x, self.y()),
                         easing=QEasingCurve.Type.InCubic)

    def _animate_to(self, pos: QPoint, easing=QEasingCurve.Type.OutCubic) -> None:
        """滑动到指定位置。

        动画**可打断**：上一次动画未结束就折返时，从当前位置续走，并按剩余
        行程缩短时长，避免"反向也要等一个满行程"的迟钝感。

        ⚠️ 这里**故意不挂起原生模糊**。曾经为了"消除 Acrylic 逐帧重算背景的
        开销"而挂起，实测（3440×1440，60Hz）挂起与保留的帧间隔只差
        **0.02ms/帧**，纯属过早优化；而每次挂起/恢复都会留下一帧背景错配
        （系统背景已撤、Qt 底板未落 = 直接透出桌面），展开与收回各闪一次。
        """
        if pos == self.pos():
            self.move(pos)
            self._resume_glass()    # 兜底：万一处于挂起态（拖动未正常收尾）
            return
        if self._anim is not None:
            self._anim.stop()   # stop() 不触发 finished，下面的新动画会接管
        travel = max(1, self.width() - config.DOCK_STRIP_PX)
        ratio = min(1.0, abs(self.x() - pos.x()) / travel)
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(max(config.DOCK_ANIM_MIN_MS, int(config.DOCK_ANIM_MS * ratio)))
        anim.setStartValue(self.pos())
        anim.setEndValue(pos)
        anim.setEasingCurve(easing)
        anim.finished.connect(self._on_anim_finished)
        anim.start()
        self._anim = anim  # 持有引用防 GC；窗口销毁时随父对象释放

    def _on_anim_finished(self) -> None:
        # 动画本身不再碰原生模糊，这里只是兜底：万一因拖动遗留了挂起态，
        # 滑动结束顺带收干净（正常路径下 _resume_glass 会立即返回）。
        self._resume_glass()

    def _restore_dock(self) -> None:
        """启动时恢复上次的停靠状态（边缘 + 垂直位置 + 所在屏）。"""
        if self.db.get_setting("autohide") != "1":
            return
        edge = self.db.get_setting("dock_edge")
        if edge not in ("left", "right"):
            return
        self._dock_edge = edge
        self._dock_expanded = False
        # 上次停靠的屏（副屏场景；老数据库没有这个键时退回当前位置推断）
        screen = self._screen_by_name(self.db.get_setting("dock_screen")) \
            or self._target_screen()
        geo = screen.geometry()
        target_x, _ = self._docked_positions(screen)
        # dock_y 是与 _dock 写入时同一套钳制规则；屏幕布局可能变过，再夹一次
        y = self.y()
        raw = self.db.get_setting("dock_y")
        if raw:
            try:
                y = int(raw)
            except ValueError:
                pass
        y = max(geo.y(), min(y, geo.y() + geo.height() - self.height()))
        self.move(QPoint(target_x, y))
        self._start_cursor_poll()

    def stop_dock_timers(self) -> None:
        self._expand_timer.stop()
        self._collapse_timer.stop()
        # 遮挡探测的临时状态（强行展开计时器 + 提示条）与这两个计时器同属
        # "这一次靠近"的有效期：展开 / 收回 / 让位 / 取消停靠都必须一起清干净。
        self._clear_occlude_state()

    def _start_cursor_poll(self) -> None:
        """停靠期间保持光标轮询；未停靠时不该有轮询开销。"""
        if self._dock_edge is not None:
            self._cursor_timer.start()

    # ---------- 靠近判定（轮询光标） ----------
    def _cursor_pos(self) -> QPoint:
        """当前系统光标位置。抽成方法便于测试注入。"""
        return QCursor.pos()

    def _pointer_inside(self, margin: int = 0) -> bool:
        rect = self.frameGeometry()
        if margin:
            rect = rect.adjusted(-margin, -margin, margin, margin)
        return rect.contains(self._cursor_pos())

    def _pointer_in_hot_zone(self) -> bool:
        """光标是否落在贴边热区里（比露出的细条更宽，斜着靠近也算）。"""
        if self._dock_edge is None:
            return False
        cur = self._cursor_pos()
        geo = self._target_screen().geometry()
        pad = config.DOCK_HOT_ZONE_PAD_Y
        if not (self.y() - pad <= cur.y() <= self.y() + self.height() + pad):
            return False
        if self._dock_edge == "left":
            return geo.x() <= cur.x() <= geo.x() + config.DOCK_HOT_ZONE_PX
        return geo.x() + geo.width() - config.DOCK_HOT_ZONE_PX <= cur.x() \
            <= geo.x() + geo.width()

    def _poll_cursor(self) -> None:
        """按系统真实光标位置决定展开/收回。

        为什么不用 Enter/Leave 事件作为唯一依据：收起后窗口只有 6px 露在屏幕
        边缘，Enter/Leave 是"控件级"事件——子控件之间切换、以及 Acrylic 窗口
        的命中测试都可能让它漏发或抖动，于是出现"鼠标靠近了却不弹出"、或悬停
        中莫名收回。轮询 QCursor.pos() 拿到的是系统真实光标位置，与事件路由
        完全无关，稳定得多。
        """
        if self._dock_edge is None or self._drag_pos is not None:
            return
        if not self.isVisible():
            return
        inside = self._pointer_inside()
        if not inside:
            self._manual_hold = False   # 光标离开面板 → 解除手动收回锁
        elif self._manual_hold:
            return                      # 手动收回后光标仍停在面板上，不自动弹出

        # 遮挡探测只在"收起态 + 光标已经靠近"时才做：展开后不必探（面板自己就占着
        # 那块地方），光标没靠近时一次都不探 → 常态零开销。
        if (not self._dock_expanded and (inside or self._pointer_in_hot_zone())
                and self._handle_occluded()):
            return
        if inside:
            # 光标已经压在面板（含收起时露出的细条）上 → 立刻展开，不留犹豫期
            self.stop_dock_timers()
            self._dock_slide_out()
            return
        if self._dock_expanded:
            # 展开但光标离开了面板：留一段缓冲再收回，避免手抖就缩回去
            if self._pointer_inside(config.DOCK_LEAVE_MARGIN_PX):
                self._collapse_timer.stop()
            elif not self._collapse_timer.isActive():
                self._collapse_timer.start()
            return
        # 收起态：只有靠近边缘热区才展开
        if self._pointer_in_hot_zone():
            # 注意用 isActive 判断：每次 start() 都会重置计时，若不判就会
            # 被轮询不停重置，计时**永远到不了点**。
            if not self._expand_timer.isActive():
                self._expand_timer.start()
        else:
            self._expand_timer.stop()
            self._clear_occlude_state()   # 光标离开边缘：强展开计时器与提示条一起清

    def _on_expand_timeout(self) -> None:
        """犹豫期结束：再确认一次光标还在边缘附近，避免"扫过就弹出"。"""
        if not (self._pointer_inside() or self._pointer_in_hot_zone()):
            return
        if self._handle_occluded():
            return      # 犹豫期这 100ms 里冒出来的窗口同样拦住（改为重新计时 1 秒）
        self._dock_slide_out()

    def _on_collapse_timeout(self) -> None:
        """收回前再确认光标确实已经离开，避免轮询空档里的误收回。"""
        if not self._pointer_inside(config.DOCK_LEAVE_MARGIN_PX):
            self._dock_slide_in()

    def enterEvent(self, event) -> None:  # noqa: N802
        # 快路径：真收到进入事件就立刻评估，不必等轮询节拍
        self._poll_cursor()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        # 收回判定统一交给 _poll_cursor（按真实光标位置判断）：子控件之间
        # 的 Enter/Leave 抖动不会再把悬停中的面板误收回。
        self._poll_cursor()
        super().leaveEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """把落在玻璃面板/卡片上的鼠标拖动转交给窗口移动。

        WA_TransparentForMouseEvents 的控件收不到事件，所以这里处理 _surface、
        列表宿主，以及"既要能点、又要能拖"的按钮。

        关键：按下时不立刻进入拖动，而是先记录起点，等鼠标真正移动超过
        DRAG_THRESHOLD 才升级为拖动。否则按钮的 press/release 会被拖动逻辑
        吞掉，导致「完成」按钮点不动。
        """
        if not isinstance(obj, QWidget):
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._pending_press = event.globalPosition().toPoint()
                self._drag_pos = None       # 尚未确认是拖动
        elif et == QEvent.Type.MouseMove:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                return super().eventFilter(obj, event)
            gpos = event.globalPosition().toPoint()
            if self._drag_pos is None:
                anchor = self._pending_press
                if anchor is None:
                    return super().eventFilter(obj, event)
                if (gpos - anchor).manhattanLength() < _DRAG_THRESHOLD_PX:
                    return super().eventFilter(obj, event)
                self._begin_drag(anchor)    # 超过阈值，正式进入拖动
            self._drag_move(gpos)
            return True
        elif et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                was_dragging = self._drag_pos is not None
                self._pending_press = None
                if was_dragging:
                    self._end_drag()
                    return True     # 拖动结束，不再触发按钮点击
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    # ---------- 拖动（统一入口） ----------
    def _suspend_glass(self) -> None:
        """临时关闭原生模糊（仅拖动期间使用），并补上定格快照。

        `glass.suspend()` 撤掉的是**系统画在窗口后面的那层背景**。撤掉之后必须由
        Qt 补上背景，否则窗口直接变全透明（只剩按钮和文字）。这个坑很隐蔽：在原生
        模糊不可用的机器上永远不会出现，只有真拿到 Acrylic 才会暴露。

        但**只补一块纯色是不够的** —— 那正是用户反馈的"移动的时候毛玻璃效果直接
        没有了，也只有颜色"。所以这里在撤之前先趁模糊还在，抓一张窗口自己的快照
        （`glass.capture_window_surface`），由 `_GlassSurface` 在拖动全程把它当背景
        画回去，观感与静止时一致；系统那边只是在合成一个普通窗口（跟手、无迟滞）。

        ⚠️ **顺序是正确性的一部分**：必须先让 Qt 把底板/快照画上去（同步重绘落地），
        **再**撤系统背景。反过来写的话，系统背景已撤、Qt 底板还没到，中间那一帧
        就是全透明、直接透出桌面 —— 实测稳定复现（均值从 41.6 跳到 82.7）。
        """
        if not self._glass_on or self._glass_suspended:
            return
        self._glass_suspended = True
        try:
            shot = glass.capture_window_surface(int(self.winId()))
        except Exception:
            shot = None
        self._surface.set_freeze(shot)
        self._apply_surface_style(theme.BG_TRANSIENT_ALPHA)
        self._surface.repaint()     # 同步落地，不给 DWM 留出空背景的那一帧
        try:
            glass.suspend(int(self.winId()))
        except Exception:
            pass

    def _resume_glass(self) -> None:
        if not self._glass_on or not self._glass_suspended:
            return
        restored = False
        try:
            restored = bool(glass.resume(int(self.winId())))
        except Exception:
            pass
        if not restored:
            # 模糊没恢复成功 → 底板继续留着。宁可少一层模糊，
            # 也不能切回透明让整个面板消失。
            self._apply_surface_style(theme.BG_TRANSIENT_ALPHA)
            return
        # 顺序：先把系统背景装回来，再撤掉定格快照与 Qt 底板。此刻快照还在，
        # 顶多是一帧"双背景"，远轻于"无背景"。
        self._glass_suspended = False
        self._surface.set_freeze(None)
        self._apply_surface_style(None)
        self._surface.repaint()

    def _begin_drag(self, global_pos: QPoint) -> None:
        self.stop_dock_timers()
        self._drag_pos = global_pos - self.frameGeometry().topLeft()
        self._suspend_glass()

    def _drag_move(self, global_pos: QPoint) -> None:
        if self._drag_pos is None:
            return
        self.move(global_pos - self._drag_pos)

    def _end_drag(self) -> None:
        self._drag_pos = None
        self._resume_glass()
        self._maybe_snap()
        if self._dock_edge is None:
            # 记住这次拖到的位置，下次启动（含开机自启）就在这儿出现。
            # 吸附成停靠态则不记：那种位置由 dock_edge/dock_y 表达，
            # 记进这里反而会覆盖掉用户上次的自由位置。
            window_state.remember(self.db, window_state.WINDOW_PANEL, self)

    def _restore_position(self) -> None:
        """启动时把面板移回上次拖到的位置（开机自启也走这条路径）。

        停靠态的位置由 dock_edge + dock_y 决定，不归这里管 —— 两者都写的话
        会互相覆盖。
        """
        if self._dock_edge is not None:
            return
        window_state.restore(self.db, window_state.WINDOW_PANEL, self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pending_press = event.globalPosition().toPoint()
            self._drag_pos = None

    def toggle_visible(self) -> None:
        """托盘图标 / 全局热键触发的切换。

        带停靠状态时不再是"显示/隐藏"二态：
        收起中 → 滑出展开；展开中 → 收回细条；只有自由浮动时才隐藏窗口。
        """
        if self._quiet:
            # 让位期间的手动显隐**完全听用户的**，并记下来：回位时不再擅自改回去。
            # （让位期间面板一定是隐藏的，所以第一次按 = 唤出、第二次按 = 收起；
            # 这两种意图都会走到这里。）热键因此天然就是"临时忽略"的逃生通道。
            self._quiet_restore_visible = not self.isVisible()
        if not self.isVisible():
            self.show()
            self._start_cursor_poll()
            self.raise_()
            self.activateWindow()
        elif self._dock_edge is not None:
            if self._dock_expanded:
                # 手动收回：光标很可能还停在面板上，加锁避免轮询立刻又弹出来
                self._manual_hold = True
                self._dock_slide_in()
            else:
                self._manual_hold = False
                self._dock_slide_out()
        else:
            self.hide()

    # ---------- 拖动 ----------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        gpos = event.globalPosition().toPoint()
        if self._drag_pos is None:
            anchor = self._pending_press
            if anchor is None:
                return
            if (gpos - anchor).manhattanLength() < _DRAG_THRESHOLD_PX:
                return
            self._begin_drag(anchor)
        self._drag_move(gpos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pending_press = None
        if self._drag_pos is not None:
            self._end_drag()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.stop_dock_timers()
        self._cursor_timer.stop()   # 隐藏后不应再被轮询唤起
        self.hide()  # 关闭按钮 = 最小化到托盘
