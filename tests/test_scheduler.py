# -*- coding: utf-8 -*-
from datetime import datetime

from src.core.models import TASK_DAILY, TASK_ONCE, TASK_WEEKLY, Task
from src.core.scheduler import in_time_window, next_reset_text, period_key, streak_days


def _t(**kw) -> Task:
    return Task(name="t", **kw)


def test_period_key_daily():
    t = _t(task_type=TASK_DAILY)
    now = datetime(2026, 9, 8, 15, 30)
    assert period_key(t, now) == "2026-09-08"


def test_period_key_weekly():
    t = _t(task_type=TASK_WEEKLY)
    now = datetime(2026, 9, 8, 15, 30)  # 周二
    assert period_key(t, now).startswith("2026-W")
    # 同一周内不同天 → 同 key
    assert period_key(t, datetime(2026, 9, 7)) == period_key(t, datetime(2026, 9, 13))
    # 下周 → 不同 key
    assert period_key(t, datetime(2026, 9, 14)) != period_key(t, datetime(2026, 9, 13))


def test_period_key_once():
    t = _t(task_type=TASK_ONCE)
    assert period_key(t) == "once"


def test_time_window():
    t = _t(time_start="06:00", time_end="12:00")
    assert in_time_window(t, datetime(2026, 9, 8, 7, 0))
    assert not in_time_window(t, datetime(2026, 9, 8, 13, 0))


def test_time_window_unlimited():
    t = _t()
    assert in_time_window(t, datetime(2026, 9, 8, 23, 59))


def test_streak_days():
    streak_days_input = {"2026-09-06", "2026-09-07", "2026-09-08"}
    assert streak_days(streak_days_input, today=datetime(2026, 9, 8).date()) == 3
    # 今天没打卡不打断连击
    assert streak_days(streak_days_input, today=datetime(2026, 9, 9).date()) == 3
    # 昨天也没打卡 → 0
    assert streak_days({"2026-09-05"}, today=datetime(2026, 9, 8).date()) == 0


def test_next_reset_text():
    t = _t(task_type=TASK_DAILY)
    assert "重置" in next_reset_text(t, datetime(2026, 9, 8))
    t2 = _t(task_type=TASK_ONCE)
    assert next_reset_text(t2) == "不重置"
