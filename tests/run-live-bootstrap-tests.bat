@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT v%PLATFORM_VERSION% - Live Bootstrap Security

echo ============================================================
echo   SECURE IOT v%PLATFORM_VERSION% - LIVE BOOTSTRAP SECURITY
echo ============================================================
echo.
python -X utf8 scripts\validate_live_bootstrap_security.py %*
set "RC=%ERRORLEVEL%"
echo.
echo Resultados CSV: validation_results\live-bootstrap\^<fecha-hora^>\
echo Codigo de salida: %RC%
echo.
pause
exit /b %RC%
