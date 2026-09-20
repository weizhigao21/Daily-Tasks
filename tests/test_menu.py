# -*- coding: utf-8 -*-
"""弹窗菜单（`ui.menu.GlassMenu`）的回归守卫。

起因：托盘右键菜单是**独立顶层窗口**，不在主面板那棵样式树里，一直没人给它
套样式 —— 弹出来是系统默认的浅色（探针实测 `#F2F2F2` 底 + `#CCCCCC` 边框），
跟暗色主 UI 摆在一起非常扎眼。现在统一走 `theme.menu_qss()` + `GlassMenu`，
并在每次弹出时把主面板那套原生 Acrylic 挂上去。

这里钉住的是三件**容易在重构里悄悄坏掉**的事：
1. 两态样式表除了底板色一模一样（否则原生模糊解析成功的那一帧菜单会跳尺寸）；
2. 原生模糊**每次弹出都要重挂**（Qt 的 Popup 窗口隐藏即销毁，下次是新 HWND，
   子菜单又是另一个窗口 —— 拿"已经做过"当缓存就会漏）；
3. 托盘菜单与卡片菜单都真的用上了 GlassMenu。
"""
import pytest

from src.ui import theme
from src.ui.menu import GlassMenu

MENU = "src.ui.menu"


@pytest.fixture
def fake_glass(monkeypatch):
    """假玻璃后端：只记账 + 返回可控结果，不碰真实窗口合成。

    offscreen 平台拿不到真 HWND，真调用必然失败 —— 不注入的话所有用例都只能
    测出"永远降级"这一条分支，等于没测。返回 (调用记录, 结果开关)。
    """
    import src.ui.menu as menu_mod

    calls = []
    state = {"result": True}

    def _apply(hwnd):
        calls.append(hwnd)
        return state["result"]

    monkeypatch.setattr(menu_mod, "_apply_glass", _apply)
    return calls, state


# ---------- 样式表 ----------

def test_glass_and_opaque_stylesheets_differ_only_in_the_backdrop():
    """两态只该差底板那一行。

    原生模糊是在菜单**已经显示出来之后**才解析出来的（要拿到 HWND），解析成功
    会立刻换样式表。若两态的 padding / border / 字号有任何差异，菜单就会在弹出
    的瞬间改变尺寸和布局 —— 肉眼看到的是"菜单跳了一下"。
    """
    glass = theme.menu_qss("glass").splitlines()
    opaque = theme.menu_qss("opaque").splitlines()
    assert len(glass) == len(opaque)
    diff = [(a, b) for a, b in zip(glass, opaque, strict=True) if a != b]
    assert len(diff) == 1, f"两态差了不止一行: {diff}"
    assert "background" in diff[0][0], f"唯一的差异应该是底板色: {diff[0]}"


def test_glass_backdrop_is_transparent_and_falls_back_to_the_menu_colour():
    """玻璃态底板透明（透出系统画的磨砂），降级态用主题里的菜单色。"""
    assert "background: transparent;" in theme.menu_qss("glass")
    assert f"background: {theme.BG_MENU};" in theme.menu_qss("opaque")
    # 圆角描边两态都在：Win10 的 Acrylic 是整块矩形模糊、不吃窗口区域，
    # 四角本来就是方的（主面板同样如此），那圈描边是"同款"的一部分。
    for mode in ("glass", "opaque"):
        qss = theme.menu_qss(mode)
        assert f"border: 1px solid {theme.STROKE};" in qss
        assert f"border-radius: {theme.R_CARD}px;" in qss


def test_the_menu_stylesheet_includes_the_disabled_item_rule():
    """置灰项要自己声明颜色。

    QSS 一接管菜单，禁用态就不走系统画法了，而兜底那条 `QWidget {{ color }}`
    是**纯白** —— 置灰项会跟可点项长得一模一样（探针抓图确认过：加规则前
    「清除保存的窗口位置」与「退出」同样亮）。托盘里「识别到网址显示「打开
    链接」」在直开开启时正是被置灰的，看不出来就等于这个开关读不出状态。
    """
    qss = theme.menu_qss("glass")
    assert "QMenu::item:disabled" in qss
    body = qss.split("QMenu::item:disabled")[1].split("}")[0]
    assert theme.TEXT_MUTED in body, "置灰项应当用弱化文字色"
    assert theme.TEXT_PRIMARY not in body


def test_menu_colour_has_a_single_source():
    """菜单底色只能来自 theme.BG_MENU，别处不许再手写色值。"""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "ui"
    offenders = []
    for path in src.glob("*.py"):
        if path.name == "theme.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if theme.BG_MENU.lstrip("#").lower() in line.lower():
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"手写了菜单底色，应改走 theme.BG_MENU: {offenders}"


# ---------- 玻璃态解析 ----------

def test_menu_starts_opaque_so_the_first_frame_is_readable(qtbot):
    """还没拿到 HWND 之前必须是降级态：否则首帧就是"透明 + 背后什么都没有"。"""
    menu = GlassMenu()
    qtbot.addWidget(menu)
    assert menu._glass_mode == "opaque"
    assert menu.styleSheet() == theme.menu_qss("opaque")


def test_menu_goes_transparent_when_native_blur_is_available(qtbot, fake_glass):
    calls, _ = fake_glass
    menu = GlassMenu()
    qtbot.addWidget(menu)
    menu.show()
    assert menu._glass_mode == "glass"
    assert menu.styleSheet() == theme.menu_qss("glass")
    assert calls, "显示时应当去挂一次原生模糊"


def test_menu_stays_opaque_when_native_blur_is_unavailable(qtbot, fake_glass):
    calls, state = fake_glass
    state["result"] = False
    menu = GlassMenu()
    qtbot.addWidget(menu)
    menu.show()
    assert calls, "不管成不成，都得真的去试一次"
    assert menu._glass_mode == "opaque"
    assert menu.styleSheet() == theme.menu_qss("opaque")


def test_every_popup_rewraps_the_native_blur(qtbot, fake_glass):
    """每次弹出都要重挂 —— 不能拿"这个菜单已经挂过"当缓存。

    Qt 的 Popup 窗口在隐藏时会被销毁，再显示是**新的 HWND**；而句柄值有可能
    被系统回收复用，"新窗口 + 旧句柄值"会被缓存误判成"已经挂过了"，菜单就此
    永远停在降级态（不透明）而不自知。
    """
    calls, _ = fake_glass
    menu = GlassMenu()
    qtbot.addWidget(menu)
    for _ in range(3):
        menu.show()
        menu.hide()
    assert len(calls) == 3, f"三次弹出应挂三次，实际 {len(calls)} 次"
    assert menu._glass_mode == "glass"


def test_a_later_popup_that_fails_falls_back_to_opaque(qtbot, fake_glass):
    """系统背景突然挂不上（换显卡 / 远程桌面）时要能退回降级态，别卡在透明上。"""
    _, state = fake_glass
    menu = GlassMenu()
    qtbot.addWidget(menu)
    menu.show()
    assert menu._glass_mode == "glass"
    menu.hide()
    state["result"] = False
    menu.show()
    assert menu._glass_mode == "opaque", "解析失败必须退回不透明底板"


def test_stylesheet_is_not_rewritten_when_the_mode_is_unchanged(qtbot, fake_glass):
    """幂等：模式没变就不该再 setStyleSheet（那会重新 polish 整棵子树）。"""
    _, _state = fake_glass
    menu = GlassMenu()
    qtbot.addWidget(menu)
    writes = []
    original = menu.setStyleSheet

    def spy(qss):
        writes.append(qss)
        original(qss)

    menu.setStyleSheet = spy
    for _ in range(3):
        menu.show()
        menu.hide()
    assert len(writes) == 1, f"只有第一次解析需要换样式表，实际换了 {len(writes)} 次"


# ---------- 接线 ----------

def test_tray_menus_are_glass_menus(taskbar):
    """托盘根菜单**和**子菜单都要是 GlassMenu。

    子菜单是另一个独立弹窗（自己的 HWND），用 `addMenu("二维码")` 建出来的是
    普通 QMenu —— 弹出来会是不透明的另一套观感，跟根菜单对不上。
    """
    root = taskbar.tray.contextMenu()
    assert isinstance(root, GlassMenu), "托盘根菜单没走 GlassMenu（会露出系统浅色）"
    assert isinstance(taskbar.qr_menu, GlassMenu), "二维码子菜单没走 GlassMenu"
    assert taskbar.qr_menu.parent() is root


def test_no_module_constructs_a_bare_qmenu():
    """UI 层不许直接 `QMenu(...)`：普通 QMenu 弹出来就是系统浅色。

    这条是**静态守卫**，不是不想做行为测试 —— 行为测试在这里有坑：`QMenu.exec`
    是模态事件循环，而 PySide6 把 `exec` 实现成从 C++ 类型解析的槽，**给基类
    QMenu 打 monkeypatch 不会生效**（`m.exec` 仍是 `<built-in method>`），
    只有给 Python 子类（GlassMenu）打才拦得住。也就是说"退回普通 QMenu"这个
    回归一旦发生，行为测试会**挂死**而不是失败（注入验证时实测挂满 4 分钟）。
    扫源码既跑不挂，又能顺手管住以后新加的菜单。

    卡片菜单同理走 `GlassMenu`（`_row_menu`），托盘根菜单与子菜单各是一个独立
    弹窗，都得挂玻璃。
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "ui"
    offenders = []
    for path in src.glob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # `menu.addMenu(...)` 不算；只认构造调用 `QMenu(`
            if re.search(r"(?<![\w.])QMenu\s*\(", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "这些地方直接建了普通 QMenu（弹出来是系统浅色）: " + str(offenders)
