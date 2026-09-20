# -*- coding: utf-8 -*-
import numpy as np
import pytest

from src.core.matcher import match_template


def _make_screen() -> np.ndarray:
    screen = np.full((200, 300, 3), 240, dtype=np.uint8)
    screen[50:90, 100:180] = (30, 120, 60)  # RGB 绿块
    return screen


def test_match_hit():
    screen = _make_screen()
    tmpl = screen[50:90, 100:180].copy()
    ok, score = match_template(screen, tmpl, threshold=0.8)
    assert ok
    assert score > 0.95


def test_match_identical_size():
    """回归：框选区域=模板（同尺寸）时 scale=1.0 不能被跳过。"""
    screen = _make_screen()
    tmpl = screen.copy()  # 模板 == 整个截屏
    ok, score = match_template(screen, tmpl, threshold=0.85)
    assert ok
    assert score > 0.99


def test_match_miss():
    screen = _make_screen()
    # 屏幕中不存在的棋盘格纹理模板（避免常量模板导致 NCC 无意义）
    tmpl = np.zeros((40, 80, 3), dtype=np.uint8)
    tmpl[::2, ::2] = 255
    tmpl[1::2, 1::2] = 255
    ok, score = match_template(screen, tmpl, threshold=0.85)
    assert not ok
    assert score < 0.85


def test_match_scale_tolerance():
    screen = _make_screen()
    # 模板稍大一点（模拟轻微缩放），多尺度搜索应能命中
    from PIL import Image

    crop = Image.fromarray(screen[50:90, 100:180]).resize((88, 44))
    tmpl = np.asarray(crop)
    ok, score = match_template(screen, tmpl, threshold=0.8)
    assert ok


def test_match_constant_template_by_mean():
    """纯色模板：NCC 无定义，退化为均值比对。

    覆盖 _ncc_scores 的常量分支（该分支依赖 _pre 元组索引，易被静默改坏）。
    """
    screen = _make_screen()
    tmpl = np.full((40, 80, 3), (30, 120, 60), dtype=np.uint8)  # 与屏幕色块同色
    ok, score = match_template(screen, tmpl, threshold=0.85)
    assert ok
    assert score > 0.0

    # 屏幕中不存在这个均值的纯色块 → 不应命中
    tmpl2 = np.full((40, 80, 3), (200, 10, 10), dtype=np.uint8)
    ok2, _ = match_template(screen, tmpl2, threshold=0.85)
    assert not ok2


def test_ncc_matches_opencv():
    """numpy FFT 实现与 cv2 TM_CCOEFF_NORMED 结果交叉校验（cv2 缺失则跳过）。"""
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(42)
    screen = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
    tmpl = screen[30:70, 40:110].copy()
    screen = screen.copy()
    screen[80, 90] = (0, 0, 0)  # 轻微扰动，避免完美 1.0

    from src.core.matcher import _to_gray

    sg, tg = _to_gray(screen), _to_gray(tmpl)
    res_cv = cv2.matchTemplate(sg.astype(np.float32), tg.astype(np.float32),
                               cv2.TM_CCOEFF_NORMED)
    ours = float(match_template(screen, tmpl, threshold=0.0)[1])
    assert abs(ours - float(res_cv.max())) < 0.02
