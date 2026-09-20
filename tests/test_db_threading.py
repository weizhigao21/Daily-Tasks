# -*- coding: utf-8 -*-
"""回归：验证工作线程中使用 TaskDB 不再抛 sqlite3.ProgrammingError。"""
import threading

from src.core.db import TaskDB
from src.core.models import Task


def test_cross_thread_access(tmp_path):
    db = TaskDB(tmp_path / "t.db")
    t = db.add_task(Task(name="跨线程任务"))
    results = {}

    def worker():
        try:
            ok = db.mark_completed(t.id, "p1", verify_result="image")
            results["ok"] = ok
            results["done"] = db.is_completed(t.id, "p1")
        except Exception as exc:  # noqa: BLE001
            results["error"] = exc

    th = threading.Thread(target=worker)
    th.start()
    th.join()

    assert "error" not in results, results["error"]
    assert results["ok"] is True
    assert results["done"] is True
    # 主线程也能读到（每线程独立连接，WAL/提交后可见）
    assert db.is_completed(t.id, "p1")


def test_main_and_thread_write_interleaved(tmp_path):
    db = TaskDB(tmp_path / "t.db")
    t = db.add_task(Task(name="交错写入"))
    db.mark_completed(t.id, "a")
    errs = []

    def worker():
        try:
            db.mark_completed(t.id, "b")
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    assert not errs
    assert db.is_completed(t.id, "a") and db.is_completed(t.id, "b")
