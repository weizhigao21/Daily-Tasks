# -*- coding: utf-8 -*-
"""二维码解码：通道顺序、多趟放大、降级与静态守卫。

素材用 `qrcode` 现场生成（开发依赖，缺失则相关用例跳过）——比塞 PNG 素材
更好：能顺手造出小码、彩色码、反色码这些"真实屏幕上的恶劣情况"。
"""
import re
from pathlib import Path

import numpy as np
import pytest

from src.core import qrdecode
from src.core.qrdecode import QrHit, QrScanResult, decode_qr, to_gray

TEXT = "https://example.com/qr-test?abc=1"
SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


def make_qr(text: str = TEXT, box_size: int = 6, border: int = 2,
            fg=(0, 0, 0), bg=(255, 255, 255)) -> np.ndarray:
    """生成一张 RGB 二维码图（qrcode 缺失时跳过当前用例）。"""
    qrcode = pytest.importorskip("qrcode")
    qr = qrcode.QRCode(border=border, box_size=box_size,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make()
    mono = np.array(qr.make_image(fill_color="black", back_color="white").convert("RGB"))
    # 上色：把黑模块/白底各换成指定颜色（造彩色码用）
    dark = mono[:, :, 0:1] < 128
    return np.where(dark, np.array(fg, np.uint8), np.array(bg, np.uint8)).astype(np.uint8)


def resize_nearest(img: np.ndarray, side: int) -> np.ndarray:
    from PIL import Image

    return np.array(Image.fromarray(img).resize((side, side), Image.Resampling.NEAREST))


# ---------- 正常路径 ----------

def test_decodes_plain_qr():
    result = decode_qr(make_qr())
    assert result.ok
    assert result.hits[0].text == TEXT
    assert "QR" in result.hits[0].format.upper()


def test_reports_error_correction_level():
    """纠错级别会带回给用户看（识别吃力时能判断是不是码本身太弱）。"""
    assert decode_qr(make_qr()).hits[0].ec_level in ("L", "M", "Q", "H")


def test_small_qr_decodes_without_any_upscale():
    """小码直接解，**不做多尺度放大**。

    这是实测下来的结论，记在这里免得后人"顺手优化"回去再加几个尺度。
    素材是 198px 的码按真实观感（双线性）缩到各尺寸后测的：

    | 边  | 原尺寸 | ×2  | ×3  |
    |-----|--------|-----|-----|
    | 120 | ✓      | ✓   | ✓   |
    |  90 | ✓      | ✓   | ✗（轻模糊那组） |
    |  72 | ✓      | ✓   | ✓   |
    |  48 | ✓      | ✓   | ✓   |
    |  40 | ✗      | ✗   | ✗   |

    也就是说：**放大从未救回原尺寸解不出的码，只偶尔把能解的搞坏**；
    而 40px 以下怎么放大都解不出来（信息已经没了）。再加上模糊与二次采样
    三组共 30 个组合的实测，结论一致 —— 所以单趟就够了。
    """
    assert decode_qr(resize_nearest(make_qr(), 72)).hits[0].text == TEXT
    assert decode_qr(resize_nearest(make_qr(), 60)).hits[0].text == TEXT


def test_decodes_colored_qr():
    """蓝模块 + 黄底：亮度关系与黑白相反，是通道权重写错时最容易暴露的组合。"""
    colored = make_qr(fg=(0, 0, 255), bg=(255, 255, 0))
    assert decode_qr(colored).hits[0].text == TEXT


def test_decodes_inverted_qr():
    """白码黑底（深色主题截图里很常见），靠引擎的 try_invert 命中。"""
    assert decode_qr(255 - make_qr()).hits[0].text == TEXT


def test_multiple_codes_in_one_region():
    both = np.hstack([make_qr("AAA"), make_qr("BBB")])
    texts = [h.text for h in decode_qr(both).hits]
    assert set(texts) == {"AAA", "BBB"}


def test_accepts_grayscale_and_rgba_input():
    rgb = make_qr()
    assert decode_qr(to_gray(rgb)).hits[0].text == TEXT
    rgba = np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])
    assert decode_qr(rgba).hits[0].text == TEXT


# ---------- 通道顺序（本项目最容易踩的坑）----------

def test_gray_conversion_uses_rgb_channel_order():
    """送进引擎的必须是**按 RGB 权重**算好的灰度，不是原始彩色数组。

    zxing-cpp 的 numpy 入参约定是 BGR，本项目的截图是 RGB。直接把 RGB 数组
    丢进去会被它当成 BGR —— 彩色码的亮度顺序整个反过来。今天之所以"看着没事"，
    是它默认 try_invert=True 把反色结果又捞回来了，纯属巧合；换个引擎版本或者
    关掉 try_invert 就会变成随机失败。所以这里**钉死送进去的东西是什么**：
    把引擎换成一个只负责记录的假对象，直接检查它收到的数组。
    """
    rgb = np.array([[[10, 100, 200], [200, 100, 10]]], dtype=np.uint8)
    seen: list[np.ndarray] = []

    class _Recorder:
        class BarcodeFormat:      # noqa: N801 - 模拟引擎的枚举容器
            QRCode = 1

        @staticmethod
        def read_barcodes(image, **_kwargs):
            seen.append(np.asarray(image))
            return []

    qrdecode._engine = (_Recorder, _Recorder.BarcodeFormat.QRCode)
    try:
        decode_qr(rgb)
    finally:
        qrdecode._engine = None

    assert seen, "引擎一次都没被调用"
    for probe in seen:
        assert probe.ndim == 2, "必须传单通道灰度，彩色数组会带进 BGR/RGB 歧义"
        assert probe.dtype == np.uint8

    # ⚠️ 期望值必须**手算写死**，不能拿 `to_gray()` 反推 —— 那是同一个函数，
    # 权重被改错时两边一起变，测试照样绿（这版就是这么假绿过一次）。
    #   (10,100,200) → 0.299*10 + 0.587*100 + 0.114*200 =  84.49 →  84
    #   (200,100,10) → 0.299*200 + 0.587*100 + 0.114*10 = 119.64 → 119
    # 若误用 BGR 权重，这两个值会互换（119 / 84），直接就被抓住。
    assert np.array_equal(seen[0], np.array([[84, 119]], dtype=np.uint8)), \
        f"灰度不是按 RGB 权重算的：实际 {seen[0].tolist()}"


def test_to_gray_shape_handling():
    two_d = np.full((4, 4), 77, np.uint8)
    assert to_gray(two_d).shape == (4, 4)
    assert to_gray(np.full((4, 4, 3), 255, np.uint8)).shape == (4, 4)
    assert to_gray(np.full((4, 4, 1), 255, np.uint8)).shape == (4, 4)
    assert to_gray(np.full((4, 4, 4), 255, np.uint8)).shape == (4, 4)
    with pytest.raises(ValueError):
        to_gray(np.zeros((4, 4, 2), np.uint8))
    with pytest.raises(ValueError):
        to_gray(np.zeros((4,), np.uint8))


def test_pure_white_and_black_are_extremes():
    assert to_gray(np.full((2, 2, 3), 255, np.uint8)).min() == 255
    assert to_gray(np.full((2, 2, 3), 0, np.uint8)).max() == 0


# ---------- 单趟策略（实测结论，别再"优化"回去）----------

def test_decodes_in_a_single_pass():
    """钉死"只跑一趟"这个实测结论 —— 重新引入多尺度会被这条挡下。"""
    seen: list[np.ndarray] = []

    class _Recorder:
        class BarcodeFormat:      # noqa: N801
            QRCode = 1

        @staticmethod
        def read_barcodes(image, **_kwargs):
            seen.append(np.asarray(image))
            return []

    qrdecode._engine = (_Recorder, _Recorder.BarcodeFormat.QRCode)
    try:
        decode_qr(make_qr())
    finally:
        qrdecode._engine = None
    assert len(seen) == 1


def test_engine_failure_is_reported_as_error_not_as_no_hit():
    """引擎抛异常要和"没找到码"分开报。

    后者界面会说"请框得更贴合"，而真实原因是图坏了或引擎出问题 —— 那句提示
    会把用户带偏，让他反复重框一个根本解不开的东西。
    """
    class _Broken:
        class BarcodeFormat:      # noqa: N801
            QRCode = 1

        @staticmethod
        def read_barcodes(_image, **_kwargs):
            raise RuntimeError("引擎内部错误")

    qrdecode._engine = (_Broken, _Broken.BarcodeFormat.QRCode)
    try:
        result = decode_qr(make_qr())
    finally:
        qrdecode._engine = None
    assert not result.ok
    assert "解码失败" in result.error


def test_deduplicates_identical_codes_in_one_image():
    """同一张图里出现两个一模一样的码（页面重复贴同一个链接），只报一次。"""
    same = make_qr("SAME")
    assert [h.text for h in decode_qr(np.hstack([same, same])).hits] == ["SAME"]


def test_falls_back_to_raw_bytes_when_text_is_empty():
    """个别码只给原始字节，不能因此把内容丢了。"""
    class _BytesOnly:
        class BarcodeFormat:      # noqa: N801
            QRCode = 1

        class _Barcode:
            text = ""
            format = "QR Code"
            ec_level = ""
            bytes = "中文内容".encode()

        @staticmethod
        def read_barcodes(_image, **_kwargs):
            return [_BytesOnly._Barcode()]

    qrdecode._engine = (_BytesOnly, _BytesOnly.BarcodeFormat.QRCode)
    try:
        result = decode_qr(np.zeros((60, 60, 3), np.uint8))
    finally:
        qrdecode._engine = None
    assert result.hits[0].text == "中文内容"


# ---------- 失败与降级 ----------

def test_noise_yields_no_hits_without_error():
    """没找到码不是错误 —— 界面文案与"组件缺失"必须能区分开。"""
    noise = (np.random.default_rng(0).random((200, 200, 3)) * 255).astype(np.uint8)
    result = decode_qr(noise)
    assert result.hits == []
    assert result.error == ""
    assert not result.ok


def test_empty_region_reports_error():
    assert "空" in decode_qr(np.zeros((0, 0, 3), np.uint8)).error


def test_unknown_shape_reports_error():
    assert decode_qr(np.zeros((10, 10, 2), np.uint8)).error


def test_missing_engine_degrades_with_message(monkeypatch):
    """没装 zxing-cpp 时只该"识别不了"，不该把程序带崩。"""
    def _boom():
        raise ImportError("No module named 'zxingcpp'")

    monkeypatch.setattr(qrdecode, "_load_engine", _boom)
    result = decode_qr(make_qr())
    assert not result.ok
    assert "zxing-cpp" in result.error


def test_result_defaults_are_empty():
    result = QrScanResult()
    assert result.hits == [] and result.error == "" and not result.ok
    assert QrScanResult(hits=[QrHit("x")]).ok


# ---------- 静态守卫 ----------

@pytest.mark.parametrize("filename", ["qrdecode.py", "hotkeys.py"])
def test_core_layers_do_not_import_qt(filename):
    """判定/算法层不得引入 Qt、也不得引入 OpenCV。

    引入 Qt 会让这些纯逻辑无法离屏覆盖；引入 OpenCV 违反"运行时不依赖
    OpenCV"的硬约定（cv2 只在测试里做交叉校验）。两者都在源码里写明白过，
    这里钉死防止后来者顺手 import。
    """
    src = (SRC_ROOT / "core" / filename).read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    for banned in ("PySide6", "PyQt", "cv2", "opencv"):
        assert not re.search(rf"\b{banned}\b", code), \
            f"{filename} 不得依赖 {banned}"


def test_formats_restricts_to_qr_only():
    """解码范围收在二维码上：混着条形码的区域不该冒出别的结果。

    要放开就改 `_FORMATS` 这一个元组（模块里唯一的调节点）。
    """
    assert qrdecode._FORMATS == ("QRCode",)
