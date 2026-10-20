# -*- coding: utf-8 -*-
"""
deploy/ddns.py —— 动态 DNS 更新（家宽 IP 会变，靠它把域名一直指向你家）

支持两种：
    duckdns   免费、无需注册信用卡，官方支持 Let's Encrypt 签发证书（推荐）
    generic   任何「访问一个 URL 就更新」的服务商（No-IP / Dynu / 花生壳 / 3322 等）

用法：
    # 单次更新
    python deploy/ddns.py --provider duckdns --domain quant --token 你的token

    # 常驻（每 10 分钟检查一次，IP 变了才更新）
    python deploy/ddns.py --provider duckdns --domain quant --token 你的token --daemon 600

    # 通用服务商（{ip} 会被替换成当前公网 IP）
    python deploy/ddns.py --provider generic \
        --url "https://dyn.dns.he.net/nic/update?hostname=xx.com&myip={ip}" \
        --auth "user:pass"

    # 注册成 Windows 计划任务（开机自启、每 10 分钟跑一次）
    python deploy/ddns.py --provider duckdns --domain quant --token 你的token --install-task

token 也可以放在 deploy/.caddy-token 或环境变量 DUCKDNS_TOKEN，避免写进命令行历史。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
import urllib.error
import base64
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "ddns.log"
LAST_IP = HERE / ".last_ip"

IP_SERVICES = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://myip.ipip.net",
    "http://ip.3322.net",
]


def log(msg: str) -> None:
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def public_ip() -> str:
    for url in IP_SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                ip = r.read().decode().strip()
            # ipip.net 返回「当前 IP：1.2.3.4 来自于：...」
            if "：" in ip or ":" in ip and not ip.count(".") == 3:
                import re
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", ip)
                ip = m.group(1) if m else ip
            if ip.count(".") == 3 and all(p.isdigit() for p in ip.split(".")):
                return ip
        except Exception:
            continue
    raise RuntimeError("无法探测公网 IP（所有探测服务都失败）")


def last_ip() -> str:
    try:
        return LAST_IP.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_ip(ip: str) -> None:
    try:
        LAST_IP.write_text(ip, encoding="utf-8")
    except Exception:
        pass


def update_duckdns(domain: str, token: str, ip: str) -> str:
    url = f"https://www.duckdns.org/update?domains={domain}&token={token}&ip={ip}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode().strip()


def update_generic(url: str, auth: str, ip: str) -> str:
    req = urllib.request.Request(url.replace("{ip}", ip))
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode().strip()[:120]


def resolve_token(args) -> str:
    if args.token:
        return args.token
    env = os.environ.get("DUCKDNS_TOKEN")
    if env:
        return env
    f = HERE / ".caddy-token"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    raise SystemExit("缺少 token：用 --token 传入，或设环境变量 DUCKDNS_TOKEN，"
                     "或写入 deploy/.caddy-token")


def install_task(args) -> None:
    """注册 Windows 计划任务：开机启动 + 每 10 分钟一次。"""
    py = sys.executable
    script = str(Path(__file__).resolve())
    cmd = [py, script, "--provider", args.provider]
    if args.provider == "duckdns":
        cmd += ["--domain", args.domain, "--token", resolve_token(args)]
    else:
        cmd += ["--url", args.url]
        if args.auth:
            cmd += ["--auth", args.auth]
    cmdline = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    name = "QuantLabDDNS"
    try:
        subprocess.run(
            ["schtasks", "/Create", "/F", "/TN", name, "/SC", "MINUTE", "/MO", "10",
             "/TR", cmdline, "/RL", "HIGHEST"],
            check=True, capture_output=True, text=True,
        )
    except Exception as e:
        raise SystemExit(f"注册计划任务失败（需要管理员权限）：{e}")
    log(f"已注册计划任务 {name}（每 10 分钟）")
    log("  查看： schtasks /Query /TN QuantLabDDNS")
    log("  删除： schtasks /Delete /TN QuantLabDDNS /F")


def main() -> None:
    ap = argparse.ArgumentParser(description="动态 DNS 更新")
    ap.add_argument("--provider", choices=["duckdns", "generic"], default="duckdns")
    ap.add_argument("--domain", help="duckdns 的子域名（不含 .duckdns.org）")
    ap.add_argument("--token", help="duckdns token")
    ap.add_argument("--url", help="generic 模式的更新 URL，含 {ip} 占位符")
    ap.add_argument("--auth", default="", help="generic 模式的 user:pass")
    ap.add_argument("--daemon", type=int, default=0, metavar="SEC",
                    help="常驻模式：每隔这么多秒检查一次（建议 600）")
    ap.add_argument("--install-task", action="store_true", help="注册为 Windows 计划任务")
    args = ap.parse_args()

    if args.install_task:
        install_task(args)
        return

    if args.provider == "duckdns" and not args.domain:
        raise SystemExit("duckdns 模式需要 --domain")

    while True:
        try:
            ip = public_ip()
        except Exception as e:
            log(f"探测失败：{e}")
        else:
            old = last_ip()
            if ip == old:
                log(f"IP 未变化 {ip}，跳过")
            else:
                try:
                    if args.provider == "duckdns":
                        r = update_duckdns(args.domain, resolve_token(args), ip)
                    else:
                        r = update_generic(args.url, args.auth, ip)
                    save_ip(ip)
                    log(f"已更新 {old or '(首次)'} → {ip}  响应：{r}")
                except Exception as e:
                    log(f"更新失败：{type(e).__name__}: {e}")
        if not args.daemon:
            return
        time.sleep(max(60, args.daemon))


if __name__ == "__main__":
    main()
