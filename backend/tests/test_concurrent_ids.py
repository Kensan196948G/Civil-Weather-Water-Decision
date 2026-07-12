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
