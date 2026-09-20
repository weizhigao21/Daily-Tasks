# -*- coding: utf-8 -*-
"""窗口位置记忆：存取、畸形输入、屏幕外落点夹回。"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget  # noqa: F401  (保留导入便于后续 UI 级测试)

from src.ui import window_state


class _FakeWidget:
    """只暴露 window_state 用到的接口，绕开 offscreen 平台 move() 不生效的问题。"""

    def __init__(self, x: int, y: int, w: int = 400, h: int = 300) -> None:
        self._x, self._y, self._w, self._h = x, y, w, h
        self.moved_to: tuple[int, int] | None = None

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h

    def move(self, x: int, y: int) -> None:
        self.moved_to = (x, y)


def test_pos_roundtrip():
    text = window_state.format_pos(-12, 345)
    assert window_state.parse_pos(text) == (-12, 345)


def test_parse_pos_rejects_malformed():
    """手改过的库 / 老数据里什么都有，畸形输入一律 None，不能崩。"""
    for raw in ("", "   ", "abc", "1;2", "1,2,3", "1,", ",2", "1e3,4", "1 ,2"):
        assert window_state.parse_pos(raw) is None, raw


def test_remember_and_restore_roundtrip(tmp_db):
    w = _FakeWidget(123, 88)
    window_state.remember(tmp_db, window_state.WINDOW_PANEL, w)
    assert tmp_db.get_setting("pos:panel") == "123,88"

    back = _FakeWidget(0, 0)
    assert window_state.restore(tmp_db, window_state.WINDOW_PANEL, back) is True
    assert back.moved_to == (123, 88)


def test_restore_missing_or_malformed_is_noop(tmp_db):
    """没存过 / 存坏了 → 返回 False 且**不许动窗口**（保持原位）。"""
    w = _FakeWidget(50, 60)
    assert window_state.restore(tmp_db, window_state.WINDOW_PANEL, w) is False
    assert w.moved_to is None

    tmp_db.set_setting("pos:panel", "garbage")
    assert window_state.restore(tmp_db, window_state.WINDOW_PANEL, w) is False
    assert w.moved_to is None


def test_restore_without_db_or_name_is_noop(tmp_db):
    w = _FakeWidget(1, 2)
    assert window_state.restore(None, window_state.WINDOW_PANEL, w) is False
    assert window_state.restore(tmp_db, "", w) is False
    assert w.moved_to is None


def test_forget_all_clears_every_window(tmp_db):
    tmp_db.set_setting("pos:panel", "1,2")
    tmp_db.set_setting("pos:task_dialog", "3,4")
    tmp_db.set_setting("pos:stats_panel", "5,6")
    tmp_db.set_setting("pos:other", "7,8")      # 不在名册里的键不该被碰

    window_state.forget_all(tmp_db)

    for name in (window_state.WINDOW_PANEL, window_state.WINDOW_TASK_DIALOG,
                 window_state.WINDOW_STATS_PANEL):
        assert tmp_db.get_setting(f"pos:{name}") == ""
    assert tmp_db.get_setting("pos:other") == "7,8"


def test_clamp_keeps_visible_position(qapp):
    """位置还能看见时，必须原样尊重——不许自作主张"优化"。"""
    screen = qapp.primaryScreen().availableGeometry()
    x = screen.x() + 50
    y = screen.y() + 50
    assert window_state.clamp_to_screens(x, y, 200, 100) == (x, y)


def test_clamp_pulls_offscreen_position_back(qapp):
    """拔掉扩展屏 / 改分辨率后，老坐标整块在屏幕外 → 夹回可见区域。"""
    screen = qapp.primaryScreen().availableGeometry()
    fixed = window_state.clamp_to_screens(
        screen.x() + screen.width() + 5000, screen.y(), 200, 100)
    assert fixed is not None
    fx, fy = fixed
    # 落点必须与屏幕相交，窗口才不会"消失"
    assert screen.right() >= fx
    assert screen.top() <= fy <= screen.bottom()
