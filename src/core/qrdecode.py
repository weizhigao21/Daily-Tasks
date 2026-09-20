# -*- coding: utf-8 -*-
"""二维码解码：从**屏幕框选的 RGB 图**里读出二维码内容。

分工：本模块只做"图 → 文本"，不碰截屏、不碰 UI、不碰 Qt —— 判定逻辑保持
纯函数，离屏测试才能覆盖到（同 `ui/fullscreen.py` / `ui/occlusion.py` 的约定，
`test_hotkeys.py::test_core_layers_do_not_import_qt` 有静态守卫）。

后端是 **zxing-cpp**（纯 wheel，无外部 DLL）。选它而不是 pyzbar / OpenCV：
- pyzbar 靠 ctypes 加载 zbar 动态库，打包时得手动塞 DLL，且对旋转/畸变容错一般；
- OpenCV 违反本项目"运行时不依赖 OpenCV"的硬约定（见 tests/test_screen.py）。

⚠️ **通道顺序是这里最容易踩的坑**：zxing-cpp 的 numpy 入参约定是 **BGR**，
而本项目所有截屏路径（`region_picker` / `verifier.capture_region`）产出的都是
**RGB**。把 RGB 数组直接丢进去，zxing 会把 R 当 B、B 当 R，彩色码的亮度顺序
可能整个反过来 —— 今天之所以"看着没事"，是因为它默认 `try_invert=True`
把反色的结果又捞了回来，纯属巧合。所以这里统一先转**灰度**再传：
既彻底消除通道歧义，又让它少算两遍通道。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# 解码范围唯一调节点：当前只解二维码。框选区域里若混着条形码，放开成 None
# 会连它们一起解出来，未必是用户想要的；要扩展改这一个元组即可。
_FORMATS = ("QRCode",)

# Rec.601 亮度权重，按 **RGB** 顺序取（入参约定就是 RGB，见模块说明）。
# 自己算灰度而不是交给 zxing：它那条 numpy 路径要 BGR，我们这条路径是 RGB。
_GRAY_W = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# 惰性缓存的引擎句柄：见 _load_engine()
_engine: tuple | None = None


@dataclass(frozen=True)
class QrHit:
    """一条解码结果。"""
    text: str
    format: str = "QR Code"
    ec_level: str = ""      # 纠错级别 L/M/Q/H，只有部分码会给


@dataclass
class QrScanResult:
    """解码结果：`hits` 为空时看 `error`（空串仅表示"没找到码"）。"""
    hits: list[QrHit] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.hits)


def _load_engine():
    """惰性导入 zxing-cpp，返回 (模块, formats)。

    刻意不在模块顶层 import：万一打包时漏了这个扩展模块，应该只有"二维码
    识别"这一个功能不可用，而不是整个程序起不来 —— 与玻璃后端、pynput
    热键的降级策略保持一致。
    """
    global _engine
    if _engine is None:
        import zxingcpp

        formats = None
        for name in _FORMATS:
            one = getattr(zxingcpp.BarcodeFormat, name)
            formats = one if formats is None else (formats | one)
        _engine = (zxingcpp, formats)
    return _engine


def to_gray(img: np.ndarray) -> np.ndarray:
    """截图数组 → uint8 单通道灰度。

    接受 RGB / RGBA / 单通道三种形态，**不做通道反转**：入参就是 RGB，
    权重按 RGB 顺序取。形状不认识时抛 ValueError，由调用方转成可读的错误。
    """
    arr = np.asarray(img)
    if arr.ndim == 3:
        if arr.shape[2] >= 3:
            weighted = arr[:, :, :3].astype(np.float32) @ _GRAY_W
            return np.clip(weighted, 0, 255).astype(np.uint8)
        if arr.shape[2] == 1:
            return np.clip(arr[:, :, 0], 0, 255).astype(np.uint8)
        raise ValueError(f"无法识别的图像形状: {arr.shape}")
    if arr.ndim != 2:
        raise ValueError(f"无法识别的图像形状: {arr.shape}")
    return np.clip(arr, 0, 255).astype(np.uint8)


def _text_of(barcode) -> str:
    """取文本；极少数码只给原始字节，退回按 UTF-8 尽力解（坏字节替换掉）。"""
    text = barcode.text or ""
    if text:
        return text
    raw = getattr(barcode, "bytes", None)
    if raw is None:
        return ""
    return bytes(raw).decode("utf-8", "replace")


def decode_qr(img: np.ndarray) -> QrScanResult:
    """解码框选区域里的二维码。只跑**一趟**。

    刻意不做"缩小/放大几个尺度各试一遍"（模板匹配那边就是这么干的，容易顺手
    复制过来）。实测结论：把图放大 ×2/×3 再解，**"原尺寸解不出、放大后能解出"
    的情况一次都没出现**（双线性缩放、高斯模糊、二次采样三组共 30 个组合），
    反而在 90px + 轻模糊那组把原本能解的 ×3 解坏了。屏幕上的二维码只要人愿意
    去框它，尺寸基本在 60px 以上，远超引擎的下限（实测临界在 40~48px 之间）。
    多跑几趟纯粹是白花时间。实测表在 tests/test_qrdecode.py。

    反色的码（白码黑底）交给引擎自己的 `try_invert`，不自己再翻一遍。
    """
    try:
        zxingcpp, formats = _load_engine()
    except Exception as exc:                                  # pragma: no cover
        return QrScanResult(error=f"缺少二维码解码组件 zxing-cpp：{exc}")
    try:
        gray = to_gray(img)
    except ValueError as exc:
        return QrScanResult(error=str(exc))
    if gray.size == 0:
        return QrScanResult(error="框选区域为空")
    try:
        found = zxingcpp.read_barcodes(
            gray,
            formats=formats,
            try_rotate=True,      # 歪着拍的、旋转 90° 的交给引擎
            try_downscale=True,   # 大图内部降采样扫，屏幕截图动辄上千像素
            try_invert=True,      # 白码黑底（深色主题里很常见）
        )
    except Exception as exc:
        # 引擎炸了要和"没找到码"分开报：后者会显示成"请框得更贴合"，
        # 而真实原因是图坏了 / 引擎出问题，那句提示会把人带偏。
        return QrScanResult(error=f"解码失败：{exc}")

    hits: list[QrHit] = []
    seen: set[str] = set()
    for barcode in found:
        text = _text_of(barcode)
        # 同一张图里出现两个一模一样的码（比如页面上重复贴了同一个链接）时只报一次
        if not text or text in seen:
            continue
        seen.add(text)
        hits.append(QrHit(
            text=text,
            format=str(getattr(barcode, "format", "") or "QR Code"),
            ec_level=str(getattr(barcode, "ec_level", "") or ""),
        ))
    return QrScanResult(hits=hits)
