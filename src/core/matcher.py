# -*- coding: utf-8 -*-
"""模板匹配（纯 numpy FFT 实现，无 OpenCV 依赖）+ 图片存取（Pillow）。

等价于 cv2.matchTemplate(TM_CCOEFF_NORMED)：去均值归一化互相关。
图像统一使用 RGB ndarray；灰度权重与 OpenCV 一致（0.299R+0.587G+0.114B）。

性能设计（面向全屏 3440x1440 级别搜索）：
- 屏幕的 Fg 频谱只算一次，所有尺度复用（旧实现每尺度重算，全屏 6.6s）；
- 窗口和 / 窗口平方和用积分图（O(1) 查询），不走 FFT；
- 每尺度只剩 1 次小模板 FFT + 1 次整屏 irfft2；
- 尺度 1.0 优先尝试，得分达阈值立即返回（命中场景 ~0.6s，未命中 ~1.9s）。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# 尺度搜索：轻微容忍 DPI / 窗口缩放差异（1.0 优先，命中即提前退出）
# 仅作为调用方未显式传 scales 时的兜底；运行时实际序列来自 config.MATCH_SCALES。
_DEFAULT_SCALES = (1.0, 0.95, 1.05, 0.9, 1.1)

_EPS = 1e-6


_GRAY_W = np.array([0.299, 0.587, 0.114], dtype=np.float64)


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    """RGB -> 灰度（权重与 OpenCV BGR2GRAY 一致）。

    用单次矩阵乘法而不是三次 astype+乘加：省掉 3 个全屏 float64 临时数组，
    3440x1440 实测 77ms -> 39ms。
    """
    return rgb.astype(np.float64) @ _GRAY_W


def _integral(a: np.ndarray) -> np.ndarray:
    """积分图（(H+1, W+1)，首行首列 0），支持 O(1) 任意矩形和。"""
    ii = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    np.cumsum(np.cumsum(a, axis=0), axis=1, out=ii[1:, 1:])
    return ii


def _window_sums(ii: np.ndarray, th: int, tw: int, H: int, W: int) -> np.ndarray:
    """积分图上取所有 th x tw 窗口的元素和，返回 (H-th+1, W-tw+1)。"""
    return (
        ii[th : H + 1, tw : W + 1]
        - ii[0 : H - th + 1, tw : W + 1]
        - ii[th : H + 1, 0 : W - tw + 1]
        + ii[0 : H - th + 1, 0 : W - tw + 1]
    )


def _ncc_scores(
    gray: np.ndarray,
    tmpl_gray: np.ndarray,
    _pre: tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int]] | None = None,
) -> np.ndarray:
    """TM_CCOEFF_NORMED 有效区得分矩阵。

    _pre: 可选预计算 (Fg, ii1, ii2, pad_hw)，多次调用同一屏幕时复用。
    """
    th, tw = tmpl_gray.shape
    H, W = gray.shape
    if th > H or tw > W:
        return np.zeros((1, 1))

    T0 = tmpl_gray - tmpl_gray.mean()
    tss = float((T0 * T0).sum())
    n = th * tw

    if tss <= _EPS:
        # 常量模板：NCC 无定义，退化为均值比对（纯色块=窗口纯色均值相等即命中）
        ii1 = _pre[1] if _pre is not None else _integral(gray)
        wmean = _window_sums(ii1, th, tw, H, W) / n
        return (np.abs(wmean - float(tmpl_gray.mean())) < 0.5).astype(np.float64)

    # 零填充到线性卷积所需尺寸（防循环绕回）；有预计算时用其统一尺寸
    if _pre is not None:
        Fg, ii1, ii2, pad_hw = _pre
    else:
        pad_hw = (H + th - 1, W + tw - 1)
        Fg = np.fft.rfft2(gray, pad_hw)
        ii1, ii2 = _integral(gray), _integral(gray * gray)

    Ft = np.fft.rfft2(T0[::-1, ::-1], pad_hw)
    corr = np.fft.irfft2(Fg * Ft, pad_hw)[th - 1 : H, tw - 1 : W]
    wsum = _window_sums(ii1, th, tw, H, W)
    wsum2 = _window_sums(ii2, th, tw, H, W)

    var = wsum2 - wsum * wsum / n
    var = np.maximum(var, 0.0)  # 浮点误差导致的小负数
    denom = np.sqrt(tss * var)
    scores = np.where(denom > _EPS, corr / np.maximum(denom, _EPS), 0.0)
    return np.clip(scores, -1.0, 1.0)


def match_template(
    screen_rgb: np.ndarray,
    template_rgb: np.ndarray,
    threshold: float = 0.85,
    scales: tuple[float, ...] = _DEFAULT_SCALES,
) -> tuple[bool, float]:
    """在截屏中查找模板。返回 (是否命中, 最佳相似度)。多尺度取最大值。

    尺度按 1.0 优先排序，任一尺度达阈值立即返回（避免全尺度搜索的耗时）。
    """
    if screen_rgb is None or template_rgb is None:
        return False, 0.0
    screen_gray = _to_gray(screen_rgb)
    tmpl_gray = _to_gray(template_rgb)
    H, W = screen_gray.shape
    th, tw = tmpl_gray.shape
    if th > H or tw > W:
        return False, 0.0

    order = sorted(scales, key=lambda s: s != 1.0)  # 1.0 优先
    max_w = max(4, int(tw * max(order)))
    max_h = max(4, int(th * max(order)))
    pad_hw = (H + max_h - 1, W + max_w - 1)

    # 屏幕频谱与积分图只算一次，所有尺度复用
    Fg = np.fft.rfft2(screen_gray, pad_hw)
    ii1, ii2 = _integral(screen_gray), _integral(screen_gray * screen_gray)
    pre = (Fg, ii1, ii2, pad_hw)

    best_score = -1.0
    for scale in order:
        new_w, new_h = max(4, int(tw * scale)), max(4, int(th * scale))
        # 模板大于截屏时跳过该尺度（相等尺寸是合法场景：框选区域=模板）
        if new_w > W or new_h > H:
            continue
        if scale == 1.0:
            scaled = tmpl_gray
        else:
            img = Image.fromarray(tmpl_gray)
            scaled = np.asarray(
                img.resize((new_w, new_h), Image.Resampling.BOX), dtype=np.float64
            )
        best_score = max(
            best_score, float(_ncc_scores(screen_gray, scaled, _pre=pre).max())
        )
        if best_score >= threshold:
            break  # 提前退出：已确定命中
    return best_score >= threshold, max(best_score, 0.0)


def load_template(path: str) -> np.ndarray | None:
    """读取模板图（RGB），失败返回 None（中文路径安全）。"""
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"))
    except Exception:
        return None


def save_image(path: str, img_rgb: np.ndarray) -> bool:
    """保存图像（Pillow，中文路径安全）。"""
    try:
        Image.fromarray(img_rgb).save(path)
        return True
    except Exception:
        return False
