# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式，不打包成单文件）。

构建：PYTHONPATH= python -m PyInstaller 日常任务栏.spec --noconfirm
产物：dist/日常任务栏/日常任务栏.exe（附 _internal 依赖目录）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)

a = Analysis(
    [str(PROJECT_ROOT / 'src' / 'app.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / '每日任务.ico'), '.'),  # 只读资源随包分发
    ],
    hiddenimports=[
        'pynput.keyboard',
        'pynput.mouse',
        # zxing-cpp 与 pynput 一样是**函数内惰性导入**（为了缺库时只失效单个
        # 功能、不拖垮整个程序）。PyInstaller 通常能扫到函数里的 import，
        # 但这里显式列出兜底：漏了它 exe 会变成"别的都能用、一扫码就报缺组件"，
        # 而这种失败在开发机上永远复现不了（开发机里 site-packages 好好的）。
        'zxingcpp',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy.tests',
        # 未使用的 PySide6 子模块（避免拉入对应 Qt DLL）
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
        'PySide6.QtWebSockets', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.QtQuickControls2', 'PySide6.QtQml', 'PySide6.QtQmlModels',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtTest',
        'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtUiTools',
        'PySide6.QtSerialPort', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'PySide6.QtSensors', 'PySide6.QtPositioning', 'PySide6.QtLocation',
        'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtTextToSpeech',
        'PySide6.QtVirtualKeyboard', 'PySide6.QtOpenGLWidgets',
        'PySide6.QtQuick3D', 'PySide6.QtSql', 'PySide6.QtDBus',
    ],
    noarchive=False,
)

# ---- 体积裁剪：excludes 拦不住 PySide6 hook 的依赖 DLL，直接过滤 binaries ----
import re

_UNWANTED_RE = re.compile(
    r"opengl32sw|qt6(quick|qml|pdf|charts|datavis|webengine|webchannel|websockets"
    r"|multimedia|3d|serialport|sensors|positioning|location|bluetooth|nfc"
    r"|test|designer|help|remoteobjects|scxml|texttospeech|virtualkeyboard"
    r"|sql|statemachine|statemachine)",
    re.IGNORECASE,
)
a.binaries = [b for b in a.binaries if not _UNWANTED_RE.search(b[0])]
a.datas = [d for d in a.datas
           if "translations" not in d[0] and "qml" not in d[0].lower()]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir 关键：二进制交给 COLLECT
    name='日常任务栏',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI 程序，无控制台黑窗
    icon=str(PROJECT_ROOT / '每日任务.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='日常任务栏',
)
