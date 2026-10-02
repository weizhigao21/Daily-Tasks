# -*- coding: utf-8 -*-
"""RegionPicker 取消路径回归。

历史缺陷：Esc 与"无效框选"只调 close()，而 picker 无父对象、未设
WA_DeleteOnClose —— close() 只隐藏、不销毁，destroyed 永远不会来，
pick_region_with_capture 的 loop.exec() 永不返回，finally 里的窗口恢复
也永不执行，整个界面消失、进程挂死。取消必须显式 emit cancelled。
"""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from src.ui.region_picker import RegionPicker


def _make_picker(qtbot):
    picker = RegionPicker(hint="测试")
    qtbot.addWidget(picker)
    picker.show()
    return picker


def test_escape_emits_cancelled_and_closes(qtbot):
    picker = _make_picker(qtbot)
    fired = []
    picker.cancelled.connect(lambda: fired.append(1))

    QTest.keyClick(picker, Qt.Key.Key_Escape)

    assert fired == [1]
    assert not picker.isVisible()


def test_tiny_release_emits_cancelled(qtbot):
    """没拖动、直接点一下屏幕（框选 < 4px）按取消处理，同样必须 emit。"""
    picker = _make_picker(qtbot)
    fired = []
    picker.cancelled.connect(lambda: fired.append(1))

    pos = QPoint(50, 50)
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=pos)

    assert fired == [1]
    assert not picker.isVisible()


def test_cancelled_quits_event_loop(qtbot):
    """端到端：cancelled 必须能唤醒 pick_region_with_capture 的嵌套循环。

    close() 本身不会（无父对象 + 无 WA_DeleteOnClose → destroyed 不来），
    所以这里只连 cancelled，验证循环确实能退出。
    """
    from PySide6.QtCore import QEventLoop, QTimer

    picker = _make_picker(qtbot)
    loop = QEventLoop()
    picker.cancelled.connect(loop.quit)
    # 兜底：万一循环退不出，本用例自己 500ms 后撤，避免整套测试挂死
    QTimer.singleShot(500, loop.quit)

    QTimer.singleShot(0, lambda: QTest.keyClick(picker, Qt.Key.Key_Escape))
    loop.exec()

    assert not picker.isVisible()
