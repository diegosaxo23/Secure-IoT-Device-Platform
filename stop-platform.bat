@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Stop Secure IoT Device Platform

echo ============================================================
echo  Secure IoT Device Platform
echo  Stop Docker + Manufacturing Agent
echo ============================================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [ERROR] Python 3 was not found in PATH.
  echo.
  pause
  exit /b 2
)

%PYTHON_CMD% scripts\start_platform.py --stop-platform
set "STOP_RC=%ERRORLEVEL%"
echo.
if "%STOP_RC%"=="0" (
  echo [OK] Platform stopped.
) else (
  echo [WARN] Stop completed with code %STOP_RC%.
)
echo.
pause
exit /b %STOP_RC%
