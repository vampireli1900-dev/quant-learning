# ============================================================
# deploy/install_autostart.ps1 —— 开机自动拉起 QuantLab（可选）
#
# 注册两个 Windows 计划任务：
#   QuantLabWeb    登录时启动应用 + Caddy（用 start_public.bat）
#   QuantLabDDNS   每 10 分钟刷新动态 DNS（用 deploy/ddns.py）
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File deploy\install_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\install_autostart.ps1 -Uninstall
# ============================================================
param(
    [switch]$Uninstall,
    [string]$DdnsDomain = "",
    [string]$DdnsToken  = ""
)

$ErrorActionPreference = "SilentlyContinue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bat  = Join-Path $root "deploy\start_public.bat"

function Remove-Task($n) {
    schtasks /Delete /TN $n /F 2>&1 | Out-Null
    Write-Host "  removed task: $n" -ForegroundColor Yellow
}

if ($Uninstall) {
    Write-Host "Uninstalling QuantLab autostart tasks..." -ForegroundColor Cyan
    Remove-Task "QuantLabWeb"
    Remove-Task "QuantLabDDNS"
    Write-Host "Done." -ForegroundColor Green
    exit 0
}

Write-Host "Installing QuantLab autostart tasks..." -ForegroundColor Cyan

# ---- 1. 主服务（登录时启动）----
# 必须挑装了项目依赖的解释器，PATH 里第一个 python 未必带 uvicorn
$candidates = @(
    $env:QUANTLAB_PY,
    "E:\Programs\miniconda\envs\aicode\python.exe",
    "$env:USERPROFILE\miniconda3\envs\aicode\python.exe",
    "$env:USERPROFILE\anaconda3\envs\aicode\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue).Source
) | Where-Object { $_ -and (Test-Path $_) }

$py = $null
foreach ($c in $candidates) {
    & $c -c "import uvicorn, fastapi, numpy" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
}
if (-not $py) {
    Write-Host "  [x] No python with project dependencies found." -ForegroundColor Red
    Write-Host "      Set `$env:QUANTLAB_PY to the right interpreter and retry." -ForegroundColor Yellow
    exit 1
}
Write-Host "  python: $py" -ForegroundColor DarkGray

$action = "cmd.exe /c `"$bat`""
schtasks /Create /F /TN "QuantLabWeb" /SC ONLOGON /TR $action /RL HIGHEST 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [ok] QuantLabWeb  -> runs at logon" -ForegroundColor Green
} else {
    Write-Host "  [x]  QuantLabWeb  failed (try running PowerShell as Administrator)" -ForegroundColor Red
}

# ---- 2. DDNS ----
if ($DdnsDomain -and $DdnsToken) {
    $ddns = Join-Path $root "deploy\ddns.py"
    $tr = "`"$py`" `"$ddns`" --provider duckdns --domain $DdnsDomain --token $DdnsToken"
    schtasks /Create /F /TN "QuantLabDDNS" /SC MINUTE /MO 10 /TR $tr /RL HIGHEST 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [ok] QuantLabDDNS -> every 10 minutes ($DdnsDomain.duckdns.org)" -ForegroundColor Green
    } else {
        Write-Host "  [x]  QuantLabDDNS failed" -ForegroundColor Red
    }
} else {
    Write-Host "  [--] DDNS skipped. To add it later:" -ForegroundColor DarkGray
    Write-Host "       python deploy\ddns.py --provider duckdns --domain <name> --token <token> --install-task"
}

Write-Host ""
Write-Host "Manage with:  schtasks /Query /TN QuantLabWeb" -ForegroundColor DarkGray
Write-Host "Remove with:  powershell -File deploy\install_autostart.ps1 -Uninstall" -ForegroundColor DarkGray
