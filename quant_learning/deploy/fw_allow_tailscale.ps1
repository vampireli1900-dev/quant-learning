# ============================================================
# deploy\fw_allow_tailscale.ps1 —— 放行 Windows 防火墙，让手机能连进来
#
# 为什么需要：Windows 防火墙默认拦掉所有入站连接。Tailscale 装好后
# 电脑多了张虚拟网卡，但防火墙并不会因此自动放行 8772 端口，
# 于是「电脑自己访问 100.x.y.z 正常，手机访问却超时」。
#
# 安全边界：只允许来源为 Tailscale 网段 100.64.0.0/10 的入站，
#           家里局域网和公网的机器依然连不进来。
#
# 用法（PowerShell，**需要管理员权限**）：
#   powershell -ExecutionPolicy Bypass -File .\deploy\fw_allow_tailscale.ps1
#   powershell -ExecutionPolicy Bypass -File .\deploy\fw_allow_tailscale.ps1 -Port 8772
#   powershell -ExecutionPolicy Bypass -File .\deploy\fw_allow_tailscale.ps1 -Status
#   powershell -ExecutionPolicy Bypass -File .\deploy\fw_allow_tailscale.ps1 -Remove
# ============================================================
param(
    [int]$Port = 8772,
    [switch]$Status,
    [switch]$Remove,
    [switch]$AlsoLan          # 额外放行家里局域网（会放宽到 192.168/10/172.16-31）
)

$ErrorActionPreference = "Stop"
$ruleName = "QuantLab Web (Tailscale only)"

# --- 管理员检查 ---
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Show-Status {
    Write-Host ""
    Write-Host "当前 $ruleName 相关规则：" -ForegroundColor Cyan
    $r = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $r) {
        Write-Host "  (无) 还没有放行规则 —— 手机会连不上" -ForegroundColor Yellow
        return
    }
    foreach ($x in $r) {
        $f = $x | Get-NetFirewallPortFilter
        $a = $x | Get-NetFirewallAddressFilter
        Write-Host ("  [{0}] {1}  port={2}  remote={3}" -f $x.Direction, $x.Action, $f.LocalPort, $a.RemoteAddress) `
            -ForegroundColor $(if ($x.Enabled -eq 'True') { 'Green' } else { 'DarkGray' })
    }
}

if ($Status) { Show-Status; exit 0 }

if ($Remove) {
    Write-Host "删除规则 $ruleName ..." -ForegroundColor Cyan
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host "  已删除" -ForegroundColor Green
    exit 0
}

if (-not $isAdmin) {
    Write-Host ""
    Write-Host "  !! 需要管理员权限才能改防火墙" -ForegroundColor Yellow
    Write-Host "     请右键 PowerShell → 「以管理员身份运行」，再执行：" -ForegroundColor Yellow
    Write-Host "     powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\fw_allow_tailscale.ps1`"" -ForegroundColor White
    Write-Host ""
    exit 2
}

# --- 建规则 ---
Write-Host ""
Write-Host "放行 TCP $Port 入站（仅 Tailscale 网段 100.64.0.0/10）..." -ForegroundColor Cyan

# 先删旧的，避免重复叠加
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule

$remote = @("100.64.0.0/10")
if ($AlsoLan) { $remote += @("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12") }

New-NetFirewallRule -DisplayName $ruleName `
    -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress $remote -Action Allow -Profile Any `
    -Description "QuantLab web terminal, reachable only from the Tailscale network" | Out-Null

Write-Host "  OK 规则已创建" -ForegroundColor Green
Show-Status

Write-Host ""
Write-Host "验证（电脑端）：" -ForegroundColor Cyan
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $ts) {
    $ip = (& $ts ip -4 2>$null | Select-Object -First 1 | Out-String).Trim()
    if ($ip) {
        Write-Host "  curl http://$ip`:$Port/login" -ForegroundColor White
        Write-Host "  手机浏览器打开 http://$ip`:$Port/m" -ForegroundColor White
    }
}
Write-Host ""
