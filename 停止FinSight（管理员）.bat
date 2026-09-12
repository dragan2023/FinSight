@echo off
setlocal
chcp 65001 >nul
title Stop FinSight as Administrator
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_all_elevated.ps1"
if errorlevel 1 (
  echo.
  echo FinSight was not stopped. Confirm the Windows administrator prompt, then try again.
  pause
  exit /b 1
)
echo.
echo FinSight has stopped. You can now double-click 启动FinSight.bat to start the latest version.
pause
endlocal
