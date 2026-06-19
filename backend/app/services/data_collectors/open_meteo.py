"""Open-Meteo コレクタ（詳細設計 §4 / §5）。

予報の取得・正規化・品質フラグ付与を行う。外部APIは「公式情報の補完」として扱う。
httpx クライアントは注入可能にし、テストはネットワーク非依存で実行する。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx

from ...core.config import settings

SOURCE_ID = "DS-OPEN-METEO"
HOURLY_VARS = [
    "temperature_2m", "precipitation", "wind_speed_10m",
    "wind_gusts_10m", "relative_humidity_2m", "weather_code",
]


def estimate_wbgt(temp_c: float | None, rh_pct: float | None) -> float | None:
    """WBGT 近似推定（屋外, BoM 近似）。公式 WBGT 未接続時の derived 値。

    e（水蒸気圧）= rh/100 * 6.105 * exp(17.27*T/(237.7+T))
    WBGT ≈ 0.567*T + 0.393*e + 3.94
    """
    if temp_c is None or rh_pct is None:
        return None
    e = (rh_pct / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return round(0.567 * temp_c + 0.393 * e + 3.94, 1)


def normalize(payload: dict) -> dict:
    """Open-Meteo の hourly レスポンスを正規化（単位統一・品質フラグ）。"""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []

    def col(name: str) -> list:
        c = hourly.get(name) or []
        return list(c) + [None] * (len(times) - len(c))

    temp, pr = col("temperature_2m"), col("precipitation")
    ws, gs = col("wind_speed_10m"), col("wind_gusts_10m")
    rh, wc = col("relative_humidity_2m"), col("weather_code")

    points = []
    for i, t in enumerate(times):
        # 主要フィールド（降雨・気温）の欠測で MISSING
        flag = "MISSING" if (temp[i] is None or pr[i] is None) else "OK"
        points.append({
            "time": t,
            "temp_c": temp[i],
            "precip_mm": pr[i],
            "wind_ms": ws[i],
            "gust_ms": gs[i],
            "humidity_pct": rh[i],
            "wbgt_derived": estimate_wbgt(temp[i], rh[i]),
            "weather_code": wc[i],
            "quality_flag": flag,
        })
    return {"source_id": SOURCE_ID, "timezone": payload.get("timezone"), "points": points}


async def fetch_forecast(
    lat: float, lon: float, *, client: httpx.AsyncClient | None = None, forecast_days: int = 2
) -> dict:
    """予報を取得して正規化。失敗時は status=ERROR で返す（画面を落とさない方針）。"""
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(HOURLY_VARS),
        "forecast_days": forecast_days,
        "timezone": settings.app_timezone,
        "wind_speed_unit": "ms",
    }
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        resp = await client.get(f"{settings.open_meteo_base_url}/forecast", params=params)
        resp.raise_for_status()
        data = normalize(resp.json())
        data.update(fetched_at=fetched_at, status="OK", error=None)
        return data
    except Exception as e:  # noqa: BLE001 - 取得失敗は隠さず status に反映
        return {"source_id": SOURCE_ID, "points": [], "fetched_at": fetched_at,
                "status": "ERROR", "error": str(e)}
    finally:
        if own:
            await client.aclose()


def window_reading(points: list[dict], start: str | None = None, end: str | None = None) -> dict:
    """作業時間帯（start〜end, ISO文字列）に該当する点から代表値を抽出する。

    指定が無い場合は先頭24点（≒当日）を対象。欠測フィールドは missing に積む。
    """
    sel = points
    if start and end:
        sel = [p for p in points if start <= p["time"] <= end] or points[:12]
    sel = sel[:24] if not (start and end) else sel

    def vals(key):
        return [p[key] for p in sel if p.get(key) is not None]

    temps, precs = vals("temp_c"), vals("precip_mm")
    winds, gusts = vals("wind_ms"), vals("gust_ms")
    hums, wbgts = vals("humidity_pct"), vals("wbgt_derived")

    missing = set()
    if not temps:
        missing.add("temp")
    if not precs:
        missing.add("precip")
    if not winds:
        missing.add("wind")
    if not wbgts:
        missing.add("wbgt")

    return {
        "precip_mm_h": round(max(precs), 1) if precs else None,
        "temp_c": round(max(temps), 1) if temps else None,
        "temp_lo": round(min(temps), 1) if temps else None,
        "wind_ms": round(max(winds), 1) if winds else None,
        "gust_ms": round(max(gusts), 1) if gusts else None,
        "humidity_pct": round(sum(hums) / len(hums)) if hums else None,
        "wbgt": round(max(wbgts), 1) if wbgts else None,
        "missing": missing,
    }
