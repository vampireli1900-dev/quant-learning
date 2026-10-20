@echo off
rem ------------------------------------------------------------
rem  Double-click to allow QuantLab through Windows Firewall.
rem  It re-launches itself elevated, so a UAC prompt will appear:
rem  click Yes.
rem
rem  Why: Windows Firewall blocks inbound connections by default.
rem  Installing Tailscale adds a virtual NIC but does NOT open
rem  port 8772, so the PC can reach its own 100.x.y.z while the
rem  phone times out.
rem
rem  NOTE: keep this file ASCII-only with CRLF line endings.
rem ------------------------------------------------------------
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel%==0 goto :run

echo.
echo  Requesting administrator permission (UAC)...
echo  If a window pops up, click Yes.
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
echo.
echo  Running as administrator. Opening TCP 8772 for the Tailscale network...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fw_allow_tailscale.ps1"
echo.
echo  Done. Press any key to close.
pause >nul
endlocal
