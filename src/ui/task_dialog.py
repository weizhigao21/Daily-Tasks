# -*- coding: utf-8 -*-
"""任务添加 / 编辑对话框。"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
)

from .. import config
from ..core.models import (
    TASK_TYPE_NAMES,
    TASK_TYPES,
    VERIFY_IMAGE,
    VERIFY_MANUAL,
    Task,
)
from . import theme, window_state
from .glass import GlassDialogMixin
from .region_picker import pick_region_hiding_app


class TaskDialog(GlassDialogMixin, QDialog):
    """编辑任务；exec() 后通过 .task 取结果。"""

    def __init__(self, parent=None, task: Task | None = None):
        super().__init__(parent)
        self._source = task
        self.task: Task | None = None
        self._pending_template: Path | None = None  # 新截取的模板临时文件

        self.setWindowTitle("编辑任务" if task else "添加任务")
        self.setMinimumWidth(460)
        # 毛玻璃 + 自动降级（取代裸 setStyleSheet）+ 记住窗口位置。
        # db 从父窗口借：TaskDialog 自己不持有存储层，而父窗口（Taskbar）有；
        # 没有父窗口（比如单测里裸建）时 getattr 拿到 None，位置记忆自动关闭。
        self.setup_glass(db=getattr(parent, "db", None),
                         state_key=window_state.WINDOW_TASK_DIALOG)
        self._build_ui()
        if task:
            self._load(task)

    # ---------- UI 构建 ----------
    def _build_ui(self) -> None:
        form = QFormLayout()
        form.setSpacing(theme.SP_3)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.name_edit = QLineEdit()
        form.addRow("任务名称", self.name_edit)

        self.type_combo = QComboBox()
        for t in TASK_TYPES:
            self.type_combo.addItem(TASK_TYPE_NAMES[t], t)
        form.addRow("任务类型", self.type_combo)

        self.verify_combo = QComboBox()
        self.verify_combo.addItem("手动点击完成", VERIFY_MANUAL)
        self.verify_combo.addItem("屏幕识别自动验证", VERIFY_IMAGE)
        self.verify_combo.currentIndexChanged.connect(self._refresh_verify_rows)
        form.addRow("验证方式", self.verify_combo)

        # 屏幕区域 + 目标模板：一步框选同时得到
        region_row = QHBoxLayout()
        self.region_label = QLabel("未设置")
        self.region_btn = QPushButton("框选目标画面…")
        self.region_btn.setToolTip("定格全屏，拖拽框选验证区域，同时截取目标模板图")
        self.region_btn.clicked.connect(self._pick_target)
        region_row.addWidget(self.region_label, 1)
        region_row.addWidget(self.region_btn)
        form.addRow("验证区域", region_row)

        # 目标模板（可重新框选，或手动指定已保存的状态图）
        tmpl_row = QHBoxLayout()
        self.tmpl_label = QLabel("随框选自动截取")
        self.tmpl_file_btn = QPushButton("选择图片…")
        self.tmpl_file_btn.clicked.connect(self._choose_template_file)
        self.tmpl_file_btn.setToolTip("若目标状态当前未出现，可稍后用此按钮指定保存好的截图")
        tmpl_row.addWidget(self.tmpl_label, 1)
        tmpl_row.addWidget(self.tmpl_file_btn)
        form.addRow("目标模板", tmpl_row)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.50, 1.00)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setValue(config.DEFAULT_MATCH_THRESHOLD)
        self.threshold_spin.setToolTip("截屏与模板的最低相似度，识别不稳时调低，误判时调高")
        form.addRow("匹配阈值", self.threshold_spin)

        # 时间窗口
        time_row = QHBoxLayout()
        self.time_check = QCheckBox("限制")
        self.time_check.toggled.connect(self._refresh_time_rows)
        self.time_start = QTimeEdit(QTime(6, 0))
        self.time_end = QTimeEdit(QTime(12, 0))
        self.time_start.setDisplayFormat("HH:mm")
        self.time_end.setDisplayFormat("HH:mm")
        time_row.addWidget(self.time_check)
        time_row.addWidget(self.time_start)
        time_row.addWidget(QLabel("至"))
        time_row.addWidget(self.time_end)
        time_row.addStretch(1)
        form.addRow("时间窗口", time_row)

        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        form.addRow("", self.enabled_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
        layout.addWidget(buttons)
        self._refresh_verify_rows()
        self._refresh_time_rows()

    def _refresh_verify_rows(self) -> None:
        is_img = self.verify_combo.currentData() == VERIFY_IMAGE
        for w in (self.region_btn, self.tmpl_file_btn, self.threshold_spin):
            w.setEnabled(is_img)
        self.region_label.setEnabled(is_img)
        self.tmpl_label.setEnabled(is_img)

    def _refresh_time_rows(self) -> None:
        on = self.time_check.isChecked()
        self.time_start.setEnabled(on)
        self.time_end.setEnabled(on)

    # ---------- 交互 ----------
    def _pick_target(self) -> None:
        """隐藏本程序全部窗口 → 定格全屏拖框 → 恢复窗口。

        一步得到验证区域 + 目标模板图，且定格帧不含本程序自身的窗口。
        藏窗口 / 等合成器 / 抢回焦点这一套两条路径共用，实现见 region_picker。
        """
        result = pick_region_hiding_app(
            hint="拖拽框选验证区域 · 松开确认 · Esc 取消", restore=self)
        if result is None:
            return
        rect, crop = result
        self._region = rect
        self.region_label.setText(
            f"{rect.x()},{rect.y()} {rect.width()}x{rect.height()}"
        )
        pending = config.TEMPLATE_DIR / "_pending_template.png"
        from ..core.matcher import save_image

        if save_image(str(pending), crop):
            self._pending_template = pending
            self.tmpl_label.setText("已截取（保存时生效）")

    def _choose_template_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择模板图片", "", "图片 (*.png *.jpg *.bmp)")
        if path:
            self._pending_template = Path(path)
            self.tmpl_label.setText(Path(path).name)

    # ---------- 模板文件生命周期 ----------
    def _staging_template(self) -> Path:
        """框选产生的临时帧（未入库任务的暂存模板）。"""
        return config.TEMPLATE_DIR / "_pending_template.png"

    @staticmethod
    def _template_dest(task: Task) -> Path:
        """正式模板路径：已入库任务固定为 task_{id}.png。

        不能再用 hash(name) 命名——Python 的 str hash 每进程随机化，
        同名任务会生成不同文件名，重复框选就留下一堆孤儿模板。
        """
        if task.id is not None:
            return config.TEMPLATE_DIR / f"task_{task.id}.png"
        return config.TEMPLATE_DIR / "_pending_template.png"

    @staticmethod
    def _to_stored(path: str) -> str:
        """把磁盘路径规范成库里存的形式：templates 目录内存文件名（可移植）。

        README 承诺"整个文件夹拷走即绿色便携"，因此绝不能存绝对路径。
        """
        if not path:
            return path
        p = Path(path)
        if p.parent == config.TEMPLATE_DIR:
            return p.name
        return str(p)

    def _apply_template(self, task: Task) -> None:
        """把待定模板落到正式路径，并清掉旧的/临时的中间文件。"""
        staging = self._staging_template()
        pending = self._pending_template
        old = self._source.template_path if self._source else ""

        if pending is None or not pending.exists():
            # 未换图：沿用原模板（顺带把历史绝对路径规范成文件名）
            if task.id is not None and self._source is not None:
                task.template_path = self._to_stored(self._source.template_path)
            return

        dest = self._template_dest(task)
        if pending.resolve() != dest.resolve():
            shutil.copyfile(pending, dest)
            if pending == staging:  # 框选临时帧已复制，删除避免堆积
                staging.unlink(missing_ok=True)
        task.template_path = self._to_stored(str(dest))

        # 清理旧模板文件（换过图后原文件不再被引用）
        self._remove_stale_template(old, dest)

    @staticmethod
    def _remove_stale_template(old: str, new_dest: Path) -> None:
        """删除不再被引用的旧模板（仅限本程序管理的 templates 目录内）。"""
        if not old:
            return
        old_path = config.resolve_template(old)
        if old_path is None or old_path.parent != config.TEMPLATE_DIR:
            return
        if old_path.resolve() == new_dest.resolve():
            return
        try:
            old_path.unlink(missing_ok=True)
        except OSError:
            pass

    def finalize_template(self, task_id: int) -> bool:
        """任务入库拿到 id 后，把暂存模板转正为 task_{id}.png。

        返回 True 表示路径有变化（调用方需回写数据库）。
        """
        if self.task is None or not self.task.template_path:
            return False
        cur = config.resolve_template(self.task.template_path)
        if cur is None or cur.name != "_pending_template.png" or not cur.exists():
            return False
        dest = config.TEMPLATE_DIR / f"task_{task_id}.png"
        try:
            dest.unlink(missing_ok=True)
            cur.replace(dest)
        except OSError:
            return False
        self.task.template_path = self._to_stored(str(dest))
        return True

    # ---------- 载入 / 提交 ----------
    def _load(self, task: Task) -> None:
        self.name_edit.setText(task.name)
        idx = self.type_combo.findData(task.task_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        idx = self.verify_combo.findData(task.verify_mode)
        if idx >= 0:
            self.verify_combo.setCurrentIndex(idx)
        if task.region:
            self._region = task.region
            self.region_label.setText(f"{task.region[0]},{task.region[1]} {task.region[2]}x{task.region[3]}")
        if task.template_path:
            self.tmpl_label.setText(Path(task.template_path).name)
        self.threshold_spin.setValue(task.threshold)
        if task.time_start and task.time_end:
            self.time_check.setChecked(True)
            self.time_start.setTime(QTime.fromString(task.time_start, "HH:mm"))
            self.time_end.setTime(QTime.fromString(task.time_end, "HH:mm"))
        self.enabled_check.setChecked(task.enabled)

    def _on_accept(self) -> None:
        task = self._source or Task()
        task.name = self.name_edit.text().strip()
        task.task_type = self.type_combo.currentData()
        task.verify_mode = self.verify_combo.currentData()
        rect = getattr(self, "_region", None)
        if rect is not None:
            if hasattr(rect, "x"):  # QRect
                task.region = (rect.x(), rect.y(), rect.width(), rect.height())
            else:
                task.region = tuple(rect)
        task.threshold = round(self.threshold_spin.value(), 2)
        if self.time_check.isChecked():
            task.time_start = self.time_start.time().toString("HH:mm")
            task.time_end = self.time_end.time().toString("HH:mm")
        else:
            task.time_start = task.time_end = ""
        task.enabled = self.enabled_check.isChecked()

        # 模板图处理：新截取/新选择的文件 → 落为正式模板（task_{id}.png）
        self._apply_template(task)

        errors = task.validate()
        if errors:
            QMessageBox.warning(self, "无法保存", "\n".join(errors))
            return
        self.task = task
        self.accept()
