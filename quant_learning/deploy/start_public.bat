@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "PORT=8772"

echo ============================================================
echo  QuantLab public launcher
echo ============================================================

rem ---------------- locate python ----------------
set "PY="
if defined QUANTLAB_PY set "PY=%QUANTLAB_PY%"
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)
if not defined PY if exist "E:\Programs\miniconda\envs\aicode\python.exe" set "PY=E:\Programs\miniconda\envs\aicode\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\envs\aicode\python.exe" set "PY=%USERPROFILE%\miniconda3\envs\aicode\python.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\envs\aicode\python.exe" set "PY=%USERPROFILE%\anaconda3\envs\aicode\python.exe"
if not defined PY (
  echo [X] Python not found. Set QUANTLAB_PY=full\path\to\python.exe
  pause
  exit /b 1
)
echo [1/4] Python : %PY%

rem ---------------- password ----------------
if not exist "configs\public.secret.yaml" (
  echo [2/4] No password set yet - creating one now
  "%PY%" -m quant_platform.cli set-password
) else (
  echo [2/4] Credentials found: configs\public.secret.yaml
)

rem ---------------- app (loopback only!) ----------------
echo [3/4] Starting app on 127.0.0.1:%PORT% with auth enabled
start "QuantLabApp" /min "%PY%" -m quant_platform.cli web --public --host 127.0.0.1 --port %PORT%

rem ---------------- caddy (HTTPS) ----------------
if exist "deploy\caddy.exe" (
  echo [4/4] Starting Caddy (HTTPS reverse proxy)
  start "QuantLabCaddy" /min "deploy\caddy.exe" run --config deploy\Caddyfile
) else (
  echo [4/4] Caddy not installed - running HTTP only ^(not recommended^)
  echo      Install: powershell -ExecutionPolicy Bypass -File deploy\setup_caddy.ps1
)

timeout /t 4 >nul
echo.
echo Health check:
"%PY%" -c "import urllib.request,json;d=json.load(urllib.request.urlopen('http://127.0.0.1:%PORT%/healthz',timeout=6));print('   /healthz ->',d)"
echo.
echo ------------------------------------------------------------
echo  LAN   : http://^<your-lan-ip^>:%PORT%/m
echo  Public: https://^<your-ddns-domain^>/m
echo  Stop  : deploy\stop_public.bat
echo ------------------------------------------------------------
endlocal
