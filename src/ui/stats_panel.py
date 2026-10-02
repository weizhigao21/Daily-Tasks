# -*- coding: utf-8 -*-
"""打卡统计面板。"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.db import TaskDB
from ..core.models import Task
from ..core.scheduler import period_key, streak_days
from . import theme, window_state
from .glass import GlassDialogMixin

# 连续天数的回溯窗口：要大于任何真实连击才不被截断（"近30天完成"列仍用 30 天）
_STREAK_WINDOW_DAYS = 730


class StatsPanel(GlassDialogMixin, QDialog):
    def __init__(self, db: TaskDB, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("打卡统计")
        self.setMinimumSize(600, 360)
        # 毛玻璃 + 自动降级 + 记住窗口位置
        self.setup_glass(db=db, state_key=window_state.WINDOW_STATS_PANEL)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SP_4, theme.SP_4, theme.SP_4, theme.SP_4)
        layout.setSpacing(theme.SP_3)
        tasks = self.db.list_tasks()
        summary = QLabel(self._summary_text(tasks))
        summary.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.fs(theme.FS_BASE)}px;"
        )
        layout.addWidget(summary)

        table = QTableWidget(len(tasks), 4)
        table.setHorizontalHeaderLabels(["任务", "近30天完成", "连续天数", "本周完成"])
        header = table.horizontalHeader()
        # 表头默认逐列居中 → 宽列（任务列表头 336px）里标题飘在正中间，与左对齐
        # 的数据隔着一大段空白，看着"没对上"。与 QTableWidgetItem 一样左对齐。
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 去掉点击行时的虚线聚焦框
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        today = date.today()
        week_dates = {(today - timedelta(days=i)).isoformat() for i in range(today.weekday() + 1)}
        for r, task in enumerate(tasks):
            dates = self.db.completion_dates(task.id, days=30)
            table.setItem(r, 0, QTableWidgetItem(task.name))
            table.setItem(r, 1, QTableWidgetItem(str(len(dates))))
            # 连续天数要数完整连击，不能被 30 天统计窗口截断（长连击显示成 30）
            streak_dates = self.db.completion_dates(task.id, days=_STREAK_WINDOW_DAYS)
            table.setItem(r, 2, QTableWidgetItem(str(streak_days(streak_dates, today))))
            week_count = len(dates & week_dates)
            table.setItem(r, 3, QTableWidgetItem(str(week_count)))
        layout.addWidget(table)

    def _summary_text(self, tasks: list[Task]) -> str:
        if not tasks:
            return "还没有任务，先添加一个吧。"
        from datetime import datetime
        done_today = sum(
            1 for t in tasks
            if t.enabled and self.db.is_completed(t.id, period_key(t, datetime.now()))
        )
        return f"共 {len(tasks)} 个任务，今日已完成 {done_today} 个。"
