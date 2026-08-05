"""環境省 熱中症予防情報サイト WBGT（暑さ指数）コレクタ（詳細設計 §4 / #9 / #113）。

予報CSV（prev15WG/dl/yohou_{地点コード}.csv）を取得・正規化する。

- 3時間刻み・値は WBGT×10（例 " 280" → 28.0℃）。サービス提供は夏期（概ね4月下旬〜10月）。
- 予測対象時刻は YYYYMMDDHH（JST）で HH=01..24。HH=24 は翌日00時として扱う。
- 取得失敗は隠さず status=ERROR で返す（画面を落とさない方針、open_meteo と同じ）。

#113 からは地点マスタ（wbgt_point_master-YYYYMMDD.csv）と全地点予報
（yohou_all.csv）も取り扱い、現場ごとの最寄り地点を自動解決する。
地点コードは設定 WBGT_STATION_CODE（例: 44132=東京）または現場の最寄り観測所。
実CSVサンプル: samples/wbgt-env-yohou-sample.csv
"""
from __future__ import annotations

import csv
import io
import math
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.config import settings
from ...models import ObservationStation, SiteStation

SOURCE_ID = "DS-WBGT"
WBGT_STATION_SOURCE_ID = "WBGT-ENV"
JST = timezone(timedelta(hours=9))

# 現場別最近傍地点の解決キャッシュ（TTL 10分。地点マスタ同期時にクリア）
_STATION_CACHE: dict[str, tuple[float, dict | None]] = {}
_STATION_CACHE_TTL = 600.0


def clear_wbgt_station_cache() -> None:
    _STATION_CACHE.clear()


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


def parse_point_master(text: str) -> list[dict]:
    """環境省 地点マスタCSV（#113）を正規化する。

    座標は「度（Latitude/Longitude）＋分（Latitude_3/Longitude_4）」表記のため
    10進度へ変換する。地点番号が空・非数字の行はスキップする。
    """
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [c.strip().replace("\ufeff", "") for c in rows[0]]

    def idx(name: str) -> int | None:
        return header.index(name) if name in header else None

    i_code, i_name = idx("地点番号"), idx("観測所名")
    i_region, i_lat, i_lat3 = idx("地方"), idx("Latitude"), idx("Latitude_3")
    i_lon, i_lon4 = idx("Longitude"), idx("Longitude_4")
    if i_code is None or i_name is None:
        raise ValueError("unexpected WBGT point master CSV shape (地点番号/観測所名 missing)")

    def coord(deg_cell: str | None, min_cell: str | None) -> float | None:
        if deg_cell is None or min_cell is None:
            return None
        try:
            deg = float(str(deg_cell).strip().replace("−", "-"))
            minutes = float(str(min_cell).strip().replace("−", "-"))
        except (TypeError, ValueError):
            return None
        minutes_deg = math.copysign(minutes / 60.0, deg) if deg != 0 else minutes / 60.0
        return round(deg + minutes_deg, 4)

    indexes = [i for i in (i_code, i_name, i_region, i_lat, i_lat3, i_lon, i_lon4)
               if i is not None]
    stations = []
    for row in rows[1:]:
        if len(row) <= max(indexes):
            continue
        code = row[i_code].strip()
        if not code.isdigit():
            continue
        stations.append({
            "station_code": code,
            "name": row[i_name].strip() or code,
            "agency": "環境省",
            "basin_name": row[i_region].strip() if i_region is not None else "",
            "latitude": coord(row[i_lat] if i_lat is not None else None,
                              row[i_lat3] if i_lat3 is not None else None),
            "longitude": coord(row[i_lon] if i_lon is not None else None,
                               row[i_lon4] if i_lon4 is not None else None),
        })
    return stations


def parse_forecast_all_csv(text: str) -> dict:
    """全地点予報CSV（yohou_all.csv、#113）を地点コード→予報点へ正規化する。"""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    lines = [r for r in reader if any(c.strip() for c in r)]
    if len(lines) < 2:
        raise ValueError("unexpected WBGT forecast-all CSV shape (need >= 2 lines)")
    head = lines[0]
    times = [c.strip() for c in head[2:]]
    by_station: dict[str, list[dict]] = {}
    for row in lines[1:]:
        code = row[0].strip() if row else ""
        if not code.isdigit():
            continue
        points = []
        for i, raw_time in enumerate(times):
            iso = _parse_time(raw_time)
            if iso is None:
                continue
            raw = row[2 + i].strip() if len(row) > 2 + i else ""
            wbgt = round(int(raw) / 10.0, 1) if raw.lstrip("-").isdigit() else None
            points.append({"time": iso, "wbgt": wbgt,
                           "quality_flag": "MISSING" if wbgt is None else "OK"})
        by_station[code] = points
    return {"source_id": SOURCE_ID, "points_by_station": by_station,
            "count": len(by_station)}


async def fetch_point_master(*, client: httpx.AsyncClient | None = None) -> dict:
    """環境省 地点マスタCSVを取得して正規化。失敗時は status=ERROR で返す。"""
    fetched_at = datetime.now(timezone.utc).isoformat()
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    try:
        resp = await client.get(settings.wbgt_point_master_url)
        resp.raise_for_status()
        stations = parse_point_master(resp.text)
        return {"source_id": SOURCE_ID, "status": "OK", "error": None,
                "stations": stations, "count": len(stations), "fetched_at": fetched_at}
    except Exception as e:  # noqa: BLE001 - 取得失敗は隠さず status に反映
        return {"source_id": SOURCE_ID, "status": "ERROR", "error": str(e),
                "stations": [], "count": 0, "fetched_at": fetched_at}
    finally:
        if own:
            await client.aclose()


async def fetch_forecast_all(*, client: httpx.AsyncClient | None = None) -> dict:
    """全地点予報CSV（yohou_all.csv）を取得して正規化（#113 一括取得）。"""
    fetched_at = datetime.now(timezone.utc).isoformat()
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    try:
        resp = await client.get(f"{settings.wbgt_base_url}/prev15WG/dl/yohou_all.csv")
        resp.raise_for_status()
        data = parse_forecast_all_csv(resp.text)
        data.update(fetched_at=fetched_at, status="OK", error=None)
        return data
    except Exception as e:  # noqa: BLE001
        return {"source_id": SOURCE_ID, "status": "ERROR", "error": str(e),
                "points_by_station": {}, "count": 0, "fetched_at": fetched_at}
    finally:
        if own:
            await client.aclose()


async def sync_point_master(db: Session, *, client: httpx.AsyncClient | None = None,
                            text: str | None = None,
                            data: dict | None = None) -> dict:
    """地点マスタを observation_stations（source_id=WBGT-ENV, kind=wbgt）へ同期する。

    data を渡すと再取得せず同期する（CLI/テスト用）。成功時のみ書き込み、
    失敗時はDBを変更せず ERROR を返す（欠測を安全側に倒す）。
    """
    if data is None:
        if text is None:
            data = await fetch_point_master(client=client)
        else:
            stations = parse_point_master(text)
            data = {"status": "OK", "stations": stations, "count": len(stations)}
    if data.get("status") != "OK":
        return {"status": data.get("status"), "error": data.get("error"),
                "count": 0, "upserted": 0, "updated": 0}
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    upserted = updated = 0
    for st in data["stations"]:
        row = db.scalar(select(ObservationStation).where(
            ObservationStation.source_id == WBGT_STATION_SOURCE_ID,
            ObservationStation.station_code == st["station_code"]))
        if row is None:
            db.add(ObservationStation(
                id=f"WB{st['station_code']}", source_id=WBGT_STATION_SOURCE_ID,
                station_code=st["station_code"], name=st["name"], agency=st["agency"],
                basin_name=st["basin_name"], kind="wbgt", latitude=st["latitude"],
                longitude=st["longitude"], status="active", created_at=now, updated_at=now))
            upserted += 1
        else:
            row.name = st["name"]
            row.agency = st["agency"]
            row.basin_name = st["basin_name"]
            row.latitude = st["latitude"]
            row.longitude = st["longitude"]
            row.status = "active"
            row.updated_at = now
            updated += 1
    db.commit()
    clear_wbgt_station_cache()
    return {"status": "OK", "count": data["count"], "upserted": upserted,
            "updated": updated}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resolve_site_wbgt_station(db: Session, site_id: str,
                              lat: float, lon: float) -> dict | None:
    """現場に最も近いWBGT観測所を返す（#113）。

    優先順: ①site_stationsで明示指定されたWBGT観測所（sort_order順）
    ②kind=wbgt・activeの観測所から最近傍（ハーバサイン距離）
    ③該当なし → None（呼び出し側は WBGT_STATION_CODE 設定へフォールバック）
    """
    now = time.monotonic()
    hit = _STATION_CACHE.get(site_id)
    if hit and now - hit[0] < _STATION_CACHE_TTL:
        return hit[1]

    selected = None
    explicit = db.scalars(
        select(ObservationStation)
        .join(SiteStation, SiteStation.station_id == ObservationStation.id)
        .where(SiteStation.site_id == site_id,
               ObservationStation.kind == "wbgt",
               ObservationStation.status == "active")
        .order_by(SiteStation.sort_order, SiteStation.id)
    ).all()
    if explicit:
        selected = explicit[0]
    else:
        best_dist: float | None = None
        for st in db.scalars(select(ObservationStation).where(
                ObservationStation.kind == "wbgt",
                ObservationStation.status == "active")).all():
            if st.latitude is None or st.longitude is None:
                continue
            d = _haversine_km(lat, lon, st.latitude, st.longitude)
            if best_dist is None or d < best_dist:
                best_dist, selected = d, st

    result = None
    if selected is not None:
        result = {"station_code": selected.station_code, "name": selected.name,
                  "id": selected.id, "latitude": selected.latitude,
                  "longitude": selected.longitude}
    _STATION_CACHE[site_id] = (now, result)
    return result


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
