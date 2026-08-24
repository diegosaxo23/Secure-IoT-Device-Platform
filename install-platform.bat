@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Install Secure IoT Device Platform

echo ============================================================
echo  Secure IoT Device Platform
echo  One-time clean installation
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

echo [INSTALL] Using %PYTHON_CMD%
%PYTHON_CMD% scripts\install_platform.py
set "INSTALL_RC=%ERRORLEVEL%"

echo.
if "%INSTALL_RC%"=="0" (
  echo [OK] Installation completed successfully.
  echo [NEXT] Run start-platform.bat to start the platform.
) else (
  echo [ERROR] Installation failed with code %INSTALL_RC%.
)
echo.
pause
exit /b %INSTALL_RC%
