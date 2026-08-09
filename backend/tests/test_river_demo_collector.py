"""河川観測デモ自動取得（#31 拡張）のテスト。

- ensure_demo_stations: 観測所マスタ＋現場紐付けの冪等整備
- collect_demo_observations: 決定的な水位・雨量を source=DEMO-RIVER で自動投入
- refresh_demo_source_status: DS-RIVER-DEMO を OK 表示
- API: デモ観測がある現場は automatic=true / provider に「デモ自動取得」を明示
"""
from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import (
    AuditLog, DataSourceStatus, ObservationStation, RiverObservation, SiteStation,
)
from app.services.data_collectors import river_collector
from app.services.data_collectors.river_collector import JST

DEMO_STATION_IDS = [f"OSDEMO{i:02d}" for i in range(1, 11)]
DEMO_LINK_IDS = [f"SSDEMO{i:02d}" for i in range(1, 11)]


def _cleanup(baseline_audit: int) -> None:
    db = SessionLocal()
    try:
        db.execute(delete(RiverObservation).where(
            RiverObservation.source == river_collector.SOURCE_ID))
        db.execute(delete(SiteStation).where(
            SiteStation.id.in_(DEMO_LINK_IDS)))
        db.execute(delete(ObservationStation).where(
            ObservationStation.source_id == river_collector.SOURCE_ID))
        db.execute(delete(DataSourceStatus).where(
            DataSourceStatus.id == river_collector.DATA_SOURCE_ID))
        db.execute(delete(AuditLog).where(AuditLog.id > baseline_audit))
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_demo_collector_and_api_automatic(client, monkeypatch):
    monkeypatch.setattr(settings, "river_demo_enabled", True)
    baseline = SessionLocal()
    try:
        baseline_audit = baseline.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0
    finally:
        baseline.close()

    db = SessionLocal()
    try:
        fixed_slot = datetime(2026, 8, 9, 12, 30, 0, tzinfo=JST)
        first = river_collector.ensure_demo_stations(db)
        second = river_collector.ensure_demo_stations(db)
        assert first["created"] == 10
        assert second["created"] == 0
        assert first["linked"] == 10
        assert second["linked"] == 0

        result = river_collector.collect_demo_observations(db, slot=fixed_slot)
        assert result["stations"] == 10
        assert result["written"] == 10
        db.flush()  # SessionLocal は autoflush=False のため明示 flush
        rows = db.scalars(select(RiverObservation).where(
            RiverObservation.source == river_collector.SOURCE_ID)).all()
        assert len(rows) == 10
        assert all(r.water_level_m is not None and r.rainfall_mm_h is not None
                   and r.quality == "OK" and r.recorded_by == "system" for r in rows)

        status = river_collector.refresh_demo_source_status(db)
        assert status["status"] == "OK"
        src = db.get(DataSourceStatus, river_collector.DATA_SOURCE_ID)
        assert src is not None and src.status == "OK" and src.fails == 0
        db.commit()
    finally:
        db.close()

    # API はデモ自動取得を automatic=true で明示
    data = client.get("/api/sites/S01/observation-stations").json()
    assert data["automatic"] is True
    assert "デモ自動取得" in data["provider"]
    series = client.get("/api/sites/S01/river-observations?limit=10").json()
    assert series["automatic"] is True
    assert series["observations"]
    assert all(o["source"] == river_collector.SOURCE_ID for o in series["observations"])

    # 再実行は同じ時刻スロットを上書きする（行数が増えない）
    db = SessionLocal()
    try:
        before = len(db.scalars(select(RiverObservation).where(
            RiverObservation.source == river_collector.SOURCE_ID)).all())
        river_collector.collect_demo_observations(db, slot=fixed_slot)
        db.flush()
        after = len(db.scalars(select(RiverObservation).where(
            RiverObservation.source == river_collector.SOURCE_ID)).all())
        assert after == before
    finally:
        db.close()
    _cleanup(baseline_audit)
