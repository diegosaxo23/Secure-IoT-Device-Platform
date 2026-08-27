@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT v%PLATFORM_VERSION% - Physical Benchmark

echo ============================================================
echo   SECURE IOT v%PLATFORM_VERSION% - BENCHMARK FISICO
echo ============================================================
echo.

if not "%~1"=="" goto runargs
set "PROFILE=cromaled"
set "PORT=COM2"
set "RUNS=10"
set /p PROFILE=Profile [cromaled/area_lz7/as7341] (default cromaled): 
if "%PROFILE%"=="" set "PROFILE=cromaled"
set /p PORT=Puerto serie (default COM2): 
if "%PORT%"=="" set "PORT=COM2"
set /p RUNS=Numero de ensayos (default 10): 
if "%RUNS%"=="" set "RUNS=10"
python -X utf8 scripts\benchmark_real_device.py --profile "%PROFILE%" --port "%PORT%" --runs "%RUNS%"
goto done

:runargs
python -X utf8 scripts\benchmark_real_device.py %*

:done
set "RC=%ERRORLEVEL%"
echo.
echo CSV automaticos: validation_results\physical\^<fecha-hora^>\
echo   runs.csv
echo   physical-metrics.csv
echo   physical-metrics-summary.csv
echo Codigo de salida: %RC%
echo.
pause
exit /b %RC%
