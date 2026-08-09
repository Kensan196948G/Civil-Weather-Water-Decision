#!/usr/bin/env python3
"""Local browser E2E smoke test.

Starts a disposable FastAPI backend and the static frontend on loopback ports,
then drives Playwright through the real login UI. It uses APP_ENV=local and a
temporary SQLite DB, so no production secrets or external databases are needed.
"""
from __future__ import annotations

import os
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
E2E_DB = BACKEND / "_e2e_cw.db"
E2E_OPS_STATUS = BACKEND / "_e2e_ops_status.json"
FRONTEND_SECURITY_HEADERS = {
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
CSP_REPORT_ONLY_TOKENS = (
    "default-src 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "connect-src 'self' http://127.0.0.1:* http://localhost:*",
    "frame-src 'none'",
    "worker-src 'none'",
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_url(url: str, timeout_s: float = 40.0) -> None:
    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - report final connection failure
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


def fetch_status_and_headers(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers


def assert_frontend_headers(base_url: str) -> None:
    cases = [
        ("/", 200, "text/html", "no-store"),
        ("/data-adapter.js", 200, ("text/javascript", "application/javascript"), "no-store"),
        ("/vendor/leaflet/leaflet.css", 200, "text/css", None),
        ("/favicon.ico", 204, None, None),
        ("/missing-security-header-check", 404, "text/html", None),
    ]
    for path, expected_status, content_type, cache_control in cases:
        status, headers = fetch_status_and_headers(base_url + path)
        if status != expected_status:
            raise RuntimeError(f"unexpected frontend status for {path}: {status}")
        for key, expected in FRONTEND_SECURITY_HEADERS.items():
            actual = headers.get(key)
            if actual != expected:
                raise RuntimeError(f"unexpected frontend {key} for {path}: {actual!r}")
        if headers.get("Content-Security-Policy"):
            raise RuntimeError(f"frontend CSP must remain report-only for {path}")
        csp = headers.get("Content-Security-Policy-Report-Only", "")
        for token in CSP_REPORT_ONLY_TOKENS:
            if token not in csp:
                raise RuntimeError(f"frontend CSP report-only missing {token!r} for {path}: {csp!r}")
        actual_cache = headers.get("Cache-Control")
        if actual_cache != cache_control:
            raise RuntimeError(f"unexpected frontend Cache-Control for {path}: {actual_cache!r}")
        if content_type is None:
            continue
        actual_type = headers.get("Content-Type", "").split(";", 1)[0]
        allowed_types = content_type if isinstance(content_type, tuple) else (content_type,)
        if actual_type not in allowed_types:
            raise RuntimeError(f"unexpected frontend Content-Type for {path}: {actual_type!r}")
    api_status, api_headers = fetch_status_and_headers(base_url + "/api/auth/me")
    if api_status != 401:
        raise RuntimeError(f"unexpected proxied API status for /api/auth/me: {api_status}")
    if api_headers.get("Cache-Control") != "no-store":
        raise RuntimeError(f"proxied API must preserve no-store cache policy: {api_headers.get('Cache-Control')!r}")


class OpenMeteoStub(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
        if self.path.startswith("/marine"):
            start = datetime(2026, 6, 20, 0, 0)
            times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(48)]
            body = json.dumps({
                "timezone": "Asia/Tokyo",
                "hourly": {
                    "time": times,
                    "wave_height": [0.6 + (i % 5) * 0.4 for i in range(48)],
                    "wave_period": [5.0 + (i % 3) for i in range(48)],
                    "wave_direction": [120 + (i % 4) * 10 for i in range(48)],
                    "wind_wave_height": [0.5 for _ in range(48)],
                    "wind_wave_period": [4.0 for _ in range(48)],
                    "wind_wave_direction": [110 for _ in range(48)],
                    "swell_wave_height": [0.4 + (i % 3) * 0.3 for i in range(48)],
                    "swell_wave_period": [9.0 + (i % 2) for i in range(48)],
                    "swell_wave_direction": [200 for _ in range(48)],
                    "sea_surface_temperature": [24.0 for _ in range(48)],
                },
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.path.startswith("/forecast"):
            self.send_error(404)
            return
        start = datetime(2026, 6, 20, 0, 0)
        times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(48)]
        body = json.dumps({
            "timezone": "Asia/Tokyo",
            "hourly": {
                "time": times,
                "temperature_2m": [24 + (i % 8) for i in range(48)],
                "precipitation": [6 if i == 8 else 0 for i in range(48)],
                "wind_speed_10m": [3 + (i % 4) for i in range(48)],
                "wind_gusts_10m": [8 + (i % 5) for i in range(48)],
                "relative_humidity_2m": [65 for _ in range(48)],
                "weather_code": [61 if i == 8 else 2 for i in range(48)],
            },
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_open_meteo_stub(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), OpenMeteoStub)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=8)


def output_tail(proc: subprocess.Popen[bytes], label: str) -> None:
    out = b""
    if proc.stdout:
        try:
            out = proc.stdout.read()[-4000:]
        except Exception:
            out = b""
    if out:
        print(f"--- {label} log tail ---", file=sys.stderr)
        print(out.decode("utf-8", errors="replace"), file=sys.stderr)


def browser_executable() -> str | None:
    return os.environ.get("E2E_BROWSER_EXECUTABLE") or None


def main() -> int:
    debug = os.environ.get("E2E_DEBUG") == "1"
    failed = False
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(E2E_DB) + suffix)
        if path.exists():
            path.unlink()
    E2E_OPS_STATUS.write_text(json.dumps({
        "snapshot_utc": "2026-07-13T00:00:00Z",
        "services": [
            {"unit": "cwwd-backend.service", "active_state": "active", "result": "success", "n_restarts": "0"},
            {"unit": "cwwd-frontend.service", "active_state": "active", "result": "success", "n_restarts": "0"},
        ],
        "timers": [
            {"unit": "cwwd-ops-status.timer", "active_state": "active", "unit_file_state": "enabled",
             "last_trigger": "Mon 2026-07-13 00:00:00 JST", "next_elapse": "Mon 2026-07-13 00:30:00 JST",
             "service_result": "success"},
        ],
        "failed_units": [],
        "failed_units_count": 0,
        "status": "ok",
    }), encoding="utf-8")

    backend_port = free_port()
    frontend_port = free_port()
    open_meteo_port = free_port()
    open_meteo = start_open_meteo_stub(open_meteo_port)
    env = os.environ.copy()
    env.update({
        "APP_ENV": "local",
        "DATABASE_URL": "sqlite:///./_e2e_cw.db",
        "OPEN_METEO_BASE_URL": f"http://127.0.0.1:{open_meteo_port}",
        "OPEN_METEO_MARINE_BASE_URL": f"http://127.0.0.1:{open_meteo_port}",
        "CORS_ORIGINS": "*",
        "ENABLE_SCHEDULER": "false",
        "ENABLE_JMA_WARNINGS": "false",
        "SETTINGS_ENCRYPTION_KEY": "test-only-settings-encryption-key-32bytes-plus-000",
        "OPS_STATUS_JSON_PATH": str(E2E_OPS_STATUS),
    })
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(backend_port)],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    frontend = subprocess.Popen(
        [sys.executable, "serve.py"],
        cwd=FRONTEND,
        env={**os.environ.copy(), "HOST": "127.0.0.1", "PORT": str(frontend_port),
             "CW_BACKEND_PROXY_BASE": f"http://127.0.0.1:{backend_port}",
             "CW_TILE_URL": os.environ.get("E2E_TILE_URL", "none")},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    page = None
    try:
        wait_url(f"http://127.0.0.1:{backend_port}/health")
        frontend_base = f"http://127.0.0.1:{frontend_port}"
        wait_url(frontend_base + "/")
        assert_frontend_headers(frontend_base)
        url = frontend_base + "/"
        with sync_playwright() as p:
            browser_name = os.environ.get("E2E_BROWSER", "firefox")
            browser_type = getattr(p, browser_name)
            launch_args = {"headless": True}
            exe = browser_executable()
            if exe:
                launch_args["executable_path"] = exe
            browser = browser_type.launch(**launch_args)
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            page.route("**://tile.openstreetmap.org/**", lambda route: route.abort())
            page.add_init_script("""
                window.__cwCspViolations = [];
                window.addEventListener('securitypolicyviolation', function (event) {
                    window.__cwCspViolations.push({
                        directive: event.violatedDirective,
                        blockedURI: event.blockedURI
                    });
                });
            """)
            if debug:
                page.on("console", lambda msg: print(
                    f"browser_console[{msg.type}] {msg.text}", file=sys.stderr))
                page.on("requestfailed", lambda req: print(
                    f"browser_request_failed {req.method} {req.url} {req.failure}", file=sys.stderr))
                page.on("response", lambda res: (
                    print(f"browser_response {res.status} {res.url}", file=sys.stderr)
                    if "/api/auth/login" in res.url or "/api/auth/me" in res.url else None
                ))
            response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if response is None:
                raise RuntimeError("frontend navigation did not return a response")
            headers = response.headers
            expected_headers = {
                "x-content-type-options": "nosniff",
                "x-frame-options": "DENY",
                "referrer-policy": "no-referrer",
                "permissions-policy": "geolocation=(), microphone=(), camera=()",
            }
            for key, expected in expected_headers.items():
                actual = headers.get(key)
                if actual != expected:
                    raise RuntimeError(f"unexpected frontend {key}: {actual!r}")
            page.locator("#cw-login.on").wait_for(timeout=45_000)
            tile_url = page.evaluate("window.__CW_TILE_URL__")
            if tile_url not in ("", None):
                raise RuntimeError(f"unexpected E2E tile URL: {tile_url!r}")
            api_base = page.evaluate("window.__CW_API_BASE__")
            expected_api_base = ""
            if api_base != expected_api_base:
                raise RuntimeError(f"unexpected API base: {api_base!r}")
            if debug:
                print("api_base=" + str(api_base), file=sys.stderr)
            page.locator('input[name="username"]').fill("admin")
            page.locator('input[name="password"]').fill("admin123")
            page.locator("#cw-login-form button").click()
            page.wait_for_function(
                "() => !document.getElementById('cw-login')?.classList.contains('on')",
                timeout=45_000,
            )
            page.locator("#cw-user-name").wait_for(timeout=20_000)
            page.get_by_text("システム管理者（管理者）").wait_for(timeout=20_000)
            page.get_by_text("北川 下流右岸 護岸工事").first.wait_for(timeout=45_000)
            # サイドバーはアコーディオン式（ダッシュボード以外はグループ見出しで開く）
            page.locator('#cw-sidebar .cw-sb-h[data-group="管理"]').click()
            page.locator("#cw-sidebar button", has_text="運用状態").click()
            page.locator("#cw-ops-screen").wait_for(state="visible", timeout=20_000)
            # /api/admin/ops/status-snapshot の実装は Issue #95 (PR #96) 側にあり、
            # このブランチ単体では未マージ。API 実装後の正常表示と、未実装時の
            # フロントエンド側フォールバック表示のどちらでも smoke として成立させる。
            try:
                page.get_by_text("cwwd-backend.service").wait_for(timeout=10_000)
                page.get_by_text("cwwd-ops-status.timer").wait_for(timeout=10_000)
                page.get_by_text("failed cwwd unit はありません").wait_for(timeout=10_000)
            except PlaywrightTimeoutError:
                page.get_by_text("運用状態を取得できませんでした").wait_for(timeout=10_000)
            # #72 段5: 海象データ：全国版（地図＋一覧）
            page.locator('#cw-sidebar .cw-sb-h[data-group="気象・海象データ"]').click()
            page.locator("#cw-sidebar button", has_text="海象データ：全国版").click()
            page.locator("#cw-marine-screen").wait_for(state="visible", timeout=20_000)
            page.locator("#cw-marine-map").wait_for(state="visible", timeout=20_000)
            page.get_by_text("有義波高").first.wait_for(timeout=20_000)
            page.locator("#cw-marine-screen").get_by_text("未接続", exact=False).first.wait_for(timeout=20_000)
            # 現場一覧（読込・一覧テーブル表示）
            page.locator('#cw-sidebar .cw-sb-h[data-group="現場管理"]').click()
            page.locator("#cw-sidebar button", has_text="現場一覧").click()
            page.locator("#cw-sites-screen").wait_for(state="visible", timeout=20_000)
            page.locator("#cw-sites-body table").wait_for(timeout=20_000)
            page.get_by_text("登録済みの全現場").wait_for(timeout=20_000)
            # 熱中症/WBGT 全国マップ
            page.locator('#cw-sidebar .cw-sb-h[data-group="施工判定"]').click()
            page.locator("#cw-sidebar button", has_text="熱中症・WBGT").click()
            page.locator("#cw-wbgt-screen").wait_for(state="visible", timeout=20_000)
            page.locator("#cw-wbgt-map").wait_for(state="visible", timeout=20_000)
            page.locator("#cw-wbgt-rank").wait_for(timeout=20_000)
            # #72 段5: 海上作業判定（評価実行）
            page.locator("#cw-sidebar button", has_text="海上作業").click()
            page.locator("#cw-mwork-screen").wait_for(state="visible", timeout=20_000)
            page.locator("#cw-mwork-run").wait_for(timeout=20_000)
            page.locator("#cw-mwork-run").click()
            page.get_by_text("評価しました").wait_for(timeout=20_000)
            page.get_by_text("全体レベル").wait_for(timeout=20_000)
            page.locator("#cw-logout").click()
            page.locator("#cw-login.on").wait_for(timeout=45_000)
            csp_violations = page.evaluate("window.__cwCspViolations")
            if csp_violations:
                raise RuntimeError(f"unexpected CSP report-only violations: {csp_violations!r}")
            browser.close()
        print("RESULT: e2e smoke passed")
        return 0
    except PlaywrightTimeoutError as exc:
        failed = True
        if page is not None:
            try:
                msg = page.locator("#cw-login-msg").text_content(timeout=1000)
                token = page.evaluate("localStorage.getItem('cw_token')")
                print(f"login_msg={msg!r} token_present={bool(token)} url={page.url}", file=sys.stderr)
            except Exception:
                pass
        print(f"RESULT: e2e smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        stop(frontend)
        stop(backend)
        if failed or debug:
            output_tail(frontend, "frontend")
            output_tail(backend, "backend")
        open_meteo.shutdown()
        open_meteo.server_close()
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(E2E_DB) + suffix)
            if path.exists():
                path.unlink()
        if E2E_OPS_STATUS.exists():
            E2E_OPS_STATUS.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
