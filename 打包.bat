@echo off
chcp 936 >nul
cd /d %~dp0
echo ============================================
echo   打包 日常任务栏 (PyInstaller onedir)
echo ============================================
set PYTHONPATH=
if not exist build mkdir build
if not exist dist mkdir dist
python -m PyInstaller 日常任务栏.spec --noconfirm
if errorlevel 1 (
    echo [失败] 打包出错，5 秒后自动重试一次...
    timeout /t 5 /nobreak >nul
    rd /s /q build 2>nul
    if not exist build mkdir build
    python -m PyInstaller 日常任务栏.spec --noconfirm
)
if errorlevel 1 (
    echo [失败] 重试后仍打包出错，请检查上方日志
    pause
    exit /b 1
)
echo [成功] 产物位于 dist\日常任务栏\日常任务栏.exe
pause
