@echo off
chcp 65001 >nul 2>&1
echo Stopping QuantLab public services...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8772" ^| findstr LISTENING') do (
  echo   killing PID %%a
  taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8770" ^| findstr LISTENING') do (
  echo   killing PID %%a
  taskkill /PID %%a /F >nul 2>&1
)

taskkill /IM caddy.exe /F >nul 2>&1 && echo   caddy stopped
echo Done.
