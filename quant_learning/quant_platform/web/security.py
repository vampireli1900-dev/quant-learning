# -*- coding: utf-8 -*-
"""
web/security.py —— 公网访问安全层
=================================
把「局域网只读终端」升级成「可以安全放在公网上的个人服务」。

覆盖四件事：
    1. **身份认证**：用户名 + 密码 → 签名会话 Cookie（HttpOnly / SameSite=Lax / 可选 Secure）。
    2. **访问控制**：未登录访问页面跳转 /login，访问 /api/* 返回 401。静态资源按白名单放行。
    3. **滥用防护**：登录失败按 IP 锁定、API 按 IP 限流、审计日志落盘。
    4. **响应加固**：HSTS / CSP / X-Frame-Options / nosniff / Referrer-Policy 等安全头。

零新增依赖：只用标准库（hashlib / hmac / secrets）+ 已有的 fastapi / pyyaml。

用法：
    from .security import load_public_config, install_security
    cfg = load_public_config()
    install_security(app, cfg)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

try:
    import yaml
except Exception:                                              # noqa: BLE001
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
PUBLIC_CFG = CONFIG_DIR / "public.yaml"
SECRET_CFG = CONFIG_DIR / "public.secret.yaml"      # 敏感值单独存，建议 gitignore
LOG_DIR = PROJECT_ROOT / "logs"
STATIC_DIR = Path(__file__).resolve().parent / "static"

PBKDF2_ITER = 200_000
COOKIE_NAME = "ql_sess"
TOKEN_PARAM = "token"
LOGIN_PAGE = "/login"

# 无需登录即可访问的路径
FREE_PATHS = {"/login", "/api/auth/login", "/healthz", "/api/healthz", "/robots.txt"}


# ----------------------------------------------------------------------------
# 口令哈希：pbkdf2_sha256，格式 pbkdf2$<iter>$<salt_b64>$<hash_b64>
# ----------------------------------------------------------------------------
def hash_password(password: str, iterations: int = PBKDF2_ITER, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode().rstrip("="),
        base64.b64encode(dk).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = (encoded or "").split("$")
        if algo != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        want = base64.b64decode(hash_b64 + "=" * (-len(hash_b64) % 4))
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(got, want)
    except Exception:                                          # noqa: BLE001
        return False


def make_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


# ----------------------------------------------------------------------------
# 吊销名单：会话是无状态签名令牌，登出后仍可用到过期，
# 所以给每个令牌发一个 jti，登出时把它记进名单（重启即失效，足够个人用）
# ----------------------------------------------------------------------------
_REVOKED: Dict[str, float] = {}
_REVOKE_LOCK = threading.Lock()


def revoke_token(jti: str) -> None:
    if not jti:
        return
    now = time.time()
    with _REVOKE_LOCK:
        for k, t in [kv for kv in _REVOKED.items() if now - kv[1] > 400 * 86400]:
            _REVOKED.pop(k, None)
        _REVOKED[jti] = now


def is_revoked(jti: str) -> bool:
    return bool(jti) and jti in _REVOKED


# ----------------------------------------------------------------------------
# 会话令牌：HMAC-SHA256 签名的 JSON（自带 exp，无需服务端保存）
# ----------------------------------------------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(secret: str, payload: dict) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return body + "." + sig


def parse_token(secret: str, token: str) -> Optional[dict]:
    if not secret or not token:
        return None
    try:
        body, sig = str(token).rsplit(".", 1)
    except ValueError:
        return None
    want = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, want):
        return None
    try:
        data = json.loads(_b64d(body))
    except Exception:                                          # noqa: BLE001
        return None
    if float(data.get("exp", 0)) < time.time():
        return None
    return data


# ----------------------------------------------------------------------------
# 限流 / 失败锁定（进程内，重启即清零；个人服务足够）
# ----------------------------------------------------------------------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def over(self, key: str, limit: int, window: float) -> bool:
        """窗口内第 limit+1 次调用返回 True（= 超限）。"""
        now = time.time()
        with self._lock:
            arr = self._hits.setdefault(key, [])
            arr[:] = [t for t in arr if now - t < window]
            arr.append(now)
            return len(arr) > limit

    def count(self, key: str, window: float) -> int:
        now = time.time()
        with self._lock:
            arr = self._hits.get(key, [])
            return len([t for t in arr if now - t < window])

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
@dataclass
class PublicConfig:
    enabled: bool = False
    username: str = "quant"
    password_hash: str = ""
    secret: str = ""
    session_ttl: int = 12 * 3600          # 普通登录 12 小时
    remember_ttl: int = 30 * 24 * 3600    # 记住我 30 天
    secure_cookie: bool = True            # 仅 HTTPS 下发 Cookie（公网必须开）
    allow_token_param: bool = True        # 允许 ?token= 免密访问（便于保存直链）
    trusted_proxies: List[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    allowed_hosts: List[str] = field(default_factory=list)   # 空 = 不限制 Host
    mask_account: bool = False            # 脱敏真实账户（净值/持仓/委托）
    disable_sql_query: bool = False       # 关闭 /api/db/query
    login_max_fail: int = 8
    login_window: int = 600               # 10 分钟
    lock_seconds: int = 900               # 锁 15 分钟
    api_per_min: int = 600
    audit: bool = True
    hsts_seconds: int = 31536000

    @property
    def ready(self) -> bool:
        """鉴权能否真正生效（有口令哈希 + 签名密钥）。"""
        return bool(self.password_hash) and bool(self.secret)


DEFAULT_PUBLIC_YAML = """\
# ============================================================
# configs/public.yaml —— 公网访问配置（非敏感项）
# 敏感值（签名密钥 / 口令哈希）在 configs/public.secret.yaml，
# 由 `python -m quant_platform.cli set-password` 自动生成。
# ============================================================
public:
  # 是否启用登录鉴权。false = 局域网直连模式（不加任何限制）
  enabled: false

  # 登录用户名
  username: quant

  # 会话有效期（小时）；勾「记住我」时用 remember_days
  session_hours: 12
  remember_days: 30

  # 只在 HTTPS 下下发会话 Cookie。公网部署必须为 true
  secure_cookie: true

  # 允许用 ?token=xxx 免密直连（登录后可在「分享直链」里生成）
  allow_token_param: true

  # 可信反向代理 IP（只有来自它们的 X-Forwarded-For 才被采信）
  trusted_proxies:
    - 127.0.0.1
    - ::1

  # 允许的 Host 头（留空 = 不校验）；建议填你的 DDNS 域名，防止域名扫描
  allowed_hosts: []

  # ---- 暴露范围 ----
  # 脱敏真实账户：隐藏净值/持仓/委托，只留策略研究与行情
  mask_account: false
  # 关闭自定义 SQL 查询接口 /api/db/query
  disable_sql_query: false

  # ---- 滥用防护 ----
  login_max_fail: 8        # 10 分钟内失败次数上限
  lock_seconds: 900        # 超限后锁定秒数
  api_per_min: 600         # 单 IP 每分钟 API 上限
  audit: true              # 记录访问日志到 logs/public_access.log
"""


def _read_yaml(path: Path) -> dict:
    if yaml is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:                                          # noqa: BLE001
        return {}


def load_public_config(verbose: bool = False) -> PublicConfig:
    """读取 public.yaml + public.secret.yaml；缺失时生成默认文件与密钥。"""
    cfg = PublicConfig()
    raw = _read_yaml(PUBLIC_CFG)
    p = raw.get("public", raw) if isinstance(raw, dict) else {}

    def g(k, default):
        v = p.get(k, default)
        return default if v is None else v

    cfg.enabled = bool(g("enabled", False))
    cfg.username = str(g("username", "quant"))
    cfg.session_ttl = int(float(g("session_hours", 12)) * 3600)
    cfg.remember_ttl = int(float(g("remember_days", 30)) * 86400)
    cfg.secure_cookie = bool(g("secure_cookie", True))
    cfg.allow_token_param = bool(g("allow_token_param", True))
    tp = g("trusted_proxies", ["127.0.0.1", "::1"]) or []
    cfg.trusted_proxies = [str(x) for x in tp]
    ah = g("allowed_hosts", []) or []
    cfg.allowed_hosts = [str(x).lower() for x in ah if str(x).strip()]
    cfg.mask_account = bool(g("mask_account", False))
    cfg.disable_sql_query = bool(g("disable_sql_query", False))
    cfg.login_max_fail = int(g("login_max_fail", 8))
    cfg.lock_seconds = int(g("lock_seconds", 900))
    cfg.api_per_min = int(g("api_per_min", 600))
    cfg.audit = bool(g("audit", True))

    # ---- 敏感值 ----
    sec = _read_yaml(SECRET_CFG)
    sec = sec.get("public_secret", sec) if isinstance(sec, dict) else {}
    cfg.password_hash = str(sec.get("password_hash", "") or "")
    cfg.secret = str(sec.get("secret", "") or "")
    dirty = False
    if not cfg.secret:
        cfg.secret = make_secret()
        dirty = True
    # 环境变量优先（方便容器/系统服务注入，不落盘）
    cfg.secret = os.environ.get("QUANTLAB_SECRET", cfg.secret)
    env_hash = os.environ.get("QUANTLAB_PASSWORD_HASH")
    if env_hash:
        cfg.password_hash = env_hash
    if dirty:
        save_secret(cfg, verbose=verbose)

    if not PUBLIC_CFG.exists():
        try:
            PUBLIC_CFG.write_text(DEFAULT_PUBLIC_YAML, encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            pass
    return cfg


def save_secret(cfg: PublicConfig, verbose: bool = False) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        body = (
            "# 自动生成，请勿提交到版本库；修改口令请用\n"
            "#   python -m quant_platform.cli set-password\n"
            "public_secret:\n"
            f"  secret: {cfg.secret}\n"
            f"  password_hash: {cfg.password_hash}\n"
        )
        SECRET_CFG.write_text(body, encoding="utf-8")
        if verbose:
            print(f"[security] 已写入 {SECRET_CFG}")
    except Exception as e:                                     # noqa: BLE001
        print(f"[security] 写入 {SECRET_CFG} 失败：{e}")


def write_password(cfg: PublicConfig, password: str) -> None:
    cfg.password_hash = hash_password(password)
    save_secret(cfg)


# ----------------------------------------------------------------------------
# 审计日志
# ----------------------------------------------------------------------------
_AUDIT_LOCK = threading.Lock()
_MAX_LOG_BYTES = 4 * 1024 * 1024


def audit(event: str, ip: str = "", detail: str = "") -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fp = LOG_DIR / "public_access.log"
        with _AUDIT_LOCK:
            if fp.exists() and fp.stat().st_size > _MAX_LOG_BYTES:
                try:
                    fp.replace(fp.with_suffix(".1.log"))
                except Exception:                              # noqa: BLE001
                    pass
            line = "%s | %-14s | %-16s | %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), event, ip[:45], detail[:160],
            )
            with fp.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:                                          # noqa: BLE001
        pass


# ----------------------------------------------------------------------------
# 访问控制
# ----------------------------------------------------------------------------
def client_ip(request: Request, trusted: List[str]) -> str:
    host = request.client.host if request.client else "unknown"
    if host in trusted:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        real = request.headers.get("x-real-ip", "")
        if real:
            return real.strip()
    return host


def is_free_path(path: str) -> bool:
    if path in FREE_PATHS:
        return True
    if path.startswith("/static/"):
        rest = path[len("/static/"):]
        # 页面本体必须登录（防止绕过 / 和 /m 直取）
        if rest in ("index.html", "mobile.html"):
            return False
        if rest in ("login.html", "mobile.webmanifest"):
            return True
        if rest.startswith(("css/", "js/", "vendor/")):
            return True
        if rest.endswith((".png", ".ico", ".svg", ".json", ".webmanifest", ".txt")):
            return True
        return False
    return False


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "X-Permitted-Cross-Domain-Policies": "none",
}

# 前端全部是本地资源（含 vendor/lightweight-charts），可以收紧 CSP
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "manifest-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class AuthGuard:
    def __init__(self, cfg: PublicConfig) -> None:
        self.cfg = cfg
        self.limiter = RateLimiter()
        self._lock_at: Dict[str, float] = {}

    # -- 凭证提取 --
    def credential(self, request: Request) -> Optional[dict]:
        tok = request.cookies.get(COOKIE_NAME, "")
        if not tok:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                tok = auth[7:].strip()
        if not tok and self.cfg.allow_token_param:
            tok = request.query_params.get(TOKEN_PARAM, "") or ""
        data = parse_token(self.cfg.secret, tok) if tok else None
        if data and is_revoked(str(data.get("j", ""))):
            return None
        return data

    def locked(self, ip: str) -> int:
        """返回剩余锁定秒数，0 = 未锁。"""
        at = self._lock_at.get(ip)
        if not at:
            return 0
        left = self.cfg.lock_seconds - (time.time() - at)
        if left <= 0:                       # 锁定期满，自动解除
            self._lock_at.pop(ip, None)
            self.limiter.reset("fail:" + ip)
            return 0
        return int(left)

    def note_fail(self, ip: str) -> bool:
        """记一次失败；达到上限则锁定，返回是否被锁。"""
        if self.limiter.over("fail:" + ip, max(0, self.cfg.login_max_fail - 1), self.cfg.login_window):
            self._lock_at[ip] = time.time()
            self.limiter.reset("fail:" + ip)
            return True
        return False

    # -- 中间件主体 --
    async def __call__(self, request: Request, call_next):
        cfg = self.cfg
        if not cfg.enabled or not cfg.ready:
            resp = await call_next(request)
            self._headers(resp, request)
            return resp

        path = request.url.path
        ip = client_ip(request, cfg.trusted_proxies)

        # Host 白名单（防域名扫描 / DNS rebinding）
        if cfg.allowed_hosts:
            host = (request.headers.get("host", "").split(":")[0] or "").lower()
            if host and host not in cfg.allowed_hosts and host not in ("localhost",):
                return JSONResponse({"detail": "invalid host"}, status_code=404)

        if is_free_path(path):
            resp = await call_next(request)
            self._headers(resp, request)
            return resp

        # API 限流
        if path.startswith("/api/") and cfg.api_per_min > 0:
            if self.limiter.over("api:" + ip, cfg.api_per_min, 60):
                audit("rate_limit", ip, path)
                return JSONResponse({"detail": "too many requests"}, status_code=429)

        user = self.credential(request)
        if not user:
            if path.startswith("/api/"):
                audit("401", ip, path)
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            nxt = path + (("?" + request.url.query) if request.url.query else "")
            return RedirectResponse(f"{LOGIN_PAGE}?next={_q(nxt)}", status_code=302)

        resp = await call_next(request)
        self._headers(resp, request)
        return resp

    def _headers(self, resp: Response, request: Request) -> None:
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        if str(request.url.scheme) == "https" and self.cfg.hsts_seconds:
            resp.headers.setdefault(
                "Strict-Transport-Security", f"max-age={self.cfg.hsts_seconds}; includeSubDomains"
            )
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


# ----------------------------------------------------------------------------
# 账户脱敏（mask_account = true 时启用）
# ----------------------------------------------------------------------------
# 脱敏时保留的字段名（比例类，看得出结构但看不出规模）
MASK_KEEP = ("weight", "ratio", "percent", "pct", "占比", "权重", "比例")


def mask_account_payload(d: dict) -> dict:
    """
    把真实金额 / 股数 / 成本一律抹成 None，只留标的、币种与权重占比。

    用「除比例外数值全隐藏」而不是挑关键词，是因为账户接口字段多（市值、成本、
    可用资金、维持保证金…），挑关键词一定会漏，漏一个就等于没脱敏。
    """
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        lk = str(k).lower()
        if isinstance(v, dict):
            out[k] = mask_account_payload(v)
        elif isinstance(v, list):
            out[k] = [mask_account_payload(x) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v if any(s in lk for s in MASK_KEEP) else None
        else:
            out[k] = v
    return out


# ----------------------------------------------------------------------------
# 登录页
# ----------------------------------------------------------------------------
LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>QuantLab · 登录</title>
<link rel="icon" href="/static/icon-192.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<style>
  :root{--bg:#0b0e14;--card:#131722;--line:#222836;--txt:#d5dae3;--dim:#7c8798;--acc:#2962ff;--up:#e0524f;--warn:#f0a02a}
  *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
  body{min-height:100vh;background:radial-gradient(1100px 600px at 50% -10%,#16203a 0%,var(--bg) 60%);
       display:flex;align-items:center;justify-content:center;padding:24px;
       font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--txt)}
  .box{width:100%;max-width:380px}
  .brand{display:flex;align-items:center;gap:10px;margin-bottom:22px}
  .logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#2962ff,#658cff);
        display:flex;align-items:center;justify-content:center}
  .logo svg{width:20px;height:20px}
  .brand h1{font-size:17px;font-weight:650;letter-spacing:.3px}
  .brand span{font-size:11px;color:var(--dim);display:block;font-weight:400;letter-spacing:.6px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px 22px;
        box-shadow:0 18px 50px rgba(0,0,0,.45)}
  .card h2{font-size:15px;font-weight:600;margin-bottom:4px}
  .card p.sub{font-size:12.5px;color:var(--dim);margin-bottom:18px}
  label{display:block;font-size:12px;color:var(--dim);margin:0 0 6px 2px}
  input{width:100%;height:44px;padding:0 13px;border-radius:10px;border:1px solid var(--line);
        background:#0e121b;color:var(--txt);font-size:15px;outline:none;transition:border-color .18s,box-shadow .18s}
  input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(41,98,255,.16)}
  .f{margin-bottom:13px}
  .row{display:flex;align-items:center;justify-content:space-between;margin:4px 0 16px}
  .chk{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--dim);cursor:pointer;user-select:none}
  .chk input{width:15px;height:15px;accent-color:var(--acc)}
  button{width:100%;height:44px;border:0;border-radius:10px;background:linear-gradient(135deg,#2962ff,#4b7dff);
         color:#fff;font-size:15px;font-weight:600;cursor:pointer;transition:transform .12s,filter .18s}
  button:hover{filter:brightness(1.08)}
  button:active{transform:scale(.985)}
  button:disabled{opacity:.55;cursor:default}
  .err{margin-top:13px;padding:10px 12px;border-radius:9px;font-size:12.5px;display:none;
       background:rgba(224,82,79,.12);border:1px solid rgba(224,82,79,.35);color:#ff8a86}
  .err.on{display:block;animation:sh .3s ease}
  @keyframes sh{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
  .warn{margin-top:14px;padding:10px 12px;border-radius:9px;font-size:12px;line-height:1.55;
        background:rgba(240,160,42,.1);border:1px solid rgba(240,160,42,.3);color:#f4bd6a;display:none}
  .warn.on{display:block}
  .foot{margin-top:16px;text-align:center;font-size:11.5px;color:#5b6577;line-height:1.7}
  .lang{float:right;font-size:12px;color:var(--dim);cursor:pointer;user-select:none;padding:2px 8px;
        border:1px solid var(--line);border-radius:20px}
  .lang:hover{color:var(--txt);border-color:#38415a}
</style>
</head>
<body>
<div class="box">
  <div class="brand">
    <div class="logo"><svg viewBox="0 0 24 24" fill="none"><path d="M4 16.5 9.5 11l3.5 3.5L20 7"
      stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
    <div><h1>QuantLab<span>量化模型实验平台</span></h1></div>
  </div>
  <div class="card">
    <span class="lang" id="lang">EN</span>
    <h2 id="tTitle">登录</h2>
    <p class="sub" id="tSub">请输入账号密码以访问终端</p>
    <form id="f" autocomplete="on">
      <div class="f"><label id="tUser" for="u">用户名</label>
        <input id="u" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></div>
      <div class="f"><label id="tPass" for="p">密码</label>
        <input id="p" name="password" type="password" autocomplete="current-password" required></div>
      <div class="row">
        <label class="chk"><input type="checkbox" id="rm"><span id="tRm">记住我（30 天）</span></label>
      </div>
      <button type="submit" id="go">登 录</button>
    </form>
    <div class="err" id="err"></div>
    <div class="warn" id="warn"></div>
  </div>
  <div class="foot" id="tFoot">仅本人使用 · 连续输错会临时锁定 IP<br>建议通过 HTTPS 域名访问</div>
</div>
<script>
const S={zh:{title:'登录',sub:'请输入账号密码以访问终端',user:'用户名',pass:'密码',rm:'记住我（30 天）',
  go:'登 录',foot:'仅本人使用 · 连续输错会临时锁定 IP<br>建议通过 HTTPS 域名访问',
  footTun:'仅本人使用 · 连续输错会临时锁定 IP<br>Tailscale 隧道已端到端加密',
  e1:'用户名或密码错误',e2:'尝试过于频繁，请稍后再试',e3:'网络错误，请重试',
  w:'当前通过 <b>HTTP</b> 明文访问，密码可能被网络中的其他人截获。强烈建议配置 HTTPS。'},
  en:{title:'Sign in',sub:'Enter your credentials to open the terminal',user:'Username',pass:'Password',
  rm:'Remember me (30 days)',go:'Sign in',foot:'Personal use only · repeated failures lock the IP<br>Always prefer HTTPS',
  footTun:'Personal use only · repeated failures lock the IP<br>End-to-end encrypted via Tailscale',
  e1:'Wrong username or password',e2:'Too many attempts, try again later',e3:'Network error, please retry',
  w:'You are on plain <b>HTTP</b> — your password can be intercepted. Please enable HTTPS.'}};
let lang=(localStorage.getItem('qp_lang')||'zh');
// Tailscale 的 100.64.0.0/10 与 *.ts.net 走的是 WireGuard 加密隧道，
// 虽然浏览器显示 http://，但流量本身已是端到端加密，不该报警、footer 也不该劝上 HTTPS
var _h=location.hostname;
var _tun=/^100\\.(6[4-9]|[7-9]\\d|1[01]\\d|12[0-7])\\./.test(_h)||/\\.ts\\.net$/.test(_h);
function apply(){const d=S[lang]||S.zh;
 document.getElementById('tTitle').textContent=d.title;document.getElementById('tSub').textContent=d.sub;
 document.getElementById('tUser').textContent=d.user;document.getElementById('tPass').textContent=d.pass;
 document.getElementById('tRm').textContent=d.rm;document.getElementById('go').textContent=d.go;
 document.getElementById('tFoot').innerHTML=_tun?d.footTun:d.foot;
 document.getElementById('lang').textContent=lang==='zh'?'EN':'中';
 document.documentElement.lang=lang==='zh'?'zh-CN':'en';
 const e=document.getElementById('err'); if(e.dataset.k) e.textContent=d[e.dataset.k];}
document.getElementById('lang').onclick=()=>{lang=lang==='zh'?'en':'zh';localStorage.setItem('qp_lang',lang);apply();};
apply();
if(location.protocol==='http:'&&_h!=='127.0.0.1'&&_h!=='localhost'&&!_tun){
  const w=document.getElementById('warn');w.innerHTML=(S[lang]||S.zh).w;w.classList.add('on');}
const next=new URLSearchParams(location.search).get('next')||'/';
document.getElementById('f').onsubmit=async ev=>{ev.preventDefault();
  const b=document.getElementById('go'),e=document.getElementById('err');
  b.disabled=true;b.textContent='...';
  try{const r=await fetch('/api/auth/login?next='+encodeURIComponent(next),{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:document.getElementById('u').value,
      password:document.getElementById('p').value,remember:document.getElementById('rm').checked})});
   const d=await r.json();
   if(r.ok){location.href=d.redirect||next;}
   else{e.dataset.k=r.status===429?'e2':'e1';e.textContent=(S[lang]||S.zh)[e.dataset.k]||d.detail||'error';
        e.classList.add('on');b.disabled=false;b.textContent=(S[lang]||S.zh).go;}
  }catch(err){e.dataset.k='e3';e.textContent=(S[lang]||S.zh).e3;e.classList.add('on');
   b.disabled=false;b.textContent=(S[lang]||S.zh).go;}};
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# 安装到 FastAPI
# ----------------------------------------------------------------------------
def install_security(app, cfg: Optional[PublicConfig] = None) -> PublicConfig:
    cfg = cfg or load_public_config()
    guard = AuthGuard(cfg)
    app.state.public_cfg = cfg
    app.state.auth_guard = guard

    @app.middleware("http")
    async def _security_mw(request: Request, call_next):
        return await guard(request, call_next)

    @app.get("/login", include_in_schema=False)
    def _login_page():
        return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})

    @app.post("/api/auth/login", include_in_schema=False)
    async def _login(request: Request):
        ip = client_ip(request, cfg.trusted_proxies)
        left = guard.locked(ip)
        if left:
            audit("login_locked", ip, f"{left}s")
            return JSONResponse({"detail": "locked", "retry_after": left}, status_code=429)
        try:
            body = await request.json()
        except Exception:                                      # noqa: BLE001
            body = {}
        user = str(body.get("username", "") or "")
        pw = str(body.get("password", "") or "")
        remember = bool(body.get("remember", False))
        ok = hmac.compare_digest(user, cfg.username) and cfg.password_hash and verify_password(pw, cfg.password_hash)
        if not ok:
            guard.note_fail(ip)
            left2 = guard.locked(ip)
            audit("login_fail", ip, f"user={user[:20]}" + (f" locked={left2}s" if left2 else ""))
            return JSONResponse(
                {"detail": "bad credentials", "retry_after": left2},
                status_code=429 if left2 else 401,
            )
        ttl = cfg.remember_ttl if remember else cfg.session_ttl
        tok = sign_token(cfg.secret, {"u": user, "exp": time.time() + ttl,
                                      "t": int(time.time()), "j": secrets.token_hex(8)})
        audit("login_ok", ip, f"user={user} remember={remember}")
        resp = JSONResponse({"ok": True, "redirect": _safe_next(request)})
        resp.set_cookie(
            COOKIE_NAME, tok, max_age=ttl, httponly=True,
            secure=cfg.secure_cookie and str(request.url.scheme) == "https",
            samesite="lax", path="/",
        )
        return resp

    @app.get("/api/auth/logout", include_in_schema=False)
    @app.post("/api/auth/logout", include_in_schema=False)
    def _logout(request: Request):
        cur = guard.credential(request)
        if cur:
            revoke_token(str(cur.get("j", "")))
            audit("logout", client_ip(request, cfg.trusted_proxies), cur.get("u", ""))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp

    @app.get("/api/auth/token", include_in_schema=False)
    def _token(request: Request):
        """给已登录用户生成一个可保存的直链 token（?token=）。"""
        if not cfg.allow_token_param:
            return JSONResponse({"detail": "disabled"}, status_code=403)
        cur = guard.credential(request)
        if not cur:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        ttl = int(request.query_params.get("days", "30"))
        ttl = max(1, min(ttl, 365)) * 86400
        tok = sign_token(cfg.secret, {"u": cur.get("u", cfg.username), "exp": time.time() + ttl,
                                      "t": int(time.time()), "k": "link", "j": secrets.token_hex(8)})
        return {"token": tok, "expires_in": ttl}

    @app.get("/healthz", include_in_schema=False)
    @app.get("/api/healthz", include_in_schema=False)
    def _healthz():
        return {"ok": True, "auth": bool(cfg.enabled and cfg.ready), "ts": int(time.time())}

    if cfg.enabled and cfg.ready:
        print(f"[security] 公网鉴权已启用：用户 {cfg.username}｜会话 {cfg.session_ttl//3600}h｜"
              f"Secure Cookie={cfg.secure_cookie}｜限流 {cfg.api_per_min}/min")
    elif cfg.enabled:
        print("[security] 警告：public.enabled=true 但未设置口令，鉴权未生效！"
              " 请执行 `python -m quant_platform.cli set-password`")
    return cfg


def _safe_next(request: Request) -> str:
    nxt = request.query_params.get("next", "") or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        return "/"
    return nxt
