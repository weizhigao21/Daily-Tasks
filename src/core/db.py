# -*- coding: utf-8 -*-
"""SQLite 存储层：tasks + task_logs 两表。"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    task_type     TEXT NOT NULL DEFAULT 'daily',
    verify_mode   TEXT NOT NULL DEFAULT 'manual',
    region        TEXT,
    template_path TEXT DEFAULT '',
    threshold     REAL DEFAULT 0.85,
    time_start    TEXT DEFAULT '',
    time_end      TEXT DEFAULT '',
    enabled       INTEGER DEFAULT 1,
    created_at    TEXT
);
CREATE TABLE IF NOT EXISTS task_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        INTEGER NOT NULL,
    period_key     TEXT NOT NULL,
    completed_date TEXT NOT NULL,
    completed_at   TEXT NOT NULL,
    verify_result  TEXT NOT NULL DEFAULT 'manual',
    UNIQUE(task_id, period_key)
);
CREATE INDEX IF NOT EXISTS idx_logs_task_date ON task_logs(task_id, completed_date);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TaskDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._local = threading.local()  # 每线程独立连接（sqlite 不允许跨线程复用）

    # ---------- 连接 ----------
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------- 任务 CRUD ----------
    def add_task(self, task: Task) -> Task:
        cur = self.conn.execute(
            "INSERT INTO tasks(name, task_type, verify_mode, region, template_path,"
            " threshold, time_start, time_end, enabled, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task.name, task.task_type, task.verify_mode,
             _region_to_str(task.region), task.template_path, task.threshold,
             task.time_start, task.time_end, int(task.enabled), task.created_at),
        )
        self.conn.commit()
        task.id = cur.lastrowid
        return task

    def update_task(self, task: Task) -> None:
        assert task.id is not None
        self.conn.execute(
            "UPDATE tasks SET name=?, task_type=?, verify_mode=?, region=?, template_path=?,"
            " threshold=?, time_start=?, time_end=?, enabled=? WHERE id=?",
            (task.name, task.task_type, task.verify_mode,
             _region_to_str(task.region), task.template_path, task.threshold,
             task.time_start, task.time_end, int(task.enabled), task.id),
        )
        self.conn.commit()

    def delete_task(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.conn.execute("DELETE FROM task_logs WHERE task_id=?", (task_id,))
        self.conn.commit()

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(self, enabled_only: bool = False) -> list[Task]:
        sql = "SELECT * FROM tasks"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        return [_row_to_task(r) for r in self.conn.execute(sql).fetchall()]

    # ---------- 设置 ----------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- 完成日志 ----------
    def mark_completed(self, task_id: int, period_key: str,
                       verify_result: str = "manual") -> bool:
        """标记完成。已存在同周期记录时返回 False（防重复）。"""
        now = datetime.now()
        try:
            self.conn.execute(
                "INSERT INTO task_logs(task_id, period_key, completed_date, completed_at, verify_result)"
                " VALUES(?,?,?,?,?)",
                (task_id, period_key, now.date().isoformat(),
                 now.isoformat(timespec="seconds"), verify_result),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def is_completed(self, task_id: int, period_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM task_logs WHERE task_id=? AND period_key=?",
            (task_id, period_key),
        ).fetchone()
        return row is not None

    def completion_dates(self, task_id: int, days: int = 90) -> set[str]:
        """近 N 天的完成日期集合（ISO 字符串），用于统计。"""
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = self.conn.execute(
            "SELECT DISTINCT completed_date FROM task_logs"
            " WHERE task_id=? AND completed_date>=?",
            (task_id, since),
        ).fetchall()
        return {r["completed_date"] for r in rows}


def _region_to_str(region) -> str:
    return ",".join(str(int(v)) for v in region) if region else ""


def _row_to_task(row: sqlite3.Row) -> Task:
    region_str = row["region"] or ""
    region = None
    if region_str:
        parts = [int(v) for v in region_str.split(",")]
        if len(parts) == 4:
            region = tuple(parts)  # type: ignore[assignment]
    return Task(
        id=row["id"],
        name=row["name"],
        task_type=row["task_type"],
        verify_mode=row["verify_mode"],
        region=region,
        template_path=row["template_path"] or "",
        threshold=float(row["threshold"] or 0.85),
        time_start=row["time_start"] or "",
        time_end=row["time_end"] or "",
        enabled=bool(row["enabled"]),
        created_at=row["created_at"] or "",
    )
