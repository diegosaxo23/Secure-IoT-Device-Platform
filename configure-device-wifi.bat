@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERROR] Python 3 was not found in PATH.
  pause
  exit /b 2
)
%PYTHON_CMD% scripts\configure_device_wifi.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
