"""河川観測コレクタ（デモ・シミュレーション版）。

水防災オープンデータ提供サービス（有償・契約）の接続前段階として、決定的な
シミュレーション値（水位・雨量）を自動生成して river_observations へ書き込む。
source_id は DEMO-RIVER、画面上も「デモ自動取得」と明示し、公式データと誤認
させない。観測所マスタは seed とスケジューラ双方から冪等に整備する。
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ...core.config import settings
from ...models import (
    DataSourceStatus, IdCounter, ObservationStation, RiverObservation, Site,
    SiteStation,
)

SOURCE_ID = "DEMO-RIVER"
DATA_SOURCE_ID = "DS-RIVER-DEMO"

JST = timezone(timedelta(hours=9))

# デモ観測所: (site_id, rel, station_code, name, basin, lat, lon)
DEMO_STATIONS = [
    ("S01", "upstream", "DEMO-S01-UP", "北川 上流（デモ）水位・雨量観測所",
     "北川", 35.787, 139.769),
    ("S01", "nearest", "DEMO-S01-NEAR", "北川 護岸地点（デモ）水位観測所",
     "北川", 35.766, 139.776),
    ("S05", "upstream", "DEMO-S05-UP", "西沢川 上流（デモ）水位・雨量観測所",
     "西沢川", 35.724, 139.511),
    ("S05", "nearest", "DEMO-S05-NEAR", "西沢川 樋門地点（デモ）水位観測所",
     "西沢川", 35.713, 139.517),
    ("S07", "upstream", "DEMO-S07-UP", "石狩川 北部 上流（デモ）水位・雨量観測所",
     "石狩川", 43.140, 141.310),
    ("S07", "nearest", "DEMO-S07-NEAR", "石狩川 北部（デモ）水位観測所",
     "石狩川", 43.115, 141.322),
    ("S09", "upstream", "DEMO-S09-UP", "信濃川 上流（デモ）水位・雨量観測所",
     "信濃川", 37.950, 139.020),
    ("S09", "nearest", "DEMO-S09-NEAR", "信濃川 護岸地点（デモ）水位観測所",
     "信濃川", 37.933, 139.042),
    ("S13", "upstream", "DEMO-S13-UP", "太田川 上流（デモ）水位・雨量観測所",
     "太田川", 34.430, 132.450),
    ("S13", "nearest", "DEMO-S13-NEAR", "太田川 河川内（デモ）水位観測所",
     "太田川", 34.412, 132.462),
]

# 観測所ごとの基準水位（m）。現場固有の想定河川規模で決定的に変動させる。
_BASE_LEVEL = {
    "S01": 1.80, "S05": 1.30, "S07": 2.40, "S09": 2.10, "S13": 1.60,
}


def _now() -> datetime:
    return datetime.now(JST)


def _next_id(db, prefix: str, width: int) -> str:
    """IdCounter を利用した連番ID採番（routes._allocate_id と同方式の軽量版）。"""
    row = db.get(IdCounter, prefix)
    if row is None:
        row = IdCounter(name=prefix, value=0)
        db.add(row)
        db.flush()
    row.value += 1
    return f"{prefix}{row.value:0{width}d}"


def ensure_demo_stations(db) -> dict:
    """デモ観測所マスタと現場紐付けを冪等に整備し、(created, linked) を返す。"""
    created = 0
    linked = 0
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    # 同一セッションで先に add された Site 等を可視化（SessionLocal は autoflush=False）
    db.flush()
    for i, (site_id, rel, code, name, basin, lat, lon) in enumerate(DEMO_STATIONS, 1):
        st = db.scalar(select(ObservationStation).where(
            ObservationStation.source_id == SOURCE_ID,
            ObservationStation.station_code == code))
        if st is None:
            st = ObservationStation(
                id=f"OSDEMO{i:02d}", source_id=SOURCE_ID, station_code=code,
                name=name, agency="シミュレーション（デモ）", basin_name=basin,
                kind="water_rain", latitude=lat, longitude=lon,
                status="active", created_at=now, updated_at=now)
            db.add(st)
            created += 1
        link = db.scalar(select(SiteStation).where(
            SiteStation.site_id == site_id,
            SiteStation.station_id == st.id))
        if link is None and db.get(Site, site_id) is not None:
            db.add(SiteStation(
                id=f"SSDEMO{i:02d}", site_id=site_id, station_id=st.id,
                rel=rel, sort_order=100 + i, created_at=now))
            linked += 1
    if created or linked:
        # 同一セッション内で続けて呼ばれても未コミット重複を起こさないよう flush
        db.flush()
    return {"created": created, "linked": linked}


def _demo_values(site_id: str, station_id: str, dt: datetime) -> tuple[float, float]:
    """時刻と観測所から決定的な水位・雨量を生成する。"""
    base = _BASE_LEVEL.get(site_id, 1.5)
    idx = int(station_id.replace("OSDEMO", "") or 0)
    hour = dt.hour
    day = dt.toordinal()
    seed = int(hashlib.sha256(f"{site_id}|{station_id}|{day}".encode()).hexdigest()[:8], 16)
    # 日周期＋ゆっくりした週間変動。デモだが安全側に「上がりやすい」傾向も含める
    level = base + 0.45 * math.sin((hour + idx) / 24.0 * 2 * math.pi) \
        + 0.18 * math.sin(day / 5.0 + idx)
    # 上位7%程度は小雨、まれに中程度の雨（デモ用）
    if seed % 14 == 0:
        rain = round(0.5 + (seed % 45) / 10.0, 1)
    elif seed % 7 == 0:
        rain = round(0.2 + (seed % 15) / 20.0, 1)
    else:
        rain = 0.0
    return round(max(0.05, level), 2), rain


def collect_demo_observations(db, slot: datetime | None = None) -> dict:
    """デモ観測所へ10分粒度の実測値を upsert する。"""
    if not settings.river_demo_enabled:
        return {"written": 0, "stations": 0}
    now = _now()
    if slot is None:
        slot = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    written = 0
    stations = db.scalars(select(ObservationStation).where(
        ObservationStation.source_id == SOURCE_ID,
        ObservationStation.status == "active")).all()
    for st in stations:
        site_id = db.scalar(select(SiteStation.site_id).where(
            SiteStation.station_id == st.id))
        if not site_id or db.get(Site, site_id) is None:
            continue
        level, rain = _demo_values(site_id, st.id, slot)
        observed_at = slot.strftime("%Y-%m-%dT%H:%M:%S+09:00")
        row = db.scalar(select(RiverObservation).where(
            RiverObservation.station_id == st.id,
            RiverObservation.observed_at == observed_at))
        if row is None:
            row = RiverObservation(
                id=_next_id(db, "RO", 5),
                station_id=st.id, observed_at=observed_at,
                water_level_m=level, rainfall_mm_h=rain, quality="OK",
                source=SOURCE_ID, recorded_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                recorded_by="system", note="デモ自動取得（シミュレーション）")
            db.add(row)
        else:
            row.water_level_m = level
            row.rainfall_mm_h = rain
            row.quality = "OK"
            row.source = SOURCE_ID
            row.recorded_at = now.strftime("%Y-%m-%d %H:%M:%S")
            row.recorded_by = "system"
            row.note = "デモ自動取得（シミュレーション）"
        written += 1
    # 14日より古いデモ実測は掃除（テーブル肥大の抑制。判定に使う直近値は残す）
    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    for st in stations:
        old = db.scalars(select(RiverObservation).where(
            RiverObservation.station_id == st.id,
            RiverObservation.source == SOURCE_ID,
            RiverObservation.observed_at < cutoff)).all()
        for row in old:
            db.delete(row)
    return {"written": written, "stations": len(stations)}


def refresh_demo_source_status(db) -> dict:
    """DS-RIVER-DEMO の状態を OK に更新する（デモ収集が稼働している事実の明示）。"""
    now = _now().strftime("%m/%d %H:%M")
    row = db.get(DataSourceStatus, DATA_SOURCE_ID)
    if row is None:
        row = DataSourceStatus(
            id=DATA_SOURCE_ID, name="河川観測 デモ自動取得", kind="準公式",
            status="OK", last_ok=now, fails=0, avg_ms=10, trust="補助",
            note="デモ・シミュレーションによる自動取得（水防災オープンデータ提供サービスの"
                 "接続前に暫定運用）。退避判断は必ず公式サイトと現地確認を優先。")
        db.add(row)
    else:
        row.status = "OK"
        row.last_ok = now
        row.fails = 0
        row.avg_ms = 10
        row.kind = "準公式"
        row.trust = "補助"
        row.note = ("デモ・シミュレーションによる自動取得（水防災オープンデータ提供サービス"
                    "の接続前に暫定運用）。退避判断は必ず公式サイトと現地確認を優先。")
    db.flush()  # SessionLocal は autoflush=False のため、呼び出し直後の読取でも可視化する
    return {"id": DATA_SOURCE_ID, "status": "OK"}
