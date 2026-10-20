@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "PORT=8772"

echo ============================================================
echo  QuantLab via Tailscale   (no public IP needed)
echo ============================================================

rem ---- pick a python that really has the project dependencies ----
rem The first python on PATH may be a bare interpreter without
rem uvicorn/fastapi/numpy, so try each candidate by importing them.
set "PY="
for %%P in ("%QUANTLAB_PY%" "E:\Programs\miniconda\envs\aicode\python.exe" "E:\Programs\miniconda\python.exe" "%USERPROFILE%\miniconda3\envs\aicode\python.exe" "%USERPROFILE%\anaconda3\envs\aicode\python.exe" "python") do (
  if not defined PY (
    if not "%%~P"=="" (
      %%~P -c "import uvicorn, fastapi, numpy" >nul 2>&1
      if not errorlevel 1 set "PY=%%~P"
    )
  )
)
if not defined PY (
  echo [X] No python with the project dependencies was found.
  echo     Fix:  set QUANTLAB_PY=E:\Programs\miniconda\envs\aicode\python.exe
  pause
  exit /b 1
)
echo [1/4] Python : %PY%

rem ---- bind to the Tailscale interface only ----
rem Binding 100.x.y.z instead of 0.0.0.0 means only devices logged into
rem your tailnet can reach it: not the home LAN, not the internet.
set "PATH=%PATH%;C:\Program Files\Tailscale"
set "TS="
for /f "delims=" %%i in ('tailscale ip -4 2^>nul') do (
  if not defined TS set "TS=%%i"
)
set "HOST=0.0.0.0"
if defined TS (
  set "HOST=%TS%"
  echo [2/4] Tailscale IP : %TS%   [reachable only inside your tailnet]
) else (
  echo [2/4] Tailscale is not running - falling back to 0.0.0.0 [LAN only]
  echo       Setup:  powershell -ExecutionPolicy Bypass -File deploy\tailscale_setup.ps1
)

if not exist "configs\public.secret.yaml" (
  echo [3/4] No password set yet - creating one now
  "%PY%" -m quant_platform.cli set-password
) else (
  echo [3/4] Credentials found: configs\public.secret.yaml
)

echo [4/4] Starting on %HOST%:%PORT% (login required)
echo.
echo ------------------------------------------------------------
if defined TS (
  echo  Open on phone : http://%TS%:%PORT%/m
) else (
  echo  Open          : http://[lan-ip]:%PORT%/m
)
echo  Stop           : deploy\stop_public.bat
echo ------------------------------------------------------------
echo.

"%PY%" -m quant_platform.cli web --public --host %HOST% --port %PORT%
endlocal
