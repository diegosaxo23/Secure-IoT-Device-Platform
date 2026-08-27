@echo off
setlocal
cd /d "%~dp0.."
for /f "usebackq delims=" %%V in ("VERSION") do set "PLATFORM_VERSION=%%V"
title Secure IoT Device Platform v%PLATFORM_VERSION% - Validation Menu

:menu
cls
echo ============================================================
echo       SECURE IOT DEVICE PLATFORM v%PLATFORM_VERSION%
echo                  VALIDATION MENU
echo ============================================================
echo.
echo   1. Pytest completo
echo   2. Tests de seguridad resumidos
echo   3. Compilar los 3 firmware ESP32
echo   4. Pytest + compilacion firmware
echo   5. Ataques bootstrap contra API real
echo   6. ACL MQTT contra broker real
echo   7. Revocacion contra broker real
echo   8. Benchmark simulado limpio 1/10/25/50
echo   9. Benchmark fisico (10 por defecto)
echo   0. Salir
echo.
set /p CHOICE=Selecciona una opcion: 

if "%CHOICE%"=="1" call "%~dp0run-tests.bat"
if "%CHOICE%"=="2" call "%~dp0run-security-tests.bat"
if "%CHOICE%"=="3" call "%~dp0run-firmware-tests.bat"
if "%CHOICE%"=="4" call "%~dp0run-all-tests.bat"
if "%CHOICE%"=="5" call "%~dp0run-live-bootstrap-tests.bat"
if "%CHOICE%"=="6" call "%~dp0run-live-mqtt-acl-test.bat"
if "%CHOICE%"=="7" call "%~dp0run-live-revocation-test.bat"
if "%CHOICE%"=="8" call "%~dp0benchmark-simulated.bat"
if "%CHOICE%"=="9" call "%~dp0benchmark-real.bat"
if "%CHOICE%"=="0" goto end

goto menu

:end
echo.
echo Cerrando menu de validacion.
pause
exit /b 0
