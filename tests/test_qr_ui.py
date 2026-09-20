# -*- coding: utf-8 -*-
"""二维码识别的 UI 侧：热键录制控件、设置窗、结果窗，以及面板的接线。"""
import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from src import config
from src.core import hotkeys
from src.core.qrdecode import QrHit
from src.ui.hotkey_edit import HotkeyEdit, qt_mods, qt_token
from src.ui.qr_result import (
    QrResultDialog,
    is_openable_url,
    open_url_in_browser,
)
from src.ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def _no_modal_popups(monkeypatch):
    """屏蔽模态弹窗，防止 offscreen 环境下挂起。"""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


# ---------- 按键翻译 ----------

def test_qt_token_maps_letters_digits_and_function_keys():
    assert qt_token(Qt.Key.Key_A) == "a"
    assert qt_token(Qt.Key.Key_Z) == "z"
    assert qt_token(Qt.Key.Key_0) == "0"
    assert qt_token(Qt.Key.Key_9) == "9"
    assert qt_token(Qt.Key.Key_F1) == "<f1>"
    assert qt_token(Qt.Key.Key_F24) == "<f24>"
    assert qt_token(Qt.Key.Key_Space) == "<space>"
    assert qt_token(Qt.Key.Key_PageUp) == "<page_up>"


def test_qt_token_ignores_keys_outside_the_whitelist():
    """标点是故意不支持的：按住 Ctrl 时 Qt 给不出可靠的字符，录不出来。"""
    assert qt_token(Qt.Key.Key_Comma) is None
    assert qt_token(Qt.Key.Key_Control) is None
    assert qt_token(Qt.Key.Key_Meta) is None


def test_every_mapped_special_key_is_accepted_by_core():
    """UI 的映射表与 core 的白名单必须严格对齐。

    两边各存一份"什么键能用"是必要的（一边认 Qt 枚举、一边认 pynput 键名），
    但对不齐的表现是"按下去没反应"或"录完立刻报错"，很难从代码上看出来。
    """
    from src.ui.hotkey_edit import _QT_SPECIAL

    for qt_key, token in _QT_SPECIAL.items():
        assert token[1:-1] in hotkeys._SPECIAL_KEYS, f"{qt_key} 映射到白名单外的 {token}"
        assert hotkeys.validate(f"<ctrl>+{token}") == ""


def test_qt_mods_order_matches_core():
    mods = qt_mods(Qt.KeyboardModifier.AltModifier
                   | Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.MetaModifier)
    assert mods == ("ctrl", "alt", "cmd")


# ---------- 录制控件 ----------

@pytest.fixture
def edit(qtbot):
    widget = HotkeyEdit("<ctrl>+<alt>+q")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_edit_shows_human_readable_combo(edit):
    assert edit.combo() == "<ctrl>+<alt>+q"
    assert edit.text() == "Ctrl + Alt + Q"


def test_recording_captures_combo(edit, qtbot):
    seen = []
    edit.captured.connect(seen.append)
    edit.start_recording()
    assert edit.is_recording()
    qtbot.keyClick(edit, Qt.Key.Key_K,
                   modifier=Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.ShiftModifier)
    assert seen == ["<ctrl>+<shift>+k"]
    assert not edit.is_recording()
    assert edit.text() == "Ctrl + Shift + K"


def test_modifier_keys_alone_do_not_report_a_rejection(edit, qtbot):
    """按 Ctrl+Alt+Q 的顺序必然是 Ctrl → Alt → Q。

    前两下若不声不响地跳过，用户会先看到一条"这个键不支持"的报错闪出来，
    像是按错了。这里钉死"按住修饰键是正常流程"。
    """
    rejected = []
    edit.rejected.connect(rejected.append)
    edit.start_recording()
    qtbot.keyClick(edit, Qt.Key.Key_Control, modifier=Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(edit, Qt.Key.Key_Alt,
                   modifier=Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.AltModifier)
    assert rejected == []
    assert edit.is_recording()


def test_escape_cancels_recording_and_keeps_old_combo(edit, qtbot):
    edit.start_recording()
    qtbot.keyClick(edit, Qt.Key.Key_Escape)
    assert not edit.is_recording()
    assert edit.combo() == "<ctrl>+<alt>+q"
    assert edit.text() == "Ctrl + Alt + Q"


def test_rejects_shift_only_combo(edit, qtbot):
    rejected = []
    edit.rejected.connect(rejected.append)
    edit.start_recording()
    qtbot.keyClick(edit, Qt.Key.Key_A, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert rejected and "Shift" in rejected[0]
    assert edit.is_recording()          # 留在录制态，直接重按即可
    assert edit.combo() == "<ctrl>+<alt>+q"


def test_rejects_whitelist_external_key(edit, qtbot):
    rejected = []
    edit.rejected.connect(rejected.append)
    edit.start_recording()
    qtbot.keyClick(edit, Qt.Key.Key_Comma,
                   modifier=Qt.KeyboardModifier.ControlModifier)
    assert rejected and "不支持" in rejected[0]


def test_focus_loss_ends_recording(edit):
    """焦点走了还挂着"正在录制"的空框，用户会以为它卡住了。

    直接投递 FocusOut 事件，不走 `setFocus()`：offscreen 平台下窗口拿不到
    真正的激活状态，焦点转移常常不生效 —— 那样测出来的是平台行为，不是逻辑。
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    edit.start_recording()
    QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut))
    assert not edit.is_recording()
    assert edit.text() == "Ctrl + Alt + Q"


def test_space_and_enter_do_not_reach_the_default_button(qtbot):
    """没在录制时，回车 / 空格不该穿透成对话框默认按钮的点击。

    设置窗里这几个框旁边就是「保存」，误按一次就把窗口提交了。
    """
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

    dlg = QDialog()
    qtbot.addWidget(dlg)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
    # ⚠️ 用 parent 构造，不要事后 addWidget 改父级：qtbot 会按注册时的对象
    # 在 teardown 里 close()，被改了父级的控件可能已经随之销毁 → 报"对象已删除"。
    edit = HotkeyEdit("<ctrl>+<alt>+q", dlg)
    layout = QVBoxLayout(dlg)
    layout.addWidget(edit)
    layout.addWidget(buttons)
    dlg.show()
    accepted = []
    buttons.accepted.connect(lambda: accepted.append(1))
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        edit.setFocus()
        qtbot.keyClick(edit, key)
    assert accepted == []


# ---------- 设置窗 ----------

@pytest.fixture
def settings(tmp_db, qtbot):
    dlg = SettingsDialog(tmp_db)
    qtbot.addWidget(dlg)
    return dlg


def test_settings_loads_current_values(settings, tmp_db):
    assert settings.combos() == {
        "toggle": config.HOTKEY_TOGGLE,
        "qr": config.HOTKEY_QR,
    }


def test_settings_loads_customized_values(settings, tmp_db):
    tmp_db.set_setting(config.SETTING_HOTKEY_QR, "<ctrl>+<shift>+j")
    assert SettingsDialog(tmp_db).combos()["qr"] == "<ctrl>+<shift>+j"


def test_settings_saves_both_combos(settings, tmp_db):
    settings._edits["qr"].set_combo("<cmd>+<alt>+j")
    settings._on_accept()
    assert settings.saved
    # 落库的是**规范形态**（修饰键顺序恒定），不是用户/调用方随手写的拼法
    assert tmp_db.get_setting(config.SETTING_HOTKEY_QR) == "<alt>+<cmd>+j"
    assert tmp_db.get_setting(config.SETTING_HOTKEY_TOGGLE) == config.HOTKEY_TOGGLE


def test_settings_duplicate_check_ignores_modifier_order(settings, tmp_db):
    """同一组合的不同拼法必须被判成冲突，否则会存下两个等价热键。"""
    settings._edits["qr"].set_combo("<alt>+<ctrl>+t")     # 与呼出键等价
    settings._on_accept()
    assert not settings.saved
    assert "同一个组合键" in settings.error_label.text()


def test_settings_rejects_duplicate_combos(settings, tmp_db):
    """两个功能抢同一个键必须整窗拦下，且一个字都不能落库。"""
    settings._edits["qr"].set_combo("<ctrl>+<alt>+t")
    settings._on_accept()
    assert not settings.saved
    assert "同一个组合键" in settings.error_label.text()
    assert tmp_db.get_setting(config.SETTING_HOTKEY_QR) == ""


def test_settings_rejects_invalid_combo(settings, tmp_db):
    settings._edits["toggle"].set_combo("t")
    settings._on_accept()
    assert not settings.saved
    assert "呼出" in settings.error_label.text()
    assert tmp_db.get_setting(config.SETTING_HOTKEY_TOGGLE) == ""


def test_settings_restore_default_clears_error(settings):
    settings._show_error("出错了")
    assert settings.error_label.isVisible() or settings.error_label.text()
    settings._restore_default(settings._edits["qr"], config.HOTKEY_QR)
    assert settings.error_label.text() == ""
    assert settings.combos()["qr"] == config.HOTKEY_QR


# ---------- 结果窗 ----------

def test_is_openable_url():
    assert is_openable_url("https://a.com/x")
    assert is_openable_url("http://a.com")
    assert is_openable_url("www.a.com/x?y=1")
    assert not is_openable_url("这是一段普通文本")
    assert not is_openable_url("文本里提到 http://a.com 但整体不是网址")
    assert not is_openable_url("")


@pytest.fixture
def result(qtbot):
    def _make(hits, **kw):
        dlg = QrResultDialog([QrHit(t) for t in hits], **kw)
        qtbot.addWidget(dlg)
        # 必须真的 show()：子控件的 isVisible() 要求整条父链都可见，
        # 光 setVisible(True) 在未显示的对话框上查出来永远是 False。
        dlg.show()
        return dlg
    return _make


def test_result_copies_to_clipboard_on_open(result):
    """扫完十有八九是要去别处粘贴，所以默认就复制好。"""
    dlg = result(["https://example.com/abc"])
    assert QApplication.clipboard().text() == "https://example.com/abc"
    assert "已复制" in dlg.status.text()


def test_result_shows_text_and_open_button_for_url(result):
    dlg = result(["https://example.com/abc"])
    assert dlg.text.toPlainText() == "https://example.com/abc"
    assert dlg.open_btn.isVisible()
    assert not dlg.chooser.isVisible()      # 只有一个码就不给选择器


def test_result_hides_open_button_for_plain_text(result):
    dlg = result(["一段普通文本"])
    assert not dlg.open_btn.isVisible()


def test_result_switching_multi_hits_updates_clipboard(result):
    dlg = result(["AAA", "https://bbb.com"])
    assert dlg.chooser.isVisible()
    assert QApplication.clipboard().text() == "AAA"
    dlg.chooser.setCurrentIndex(1)
    assert dlg.text.toPlainText() == "https://bbb.com"
    assert QApplication.clipboard().text() == "https://bbb.com"
    assert dlg.current().text == "https://bbb.com"


def test_result_opens_link_in_browser(result, monkeypatch):
    opened = []
    monkeypatch.setattr("src.ui.qr_result.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))
    dlg = result(["www.example.com/x"])
    dlg._open_link()
    assert opened == ["https://www.example.com/x"], "www. 开头必须补上协议"


def test_result_does_not_open_plain_text(result, monkeypatch):
    opened = []
    monkeypatch.setattr("src.ui.qr_result.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url)))
    dlg = result(["普通文本"])
    dlg._open_link()
    assert opened == []


# ---------- 结果窗：两个行为开关 ----------

def test_result_auto_copies_by_default(result):
    """默认必须还是"扫完就能直接粘贴"——开关只在显式关掉时改变行为。"""
    QApplication.clipboard().setText("")
    dlg = result(["https://example.com/abc"])
    assert QApplication.clipboard().text() == "https://example.com/abc"
    assert "已复制" in dlg.status.text()


def test_result_does_not_copy_when_autocopy_disabled(result):
    """关掉自动复制后，扫得再顺也不许覆盖剪贴板里原本的东西。"""
    QApplication.clipboard().setText("我本来在剪贴板里的内容")
    dlg = result(["https://example.com/abc"], autocopy=False)
    assert QApplication.clipboard().text() == "我本来在剪贴板里的内容"
    # 状态行不能照抄"已复制"，那是在骗用户
    assert "已复制" not in dlg.status.text()
    assert "未自动复制" in dlg.status.text()
    # 手动点复制是唯一写入途径，且要有反馈
    dlg.copy_btn.click()
    assert QApplication.clipboard().text() == "https://example.com/abc"
    assert "已复制" in dlg.status.text()


def test_result_switching_multi_hits_does_not_copy_when_disabled(result):
    """关掉自动复制时，切换下拉框同样不该偷偷写剪贴板。"""
    QApplication.clipboard().setText("原内容")
    dlg = result(["AAA", "https://bbb.com"], autocopy=False)
    dlg.chooser.setCurrentIndex(1)
    assert dlg.text.toPlainText() == "https://bbb.com"
    assert QApplication.clipboard().text() == "原内容"


def test_result_shows_open_button_by_default_and_hides_it_when_disabled(result):
    assert result(["https://example.com/abc"]).open_btn.isVisible()
    assert not result(["https://example.com/abc"], open_link=False).open_btn.isVisible()


def test_result_link_disabled_also_blocks_the_browser(result, monkeypatch):
    """关掉时不只是藏按钮：真去调也不许打开（语义，不是外观）。"""
    opened = []
    monkeypatch.setattr("src.ui.qr_result.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url)))
    dlg = result(["https://example.com/abc"], open_link=False)
    dlg._open_link()
    assert opened == []


def test_result_text_area_is_not_painted_white(result):
    """回归：文本区曾经是纯白底 + 纯白字，整块内容看不见。

    判据取"深色像素占多数"而不是"某几个点是深的"：文本框有 1px 的**聚焦边框**
    （强调色，设计如此），角上取点会采到它，按点断言就变成误报。
    按面积统计既躲开边框，又更贴近用户看到的事实：这块区域整体是深的。
    """
    dlg = result(["https://example.com/abc"])
    QApplication.processEvents()
    img = dlg.text.grab().toImage()
    # 必须在取色前确认真抓到了画面：0x0 图会让遍历一个点都取不到，
    # 断言就变成永远通过（本项目栽过"假绿"，这条守卫不能省）。
    assert img.width() > 8 and img.height() > 8, "没抓到文本区画面，用例会假绿"

    xs, ys = range(0, img.width(), 4), range(0, img.height(), 4)
    points = [(x, y) for y in ys for x in xs]
    dark = sum(1 for x, y in points if img.pixelColor(x, y).lightness() < 128)
    ratio = dark / len(points)
    assert ratio > 0.5, f"文本区只有 {ratio:.0%} 是深色——底色又落回系统默认（纯白）了"


def test_result_preview_flattens_multiline(result):
    dlg = result(["a\nb\nc"])
    assert dlg.chooser.count() == 1


# ---------- 面板接线 ----------

def _fake_scan(monkeypatch, hits, monkeypatch_decode=None):
    """把框选与解码都换成假的，只验面板的接线。"""
    import src.ui.taskbar as taskbar_mod

    crop = np.zeros((20, 20, 3), np.uint8)
    calls = {"picked": 0}

    def _pick(hint="", restore=None):
        calls["picked"] += 1
        assert "二维码" in hint, "提示语要告诉用户这是二维码框选"
        return (None, crop)

    monkeypatch.setattr(taskbar_mod, "pick_region_hiding_app", _pick)
    monkeypatch.setattr(taskbar_mod, "decode_qr",
                        monkeypatch_decode or (lambda _img: _result(hits)))
    return calls


def _result(hits):
    from src.core.qrdecode import QrScanResult

    return QrScanResult(hits=[QrHit(t) for t in hits])


def test_scan_qr_shows_result_dialog(taskbar, monkeypatch, qtbot):
    """非链接的内容走结果窗（链接走"直开"那条路，另有专门用例）。"""
    _fake_scan(monkeypatch, ["一段纯文本"])
    taskbar.scan_qr()
    assert taskbar._qr_dialog is not None
    assert taskbar._qr_dialog.isVisible()
    assert QApplication.clipboard().text() == "一段纯文本"


def test_scan_qr_cancel_is_silent(taskbar, monkeypatch):
    import src.ui.taskbar as taskbar_mod

    monkeypatch.setattr(taskbar_mod, "pick_region_hiding_app",
                        lambda **_k: None)
    taskbar.scan_qr()
    assert taskbar._qr_dialog is None
    assert not taskbar._qr_busy


def test_scan_qr_no_hit_reports_without_dialog(taskbar, monkeypatch):
    _fake_scan(monkeypatch, [])
    taskbar.scan_qr()
    assert taskbar._qr_dialog is None
    assert not taskbar._qr_busy


def test_scan_qr_reports_decode_error(taskbar, monkeypatch):
    """缺组件 / 图坏了这类失败只该提示，不该弹空结果窗。"""
    from src.core.qrdecode import QrScanResult

    _fake_scan(monkeypatch, [],
               lambda _img: QrScanResult(error="缺少二维码解码组件 zxing-cpp：x"))
    taskbar.scan_qr()
    assert taskbar._qr_dialog is None
    assert not taskbar._qr_busy


def test_scan_qr_is_reentrancy_guarded(taskbar, monkeypatch):
    """热键是全局的，框选期间再按一次不能叠出第二个遮罩。

    遮罩已经铺满屏幕，第二次进入会把它隐藏掉再叠一层，最后谁都框不成。
    """
    calls = _fake_scan(monkeypatch, ["X"])
    taskbar._qr_busy = True
    taskbar.scan_qr()
    assert calls["picked"] == 0
    taskbar._qr_busy = False


def test_scan_qr_refuses_while_modal_dialog_is_open(taskbar, monkeypatch, qtbot):
    """模态对话框开着时不能框选。

    框选前要隐藏本程序所有窗口，而 **hide 会终止 exec() 的模态循环** ——
    那个对话框会莫名自己关掉（托盘菜单不受模态限制，用户照样点得到）。
    """
    from PySide6.QtWidgets import QDialog

    calls = _fake_scan(monkeypatch, ["X"])
    dlg = QDialog(taskbar)
    qtbot.addWidget(dlg)
    dlg.setModal(True)
    dlg.show()
    assert QApplication.activeModalWidget() is dlg
    taskbar.scan_qr()
    assert calls["picked"] == 0
    dlg.close()


def test_scan_qr_pauses_fullscreen_watch_and_restores_it(taskbar, monkeypatch):
    """框选遮罩铺满整屏，全屏检测会把它当成"别的应用全屏了"而让面板让位。"""
    import src.ui.taskbar as taskbar_mod

    seen = {}
    crop = np.zeros((20, 20, 3), np.uint8)

    def _pick(hint="", restore=None):
        seen["timer_stopped"] = not taskbar._quiet_timer.isActive()
        return (None, crop)

    monkeypatch.setattr(taskbar_mod, "pick_region_hiding_app", _pick)
    monkeypatch.setattr(taskbar_mod, "decode_qr", lambda _img: _result(["X"]))
    taskbar._quiet_timer.start()
    taskbar.scan_qr()
    assert seen["timer_stopped"], "框选期间必须停掉全屏检测"
    assert taskbar._quiet_timer.isActive(), "结束后要按设置恢复检测"


def test_scan_qr_replaces_previous_result_window(taskbar, monkeypatch):
    _fake_scan(monkeypatch, ["AAA"])
    taskbar.scan_qr()
    first = taskbar._qr_dialog
    taskbar.scan_qr()
    assert taskbar._qr_dialog is not first


def _drain_deferred_deletes():
    """让排队的 `deleteLater()` 真的执行完。

    ⚠️ `processEvents()` 默认**不**处理 DeferredDelete，必须显式投递。上一版
    替换用例扫两次却全绿，唯一原因就是它一次事件循环都没跑 —— 拿到的对象还
    活着，跟真机上"关了窗、隔了几轮事件循环再扫"根本不是一回事。
    """
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_result_dialog_reference_is_cleared_after_it_dies(taskbar, monkeypatch):
    """结果窗销毁后，面板持有的引用必须变成 None。

    契约：`_qr_dialog is not None` 恒等于"这个对象还能用"。一旦 C++ 对象被
    deleteLater 删掉而引用留着，这个等式就断了 —— 下次扫码调 `.close()` 会
    `RuntimeError: Internal C++ object already deleted`（真机崩过）。
    """
    _fake_scan(monkeypatch, ["AAA"])
    taskbar.scan_qr()
    dlg = taskbar._qr_dialog
    assert dlg is not None
    dlg.close()
    _drain_deferred_deletes()
    assert taskbar._qr_dialog is None, \
        "结果窗已销毁，面板却还攥着失效引用 —— 下次扫码必然崩"


def test_scan_qr_after_result_window_died_does_not_raise(taskbar, monkeypatch):
    """回归：关掉结果窗、事件循环跑完之后再扫一次。

    这就是真机那条堆栈：`_show_qr_result` 里 `self._qr_dialog.close()` 撞上
    已被 `deleteLater` 删掉的 C++ 对象。复现的关键是**中间必须跑事件循环**，
    否则删除队不执行，测出来是假绿。
    """
    _fake_scan(monkeypatch, ["AAA"])
    taskbar.scan_qr()
    first = taskbar._qr_dialog
    first.close()                     # 用户按 Esc / 点关闭
    _drain_deferred_deletes()         # 真机在这里隔了无数轮事件循环
    taskbar.scan_qr()                 # 修复前：RuntimeError
    assert taskbar._qr_dialog is not None
    assert taskbar._qr_dialog is not first
    assert taskbar._qr_dialog.isVisible()


def test_old_dialog_deletion_does_not_clear_the_new_one(taskbar, monkeypatch):
    """旧窗的销毁信号会**晚于**新窗赋值到达，不能把新窗的引用连带清掉。

    `_show_qr_result` 里"关旧窗 → 建新窗"是同步的，中间不跑事件循环，而
    `deleteLater` 要等下一轮才执行 —— 所以旧窗的销毁信号一定在新窗赋值之后才
    到。清理若不做身份比对，就会把刚建好的新窗引用抹成 None，于是下次扫码
    "没有旧窗要关"，结果窗开始在屏幕上堆积。
    （后果不是"新窗消失"：顶层窗口不靠 Python 引用存活，实测过 —— 见下方
    最后那段窗口计数的断言，它盯的就是真实的堆积现象。）
    """
    _fake_scan(monkeypatch, ["AAA"])
    taskbar.scan_qr()
    first = taskbar._qr_dialog
    taskbar.scan_qr()                 # 关旧建新，全程不给事件循环机会
    second = taskbar._qr_dialog
    assert second is not first
    _drain_deferred_deletes()         # 旧窗到这一刻才真正析构
    assert taskbar._qr_dialog is second, \
        "旧窗的销毁信号把新窗的引用清掉了 —— 下次扫码将关不掉它"

    # 把"引用断了"的**真实后果**也钉住：再扫一次，屏幕上只该剩一个结果窗。
    taskbar.scan_qr()
    _drain_deferred_deletes()
    left = [w for w in QApplication.topLevelWidgets()
            if isinstance(w, QrResultDialog) and w.isVisible()]
    assert len(left) == 1, f"结果窗堆了 {len(left)} 个 —— 旧窗没被关掉"


# ---------- 热键接线 ----------

def test_taskbar_registers_both_hotkeys(taskbar):
    """接线正确性：两个功能都绑上了，且用的是库里的当前值。"""
    bound = taskbar._hotkeys.bindings()
    assert bound == {"呼出 / 隐藏面板": config.HOTKEY_TOGGLE,
                     "识别二维码": config.HOTKEY_QR}


def test_hotkey_labels_follow_the_settings(taskbar, tmp_db):
    """改键后菜单与按钮提示必须跟着变，否则用户以为改键没生效。"""
    tmp_db.set_setting(config.SETTING_HOTKEY_QR, "<cmd>+<alt>+j")
    taskbar._setup_hotkeys()
    assert "Alt + Win + J" in taskbar.qr_btn.toolTip()
    assert taskbar.act_qr.text().endswith("Alt + Win + J")
    assert taskbar.act_show.text().endswith(hotkeys.label(config.HOTKEY_TOGGLE))


def test_show_settings_stops_and_restores_hotkeys(taskbar, monkeypatch):
    """录制期间必须停掉热键：按下的组合若还绑着动作会当场触发那个动作。"""
    import src.ui.taskbar as taskbar_mod

    backend = taskbar._hotkeys._backend
    observed = {}

    class _Dlg:
        def exec(self):
            observed["handlers_during"] = dict(backend.handlers)
            return 0

    monkeypatch.setattr(taskbar_mod, "SettingsDialog", lambda *a, **k: _Dlg())
    taskbar._show_settings()
    assert observed["handlers_during"] == {}, "设置窗开着时不该有任何热键生效"
    # 关掉之后必须重新注册 —— 是我们停掉的，不注册它们就"正常"地消失了
    assert set(backend.handlers) == {config.HOTKEY_TOGGLE, config.HOTKEY_QR}


def test_show_settings_restores_hotkeys_even_if_dialog_raises(taskbar, monkeypatch):
    import src.ui.taskbar as taskbar_mod

    class _Dlg:
        def exec(self):
            raise RuntimeError("窗口炸了")

    monkeypatch.setattr(taskbar_mod, "SettingsDialog", lambda *a, **k: _Dlg())
    with pytest.raises(RuntimeError):
        taskbar._show_settings()
    assert set(taskbar._hotkeys._backend.handlers) == {
        config.HOTKEY_TOGGLE, config.HOTKEY_QR}


# ---------- 识别到链接直接开浏览器 ----------

def _fake_open(monkeypatch, ok=True):
    """把"交给系统浏览器"换成假的，返回被打开的内容列表。"""
    import src.ui.taskbar as taskbar_mod

    opened = []

    def _open(text):
        opened.append(text)
        return ok

    monkeypatch.setattr(taskbar_mod, "open_url_in_browser", _open)
    return opened


def test_open_url_in_browser_adds_scheme_for_bare_www(monkeypatch):
    """`www.` 开头必须补 `https://`，否则浏览器会把它当相对路径。"""
    opened = []
    monkeypatch.setattr("src.ui.qr_result.QDesktopServices.openUrl",
                        staticmethod(lambda url: opened.append(url) or True))
    assert open_url_in_browser("www.example.com") is True
    assert [u.toString() for u in opened] == ["https://www.example.com"]
    opened.clear()
    assert open_url_in_browser("普通文本不是链接") is False, "非链接不该硬开"
    assert opened == []


def test_scan_qr_opens_link_directly_without_dialog(taskbar, monkeypatch):
    """直开默认开：扫到链接直接交给浏览器，不弹结果窗。"""
    _fake_scan(monkeypatch, ["https://example.com/a"])
    opened = _fake_open(monkeypatch)
    taskbar.scan_qr()
    assert opened == ["https://example.com/a"]
    assert taskbar._qr_dialog is None, "直开时不该弹结果窗"
    assert QApplication.clipboard().text() == "https://example.com/a", \
        "直开也要把内容留在剪贴板"


def test_scan_qr_direct_picks_the_first_link_among_multiple_hits(taskbar, monkeypatch):
    """多码：取第一个链接直开，不因为多码就退回弹窗（用户确认过的行为）。"""
    _fake_scan(monkeypatch, ["纯文本", "https://b.example", "https://c.example"])
    opened = _fake_open(monkeypatch)
    taskbar.scan_qr()
    assert opened == ["https://b.example"]
    assert taskbar._qr_dialog is None


def test_scan_qr_keeps_dialog_when_direct_is_off(taskbar, monkeypatch):
    """关掉直开 → 回到"弹窗 + 打开链接按钮"的老行为。"""
    taskbar.act_qr_open_direct.setChecked(False)
    _fake_scan(monkeypatch, ["https://example.com/a"])
    opened = _fake_open(monkeypatch)
    taskbar.scan_qr()
    assert opened == []
    assert taskbar._qr_dialog is not None and taskbar._qr_dialog.isVisible()


def test_scan_qr_still_shows_dialog_for_non_link_content(taskbar, monkeypatch):
    """不是链接的内容照样弹窗——不然用户根本看不到扫到了什么。"""
    _fake_scan(monkeypatch, ["纯文本内容"])
    opened = _fake_open(monkeypatch)
    taskbar.scan_qr()
    assert opened == []
    assert taskbar._qr_dialog is not None


def test_scan_qr_direct_does_not_bypass_the_autocopy_switch(taskbar, monkeypatch):
    """直开只省掉"看一眼再点按钮"，不该顺带绕开「识别后自动复制」。"""
    taskbar.act_qr_autocopy.setChecked(False)
    QApplication.clipboard().setText("哨兵")
    _fake_scan(monkeypatch, ["https://example.com/a"])
    opened = _fake_open(monkeypatch)
    taskbar.scan_qr()
    # 先确认真的走了直开路径 —— 否则"没写剪贴板"可能只是因为它压根没直开
    # （禁掉直开这条用例照样会绿，等于白测）
    assert opened == ["https://example.com/a"]
    assert taskbar._qr_dialog is None
    assert QApplication.clipboard().text() == "哨兵"


def test_scan_qr_direct_reports_when_the_browser_refuses(taskbar, monkeypatch):
    """浏览器交不出去时必须出声，否则用户看到的是"扫完什么都没发生"。"""
    _fake_scan(monkeypatch, ["https://example.com/a"])
    _fake_open(monkeypatch, ok=False)
    said = []
    monkeypatch.setattr(taskbar, "_notify", lambda msg, **kw: said.append((msg, kw)))
    taskbar.scan_qr()
    assert said, "打开失败却一声不吭"
    assert "没能打开" in said[0][0]
    assert said[0][1].get("warning") is True


# ---------- 托盘「二维码」子菜单 ----------

def _qr_menu(taskbar):
    """二维码子菜单本体。

    ⚠️ **绝对不要用 `action.menu()` 去找子菜单**。PySide6 6.11.1 实测：这个调用
    会让返回的 QMenu 包装带走所有权，**子菜单的 C++ 对象当场被删**（不用等 GC——
    调用完立刻 `sub.actions()` 就报 "Internal C++ object already deleted"）。
    实测安全的取法是 `sub.menuAction()` 与 `sub.parent()`。
    产品代码从不调 `.menu()`，所以这个坑只坑探针和测试，但足以让用例假失败、
    且失败信息完全指不到真正的原因。
    """
    sub = taskbar.qr_menu
    assert sub.parent() is taskbar.tray.contextMenu(), "子菜单没挂在托盘根菜单上"
    assert sub.menuAction() in list(taskbar.tray.contextMenu().actions()), \
        "根菜单里找不到这个子菜单的条目"
    return sub


def test_tray_groups_qr_entries_into_a_submenu(taskbar):
    """二维码相关收进子菜单，根菜单不再平铺识别入口。"""
    root = [a.text() for a in taskbar.tray.contextMenu().actions()]
    assert any(t.startswith("二维码") for t in root)
    assert not any(t.startswith("识别二维码") for t in root), \
        "识别二维码应已挪进子菜单"
    assert any(t.startswith("快捷键设置") for t in root)
    sub = [a.text() for a in _qr_menu(taskbar).actions()]
    assert any(t.startswith("识别二维码") for t in sub)
    assert "识别后自动复制" in sub
    assert "识别到链接直接打开浏览器" in sub
    assert "识别到网址显示「打开链接」" in sub


def test_tray_qr_trigger_is_wired_to_scan(taskbar, monkeypatch):
    calls = _fake_scan(monkeypatch, ["AAA"])
    for act in _qr_menu(taskbar).actions():
        if act.text().startswith("识别二维码"):
            act.trigger()
            break
    else:
        raise AssertionError("子菜单里没有识别入口")
    assert calls["picked"] == 1


def test_tray_qr_switches_write_through_to_the_db(taskbar):
    taskbar.act_qr_autocopy.setChecked(False)
    taskbar.act_qr_open_direct.setChecked(False)
    taskbar.act_qr_open_link.setChecked(False)
    assert taskbar.db.get_setting(config.SETTING_QR_AUTOCOPY) == "0"
    assert taskbar.db.get_setting(config.SETTING_QR_OPEN_DIRECT) == "0"
    assert taskbar.db.get_setting(config.SETTING_QR_OPEN_LINK) == "0"
    taskbar.act_qr_autocopy.setChecked(True)
    assert taskbar.db.get_setting(config.SETTING_QR_AUTOCOPY) == "1"


def test_qr_open_link_switch_is_greyed_out_while_direct_open_is_on(taskbar):
    """直开开着时那个按钮开关没有任何场景可用 → 置灰；关掉直开后要能改回来。"""
    assert taskbar.act_qr_open_direct.isChecked(), "直开应默认开"
    assert not taskbar.act_qr_open_link.isEnabled(), \
        "直开开启时「显示打开链接」应置灰（弹窗根本不会出现）"
    taskbar.act_qr_open_direct.setChecked(False)
    assert taskbar.act_qr_open_link.isEnabled(), "关掉直开后置灰必须解除"


def test_tray_qr_switches_default_to_on(taskbar, tmp_db):
    """老库里没有这几个键 → 缺省必须等于开，否则升级上来会静默少功能。"""
    assert taskbar.act_qr_autocopy.isChecked()
    assert taskbar.act_qr_open_direct.isChecked()
    assert taskbar.act_qr_open_link.isChecked()
    assert taskbar._qr_autocopy_enabled()
    assert taskbar._qr_open_direct_enabled()
    assert taskbar._qr_open_link_enabled()
    tmp_db.set_setting(config.SETTING_QR_AUTOCOPY, "0")
    assert not taskbar._qr_autocopy_enabled()
    assert taskbar._qr_open_link_enabled(), "只关了一个，另一个不受影响"


def test_scan_qr_passes_the_switches_to_the_result_window(taskbar, tmp_db,
                                                         monkeypatch):
    """托盘开关必须真的作用到结果窗，而不是只改了个勾。"""
    tmp_db.set_setting(config.SETTING_QR_AUTOCOPY, "0")
    tmp_db.set_setting(config.SETTING_QR_OPEN_LINK, "0")
    # 直开关掉：否则这个链接会被直开那条路拦下，根本走不到结果窗
    tmp_db.set_setting(config.SETTING_QR_OPEN_DIRECT, "0")
    QApplication.clipboard().setText("原内容")
    _fake_scan(monkeypatch, ["https://example.com/x"])
    taskbar.scan_qr()
    dlg = taskbar._qr_dialog
    assert dlg._autocopy is False
    assert dlg._link_allowed is False
    assert not dlg.open_btn.isVisible()
    assert QApplication.clipboard().text() == "原内容"


def test_qr_button_is_hit_testable(taskbar):
    """header 里的按钮要真能点到（不能用 widgetAt 之外的方式验证，见 test_ui）。"""
    QApplication.processEvents()
    hit = QApplication.widgetAt(taskbar.qr_btn.mapToGlobal(taskbar.qr_btn.rect().center()))
    assert hit is taskbar.qr_btn or taskbar.qr_btn.isAncestorOf(hit)
