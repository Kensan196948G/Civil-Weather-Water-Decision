"""NOWPHAS（国土交通省 全国港湾海洋波浪情報網）リアルタイムコレクタ。

WMCDSS からの統合（2026-08-12 ユーザー承認）: JMA 波浪ナウキャストの提供方式
変更（従来 URL 404）に伴い、公的データ NOWPHAS を海象の一次情報として利用する。

実測確認済みエンドポイント:
  - 観測局マスタ: /PROG/xml/POINT_SETUP.xml（全国約120局）
  - 波浪実況:     /mapxml/1（10分更新。yugiha/shiyuki/namimuki 等、99999=欠測）
  - 潮位実況:     /choui_mapxml/1（choui cm / tenmon / hensa）

出力は Open-Meteo Marine コレクタ（marine.py）と同一の points 形状に正規化し、
`assessment._cached_marine` が NOWPHAS を優先、未取得/遠隔時は Open-Meteo へ
フォールバックできるようにする。
"""
from __future__ import annotations

import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from ...core.config import settings

SOURCE_ID = "DS-NOWPHAS"

JST = ZoneInfo("Asia/Tokyo")

# 物理的妥当範囲（marine.py と同じ安全マージン方針）
WAVE_HEIGHT_RANGE = (0.0, 25.0)   # m
WAVE_PERIOD_RANGE = (1.0, 30.0)   # s
WAVE_DIR_RANGE = (0.0, 360.0)     # 度

_MISSING = {"99999", ""}

_COMPASS_TO_DEG = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}

# 上流への負荷を抑えるためのプロセス内キャッシュ（局マスタ 1h / 実況 5min）
_STATION_CACHE: tuple[float, list] = (0.0, [])
_SAMPLE_CACHE: tuple[float, dict] = (0.0, {})
_STATION_TTL = 3600.0
_SAMPLE_TTL = 300.0


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    lat: float
    lon: float


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    v = value.strip()
    if v in _MISSING:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _compass_to_deg(raw: str | None) -> float | None:
    v = (raw or "").strip().upper()
    return _COMPASS_TO_DEG.get(v)


def _parse_jst_stamp(raw: str) -> str | None:
    """'202608122300' (JST) → ISO8601（UTC 表記）。"""
    raw = (raw or "").strip()
    if len(raw) < 12:
        return None
    try:
        dt = datetime.strptime(raw[:12], "%Y%m%d%H%M").replace(tzinfo=JST)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_station(
    stations: list[Station], lat: float, lon: float, *, max_km: float
) -> Station | None:
    best: Station | None = None
    best_d = max_km
    for st in stations:
        d = haversine_km(lat, lon, st.lat, st.lon)
        if d < best_d:
            best_d = d
            best = st
    return best


async def _get_stations(client: httpx.AsyncClient) -> list[Station]:
    global _STATION_CACHE
    now = time.monotonic()
    if _STATION_CACHE[0] and now - _STATION_CACHE[0] < _STATION_TTL:
        return _STATION_CACHE[1]
    resp = await client.get(f"{settings.nowphas_base_url}/PROG/xml/POINT_SETUP.xml", timeout=15.0)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    stations: list[Station] = []
    for p in root.iter("point"):
        code, name = p.get("code"), p.get("name")
        lat, lon = _num(p.get("lat")), _num(p.get("lon"))
        if code and name and lat is not None and lon is not None:
            stations.append(Station(code=code, name=name, lat=lat, lon=lon))
    _STATION_CACHE = (now, stations)
    return stations


def _parse_map(content: bytes) -> tuple[str | None, dict[str, dict[str, str]]]:
    root = ET.fromstring(content)
    observed_at = _parse_jst_stamp(root.get("time20min") or "")
    by_code: dict[str, dict[str, str]] = {}
    for md in root.findall("mapdata"):
        code = md.get("code")
        if code:
            by_code[code] = {ch.tag: (ch.text or "") for ch in md}
    return observed_at, by_code


def _parse_tide(content: bytes) -> tuple[str | None, dict[str, dict[str, str]]]:
    root = ET.fromstring(content)
    observed_at = _parse_jst_stamp(root.get("datatime") or "")
    by_code: dict[str, dict[str, str]] = {}
    for md in root.findall("mapdata"):
        code = md.get("code")
        if code:
            by_code[code] = {ch.tag: (ch.text or "") for ch in md}
    return observed_at, by_code


async def _get_samples(client: httpx.AsyncClient) -> dict[str, dict]:
    global _SAMPLE_CACHE
    now = time.monotonic()
    if _SAMPLE_CACHE[0] and now - _SAMPLE_CACHE[0] < _SAMPLE_TTL:
        return _SAMPLE_CACHE[1]
    map_resp = await client.get(f"{settings.nowphas_base_url}/mapxml/1", timeout=15.0)
    map_resp.raise_for_status()
    wave_at, wave_map = _parse_map(map_resp.content)
    tide_at: str | None = None
    tide_map: dict[str, dict[str, str]] = {}
    try:
        tide_resp = await client.get(f"{settings.nowphas_base_url}/choui_mapxml/1", timeout=15.0)
        if tide_resp.status_code == 200:
            tide_at, tide_map = _parse_tide(tide_resp.content)
    except httpx.HTTPError:
        # 潮位は補助情報。波浪が取れていれば失敗を致命にしない。
        pass

    samples: dict[str, dict] = {}
    for code, fields in wave_map.items():
        wave_height = _num(fields.get("yugiha"))
        wave_period = _num(fields.get("shiyuki"))
        wave_dir = _compass_to_deg(fields.get("namimuki"))
        if wave_height is None or wave_period is None:
            flag = "MISSING"
        elif (not (WAVE_HEIGHT_RANGE[0] <= wave_height <= WAVE_HEIGHT_RANGE[1])
              or not (WAVE_PERIOD_RANGE[0] <= wave_period <= WAVE_PERIOD_RANGE[1])
              or (wave_dir is not None and not (WAVE_DIR_RANGE[0] <= wave_dir <= WAVE_DIR_RANGE[1]))):
            flag = "OUTLIER"
        else:
            flag = "OK"
        samples[code] = {
            "time": wave_at,
            "wave_height_m": wave_height,
            "wave_period_s": wave_period,
            "wave_direction_deg": wave_dir,
            "swell_wave_height_m": None,
            "swell_wave_period_s": None,
            "swell_wave_direction_deg": None,
            "sea_surface_temp_c": None,
            "tide_level_m": None,
            "tide_observed_at": tide_at,
            "quality_flag": flag,
        }
    for code, fields in tide_map.items():
        if code in samples:
            choui = _num(fields.get("choui"))
            samples[code]["tide_level_m"] = (choui / 100.0) if choui is not None else None
    _SAMPLE_CACHE = (now, samples)
    return samples


def _is_fresh(sample: dict, *, max_age_minutes: float = 120.0) -> bool:
    """NOWPHAS 実況の鮮度ガード（WMCDSS の鮮度方針を移植）。

    10 分更新の実況で 2 時間以上古いサンプルは欠測扱い（判定へ混入させない）。
    """
    t = sample.get("time")
    if not t:
        return False
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    return 0 <= age <= max_age_minutes


async def fetch_nowphas(
    lat: float, lon: float, *, client: httpx.AsyncClient | None = None
) -> dict:
    """現場座標の最近傍 NOWPHAS 局の実況を取得・正規化。

    返却形状は marine.fetch_marine と互換:
      {"source_id", "points": [..], "fetched_at", "status", "error"}
    status: OK / NO_STATION / ERROR（NO_STATION は Open-Meteo フォールバック対象）
    """
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        stations = await _get_stations(client)
        station = nearest_station(
            stations, lat, lon, max_km=settings.nowphas_max_distance_km
        )
        if station is None:
            return {"source_id": SOURCE_ID, "points": [], "fetched_at": fetched_at,
                    "status": "NO_STATION", "error": "nearest NOWPHAS station too far"}
        samples = await _get_samples(client)
        sample = samples.get(station.code)
        if sample is None or sample.get("quality_flag") != "OK" or not _is_fresh(sample):
            return {"source_id": SOURCE_ID, "points": [], "fetched_at": fetched_at,
                    "status": "NO_STATION",
                    "error": f"station {station.code} has no fresh/valid sample"}
        points = [{k: v for k, v in sample.items() if k != "tide_observed_at"}]
        points[0]["station"] = {"code": station.code, "name": station.name}
        return {"source_id": SOURCE_ID, "points": points, "fetched_at": fetched_at,
                "status": "OK", "error": None}
    except Exception as e:  # noqa: BLE001 - 取得失敗は隠さず status に反映
        return {"source_id": SOURCE_ID, "points": [], "fetched_at": fetched_at,
                "status": "ERROR", "error": str(e)}
    finally:
        if own:
            await client.aclose()
