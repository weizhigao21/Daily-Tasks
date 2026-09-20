# -*- coding: utf-8 -*-
"""快捷键设置窗。

打开期间调用方会**停掉全部全局热键**（见 taskbar._show_settings）：录制时按下的
组合键如果还绑定着动作，就会当场触发那个动作——比如把呼出键改到 Ctrl+Alt+Q，
一按就把二维码框选弹了出来，把设置窗压下去。

用模态 `exec()` 是安全的：这个窗口生命周期内不需要被隐藏（不像任务编辑框那样
要在截图前藏起来），也就不会踩到"hide 终止模态循环"那个坑。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import config
from ..core import hotkeys
from ..core.db import TaskDB
from . import theme, window_state
from .glass import GlassDialogMixin
from .hotkey_edit import HotkeyEdit

# (名字, 展示名, settings 键, 默认值, 说明)
_FIELDS = (
    ("toggle", "呼出 / 隐藏面板", config.SETTING_HOTKEY_TOGGLE, config.HOTKEY_TOGGLE,
     "在任何程序里都能把面板叫出来或收回去"),
    ("qr", "识别二维码", config.SETTING_HOTKEY_QR, config.HOTKEY_QR,
     "按下后直接在屏幕上拖一个框，识别框内的二维码并复制内容"),
)


class SettingsDialog(GlassDialogMixin, QDialog):
    """编辑两个全局热键。exec() 后看 `.saved` 判断要不要重新注册。"""

    def __init__(self, db: TaskDB, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.saved = False
        self._edits: dict[str, HotkeyEdit] = {}
        self.setWindowTitle("快捷键设置")
        self.setMinimumWidth(470)
        self.setup_glass(db=db, state_key=window_state.WINDOW_SETTINGS)
        self._build()

    # ---------- UI ----------
    def _build(self) -> None:
        form = QFormLayout()
        form.setSpacing(theme.SP_3)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        for name, title, setting_key, default, tip in _FIELDS:
            form.addRow(title, self._build_row(name, setting_key, default, tip))

        note = QLabel(
            "点击输入框后按下组合键即可录制，Esc 取消。\n"
            "必须包含 Ctrl / Alt / Win 中的至少一个——只用字母或 Shift 会抢走正常打字。\n"
            "热键是「同时响应」而不是独占：若某个组合已被别的程序占用，两个程序会一起反应。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.fs(theme.FS_XS)}px;")

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.ERROR}; font-size: {theme.fs(theme.FS_XS)}px;")
        self.error_label.setVisible(False)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryBtn")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("ghostBtn")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SP_4, theme.SP_4, theme.SP_4, theme.SP_4)
        layout.setSpacing(theme.SP_3)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def _build_row(self, name: str, setting_key: str, default: str, tip: str) -> QHBoxLayout:
        edit = HotkeyEdit(hotkeys.load_combo(self.db, setting_key, default))
        edit.setToolTip(tip)
        edit.captured.connect(lambda _=None: self._show_error(""))
        edit.rejected.connect(self._show_error)
        self._edits[name] = edit

        reset = QPushButton("默认")
        reset.setObjectName("ghostBtn")
        reset.setToolTip(f"恢复为 {hotkeys.label(default)}")
        reset.clicked.connect(lambda _=False, e=edit, d=default: self._restore_default(e, d))

        row = QHBoxLayout()
        row.setSpacing(theme.SP_2)
        row.addWidget(edit, 1)
        row.addWidget(reset)
        return row

    def _restore_default(self, edit: HotkeyEdit, default: str) -> None:
        edit.set_combo(hotkeys.normalize(default) or default)
        self._show_error("")

    # ---------- 反馈 ----------
    def _show_error(self, message: str) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    # ---------- 提交 ----------
    def _on_accept(self) -> None:
        """校验 → 落库。任一项不合法就整窗不保存，避免"改了一半"。"""
        combos: dict[str, str] = {}
        for name, title, _setting_key, _default, _tip in _FIELDS:
            combo = self._edits[name].combo()
            reason = hotkeys.validate(combo)
            if reason:
                self._show_error(f"「{title}」{reason}")
                return
            combos[name] = combo
        if len(set(combos.values())) != len(combos):
            self._show_error("两个功能不能使用同一个组合键，请改掉其中一个。")
            return
        for name, _title, setting_key, _default, _tip in _FIELDS:
            hotkeys.save_combo(self.db, setting_key, combos[name])
        self.saved = True
        self.accept()

    def combos(self) -> dict[str, str]:
        """当前界面上的组合键（测试与调用方读这个，别去摸私有控件）。"""
        return {name: edit.combo() for name, edit in self._edits.items()}
