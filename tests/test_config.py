# -*- coding: utf-8 -*-
"""config.resolve_template：模板路径解析（可移植性 + 历史绝对路径兼容）。"""
from src import config


def test_resolve_empty():
    assert config.resolve_template("") is None


def test_resolve_filename_uses_template_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TEMPLATE_DIR", tmp_path)
    assert config.resolve_template("task_3.png") == tmp_path / "task_3.png"


def test_resolve_live_absolute_path_kept(monkeypatch, tmp_path):
    """绝对路径仍存在 → 原样使用（不破坏现有数据）。"""
    monkeypatch.setattr(config, "TEMPLATE_DIR", tmp_path / "templates")
    live = tmp_path / "elsewhere.png"
    live.write_bytes(b"x")
    assert config.resolve_template(str(live)) == live


def test_resolve_dead_absolute_falls_back_by_name(monkeypatch, tmp_path):
    """拷走整个文件夹后旧绝对路径失效 → 按文件名回退到当前 templates 目录。"""
    tdir = tmp_path / "templates"
    tdir.mkdir()
    monkeypatch.setattr(config, "TEMPLATE_DIR", tdir)
    (tdir / "task_1.png").write_bytes(b"moved")

    dead = tmp_path / "old_location" / "task_1.png"   # 不存在
    resolved = config.resolve_template(str(dead))
    assert resolved == tdir / "task_1.png"
    assert resolved.exists()
