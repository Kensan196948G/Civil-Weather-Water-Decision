"""通知導出・ディスパッチのテスト（設計§14）。"""
import json

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AppSetting, NotificationDelivery
from app.services import notifications
from app.services.notifications import build_notifications


def test_build_notifications_derives_from_cards_and_sources():
    cards = [
        {"id": "S01", "name": "現場A", "level": 2, "summary": "洪水情報",
         "wbgt": 29, "riverState": "rising", "updated": "08:00"},
        {"id": "S05", "name": "現場B", "level": 3, "summary": "",
         "wbgt": 26, "riverState": "stale", "updated": "06:00"},
        {"id": "S06", "name": "現場C", "level": 0, "summary": "",
         "wbgt": 20, "riverState": "none", "updated": "08:00"},
    ]
    sources = [
        {"id": "DS-X", "name": "X", "status": "Error", "fails": 3, "lastOk": "—"},
        {"id": "DS-Y", "name": "Y", "status": "Warning", "fails": 1, "lastOk": "06:00"},
        {"id": "DS-Z", "name": "Z", "status": "OK", "fails": 0, "lastOk": "08:00"},
    ]
    ns = build_notifications(cards, sources)
    kinds = {n["kind"] for n in ns}
    assert {"risk", "data", "wbgt", "river", "source"} <= kinds
    # 重大度降順
    sev = [n["severity"] for n in ns]
    assert sev == sorted(sev, reverse=True)
    # 各通知に免責文
    assert all(n.get("disclaimer") for n in ns)
    # OK ソース・通常現場は通知を生まない
    assert not any(n["id"] == "src-DS-Z" for n in ns)


def _notif(time="08:00", severity=2):
    return {"id": "risk-S01", "kind": "risk", "siteId": "S01", "severity": severity,
            "title": "中止検討", "message": "現場A：洪水情報", "time": time}


def _clear_notification_state(db):
    db.query(NotificationDelivery).delete()
    db.query(AppSetting).filter(AppSetting.key == "notify").delete()
    db.commit()


@pytest.mark.asyncio
async def test_dispatch_logs_high_severity_and_suppresses_duplicates(client, monkeypatch):
    monkeypatch.setattr(notifications.settings, "slack_webhook_url", "")
    monkeypatch.setattr(notifications.settings, "teams_webhook_url", "")
    monkeypatch.setattr(notifications.settings, "notification_dedup_seconds", 3600)

    with SessionLocal() as db:
        _clear_notification_state(db)
        first = await notifications.dispatch(db, [_notif(), _notif(severity=1)], now=100.0)
        second = await notifications.dispatch(db, [_notif(time="08:05")], now=200.0)

        rows = db.scalars(select(NotificationDelivery)).all()

    assert first == {"sent": 0, "logged": 1, "suppressed": 0, "failed": 0}
    assert second == {"sent": 0, "logged": 0, "suppressed": 1, "failed": 0}
    assert len(rows) == 1 and rows[0].channel == "log"


@pytest.mark.asyncio
async def test_dispatch_ttl_expiry_allows_repeat(client, monkeypatch):
    monkeypatch.setattr(notifications.settings, "notification_dedup_seconds", 10)

    with SessionLocal() as db:
        _clear_notification_state(db)
        first = await notifications.dispatch(db, [_notif()], now=100.0)
        second = await notifications.dispatch(db, [_notif()], now=105.0)
        third = await notifications.dispatch(db, [_notif()], now=111.0)

    assert first["logged"] == 1
    assert second["suppressed"] == 1
    assert third["logged"] == 1


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FailingClient:
    def __init__(self):
        self.calls = 0

    async def post(self, url, json):
        self.calls += 1
        return _Resp(500)


@pytest.mark.asyncio
async def test_failed_webhook_is_not_marked_sent_and_retries(client, monkeypatch):
    monkeypatch.setattr(notifications.settings, "slack_webhook_url", "https://hooks.example/slack")
    monkeypatch.setattr(notifications.settings, "teams_webhook_url", "")
    monkeypatch.setattr(notifications.settings, "notification_dedup_seconds", 3600)

    with SessionLocal() as db:
        _clear_notification_state(db)
        db.add(AppSetting(key="notify", value=json.dumps({"slack_enabled": True,
                                                          "teams_enabled": False}),
                          updated_at="now", updated_by="test"))
        db.commit()
        fake = _FailingClient()
        first = await notifications.dispatch(db, [_notif()], client=fake, now=100.0)
        second = await notifications.dispatch(db, [_notif()], client=fake, now=101.0)
        row = db.scalar(select(NotificationDelivery).where(NotificationDelivery.channel == "slack"))

    assert first["failed"] == 1 and first["suppressed"] == 0
    assert second["failed"] == 1 and second["suppressed"] == 0
    assert fake.calls == 2
    assert row.status == "failed" and row.last_sent_at is None


@pytest.mark.asyncio
async def test_notify_settings_gate_external_targets(client, monkeypatch):
    monkeypatch.setattr(notifications.settings, "slack_webhook_url", "https://hooks.example/slack")
    monkeypatch.setattr(notifications.settings, "teams_webhook_url", "")

    with SessionLocal() as db:
        _clear_notification_state(db)
        assert notifications.enabled_targets(db) == [("log", "")]
        db.add(AppSetting(key="notify", value=json.dumps({"slack_enabled": True,
                                                          "teams_enabled": False}),
                          updated_at="now", updated_by="test"))
        db.commit()
        assert notifications.enabled_targets(db) == [("slack", "https://hooks.example/slack")]
