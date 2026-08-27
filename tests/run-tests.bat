@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT v%PLATFORM_VERSION% - Pytest

echo ============================================================
echo   SECURE IOT DEVICE PLATFORM v%PLATFORM_VERSION% - PYTEST
echo ============================================================
echo.
python -X utf8 scripts\run_tests.py --python-only %*
set "RC=%ERRORLEVEL%"
echo.
echo Resultados CSV: validation_results\tests\^<fecha-hora^>\
echo Codigo de salida: %RC%
echo.
pause
exit /b %RC%
