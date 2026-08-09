"""Open-Meteo Marine API コレクタ（海象データ：全国版 / 海上作業判定用）。

国交省 NOWPHAS・気象庁潮位の正式データソースは利用条件・API整備状況の確認が必要なため、
現段階では Open-Meteo Marine API（無料・APIキー不要）を主データソースとし、公式情報への
リンクと未接続ソースを明示する。潮位は現時点で取得せず「補完中」と表示する（実態以上に
良く見せない方針）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ...core.config import settings

SOURCE_ID = "DS-OPEN-METEO-MARINE"

HOURLY_VARS = [
    "wave_height", "wave_period", "wave_direction",
    "wind_wave_height", "wind_wave_period", "wind_wave_direction",
    "swell_wave_height", "swell_wave_period", "swell_wave_direction",
    "sea_surface_temperature",
]

# 物理的妥当範囲（観測記録を誤検知しない安全マージン込み）
WAVE_HEIGHT_RANGE = (0.0, 25.0)   # m（記録級の大波浪でも15m前後）
WAVE_PERIOD_RANGE = (1.0, 30.0)   # s
WAVE_DIR_RANGE = (0.0, 360.0)     # 度
SST_RANGE = (-2.0, 36.0)          # ℃


def _is_outlier(wave_height: float | None, wave_period: float | None,
                wave_direction: float | None, sst: float | None) -> bool:
    """波高・周期・波向・水温のいずれかが物理的範囲外なら True。"""
    if wave_height is not None and not (WAVE_HEIGHT_RANGE[0] <= wave_height <= WAVE_HEIGHT_RANGE[1]):
        return True
    if wave_period is not None and not (WAVE_PERIOD_RANGE[0] <= wave_period <= WAVE_PERIOD_RANGE[1]):
        return True
    if wave_direction is not None and not (WAVE_DIR_RANGE[0] <= wave_direction <= WAVE_DIR_RANGE[1]):
        return True
    if sst is not None and not (SST_RANGE[0] <= sst <= SST_RANGE[1]):
        return True
    return False


def normalize(payload: dict) -> dict:
    """Open-Meteo Marine の hourly レスポンスを正規化（単位統一・品質フラグ）。"""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []

    def col(name: str) -> list:
        c = hourly.get(name) or []
        return list(c) + [None] * (len(times) - len(c))

    wh = col("wave_height")
    wp = col("wave_period")
    wd = col("wave_direction")
    wwh = col("wind_wave_height")
    wwp = col("wind_wave_period")
    wwd = col("wind_wave_direction")
    swh = col("swell_wave_height")
    swp = col("swell_wave_period")
    swd = col("swell_wave_direction")
    sst = col("sea_surface_temperature")

    points = []
    for i, t in enumerate(times):
        # 主要フィールド（波高・周期）の欠測で MISSING、次点で範囲外値を OUTLIER
        if wh[i] is None or wp[i] is None:
            flag = "MISSING"
        elif _is_outlier(wh[i], wp[i], wd[i], sst[i]):
            flag = "OUTLIER"
        else:
            flag = "OK"
        points.append({
            "time": t,
            "wave_height_m": wh[i],
            "wave_period_s": wp[i],
            "wave_direction_deg": wd[i],
            "wind_wave_height_m": wwh[i],
            "wind_wave_period_s": wwp[i],
            "wind_wave_direction_deg": wwd[i],
            "swell_wave_height_m": swh[i],
            "swell_wave_period_s": swp[i],
            "swell_wave_direction_deg": swd[i],
            "sea_surface_temp_c": sst[i],
            "quality_flag": flag,
        })
    return {"source_id": SOURCE_ID, "timezone": payload.get("timezone"), "points": points}


async def fetch_marine(
    lat: float, lon: float, *, client: httpx.AsyncClient | None = None, forecast_days: int = 2
) -> dict:
    """沿岸地点の海洋予報を取得して正規化。失敗時は status=ERROR で返す。"""
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(HOURLY_VARS),
        "forecast_days": forecast_days,
        "timezone": settings.app_timezone,
    }
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        resp = await client.get(f"{settings.open_meteo_marine_base_url}/marine", params=params)
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


def _max_round(points: list[dict], key: str, digits: int = 2) -> float | None:
    vals = [p[key] for p in points if p.get(key) is not None]
    return round(max(vals), digits) if vals else None


def window_reading(points: list[dict], start: str | None = None, end: str | None = None) -> dict:
    """作業時間帯（start〜end）の代表値（ピーク）を抽出。欠測フィールドは missing に積む。"""
    sel = points
    if start and end:
        sel = [p for p in points if start <= p["time"] <= end] or points[:12]
    sel = sel[:24] if not (start and end) else sel

    missing: set[str] = set()
    wave_heights = [p["wave_height_m"] for p in sel if p.get("wave_height_m") is not None]
    wave_periods = [p["wave_period_s"] for p in sel if p.get("wave_period_s") is not None]
    swells = [p["swell_wave_height_m"] for p in sel if p.get("swell_wave_height_m") is not None]
    if not wave_heights:
        missing.add("wave")
    if not wave_periods:
        missing.add("wave_period")
    if not swells:
        missing.add("swell")

    return {
        "wave_height_m": round(max(wave_heights), 2) if wave_heights else None,
        "wave_period_s": round(max(wave_periods), 1) if wave_periods else None,
        "wave_direction_deg": _max_round(sel, "wave_direction_deg"),
        "swell_wave_height_m": round(max(swells), 2) if swells else None,
        "swell_wave_period_s": _max_round(sel, "swell_wave_period_s", 1),
        "swell_wave_direction_deg": _max_round(sel, "swell_wave_direction_deg"),
        "sea_surface_temp_c": _max_round(sel, "sea_surface_temp_c", 1),
        "missing": missing,
    }
