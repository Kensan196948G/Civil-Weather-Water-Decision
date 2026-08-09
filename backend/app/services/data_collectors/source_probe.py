"""データソース実プローブ（詳細設計 §8.9 / FR-081〜083）。

各データソースの公開エンドポイントへ実HTTP疎通し、status / last_ok / fails / avg_ms を
動的更新する。到達できないソース（契約制の水防災オープンデータ等）はプローブ対象外とし、
シード状態（Error/未接続）を保持する＝「実態以上に良く見せない」。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from ...core.config import settings
from ...models import DataSourceStatus

JST = timezone(timedelta(hours=9))
SLOW_MS = 1500  # これを超える応答は Warning（遅延）
PROBE_HEADERS = {
    # 一部の公式サイト（river.go.jp 等）はbot既定UAを拒否するため、ブラウザ相当UAで
    # トップページ疎通のみ行う（スクレイピングはしない。プローブはliveness確認のみ）
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 CWWD-Probe/0.3"),
}

# source_id -> liveness 確認用 公開URL（認証不要）。ここに無いソースはプローブしない。
PROBE_TARGETS = {
    "DS-OPEN-METEO": settings.open_meteo_base_url
    + "/forecast?latitude=35&longitude=139&hourly=temperature_2m&forecast_days=1",
    "DS-OPEN-METEO-MARINE": settings.open_meteo_marine_base_url
    + "/marine?latitude=35&longitude=139&hourly=wave_height&forecast_days=1",
    "DS-JMA-XML": "https://www.data.jma.go.jp/developer/xml/feed/regular.xml",
    "DS-WBGT": "https://www.wbgt.env.go.jp/",
    "DS-RIVER-GO": "https://www.river.go.jp/",
    "DS-NASA-POWER": "https://power.larc.nasa.gov/",
    "DS-JMA-CSV": "https://www.jma.go.jp/bosai/amedas/",
    "DS-JAXA": "https://gportal.jaxa.jp/gpr/",
    "DS-NOAA": "https://api.weather.gov/",
}


async def probe_one(url: str, client: httpx.AsyncClient) -> tuple[str, int, bool, str | None]:
    """1URLを疎通。(status, ms, ok, error) を返す。"""
    t0 = time.monotonic()
    try:
        resp = await client.get(url, timeout=settings.probe_timeout_seconds, follow_redirects=True)
        ms = int((time.monotonic() - t0) * 1000)
        code = resp.status_code
        if 200 <= code < 300:
            return ("OK" if ms < SLOW_MS else "Warning", ms, True, None if ms < SLOW_MS else "slow")
        if 300 <= code < 500:
            return ("Warning", ms, True, f"HTTP {code}")
        return ("Error", ms, False, f"HTTP {code}")
    except Exception as e:  # noqa: BLE001 - 失敗は隠さず Error として記録
        ms = int((time.monotonic() - t0) * 1000)
        return ("Error", ms, False, type(e).__name__)


async def probe_all(db, client: httpx.AsyncClient | None = None) -> dict:
    """PROBE_TARGETS を順に疎通し DataSourceStatus を更新。結果サマリを返す。"""
    own = client is None
    if own:
        client = httpx.AsyncClient(
            timeout=settings.probe_timeout_seconds,
            follow_redirects=True,
            headers=PROBE_HEADERS,
        )
    now = datetime.now(JST).strftime("%m/%d %H:%M")
    results = {}
    try:
        for sid, url in PROBE_TARGETS.items():
            row = db.get(DataSourceStatus, sid)
            if not row:
                continue
            status, ms, ok, err = await probe_one(url, client)
            row.status = status
            row.avg_ms = (row.avg_ms + ms) // 2 if row.avg_ms else ms
            if ok:
                row.last_ok = now
                row.fails = 0
            else:
                row.fails = (row.fails or 0) + 1
            results[sid] = {"status": status, "ms": ms, "ok": ok, "error": err}
        db.commit()
    finally:
        if own:
            await client.aclose()
    return results
