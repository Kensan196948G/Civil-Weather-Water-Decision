"""API ルータ（詳細設計 §7）。WebUI 接続用エンドポイント。"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..models import DataSourceStatus, DecisionLog, Site, Station, WorkType
from ..services import assessment
from ..services.data_collectors import open_meteo

router = APIRouter()


# ---------- 現場 ----------
@router.get("/sites")
def list_sites(db: Session = Depends(get_db)):
    sites = db.scalars(select(Site).order_by(Site.id)).all()
    return [{"id": s.id, "code": s.site_code, "name": s.name, "loc": s.loc,
             "lat": s.latitude, "lon": s.longitude, "work": s.work_type,
             "project": s.project_type, "riverWork": s.river_work_flag,
             "manager": s.manager, "status": s.status} for s in sites]


@router.get("/sites/{site_id}")
async def get_site(site_id: str, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    card = await assessment.assess_site(site)
    plans = []
    for p in site.plans:
        d = await assessment.assess_decision(site, p.work_type, p.planned_start, p.planned_end)
        plans.append({"id": p.id, "title": p.title, "time": f"{p.planned_start}–{p.planned_end}",
                      "contractor": p.contractor, "workType": p.work_type,
                      "level": d["overall_level"], "levelLabel": d["overall_label"],
                      "reason": d["summary"]})
    history = db.scalars(
        select(DecisionLog).where(DecisionLog.site_id == site_id)
        .order_by(DecisionLog.id.desc()).limit(4)).all()
    return {**card,
            "manager": site.manager + "（現場管理者）", "project": site.project_type,
            "hasRiver": site.work_type == "river" or site.river_work_flag,
            "plans": plans,
            "history": [{"datetime": h.decided_at, "action": h.action, "level": h.level,
                         "comment": h.comment, "by": h.decided_by} for h in history]}


@router.get("/sites/{site_id}/stations")
def site_stations(site_id: str, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    return [{"id": st.id, "name": st.name, "type": st.type, "rel": st.rel,
             "lat": st.latitude, "lon": st.longitude} for st in site.stations]


# ---------- ダッシュボード ----------
@router.get("/dashboard/site-risk")
async def dashboard_site_risk(db: Session = Depends(get_db)):
    sites = db.scalars(select(Site).order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites))
    counts = [0, 0, 0, 0]
    for c in cards:
        counts[c["level"]] += 1
    return {"summary": counts, "sites": cards}


@router.get("/dashboard/data-sources")
def dashboard_data_sources(db: Session = Depends(get_db)):
    rows = db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.id)).all()
    return [{"id": d.id, "name": d.name, "kind": d.kind, "status": d.status,
             "lastOk": d.last_ok, "fails": d.fails, "ms": d.avg_ms,
             "trust": d.trust, "note": d.note} for d in rows]


# ---------- 気象 ----------
@router.get("/weather/timeseries")
async def weather_timeseries(site_id: str = Query(...), db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    data = await assessment._cached_fetch(site.latitude, site.longitude, site.id)
    return {"siteId": site.id, "source": open_meteo.SOURCE_ID, "status": data.get("status"),
            "fetchedAt": data.get("fetched_at"), "points": data.get("points", [])[:24]}


# ---------- 作業判断 ----------
class EvaluateReq(BaseModel):
    site_id: str
    work_type: str
    start: str | None = None
    end: str | None = None


@router.post("/decisions/evaluate")
async def evaluate_decision(req: EvaluateReq, db: Session = Depends(get_db)):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    return await assessment.assess_decision(site, req.work_type, req.start, req.end)


# ---------- 判断履歴 ----------
class DecisionLogReq(BaseModel):
    site_id: str
    work_type: str
    level: int = 1
    action: str
    comment: str = ""
    decided_by: str = "山田（現場管理者）"


@router.get("/decision-logs")
def list_decision_logs(action: str | None = None, db: Session = Depends(get_db)):
    q = select(DecisionLog).order_by(DecisionLog.id.desc())
    if action and action != "all":
        q = q.where(DecisionLog.action == action)
    rows = db.scalars(q).all()
    return [{"id": h.id, "datetime": h.decided_at, "siteId": h.site_id, "site": h.site_name,
             "workType": h.work_type, "level": h.level, "action": h.action,
             "comment": h.comment, "by": h.decided_by} for h in rows]


@router.post("/decision-logs")
def create_decision_log(req: DecisionLogReq, db: Session = Depends(get_db)):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    n = db.scalar(select(DecisionLog).order_by(DecisionLog.id.desc()))
    next_num = (int(n.id[1:]) + 1) if n else 1
    entry = DecisionLog(
        id=f"L{next_num:02d}", site_id=site.id, site_name=site.name,
        work_type=req.work_type, level=req.level, action=req.action,
        comment=req.comment or "（メモなし）", decided_by=req.decided_by,
        decided_at=datetime.now(assessment.JST).strftime("%m/%d %H:%M"))
    db.add(entry)
    db.commit()
    return {"id": entry.id, "status": "recorded"}


@router.get("/decision-logs/export.csv")
def export_decision_logs(db: Session = Depends(get_db)):
    rows = db.scalars(select(DecisionLog).order_by(DecisionLog.id.desc())).all()
    buf = io.StringIO()
    buf.write("﻿")  # Excel(JP) 用 BOM
    w = csv.writer(buf)
    w.writerow(["decision_log_id", "site_id", "site_name", "work_type", "level",
                "action", "comment", "decided_by", "decided_at"])
    for h in rows:
        w.writerow([h.id, h.site_id, h.site_name, h.work_type, h.level,
                    h.action, h.comment, h.decided_by, h.decided_at])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=decision_logs.csv"})


# ---------- データ取得（手動再取得） ----------
@router.post("/data-collectors/run")
async def run_collectors(db: Session = Depends(get_db)):
    assessment.clear_cache()
    sites = db.scalars(select(Site)).all()
    cards = await assessment.assess_all(list(sites))
    ok = sum(1 for c in cards if c["weatherStatus"] == "OK")
    src = db.get(DataSourceStatus, open_meteo.SOURCE_ID)
    if src:
        now = datetime.now(assessment.JST).strftime("%m/%d %H:%M")
        if ok > 0:
            src.status, src.last_ok, src.fails = "OK", now, 0
        else:
            src.status, src.fails = "Error", src.fails + 1
        db.commit()
    return {"refetched": len(cards), "weatherOk": ok, "total": len(cards)}
