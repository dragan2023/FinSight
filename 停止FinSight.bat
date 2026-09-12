@echo off
setlocal
chcp 65001 >nul
title 停止 FinSight
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_all.ps1"
if errorlevel 1 (
  echo.
  echo 未停止服务：请查看上方原因。若提示 Access is denied，请双击 停止FinSight（管理员）.bat。
  pause
  exit /b 1
)
echo.
pause
endlocal
