# -*- coding: utf-8 -*-
"""周期与时间窗口判定。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import TASK_DAILY, TASK_WEEKLY, Task


def period_key(task: Task, now: datetime | None = None) -> str:
    """任务当前所处周期键：每日=日期，每周=ISO 周号，一次性=固定值。"""
    now = now or datetime.now()
    if task.task_type == TASK_DAILY:
        return now.date().isoformat()
    if task.task_type == TASK_WEEKLY:
        iso = now.date().isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return "once"


def in_time_window(task: Task, now: datetime | None = None) -> bool:
    """当前时间是否在任务配置的验证时间窗口内（未配置 = 全天）。"""
    if not task.time_start or not task.time_end:
        return True
    now = now or datetime.now()
    hhmm = now.strftime("%H:%M")
    return task.time_start <= hhmm <= task.time_end


def is_due_today(task: Task, now: datetime | None = None) -> bool:
    """任务今天是否需要出现/完成（为 weekly 预留，当前三种类型均显示）。"""
    return task.enabled


def next_reset_text(task: Task, now: datetime | None = None) -> str:
    """下一次周期重置时间的人类可读描述。"""
    now = now or datetime.now()
    if task.task_type == TASK_DAILY:
        nxt = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        return f"明天 {nxt.strftime('%m-%d')} 重置"
    if task.task_type == TASK_WEEKLY:
        # 下周一
        days_ahead = (7 - now.weekday()) % 7 or 7
        nxt = now.date() + timedelta(days=days_ahead)
        return f"周一 {nxt.strftime('%m-%d')} 重置"
    return "不重置"


def streak_days(completed_dates: set[str], today: date | None = None) -> int:
    """连续打卡天数（以 completed_dates 中 ISO 日期字符串计算）。"""
    today = today or date.today()
    streak = 0
    day = today
    # 今天没打卡不打断连击（从昨天起算）
    if day.isoformat() not in completed_dates:
        day -= timedelta(days=1)
    while day.isoformat() in completed_dates:
        streak += 1
        day -= timedelta(days=1)
    return streak
