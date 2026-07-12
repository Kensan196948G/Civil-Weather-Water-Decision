"""API ルータ（詳細設計 §7）。WebUI 接続用エンドポイント。"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import get_current_user, require_role
from ..models import (
    AuditLog, DataSourceStatus, DecisionLog, DecisionReason, DecisionResult,
    Site, User, WorkPlan, WorkType,
)
from ..services import assessment, notifications
from ..services.audit import audit
from ..services.data_collectors import open_meteo, source_probe

# ルータ全体に認証を必須化（/auth と /health は別ルータ/main で公開）
router = APIRouter(dependencies=[Depends(get_current_user)])

WORK_KEYS = {"river", "concrete", "earthwork", "pavement", "crane", "heat"}
RIVER_STATES = {"none", "stable", "rising", "stale"}


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


# ---------- 作業種別マスタ ----------
@router.get("/work-types")
def list_work_types(db: Session = Depends(get_db)):
    rows = db.scalars(select(WorkType).order_by(WorkType.id)).all()
    return [{"id": w.id, "name": w.name, "color": w.color} for w in rows]


# ---------- 現場 書き込み（登録/更新/無効化） ----------
class SiteCreate(BaseModel):
    name: str
    site_code: str | None = None
    loc: str = ""
    latitude: float
    longitude: float
    work_type: str
    project_type: str = "公共"
    river_work_flag: bool = False
    river_state: str = "none"
    river_note: str = "近接なし"
    flood_info: bool = False
    manager: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not v or not v.strip():
            raise ValueError("現場名は必須です")
        return v.strip()

    @field_validator("work_type")
    @classmethod
    def _work(cls, v):
        if v not in WORK_KEYS:
            raise ValueError(f"work_type は {sorted(WORK_KEYS)} のいずれか")
        return v

    @field_validator("river_state")
    @classmethod
    def _rstate(cls, v):
        if v not in RIVER_STATES:
            raise ValueError(f"river_state は {sorted(RIVER_STATES)} のいずれか")
        return v

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v):
        if not -90 <= v <= 90:
            raise ValueError("緯度は -90〜90")
        return v

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v):
        if not -180 <= v <= 180:
            raise ValueError("経度は -180〜180")
        return v

    @model_validator(mode="after")
    def _no_html(self):
        # 名称等の HTML 危険文字を入力境界で拒否（XSS 多層防御。地図ポップアップ等を保護）
        for f in ("name", "loc", "manager", "site_code"):
            v = getattr(self, f, None)
            if isinstance(v, str) and ("<" in v or ">" in v):
                raise ValueError("名称・所在地等に < > は使用できません")
        return self


class SiteUpdate(BaseModel):
    name: str | None = None
    loc: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    work_type: str | None = None
    project_type: str | None = None
    river_work_flag: bool | None = None
    river_state: str | None = None
    river_note: str | None = None
    flood_info: bool | None = None
    manager: str | None = None
    status: str | None = None


def _next_site_id(db: Session) -> str:
    ids = db.scalars(select(Site.id)).all()
    nums = [int(x[1:]) for x in ids if x[1:].isdigit()]
    return f"S{(max(nums) + 1) if nums else 1:02d}"


@router.post("/sites", status_code=201)
def create_site(req: SiteCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin", "tech_manager"))):
    sid = _next_site_id(db)
    site = Site(
        id=sid, site_code=req.site_code or f"CW-{sid}", name=req.name, loc=req.loc,
        latitude=req.latitude, longitude=req.longitude, work_type=req.work_type,
        project_type=req.project_type, river_work_flag=req.river_work_flag,
        river_state=req.river_state, river_note=req.river_note, flood_info=req.flood_info,
        manager=req.manager, status="active",
    )
    db.add(site)
    db.commit()
    audit(db, user, "site_create", f"{sid} {site.name}", site_id=sid)
    return {"id": sid, "code": site.site_code, "name": site.name, "status": "created"}


@router.put("/sites/{site_id}")
def update_site(site_id: str, req: SiteUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    data = req.model_dump(exclude_none=True)
    if "work_type" in data and data["work_type"] not in WORK_KEYS:
        raise HTTPException(422, "invalid work_type")
    if "river_state" in data and data["river_state"] not in RIVER_STATES:
        raise HTTPException(422, "invalid river_state")
    for k, v in data.items():
        setattr(site, k, v)
    db.commit()
    audit(db, user, "site_update", f"{site_id} {','.join(data.keys())}", site_id=site_id)
    return {"id": site.id, "status": "updated"}


@router.delete("/sites/{site_id}")
def deactivate_site(site_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site.status = "inactive"
    db.commit()
    audit(db, user, "site_deactivate", f"{site_id} {site.name}", site_id=site_id)
    return {"id": site.id, "status": "inactive"}


# ---------- ダッシュボード ----------
@router.get("/dashboard/site-risk")
async def dashboard_site_risk(db: Session = Depends(get_db)):
    sites = db.scalars(
        select(Site).where(Site.status == "active").order_by(Site.id)).all()
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


def _next_result_id(db: Session) -> str:
    last = db.scalar(select(DecisionResult).order_by(DecisionResult.id.desc()))
    n = (int(last.id[2:]) + 1) if last else 1
    return f"DR{n:05d}"


def _persist_decision_result(db: Session, site_id: str, work_type: str, res: dict) -> str:
    """判定結果と理由を永続化し、結果IDを返す（監査・実績分析の正本。設計§6.2.11/§6.2.12）。"""
    rid = _next_result_id(db)
    db.add(DecisionResult(
        id=rid, site_id=site_id, work_type=work_type,
        evaluated_at=datetime.now(assessment.JST).strftime("%m/%d %H:%M"),
        overall_level=res["overall_level"], overall_label=res["overall_label"],
        summary=res["summary"], data_quality_summary=res["data_quality_summary"],
        weather_status=res.get("weatherStatus", "")))
    for i, r in enumerate(res.get("reasonsRaw", []), 1):
        db.add(DecisionReason(
            id=f"{rid}-{i:02d}", decision_result_id=rid, severity=r["severity"],
            reason_code=r["reason_code"], message=r["message"],
            source_id=r["source_id"], observed_value=r["observed_value"]))
    db.commit()
    return rid


@router.post("/decisions/evaluate")
async def evaluate_decision(req: EvaluateReq, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    res = await assessment.assess_decision(site, req.work_type, req.start, req.end)
    rid = _persist_decision_result(db, site.id, req.work_type, res)
    audit(db, user, "evaluate", f"{rid} {req.work_type} L{res['overall_level']}", site_id=site.id)
    res["resultId"] = rid
    return res


@router.get("/decision-results/{result_id}")
def get_decision_result(result_id: str, db: Session = Depends(get_db)):
    dr = db.get(DecisionResult, result_id)
    if not dr:
        raise HTTPException(404, "decision result not found")
    return {
        "id": dr.id, "siteId": dr.site_id, "workType": dr.work_type,
        "evaluatedAt": dr.evaluated_at, "overall_level": dr.overall_level,
        "overall_label": dr.overall_label, "summary": dr.summary,
        "data_quality_summary": dr.data_quality_summary, "weatherStatus": dr.weather_status,
        "reasons": [{"severity": r.severity, "reason_code": r.reason_code,
                     "message": r.message, "source_id": r.source_id,
                     "observed_value": r.observed_value} for r in dr.reasons],
    }


# ---------- 作業予定（#16 T1-05・詳細設計§7 /api/work-plans） ----------
WORK_PLAN_STATUSES = {"planned", "done", "postponed", "cancelled"}


def _work_plan_dict(p: WorkPlan) -> dict:
    return {"id": p.id, "siteId": p.site_id, "workType": p.work_type, "title": p.title,
            "plannedStart": p.planned_start, "plannedEnd": p.planned_end,
            "contractor": p.contractor, "summary": p.summary, "status": p.status}


class WorkPlanCreate(BaseModel):
    site_id: str
    work_type: str
    title: str = ""
    planned_start: str
    planned_end: str
    contractor: str = ""
    summary: str = ""
    status: str = "planned"

    @field_validator("work_type")
    @classmethod
    def _work(cls, v):
        if v not in WORK_KEYS:
            raise ValueError(f"work_type は {sorted(WORK_KEYS)} のいずれか")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in WORK_PLAN_STATUSES:
            raise ValueError(f"status は {sorted(WORK_PLAN_STATUSES)} のいずれか")
        return v

    @field_validator("planned_start", "planned_end")
    @classmethod
    def _iso(cls, v):
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("日時は ISO 8601（例: 2026-06-20T08:00）で指定してください") from None
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.planned_end <= self.planned_start:
            raise ValueError("planned_end は planned_start より後である必要があります")
        return self

    @model_validator(mode="after")
    def _no_html(self):
        # XSS多層防御（設計方針に準拠。SiteCreate と同様の入力境界チェック）
        for f in ("title", "contractor", "summary"):
            v = getattr(self, f, None)
            if isinstance(v, str) and ("<" in v or ">" in v):
                raise ValueError("タイトル・協力会社・概要に < > は使用できません")
        return self


class WorkPlanUpdate(BaseModel):
    work_type: str | None = None
    title: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    contractor: str | None = None
    summary: str | None = None
    status: str | None = None


def _next_plan_id(db: Session) -> str:
    ids = db.scalars(select(WorkPlan.id)).all()
    nums = [int(x[2:]) for x in ids if x[2:].isdigit()]
    return f"WP{(max(nums) + 1) if nums else 1:02d}"


@router.get("/work-plans")
def list_work_plans(site_id: str | None = None, date: str | None = None,
                    db: Session = Depends(get_db)):
    q = select(WorkPlan).order_by(WorkPlan.id)
    if site_id:
        q = q.where(WorkPlan.site_id == site_id)
    if date:
        # 日別表示（FR-014）: planned_start の日付部分（YYYY-MM-DD）で絞り込み
        q = q.where(WorkPlan.planned_start.like(f"{date}%"))
    rows = db.scalars(q).all()
    return [_work_plan_dict(p) for p in rows]


@router.get("/work-plans/{plan_id}")
def get_work_plan(plan_id: str, db: Session = Depends(get_db)):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    return _work_plan_dict(plan)


@router.post("/work-plans", status_code=201)
def create_work_plan(req: WorkPlanCreate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager", "site_manager"))):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    pid = _next_plan_id(db)
    plan = WorkPlan(id=pid, site_id=req.site_id, work_type=req.work_type, title=req.title,
                    planned_start=req.planned_start, planned_end=req.planned_end,
                    contractor=req.contractor, summary=req.summary, status=req.status)
    db.add(plan)
    db.commit()
    audit(db, user, "work_plan_create", f"{pid} {plan.title or plan.work_type}", site_id=site.id)
    return {"id": pid, "status": "created"}


@router.put("/work-plans/{plan_id}")
def update_work_plan(plan_id: str, req: WorkPlanUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager", "site_manager"))):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    data = req.model_dump(exclude_none=True)
    if "work_type" in data and data["work_type"] not in WORK_KEYS:
        raise HTTPException(422, "invalid work_type")
    if "status" in data and data["status"] not in WORK_PLAN_STATUSES:
        raise HTTPException(422, "invalid status")
    for f in ("title", "contractor", "summary"):
        if f in data and isinstance(data[f], str) and ("<" in data[f] or ">" in data[f]):
            raise HTTPException(422, f"{f} に < > は使用できません")
    start = data.get("planned_start", plan.planned_start)
    end = data.get("planned_end", plan.planned_end)
    if ("planned_start" in data or "planned_end" in data) and end <= start:
        raise HTTPException(422, "planned_end は planned_start より後である必要があります")
    for k, v in data.items():
        setattr(plan, k, v)
    db.commit()
    audit(db, user, "work_plan_update", f"{plan_id} {','.join(data.keys())}", site_id=plan.site_id)
    return {"id": plan.id, "status": "updated"}


@router.post("/work-plans/{plan_id}/evaluate")
async def evaluate_work_plan(plan_id: str, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    site = db.get(Site, plan.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    res = await assessment.assess_decision(site, plan.work_type, plan.planned_start, plan.planned_end)
    rid = _persist_decision_result(db, site.id, plan.work_type, res)
    audit(db, user, "evaluate", f"{rid} {plan_id} L{res['overall_level']}", site_id=site.id)
    res["resultId"] = rid
    res["workPlanId"] = plan_id
    return res


# ---------- 判断履歴 ----------
class DecisionLogReq(BaseModel):
    site_id: str
    work_type: str
    level: int = 1
    action: str
    comment: str = ""
    decision_result_id: str | None = None
    # decided_by はクライアントから受け取らず、認証ユーザーから導出（なりすまし防止 #8）


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
def create_decision_log(req: DecisionLogReq, db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin", "tech_manager", "site_manager", "safety"))):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    n = db.scalar(select(DecisionLog).order_by(DecisionLog.id.desc()))
    next_num = (int(n.id[1:]) + 1) if n else 1
    entry = DecisionLog(
        id=f"L{next_num:02d}", site_id=site.id, site_name=site.name,
        work_type=req.work_type, level=req.level, action=req.action,
        comment=req.comment or "（メモなし）", decided_by=user.display_name,
        decision_result_id=req.decision_result_id,
        decided_at=datetime.now(assessment.JST).strftime("%m/%d %H:%M"))
    db.add(entry)
    db.commit()
    audit(db, user, "decision_log", f"{entry.id} {req.action} L{req.level}", site_id=site.id)
    return {"id": entry.id, "status": "recorded"}


@router.get("/decision-logs/export.csv")
def export_decision_logs(db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    audit(db, user, "csv_export", "decision_logs.csv")
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
async def run_collectors(db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    audit(db, user, "collectors_run", "手動再取得")
    assessment.clear_cache()
    sites = db.scalars(select(Site).where(Site.status == "active")).all()
    cards = await assessment.assess_all(list(sites))
    ok = sum(1 for c in cards if c["weatherStatus"] == "OK")
    # 全データソースを実プローブして状態を更新（Open-Meteo含む）
    probed = await source_probe.probe_all(db)
    return {"refetched": len(cards), "weatherOk": ok, "total": len(cards),
            "probed": {k: v["status"] for k, v in probed.items()}}


# ---------- 通知（設計§14） ----------
@router.get("/notifications")
async def list_notifications(db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    sites = db.scalars(select(Site).where(Site.status == "active").order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites))
    src = db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.id)).all()
    sources = [{"id": d.id, "name": d.name, "status": d.status,
                "fails": d.fails, "lastOk": d.last_ok} for d in src]
    notifs = notifications.build_notifications(cards, sources)
    return {"count": len(notifs), "notifications": notifs}


# ---------- 監査ログ（管理者・技術管理者） ----------
@router.get("/admin/audit-logs")
def list_audit_logs(limit: int = 100, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin", "tech_manager"))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": r.id, "timestamp": r.timestamp, "user": r.username, "action": r.action,
             "message": r.message, "siteId": r.site_id} for r in rows]
