"""#63: 監査書き込みをドメイン変更と同一トランザクション化したことの検証。

旧実装はドメインを commit した後に第2の commit で監査を書いていたため、監査側が失敗すると
「ドメイン行は存在するのに 500」となり、クライアント再試行が業務データを二重登録し得た。
本テストは audit_add を故意に失敗させ、次を確認する:
  (1) 監査失敗時にドメイン行が残らずロールバックされる（原子性）
  (2) 監査復旧後の再試行がちょうど 1 件だけ作る（＝再試行二重登録が起きない）
  (3) 正常時に監査が同一 commit で記録される（監査欠落なし）

共有テストDB（シード件数前提の他テストあり）を汚さないよう、作成行・増えた監査行は各テスト末尾で削除する。
TestClient は raise_server_exceptions=True のため、ハンドラ内例外は pytest.raises で受ける。
なお conftest の client fixture が同じ monkeypatch で open_meteo をモックしているため、
解除は monkeypatch.undo() ではなく元関数の setattr で行う（他フィクスチャのモックを戻さないため）。
"""
import pytest
from sqlalchemy import func, select

from app.api import routes as routes_mod
from app.core.db import SessionLocal
from app.models import AuditLog, DecisionLog, DecisionResult, Site, WorkPlan


def _boom(*args, **kwargs):
    """audit_add の差し替え用: 監査書き込み失敗を模擬する。"""
    raise RuntimeError("simulated audit failure (#63 test)")


def _count(model, **eq) -> int:
    db = SessionLocal()
    try:
        q = select(func.count()).select_from(model)
        for k, v in eq.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)
    finally:
        db.close()


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def _delete_audit_after(baseline_id: int) -> None:
    """テスト中に増えた監査行を削除（baseline より後の id を対象。成功再試行の監査も回収）。"""
    db = SessionLocal()
    try:
        for row in db.scalars(select(AuditLog).where(AuditLog.id > baseline_id)):
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _delete_rows(model, ids) -> None:
    """作成した行を後始末（DecisionResult は relationship cascade で reasons も削除）。"""
    if not ids:
        return
    db = SessionLocal()
    try:
        for row in db.scalars(select(model).where(model.id.in_(ids))):
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _site_name(sid: str) -> str:
    db = SessionLocal()
    try:
        return db.get(Site, sid).name
    finally:
        db.close()


def _set_site_name(sid: str, name: str) -> None:
    """監査を経由せず直接更新（後始末で共有DBの S01 を元の name に戻す用）。"""
    db = SessionLocal()
    try:
        db.get(Site, sid).name = name
        db.commit()
    finally:
        db.close()


def _find_audit(action: str, msg_prefix: str):
    """message が "{msg_prefix} " で始まる監査行を返す（ID は英数字で LIKE 特殊文字を含まない）。"""
    db = SessionLocal()
    try:
        return db.scalar(select(AuditLog)
                         .where(AuditLog.action == action)
                         .where(AuditLog.message.like(f"{msg_prefix} %")))
    finally:
        db.close()


def test_site_create_atomic_rollback_on_audit_failure(client, monkeypatch):
    """create_site: 監査失敗で Site 行が残らず、復旧後の再試行がちょうど 1 件だけ作る。"""
    payload = {"name": "原子性テスト現場63", "loc": "Z市", "latitude": 35.55,
               "longitude": 139.55, "work_type": "earthwork"}
    baseline_audit = _max_audit_id()
    sites_before = _count(Site)
    audit_before = _count(AuditLog, action="site_create")
    original = routes_mod.audit_add

    # 監査書き込みを失敗させる → 作成は例外（500相当）になり、ドメイン行は残らない
    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    with pytest.raises(RuntimeError):
        client.post("/api/sites", json=payload)
    assert _count(Site) == sites_before, "監査失敗時に Site 行が残ってはならない（原子性）"
    assert _count(AuditLog, action="site_create") == audit_before, "監査行も残らない"

    # 監査を復旧して同一ペイロードで再試行 → ちょうど 1 件だけ（再試行二重登録が起きない）
    monkeypatch.setattr(routes_mod, "audit_add", original)
    r = client.post("/api/sites", json=payload)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    try:
        assert _count(Site) == sites_before + 1, "再試行後も Site はちょうど 1 件増"
        assert _count(AuditLog, action="site_create") == audit_before + 1, "監査もちょうど 1 件"
    finally:
        _delete_rows(Site, [sid])
        _delete_audit_after(baseline_audit)


def test_site_update_atomic_rollback_on_audit_failure(client, monkeypatch):
    """update_site: 監査失敗で name 更新がロールバックされ、復旧後は更新が成功する。"""
    baseline_audit = _max_audit_id()
    original_name = _site_name("S01")
    new_name = "監査失敗検証・更新名63"
    original = routes_mod.audit_add
    try:
        monkeypatch.setattr(routes_mod, "audit_add", _boom)
        with pytest.raises(RuntimeError):
            client.put("/api/sites/S01", json={"name": new_name})
        assert _site_name("S01") == original_name, "監査失敗時に更新はロールバックされる"

        monkeypatch.setattr(routes_mod, "audit_add", original)
        r = client.put("/api/sites/S01", json={"name": new_name})
        assert r.status_code == 200, r.text
        assert _site_name("S01") == new_name, "監査復旧後は更新が成功する"
    finally:
        _set_site_name("S01", original_name)  # 共有DBの S01 を元に戻す
        _delete_audit_after(baseline_audit)


def test_decision_log_create_atomic(client, monkeypatch):
    """create_decision_log: 監査失敗で DecisionLog 行が残らず、復旧後は +1。"""
    payload = {"site_id": "S01", "work_type": "河川内作業", "level": 1,
               "action": "monitor", "comment": "原子性テスト63"}
    baseline_audit = _max_audit_id()
    logs_before = _count(DecisionLog)
    original = routes_mod.audit_add

    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    with pytest.raises(RuntimeError):
        client.post("/api/decision-logs", json=payload)
    assert _count(DecisionLog) == logs_before, "監査失敗時に DecisionLog 行が残ってはならない"

    monkeypatch.setattr(routes_mod, "audit_add", original)
    r = client.post("/api/decision-logs", json=payload)
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    try:
        assert _count(DecisionLog) == logs_before + 1, "復旧後は DecisionLog がちょうど +1"
    finally:
        _delete_rows(DecisionLog, [lid])
        _delete_audit_after(baseline_audit)


def test_evaluate_atomic(client, monkeypatch):
    """evaluate: 監査失敗で DecisionResult が残らず、復旧後は結果 +1・監査 +1。"""
    payload = {"site_id": "S01", "work_type": "river",
               "start": "2026-06-20T08:00", "end": "2026-06-20T12:00"}
    baseline_audit = _max_audit_id()
    results_before = _count(DecisionResult)
    audit_before = _count(AuditLog, action="evaluate")
    original = routes_mod.audit_add

    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    with pytest.raises(RuntimeError):
        client.post("/api/decisions/evaluate", json=payload)
    assert _count(DecisionResult) == results_before, "監査失敗時に DecisionResult が残ってはならない"
    assert _count(AuditLog, action="evaluate") == audit_before, "監査行も残らない"

    monkeypatch.setattr(routes_mod, "audit_add", original)
    r = client.post("/api/decisions/evaluate", json=payload)
    assert r.status_code == 200, r.text
    rid = r.json()["resultId"]
    try:
        assert _count(DecisionResult) == results_before + 1, "復旧後は DecisionResult が +1"
        assert _count(AuditLog, action="evaluate") == audit_before + 1, "監査 evaluate も +1"
    finally:
        _delete_rows(DecisionResult, [rid])  # cascade で reasons も削除
        _delete_audit_after(baseline_audit)


def test_audit_written_in_same_commit(client):
    """正常系: work-plan 作成が成功すると監査行が同一 commit で記録される（監査欠落なし）。"""
    baseline_audit = _max_audit_id()
    r = client.post("/api/work-plans", json={
        "site_id": "S01", "work_type": "river", "title": "監査同時記録テスト63",
        "planned_start": "2026-06-21T08:00", "planned_end": "2026-06-21T12:00"})
    assert r.status_code == 201, r.text
    wpid = r.json()["id"]
    try:
        row = _find_audit("work_plan_create", wpid)
        assert row is not None, "work_plan_create の監査が同一 commit で残るべき（監査欠落なし）"
        assert row.site_id == "S01"
        assert row.message.startswith(f"{wpid} ")
    finally:
        _delete_rows(WorkPlan, [wpid])
        _delete_audit_after(baseline_audit)
