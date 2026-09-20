# -*- coding: utf-8 -*-

from src.core.db import TaskDB
from src.core.models import TASK_DAILY, Task


def _mk_task(**kw) -> Task:
    defaults = dict(name="测试任务", task_type=TASK_DAILY)
    defaults.update(kw)
    return Task(**defaults)


def test_add_and_get(tmp_db: TaskDB):
    t = tmp_db.add_task(_mk_task())
    assert t.id is not None
    got = tmp_db.get_task(t.id)
    assert got.name == "测试任务"
    assert got.task_type == TASK_DAILY


def test_update_task(tmp_db: TaskDB):
    t = tmp_db.add_task(_mk_task())
    t.name = "改名"
    t.region = (10, 20, 300, 200)
    t.threshold = 0.9
    tmp_db.update_task(t)
    got = tmp_db.get_task(t.id)
    assert got.name == "改名"
    assert got.region == (10, 20, 300, 200)
    assert got.threshold == 0.9


def test_delete_task_removes_logs(tmp_db: TaskDB):
    t = tmp_db.add_task(_mk_task())
    tmp_db.mark_completed(t.id, "2026-09-08")
    tmp_db.delete_task(t.id)
    assert tmp_db.get_task(t.id) is None
    assert not tmp_db.is_completed(t.id, "2026-09-08")


def test_mark_completed_dedup(tmp_db: TaskDB):
    t = tmp_db.add_task(_mk_task())
    assert tmp_db.mark_completed(t.id, "2026-09-08") is True
    # 同周期重复标记 → False（防重复）
    assert tmp_db.mark_completed(t.id, "2026-09-08") is False
    assert tmp_db.mark_completed(t.id, "2026-09-09") is True


def test_completion_dates(tmp_db: TaskDB):
    t = tmp_db.add_task(_mk_task())
    tmp_db.mark_completed(t.id, "once-a")
    dates = tmp_db.completion_dates(t.id, days=30)
    assert len(dates) == 1
