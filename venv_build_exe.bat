@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Building USB HID Tool EXE (Reusable VENV)

cd /d "%~dp0"

:: 1. 檢查並建立/啟用獨立的 venv 環境
if not exist "venv_build" (
    echo ===================================================
    echo   [VENV] Creating clean virtual environment...
    echo ===================================================
    python -m venv venv_build
    
    echo ===================================================
    echo   [VENV] Installing required packages for the first time...
    echo ===================================================
    call venv_build\Scripts\activate.bat
    python -m pip install --upgrade pip >nul
    pip install wxPython hidapi pyinstaller
) else (
    echo ===================================================
    echo   [VENV] Using existing venv_build environment...
    echo ===================================================
    call venv_build\Scripts\activate.bat
)

:: 2. 清理舊的 build/dist 資料夾
echo ===================================================
echo   Cleaning old build/ and dist/ folders...
echo ===================================================
if exist "build" rd /s /q "build"
if exist "dist" rd /s /q "dist"

:: 3. 執行 PyInstaller 打包
echo ===================================================
echo   Starting PyInstaller Build Process...
echo ===================================================
pyinstaller --noconfirm --onefile --windowed ^
  --icon=usb_hid_tool.ico ^
  --add-data "usb_hid_tool.ico;." ^
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

:: 4. 退出 venv 虛擬環境
call deactivate

echo.
if %ERRORLEVEL% EQU 0 (
    echo ===================================================
    echo   Build Completed Successfully!
    
    set "EXE_PATH=dist\usb_hid_tool_wx.exe"
    if exist "!EXE_PATH!" (
        for %%A in ("!EXE_PATH!") do set "FILE_SIZE_BYTES=%%~zA"
        
        :: 計算 MB
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