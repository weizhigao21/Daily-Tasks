# -*- coding: utf-8 -*-
"""设计 token 与全局样式（暗色玻璃 · 克制风）。

单一来源：颜色 / 字号 / 间距 / 圆角 / 阴影全部在此定义，
taskbar、task_dialog、stats_panel 统一从这里取，禁止各自硬编码色值。
"""
from __future__ import annotations

# ---------- 颜色 token ----------
# 玻璃底板（原生模糊生效时作为半透明叠加层，降级时即面板本身）
BG_BASE = "#1B1D21"
# 降级自绘玻璃时面板的不透明度。0.72 → 0.86：玻璃感保留，但文字背后的
# 底色更稳定，暗底小字不会因桌面亮色花纹而"糊掉"。
BG_BASE_ALPHA = 0.86
# 原生模糊被临时关闭期间（拖动 / 滑行动画）用的底板不透明度。
# 此时系统背景已被撤掉，底板必须自己顶上，否则窗口只剩按钮与文字悬空；
# 取比降级值略低，是为了尽量贴近 Acrylic 的观感，动画结束切回透明时不"跳"。
BG_TRANSIENT_ALPHA = 0.74
BG_ELEVATED = "rgba(255, 255, 255, 0.04)"   # 卡片行底色
BG_ELEVATED_HOVER = "rgba(255, 255, 255, 0.075)"
BG_INPUT = "rgba(0, 0, 0, 0.28)"
# 弹窗表面（右键菜单 / 下拉列表 / 工具提示）。比面板底板亮一档：它们是"浮在
# 面板之上"的一层，同色会看不出层级。原生模糊生效时这层会被替换成磨砂玻璃，
# 见 `menu_qss()`。
BG_MENU = "#23262C"

STROKE = "rgba(255, 255, 255, 0.10)"        # 面板描边
STROKE_SOFT = "rgba(255, 255, 255, 0.06)"   # 卡片描边
STROKE_HOVER = "rgba(255, 255, 255, 0.18)"

# 强调色 · 单一青蓝
ACCENT = "#5AC8FA"
ACCENT_DIM = "rgba(90, 200, 250, 0.16)"
ACCENT_TEXT = "#0B1216"        # 强调色按钮上的文字（深色保证对比）

# 语义色
DONE = "#4ADE80"
TODO = "#8B93A1"
WARN = "#FBBF24"
ERROR = "#F87171"

# 文字层级（暗底上的对比度已按 WCAG AA 校准：主文字 13:1 以上）
TEXT_PRIMARY = "#FFFFFF"       # 主文字：纯白，最高对比
TEXT_SECONDARY = "#C3CAD6"     # 次要文字
TEXT_MUTED = "#8B94A3"         # 弱化文字（较原 #6B7280 提亮，小字不再糊）

SCROLL_HANDLE = "rgba(255, 255, 255, 0.20)"
SCROLL_HANDLE_HOVER = "rgba(255, 255, 255, 0.34)"

# ---------- 字号 / 字重 ----------
# 整体上浮一档：11→12 是 9% 增幅，在 100% 缩放的笔记本屏上肉眼可辨。
FS_XS = 12
FS_SM = 13
FS_BASE = 14
FS_LG = 16
FS_XL = 20

# 字号缩放系数：跟随系统 DPI 微调（1.0 = 基准，见 set_scale）
_scale = 1.0


def set_scale(factor: float) -> None:
    """设置字号缩放系数（由高 DPI 检测调用，1.0 为基准）。

    基准字号本身已按"暗底小字要看得清"调过一轮，这里只做浮动微调：
    4K/200% 缩放的屏幕上需要更大，100% 的屏幕保持原样。
    """
    global _scale
    _scale = max(0.8, min(2.0, factor))


def fs(px: int) -> int:
    """把基准字号换算成当前缩放下的实际像素值。"""
    return max(11, round(px * _scale))


# ---------- 间距 / 圆角 ----------
SP_1 = 4
SP_2 = 8
SP_3 = 12
SP_4 = 16
ROW_HEIGHT = 46          # 比 44 略高：给双行文字更松的呼吸空间
PANEL_WIDTH = 392

R_PANEL = 14
R_CARD = 10
R_PILL = 999

FONT_FAMILY = '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif'


def bg_rgba(alpha: float) -> str:
    """面板底板色（带透明度）。

    从 BG_BASE 推导而不是各自写死 `rgba(27, 29, 33, …)`——同一颜色在 QSS 与
    窗口代码里各存一份，改色时必漏一处（`#1B1D21` 与 `27,29,33` 是同一个值，
    肉眼根本看不出来）。
    """
    r, g, b = (int(BG_BASE[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


def app_qss() -> str:
    """全局样式表：应用到 QApplication，覆盖所有对话框与面板。

    字号一律走 fs() 换算，保证高 DPI 下同步放大。
    """
    fs_xs, fs_sm = fs(FS_XS), fs(FS_SM)
    fs_base, fs_lg = fs(FS_BASE), fs(FS_LG)
    return f"""
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: {fs_base}px;
    color: {TEXT_PRIMARY};
}}

/* ---------- 主面板：玻璃底板 ---------- */
QWidget#glassRoot {{
    background: transparent;
}}
QWidget#glassSurface {{
    background: {bg_rgba(BG_BASE_ALPHA)};
    border: 1px solid {STROKE};
    border-radius: {R_PANEL}px;
}}

/* ---------- 标题区 ---------- */
QLabel#appTitle {{
    font-size: {fs_lg}px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
QLabel#appVersion {{
    font-size: {fs_xs}px;
    color: {TEXT_MUTED};
}}
QLabel#appDate {{
    font-size: {fs_sm}px;
    color: {TEXT_MUTED};
}}
QLabel#sectionLabel {{
    font-size: {fs_xs}px;
    font-weight: 600;
    color: {TEXT_MUTED};
    letter-spacing: 1px;
}}
QFrame#divider {{
    background: {STROKE_SOFT};
    border: none;
    max-height: 1px;
}}

/* ---------- 任务卡片 ---------- */
QWidget#taskCard {{
    background: {BG_ELEVATED};
    border: 1px solid {STROKE_SOFT};
    border-radius: {R_CARD}px;
}}
QWidget#taskCard:hover {{
    background: {BG_ELEVATED_HOVER};
    border-color: {STROKE_HOVER};
}}
QLabel#taskName {{
    font-size: {fs_base}px;
    font-weight: 500;
    color: {TEXT_PRIMARY};
    background: transparent;
}}
QLabel#taskNameDone {{
    font-size: {fs_base}px;
    font-weight: 500;
    color: {TODO};
    text-decoration: line-through;
    background: transparent;
}}
QLabel#taskMeta {{
    font-size: {fs_xs}px;
    color: {TEXT_SECONDARY};
    background: transparent;
}}
QLabel#statusPillTodo, QLabel#statusPillDone, QLabel#statusPillBusy,
QLabel#statusPillFail, QLabel#statusPillOff {{
    font-size: {fs_xs}px;
    font-weight: 600;
    /* 固定高度 + 半径取半高即可成完整胶囊。Qt 的 QSS 对超大半径
       （如 999px）会直接放弃圆角，必须给确定值。 */
    min-height: 20px;
    max-height: 20px;
    border-radius: 10px;
    padding: 0px 10px;
    background: transparent;
}}
QLabel#statusPillTodo  {{ color: #C3CAD6; background: rgba(195, 202, 214, 0.18); }}
QLabel#statusPillDone  {{ color: #6EE79A; background: rgba(74, 222, 128, 0.20); }}
QLabel#statusPillBusy  {{ color: #FCD34D; background: rgba(251, 191, 36, 0.20); }}
QLabel#statusPillFail  {{ color: #FCA5A5; background: rgba(248, 113, 113, 0.20); }}
QLabel#statusPillOff   {{ color: {TEXT_MUTED}; background: rgba(139, 148, 163, 0.16); }}
QLabel#emptyHint {{
    color: {TEXT_MUTED};
    font-size: {fs_sm}px;
    padding: 28px 12px;
    background: transparent;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid {STROKE};
    border-radius: 8px;
    padding: 5px 12px;
    color: {TEXT_PRIMARY};
    font-size: {fs_sm}px;
}}
QPushButton:hover {{
    background: rgba(255, 255, 255, 0.12);
    border-color: {STROKE_HOVER};
}}
QPushButton:pressed {{
    background: rgba(255, 255, 255, 0.04);
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background: rgba(255, 255, 255, 0.03);
    border-color: {STROKE_SOFT};
}}
QPushButton#primaryBtn {{
    background: {ACCENT};
    color: {ACCENT_TEXT};
    border: none;
    font-weight: 600;
}}
QPushButton#primaryBtn:hover {{
    background: #7AD6FA;
}}
QPushButton#ghostBtn {{
    background: transparent;
    border: 1px solid {STROKE_SOFT};
    color: {TEXT_SECONDARY};
}}
QPushButton#ghostBtn:hover {{
    background: rgba(255, 255, 255, 0.08);
    color: {TEXT_PRIMARY};
}}
/* 图标按钮只负责**底色**（图标本身是自绘的矢量图，见 ui/icons.py）。
   这里的 color 只是兜底：图标颜色由 IconButton 自己按悬停状态取 theme 常量，
   不走 palette——QSS 的颜色何时同步进 palette 取决于样式引擎的 polish 时机，
   不想把图标颜色押在那上面。 */
QPushButton#iconBtn {{
    background: transparent;
    border: none;
    border-radius: 8px;
    color: {TEXT_SECONDARY};
    padding: 0;
}}
QPushButton#iconBtn:hover {{
    background: {ACCENT_DIM};
    color: {ACCENT};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit {{
    background: {BG_INPUT};
    border: 1px solid {STROKE};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QTimeEdit:focus {{
    border-color: {ACCENT};
    background: rgba(0, 0, 0, 0.36);
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QTimeEdit:disabled {{
    color: {TEXT_MUTED};
    background: rgba(0, 0, 0, 0.16);
}}
/* 多行文本框单独成组。⚠️ 别把它并进上面那组就以为完事——这里是本项目栽过的坑：
   上面的 `QWidget {{ color: {TEXT_PRIMARY} }}` 是**纯白**，而 QPlainTextEdit 属于
   "没人给它刷过底色"的控件，底色来自系统调色板（浅色系统 = 纯白），于是白字白底、
   整块内容看不见。类型选择器在 QSS 里同权重、**后写的赢**，所以这条必须排在那条之后。
   QrResultDialog 是第一个用它的地方（首用即中招），加规则时一并把 QTextEdit 带上。 */
QPlainTextEdit, QTextEdit {{
    background: {BG_INPUT};
    border: 1px solid {STROKE};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT};
}}
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
    background: rgba(0, 0, 0, 0.36);
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_SECONDARY};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {BG_MENU};
    border: 1px solid {STROKE};
    border-radius: 8px;
    padding: 4px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_DIM};
    selection-color: {ACCENT};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}

/* ---------- 复选框 ---------- */
QCheckBox {{
    color: {TEXT_SECONDARY};
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1px solid {STROKE_HOVER};
    background: {BG_INPUT};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

/* ---------- 对话框 ---------- */
QDialog {{
    background: {BG_BASE};
}}
QDialog QLabel {{
    color: {TEXT_SECONDARY};
    background: transparent;
}}
/* ---------- 表格 ---------- */
QTableWidget {{
    background: rgba(0, 0, 0, 0.22);
    border: 1px solid {STROKE_SOFT};
    border-radius: {R_CARD}px;
    gridline-color: transparent;
    color: {TEXT_PRIMARY};
    outline: none;
}}
QTableWidget::item {{
    padding: 8px 10px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {ACCENT_DIM};
    color: {ACCENT};
}}
/* 表头是 QTableView 里的**独立子控件**，不在 QTableWidget 的绘制范围内。
   只把 section 设成透明没用：那时露出来的是 QHeaderView 自己的**系统调色板**
   底色（本机实测 #F0F0F0），深色面板顶上就是一条刺眼的白带。宿主这层也必须
   一起去底色，才能透到 QTableWidget 自己的 rgba(0,0,0,0.22) 上。 */
QHeaderView {{
    background: transparent;
    border: none;
}}
QHeaderView::section {{
    background: transparent;
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {STROKE_SOFT};
    padding: 8px 10px;
    font-size: {fs_xs}px;
    font-weight: 600;
}}

/* ---------- 滚动条 ---------- */
QScrollArea {{
    background: transparent;
    border: none;
}}
/* 只给滚动区的内容宿主去底色。绝不能用
   `QScrollArea > QWidget > QWidget`——它会命中滚动区里所有两级后代
   （卡片、状态胶囊等），强行给它们刷上底色，导致胶囊变成深色方块。 */
QWidget#listHost {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {SCROLL_HANDLE};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {SCROLL_HANDLE_HOVER};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    height: 0;
}}
QScrollBar:horizontal {{
    height: 0;
}}

/* ---------- 菜单 / 提示 ----------
   弹窗菜单是**独立顶层窗口**，不在主面板那棵样式树里。托盘右键菜单一度因为
   没人给它套样式而露出系统默认的浅色（实测 #F2F2F2 底 + #CCCCCC 边框），
   与主 UI 完全不搭。现在统一走 `menu_qss()` / `ui.menu.GlassMenu`。
   这里这份是给"挂在别的控件树上的菜单"兜底的（如 QLineEdit 自带的右键菜单，
   它会把父控件的样式表继承过去），底色与 menu_qss("opaque") 同源。 */
QMenu {{
    background: {BG_MENU};
    border: 1px solid {STROKE};
    border-radius: {R_CARD}px;
    padding: 5px;
    color: {TEXT_PRIMARY};
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {ACCENT_DIM};
    color: {ACCENT};
}}
/* 置灰项必须自己声明颜色。QSS 一旦接管菜单，禁用态就不再走系统画法，
   而兜底那条 `QWidget {{ color: {TEXT_PRIMARY} }}` 是**纯白** —— 置灰项会跟
   可点项长得一模一样（托盘里「识别到网址显示「打开链接」」在直开开启时是被
   置灰的，看不出来就等于这个开关的状态没法读）。 */
QMenu::item:disabled {{
    color: {TEXT_MUTED};
    background: transparent;
}}
QMenu::separator {{
    height: 1px;
    background: {STROKE_SOFT};
    margin: 4px 8px;
}}
QToolTip {{
    background: {BG_MENU};
    color: {TEXT_PRIMARY};
    border: 1px solid {STROKE};
    border-radius: 6px;
    padding: 5px 8px;
}}
"""


def dialog_qss(mode: str = "opaque", bg_override: str | None = None) -> str:
    """对话框底板样式。两态与 `GlassDialogMixin` 的状态一一对应：

    "glass"  原生硬模糊（Acrylic）已在窗口后方生效 → 底色透明，透出模糊；
    "opaque" 原生模糊不可用、或被临时撤掉（移动中）→ 不透明底板顶上。

    bg_override 仅对 "opaque" 生效：移动期由 `glass.capture_window_surface()`
    从定格快照的边缘采出静止观感色传进来。写死成 BG_BASE 是不行的——静止观感是
    "Acrylic 的深色 tint 叠在模糊后的桌面"上，随壁纸而变（本机实测静止
    `RGB(85,82,81)` 而 BG_BASE 只有 `RGB(27,29,33)`），固定值会让移动瞬间从
    "磨砂灰"跳成"纯黑"，这正是用户反馈的"移动的时候背景会变成纯黑"。
    它同时也是定格快照之下的兜底：万一快照没画上，窗口也不会变成透明。

    为什么没有"半透明"这一档：曾经试过在移动期降级为 ACCENT_ENABLE_BLURBEHIND
    并配半透明底板（FluentWPF 的做法），但本机实测该 accent 直接输出纯白且切不
    回来，详见 `glass.py` 的实测记录。撤掉系统背景时底板必须是**不透明**的，
    否则背后内容会直接透上来。

    单独立一个函数而不是让调用方去替换 app_qss() 的字符串：字符串替换一旦
    改动上面的模板就静默失效，属于易碎写法。
    """
    bg = "transparent" if mode == "glass" else (bg_override or BG_BASE)
    return app_qss() + f"""
QDialog, QWidget#glassDialog {{
    background: {bg};
}}
"""


def menu_qss(mode: str = "opaque") -> str:
    """弹窗菜单底板样式。两态与 `ui.menu.GlassMenu` 的状态一一对应：

    "glass"  原生 Acrylic 已在弹窗后方生效 → 底板透明，透出磨砂玻璃；
    "opaque" 原生模糊不可用 → 不透明深色底板（= app_qss 里那套菜单观感）。

    为什么玻璃态仍然写着**圆角描边**：Win10 的 Acrylic 是一整块矩形模糊，
    它不吃窗口区域（实测：用 `SetWindowRgn` 给弹窗切了圆角，四角照样是模糊色
    #555352，深色区域的包围盒恰好等于整窗矩形）。所以玻璃态下四角本来就是
    方的 —— **主面板也一样**：探针复刻面板结构实测，玻璃态下它的深色区域同样
    等于整窗矩形、角点取到的是模糊色，只有那圈圆角描边是 Qt 画上去的。
    菜单照抄同一套处理，"同款"才是真的同源；单给菜单做真圆角反而会更不像。

    单独立一个函数、而不是让调用方复制一份 app_qss：底色只能有一个来源
    （`BG_MENU`），否则改色时必漏一处。
    """
    bg = "transparent" if mode == "glass" else BG_MENU
    return app_qss() + f"""
QMenu {{
    background: {bg};
    border: 1px solid {STROKE};
    border-radius: {R_CARD}px;
}}
"""
