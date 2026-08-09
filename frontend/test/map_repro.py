#!/usr/bin/env python3
"""地図レンダリング回帰テスト（Playwright / Firefox）。

ダッシュボード・現場詳細・作業判断（コンクリート打設）・海象データの
Leaflet マーカー/パス件数を出力し、マップ非表示の回帰を検出する。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_smoke import (  # noqa: E402
    BACKEND, E2E_DB, E2E_OPS_STATUS, FRONTEND, free_port, start_open_meteo_stub,
    stop, wait_url,
)
from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    for suffix in ("", "-wal", "-shm"):
        Path(str(E2E_DB) + suffix).unlink(missing_ok=True)
    E2E_OPS_STATUS.write_text('{"status":"ok","failed_units_count":0,"services":[],"timers":[]}')
    open_meteo_port = free_port()
    backend_port = free_port()
    frontend_port = free_port()
    open_meteo = start_open_meteo_stub(open_meteo_port)
    env = os.environ.copy()
    env.update({
        "APP_ENV": "local", "DATABASE_URL": "sqlite:///./_e2e_cw.db",
        "OPEN_METEO_BASE_URL": f"http://127.0.0.1:{open_meteo_port}",
        "OPEN_METEO_MARINE_BASE_URL": f"http://127.0.0.1:{open_meteo_port}",
        "CORS_ORIGINS": "*", "ENABLE_SCHEDULER": "false", "ENABLE_JMA_WARNINGS": "false",
        "SETTINGS_ENCRYPTION_KEY": "test-only-settings-encryption-key-32bytes-plus-000",
        "OPS_STATUS_JSON_PATH": str(E2E_OPS_STATUS),
    })
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(backend_port)], cwd=BACKEND, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    frontend = subprocess.Popen(
        [sys.executable, "serve.py"], cwd=FRONTEND,
        env={**os.environ.copy(), "HOST": "127.0.0.1", "PORT": str(frontend_port),
             "CW_BACKEND_PROXY_BASE": f"http://127.0.0.1:{backend_port}",
             "CW_TILE_URL": "none"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        wait_url(f"http://127.0.0.1:{backend_port}/health")
        base = f"http://127.0.0.1:{frontend_port}"
        wait_url(base + "/")
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            console = []
            page.on("console", lambda msg: console.append(
                f"[{msg.type}] {msg.text[:180]}"))
            page.on("pageerror", lambda exc: console.append(f"[pageerror] {str(exc)[:180]}"))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=45000)
            page.locator("#cw-login.on").wait_for(timeout=45000)
            page.locator('input[name="username"]').fill("admin")
            page.locator('input[name="password"]').fill("admin123")
            page.locator("#cw-login-form button").click()
            page.locator("#cw-user-name").wait_for(timeout=45000)
            page.get_by_text("北川 下流右岸 護岸工事").first.wait_for(timeout=45000)
            page.wait_for_timeout(2500)

            def markers(sel):
                return page.evaluate(
                    "(s) => document.querySelectorAll(s).length", sel)

            print("dash markers:", markers("#cw-map-dash .leaflet-marker-icon"),
                  "paths:", markers("#cw-map-dash .leaflet-overlay-pane path"))
            page.screenshot(path="/tmp/map_dash.png")
            # 現場詳細
            page.get_by_role("button", name="現場詳細を開く").first.click(force=True)
            page.locator("#cw-map-site").wait_for(state="visible", timeout=15000)
            page.wait_for_timeout(1500)
            print("site markers:", markers("#cw-map-site .leaflet-marker-icon"),
                  "paths:", markers("#cw-map-site .leaflet-overlay-pane path"))
            print("site stations markers:", markers("#cw-map-site .leaflet-marker-icon:nth-child(n)"))
            page.screenshot(path="/tmp/map_site.png")
            # 作業判断（コンクリート打設）
            page.locator("#cw-sidebar button", has_text="コンクリート打設").click()
            page.locator("#cw-map-dec").wait_for(state="visible", timeout=15000)
            page.wait_for_timeout(1500)
            print("dec markers:", markers("#cw-map-dec .leaflet-marker-icon"),
                  "paths:", markers("#cw-map-dec .leaflet-overlay-pane path"))
            print("dec marker html:", page.evaluate(
                "document.querySelectorAll('#cw-map-dec .leaflet-marker-icon').length"
                " + ' icons, ' + document.querySelectorAll('#cw-map-dec .leaflet-container').length + ' containers'"))
            page.screenshot(path="/tmp/map_dec.png")
            # 海象全国
            page.locator("#cw-sidebar button", has_text="海象データ：全国版").click()
            page.locator("#cw-marine-map").wait_for(state="visible", timeout=15000)
            page.wait_for_timeout(2500)
            print("marine markers:", markers("#cw-marine-map .leaflet-marker-icon"),
                  "paths:", markers("#cw-marine-map .leaflet-overlay-pane path"))
            page.screenshot(path="/tmp/map_marine.png")
            print("--- console errors ---")
            for line in console:
                if line.startswith("[error]") or line.startswith("[pageerror]"):
                    print(line)
            print("console total:", len(console))
            browser.close()
        print("MAP REPRO DONE")
        return 0
    finally:
        stop(frontend)
        stop(backend)
        open_meteo.shutdown()
        open_meteo.server_close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(E2E_DB) + suffix).unlink(missing_ok=True)
        if E2E_OPS_STATUS.exists():
            E2E_OPS_STATUS.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
