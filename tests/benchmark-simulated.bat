@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT v%PLATFORM_VERSION% - Simulated Benchmark 1-10-25-50

echo ============================================================
echo   SECURE IOT v%PLATFORM_VERSION% - BENCHMARK SIMULADO
echo   Flotas automaticas: 1, 10, 25 y 50 dispositivos
echo   Limpieza previa: SOLO simulados; fisicos preservados
echo ============================================================
echo.
echo Antes de cada escala se borran los simulados anteriores,
echo se actualiza la CRL y se reinicia Mosquitto.
echo La flota final de 50 queda disponible para inspeccion.
echo.
python -X utf8 scripts\benchmark_simulated_fleet.py %*
set "RC=%ERRORLEVEL%"
echo.
echo CSV automaticos: validation_results\simulated\^<fecha-hora^>\
echo   fleet-001\metrics.csv + metrics-summary.csv
echo   fleet-010\metrics.csv + metrics-summary.csv
echo   fleet-025\metrics.csv + metrics-summary.csv
echo   fleet-050\metrics.csv + metrics-summary.csv
echo   fleet-summary.csv
echo Codigo de salida: %RC%
echo.
pause
exit /b %RC%
