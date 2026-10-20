# ============================================================
# deploy\tailscale_setup.ps1 —— 没有公网 IP（CGNAT）时的方案：Tailscale 组网
#
# 原理：端口转发是「外面主动连进来」，CGNAT 下这条路被运营商堵死；
#        Tailscale 反过来 —— 家里机器主动连出去，手机和它组成加密虚拟局域网，
#        路由器不用开任何端口，也不需要公网 IP。
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File deploy\tailscale_setup.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\tailscale_setup.ps1 -Status   # 只看状态
# ============================================================
param(
    [switch]$Status
)

$ErrorActionPreference = "Continue"
$tsExe = "C:\Program Files\Tailscale\tailscale.exe"
$msi   = "$env:TEMP\tailscale-setup-latest-amd64.msi"

function Write-Step($n, $msg) { Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)       { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)      { Write-Host "  !!  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Host "  Tailscale 组网 —— 让外面的手机连回家里这台机器" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Host ""

# ---------- 1. 查找 / 安装 ----------
Write-Step 1 "检查 Tailscale"

if (-not (Test-Path $tsExe)) {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { $tsExe = $cmd.Source }
}

if (-not (Test-Path $tsExe)) {
    Write-Bad "未安装，开始安装…"
    $installed = $false

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "     用 winget 安装…"
        winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) { $installed = $true }
    }

    if (-not $installed) {
        Write-Host "     下载安装包…" -ForegroundColor DarkGray
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi" `
                              -OutFile $msi -UseBasicParsing
            Write-Host "     已下载，弹窗里点「是」完成安装…" -ForegroundColor DarkGray
            Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /passive" -Verb RunAs -Wait
            $installed = Test-Path $tsExe
        } catch {
            Write-Bad "下载失败：$_"
            Write-Host "     请手动下载： https://tailscale.com/download/windows" -ForegroundColor Yellow
            exit 1
        }
    }

    if (-not (Test-Path $tsExe)) {
        Write-Bad "安装未完成，装好后重新运行本脚本"
        exit 1
    }
}
Write-Ok "找到 $tsExe"

# ---------- 2. 登录 ----------
Write-Step 2 "登录 Tailscale 账号"

$self = $null
try {
    $st = & $tsExe status --json 2>$null | ConvertFrom-Json
    if ($st -and $st.Self) { $self = $st.Self }
} catch { }

if (-not $self) {
    Write-Host "     正在启动登录（浏览器会打开授权页）…" -ForegroundColor DarkGray
    $job = Start-Job -ScriptBlock { & $using:tsExe up 2>&1 }
    Start-Sleep -Seconds 4
    $partial = Receive-Job $job -ErrorAction SilentlyContinue | Out-String
    if ($partial -match '(https://login\.tailscale\.com/\S+)') {
        Write-Host "     打开：$($matches[1])" -ForegroundColor DarkGray
        Start-Process $matches[1]
    }

    Write-Host "     等待授权…（最多 3 分钟）" -ForegroundColor DarkGray
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            $st = & $tsExe status --json 2>$null | ConvertFrom-Json
            if ($st -and $st.Self -and $st.Self.Online) { $self = $st.Self; break }
        } catch { }
    }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
}

if (-not $self) {
    Write-Bad "还没登录成功。请手动执行： `"$tsExe`" up"
    Write-Host "     然后打开它打印的链接完成授权，再跑一次本脚本。" -ForegroundColor Yellow
    exit 1
}
Write-Ok "已登录：$($self.DNSName)"

# ---------- 3. 地址 ----------
Write-Step 3 "本机在虚拟局域网里的地址"

$ip = (& $tsExe ip -4 2>$null | Select-Object -First 1).Trim()
Write-Host ""
Write-Host "     Tailscale IP : $ip" -ForegroundColor White
Write-Host "     MagicDNS 名  : $($self.DNSName.TrimEnd('.'))" -ForegroundColor White
Write-Host ""

# ---------- 4. 手机端 ----------
Write-Step 4 "手机要做的（一次性）"
Write-Host "     1) 装 App：App Store / Google Play 搜「Tailscale」，"
Write-Host "        或国内应用商店；也可从 https://tailscale.com/download 取 apk"
Write-Host "     2) 用【和电脑同一个账号】登录（微软 / Google / 苹果 / 邮箱都行）"
Write-Host "     3) 登录后保持 VPN 开关打开（它就一直在后台，不用每次手动连）"
Write-Host ""

# ---------- 5. 启动服务 ----------
Write-Step 5 "启动服务（只监听 Tailscale 网卡）"
Write-Host "     执行： deploy\start_tailscale.bat"
Write-Host "     它会把服务绑定到 $ip ，"
Write-Host "     这样家里局域网和公网都摸不到，只有你登录了 Tailscale 的设备能访问。"
Write-Host ""
Write-Host "     手机浏览器打开：" -NoNewline
Write-Host " http://$ip`:8772/m" -ForegroundColor Green
Write-Host "     或者更好记：  " -NoNewline
Write-Host " http://$($self.DNSName.TrimEnd('.')):8772/m" -ForegroundColor Green
Write-Host ""

# ---------- 6. 可选：固定 IP / 免登录过期 ----------
Write-Host "------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host " 提示" -ForegroundColor DarkGray
Write-Host "  · 设备密钥默认 180 天过期，想长期免管：在 Tailscale 管理后台"
Write-Host "    Machines → 本机 → Disable key expiry"
Write-Host "  · 想用 https:// 访问（虚拟网内自动证书）："
Write-Host "    `"$tsExe`" serve --bg --https=443 http://127.0.0.1:8772"
Write-Host "  · 完整说明见 docs/TAILSCALE.md"
Write-Host ""

if ($Status) { exit 0 }
