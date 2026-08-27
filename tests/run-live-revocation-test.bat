@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT v%PLATFORM_VERSION% - Live Revocation

echo ============================================================
echo   SECURE IOT v%PLATFORM_VERSION% - LIVE REVOCATION
echo ============================================================
echo.

if not "%~1"=="" goto runargs
set /p DEVICE_ID=Device ID simulado provisionado a revocar: 
set /p MQTT_HOST=IP/host del broker (Enter para usar provisioning.json): 
if "%MQTT_HOST%"=="" (
  python -X utf8 scripts\validate_live_revocation.py --device-id "%DEVICE_ID%"
) else (
  python -X utf8 scripts\validate_live_revocation.py --device-id "%DEVICE_ID%" --mqtt-host "%MQTT_HOST%"
)
goto done

:runargs
python -X utf8 scripts\validate_live_revocation.py %*

:done
set "RC=%ERRORLEVEL%"
echo.
echo Resultados CSV: validation_results\live-revocation\^<fecha-hora^>\
echo Codigo de salida: %RC%
echo.
pause
exit /b %RC%
