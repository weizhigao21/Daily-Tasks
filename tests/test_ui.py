# -*- coding: utf-8 -*-
"""UI 冒烟测试（offscreen）。"""
from datetime import datetime

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication

from src.core.models import (
    TASK_DAILY,
    TASK_ONCE,
    TASK_WEEKLY,
    VERIFY_IMAGE,
    VERIFY_MANUAL,
    Task,
)
from src.ui.task_dialog import TaskDialog


@pytest.fixture(autouse=True)
def _tmp_template_dir(monkeypatch, tmp_path):
    """模板目录指向临时目录，避免测试污染真实 data/templates。"""
    from src import config

    tdir = tmp_path / "templates"
    tdir.mkdir()
    monkeypatch.setattr(config, "TEMPLATE_DIR", tdir)


@pytest.fixture(autouse=True)
def _no_modal_popups(monkeypatch):
    """屏蔽模态弹窗，防止 offscreen 环境下测试挂起。"""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def test_taskbar_empty_state(taskbar):
    assert taskbar.db.list_tasks() == []


def test_add_manual_task_and_complete(taskbar, tmp_db, qtbot):
    t = tmp_db.add_task(Task(name="手动任务", task_type=TASK_DAILY, verify_mode=VERIFY_MANUAL))
    taskbar.refresh_all()
    assert t.id in taskbar._rows
    # 模拟点击完成按钮
    qtbot.mouseClick(taskbar._rows[t.id]["btn"], Qt.MouseButton.LeftButton)
    taskbar.refresh_statuses()
    assert tmp_db.is_completed(t.id, datetime.now().date().isoformat())


def test_row_buttons_are_hit_testable(taskbar, tmp_db):
    """卡片不能让鼠标穿透，否则卡片内的「完成 / ⋯」按钮点不动。

    `WA_TransparentForMouseEvents` 的语义是"控件**及其子树**一起退出鼠标
    命中链"，所以设在卡片（容器）上会连带废掉里面的按钮。而
    `qtbot.mouseClick(btn)` 是把事件直接投递给按钮、**绕过命中测试**的，
    所以上面那条用例全绿也掩盖了这个 bug —— 必须用
    `QApplication.widgetAt()` 真实命中一次才测得出来。
    """
    t = tmp_db.add_task(Task(name="可点任务", task_type=TASK_DAILY,
                             verify_mode=VERIFY_MANUAL))
    taskbar.refresh_all()
    QApplication.processEvents()   # 让布局落位，否则 widgetAt 会命中到旧几何
    row = taskbar._rows[t.id]
    card = row["card"]

    assert not card.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
        "卡片是容器，不能整体鼠标穿透（会连带废掉里面的按钮）"

    # 完成按钮 + ⋯ 菜单按钮：真实命中到自身（或其后代）
    menu_btn = card.layout().itemAt(4).widget()
    for w in (row["btn"], menu_btn):
        hit = QApplication.widgetAt(w.mapToGlobal(w.rect().center()))
        assert hit is not None, f"{w.objectName()} 不在命中链上（鼠标完全够不到）"
        assert hit is w or w.isAncestorOf(hit), \
            f"{w.objectName()} 的点击被 {hit} 截走"

    # 反过来：文字区仍应穿透回卡片，保证按在文字上还能拖窗口
    name = row["name"]
    assert name.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert QApplication.widgetAt(name.mapToGlobal(name.rect().center())) is card, \
        "按在任务名上应命中卡片（由卡片的事件过滤器接管拖动）"


def test_completed_row_shows_state(taskbar, tmp_db):
    t = tmp_db.add_task(Task(name="已完成任务"))
    tmp_db.mark_completed(t.id, datetime.now().date().isoformat())
    taskbar.refresh_all()
    row = taskbar._rows[t.id]
    assert "已完成" in row["status"].text()
    # 完成后按钮应隐藏（避免按钮内重复显示"已完成"）
    assert not row["btn"].isVisible()


def test_dialog_modeless_flow_survives_hide_restore(taskbar, tmp_db, qtbot):
    """模拟截图流程：非模态对话框被隐藏再恢复后，保存不应丢失。"""
    dlg = TaskDialog(taskbar)
    taskbar._open_task_dialog(dlg)
    assert dlg.isVisible()
    # 截图前隐藏所有窗口（模拟 _pick_target 行为）
    dlg.hide()
    dlg.show()  # 恢复
    assert dlg.isVisible()
    # 填写并保存
    dlg.name_edit.setText("非模态任务")
    dlg._on_accept()
    assert dlg.task is not None
    dlg.done(dlg.result())
    assert tmp_db.list_tasks()[0].name == "非模态任务"


def test_dialog_manual_task_validation(qtbot):
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("新任务")
    dlg._on_accept()
    assert dlg.task is not None
    assert dlg.task.name == "新任务"
    assert dlg.task.verify_mode == VERIFY_MANUAL


def test_dialog_image_task_requires_region(qtbot):
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("图验任务")
    idx = dlg.verify_combo.findData(VERIFY_IMAGE)
    dlg.verify_combo.setCurrentIndex(idx)
    dlg._on_accept()
    assert dlg.task is None  # 缺区域/模板，应拦截


def test_dialog_image_task_ok(qtbot, tmp_path):
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("图验任务")
    idx = dlg.verify_combo.findData(VERIFY_IMAGE)
    dlg.verify_combo.setCurrentIndex(idx)
    dlg._region = (0, 0, 200, 100)
    tmpl = tmp_path / "t.png"
    tmpl.write_bytes(b"fake")
    dlg._pending_template = tmpl
    dlg._on_accept()
    assert dlg.task is not None
    assert dlg.task.region == (0, 0, 200, 100)
    assert dlg.task.template_path


# ---------- 模板文件生命周期 ----------
def test_new_task_template_finalized_to_id_name(taskbar, tmp_db):
    """新增任务：框选模板先落暂存名，入库后转正为 task_{id}.png。"""
    from src import config

    dlg = TaskDialog(taskbar)
    taskbar._open_task_dialog(dlg)
    dlg.name_edit.setText("图验任务")
    dlg.verify_combo.setCurrentIndex(dlg.verify_combo.findData(VERIFY_IMAGE))
    dlg._region = (0, 0, 200, 100)
    staging = dlg._staging_template()
    staging.write_bytes(b"fake-template")
    dlg._pending_template = staging
    dlg._on_accept()
    assert dlg.task is not None
    dlg.done(dlg.result())

    saved = tmp_db.list_tasks()[0]
    # 只存文件名（可移植），不存绝对路径
    assert saved.template_path == f"task_{saved.id}.png"
    assert config.resolve_template(saved.template_path).exists()
    assert not staging.exists(), "暂存文件应已转正，不留残骸"


def test_edit_task_replaces_stale_template(taskbar, tmp_db):
    """历史遗留的 pending_*.png 在换图后应被清理，用户源文件必须保留。"""
    from src import config

    old = config.TEMPLATE_DIR / "pending_legacy_1234.png"
    old.write_bytes(b"old")
    t = tmp_db.add_task(Task(name="旧模板", task_type=TASK_DAILY,
                             verify_mode=VERIFY_IMAGE, region=(0, 0, 10, 10),
                             template_path=str(old)))  # 模拟历史绝对路径

    dlg = TaskDialog(taskbar, t)
    taskbar._open_task_dialog(dlg)
    chosen = config.TEMPLATE_DIR / "chosen.png"
    chosen.write_bytes(b"new")
    dlg._pending_template = chosen
    dlg._on_accept()
    dlg.done(dlg.result())

    got = tmp_db.get_task(t.id)
    assert got.template_path == f"task_{t.id}.png"
    assert config.resolve_template(got.template_path).exists()
    assert not old.exists(), "旧模板应被清理"
    assert chosen.exists(), "用户选择的源文件不能删除"


def test_edit_task_without_repick_normalizes_path(taskbar, tmp_db):
    """未换图时编辑保存：历史绝对路径应被规范成文件名，模板文件保持可用。"""
    from src import config

    legacy = config.TEMPLATE_DIR / "pending_keepme_777.png"
    legacy.write_bytes(b"keep")
    t = tmp_db.add_task(Task(name="不换图", task_type=TASK_DAILY,
                             verify_mode=VERIFY_IMAGE, region=(0, 0, 10, 10),
                             template_path=str(legacy)))

    dlg = TaskDialog(taskbar, t)
    taskbar._open_task_dialog(dlg)
    dlg._on_accept()          # 不设置 _pending_template = 未换图
    dlg.done(dlg.result())

    got = tmp_db.get_task(t.id)
    assert got.template_path == "pending_keepme_777.png"
    assert legacy.exists(), "未换图不能删原模板"
    assert config.resolve_template(got.template_path) == legacy


def test_failed_validation_leaves_source_and_template_intact(taskbar, tmp_db):
    """保存原子性：校验失败后点取消，共享 Task 对象与磁盘模板都不能被动过。

    历史缺陷：旧实现在 validate() 之前就地改 self._source（taskbar 任务列表
    持有的共享对象）并覆盖/删除模板文件——校验失败再点"取消"，作废的修改
    会在之后"禁用/启用"的全字段 UPDATE 时被写进数据库，旧模板已删不可恢复。
    """
    from src import config

    old = config.TEMPLATE_DIR / "atomic_keep.png"
    old.write_bytes(b"old")
    t = tmp_db.add_task(Task(name="原名", task_type=TASK_DAILY,
                             verify_mode=VERIFY_IMAGE, region=(0, 0, 10, 10),
                             template_path=str(old)))

    dlg = TaskDialog(taskbar, t)
    taskbar._open_task_dialog(dlg)
    dlg.name_edit.setText("")            # 名称为空 → 校验必失败
    new = config.TEMPLATE_DIR / "atomic_new.png"
    new.write_bytes(b"new")
    dlg._pending_template = new
    dlg._on_accept()

    assert dlg.task is None, "校验失败不该提交"
    assert t.name == "原名", "共享 Task 对象不能被未提交的编辑改坏"
    assert old.exists(), "校验失败不能删旧模板"
    assert new.exists(), "校验失败不能动新图"
    dlg.reject()


def test_reject_discards_own_staging_only(taskbar):
    """取消要清掉自己框选的暂存图，但不能碰同时开着的其它对话框的。

    历史缺陷：暂存名固定为 _pending_template.png，非模态多开时互相覆盖、
    互相 replace 走；取消后残骸永久留在 templates 目录。
    """
    a, b = TaskDialog(taskbar), TaskDialog(taskbar)
    assert a._staging_template() != b._staging_template(), "每个实例要有独立暂存名"
    a._staging_template().write_bytes(b"a")
    b._staging_template().write_bytes(b"b")
    a._pending_template = a._staging_template()
    b._pending_template = b._staging_template()

    a.reject()

    assert not a._staging_template().exists(), "取消后自己的暂存图应清理"
    assert b._staging_template().exists(), "别把别人对话框的暂存图一起删了"
    b._staging_template().unlink()


def test_verify_success_not_reported_as_miss_when_period_already_recorded(
        taskbar, tmp_db, monkeypatch):
    """验证匹配成功、但周期里已有记录（并发行）时不能显示"未命中/重试"。"""
    import src.ui.taskbar as taskbar_mod
    from src.core.verifier import VerifyResult

    t = tmp_db.add_task(Task(name="图验", task_type=TASK_DAILY,
                             verify_mode=VERIFY_IMAGE, region=(0, 0, 10, 10),
                             template_path="x.png"))
    pk = datetime.now().date().isoformat()
    tmp_db.mark_completed(t.id, pk)      # 先占住周期
    monkeypatch.setattr(taskbar_mod, "verify_task",
                        lambda task: VerifyResult(True, 0.93, "匹配成功"))

    got = []
    taskbar._verify_done.connect(lambda tid, ok, msg, score: got.append(ok))
    taskbar._verify_worker(t, pk)

    assert got == [True], "匹配成功就算完成，撞重复记录不算失败"


# ---------- 毛玻璃对话框：移动期撤 Acrylic（Win10 拖动迟滞） ----------
class _GlassSpy:
    """替身后端：记录 suspend/resume 调用次数。"""

    def __init__(self, resume_ok: bool = True) -> None:
        self.suspended = 0
        self.resumed = 0
        self._resume_ok = resume_ok

    def suspend(self, hwnd: int) -> None:
        self.suspended += 1

    def resume(self, hwnd: int) -> bool:
        self.resumed += 1
        return self._resume_ok


SAMPLED_BG = "#4E4B48"


def _fake_shot(color: str | None = SAMPLED_BG, w: int = 400, h: int = 300):
    """构造一张假的定格快照（整块像素都填 color 对应的 BGRA）。"""
    from src.ui import glass

    if color is None:
        return None
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    row = bytes([b, g, r, 255]) * w          # BGRA
    return glass.SurfaceShot(w, h, row * h, color)


def _force_glass(dlg, spy, monkeypatch, shot="default"):
    """把对话框推进"原生模糊已生效"的状态。

    offscreen 下拿不到真 Acrylic，必须手动置位 + 打桩后端，否则这些分支
    在测试里永远走不到（同"降级路径测不出原生模糊的 bug"）。
    `capture_window_surface` 也必须打桩：真实实现会去抓屏幕，单测不该依赖
    屏幕内容（传 shot=None 可模拟抓屏失败）。
    """
    from src.ui import glass

    if shot == "default":
        shot = _fake_shot()
    monkeypatch.setattr(glass, "suspend", spy.suspend)
    monkeypatch.setattr(glass, "resume", spy.resume)
    monkeypatch.setattr(glass, "capture_window_surface", lambda _hwnd: shot)
    dlg._glass_resolved = True
    dlg._glass_on = True
    dlg._glass_move_armed = True
    dlg._apply_dialog_qss("glass")
    dlg._glass_shown_at = 0.0    # 越过开窗落位期
    dlg._glass_geo = None


def test_dialog_suspends_acrylic_while_moving(qtbot, monkeypatch):
    """移动窗口时撤 Acrylic、且同时补上不透明底板（不能出现无底板的一帧）。

    注意**不要**改成 ACCENT_ENABLE_BLURBEHIND（FluentWPF 的"软模糊"做法）：
    本机实测它输出纯白且切不回来，详见 `glass.py` 的实测记录。
    """
    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)

    dlg._throttle_glass()
    assert spy.suspended == 1, "移动时应撤掉 Acrylic（Win10 迟滞元凶）"
    assert dlg._glass_qss_mode == "opaque", "撤 Acrylic 的同时必须补不透明底板"
    assert dlg._glass_freeze is not None, "移动期必须有定格快照顶着（否则只剩纯色）"
    assert dlg._glass_settle.isActive(), "应启动恢复倒计时"

    dlg._throttle_glass()          # 同几何重复触发
    assert spy.suspended == 1, "同几何不应重复撤（幂等，避免每帧 setStyleSheet）"

    dlg._restore_glass()
    assert spy.resumed == 1, "停下后应恢复 Acrylic"
    assert dlg._glass_qss_mode == "glass"
    assert dlg._glass_freeze is None, "恢复后快照要撤掉，别挡住真模糊"


def test_dialog_paints_opaque_backdrop_before_removing_acrylic(qtbot, monkeypatch):
    """回归（底板）：撤系统背景的那一刻，不透明底板必须已经在位。

    反序写（先撤系统背景、再换 Qt 底板）会留下一帧"系统不画 + Qt 没画"，
    对话框会露出窗口黑底 —— 与主面板第五轮那个 P0 完全同源。
    """
    from src.ui import glass

    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    spy = _GlassSpy()
    _force_glass(dlg, spy, monkeypatch)
    seen = {}

    def fake_suspend(_hwnd):
        seen["mode"] = dlg._glass_qss_mode

    monkeypatch.setattr(glass, "suspend", fake_suspend)
    dlg._throttle_glass()

    assert seen.get("mode") == "opaque", "撤系统背景时底板必须已是不透明的"


def test_dialog_moving_shows_frozen_snapshot(qtbot, monkeypatch):
    """回归（拖动只剩纯色）：移动期必须把**定格快照**画回窗口。

    移动期必须撤掉会迟滞的 Acrylic，而 Qt 补的底板只能是一块纯色——用户
    反馈的"点击移动的时候毛玻璃效果直接没有了，也只有颜色"就是缺了快照。
    撤之前把窗口此刻的样子（模糊桌面 + tint + 控件）整体拍下，移动全程
    把这张图当背景画回去，观感与静止时一致。
    """
    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)

    dlg._throttle_glass()
    assert dlg._glass_freeze is not None, "移动期应有定格快照"
    assert dlg._glass_qss_bg == SAMPLED_BG, "快照之下的兜底色应来自快照边缘采样"
    assert f"background: {SAMPLED_BG}" in dlg.styleSheet(), "兜底色必须真的写进样式表"
    ratio = dlg._glass_freeze.devicePixelRatio()
    assert ratio == dlg.devicePixelRatioF(), "快照按物理像素抓的，必须标对 DPR"


def test_dialog_moving_falls_back_when_snapshot_fails(qtbot, monkeypatch):
    """抓屏失败 → 回退主题底板色且不留快照，不能让窗口透明。"""
    from src.ui import theme

    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch, shot=None)

    dlg._throttle_glass()
    assert dlg._glass_freeze is None
    assert dlg._glass_qss_bg == theme.BG_BASE


def test_dialog_resizing_drops_stale_freeze(qtbot, monkeypatch):
    """拖动中被缩放 → 旧快照尺寸对不上，宁可退成纯色也不画歪。"""
    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)

    dlg._throttle_glass()
    assert dlg._glass_freeze is not None
    # 现在发生缩放：快照赶不上新尺寸，必须丢掉
    dlg.resize(dlg.width() + 30, dlg.height() + 10)
    dlg._throttle_glass(resized=True)
    assert dlg._glass_freeze is None, "缩放期不得沿用尺寸对不上的旧快照"
    assert spy.suspended >= 1, "缩放同样要撤 Acrylic"


def test_sampled_color_ready_before_suspend(qtbot, monkeypatch):
    """顺序：撤系统背景时，快照与底板必须已经在位（不能是主题色那一帧）。"""
    from src.ui import glass

    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    spy = _GlassSpy()
    _force_glass(dlg, spy, monkeypatch)
    seen = {}

    def fake_suspend(_hwnd):
        seen["bg"] = dlg._glass_qss_bg
        seen["freeze"] = dlg._glass_freeze is not None

    monkeypatch.setattr(glass, "suspend", fake_suspend)
    dlg._throttle_glass()

    assert seen.get("bg") == SAMPLED_BG, "撤背景时底板必须已是采样色"
    assert seen.get("freeze") is True, "撤背景时快照必须已经画上"


def test_restore_resets_move_baseline(qtbot, monkeypatch):
    """恢复后几何基线要对齐当前几何。

    不更新的话，Windows 拖动循环退出后补投的那批 moveEvent（携带的是最终几何）
    会被当成"窗口又移动了"，刚恢复就被再次撤掉 —— 窗口会一直停在无模糊的纯色底板上。
    """
    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)

    dlg._throttle_glass()
    # 模拟"窗口已经被拖到新位置，但基线还停在拖动中途的旧几何"
    # （offscreen 平台不响应 move()，只能直接伪造基线）
    stale = dlg._glass_geo
    dlg._glass_geo = QRect(stale.x() - 40, stale.y() - 25,
                           stale.width(), stale.height())
    dlg._restore_glass()
    assert dlg._glass_qss_mode == "glass"
    assert dlg._glass_geo == dlg.geometry(), "恢复后基线必须对齐当前几何"


def test_capture_surface_rejects_degenerate_window(monkeypatch):
    """窗口太小 / 拿不到矩形时，抓屏应老实返回 None（由调用方回退）。"""
    from src.ui import glass

    monkeypatch.setattr(glass, "_client_rect", lambda _hwnd: None)
    assert glass.capture_window_surface(0) is None

    monkeypatch.setattr(glass, "_client_rect", lambda _hwnd: (0, 0, 30, 30))
    assert glass.capture_window_surface(123) is None


def test_capture_surface_drops_broken_screen_instance(monkeypatch):
    """抓屏抛异常（分辨率变化等）→ 返回 None 并丢弃缓存的截屏实例。"""
    from src.ui import glass

    def boom(_box):
        raise RuntimeError("screen gone")

    monkeypatch.setattr(glass, "_client_rect", lambda _hwnd: (0, 0, 400, 300))
    monkeypatch.setattr(glass, "_sct", object())     # 旧实例
    monkeypatch.setattr(glass, "_screen", lambda: (_ for _ in ()).throw(
        RuntimeError("dead")))
    assert glass.capture_window_surface(1) is None
    assert glass._sct is None, "失效实例必须丢掉，下次重建"


def test_edge_color_clamps_extremes():
    """边缘采样要夹在合理区间：纯白背景不能让底板变浅色，纯黑不能彻底黑掉。"""
    from src.ui import glass

    w, h = 40, 30
    white = bytes([255, 255, 255, 255]) * (w * h)
    got = glass._edge_color(white, w, h)
    assert got == "#{:02X}{:02X}{:02X}".format(*(glass._SAMPLE_MAX,) * 3), \
        "全白必须被夹到上限，否则拖动时会闪出一块浅色"

    black = bytes([0, 0, 0, 255]) * (w * h)
    got = glass._edge_color(black, w, h)
    assert got == "#{:02X}{:02X}{:02X}".format(*(glass._SAMPLE_MIN,) * 3), \
        "全黑必须被夹到下限，否则移动期会掉成纯黑"

    assert glass._edge_color(b"", 10, 10) is None
    assert glass._edge_color(white, 0, 0) is None


def test_warm_up_screen_caches_instance(monkeypatch):
    """预热应把截屏实例缓存下来，后续采样不再付构造代价。

    采样只发生在"窗口移动的第一帧"上，首次构造实测 44ms（复用后 12ms），
    所以这件事必须在启动时就做掉。
    """
    from src.core import screen as screen_mod
    from src.ui import glass

    sentinel = object()
    monkeypatch.setattr(glass, "_sct", None)
    monkeypatch.setattr(screen_mod, "open_screen", lambda: sentinel)

    glass.warm_up_screen()
    assert glass._sct is sentinel


def test_warm_up_screen_swallows_failure(monkeypatch):
    """预热失败不能冒泡——拿不到屏幕也不该妨碍程序启动。"""
    from src.core import screen as screen_mod
    from src.ui import glass

    def boom():
        raise RuntimeError("no screen")

    monkeypatch.setattr(glass, "_sct", None)
    monkeypatch.setattr(screen_mod, "open_screen", boom)

    glass.warm_up_screen()          # 不应抛
    assert glass._sct is None


def test_dialog_does_not_touch_glass_during_open_settle(qtbot, monkeypatch):
    """开窗落位阶段不动 Acrylic，否则会看到"闪一下不透明"。"""
    import time

    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)
    dlg._glass_shown_at = time.monotonic()
    dlg._glass_geo = None

    dlg._throttle_glass()
    assert spy.suspended == 0
    assert dlg._glass_qss_mode == "glass"

    # 落位计时一过就正常生效
    dlg._glass_shown_at = time.monotonic() - 10
    dlg._glass_geo = None
    dlg._throttle_glass()
    assert spy.suspended == 1


def test_dialog_keeps_opaque_when_resume_fails(qtbot, monkeypatch):
    """恢复 Acrylic 失败时保持不透明底板——宁可少层模糊，也不能让底板消失。"""
    spy = _GlassSpy(resume_ok=False)
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)

    dlg._throttle_glass()
    dlg._restore_glass()
    assert spy.resumed == 1
    assert dlg._glass_qss_mode == "opaque", "恢复失败必须保留不透明底板"


def test_dialog_move_event_triggers_throttle(qtbot, monkeypatch):
    """moveEvent / resizeEvent 必须真的接到节流逻辑上。"""
    spy = _GlassSpy()
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    _force_glass(dlg, spy, monkeypatch)

    dlg._glass_geo = None
    dlg.move(dlg.x() + 40, dlg.y() + 30)
    QApplication.processEvents()
    assert spy.suspended >= 1, "移动窗口应触发撤 Acrylic"
    assert dlg._glass_qss_mode == "opaque"


def test_dialog_without_native_glass_never_suspends(qtbot, monkeypatch):
    """降级路径（没拿到原生模糊）不该有任何挂起动作。"""
    spy = _GlassSpy()
    from src.ui import glass

    monkeypatch.setattr(glass, "suspend", spy.suspend)
    monkeypatch.setattr(glass, "resume", spy.resume)
    dlg = TaskDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg._glass_on is False or dlg._glass_resolved

    dlg._glass_geo = None
    dlg.move(dlg.x() + 30, dlg.y() + 20)
    QApplication.processEvents()
    dlg._restore_glass()
    assert spy.suspended == 0
    assert spy.resumed == 0
    assert dlg._glass_qss_mode == "opaque"


def test_row_tooltip_shows_name_and_reset_hint(taskbar, tmp_db):
    """任务名 tooltip 应带周期重置提示（next_reset_text 不再是死代码）。"""
    daily = tmp_db.add_task(Task(name="每日任务", task_type=TASK_DAILY))
    weekly = tmp_db.add_task(Task(name="每周任务", task_type=TASK_WEEKLY))
    once = tmp_db.add_task(Task(name="一次性任务", task_type=TASK_ONCE))
    taskbar.refresh_all()

    daily_tip = taskbar._rows[daily.id]["name"].toolTip()
    assert "每日任务" in daily_tip, "仍要保留完整任务名（防长名截断）"
    assert "重置" in daily_tip

    assert "重置" in taskbar._rows[weekly.id]["name"].toolTip()
    assert taskbar._rows[once.id]["name"].toolTip().endswith("不重置")


# ---------- 标题栏版本号 ----------
def _labelled(bar, name: str):
    """取标题栏里指定 objectName 的标签，顺便断言它存在。"""
    from PySide6.QtWidgets import QLabel

    lbl = bar.findChild(QLabel, name)
    assert lbl is not None, f"标题栏里没有 objectName={name!r} 的标签"
    return lbl


def test_header_shows_version_right_of_the_app_name(taskbar):
    """版本号要紧跟程序名右侧，且在同一行（不是掉到下一行去）。"""
    from src import config
    from src.ui.version import APP_VERSION

    title = _labelled(taskbar, "appTitle")
    ver = _labelled(taskbar, "appVersion")
    assert title.text() == config.APP_NAME
    assert ver.text() == APP_VERSION, "显示的版本号必须来自 version.py 这个唯一来源"

    t_right = title.mapTo(taskbar, title.rect().topRight()).x()
    v_left = ver.mapTo(taskbar, ver.rect().topLeft()).x()
    assert v_left >= t_right, "版本号跑到程序名左边去了"
    assert v_left - t_right <= 12, f"离得太远（{v_left - t_right}px），不像名字的附属"

    # 同一行：纵向区间要有重叠
    t_top = title.mapTo(taskbar, title.rect().topLeft()).y()
    t_bottom = title.mapTo(taskbar, title.rect().bottomLeft()).y()
    v_top = ver.mapTo(taskbar, ver.rect().topLeft()).y()
    v_bottom = ver.mapTo(taskbar, ver.rect().bottomLeft()).y()
    assert v_top < t_bottom and t_top < v_bottom, "版本号没和程序名在同一行"


def test_version_label_is_smaller_than_the_app_name(taskbar):
    """"字号小一点"——必须真的比标题小，不能靠颜色糊弄过去。"""
    title = _labelled(taskbar, "appTitle")
    ver = _labelled(taskbar, "appVersion")
    t_px = title.font().pixelSize() or title.font().pointSize()
    v_px = ver.font().pixelSize() or ver.font().pointSize()
    assert t_px > 0 and v_px > 0, "字号取不到，用例会假绿"
    assert v_px < t_px, f"版本号字号 {v_px} 不小于标题的 {t_px}"


def test_version_label_shares_the_app_name_text_line(taskbar):
    """版本号与程序名要落在同一条文字中线上（不是掉到基线下方）。

    ⚠️ 判据是**墨迹**，不是控件外框：标题标签的外框被撑满整行（28px）而文字
    居中，版本号标签贴着自己的文字高度 —— 早期版本断言"两个控件底边对齐"，
    结果加了 `Qt.AlignBottom`（墨迹中心低 8px、明显掉一格）照样通过，是条
    没有区分力的守卫。

    用 `render()` 画到已知底色上取墨迹：QLabel 不画背景，`grab()` 出来的透明
    区转 QImage 后是纯黑，直接扫会把每一行都算成"有墨迹"。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPixmap

    ink_bg = QColor("#FF00FF")       # 故意用主题里不会出现的颜色当底
    title = _labelled(taskbar, "appTitle")
    ver = _labelled(taskbar, "appVersion")

    def ink_center_y(w) -> float:
        pm = QPixmap(w.size())
        pm.fill(ink_bg)
        w.render(pm)
        img = pm.toImage()
        rows = [y for y in range(img.height())
                if any(img.pixelColor(x, y) != ink_bg for x in range(img.width()))]
        assert rows, "这个标签一个墨迹像素都没有，守卫会假绿"
        top = w.mapTo(taskbar, QPoint(0, 0)).y()
        return top + (rows[0] + rows[-1]) / 2

    t_c = ink_center_y(title)
    v_c = ink_center_y(ver)
    assert abs(v_c - t_c) <= 2, \
        f"版本号文字中线比程序名低/高 {v_c - t_c:+.1f}px，两者不在同一条文字线上"


def test_version_lives_in_exactly_one_place():
    """版本号唯一来源：除了 version.py，别处不许再写死版本字面量。

    "v0.3.1" 这种字符串一旦散出去，发版时必漏一处（README/CHANGELOG 是文档、
    另行同步，这里只管代码）。只认"整个字符串就是版本号"的字面量 ——
    URL 里的 `/v1/` 之类不该被误伤，所以不做子串匹配。
    """
    import re
    import tokenize
    from pathlib import Path

    src_root = Path(__file__).resolve().parent.parent / "src"
    pat = re.compile(r"v?\d+\.\d+(\.\d+)?$")
    offenders = []
    for py in src_root.rglob("*.py"):
        if py.name == "version.py":
            continue
        with py.open("rb") as fh:
            try:
                for tok in tokenize.tokenize(fh.readline):
                    if tok.type == tokenize.STRING:
                        # 去掉引号/前缀
                        val = re.sub(r"^[a-zA-Z]*", "", tok.string).strip("'\"")
                        if pat.match(val):
                            offenders.append(f"{py.name}:{tok.start[0]} {val!r}")
            except tokenize.TokenError:
                continue
    assert not offenders, \
        "版本号只能定义在 src/ui/version.py，别处发现字面量：" + ", ".join(offenders)
