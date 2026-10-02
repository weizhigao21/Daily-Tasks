# -*- coding: utf-8 -*-
"""二维码识别结果窗。

结果**已经在构造时进剪贴板**了：扫码这个动作里，用户十有八九是要去别处粘贴，
让"扫完还得再点一下复制"是白白多一步。窗口里给一行提示说明已复制，同时留着
复制按钮，供用户切换了内容、或想再复制时用。
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.qrdecode import QrHit
from . import theme, window_state
from .combo import ThemedComboBox
from .glass import GlassDialogMixin

# 可"打开链接"的内容。只认明确的协议或 www. 开头——扫码结果里混着大段文本时，
# 不该因为文里出现 "http" 就冒出一个打开按钮。
_URL_RE = re.compile(r"^(?:https?|ftp)://\S+$|^www\.\S+$", re.IGNORECASE)


def is_openable_url(text: str) -> bool:
    return bool(_URL_RE.match((text or "").strip()))


def _browse_url(text: str) -> QUrl:
    """拿这个内容去开浏览器。`www.` 开头的得补协议，否则浏览器会当成相对路径。"""
    text = text.strip()
    if text.lower().startswith("www."):
        return QUrl("https://" + text)
    return QUrl(text)


def open_url_in_browser(text: str) -> bool:
    """把识别到的内容交给系统浏览器打开，返回是否递交成功。

    抽出来是为了让"扫码直开"与"结果窗的按钮"共用同一套规则 —— 补 `https://`
    的逻辑只有这一处，各写一遍迟早分叉。

    ⚠️ 调用方必须看返回值：`openUrl` 失败时界面若不出声，用户看到的就是
    "扫完什么都没发生"，会当成程序卡了。（内容不是链接时同样返回 False。）
    """
    if not is_openable_url(text):
        return False
    return QDesktopServices.openUrl(_browse_url(text))


class QrResultDialog(GlassDialogMixin, QDialog):
    """展示解码结果：自动复制 + 可选切换 + 打开链接。

    两个行为由托盘「二维码」子菜单控制，构造时传进来：`autocopy`（是否自动
    写剪贴板）与 `open_link`（网址是否给「打开链接」按钮）。默认都开。

    刻意不做模态（调用方用 `show()`）：扫码期间要隐藏本程序所有窗口才能拍到
    干净的画面，而**模态循环一旦被 hide 就会终止**（见 taskbar 里那条
    "对话框禁用 exec" 的约定）。这个窗口会经历被隐藏再显示，所以不能用 exec。
    """

    def __init__(self, hits: list[QrHit], parent=None, *,
                 autocopy: bool = True, open_link: bool = True) -> None:
        super().__init__(parent)
        self.hits = list(hits)
        # ⚠️ 这两个标记必须在 `_select()` 之前设好——首次选中会读它们来决定
        # 要不要复制、要不要给按钮，设晚了第一次就按默认值走。
        self._autocopy = bool(autocopy)
        self._link_allowed = bool(open_link)
        self.setWindowTitle("二维码识别")
        self.setMinimumWidth(420)
        # 毛玻璃 + 自动降级 + 记住窗口位置（db 从父窗口借，单测里裸建则自动关闭）
        self.setup_glass(db=getattr(parent, "db", None),
                         state_key=window_state.WINDOW_QR_RESULT)
        self._build()
        self._select(0)          # 顺带完成首次复制（自动复制关掉时则只显示内容）

    # ---------- UI ----------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SP_4, theme.SP_4, theme.SP_4, theme.SP_4)
        layout.setSpacing(theme.SP_3)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.fs(theme.FS_XS)}px;")
        layout.addWidget(self.status)

        self.chooser = ThemedComboBox()
        for i, hit in enumerate(self.hits):
            self.chooser.addItem(f"第 {i + 1} 个 · {_preview(hit.text)}", i)
        # 只有一个码时不给选择器，省得把简单事情说复杂。
        # ⚠️ "有没有多个码"用显式标志记着，**不要**去读 chooser.isVisible()：
        # 对话框 show() 之前 isVisible() 恒为 False，拿它当业务状态会导致
        # "多码时切换下拉框没反应"这种只在显示后才暴露的错。
        self._multi = len(self.hits) > 1
        self.chooser.setVisible(self._multi)
        self.chooser.currentIndexChanged.connect(
            lambda _=0: self._select(self.chooser.currentIndex()))
        layout.addWidget(self.chooser)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(96)
        # 内容多为 URL / 长串，允许横滚比强行折行更好读
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SP_2)
        self.open_btn = QPushButton("打开链接")
        self.open_btn.setObjectName("ghostBtn")
        self.open_btn.clicked.connect(self._open_link)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("primaryBtn")
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("ghostBtn")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    # ---------- 行为 ----------
    def current(self) -> QrHit | None:
        idx = self.chooser.currentIndex() if self._multi else 0
        if 0 <= idx < len(self.hits):
            return self.hits[idx]
        return self.hits[0] if self.hits else None

    def _select(self, index: int) -> None:
        hit = self.hits[index] if 0 <= index < len(self.hits) else None
        if hit is None:
            return
        self.text.setPlainText(hit.text)
        # 「打开链接」是**开关与内容的双重条件**：开关关掉时，哪怕内容是网址也不给按钮
        self.open_btn.setVisible(self._link_allowed and is_openable_url(hit.text))
        self._set_status("已复制到剪贴板" if self._autocopy else "识别完成（未自动复制）")
        if self._autocopy:
            self.copy_current()      # 每次切换都顺手复制，符合"扫完就要粘贴"的用法

    def _set_status(self, head: str) -> None:
        """状态行 = 一句话结论 + 当前内容的元信息。

        元信息从 `current()` 现推，不另存一份——本项目在"状态各存一份然后漂移"
        上栽过，能推导出来的东西就不要缓存。
        """
        hit = self.current()
        if hit is None:
            return
        level = f" · 纠错 {hit.ec_level}" if hit.ec_level else ""
        total = f"（共 {len(self.hits)} 个，可在上方切换）" if self._multi else ""
        self.status.setText(f"{head} · {hit.format}{level}{total}")

    def _on_copy_clicked(self) -> None:
        """手动复制。自动复制关掉时状态行要跟着改，否则点完没有任何反馈。"""
        if self.copy_current() and not self._autocopy:
            self._set_status("已复制到剪贴板")

    def copy_current(self) -> str:
        """把当前结果写进剪贴板，返回写进去的文本（空内容不覆盖剪贴板）。"""
        hit = self.current()
        if hit is None or not hit.text:
            return ""
        QApplication.clipboard().setText(hit.text)
        return hit.text

    def _open_link(self) -> None:
        hit = self.current()
        if hit is None or not self._link_allowed or not is_openable_url(hit.text):
            return
        if not open_url_in_browser(hit.text):
            # 走到这儿说明内容**是**链接、但系统没能把它交出去（没装默认浏览器
            # 之类）。必须说话——否则用户点了按钮界面毫无反应，只会以为坏了。
            self.status.setText("打不开这个链接，检查一下系统默认浏览器")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 扫码结果大多是"看一眼就走"，Esc 直接关
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


def _preview(text: str, limit: int = 28) -> str:
    """下拉项里的一行预览：换行折成空格、超长截断。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"
