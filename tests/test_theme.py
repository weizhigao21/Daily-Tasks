# -*- coding: utf-8 -*-
"""样式表覆盖度。

这里是**回归守卫**，不是外观测试。起因：二维码结果窗的多行文本区底色是纯白、
文字又是白的，整块内容看不见。查下来是两件事凑一起：

1. `app_qss()` 顶部的 `QWidget {{ color: #FFFFFF }}` 是给所有控件刷**纯白**文字的；
2. `QPlainTextEdit` 当时在本项目里**从没被用过**，也就没人给它写过底色 ——
   底色于是落到系统调色板上（浅色系统 = 纯白）。

于是白字白底。这类问题在浅色系统上必现、在深色系统上看不出来，而且只要没人
用过某个控件类就不会暴露，所以必须由测试钉住"用到的控件类要自己声明底色"。
"""
import re
from pathlib import Path

import pytest

from src.ui import theme

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# 会显示用户数据的输入 / 文本区控件：底色必须由样式表明确给出，
# 不能依赖系统调色板。加新控件类时把它补进来。
#
# QHeaderView 是同一类坑的另一种长相：它给 section 设了透明底，但**宿主自己那层**
# 没声明 → section 透明后露的是 QHeaderView 的系统调色板底色（本机实测 #EFEFEF），
# 深色面板上一条白带。"子部件声明了"不等于"宿主声明了"。
_REQUIRE_OWN_BACKGROUND = (
    "QPlainTextEdit",
    "QTextEdit",
    "QLineEdit",
    "QComboBox",
    "QSpinBox",
    "QHeaderView",
)


def _rules(qss: str) -> list[tuple[str, str]]:
    """把 QSS 拆成 (选择器, 声明体) 列表。

    先剥掉 `/* ... */` 注释：正则抓选择器时会把紧邻上方的注释一起吞进来，
    于是 `part == cls` 永远不成立，守卫会变成"永远失败"（跟永远通过一样没用）。

    app_qss() 里没有嵌套块，正则拆得动；匹配不到足够多块时断言失败，
    避免"解析器悄悄失效 → 守卫结论不可信"。
    """
    stripped = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)
    found = re.findall(r"([^{}]+)\{([^{}]*)\}", stripped)
    assert len(found) > 20, "QSS 解析结果太少，解析逻辑可能已失效"
    return [(sel.strip(), body) for sel, body in found]


def _base_rules_for(qss: str, cls: str) -> list[str]:
    """该类**不带伪类**的基础规则体。

    只认选择器里单独出现的类名：`:focus` / `:disabled` 这类状态规则不算数——
    否则只要有个聚焦态的背景色，守卫就会被蒙过去，而常态底色仍然是系统默认的白。
    """
    bodies = []
    for selector, body in _rules(qss):
        for part in selector.split(","):
            if part.strip() == cls:
                bodies.append(body)
    return bodies


def _declares_background(body: str) -> bool:
    """声明体里有没有真正的 `background:` 属性。

    不能直接 `"background" in body`：`selection-background-color` 里也有这个子串，
    那样一条选区色就能把守卫蒙过去（注入验证抓出来过）。
    """
    return re.search(r"(?:^|[;{\s])background\s*:", body) is not None


@pytest.mark.parametrize("cls", _REQUIRE_OWN_BACKGROUND)
def test_data_widgets_declare_their_own_background(cls):
    """这些控件必须有一条自己出面的 background 声明，不能靠系统调色板。"""
    bodies = _base_rules_for(theme.app_qss(), cls)
    assert bodies, f"{cls} 在样式表里没有基础规则（底色会落到系统调色板）"
    assert any(_declares_background(body) for body in bodies), \
        f"{cls} 的基础规则里没有 background —— 浅色系统上会变纯白底"


def test_plain_text_edit_rule_comes_after_the_blanket_widget_rule():
    """顺序即正确性：兜底那条给所有控件刷了纯白文字，类型选择器同权重、后写的赢。

    如果哪天有人把文本区那条挪到 `QWidget {{ color }}` 前面，文字色就会变回由
    兜底规则决定——底色对了、字色又错了，症状从"看不见"变成"显示成别的颜色"，
    照样是坏的。
    """
    qss = theme.app_qss()
    blanket = qss.index("font-family:")            # QWidget 兜底块
    textedit = qss.index("QPlainTextEdit, QTextEdit")
    assert textedit > blanket, "文本区规则必须排在 QWidget 兜底规则之后"


def test_only_one_place_defines_the_dialog_backdrop():
    """底板样式只能有一个来源，别在别处再手写一份色值。"""
    offenders = []
    for path in (SRC_ROOT / "ui").glob("*.py"):
        if path.name == "theme.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"background\s*:\s*#[0-9A-Fa-f]{6}", line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"这些地方手写了背景色，应改走 theme: {offenders}"
