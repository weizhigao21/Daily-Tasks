# -*- coding: utf-8 -*-
"""开机自启（Windows 注册表 Run 项）。"""
from __future__ import annotations

import sys
from pathlib import Path

from .. import config

# 打包产物相对路径（源码模式下存在则优先注册 exe）
_PACKAGED_EXE = Path("dist") / "日常任务栏" / "日常任务栏.exe"


def _command() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller 打包
        return f'"{sys.executable}"'
    packaged = config.PROJECT_ROOT / _PACKAGED_EXE
    if packaged.is_file():  # 已打包的 exe 优先（避免注册表残留指向源码）
        return f'"{packaged}"'
    app_py = config.PROJECT_ROOT / "src" / "app.py"
    return f'"{sys.executable}" "{app_py}"'


def is_enabled() -> bool:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.RUN_KEY_PATH, 0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, config.RUN_KEY_NAME)
            return True
        finally:
            key.Close()
    except Exception:
        return False


def set_enabled(enable: bool) -> bool:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, config.RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            if enable:
                winreg.SetValueEx(key, config.RUN_KEY_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, config.RUN_KEY_NAME)
                except FileNotFoundError:
                    pass
        finally:
            key.Close()
        return True
    except Exception:
        return False
