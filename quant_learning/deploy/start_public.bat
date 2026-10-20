@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "PORT=8772"

echo ============================================================
echo  QuantLab public launcher  (port forwarding + HTTPS)
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

if not exist "configs\public.secret.yaml" (
  echo [2/4] No password set yet - creating one now
  "%PY%" -m quant_platform.cli set-password
) else (
  echo [2/4] Credentials found: configs\public.secret.yaml
)

rem ---- app listens on loopback only; Caddy terminates HTTPS ----
echo [3/4] Starting app on 127.0.0.1:%PORT% with login required
start "QuantLabApp" /min "%PY%" -m quant_platform.cli web --public --host 127.0.0.1 --port %PORT%

if exist "deploy\caddy.exe" (
  echo [4/4] Starting Caddy (HTTPS reverse proxy)
  start "QuantLabCaddy" /min "deploy\caddy.exe" run --config deploy\Caddyfile
) else (
  echo [4/4] Caddy not installed - HTTP only (not recommended)
  echo       Setup:  powershell -ExecutionPolicy Bypass -File deploy\setup_caddy.ps1
)

timeout /t 4 >nul
echo.
echo Health check:
"%PY%" -c "import urllib.request,json;print('   /healthz ->',json.load(urllib.request.urlopen('http://127.0.0.1:%PORT%/healthz',timeout=6)))"
echo.
echo ------------------------------------------------------------
echo  LAN    : http://[lan-ip]:%PORT%/m
echo  Public : https://[your-ddns-domain]/m
echo  Stop   : deploy\stop_public.bat
echo ------------------------------------------------------------
endlocal
