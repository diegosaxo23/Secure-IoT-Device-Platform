@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT v%PLATFORM_VERSION% - Live MQTT ACL

echo ============================================================
echo   SECURE IOT v%PLATFORM_VERSION% - LIVE MQTT ACL
echo ============================================================
echo.

if not "%~1"=="" goto runargs
set /p DEVICE_A=Device A provisionado (ej. CLED-SIM-0001): 
set /p DEVICE_B=Device B ajeno (ej. CLED-SIM-0002): 
set /p MQTT_HOST=IP/host del broker: 
python -X utf8 scripts\validate_live_mqtt_acl.py --device-id "%DEVICE_A%" --other-device-id "%DEVICE_B%" --mqtt-host "%MQTT_HOST%"
goto done

:runargs
python -X utf8 scripts\validate_live_mqtt_acl.py %*

:done
set "RC=%ERRORLEVEL%"
echo.
echo Resultados CSV: validation_results\live-mqtt-acl\^<fecha-hora^>\
echo Codigo de salida: %RC%
echo.
pause
exit /b %RC%
