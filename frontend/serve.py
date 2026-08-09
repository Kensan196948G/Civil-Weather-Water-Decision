#!/usr/bin/env python3
"""WebUI 開発サーバ（自動IP＋空きポート, サーバ側アダプタ注入）。

`.dc.html` をディスク上は無改修のまま、HTTP 応答時に
  ・<head> に API ベース設定（?api= / localStorage）
  ・</body> 直前に data-adapter.js
を注入して返す。document.write を使わないため、Leaflet 等の外部 script は
通常の parser script として読み込まれ、Chrome の parser-blocking 警告が出ない。

  python3 frontend/serve.py
  → 表示された URL を開く。CW_BACKEND_PROXY_BASE を設定すると /api 等を同一オリジンで backend へ proxy する。
"""
import http.server
import json
import os
import socket
import socketserver
import urllib.error
import urllib.request
from urllib.parse import unquote, urlparse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design")
os.chdir(ROOT)
DC = "気象河川施工判断支援.dc.html"
BACKEND_PROXY_BASE = os.environ.get("CW_BACKEND_PROXY_BASE", "").rstrip("/")
BACKEND_PROXY_ALLOWED_HOSTS = {
    host.strip()
    for host in os.environ.get("CW_BACKEND_PROXY_ALLOWED_HOSTS", "127.0.0.1,localhost,::1").split(",")
    if host.strip()
}

CFG_TEMPLATE = """<script>(function(){
function d(h){return h==='localhost'||h==='127.0.0.1'||h==='::1'||/^10\\./.test(h)||/^192\\.168\\./.test(h)||/^172\\.(1[6-9]|2[0-9]|3[0-1])\\./.test(h);}
function s(v){if(!v)return'';try{var u=new URL(v,location.origin);if(u.origin===location.origin)return u.origin;if(d(location.hostname)&&(d(u.hostname)||u.hostname===location.hostname))return u.origin;}catch(e){}return'';}
var p=new URLSearchParams(location.search);var a=p.get('api');try{if(a!==null){a=s(a);if(a)localStorage.setItem('cw_api',a);else localStorage.removeItem('cw_api');}else a=s(localStorage.getItem('cw_api'));}catch(e){}
window.__CW_API_BASE__=a||'';%s})();</script>"""
ADP = '<script src="./data-adapter.js"></script>'
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=31536000",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "X-Permitted-Cross-Domain-Policies": "none",
    "X-Download-Options": "noopen",
}
CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://static.cloudflareinsights.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://cyberjapandata.gsi.go.jp; "
    "font-src 'self' data:; "
    "connect-src 'self' http://127.0.0.1:* http://localhost:*; "
    "frame-src 'none'; "
    "worker-src 'none'; "
    "manifest-src 'self'"
)

# 国土地理院 標準地図（APIキー不要・公式）。外部タイル未設定時に既定として使用し、
# マップ非表示（背景タイル欠落）を解消する。利用規約: https://maps.gsi.go.jp/development/ichiran.html
GSI_TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"
GSI_TILE_ATTRIBUTION = "国土地理院"


def _tile_url_setting():
    raw = os.environ.get("CW_TILE_URL")
    if raw is None:
        return GSI_TILE_URL  # 未設定時は国土地理院を既定（本番systemdも明示設定に更新済み）
    raw = raw.strip()
    if raw.lower() in {"", "none", "off", "disabled"}:
        return ""
    return raw


def _tile_attribution_setting():
    raw = os.environ.get("CW_TILE_ATTRIBUTION")
    if raw is None:
        return GSI_TILE_ATTRIBUTION
    return raw.strip()


def config_script():
    tile = _tile_url_setting()
    tile_attr = _tile_attribution_setting()
    tile_js = "window.__CW_TILE_URL__=" + json.dumps(tile) + ";"
    tile_js += "window.__CW_TILE_ATTRIBUTION__=" + json.dumps(tile_attr) + ";"
    return CFG_TEMPLATE % tile_js


def _is_proxy_path(path):
    return path.startswith("/api/") or path == "/api" or path in {"/health", "/readyz"}


def _safe_backend_base():
    if not BACKEND_PROXY_BASE:
        return ""
    try:
        parsed = urlparse(BACKEND_PROXY_BASE)
    except Exception:
        return ""
    if parsed.scheme != "http":
        return ""
    if parsed.hostname not in BACKEND_PROXY_ALLOWED_HOSTS:
        return ""
    if not parsed.port:
        return ""
    return BACKEND_PROXY_BASE


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _serve_injected(self):
        try:
            with open(DC, "rb") as f:
                html = f.read().decode("utf-8")
        except OSError:
            self.send_error(404)
            return
        cfg = config_script()
        html = html.replace("</head>", cfg + "</head>", 1) if "</head>" in html else cfg + html
        html = html.replace("</body>", ADP + "</body>", 1) if "</body>" in html else html + ADP
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # 常に最新のアダプタを配信
        self.end_headers()
        self.wfile.write(body)

    def _proxy_to_backend(self):
        base = _safe_backend_base()
        if not base:
            self.send_error(502, "backend proxy is not configured")
            return
        parsed = urlparse(self.path)
        target = base + parsed.path + (("?" + parsed.query) if parsed.query else "")
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else None
        headers = {}
        hop_by_hop = {
            "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
            "te", "trailers", "transfer-encoding", "upgrade", "host",
            "content-length", "accept-encoding",
        }
        for key, value in self.headers.items():
            if key.lower() not in hop_by_hop:
                headers[key] = value
        headers["Accept-Encoding"] = "identity"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = "http"
        req = urllib.request.Request(target, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                self._send_proxy_response(res.status, res.headers, res.read())
        except urllib.error.HTTPError as exc:
            self._send_proxy_response(exc.code, exc.headers, exc.read())
        except Exception:
            self.send_error(502, "backend proxy request failed")

    def _send_proxy_response(self, status, headers, body):
        self.send_response(status)
        passthrough = {
            "content-type", "content-disposition", "cache-control",
            "www-authenticate", "location",
        }
        has_cache_control = False
        for key, value in headers.items():
            if key.lower() in passthrough:
                self.send_header(key, value)
                if key.lower() == "cache-control":
                    has_cache_control = True
        if not has_cache_control:
            # バックエンドが Cache-Control を返さない場合でも、認証情報を含む
            # API 応答がブラウザ/中間キャッシュに保存されないよう既定で no-store を強制する。
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def end_headers(self):
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Security-Policy-Report-Only", CSP_REPORT_ONLY)
        # data-adapter.js 等のスクリプトはデプロイのたびに更新されるため、ブラウザに
        # 古い版をキャッシュさせない（no-store）。これが無いと更新後もブラウザが旧UIを
        # 表示し続ける（メニュー変更が「反映されない」ように見える原因になる）。
        if self.path.endswith(".js"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if _is_proxy_path(path):
            self._proxy_to_backend()
            return
        if path == "/favicon.ico":  # 404ノイズ抑制（空アイコン）
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/", "/index.html") or path == "/" + DC:
            self._serve_injected()
            return
        super().do_GET()

    def do_HEAD(self):
        path = unquote(urlparse(self.path).path)
        if _is_proxy_path(path):
            self._proxy_to_backend()
            return
        super().do_HEAD()

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if _is_proxy_path(path):
            self._proxy_to_backend()
            return
        self.send_error(404)

    def do_PUT(self):
        path = unquote(urlparse(self.path).path)
        if _is_proxy_path(path):
            self._proxy_to_backend()
            return
        self.send_error(404)

    def do_PATCH(self):
        path = unquote(urlparse(self.path).path)
        if _is_proxy_path(path):
            self._proxy_to_backend()
            return
        self.send_error(404)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        if _is_proxy_path(path):
            self._proxy_to_backend()
            return
        self.send_error(404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    ip = lan_ip()
    _port = int(os.environ["PORT"]) if os.environ.get("PORT") else 0  # 0=空きポート自動
    host = os.environ.get("HOST", "0.0.0.0")
    httpd = Server((host, _port), Handler)
    port = httpd.server_address[1]
    print(f"IP={ip}")
    print(f"PORT={port}")
    print(f"BIND={host}")
    print(f"URL=http://{ip}:{port}/")
    print(f"LOOPBACK=http://127.0.0.1:{port}/", flush=True)
    httpd.serve_forever()
