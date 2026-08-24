@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Secure IoT Device Platform

echo ============================================================
echo  Secure IoT Device Platform
echo  Complete platform startup
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
  echo Install Python 3 and enable "Add Python to PATH", then run this file again.
  echo.
  pause
  exit /b 2
)

echo [START] Using %PYTHON_CMD%
%PYTHON_CMD% scripts\start_platform.py
set "START_RC=%ERRORLEVEL%"

if not "%START_RC%"=="0" (
  echo.
  echo [ERROR] Platform startup failed with code %START_RC%.
  echo.
  if exist "logs\manufacturing-agent.log" (
    echo ---------------- Manufacturing log ----------------
    powershell -NoProfile -Command "Get-Content -LiteralPath 'logs\manufacturing-agent.log' -Tail 80" 2>nul
    if errorlevel 1 type "logs\manufacturing-agent.log"
    echo ---------------------------------------------------
  )
  echo.
  pause
  exit /b %START_RC%
)

echo.
echo [OK] Docker broker, signed local-time service, dashboard/API, Simulation Manager, and host programmer are running.
echo [INFO] No device is programmed until Program Device is pressed in the dashboard.
echo [INFO] Use stop-platform.bat to stop the complete platform.
echo.

%PYTHON_CMD% scripts\show_startup_summary.py

echo.
pause
exit /b 0
