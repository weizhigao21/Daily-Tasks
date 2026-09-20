# -*- coding: utf-8 -*-
"""自绘图标的回归守卫。

起因：面板右上角三个按钮原先拿符号字符当图标（码位是 U+25A6 / U+25A9 /
U+FF0B / U+22EF）。这些码位落在几何图形区与数学运算符区，主流中文字体
并没有收录它们 —— 真机上渲染成一排空心方块，也就是"豆腐块"。

**这类问题在开发机上不可复现**：字体回退链随系统与字体版本变化，同一个字符
在这台机器上有字形、在用户那台上没有。所以守卫分两层：

1. **静态层**：源码的字符串字面量里不许再出现这些冷门码位；
2. **像素层**：自绘图标必须真的在按钮上画出预期颜色的图形 —— 只断言
   "调用了绘制函数"是不够的，那样画歪了、画到裁剪区外、被 QSS 盖住都测不出来。
"""
import tokenize
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from src.core.models import TASK_DAILY, VERIFY_MANUAL, Task
from src.ui import icons, theme
from src.ui.icons import IconButton

SRC_UI = Path(__file__).resolve().parent.parent / "src" / "ui"

# 真机上会渲染成豆腐块的码位。写成转义序列，免得这份守卫的源码自己去撞自己的规则。
_COLD_SYMBOLS = "\u25a6\u25a9\uff0b\u22ef"


# ---------- 工具 ----------

def _render(kind: str, size: int = 48) -> QImage:
    """按设计网格把某个图标画到已知底色的图上 —— 测的是图形定义本身。"""
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor("#000000"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(size / icons._GRID, size / icons._GRID)
    icons._DRAWERS[kind](p, QColor("#FFFFFF"))
    p.end()
    return img


def _ink_ratio(img: QImage) -> float:
    """非背景像素占比。"""
    bg = QColor("#000000")
    ink = sum(1 for y in range(img.height()) for x in range(img.width())
              if img.pixelColor(x, y) != bg)
    return ink / (img.width() * img.height())


def _count_close(img: QImage, target: str, tol: int = 26) -> int:
    """有多少像素落在目标颜色附近（抗锯齿边缘不做要求，只看笔画核心）。"""
    t = QColor(target)
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if (abs(c.red() - t.red()) <= tol
                    and abs(c.green() - t.green()) <= tol
                    and abs(c.blue() - t.blue()) <= tol):
                n += 1
    return n


@pytest.fixture
def bare_button(qtbot):
    """一个脱开面板样式表的图标按钮，便于做干净的像素断言。"""
    def _make(kind: str, box: int = 28, icon: int = 16):
        btn = IconButton(kind, box=box, icon=icon)
        # 显式压掉 QSS：面板里那条 `#iconBtn` 规则只在 app 级样式表存在时生效，
        # 这里要测的是"图标画没画出来"，别让按钮自身的默认外观混进来。
        btn.setStyleSheet("background: transparent; border: none;")
        qtbot.addWidget(btn)
        btn.show()
        QApplication.processEvents()
        # ⚠️ offscreen 平台的光标常年停在 (10,10)，而无父级的按钮 show() 后正好
        # 落在它下面 → 一显示就处于悬停态，笔画是强调色。这是**测试环境**的产物
        # （真机上鼠标未必在按钮上），但会让像素断言读到意料之外的颜色。
        # 显式发一次 Leave 把基线钉成常态，断言才有确定的前提。
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        return btn
    return _make


# ---------- 静态层：不许再用冷门码位 ----------

def test_ui_string_literals_do_not_use_cold_symbols():
    """UI 源码的字符串字面量里不许出现这些码位（注释与文档不受限）。

    只扫字面量而不是整份文件：注释里**应该**能写下这些字符来说明问题，
    否则下一个人看不懂为什么不能用它们。
    """
    offenders: list[str] = []
    for path in sorted(SRC_UI.glob("*.py")):
        with open(path, encoding="utf-8") as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type != tokenize.STRING:
                    continue
                hits = [c for c in _COLD_SYMBOLS if c in tok.string]
                if hits:
                    shown = "、".join(f"U+{ord(c):04X}" for c in hits)
                    offenders.append(f"{path.name}:{tok.start[0]} 含 {shown}")
    assert not offenders, (
        "这些码位在中文字体里没有字形，真机上会渲染成豆腐块。"
        "图标请用 ui/icons.py 的自绘矢量图：\n  " + "\n  ".join(offenders))


# ---------- 像素层：图标真的画出来了 ----------

@pytest.mark.parametrize("kind", icons.icon_kinds())
def test_icon_kind_draws_a_visible_shape(kind):
    ratio = _ink_ratio(_render(kind))
    assert 0.02 < ratio < 0.6, (
        f"{kind} 的着墨比例 {ratio:.1%} 不合理：过低说明图形没画出来，"
        "过高说明几个图形糊成了一团")


def test_icon_kinds_are_visually_distinct():
    """四个图标必须长得不一样 —— 复制粘贴时忘了改图形，这里就会响。"""
    seen: dict[bytes, str] = {}
    for kind in icons.icon_kinds():
        sig = bytes(_render(kind).constBits())
        # 用 pytest.fail 而不是 assert：直接比对两个 bytes 时，pytest 会把整张
        # 位图打进失败信息里（几百字符的转义串），淹掉真正有用的那一句。
        if sig in seen:
            pytest.fail(f"{kind} 与 {seen[sig]} 画出来一模一样")
        seen[sig] = kind


def test_unknown_icon_kind_fails_at_construction(qtbot):
    """拼错图标名要在构造期就炸，而不是安静地画出一个空按钮。"""
    with pytest.raises(ValueError, match="未知图标"):
        IconButton("qrcode")


def test_icon_button_has_no_text(bare_button):
    """文字恒为空 —— 这正是不该退回"塞个字符"的原因。"""
    assert bare_button("add").text() == ""


@pytest.mark.parametrize("kind", icons.icon_kinds())
def test_icon_button_paints_its_icon(bare_button, kind):
    """按钮上必须真的出现图标颜色的笔画。"""
    btn = bare_button(kind)
    n = _count_close(btn.grab().toImage(), theme.TEXT_SECONDARY)
    assert n >= 8, f"{kind} 按钮上找不到图标颜色的像素（只找到 {n} 个）"


def test_icon_button_turns_accent_on_hover(bare_button):
    """鼠标进入后笔画换成强调色 —— 事件、状态、重绘三者要真的连起来。"""
    btn = bare_button("add")          # fixture 已把基线钉成常态
    assert _count_close(btn.grab().toImage(), theme.TEXT_SECONDARY) >= 8, \
        "前提不成立：常态下就没画出图标"

    QApplication.sendEvent(btn, QEvent(QEvent.Type.Enter))
    assert _count_close(btn.grab().toImage(), theme.ACCENT) >= 8, \
        "悬停后没看到强调色笔画"

    QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
    assert _count_close(btn.grab().toImage(), theme.TEXT_SECONDARY) >= 8, \
        "鼠标移开后没有回到常态色"


# ---------- 接线：面板与卡片确实用的是图标按钮 ----------

def test_panel_header_uses_icon_buttons(taskbar):
    for btn in (taskbar.qr_btn, taskbar.stats_btn):
        assert isinstance(btn, IconButton), \
            f"{btn.objectName()} 不是图标按钮（可能又退回放字符了）"
        assert btn.text() == ""


def test_task_card_menu_button_is_an_icon_button(taskbar, tmp_db):
    tmp_db.add_task(Task(name="随便一个任务", task_type=TASK_DAILY,
                         verify_mode=VERIFY_MANUAL))
    taskbar.refresh_all()
    QApplication.processEvents()
    card = taskbar._rows[list(taskbar._rows)[0]]["card"]
    menu_btn = card.layout().itemAt(4).widget()
    assert isinstance(menu_btn, IconButton)
    assert menu_btn.text() == ""
