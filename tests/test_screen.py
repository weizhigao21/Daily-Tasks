# -*- coding: utf-8 -*-
"""mss 兼容层：不得回退到已弃用的 mss.mss() 工厂函数；屏幕采样不得逐像素读。"""
import re
import warnings
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# 逐像素读屏幕的 Win32 API。用词边界匹配，避免误伤 GetPixelFormat 这类无关函数。
_PER_PIXEL_API = re.compile(r"\b(?:Get|Set)Pixel\b")


def test_open_screen_works_without_deprecation_warning():
    """open_screen 返回可用上下文管理器，且不触发弃用警告。

    mss 10 起 `mss.mss()` 弃用（改用 `mss.MSS`），裸用会在未来版本直接失效。
    把 DeprecationWarning 升级为异常，一旦有人改回旧写法本用例立刻失败。
    """
    from src.core.screen import open_screen

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with open_screen() as sct:
            assert sct.monitors, "应能取到至少一个显示器条目"


def test_no_deprecated_mss_factory_in_src():
    """静态兜底：src 下任何位置都不得出现 `mss.mss(`。"""
    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "mss.mss(" in line:
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{lineno}")
    assert not offenders, f"发现弃用的 mss 工厂调用: {offenders}"


def test_no_deprecated_qt_attributes_in_src():
    """静态兜底：src 下不得出现已弃用的 Qt ApplicationAttribute。

    `AA_UseHighDpiPixmaps` 在 Qt 6 已弃用——Qt 6 起高 DPI 像素图恒定开启，
    该属性是空操作，连读取枚举值都会触发 DeprecationWarning。
    过去 app.py 曾用它（Python 3.10 默认不显示 DeprecationWarning，一直没被发现）。
    """
    deprecated = ("AA_UseHighDpiPixmaps",)
    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in deprecated:
                # 注释里提到该名字（用于说明"不要用"）不算违规
                if name in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(SRC_ROOT)}:{lineno} -> {name}")
    assert not offenders, f"发现已弃用的 Qt 属性: {offenders}"


def test_no_softblur_downgrade_in_src():
    """静态兜底：不得把移动期的"撤 Acrylic"改成 ACCENT_ENABLE_BLURBEHIND。

    网上（含 FluentWPF 作者本人）推荐"移动期换软模糊"，看似更优，但**本机
    实测不可用**：Win10 19041 上该 accent 在纯白衬底上输出 RGB(255,255,255)
    （不吃 GradientColor，三种 tint 都一样），且随后切回 Acrylic 也不生效——
    用户看到的现象就是"拖动时窗口变纯白"。实测数据见 `src/ui/glass.py` 顶部。
    这个坑钉死在测试里，防止后人照网上的说法"优化"回去。
    """
    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "_ACCENT_ENABLE_BLURBEHIND" not in line:
                continue
            stripped = line.strip()
            # 允许：说明用的注释行、以及常量定义行本身
            if stripped.startswith("#") or stripped.startswith("_ACCENT_ENABLE_BLURBEHIND ="):
                continue
            offenders.append(f"{path.relative_to(SRC_ROOT)}:{lineno}")
    assert not offenders, (
        f"BLURBEHIND 在本机实测会输出纯白且切不回来，不得用于设置 accent: {offenders}"
    )


def test_no_soften_helper_in_glass():
    """静态兜底：`glass.soften()` 这类"软模糊降级"入口不应再出现。"""
    src = (SRC_ROOT / "ui" / "glass.py").read_text(encoding="utf-8")
    assert "def soften" not in src, "软模糊降级已实测不可用，不应保留该入口"


def test_no_per_pixel_screen_read_in_src():
    """静态兜底：src 下不得逐像素读屏幕（GetPixel / SetPixel）。

    屏幕 DC 上的 `GetPixel` 每次调用都会强制同步一次桌面合成，实测**恰好一帧**
    （本机 12.12 ms，中位=最大）。逐像素采一块 3×36 的窄带就是 108 × 12ms ≈ 1.3s，
    两条带 ≈2.6s；整窗更不必说 —— 而且这段通常跑在 GUI 线程上，现象就是"程序卡死"。
    采屏幕一律走 `core.screen.open_screen()` + 一次 `sct.grab()`（分区一次 BitBlt，
    整带 ≈6ms）。过去探针脚本里踩过这个坑，记在这里防止被"更精确的采样"诱惑回去。
    """
    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PER_PIXEL_API.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{lineno}")
    assert not offenders, (
        f"屏幕采样不得逐像素读取（每次 GetPixel ≈ 一帧，会把 GUI 线程钉死）: {offenders}"
    )
