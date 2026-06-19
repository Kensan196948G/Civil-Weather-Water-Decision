#!/usr/bin/env python3
"""WebUI 開発サーバ（自動IP＋空きポート, サーバ側アダプタ注入）。

`.dc.html` をディスク上は無改修のまま、HTTP 応答時に
  ・<head> に API ベース設定（?api= / localStorage）
  ・</body> 直前に data-adapter.js
を注入して返す。document.write を使わないため、Leaflet 等の外部 script は
通常の parser script として読み込まれ、Chrome の parser-blocking 警告が出ない。

  python3 frontend/serve.py
  → 表示された URL を ?api=http://<host>:<backend-port> 付きで開く
"""
import http.server
import os
import socket
import socketserver
from urllib.parse import unquote, urlparse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design")
os.chdir(ROOT)
DC = "気象河川施工判断支援.dc.html"

CFG = ("<script>(function(){var p=new URLSearchParams(location.search);"
       "var a=p.get('api');try{if(a!==null)localStorage.setItem('cw_api',a);"
       "else a=localStorage.getItem('cw_api');}catch(e){}"
       "window.__CW_API_BASE__=a||'';})();</script>")
ADP = '<script src="./data-adapter.js"></script>'


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
        html = html.replace("</head>", CFG + "</head>", 1) if "</head>" in html else CFG + html
        html = html.replace("</body>", ADP + "</body>", 1) if "</body>" in html else html + ADP
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # 常に最新のアダプタを配信
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html") or path == "/" + DC:
            self._serve_injected()
            return
        super().do_GET()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    ip = lan_ip()
    httpd = Server(("0.0.0.0", 0), Handler)
    port = httpd.server_address[1]
    print(f"IP={ip}")
    print(f"PORT={port}")
    print(f"URL=http://{ip}:{port}/")
    print(f"LOOPBACK=http://127.0.0.1:{port}/", flush=True)
    httpd.serve_forever()
