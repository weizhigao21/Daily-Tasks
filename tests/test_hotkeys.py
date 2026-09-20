# -*- coding: utf-8 -*-
"""热键：组合键解析 / 校验 / 取值回退，以及注册器的热重载与失败处理。"""
import re
from pathlib import Path

import pytest

from src.core import hotkeys
from src.core.hotkeys import Combo, HotkeyManager, label, normalize, parse, validate

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


class FakeBackend:
    """只记账的假后端。真后端是进程级低层键盘钩子，测试里绝不能碰。"""

    def __init__(self, fail: Exception | None = None):
        self.handlers: dict = {}
        self.starts = 0
        self.stops = 0
        self.fail = fail

    def start(self, handlers):
        self.starts += 1
        if self.fail is not None:
            raise self.fail
        self.handlers = dict(handlers)

    def stop(self):
        self.stops += 1
        self.handlers = {}


# ---------- 解析 ----------

@pytest.mark.parametrize("raw, expected", [
    ("<ctrl>+<alt>+t", "<ctrl>+<alt>+t"),
    ("<CTRL>+<Alt>+T", "<ctrl>+<alt>+t"),          # 大小写不敏感
    ("  <ctrl> + <alt> + t  ", "<ctrl>+<alt>+t"),  # 容忍空格
    ("ctrl+alt+t", "<ctrl>+<alt>+t"),              # 手写的库值可以不带尖括号
    ("<alt>+<ctrl>+t", "<ctrl>+<alt>+t"),          # 顺序无关，输出恒定
    ("<ctrl>+<alt>+<f5>", "<ctrl>+<alt>+<f5>"),
    ("<cmd>+<alt>+1", "<alt>+<cmd>+1"),
    ("<ctrl>+<alt>+<space>", "<ctrl>+<alt>+<space>"),
])
def test_normalize_canonicalizes(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", None, 123,
    "<ctrl>+<alt>",            # 只有修饰键
    "<ctrl>+<alt>+t+u",        # 两个主键
    "<ctrl>+<alt>++",          # 空片段（想拿 "+" 当主键）
    "<ctrl>+<alt>+<nope>",     # 白名单外的特殊键
    "<ctrl>+<alt>+<",          # 残缺的尖括号
    "ctrl+alt+啊",              # 非 ASCII 主键
    "garbage++",
])
def test_parse_rejects_malformed(raw):
    assert parse(raw) is None
    assert normalize(raw) is None


def test_parse_accepts_bare_key_but_validate_will_reject_it():
    """解析层不判"能不能用"：解析是语法，校验才是规则。

    `t` 语法合法（就是字母 t），但它作为全局热键会抢走正常打字 —— 那是
    `validate` 该拦的事。两层分开，设置界面才能给出准确的提示而不是"格式错误"。
    """
    assert parse("t") is not None
    assert parse("t").mods == ()
    assert validate("t") != ""


def test_parse_keeps_modifier_order_stable():
    """输出顺序恒定（Ctrl → Alt → Shift → Win），两次不同的输入才可比。"""
    assert Combo(("ctrl", "alt"), "t").pynput == "<ctrl>+<alt>+t"
    assert parse("<cmd>+<shift>+<f1>").pynput == "<shift>+<cmd>+<f1>"


def test_duplicate_modifier_is_collapsed():
    """左右分立的写法（ctrl_l+ctrl_r）归一后不该重复出现。"""
    assert normalize("<ctrl_l>+<ctrl_r>+<alt>+t") == "<ctrl>+<alt>+t"


# ---------- 校验 ----------

@pytest.mark.parametrize("raw", [
    "<ctrl>+<alt>+t", "<cmd>+<shift>+<f5>", "<alt>+1", "<ctrl>+<alt>+<space>",
])
def test_validate_accepts_usable_combos(raw):
    assert validate(raw) == ""


def test_validate_rejects_blank():
    assert "按下" in validate("")


def test_validate_demands_a_strong_modifier():
    """只用字母会抢走正常打字 —— 这是把用户自己坑了，必须拦下。"""
    assert "修饰键" in validate("t")


def test_validate_rejects_shift_only():
    """Shift+字母 就是打大写字母，同样属于"抢走正常输入"。"""
    assert "Shift" in validate("<shift>+a")


def test_validate_rejects_windows_reserved_combo():
    """Ctrl+Alt+Del 是系统保留，设了也收不到，不如当场说清楚。"""
    assert "保留" in validate("<ctrl>+<alt>+<delete>")


def test_validate_rejects_unparseable():
    assert validate("<ctrl>+<alt>+<nope>") != ""


# ---------- 展示 ----------

@pytest.mark.parametrize("raw, expected", [
    ("<ctrl>+<alt>+t", "Ctrl + Alt + T"),
    ("<cmd>+<shift>+<f5>", "Shift + Win + F5"),
    ("<ctrl>+<alt>+<page_up>", "Ctrl + Alt + PageUp"),
    ("<alt>+<left>", "Alt + ←"),
    ("<ctrl>+<alt>+5", "Ctrl + Alt + 5"),
])
def test_label_is_human_readable(raw, expected):
    assert label(raw) == expected


def test_label_without_modifiers_has_no_dangling_separator():
    """解析层不拦"没有修饰键"，展示层也不能拼出前导的 ' + '。"""
    assert label("t") == "T"


def test_label_falls_back_to_raw_text():
    assert label("<ctrl>+<alt>+<nope>") == "<ctrl>+<alt>+<nope>"
    assert label("") == ""


# ---------- 取值：非法值必须回退 ----------

def test_load_combo_uses_default_when_absent(tmp_db):
    from src import config

    assert hotkeys.load_combo(tmp_db, "hotkey_qr", config.HOTKEY_QR) \
        == config.HOTKEY_QR


def test_load_combo_normalizes_stored_value(tmp_db):
    tmp_db.set_setting("hotkey_qr", "<ALT>+<CTRL>+K")
    assert hotkeys.load_combo(tmp_db, "hotkey_qr", "<ctrl>+<alt>+q") \
        == "<ctrl>+<alt>+k"


def test_load_combo_falls_back_on_corrupt_value(tmp_db):
    """手改坏的库不该让呼出热键凭空消失（那会变成"程序在跑但按不出来"）。"""
    tmp_db.set_setting("hotkey_toggle", "不是热键")
    assert hotkeys.load_combo(tmp_db, "hotkey_toggle", "<ctrl>+<alt>+t") \
        == "<ctrl>+<alt>+t"


def test_load_combo_swallows_db_errors():
    class _Bad:
        def get_setting(self, *_a, **_k):
            raise RuntimeError("库炸了")

    assert hotkeys.load_combo(_Bad(), "k", "<ctrl>+<alt>+t") == "<ctrl>+<alt>+t"


def test_save_combo_stores_canonical_form(tmp_db):
    assert hotkeys.save_combo(tmp_db, "hotkey_qr", "<ALT>+<CTRL>+K")
    assert tmp_db.get_setting("hotkey_qr") == "<ctrl>+<alt>+k"


def test_save_combo_refuses_invalid(tmp_db):
    assert not hotkeys.save_combo(tmp_db, "hotkey_qr", "垃圾")
    assert tmp_db.get_setting("hotkey_qr") == ""


# ---------- 注册器 ----------

def test_apply_registers_normalized_handlers():
    backend = FakeBackend()
    manager = HotkeyManager(backend=backend)
    calls = []
    assert manager.apply([
        ("呼出", "<ALT>+<CTRL>+T", lambda: calls.append("toggle")),
        ("扫码", "<ctrl>+<alt>+q", lambda: calls.append("qr")),
    ])
    assert set(backend.handlers) == {"<ctrl>+<alt>+t", "<ctrl>+<alt>+q"}
    assert manager.last_error == ""
    assert manager.bindings() == {"呼出": "<ctrl>+<alt>+t", "扫码": "<ctrl>+<alt>+q"}
    backend.handlers["<ctrl>+<alt>+q"]()
    assert calls == ["qr"]


def test_apply_rejects_duplicate_combo():
    """两个功能抢同一个键，必须整批失败 —— 只报一半会让用户以为生效了。"""
    backend = FakeBackend()
    manager = HotkeyManager(backend=backend)
    assert not manager.apply([
        ("呼出", "<ctrl>+<alt>+t", lambda: None),
        ("扫码", "<ctrl>+<alt>+t", lambda: None),
    ])
    assert "同一个组合键" in manager.last_error
    assert backend.starts == 0


def test_apply_with_invalid_combo_keeps_old_binding_alive():
    """改键填错时旧热键必须还在：否则用户的呼出键会因为改错而彻底消失。"""
    backend = FakeBackend()
    manager = HotkeyManager(backend=backend)
    assert manager.apply([("呼出", "<ctrl>+<alt>+t", lambda: None)])
    assert not manager.apply([("呼出", "t", lambda: None)])
    assert set(backend.handlers) == {"<ctrl>+<alt>+t"}
    assert manager.bindings() == {"呼出": "<ctrl>+<alt>+t"}
    assert "呼出" in manager.last_error


def test_apply_backend_failure_clears_bindings():
    """后端注册失败时旧钩子已经被停掉，bindings 必须跟着清空。

    留着它就是在谎报"还绑着什么"——设置界面会显示一个按下去没反应的键。
    """
    backend = FakeBackend()
    manager = HotkeyManager(backend=backend)
    assert manager.apply([("呼出", "<ctrl>+<alt>+t", lambda: None)])

    backend.fail = RuntimeError("钩子起不来")
    assert not manager.apply([("呼出", "<ctrl>+<alt>+k", lambda: None)])
    assert manager.bindings() == {}
    assert "钩子起不来" in manager.last_error


def test_apply_can_be_called_repeatedly():
    """改键就是重复 apply；每轮都换掉上一轮，不该累积。"""
    backend = FakeBackend()
    manager = HotkeyManager(backend=backend)
    for combo in ("<ctrl>+<alt>+t", "<ctrl>+<alt>+k", "<cmd>+<alt>+p"):
        assert manager.apply([("呼出", combo, lambda: None)])
        assert set(backend.handlers) == {normalize(combo)}
    assert backend.starts == 3


def test_stop_clears_state():
    backend = FakeBackend()
    manager = HotkeyManager(backend=backend)
    manager.apply([("呼出", "<ctrl>+<alt>+t", lambda: None)])
    manager.stop()
    assert manager.bindings() == {}
    assert backend.handlers == {}


def test_default_backend_does_not_touch_the_real_keyboard_until_started():
    """构造默认管理器不该立刻装上全局钩子（真装钩子只发生在 start 里）。"""
    manager = HotkeyManager()
    assert manager.bindings() == {}


def test_tests_never_install_a_real_keyboard_hook(taskbar):
    """兜底守卫：测试里构造面板用的必须是假后端。

    真后端是**进程级低层键盘钩子**，而 taskbar 这个 fixture 被几十条用例共用 ——
    漏了 conftest 里那道 autouse 替换，整轮测试就会一层层叠加真钩子：既拖慢执行，
    也会让"热键到底注册上没有"永远无法断言（真钩子只会静默成功）。
    """
    assert type(taskbar._hotkeys._backend).__name__ == "_NoHookBackend"


# ---------- 与 pynput 的一致性 ----------

def test_every_whitelisted_special_key_is_parseable_by_pynput():
    """白名单必须与 pynput 认识的键名一致。

    这份白名单是手抄 pynput `keyboard.Key` 成员名来的（为了不让解析逻辑依赖
    pynput），而"手抄的清单迟早会漂"—— pynput 换了成员名、或者我们抄错了，
    表现就变成"录进去的热键根本没反应"，且只看代码看不出来。这里逐条对拍。
    """
    keyboard = pytest.importorskip("pynput.keyboard")
    broken = []
    for name in sorted(hotkeys._SPECIAL_KEYS):
        combo = f"<ctrl>+<alt>+<{name}>"
        try:
            keyboard.HotKey.parse(combo)
        except Exception as exc:                     # noqa: BLE001 - 要的就是全部
            broken.append(f"{combo} -> {type(exc).__name__}: {exc}")
    assert not broken, f"白名单里有 pynput 解析不了的键: {broken}"


def test_every_whitelisted_special_key_has_a_label():
    """每个能录的键都要能显示成人话，否则设置界面会出现空白框。"""
    missing = [n for n in hotkeys._SPECIAL_KEYS
               if label(f"<ctrl>+<{n}>") in ("", "Ctrl + ")]
    assert not missing, f"这些键没有展示文案: {missing}"


def test_format_whitelist_matches_pynput_key_members():
    """反向检查：pynput 有而白名单里没有的"常用键"值得想一想是不是漏了。

    只检查字母数字与编辑类键，媒体键/数字小键盘锁这类故意不收的不算。
    """
    keyboard = pytest.importorskip("pynput.keyboard")
    expected_present = {"space", "tab", "enter", "esc", "backspace", "delete",
                        "home", "end", "page_up", "page_down",
                        "up", "down", "left", "right"}
    members = set(keyboard.Key.__members__)
    assert expected_present <= members, "pynput 的键名变了，白名单该跟着更新"
    assert expected_present <= hotkeys._SPECIAL_KEYS


# ---------- 静态守卫 ----------

def test_hotkeys_module_has_no_qt_no_pynput_at_module_level():
    """本模块必须保持 Qt-free / pynput-free 的可导入性。

    Qt-free：解析与校验是纯逻辑，要能被离屏完整覆盖。
    pynput-free：没装 pynput 时"热键"这一个功能降级，其余照常。
    """
    src = (SRC_ROOT / "core" / "hotkeys.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert "class HotkeyManager" in src, "文件结构变了，守卫需要复核"
    # 顶层不得出现 pynput / Qt 的 import（函数内的惰性导入是允许的）
    for line in code.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        assert not re.match(r"^import pynput\b", stripped), "pynput 必须惰性导入"
        assert "PySide6" not in stripped, "core 层不得引入 Qt"
