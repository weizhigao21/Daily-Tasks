# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def tmp_db(tmp_path):
    from src.core.db import TaskDB

    db = TaskDB(tmp_path / "tasks.db")
    yield db
    db.close()


class _NoHookBackend:
    """假热键后端：只记账，不碰真实键盘。

    供 conftest 的 autouse 兜底与需要断言"注册了什么"的用例复用。
    """

    def __init__(self):
        self.handlers: dict = {}
        self.start_count = 0
        self.stop_count = 0

    def start(self, handlers):
        self.handlers = dict(handlers)
        self.start_count += 1

    def stop(self):
        self.handlers = {}
        self.stop_count += 1


@pytest.fixture(autouse=True)
def no_real_global_hotkeys(monkeypatch):
    """任何用例都不得真的注册全局键盘钩子。

    pynput 的 Listener 是**进程级低层键盘钩子**：测试里随手建一个 Taskbar
    就会装上，而 taskbar 这个 fixture 被几十条用例共用 —— 钩子会一层层叠加，
    既拖慢整轮测试，也让"热键注册成功了吗"变得不可断言（真的钩子只会静默成功）。
    默认全部换成假后端；要验证注册行为的用例自己注入 _NoHookBackend。
    """
    from src.core import hotkeys

    monkeypatch.setattr(hotkeys, "_PynputBackend", _NoHookBackend, raising=False)


@pytest.fixture
def taskbar(tmp_db, qtbot):
    from src.ui.taskbar import Taskbar

    bar = Taskbar(db=tmp_db)
    qtbot.addWidget(bar)
    bar.show()
    yield bar
    bar.tray.hide()
