# -*- coding: utf-8 -*-
"""验证链：截屏 → 模板匹配 → 判定，含调试截图落盘。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .. import config
from .matcher import load_template, match_template, save_image
from .models import Task
from .scheduler import in_time_window


@dataclass
class VerifyResult:
    ok: bool
    score: float
    message: str


def capture_region(region: tuple[int, int, int, int]) -> np.ndarray:
    """用 mss 截取屏幕区域（物理像素），返回 RGB ndarray。"""
    from .screen import open_screen

    x, y, w, h = (int(v) for v in region)
    with open_screen() as sct:
        raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
        # mss BGRA -> RGB
        arr = np.frombuffer(raw.rgb, dtype=np.uint8).reshape((h, w, 3))
        return arr.copy()


def capture_template(region, save_path: str) -> bool:
    """截取区域并保存为目标模板图。"""
    img = capture_region(region)
    return save_image(save_path, img)


def _expand_region(
    region: tuple[int, int, int, int], mon: dict
) -> tuple[int, int, int, int]:
    """把框选区域向四周扩展 VERIFY_SEARCH_MARGIN_PX，钳制到虚拟屏幕内。

    region 语义是"目标锚点"，搜索时容忍目标在锚点附近小幅位移。
    扩展结果不包含原始区域或完全在屏幕外时，回退原区域。
    """
    x, y, w, h = (int(v) for v in region)
    m = config.VERIFY_SEARCH_MARGIN_PX
    left = max(int(mon["left"]), x - m)
    top = max(int(mon["top"]), y - m)
    right = min(int(mon["left"]) + int(mon["width"]), x + w + m)
    bottom = min(int(mon["top"]) + int(mon["height"]), y + h + m)
    contained = left <= x and top <= y and right >= x + w and bottom >= y + h
    on_screen = (
        right > int(mon["left"])
        and bottom > int(mon["top"])
        and left < int(mon["left"]) + int(mon["width"])
        and top < int(mon["top"]) + int(mon["height"])
    )
    if not (contained and on_screen):
        return x, y, w, h
    return left, top, right - left, bottom - top


def _virtual_screen() -> dict:
    """虚拟桌面边界，mss 不可用时给一个足够大的默认值。"""
    try:
        from .screen import open_screen

        with open_screen() as sct:
            return dict(sct.monitors[0])
    except Exception:
        return {"left": -100000, "top": -100000, "width": 200000, "height": 200000}


def _resolve_search_region(
    task_region: tuple[int, int, int, int], mon: dict
) -> tuple[int, int, int, int]:
    """根据配置决定搜索范围：全屏 或 锚点扩展区域。"""
    if config.VERIFY_SEARCH_FULL_SCREEN:
        return (
            int(mon["left"]), int(mon["top"]),
            int(mon["width"]), int(mon["height"]),
        )
    return _expand_region(task_region, mon)


def verify_task(task: Task) -> VerifyResult:
    """完整验证：时间窗口 + 模板匹配。

    搜索范围由 config.VERIFY_SEARCH_FULL_SCREEN 决定：
    - True：截取整个虚拟桌面，目标在屏幕任何位置都能识别（~1s）
    - False：只截取框选区域±VERIFY_SEARCH_MARGIN_PX（快、更不易误判）
    """
    if not task.is_image_verify:
        return VerifyResult(False, 0.0, "该任务未配置图片验证")
    if not in_time_window(task):
        return VerifyResult(
            False, 0.0,
            f"不在验证时间窗口内（{task.time_start}~{task.time_end}）",
        )
    tmpl_path = config.resolve_template(task.template_path)
    tmpl = load_template(str(tmpl_path)) if tmpl_path else None
    if tmpl is None:
        return VerifyResult(False, 0.0, f"模板图读取失败: {task.template_path}")
    try:
        region = _resolve_search_region(task.region, _virtual_screen())
        screen = capture_region(region)
    except Exception as exc:
        return VerifyResult(False, 0.0, f"截屏失败: {exc}")

    ok, score = match_template(
        screen, tmpl, threshold=task.threshold, scales=config.MATCH_SCALES
    )
    if not ok:
        _save_debug_shot(task, screen, score)
        scope = "全屏" if config.VERIFY_SEARCH_FULL_SCREEN else \
            f"区域±{config.VERIFY_SEARCH_MARGIN_PX}px"
        return VerifyResult(
            False, score,
            f"未匹配到目标画面（相似度 {score:.2f} < {task.threshold:.2f}，"
            f"搜索范围={scope}），截图已存 debug 目录",
        )
    return VerifyResult(True, score, f"匹配成功（相似度 {score:.2f}）")


def _save_debug_shot(task: Task, screen: np.ndarray, score: float) -> None:
    """失败时保存截图，便于调整区域坐标 / 模板 / 阈值。

    全屏模式下截图很大，缩放到最大 1600px 宽再落盘。
    """
    try:
        config.ensure_dirs()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in task.name if c.isalnum() or c in "_-")[:20] or "task"
        path = str(config.DEBUG_DIR / f"{safe_name}_{ts}_s{score:.2f}.png")
        max_w = 1600
        if screen.shape[1] > max_w:
            from PIL import Image

            h = int(screen.shape[0] * max_w / screen.shape[1])
            im = Image.fromarray(screen).resize((max_w, h), Image.Resampling.BILINEAR)
            im.save(path)
        else:
            save_image(path, screen)
    except Exception:
        pass
