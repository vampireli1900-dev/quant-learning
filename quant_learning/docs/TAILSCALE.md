# 没有公网 IP 怎么办：Tailscale 组网

> 项目路径 `D:\pycharm_file\2026_stork\quant_learning`
> Python 用 `E:\Programs\miniconda\envs\aicode\python.exe`（PATH 里的 python 没装依赖）

---

## 1. 为什么端口转发这条路走不通

端口转发的前提是「外面的人能主动找到你家路由器」。但运营商为了省 IPv4 地址，
在机房又做了一层 NAT（**CGNAT**），你的路由器拿到的是一个内网地址：

```
你的电脑 → 路由器 → 运营商 CGNAT 机房 → 互联网
                     ↑
              路由器 WAN 口是 100.x / 10.x / 192.168.x
              外面的请求到不了这里，转发规则写了也没人能触发
```

判断方法：路由器管理页看 **WAN 口 IP**。如果它是下面这些，就是 CGNAT：

| WAN 地址段 | 结论 |
|---|---|
| `100.64.x.x` ~ `100.127.x.x` | CGNAT（最常见） |
| `10.x.x.x` / `172.16-31.x` / `192.168.x.x` | 内网地址 |
| 跟 `public-check` 第 [6] 项探测到的公网 IP **不一致** | 多层 NAT |

还有一种办法是给运营商客服打电话要公网 IP（部分宽带可以免费开通，
就说家里要装监控/NAS），但成功率看地区和套餐，**不保证**。

---

## 2. Tailscale 的思路：反过来连

既然外面进不来，那就让家里的机器**主动出去**：

```
   家里的电脑  ──主动外连──▶  Tailscale 协调服务器（只负责牵线）
        ▲                              │
        │                              ▼
   你的手机   ────────────▶  两者之间建立 WireGuard 加密隧道
                             手机直接访问 http://100.x.y.z:8772/m
```

- 流量点对点加密，不经过第三方服务器（打洞失败时才走中继）
- **路由器不用开任何端口，不需要公网 IP，不需要 DDNS，不需要域名**
- 手机和电脑必须在同一个 Tailscale 账号下（= 你的私人网络）

---

## 3. 三步上手

### 第 1 步 · 电脑装 Tailscale

```powershell
powershell -ExecutionPolicy Bypass -File deploy\tailscale_setup.ps1
```

脚本会：检查/安装 → 打开浏览器让你授权登录 → 打印本机在虚拟网里的 IP 和名字。

装完后确认：

```bash
E:\Programs\miniconda\envs\aicode\python.exe -m quant_platform.cli public-check
# 看第 [5] 项：✓ 已登录 100.x.y.z
```

### 第 2 步 · 手机装 App（一次性）

1. iOS：App Store 搜 **Tailscale**；Android：Google Play，或官网下 apk
2. 用**和电脑同一个账号**登录
3. 打开 VPN 开关后就不用管了（后台常驻，耗电很低）

> 这一步是 Tailscale 唯一的"代价"：手机要装 App。换来的是最稳、最安全的方案。

### 第 3 步 · 启动服务

```bat
deploy\start_tailscale.bat
```

它会把服务**只绑到 Tailscale 的那个 IP**（不是 `0.0.0.0`）：

```
家里局域网的其他设备  → 连不上
互联网上的任何人      → 连不上
登录了你 Tailscale 的设备 → 能访问
```

手机浏览器打开（脚本会直接打印）：

```
http://100.x.y.z:8772/m
```

或者用好记的名字（MagicDNS，不用背 IP）：

```
http://你的机器名.tailnet名.ts.net:8772/m
```

登录页输入你设的用户名密码即可（**Tailscale 加密 + 应用登录双重保护**）。

---

## 4. 进阶

### 想要 https://（虚拟网内自动证书）

```powershell
cd "C:\Program Files\Tailscale"
.\tailscale.exe serve --bg --https=443 http://127.0.0.1:8772
```

然后手机访问 `https://你的机器名.ts.net`（Tailscale 会签一张该域名证书，
装了 App 的设备自动信任）。不强制 —— 隧道本身已经是加密的，
http:// 在 Tailscale 内部其实是安全的。

### 密钥过期（180 天后要重新登录）

管理后台 https://login.tailscale.com/admin/machines
→ 点你的机器 → **Disable key expiry**，一次设置永久有效。

### 只想让手机访问这一台机器（ACL）

后台 → Access Controls，默认规则是「同账号设备互相全通」。
个人使用够用；想收紧再改。

### 怎么停 / 卸载

```bat
deploy\stop_public.bat                 :: 停服务
```
Tailscale 本身：托盘图标 → Log out / Disconnect；
卸载走「设置 → 应用 → Tailscale」。

---

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| 手机打不开，但 `public-check` 显示已登录 | ①手机 Tailscale App 的 VPN 开关是否打开 ②手机和电脑是否同一账号 ③服务是否绑在 Tailscale IP 上（用 `start_tailscale.bat`） |
| 登录页提示"明文 HTTP 不安全" | 代码已排除 Tailscale 地址（100.64/10 段、*.ts.net），不会误报；如果还出现，说明你用的不是隧道地址 |
| 连不上、很慢 | 两端可能在走中继（打洞失败）。看 `tailscale status`，直连会显示 `direct`，中继显示 `relay`。家用路由一般能直连 |
| 电脑关机后手机就连不上 | 正常，服务跑在你家电脑上。想 7×24 就得让电脑别睡：电源设置里关掉睡眠 |
| 想给家人也访问 | 后台发邀请（免费版可加用户），或后面接 Cloudflare Tunnel 用域名公开 |

---

## 6. 关于你打算申请的域名

**Tailscale 这个方案不需要域名** —— 它用 MagicDNS 给你的机器一个
`机器名.tailnet.ts.net` 的名字，功能等价。所以域名可以先不急。

等你拿到域名，想升级成「不装 App、手机浏览器直接开 https://你的域名」时，
再走 **Cloudflare Tunnel**：它和 Tailscale 一样是主动外连，**同样不需要公网 IP**，
把域名托管到 Cloudflare 后跑一条 `cloudflared` 就行，两者可以并存。

到那时告诉我，我把 tunnel 的配置和启动脚本一起补上。

> 顺带一句：如果你申请域名只是为了自己远程访问，Tailscale 已经完全够用且更私密
> （你的服务不会出现在任何公网扫描里）。域名更适合「要给别人访问」的场景。
