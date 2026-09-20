# -*- coding: utf-8 -*-
"""全局热键：组合键的解析 / 校验 / 展示，以及可热重载的注册器。

本模块**不导入 Qt、也不在顶层导入 pynput**：
- 不导入 Qt 是为了解析与校验这些纯逻辑能被离屏测试完整覆盖
  （同 `ui/fullscreen.py` / `ui/occlusion.py` 的约定，有静态守卫）；
- pynput 只在真正 `start()` 时才导入 —— 没装或钩子起不来时，应当只有
  "热键"这一个功能失效，其余照常（与玻璃后端、二维码解码的降级策略一致）。

组合键的存储格式就是 pynput 记法：`<ctrl>+<alt>+t`。放数据库里的值一律先过
`normalize()`，所以库里的形态是唯一的，比较时不必再做等价性判断。

为什么"必须含 Ctrl / Alt / Win"：pynput 走的是**低层键盘钩子**，不是
`RegisterHotKey`——它抢不走别人的组合键，只是"同时响应"。若允许把热键设成
`t` 或 `Shift+A`，用户每次正常打字都会触发动作。用 Ctrl/Alt/Win 打头是
让"这是命令"而非"这是输入"的分界线。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# 修饰键别名 → 规范名。带 _l/_r 的左右分立写法一并归一（用户手写库时常见）。
_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd", "win": "cmd", "super": "cmd",
}
# 输出顺序：固定下来，两次输入 <alt>+<ctrl>+t 与 <ctrl>+<alt>+t 应当等价
_MODS = ("ctrl", "alt", "shift", "cmd")
_MOD_LABEL = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}
# 至少要有其中之一，理由见模块说明
_STRONG_MODS = ("ctrl", "alt", "cmd")

# 可作为"主键"的特殊键白名单，取自 pynput `keyboard.Key` 的成员名（已实测
# `HotKey.parse` 能解析）。**不收标点**：按住 Ctrl 时 Qt 的 `event.text()`
# 拿不到可靠字符，录不出来（见 ui/hotkey_edit.py）。
_SPECIAL_KEYS = frozenset((
    "space", "tab", "enter", "esc", "backspace", "delete", "insert",
    "home", "end", "page_up", "page_down",
    "up", "down", "left", "right",
    "caps_lock", "num_lock", "scroll_lock", "print_screen", "pause", "menu",
    "media_play_pause", "media_next", "media_previous", "media_stop",
    "media_volume_up", "media_volume_down", "media_volume_mute",
)) | frozenset(f"f{i}" for i in range(1, 25))

# 主键里的可打印字符：只收字母与数字（理由同 _SPECIAL_KEYS 上面那条）
_SINGLE_CHAR = re.compile(r"^[a-z0-9]$")

# 系统保留、用户态永远收不到的组合（设了就是个死热键，不如当场拦下）
_RESERVED = frozenset({"<ctrl>+<alt>+<delete>"})

_SPECIAL_LABEL = {
    "space": "Space", "tab": "Tab", "enter": "Enter", "esc": "Esc",
    "backspace": "Backspace", "delete": "Delete", "insert": "Insert",
    "home": "Home", "end": "End", "page_up": "PageUp", "page_down": "PageDown",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "caps_lock": "CapsLock", "num_lock": "NumLock", "scroll_lock": "ScrollLock",
    "print_screen": "PrintScreen", "pause": "Pause", "menu": "Menu",
    "media_play_pause": "播放/暂停", "media_next": "下一曲",
    "media_previous": "上一曲", "media_stop": "停止",
    "media_volume_up": "音量+", "media_volume_down": "音量-",
    "media_volume_mute": "静音",
}


def _key_label(token: str) -> str:
    """主键记号 → 展示用文案。"""
    if token.startswith("<") and token.endswith(">"):
        name = token[1:-1]
        if name.startswith("f") and name[1:].isdigit():
            return name.upper()
        return _SPECIAL_LABEL.get(name, name)
    return token.upper()


@dataclass(frozen=True)
class Combo:
    """解析后的组合键。比较用 `pynput`（规范字符串），展示用 `label`。"""
    mods: tuple[str, ...]
    key: str            # 单字符 "t" / "5"，或 "<f5>" 这类特殊键记号

    @property
    def pynput(self) -> str:
        """规范存储形态，同时就是 pynput 认识的写法。"""
        return "+".join([f"<{m}>" for m in self.mods] + [self.key])

    @property
    def label(self) -> str:
        if not self.mods:               # 解析层不拦"没有修饰键"，展示层别多出前导 "+"
            return _key_label(self.key)
        head = " + ".join(_MOD_LABEL[m] for m in self.mods)
        return f"{head} + {_key_label(self.key)}"


def parse(text: str) -> Combo | None:
    """把组合键文本解析成 Combo；任何不认识的写法返回 None。

    宽松之处：修饰键可以不带尖括号（`ctrl+alt+t` 与 `<ctrl>+<alt>+t` 等价），
    便于容忍手改过的设置值；主键只认 `_SINGLE_CHAR` 与 `_SPECIAL_KEYS`。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    parts = [p.strip().lower() for p in text.split("+")]
    if any(not p for p in parts):       # 空片段 = 写错了（或想用 "+" 当主键）
        return None
    mods: list[str] = []
    key: str | None = None
    for part in parts:
        bare = part[1:-1] if part.startswith("<") and part.endswith(">") else part
        if bare in _ALIASES:
            mod = _ALIASES[bare]
            if mod not in mods:
                mods.append(mod)
            continue
        if key is not None:
            return None                 # 出现了两个主键
        if part.startswith("<") and part.endswith(">"):
            if bare not in _SPECIAL_KEYS:
                return None
            key = f"<{bare}>"
        elif _SINGLE_CHAR.match(part):
            key = part
        else:
            return None
    if key is None:
        return None                     # 只有修饰键，不成组合
    return Combo(tuple(m for m in _MODS if m in mods), key)


def normalize(text: str) -> str | None:
    """规范成可存储的形态；无法解析返回 None。"""
    combo = parse(text)
    return combo.pynput if combo else None


def validate(text: str) -> str:
    """校验可设置性。返回空串表示通过，否则是给用户看的原因。"""
    if not isinstance(text, str) or not text.strip():
        return "请按下要设置的组合键"
    combo = parse(text)
    if combo is None:
        return "无法识别的组合键：主键请用字母、数字、F1~F24 或空格/方向键等功能键"
    if not combo.mods:
        return "必须包含 Ctrl / Alt / Win 中的至少一个修饰键"
    if not any(m in _STRONG_MODS for m in combo.mods):
        return "只用 Shift 会抢走正常输入大写字母，请再加上 Ctrl / Alt / Win"
    if combo.pynput in _RESERVED:
        return "Ctrl + Alt + Del 是系统保留组合，程序收不到"
    return ""


def label(text: str) -> str:
    """展示用文案；解析不了就原样返回（设置界面里宁可显示原文也别显示空白）。"""
    combo = parse(text)
    return combo.label if combo else (text or "")


def load_combo(db, setting_key: str, default: str) -> str:
    """读设置里的热键；缺失或非法一律回退默认值。

    非法值必须回退而不是照用：手改过的库、被别的版本写坏的值，都不该让
    用户的呼出热键凭空消失（那会变成"程序还在跑但怎么按都不出来"）。
    """
    fallback = normalize(default) or default
    try:
        raw = db.get_setting(setting_key, "")
    except Exception:
        return fallback
    return normalize(raw) or fallback


def save_combo(db, setting_key: str, combo: str) -> bool:
    """写入规范形态。非法组合键不落库（返回 False）。"""
    norm = normalize(combo)
    if norm is None:
        return False
    db.set_setting(setting_key, norm)
    return True


class _PynputBackend:
    """真实后端：pynput 的全局键盘钩子。

    重建监听器时先停旧再起新。`GlobalHotKeys` 的构造会对无法解析的组合键抛
    ValueError —— 调用方已经用 `validate()` 过了一遍，这里抛出来即视为注册失败。
    """

    def __init__(self) -> None:
        self._listener = None

    def start(self, handlers: dict[str, Callable[[], None]]) -> None:
        self.stop()
        # 惰性导入，见模块说明
        from pynput import keyboard

        listener = keyboard.GlobalHotKeys(handlers)
        listener.daemon = True
        listener.start()
        self._listener = listener

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass


class HotkeyManager:
    """把若干「组合键 → 回调」注册成全局热键，支持改键后热重载。

    ⚠️ 回调在 **pynput 的监听线程**上触发，不是 GUI 线程。调用方必须自己跳回
    GUI 线程（本项目用 Qt 的 Signal 做队列投递），否则会在非 GUI 线程碰控件。

    后端可注入，便于在拿不到真实键盘钩子的环境里测热重载与失败处理。
    """

    def __init__(self, backend=None) -> None:
        self._backend = backend if backend is not None else _PynputBackend()
        self._bound: dict[str, str] = {}    # 名字 → 规范组合键（当前真的生效的那些）
        self.last_error = ""

    def bindings(self) -> dict[str, str]:
        """当前生效的名字 → 组合键。"""
        return dict(self._bound)

    def apply(self, spec: list[tuple[str, str, Callable[[], None]]]) -> bool:
        """按 (名字, 组合键, 回调) 列表重建热键。返回是否成功，失败原因在 last_error。

        校验失败时**不碰后端**：旧热键继续有效，用户的呼出键不会因为改键改错
        而消失。后端注册失败时旧热键已被停掉，所以 `_bound` 必须清空 ——
        留着它就是在谎报"还绑着什么"，会让设置界面显示与实际不符的键。
        """
        handlers: dict[str, Callable[[], None]] = {}
        bound: dict[str, str] = {}
        for name, combo, callback in spec:
            reason = validate(combo)
            if reason:
                self.last_error = f"{name}：{reason}"
                return False
            norm = normalize(combo)
            assert norm is not None     # validate 通过则必然可规范化
            if norm in handlers:
                self.last_error = "两个功能不能使用同一个组合键"
                return False
            handlers[norm] = callback
            bound[name] = norm
        try:
            self._backend.start(handlers)
        except Exception as exc:
            self._bound = {}
            self.last_error = f"注册全局热键失败：{exc}"
            return False
        self.last_error = ""
        self._bound = bound
        return True

    def stop(self) -> None:
        """全部注销（退出程序、或让位给"正在录制新热键"的设置窗口）。"""
        try:
            self._backend.stop()
        finally:
            self._bound = {}
