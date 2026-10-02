# -*- coding: utf-8 -*-
"""全屏定格截图 + 框选：整个画面冻结为静态图并压暗，拖拽框选目标区域。

返回：
- QRect：物理像素坐标（虚拟桌面坐标系，与 mss 一致），可直接存入任务 region
- ndarray：框选区域的 RGB 图像（从定格帧中裁出），可直接作模板图 / 解码素材

两个调用场景（任务模板、二维码识别）都走 `pick_region_hiding_app()`：定格帧是
从屏幕上抓的，本程序自己的面板永远置顶，不先藏起来就会连自己一起被拍进去。
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QEventLoop, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

_DIM_COLOR = QColor(0, 0, 0, 115)  # 全屏压暗罩

# 藏掉窗口之后要等合成器把画面刷新出来，否则定格帧里还留着刚隐藏的窗口残影
_COMPOSITOR_SETTLE_S = 0.3


def _grab_screen_np(screen) -> tuple[np.ndarray, dict]:
    """mss 定格指定屏幕，返回 (RGB ndarray, monitor dict)。物理像素。"""
    from ..core.screen import open_screen

    dpr = screen.devicePixelRatio() or 1.0
    geo = screen.geometry()
    px, py = int(geo.x() * dpr), int(geo.y() * dpr)
    pw, ph = int(geo.width() * dpr), int(geo.height() * dpr)
    with open_screen() as sct:
        mon = {"left": px, "top": py, "width": pw, "height": ph}
        raw = sct.grab(mon)
        arr = np.frombuffer(raw.rgb, dtype=np.uint8).reshape((ph, pw, 3))
        return arr.copy(), mon  # RGB


def _np_to_pixmap(img: np.ndarray, dpr: float) -> QPixmap:
    h, w, ch = img.shape
    qimg = QImage(img.data, w, h, ch * w, QImage.Format.Format_RGB888)
    pm = QPixmap.fromImage(qimg)
    pm.setDevicePixelRatio(dpr)
    return pm


class RegionPicker(QWidget):
    """全屏定格遮罩：按下拖拽框选，松开确认，Esc 取消。"""

    # 物理像素 QRect（虚拟桌面坐标） + 框选区域 RGB ndarray
    selected = Signal(QRect, object)
    # 取消（Esc / 无效框选）。必须显式 emit：picker 无父对象且未设
    # WA_DeleteOnClose，close() 只隐藏、不销毁，destroyed 永远不会来——
    # 少了这个信号，pick_region_with_capture 的事件循环就永远退不出去。
    cancelled = Signal()

    def __init__(self, hint: str = "") -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._hint = hint
        self._origin = None
        self._current = None

        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self._dpr = (screen.devicePixelRatio() if screen else 1.0) or 1.0
        self._img, self._mon = _grab_screen_np(screen)
        self._pix = _np_to_pixmap(self._img, self._dpr)
        self.setGeometry(screen.geometry())

    def _draw_frame(self, p: QPainter) -> None:
        src = QRect(0, 0, int(self.width() * self._dpr), int(self.height() * self._dpr))
        p.drawPixmap(self.rect(), self._pix, src)

    def _draw_hint(self, p: QPainter) -> None:
        """底部居中的操作提示。

        框选是全屏模态的，用户此时最容易困惑的是"怎么取消、框完会怎样"，
        所以把操作说明直接画在画面上，而不是指望他去翻文档。
        """
        if not self._hint:
            return
        f = p.font()
        f.setPixelSize(15)
        f.setBold(False)
        p.setFont(f)
        metrics = QFontMetrics(p.font())
        pad_x, pad_y = 18, 10
        box = QRectF(
            (self.width() - metrics.horizontalAdvance(self._hint)) / 2 - pad_x,
            self.height() - 84,
            metrics.horizontalAdvance(self._hint) + pad_x * 2,
            metrics.height() + pad_y * 2,
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QPen(QColor(255, 255, 255, 235)))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, self._hint)

    # ---------- 绘制 ----------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        self._draw_frame(p)                      # 定格画面
        p.fillRect(self.rect(), _DIM_COLOR)      # 全屏压暗
        if self._origin and self._current:
            rect = QRect(self._origin, self._current).normalized()
            p.save()
            p.setClipRect(rect)                  # 框内恢复原亮度
            self._draw_frame(p)
            p.restore()
            p.setPen(QPen(Qt.GlobalColor.green, 2))
            p.drawRect(rect)
            f = p.font()
            f.setPixelSize(14)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QPen(Qt.GlobalColor.green))
            label = f"{rect.width()} × {rect.height()}"
            ty = rect.y() - 8 if rect.y() > 24 else rect.bottom() + 20
            p.drawText(rect.x(), ty, label)
        self._draw_hint(p)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        rect = QRect(self._origin, event.position().toPoint()).normalized()
        if rect.width() < 4 or rect.height() < 4:
            # 无效框选（普通单击）按取消处理：必须 emit 再 close，
            # 否则 pick_region_with_capture 的 loop 永远退不出去（见 cancelled 注释）
            self.cancelled.emit()
            self.close()
            return
        dpr = self._dpr
        # 本地逻辑像素 -> 虚拟桌面物理像素
        phys = QRect(
            int((self.geometry().x() + rect.x()) * dpr),
            int((self.geometry().y() + rect.y()) * dpr),
            int(rect.width() * dpr),
            int(rect.height() * dpr),
        )
        # 从定格帧裁剪（monitor 局部坐标）
        x0 = phys.x() - self._mon["left"]
        y0 = phys.y() - self._mon["top"]
        crop = self._img[y0:y0 + phys.height(), x0:x0 + phys.width()].copy()
        self.close()
        self.selected.emit(phys, crop)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            # 同 mouseReleaseEvent 的无效框选路径：emit 是退出事件循环的唯一凭据
            self.cancelled.emit()
            self.close()


def pick_region_with_capture(hint: str = "") -> tuple[QRect, np.ndarray] | None:
    """定格全屏 → 拖框 → 返回 (物理像素 QRect, RGB ndarray)；Esc 取消返回 None。"""
    result: dict = {}
    loop = QEventLoop()
    picker = RegionPicker(hint)

    def _on_selected(rect: QRect, crop) -> None:
        result["rect"] = rect
        result["crop"] = crop
        loop.quit()

    picker.selected.connect(_on_selected)
    # 取消路径：picker 无父对象、未设 WA_DeleteOnClose，close() 只隐藏不销毁，
    # destroyed 永远不来 —— 退出循环只能靠这个显式信号（Esc/无效框选都走它）。
    picker.cancelled.connect(loop.quit)
    picker.destroyed.connect(loop.quit)
    picker.show()
    picker.raise_()
    picker.activateWindow()
    loop.exec()
    if "rect" not in result:
        return None
    return result["rect"], result["crop"]


def pick_region_hiding_app(
    hint: str = "", restore: QWidget | None = None
) -> tuple[QRect, np.ndarray] | None:
    """藏掉本程序所有窗口 → 定格框选 → 把窗口放回来。

    定格帧是从屏幕上抓的，而本程序的面板是置顶窗口，不藏起来就会出现在
    冻结画面里 —— 用户就有可能在框选区域里把自己的面板拍进去。

    抽成共用函数是因为"任务验证区域"和"二维码识别"两条路径都要走这一套，
    各写一遍必然漏掉 `processEvents` 或等待合成器那一拍。

    `restore` 传"要用完之后抢回焦点的那个窗口"（如任务编辑框）；不传则
    原样把之前可见的窗口都放回来，不动焦点。
    """
    app = QApplication.instance()
    visible = [w for w in app.topLevelWidgets() if w.isVisible()]
    for w in visible:
        w.hide()
    app.processEvents()
    time.sleep(_COMPOSITOR_SETTLE_S)   # 等合成器刷新，确保窗口从画面上消失
    try:
        return pick_region_with_capture(hint)
    finally:
        for w in visible:
            if w is not restore:
                w.show()
        if restore is not None:
            restore.show()
            restore.raise_()
            restore.activateWindow()
