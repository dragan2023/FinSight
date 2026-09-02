@echo off
setlocal
chcp 65001 >nul
title FinSight Launcher
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1" %*
if errorlevel 1 (
    echo.
    echo FinSight failed to start. See the message above.
    pause
    exit /b 1
)
endlocal
