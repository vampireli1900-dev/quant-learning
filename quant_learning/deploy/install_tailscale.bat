@echo off
rem ------------------------------------------------------------
rem  Double-click this file to install + sign in Tailscale.
rem  No need to type any command. Runs the PowerShell helper
rem  next to this file, so the working directory never matters.
rem  NOTE: this .bat must stay ASCII-only with CRLF line endings.
rem ------------------------------------------------------------
setlocal
cd /d "%~dp0"

rem Switch console to UTF-8 so the Chinese output of the .ps1 renders correctly.
rem (The .ps1 is UTF-8 with BOM; without this the console may show mojibake.)
chcp 65001 >nul 2>&1

echo.
echo  ============================================================
echo    QuantLab - Tailscale setup
echo    (for the case where you have NO public IP / CGNAT)
echo  ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tailscale_setup.ps1"

set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo  [!] Script exited with code %RC%
echo  Press any key to close...
pause >nul
endlocal
