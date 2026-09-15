@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Building USB HID Tool EXE (Reusable VENV - Python 3.12)

cd /d "%~dp0"

:: 1. 定義 Windows 系統中 Python 3.12 的實體安裝路徑
set "USER_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

:: 2. 定義專屬 Python 3.12 虛擬環境中的執行檔路徑
set "VENV_DIR=venv_build_312"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYINSTALLER=%VENV_DIR%\Scripts\pyinstaller.exe"

:: 3. 清理舊的建置產物與快取 (.mypy_cache, build, dist)
echo ===================================================
echo   Cleaning old build/, dist/ and .mypy_cache...
echo ===================================================
if exist "build" rd /s /q "build"
if exist "dist" rd /s /q "dist"
if exist ".mypy_cache" rd /s /q ".mypy_cache"

:: 4. 檢查並建立/啟用獨立的 venv 環境
if not exist "%VENV_DIR%" (
    echo ===================================================
    echo   [VENV] Creating clean Python 3.12 virtual environment...
    echo ===================================================
    "%USER_PYTHON%" -m venv %VENV_DIR%
    
    echo ===================================================
    echo   [VENV] Installing required packages for the first time...
    echo ===================================================
    "%VENV_PYTHON%" -m pip install --upgrade pip >nul
    "%VENV_PYTHON%" -m pip install wxPython hidapi pyinstaller
) else (
    echo ===================================================
    echo   [VENV] Using existing %VENV_DIR% environment...
    echo ===================================================
)

:: 印出內部 Python 版本確保建置環境正確
echo   [VENV Version Check]
"%VENV_PYTHON%" --version
echo.

:: 5. 執行 venv 內部的 PyInstaller 打包
echo ===================================================
echo   Starting PyInstaller Build Process (Python 3.12)...
echo ===================================================
"%VENV_PYINSTALLER%" --noconfirm --onefile --windowed ^
  --icon=usb_hid_tool.ico ^
  --add-data "usb_hid_tool.ico;." ^
  --exclude-module asyncio ^
  --exclude-module multiprocessing ^
  --exclude-module xmlrpc ^
  --exclude-module pydoc ^
  --exclude-module email ^
  --exclude-module tkinter ^
  --exclude-module _hashlib ^
  --exclude-module ssl ^
  --exclude-module unicodedata ^
  --exclude-module wx.adv ^
  --exclude-module wx.html ^
  --exclude-module wx.html2 ^
  --exclude-module wx.xml ^
  --exclude-module wx.xrc ^
  --exclude-module wx.media ^
  --exclude-module wx.stc ^
  --exclude-module wx.ribbon ^
  --exclude-module wx.propgrid ^
  --exclude-module wx.py ^
  usb_hid_tool_wx.py

echo.
if %ERRORLEVEL% EQU 0 (
    echo ===================================================
    echo   Build Completed Successfully!
    
    set "EXE_PATH=dist\usb_hid_tool_wx.exe"
    if exist "!EXE_PATH!" (
        for %%A in ("!EXE_PATH!") do set "FILE_SIZE_BYTES=%%~zA"
        
        :: 計算 MB 檔案大小
        set /a "SIZE_MB_INT=!FILE_SIZE_BYTES! / 1048576"
        set /a "SIZE_MB_DEC=(!FILE_SIZE_BYTES! %% 1048576) * 100 / 1048576"
        if !SIZE_MB_DEC! LSS 10 set "SIZE_MB_DEC=0!SIZE_MB_DEC!"
        
        echo   EXE Location: !EXE_PATH!
        echo   EXE File Size: !SIZE_MB_INT!.!SIZE_MB_DEC! MB ^(!FILE_SIZE_BYTES! bytes^)
    )
    echo ===================================================
) else (
    echo ===================================================
    echo   Build Failed with Error Code: %ERRORLEVEL%
    echo ===================================================
)

echo.
pause