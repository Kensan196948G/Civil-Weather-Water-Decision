"""#49 対抗レビュー対応: 同時作成バーストでも全リクエストが一意IDで成功することを検証。

リトライ回数(_ID_COMMIT_ATTEMPTS=5)を超える 6 並行で各エンドポイントを叩き、
409 が発生しないこと・IDが重複しないことを確認する（id_counters による直列採番）。
共有テストDB（シード件数前提の他テストあり）を汚さないよう、作成行は各テスト末尾で削除する。
"""
import threading

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import DecisionLog, DecisionReason, DecisionResult, Site, WorkPlan

N = 6  # リトライ回数(5)より大きい同時数


def _run_concurrent(fn):
    results = []
    lock = threading.Lock()

    def worker(i):
        out = fn(i)
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _delete_rows(model, ids):
    """作成した行を後始末（シード件数前提の他テストへの影響を残さない）。"""
    if not ids:
        return
    db = SessionLocal()
    try:
        for row in db.scalars(select(model).where(model.id.in_(ids))):
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _delete_reasons_for(result_ids):
    if not result_ids:
        return
    db = SessionLocal()
    try:
        for row in db.scalars(select(DecisionReason)
                              .where(DecisionReason.decision_result_id.in_(result_ids))):
            db.delete(row)
        db.commit()
    finally:
        db.close()


def test_concurrent_site_creation_all_succeed(client):
    def create(i):
        r = client.post("/api/sites", json={
            "name": f"同時登録テスト{i}", "loc": "X市", "latitude": 35.0 + i * 0.001,
            "longitude": 139.0, "work_type": "earthwork",
        })
        return (r.status_code, r.json().get("id"))

    results = _run_concurrent(create)
    codes = [c for c, _ in results]
    ids = [i for _, i in results if i]
    try:
        assert codes == [201] * N, f"全件201であるべき: {codes}"
        assert len(set(ids)) == N, f"IDが一意であるべき: {sorted(ids)}"
    finally:
        _delete_rows(Site, ids)


def test_concurrent_work_plan_creation_all_succeed(client):
    def create(i):
        r = client.post("/api/work-plans", json={
            "site_id": "S01", "work_type": "river", "title": f"同時予定{i}",
            "planned_start": "2026-06-21T08:00", "planned_end": "2026-06-21T12:00",
        })
        return (r.status_code, r.json().get("id"))

    results = _run_concurrent(create)
    codes = [c for c, _ in results]
    ids = [i for _, i in results if i]
    try:
        assert codes == [201] * N, f"全件201であるべき: {codes}"
        assert len(set(ids)) == N, f"IDが一意であるべき: {sorted(ids)}"
    finally:
        _delete_rows(WorkPlan, ids)


def test_concurrent_decision_log_creation_all_succeed(client):
    def create(i):
        r = client.post("/api/decision-logs", json={
            "site_id": "S01", "work_type": "河川内作業", "level": 1,
            "action": "monitor", "comment": f"同時記録{i}",
        })
        return (r.status_code, (r.json() or {}).get("id"))

    results = _run_concurrent(create)
    codes = [c for c, _ in results]
    ids = [i for _, i in results if i]
    try:
        assert all(c in (200, 201) for c in codes), f"全件成功であるべき: {codes}"
        assert len(set(ids)) == len(ids), f"IDが一意であるべき: {sorted(ids)}"
    finally:
        _delete_rows(DecisionLog, ids)


def test_concurrent_evaluate_all_succeed(client):
    def create(i):
        r = client.post("/api/decisions/evaluate", json={
            "site_id": "S01", "work_type": "river",
            "start": "2026-06-20T08:00", "end": "2026-06-20T12:00",
        })
        return (r.status_code, r.json().get("resultId"))

    results = _run_concurrent(create)
    codes = [c for c, _ in results]
    ids = [i for _, i in results if i]
    try:
        assert codes == [200] * N, f"全件200であるべき: {codes}"
        assert len(set(ids)) == N, f"DecisionResult IDが一意であるべき: {sorted(ids)}"
    finally:
        _delete_reasons_for(ids)
        _delete_rows(DecisionResult, ids)


def test_stale_counter_self_heals(client):
    """カウンタが実テーブルより遅れていても、重複検出→再同期で採番が自己修復する（対抗レビュー[high]）。"""
    from app.models import IdCounter

    db = SessionLocal()
    try:
        row = db.get(IdCounter, "S")
        if row is None:
            db.add(IdCounter(name="S", value=1))
        else:
            row.value = 1  # 実テーブル(S01..S16+)より大幅に遅らせる
        db.commit()
    finally:
        db.close()

    r = client.post("/api/sites", json={
        "name": "カウンタ遅延回復テスト", "loc": "X市",
        "latitude": 35.9, "longitude": 139.9, "work_type": "earthwork",
    })
    sid = r.json().get("id")
    try:
        assert r.status_code == 201, f"再同期リトライで成功すべき: {r.status_code} {r.json()}"
        db = SessionLocal()
        try:
            assert db.get(IdCounter, "S").value >= _maxnum_sites(db), "カウンタが実maxまで回復すべき"
        finally:
            db.close()
    finally:
        _delete_rows(Site, [sid] if sid else [])


def _maxnum_sites(db) -> int:
    ids = db.scalars(select(Site.id)).all()
    nums = [int(x[1:]) for x in ids if x[1:].isdigit()]
    return max(nums) if nums else 0


def test_permanent_db_error_not_masked_as_409():
    """恒久的なDB障害（テーブル不存在等）は409に偽装せずそのまま送出する（対抗レビュー[medium]）。"""
    import sqlite3

    import pytest
    from sqlalchemy.exc import OperationalError

    from app.api.routes import _commit_with_retry

    class _StubDb:
        def rollback(self):
            pass

        def commit(self):
            pass

        def execute(self, *a, **kw):  # _resync_counters は呼ばれない想定（IntegrityErrorのみ）
            raise AssertionError("unexpected")

    def _raise_permanent():
        raise OperationalError("stmt", {}, sqlite3.OperationalError("no such table: id_counters"))

    with pytest.raises(OperationalError):
        _commit_with_retry(_StubDb(), _raise_permanent)


def test_transient_lock_error_exhausts_to_409(monkeypatch):
    """一時的なロックエラーはリトライし、枯渇時のみ409を返す。"""
    import sqlite3

    import pytest
    from fastapi import HTTPException
    from sqlalchemy.exc import OperationalError

    from app.api import routes as routes_mod

    monkeypatch.setattr(routes_mod.time, "sleep", lambda *_: None)  # テスト高速化

    class _StubDb:
        def rollback(self):
            pass

    def _raise_locked():
        raise OperationalError("stmt", {}, sqlite3.OperationalError("database is locked"))

    with pytest.raises(HTTPException) as ei:
        routes_mod._commit_with_retry(_StubDb(), _raise_locked)
    assert ei.value.status_code == 409
