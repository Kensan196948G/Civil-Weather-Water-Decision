"""スケジューラ通知ジョブのテスト。"""

import pytest


@pytest.mark.asyncio
async def test_notification_job_builds_and_dispatches(client, monkeypatch):
    from app import scheduler

    async def fake_assess_all(sites):
        return [{
            "id": sites[0].id,
            "name": sites[0].name,
            "level": 2,
            "summary": "水位上昇",
            "wbgt": 26,
            "riverState": "rising",
            "updated": "08:00",
        }]

    seen = {}

    async def fake_dispatch(db, notifs):
        seen["notifs"] = notifs
        seen["db"] = db
        return {"sent": 0, "logged": len(notifs), "suppressed": 0}

    monkeypatch.setattr(scheduler.assessment, "assess_all", fake_assess_all)
    monkeypatch.setattr(scheduler, "dispatch", fake_dispatch)

    await scheduler._notification_job()

    assert "notifs" in seen
    assert "db" in seen
    assert any(n["severity"] >= 2 for n in seen["notifs"])
    assert any(n["kind"] == "river" for n in seen["notifs"])
