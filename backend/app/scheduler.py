"""定期バッチ（APScheduler）。詳細設計 §11 の簡易版。

- probe_sources: データソース実プローブ（status を動的更新）
- refresh_forecasts: Open-Meteo 予報キャッシュをウォーム（ダッシュボードを最新化）
- dispatch_notifications: 高severity通知を外部Webhook/ログへ送信（重複抑止あり）

テストでは settings.enable_scheduler=false により起動しない。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from .core.config import settings
from .core.db import SessionLocal
from .models import Site
from .services import assessment
from .services.data_collectors.source_probe import probe_all
from .services.notifications import build_current_notifications, dispatch

_scheduler = None
_client: httpx.AsyncClient | None = None


async def _probe_job() -> None:
    with SessionLocal() as db:
        await probe_all(db, client=_client)


async def _forecast_job() -> None:
    assessment.clear_cache()
    with SessionLocal() as db:
        sites = db.scalars(select(Site).where(Site.status == "active")).all()
        await assessment.assess_all(list(sites))


async def _notification_job() -> None:
    with SessionLocal() as db:
        notifs = await build_current_notifications(db)
        await dispatch(db, notifs, client=_client)


def start():
    """AsyncIOScheduler を起動（イベントループ実行中に呼ぶ）。"""
    global _scheduler, _client
    if _scheduler:
        return _scheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    _client = httpx.AsyncClient()
    soon = datetime.now(timezone.utc) + timedelta(seconds=3)
    sch = AsyncIOScheduler(timezone="UTC")
    sch.add_job(_probe_job, "interval", seconds=settings.probe_interval_seconds,
                next_run_time=soon, id="probe_sources", max_instances=1, coalesce=True)
    sch.add_job(_forecast_job, "interval", seconds=settings.forecast_refresh_seconds,
                id="refresh_forecasts", max_instances=1, coalesce=True)
    sch.add_job(_notification_job, "interval", seconds=settings.notification_dispatch_seconds,
                next_run_time=soon, id="dispatch_notifications", max_instances=1, coalesce=True)
    sch.start()
    _scheduler = sch
    return sch


async def stop() -> None:
    global _scheduler, _client
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    if _client:
        await _client.aclose()
        _client = None
