# -*- coding: utf-8 -*-
"""全局路径与常量配置。"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：exe 所在目录为项目根（data 与 exe 同级，便于便携使用）
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    # 只读资源（图标等）在打包目录内
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "tasks.db"
TEMPLATE_DIR = DATA_DIR / "templates"
DEBUG_DIR = DATA_DIR / "debug"

APP_NAME = "日常任务栏"
# 版本号唯一来源：src/ui/version.py（此处不再重复定义，避免两处漂移）
APP_ICON = RESOURCE_DIR / "每日任务.ico"

# 全局热键的**默认值**（pynput 语法）。实际生效值存在 settings 表里，可在
# 托盘「快捷键设置…」里改；这里同时是"取值非法时的回退值"。
# 校验规则见 core/hotkeys.py（必须含 Ctrl / Alt / Win，否则会抢走正常打字）。
HOTKEY_TOGGLE = "<ctrl>+<alt>+t"
HOTKEY_QR = "<ctrl>+<alt>+q"

# 热键在 settings 表里的键名（面板与设置窗口两处都要用，别各写一遍字面量）
SETTING_HOTKEY_TOGGLE = "hotkey_toggle"
SETTING_HOTKEY_QR = "hotkey_qr"

# 扫码行为的开关键名。都是"默认开、只有显式存了 "0" 才算关"的语义
# （老库没这两个键，缺省必须等同于开，否则升级上来的用户会静默少掉功能）。
SETTING_QR_AUTOCOPY = "qr_autocopy"      # 识别出内容后自动写进剪贴板
SETTING_QR_OPEN_LINK = "qr_open_link"    # 识别到网址时在结果窗给「打开链接」
SETTING_QR_OPEN_DIRECT = "qr_open_direct"  # 识别到链接直接开浏览器，不弹结果窗

# 模板匹配默认相似度阈值
DEFAULT_MATCH_THRESHOLD = 0.85

# 多尺度搜索用的缩放序列：容忍 DPI / 窗口缩放造成的目标尺寸差异。
# 1.0 优先尝试且达阈值即提前退出，所以命中很快；但**未命中时会把整个序列跑完**，
# 这是当前最大的耗时项（3440x1440 约 2.8s）。
# 想提速就把序列收窄，例如 (1.0, 0.95, 1.05) 约省 35%~40%，
# 代价是对缩放差异的容忍变窄（目标尺寸偏差超过 5% 可能漏检）。
MATCH_SCALES = (1.0, 0.95, 1.05, 0.9, 1.1)

# 验证时在框选区域四周扩展多少像素内搜索模板（容忍目标小幅位移）
VERIFY_SEARCH_MARGIN_PX = 120

# True=每次验证截取整个虚拟桌面，目标挪到屏幕任何位置都能识别；
# False=只在框选区域±VERIFY_SEARCH_MARGIN_PX 范围内搜索（更快、更不易误判）
VERIFY_SEARCH_FULL_SCREEN = True

# 悬浮栏轮询刷新间隔（毫秒）
REFRESH_INTERVAL_MS = 30_000

# 贴边自动隐藏
DOCK_SNAP_PX = 48          # 拖到距边缘多少像素内松手吸附
DOCK_STRIP_PX = 6          # 收起后露出的细条宽度

# 靠近判定：收起后只有 6px 细条露出，靠 Enter/Leave 事件命中太苛刻，
# 改为**轮询系统真实光标位置**（见 Taskbar._poll_cursor），与事件路由无关。
DOCK_HOT_ZONE_PX = 10      # 光标距屏幕边缘多少像素内算"靠近"（比细条宽，留容错）
DOCK_HOT_ZONE_PAD_Y = 48   # 热区在窗口上下各外扩多少像素（斜着靠近也能触发）
DOCK_POLL_MS = 60          # 光标轮询间隔，决定"靠近"的响应粒度

DOCK_EXPAND_DELAY_MS = 100   # 仅在边缘热区（未压到面板）时需要的犹豫期
DOCK_COLLAPSE_DELAY_MS = 320  # 光标离开面板后多久收回（留出误触余量）
DOCK_LEAVE_MARGIN_PX = 28    # 光标离面板多远之外才开始计时收回

DOCK_ANIM_MS = 170         # 满行程滑入/滑出时长
DOCK_ANIM_MIN_MS = 90      # 反转到一半折返时的最短时长，避免拖沓

# 全屏免打扰：前台应用占满"面板所在的那块屏"时自动让位（隐藏 + 停靠近轮询），
# 退出全屏后自动回位。判定与两个坑见 ui/fullscreen.py 的模块说明。
FULLSCREEN_POLL_MS = 1000     # 检测节拍：一次只读前景窗口矩形，开销可忽略
FULLSCREEN_HOLD_POLLS = 2     # 连续命中几次才翻转结论（防游戏启动/切分辨率时闪）
FULLSCREEN_QUIET_DEFAULT = True   # 默认开启（托盘可关）
# 「伪全屏」也算让位：无边框窗口铺满**工作区**（底下仍露着任务栏）时同样让位。
# 判据是「覆盖工作区 **且** 窗口没有标题栏样式」——标题栏那一条是关键：最大化窗口
# 同样覆盖工作区，只有它能把两者区分开。判定细节见 ui/fullscreen.py。
FULLSCREEN_INCLUDE_BORDERLESS = True

# 遮挡探测：**面板滑出后要占的那块地方**已经有别人的窗口 → 不弹出（见 ui/occlusion.py）。
# 与上面的全屏判定是两套互补机制，不是替代关系：
# - 全屏判定问"前台窗口是不是全屏"——全局代理指标，只看一个窗口；
# - 遮挡探测问"我那块地方现在有没有别人"——就地测量，不必知道对方是什么程序。
# 后者能覆盖前者必然漏掉的一类：矩形判据不成立、但视觉上确实盖着面板位置。
OCCLUDE_COLS = 3            # 采样列数（取格心点，外圈自然内缩半格）
OCCLUDE_ROWS = 3            # 采样行数
OCCLUDE_RATIO = 0.5         # 被挡采样点占比达到多少算"被挡"
OCCLUDE_CACHE_MS = 350      # 同一矩形多久内复用上次结论。一次 3×3 探测实测 ≈2.3ms，
                            # 不能按 60ms 的光标轮询节拍花，必须节流。
OCCLUDE_HOVER_MS = 1000     # 被挡时"停够这么久就强行弹出"——逃生口，不必记热键
OCCLUDE_HINT_MS = 2600      # 遮挡提示条自动消失的时间

# 开机自启注册表项
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_KEY_NAME = "DailyTaskbar"


def ensure_dirs() -> None:
    """确保运行时目录存在（打包后首次运行也能自动创建）。"""
    for d in (DATA_DIR, TEMPLATE_DIR, DEBUG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resolve_template(path: str) -> "Path | None":
    """把库中存储的模板路径解析为磁盘路径。

    新数据只存**文件名**（可移植）；历史数据存的是绝对路径，规则：
    - 绝对路径且文件仍在 → 直接用；
    - 绝对路径已失效（换了机器 / 挪了目录）→ 按文件名回退到当前 templates 目录。
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        return TEMPLATE_DIR / p.name
    if p.exists():
        return p
    return TEMPLATE_DIR / p.name
