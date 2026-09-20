# -*- coding: utf-8 -*-
"""验证链扩展搜索逻辑测试（纯函数，不依赖真实截屏）。"""
import numpy as np

from src import config
from src.core.matcher import match_template
from src.core.verifier import _expand_region, _resolve_search_region

MON = {"left": 0, "top": 0, "width": 1920, "height": 1080}


def test_resolve_full_screen_mode(monkeypatch):
    monkeypatch.setattr(config, "VERIFY_SEARCH_FULL_SCREEN", True)
    # 无论任务区域在哪，全屏模式直接返回整个虚拟桌面
    assert _resolve_search_region((500, 400, 80, 60), MON) == (0, 0, 1920, 1080)


def test_resolve_margin_mode(monkeypatch):
    monkeypatch.setattr(config, "VERIFY_SEARCH_FULL_SCREEN", False)
    assert _resolve_search_region((500, 400, 80, 60), MON) == (380, 280, 320, 300)


def test_expand_interior_region():
    # 屏幕内部区域：四周各扩 120
    assert _expand_region((500, 400, 80, 60), MON) == (380, 280, 320, 300)


def test_expand_clamps_to_screen_edge():
    # 左上角贴边：负坐标被钳制到 0
    assert _expand_region((100, 100, 50, 30), MON) == (0, 0, 270, 250)
    # 右下角贴边：钳制到屏幕右下
    assert _expand_region((1870, 1050, 40, 20), MON) == (1750, 930, 170, 150)


def test_expand_fully_offscreen_falls_back():
    # 区域完全在屏幕外：回退原区域（截屏阶段会报错并给提示）
    assert _expand_region((-500, -500, 40, 40), MON) == (-500, -500, 40, 40)


def test_expand_multi_monitor_offset():
    # 副屏（负坐标虚拟桌面）：left 钳制到 -1920，top 钳制到 0
    mon = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
    # left: max(-1920, -1900-120)=-1920  right: min(0, -1900+50+120)=-1730
    # top:  max(0, 100-120)=0           bottom: min(1080, 100+40+120)=260
    assert _expand_region((-1900, 100, 50, 40), mon) == (-1920, 0, 190, 260)


def test_verify_passes_configured_scales(monkeypatch):
    """verifier 必须把 config.MATCH_SCALES 透传给 matcher（否则配置项是摆设）。"""
    from src.core import verifier
    from src.core.models import VERIFY_IMAGE, Task

    captured = {}

    def fake_match(screen, tmpl, threshold=0.85, scales=None):
        captured["scales"] = scales
        captured["threshold"] = threshold
        return True, 1.0

    monkeypatch.setattr(verifier, "match_template", fake_match)
    monkeypatch.setattr(verifier, "load_template", lambda p: np.zeros((8, 8, 3), np.uint8))
    monkeypatch.setattr(verifier, "capture_region", lambda r: np.zeros((40, 40, 3), np.uint8))
    monkeypatch.setattr(config, "MATCH_SCALES", (1.0, 0.95))

    task = Task(name="图验", verify_mode=VERIFY_IMAGE, region=(0, 0, 40, 40),
                template_path="task_9.png", threshold=0.9)
    assert verifier.verify_task(task).ok

    assert captured["scales"] == (1.0, 0.95)
    assert captured["threshold"] == 0.9


def test_scales_argument_controls_search_cost(monkeypatch):
    """scales 决定实际尝试的模板尺寸——收窄序列就真的少跑，这是提速的根据。"""
    import src.core.matcher as m

    tried: list[tuple[int, int]] = []
    real_ncc = m._ncc_scores

    def spy(gray, tmpl_gray, _pre=None):
        tried.append(tmpl_gray.shape)
        return real_ncc(gray, tmpl_gray, _pre=_pre)

    monkeypatch.setattr(m, "_ncc_scores", spy)

    screen = np.full((120, 160, 3), 240, dtype=np.uint8)
    screen[40:72, 60:120] = (30, 120, 60)
    tmpl = screen[40:72, 60:120].copy()

    # threshold 设为不可能达到的值 → 跑完全部尺度（模拟"未命中"最坏情况）
    tried.clear()
    m.match_template(screen, tmpl, threshold=2.0, scales=(1.0,))
    assert tried == [(32, 60)], "单尺度序列只应尝试一次"

    tried.clear()
    m.match_template(screen, tmpl, threshold=2.0, scales=(1.0, 0.5))
    assert tried == [(32, 60), (16, 30)], "1.0 应优先尝试，之后才是缩放尺度"


def test_target_shifted_within_margin_still_matches():
    """核心语义：目标在锚点附近位移后仍应命中（原来会失败）。"""
    margin = config.VERIFY_SEARCH_MARGIN_PX
    # 模拟扩展后的截屏：目标从锚点 (100,100) 位移到 (160,150)，位移量在容忍范围内
    shift_x, shift_y = 60, 50
    assert max(shift_x, shift_y) < margin, "前提：位移必须小于搜索边距"
    screen = np.full((400, 400, 3), 240, dtype=np.uint8)
    screen[150:190, 160:240] = (30, 120, 60)  # 目标出现在偏移后的位置
    # 模板 = 原锚点位置的图案
    template = np.full((40, 80, 3), 240, dtype=np.uint8)
    template[:, :] = (30, 120, 60)
    # 纯色模板 NCC 无意义，加纹理
    screen[150:190, 160:240, 0] = np.tile(np.arange(80) * 3, (40, 1))
    template[:, :, 0] = np.tile(np.arange(80) * 3, (40, 1))

    ok, score = match_template(screen, template, threshold=0.85)
    assert ok, f"位移后应命中，实际得分 {score:.3f}"
