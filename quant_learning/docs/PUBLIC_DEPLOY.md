# 公网部署指南（有公网 IP 时用这篇）

把 QuantLab 从「家里电脑的本地服务」变成「在外面用手机也能打开」的个人站点。

路线：**路由器端口转发 + DDNS + Caddy 自动 HTTPS + 应用层登录鉴权**。

> ⚠️ **先确认你有没有公网 IP**：登录路由器看 WAN 口地址。
> 如果是 `100.64.x.x` / `10.x` / `192.168.x`，说明运营商做了 CGNAT，
> **端口转发写了也没用** —— 请改看 **`docs/TAILSCALE.md`**（Tailscale 组网，
> 不需要公网 IP、不用开端口、不用域名）。
> 拿不准就先跑 `python -m quant_platform.cli public-check` 看第 [5][6] 项。

---

## 先确认两件事：项目路径和 Python

项目根目录：

```
D:\pycharm_file\2026_stork\quant_learning
```

> ⚠️ **cmd 和 PowerShell 的 `cd` 不一样**：`cd /d` 是 **cmd** 的写法，
> 在 PowerShell 里会报 `找不到接受实际参数…的位置形式参数`。
> - **PowerShell** 里直接 `cd D:\pycharm_file\2026_stork\quant_learning`（没有 `/d`）
> - **cmd** 里才用 `cd /d D:\...`
>
> 下面代码块用 ```bat 标注的请在 **cmd** 里跑，用 ```powershell 标注的请在 **PowerShell** 里跑。
> 不知道自己在哪个？看提示符：以 `PS C:\...>` 开头的是 PowerShell，以 `C:\...>` 开头的是 cmd。

```bat
:: cmd：
cd /d D:\pycharm_file\2026_stork\quant_learning
```

```powershell
# PowerShell：
cd D:\pycharm_file\2026_stork\quant_learning
```

**Python 要用 conda 的 `aicode` 环境**，依赖（uvicorn / fastapi / numpy）都装在这里：

```bat
E:\Programs\miniconda\envs\aicode\python.exe
```

> ⚠️ PATH 里排第一位的 `python` 是另一个解释器（WorkBuddy 托管的 3.13），
> **没有装项目依赖**，直接 `python -m quant_platform.cli ...` 会报
> `ModuleNotFoundError: No module named 'uvicorn'`。

下文所有 `python` 都指上面那个完整路径。想少打字，二选一：

```bat
:: 方式一：设环境变量（deploy\*.bat 会自动优先用它）
set QUANTLAB_PY=E:\Programs\miniconda\envs\aicode\python.exe

:: 方式二：配一个别名（当前窗口有效）
doskey python=E:\Programs\miniconda\envs\aicode\python.exe $*
```

或者直接用 conda：

```bat
conda activate aicode
python -m quant_platform.cli public-check
```

`deploy\start_public.bat` 会自己逐个试候选解释器并挑能 import 依赖的那个，
所以双击它不需要你操心 Python 路径。

---

## 0. 先想清楚暴露的是什么

这个服务不是博客，它连着你的真实 IBKR 账户。放到公网上，别人猜到地址就能看到：

- 账户净值、现金、持仓标的与股数、历史委托
- 全部策略研究结果、回测数据
- 数据库内容（可下只读 SQL）

所以这一版做了下面这些，**动手前请确认它们都在位**：

| 防护 | 实现 | 位置 |
|---|---|---|
| 登录鉴权 | 用户名 + 密码 → HMAC 签名会话 Cookie（HttpOnly / SameSite=Lax / HTTPS 下 Secure） | `quant_platform/web/security.py` |
| 未登录拦截 | 页面 302 到 `/login`，`/api/*` 返回 401 | 同上 |
| 静态资源放行 | 只放行 css/js/vendor/图标，`.html` 页面本体必须登录 | `is_free_path()` |
| 暴力破解 | 10 分钟内错 8 次锁该 IP 15 分钟 | `AuthGuard.note_fail` |
| 接口限流 | 单 IP 600 次/分钟 | `AuthGuard.__call__` |
| 会话吊销 | 登出即把令牌 jti 拉黑 | `revoke_token()` |
| 安全响应头 | CSP / HSTS / X-Frame-Options / nosniff / Referrer-Policy | `SECURITY_HEADERS` |
| 关闭 Server 头 | `server_header=False`，不暴露 uvicorn 版本 | `run_server()` |
| 审计日志 | 登录成功/失败/锁定/登出写 `logs/public_access.log` | `audit()` |
| 口令存储 | pbkdf2-sha256 20 万轮，只存哈希，不存明文 | `configs/public.secret.yaml` |
| SQL 只读 | `assert_readonly` 拦截非 SELECT；可整个关掉 | `repository.py` / `disable_sql_query` |
| 可选脱敏 | `mask_account: true` 后净值/股数/成本一律不返回 | `mask_account_payload()` |

**剩下的风险（得靠你自己）**：口令强度、Windows 本身的安全、路由器管理口令、家里网络的其他设备。
用完不用了就把端口转发删掉 —— 那是唯一的入口开关。

---

## 1. 架构

```
手机（4G / 外面 Wi-Fi）
   │  https://quant.duckdns.org
   ▼
路由器 WAN 口 :443  ──端口转发──▶  192.168.5.71 :443   Caddy（HTTPS 终止）
                                        │
                                        ▼  127.0.0.1:8772
                                   QuantLab（登录鉴权 + 只读 API）
```

关键点：**应用只监听 `127.0.0.1`**，公网根本摸不到它，只能经过 Caddy。
即使你路由器转发写错了、或者 Caddy 挂了，暴露面也只是转发规则本身。

---

## 2. 五步走

### 步骤 1 · 设置口令

```bash
python -m quant_platform.cli set-password
```

会让你输两次密码（不回显），然后问是否开启 `public.enabled`。
密码只以 pbkdf2 哈希存进 `configs/public.secret.yaml`（已在 `.gitignore` 里）。

改密码再跑一次即可；改用户名：`set-password --username 新名字`。

> 已经预置了一个随机口令 `FXPPAZy3niKYCe`（用户名 `quant`）。**建议立刻改掉**，
> 它出现在了这次对话的记录里。

### 步骤 2 · 确认你有一个「真」公网 IP

```bash
python -m quant_platform.cli public-check
```

看第 [5] 项的公网 IP，然后登录路由器管理页看 **WAN 口 IP**，两者比对：

| 情况 | 结论 |
|---|---|
| 两个 IP 一致 | ✅ 可以端口转发 |
| WAN 是 `100.64.x.x` / `10.x` / `192.168.x` | ❌ 运营商 CGNAT，转发无效 → 改用内网穿透（Cloudflare Tunnel / frp） |
| WAN 是公网 IP，但和探测到的不一样 | ❌ 多层 NAT，同样无效 |

家宽 IP 会变（重启光猫、租约到期），所以必须配 DDNS。

### 步骤 3 · 配 DDNS

推荐 [duckdns.org](https://www.duckdns.org)：免费、不用信用卡、
而且**明确允许 Let's Encrypt 签发证书**（这一点很多 DDNS 做不到）。

1. 注册后拿到一个子域名（如 `quant`）和 token
2. 先手动跑一次验证：

```bash
python deploy/ddns.py --provider duckdns --domain quant --token 你的token
# 输出：已更新 (首次) → 1.2.3.4  响应：OK
```

3. 注册成计划任务（每 10 分钟检查一次，IP 变了才更新）：

```bash
python deploy/ddns.py --provider duckdns --domain quant --token 你的token --install-task
```

token 也可以写进 `deploy/.caddy-token` 或环境变量 `DUCKDNS_TOKEN`，免去命令行历史泄露。

用别的服务商（No-IP / Dynu / 花生壳 / 3322）：

```bash
python deploy/ddns.py --provider generic \
  --url "https://你的服务商/update?hostname=xxx&myip={ip}" --auth "user:pass"
```

### 步骤 4 · HTTPS（Caddy）

没有 HTTPS 的话，密码是明文在网络里跑的，等于没有鉴权。这一步不能省。

```powershell
# 80 端口能用（大多数情况）：下载标准版
powershell -ExecutionPolicy Bypass -File deploy\setup_caddy.ps1

# 80 端口被运营商封了：下载带 duckdns 插件的构建，走 DNS-01 验证
powershell -ExecutionPolicy Bypass -File deploy\setup_caddy.ps1 -WithDuckDns
```

然后编辑 `deploy/Caddyfile`，改掉两处：域名、邮箱。

- **80 端口可用**：删掉 `tls { dns duckdns ... }` 整块，Caddy 自动 HTTP-01 签发
- **80 被封**：保留那块，并设置环境变量 `$DUCKDNS_TOKEN`，走 DNS-01，不需要开 80

启动验证：

```powershell
deploy\caddy.exe run --config deploy\Caddyfile
```

第一次会申请证书（几十秒），看到 `certificate obtained` 就成了。
证书快到期时 Caddy 自动续，不用管。

### 步骤 5 · 路由器端口转发 + 防火墙

在路由器管理页加两条（不同品牌叫法不同：端口映射 / 虚拟服务器 / Port Forwarding）：

| 外部端口 | 内部 IP | 内部端口 | 协议 |
|---|---|---|---|
| 80 | 192.168.5.71 | 80 | TCP |
| 443 | 192.168.5.71 | 443 | TCP |

> 内网 IP 用 `public-check` 第 [4] 项查到的。建议顺手在路由器里给这台电脑
> 绑一个静态 DHCP（按 MAC 保留），否则重启后 IP 变了转发就失效。

> 80 被封的话只转 443，并把外部端口改成 8443（如 `8443 → 192.168.5.71:443`），
> 访问时用 `https://quant.duckdns.org:8443`。证书走 DNS-01 不受影响。

Windows 防火墙放行（**管理员 PowerShell**，注意别放行 8772）：

```powershell
netsh advfirewall firewall add rule name="Caddy HTTPS" dir=in action=allow protocol=TCP localport=443
netsh advfirewall firewall add rule name="Caddy HTTP"  dir=in action=allow protocol=TCP localport=80
```

---

## 3. 日常启动

```bash
# 最常用：应用（只听 127.0.0.1）+ Caddy（HTTPS）
deploy\start_public.bat

# 停止
deploy\stop_public.bat
```

手动启动的话：

```bash
python -m quant_platform.cli web --public --host 127.0.0.1 --port 8772
```

开机自启（可选）：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install_autostart.ps1
powershell -ExecutionPolicy Bypass -File deploy\install_autostart.ps1 -Uninstall   # 卸载
```

### 只想局域网用（不开鉴权）

```bash
python -m quant_platform.cli web --host 0.0.0.0 --port 8772     # 手机同 Wi-Fi 直接开
python -m quant_platform.cli web --no-auth                       # 配置开了鉴权时强制关闭
```

> 注意：这两种方式**不要**配上端口转发对外，那是裸奔。

### 不装 Caddy 的替代方案

uvicorn 自己也能跑 HTTPS（先自备证书）：

```bash
python -m quant_platform.cli web --public --host 0.0.0.0 --port 8443 \
       --ssl-certfile cert.pem --ssl-keyfile key.pem
```

---

## 4. 验证

按顺序做，别跳：

1. **本机**：`python -m quant_platform.cli public-check`
   —— 凭据 ✓、enabled ✓、`/healthz` 返回 `auth: true`
2. **局域网**：手机连家里 Wi-Fi，开 `https://quant.duckdns.org`
   —— 应能打开并跳转登录页（走一圈公网再回来，能验证转发是否通）
3. **真·外网**：手机关掉 Wi-Fi 用流量访问
   —— 这是唯一能证明端口转发真的生效的测试
4. **未登录**：无痕窗口开 `https://域名/api/bootstrap` → 应返回 `401`
5. **登录后**：正常看到看板；F12 → Application → Cookies 里 `ql_sess` 应带
   `HttpOnly` 和 `Secure`

---

## 5. 配置速查（`configs/public.yaml`）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 鉴权总开关 |
| `username` | `quant` | 登录用户名 |
| `session_hours` / `remember_days` | 12 / 30 | 会话有效期 |
| `secure_cookie` | `true` | 只在 HTTPS 下发 Cookie。**只能明文 HTTP 时必须改 false，否则登不上** |
| `allow_token_param` | `true` | 允许 `?token=` 直链，不需要就关掉 |
| `trusted_proxies` | `127.0.0.1` | 只有这些来源的 `X-Forwarded-For` 才采信（防伪造绕过限流） |
| `allowed_hosts` | 空 | 建议填 `["quant.duckdns.org"]`，挡掉用 IP 或陌生域名扫到你的人 |
| `mask_account` | `false` | 开=true 后外面看不到净值/股数/成本，只留标的与权重 |
| `disable_sql_query` | `false` | 开=true 后关闭 `/api/db/query` |
| `login_max_fail` / `lock_seconds` | 8 / 900 | 爆破锁定策略 |
| `api_per_min` | 600 | 单 IP 限流 |

改完重启服务生效。

---

## 6. 排查

| 现象 | 原因 / 处理 |
|---|---|
| 打开是 404 `invalid host` | `allowed_hosts` 没填你的域名，或填错 |
| 登录页输对密码却跳回登录页 | 你在用 HTTP 访问，但 `secure_cookie: true` → Cookie 发不下去。改用 HTTPS，或临时把 `secure_cookie` 改 false |
| 一直提示锁定 | 等 15 分钟，或重启服务（锁定是内存态）。确认密码正确可先 `--no-auth` 排除 |
| 手机流量打不开，Wi-Fi 能开 | 端口转发没生效 / CGNAT / 路由器防火墙。核对步骤 2 |
| Caddy 起不来，提示端口被占 | 80 或 443 被 IIS、Apache、VMware 等占用。停掉它们，或在 Caddyfile 全局块加 `auto_https disable_redirects` |
| 证书申请一直失败 | 80 端口没通（HTTP-01 需要），改 DNS-01：重新 `-WithDuckDns` 下载并配 `$DUCKDNS_TOKEN` |
| 域名解析到旧 IP | DDNS 没跑。看 `deploy\ddns.log`，或用 `schtasks /Query /TN QuantLabDDNS` |
| 账户页数值全是 `—` | 开了 `mask_account`，这是预期行为 |
| 想临时下线 | 路由器里删掉那条端口转发，最快最干净 |

---

## 7. 一条命令速查

```bash
python -m quant_platform.cli set-password                       # 设置/修改口令
python -m quant_platform.cli public-check                        # 部署自检
python -m quant_platform.cli web --public --host 127.0.0.1       # 公网模式启动
python -m quant_platform.cli web --host 0.0.0.0                  # 局域网模式（无鉴权）
python deploy/ddns.py --provider duckdns --domain q --token t    # 更新 DDNS
deploy\start_public.bat                                          # 一键起应用 + Caddy
deploy\stop_public.bat                                           # 一键停
```
