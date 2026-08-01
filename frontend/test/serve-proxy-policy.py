#!/usr/bin/env python3
"""Contract tests for frontend/serve.py backend proxy.

The frontend proxy is intentionally narrow: it may forward only selected API
paths to a fixed loopback backend origin configured by CW_BACKEND_PROXY_BASE.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch(url: str, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def wait_url(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            status, _headers, _body = fetch(url)
            if status < 500:
                return
        except Exception as exc:  # noqa: BLE001 - report final failure
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class BackendStub(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
        if self.path == "/health":
            self._send_json({"ok": True, "path": self.path})
            return
        if self.path.startswith("/api/echo"):
            self._send_json({
                "method": "GET",
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
            })
            return
        self._send_json({"detail": "not found", "path": self.path}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler hook
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else ""
        self._send_json({
            "method": "POST",
            "path": self.path,
            "content_type": self.headers.get("Content-Type", ""),
            "body": body,
        })


def start_backend(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), BackendStub)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def start_frontend(
    port: int,
    backend_base: str | None,
    allowed_hosts: str | None = None,
) -> subprocess.Popen[bytes]:
    env = {**os.environ.copy(), "HOST": "127.0.0.1", "PORT": str(port), "CW_TILE_URL": "none"}
    if backend_base is not None:
        env["CW_BACKEND_PROXY_BASE"] = backend_base
    if allowed_hosts is not None:
        env["CW_BACKEND_PROXY_ALLOWED_HOSTS"] = allowed_hosts
    return subprocess.Popen(
        [sys.executable, "serve.py"],
        cwd=FRONTEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def assert_security_headers(headers) -> None:
    expected = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    for key, value in expected.items():
        actual = headers.get(key)
        if actual != value:
            raise RuntimeError(f"unexpected {key}: {actual!r}")


def main() -> int:
    backend_port = free_port()
    backend = start_backend(backend_port)
    frontend_port = free_port()
    frontend = start_frontend(frontend_port, f"http://127.0.0.1:{backend_port}")
    bad_frontend_port = free_port()
    bad_frontend = start_frontend(bad_frontend_port, "http://192.168.0.1:55019")
    allowed_frontend_port = free_port()
    allowed_frontend = start_frontend(
        allowed_frontend_port,
        f"http://127.0.0.1:{backend_port}",
        "127.0.0.1",
    )
    try:
      base = f"http://127.0.0.1:{frontend_port}"
      bad_base = f"http://127.0.0.1:{bad_frontend_port}"
      allowed_base = f"http://127.0.0.1:{allowed_frontend_port}"
      wait_url(base + "/")
      wait_url(bad_base + "/")
      wait_url(allowed_base + "/")

      status, headers, body = fetch(base + "/api/echo?x=1", headers={"Authorization": "Bearer test-token"})
      data = json.loads(body)
      assert status == 200, status
      assert data["method"] == "GET", data
      assert data["path"] == "/api/echo?x=1", data
      assert data["authorization"] == "Bearer test-token", data
      assert headers.get("Cache-Control") == "no-store", headers
      assert_security_headers(headers)

      payload = b'{"username":"admin","password":"redacted"}'
      status, headers, body = fetch(
          base + "/api/auth/login",
          method="POST",
          body=payload,
          headers={"Content-Type": "application/json"},
      )
      data = json.loads(body)
      assert status == 200, status
      assert data["method"] == "POST", data
      assert data["path"] == "/api/auth/login", data
      assert data["content_type"] == "application/json", data
      assert data["body"] == payload.decode("utf-8"), data
      assert_security_headers(headers)

      status, headers, body = fetch(base + "/health")
      data = json.loads(body)
      assert status == 200, status
      assert data["path"] == "/health", data
      assert_security_headers(headers)

      status, _headers, _body = fetch(bad_base + "/api/echo")
      assert status == 502, status

      status, _headers, body = fetch(allowed_base + "/api/echo")
      data = json.loads(body)
      assert status == 200, status
      assert data["path"] == "/api/echo", data

      print("RESULT: serve proxy policy passed")
      return 0
    finally:
        stop(frontend)
        stop(bad_frontend)
        stop(allowed_frontend)
        backend.shutdown()
        backend.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
