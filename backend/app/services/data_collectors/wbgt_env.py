"""環境省 熱中症予防情報サイト WBGT（暑さ指数）コレクタ（詳細設計 §4 / #9）。

予報CSV（prev15WG/dl/yohou_{地点コード}.csv）を取得・正規化する。

- 3時間刻み・値は WBGT×10（例 " 280" → 28.0℃）。サービス提供は夏期（概ね4月下旬〜10月）。
- 予測対象時刻は YYYYMMDDHH（JST）で HH=01..24。HH=24 は翌日00時として扱う。
- 取得失敗は隠さず status=ERROR で返す（画面を落とさない方針、open_meteo と同じ）。

地点コードは設定 WBGT_STATION_CODE（例: 44132=東京）。現場ごとの最寄り地点の
自動選定は観測所マスタ正規化（#29）のスコープで、本モジュールは単一地点の取得に徹する。
実CSVサンプル: samples/wbgt-env-yohou-sample.csv
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from ...core.config import settings

SOURCE_ID = "DS-WBGT"


def _parse_time(raw: str) -> str | None:
    """YYYYMMDDHH（HH=01..24）→ ISO 'YYYY-MM-DDTHH:00'。HH=24 は翌日00時に繰り上げ。"""
    raw = raw.strip()
    if len(raw) != 10 or not raw.isdigit():
        return None
    try:
        base = datetime(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None
    dt = base + timedelta(hours=int(raw[8:10]))  # HH=24 も自然に翌日へ繰り上がる
    return dt.strftime("%Y-%m-%dT%H:00")


def parse_forecast_csv(text: str) -> dict:
    """予報CSV（2行: 予測時刻列 / 地点・更新時刻・値列）を正規化する。"""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("unexpected WBGT forecast CSV shape (need 2 lines)")
    head, body = lines[0].split(","), lines[1].split(",")
    station, updated = body[0].strip(), body[1].strip()
    points = []
    for i, cell in enumerate(head[2:]):
        t = _parse_time(cell)
        if t is None:
            continue
        raw = body[2 + i].strip() if len(body) > 2 + i else ""
        wbgt = round(int(raw) / 10.0, 1) if raw.lstrip("-").isdigit() else None
        points.append({"time": t, "wbgt": wbgt,
                       "quality_flag": "MISSING" if wbgt is None else "OK"})
    return {"source_id": SOURCE_ID, "station": station, "updated": updated, "points": points}


async def fetch_forecast(station_code: str | None = None,
                         *, client: httpx.AsyncClient | None = None) -> dict:
    """予報CSVを取得して正規化。失敗時は status=ERROR で返す（隠さない）。"""
    station = (station_code or settings.wbgt_station_code or "").strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not station:
        return {"source_id": SOURCE_ID, "points": [], "fetched_at": fetched_at,
                "status": "ERROR", "error": "wbgt_station_code not configured"}
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    try:
        resp = await client.get(f"{settings.wbgt_base_url}/prev15WG/dl/yohou_{station}.csv")
        resp.raise_for_status()
        data = parse_forecast_csv(resp.text)
        data.update(fetched_at=fetched_at, status="OK", error=None)
        return data
    except Exception as e:  # noqa: BLE001 - 取得失敗は隠さず status に反映
        return {"source_id": SOURCE_ID, "points": [], "fetched_at": fetched_at,
                "status": "ERROR", "error": str(e)}
    finally:
        if own:
            await client.aclose()


def window_max(points: list[dict], start: str | None = None, end: str | None = None) -> float | None:
    """作業時間帯に該当する予報点の最大WBGT（公式値）。該当点なし/欠測のみなら None。

    指定が無い場合は先頭8点（3時間刻み≒24時間）を対象。時間帯指定で該当点が無い場合は
    None を返し、呼び出し側が推定値へフォールバックする（公式値を時間帯外へ引き延ばさない）。
    """
    if start and end:
        sel = [p for p in points if start <= p["time"] <= end]
    else:
        sel = points[:8]
    vals = [p["wbgt"] for p in sel if p.get("wbgt") is not None]
    return round(max(vals), 1) if vals else None
