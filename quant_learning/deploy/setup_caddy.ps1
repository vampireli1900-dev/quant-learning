# ============================================================
# deploy/setup_caddy.ps1 —— 下载 Caddy（Windows 版）到 deploy/caddy.exe
#
# 用法（PowerShell，管理员不是必须的）：
#   powershell -ExecutionPolicy Bypass -File deploy\setup_caddy.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\setup_caddy.ps1 -WithDuckDns
#
# -WithDuckDns 会下载【带 duckdns DNS 插件】的构建：
#   80 端口被运营商封掉时，用它走 DNS-01 验证申请证书，不必开放 80。
# ============================================================
param(
    [switch]$WithDuckDns
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$out  = Join-Path $dir "caddy.exe"

$url = "https://caddyserver.com/api/download?os=windows&arch=amd64"
if ($WithDuckDns) {
    $url += "&p=github.com%2Fcaddy-dns%2Fduckdns"
    Write-Host "下载带 duckdns 插件的 Caddy（DNS-01 用）..." -ForegroundColor Cyan
} else {
    Write-Host "下载标准 Caddy（HTTP-01 用）..." -ForegroundColor Cyan
}
Write-Host "  $url"

try {
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
} catch {
    Write-Host "下载失败：$_" -ForegroundColor Red
    Write-Host "可手动下载：打开上面的链接，把 caddy.exe 保存到 deploy\ 目录。" -ForegroundColor Yellow
    exit 1
}

if (Test-Path $out) {
    $size = [math]::Round((Get-Item $out).Length / 1MB, 1)
    Write-Host "`n已保存到 $out （$size MB）" -ForegroundColor Green
    & $out version
    Write-Host "`n下一步："
    Write-Host "  1. 编辑 deploy\Caddyfile，把域名和邮箱改成你自己的"
    Write-Host "  2. 先启动应用：  deploy\start_public.bat"
    Write-Host "  3. 再启动 Caddy： deploy\caddy.exe run --config deploy\Caddyfile"
} else {
    Write-Host "未找到下载文件，请手动下载。" -ForegroundColor Red
    exit 1
}
