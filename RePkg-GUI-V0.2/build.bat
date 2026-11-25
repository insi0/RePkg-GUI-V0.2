@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

python -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

python -m PyInstaller -F -w -n RePkg-GUI-V0.2 --clean --noconfirm --add-data "assets\bg_right.png;assets" repkg_gui.py
if errorlevel 1 goto fail

echo.
echo Build OK: dist\RePkg-GUI-V0.2.exe
pause
exit /b 0

:fail
echo.
echo Build failed. See messages above.
pause
exit /b 1
