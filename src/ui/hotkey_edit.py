# -*- coding: utf-8 -*-
"""热键录制控件：点一下，按下的组合键就被记住。

这是**唯一**把 Qt 按键事件翻译成 pynput 记号的地方（`core/hotkeys.py` 刻意
不依赖 Qt，翻译职责归 UI 层）。

两条不显然但必须的规则：
1. **字母 / 数字从 `Qt.Key` 反推，不能读 `event.text()`** —— 按住 Ctrl 时
   Windows 把字母解释成控制字符，`text()` 拿到的是空串或怪字符。
2. **先按住修饰键不报错**。用户按 Ctrl+Alt+Q 的顺序必然是"Ctrl → Alt → Q"，
   前两下若不声不响地跳过，界面就会先闪一条"这个键不支持"，像是按错了。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit

from ..core import hotkeys

# Qt 特殊键 → 主键记号。只收 core/hotkeys.py 白名单里有的键，两边必须一致。
_QT_SPECIAL = {
    Qt.Key.Key_Space: "<space>",
    Qt.Key.Key_Tab: "<tab>",
    Qt.Key.Key_Return: "<enter>",
    Qt.Key.Key_Enter: "<enter>",
    Qt.Key.Key_Backspace: "<backspace>",
    Qt.Key.Key_Delete: "<delete>",
    Qt.Key.Key_Insert: "<insert>",
    Qt.Key.Key_Home: "<home>",
    Qt.Key.Key_End: "<end>",
    Qt.Key.Key_PageUp: "<page_up>",
    Qt.Key.Key_PageDown: "<page_down>",
    Qt.Key.Key_Up: "<up>",
    Qt.Key.Key_Down: "<down>",
    Qt.Key.Key_Left: "<left>",
    Qt.Key.Key_Right: "<right>",
    Qt.Key.Key_CapsLock: "<caps_lock>",
    Qt.Key.Key_NumLock: "<num_lock>",
    Qt.Key.Key_ScrollLock: "<scroll_lock>",
    Qt.Key.Key_Print: "<print_screen>",
    Qt.Key.Key_Pause: "<pause>",
    Qt.Key.Key_Menu: "<menu>",
    Qt.Key.Key_MediaPlay: "<media_play_pause>",
    Qt.Key.Key_MediaTogglePlayPause: "<media_play_pause>",
    Qt.Key.Key_MediaNext: "<media_next>",
    Qt.Key.Key_MediaPrevious: "<media_previous>",
    Qt.Key.Key_MediaStop: "<media_stop>",
    Qt.Key.Key_VolumeUp: "<media_volume_up>",
    Qt.Key.Key_VolumeDown: "<media_volume_down>",
    Qt.Key.Key_VolumeMute: "<media_volume_mute>",
}

# 按下的只是修饰键本身 —— 录制过程中要静默跳过（理由见模块说明第 2 条）。
# ⚠️ 不要把 CapsLock / NumLock / ScrollLock 放进来：它们是 _QT_SPECIAL 里合法的
# 主键，放进来就等于"永远录不到 Ctrl+CapsLock"（按下去被当修饰键吞掉了）。
_MODIFIER_KEYS = frozenset({
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
})

_RECORDING_PLACEHOLDER = "请按下组合键…（Esc 取消）"


def qt_token(key: int) -> str | None:
    """Qt 按键码 → 组合键里的主键记号；不支持的键返回 None。"""
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(ord("a") + int(key - Qt.Key.Key_A))
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(ord("0") + int(key - Qt.Key.Key_0))
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return f"<f{int(key - Qt.Key.Key_F1) + 1}>"
    return _QT_SPECIAL.get(Qt.Key(key))


def qt_mods(modifiers) -> tuple[str, ...]:
    """Qt 修饰键状态 → core/hotkeys.py 的规范修饰键元组（顺序也照它来）。"""
    pairs = (
        ("ctrl", Qt.KeyboardModifier.ControlModifier),
        ("alt", Qt.KeyboardModifier.AltModifier),
        ("shift", Qt.KeyboardModifier.ShiftModifier),
        ("cmd", Qt.KeyboardModifier.MetaModifier),
    )
    return tuple(name for name, flag in pairs if modifiers & flag)


class HotkeyEdit(QLineEdit):
    """只读输入框形态的热键录制器。

    为什么是输入框而不是按钮：热键的"值"本身就是一串文字，用输入框才能让它
    和旁边的「恢复默认」按钮、说明文字排在同一行的视觉节奏上；同时它天然会
    吃掉键盘事件，录制期间 Tab / 方向键不会漏给对话框去挪焦点。
    """

    captured = Signal(str)      # 录到并已通过校验的组合键（规范形态）
    rejected = Signal(str)      # 录到但不可用，参数是给用户看的原因
    recording_changed = Signal(bool)

    def __init__(self, combo: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 必须能拿到焦点，否则收不到 keyPressEvent
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._recording = False
        self._combo = ""
        self.set_combo(combo)

    # ---------- 取值 ----------
    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str) -> None:
        """设置当前组合键（不触发 captured）。

        **入口即规范化**：这里之后 widget 里存的一定是规范形态，下游
        （落库、以及设置窗里"两个功能不能撞键"的比较）就不必各自再做一次
        等价性判断 —— `<alt>+<ctrl>+t` 与 `<ctrl>+<alt>+t` 是同一个键，
        按字符串比较会漏判成"不冲突"。录制中调用只记值，不改显示。
        """
        self._combo = hotkeys.normalize(combo) or (combo or "")
        if not self._recording:
            self.setText(hotkeys.label(self._combo))

    def is_recording(self) -> bool:
        return self._recording

    # ---------- 录制 ----------
    def start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self.setText("")
        self.setPlaceholderText(_RECORDING_PLACEHOLDER)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.recording_changed.emit(True)

    def stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self.setText(hotkeys.label(self._combo))
        self.recording_changed.emit(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.start_recording()
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        # 焦点走了就退出录制态，否则这个框会一直显示成"正在录制"的空样子
        self.stop_recording()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._recording:
            # 没在录制时，回车 / 空格不该穿透成对话框默认按钮的点击
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                               Qt.Key.Key_Space, Qt.Key.Key_Escape):
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self.stop_recording()
            event.accept()
            return
        if event.key() in _MODIFIER_KEYS:
            event.accept()      # 先按住修饰键是正常流程，静默等着主键
            return
        token = qt_token(event.key())
        if token is None:
            self.rejected.emit("这个键不支持作为热键，请换一个（字母、数字或 F1~F24 等功能键）")
            event.accept()
            return
        combo = hotkeys.Combo(qt_mods(event.modifiers()), token).pynput
        reason = hotkeys.validate(combo)
        if reason:
            # 保持录制态，让用户直接重按一个合格的组合
            self.rejected.emit(reason)
            event.accept()
            return
        self._combo = combo
        self.stop_recording()
        self.captured.emit(combo)
        event.accept()
