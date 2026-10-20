@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "PORT=8772"

echo ============================================================
echo  QuantLab public launcher
echo ============================================================

rem ---------------- locate python ----------------
rem 必须挑「装了项目依赖」的解释器：PATH 里排第一位的 python 未必带
rem uvicorn/fastapi/numpy（比如各种 IDE 自带的托管 python），
rem 所以逐个候选试 import，能跑通才用。
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
  echo [X] No python with the project dependencies found.
  echo     Tried: QUANTLAB_PY, conda env "aicode", PATH python
  echo     Fix:   set QUANTLAB_PY=E:\Programs\miniconda\envs\aicode\python.exe
  echo            or install: pip install uvicorn fastapi numpy pandas
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
