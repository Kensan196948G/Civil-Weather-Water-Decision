"""判定ルール（閾値）管理APIのテスト（#34/#35, FR-054・対抗レビュー反映版）。

設計: DBが単一の真実。評価パスは rules.effective_th()（短TTLキャッシュ）で解決する。
テスト間の影響を残さないよう、実効閾値キャッシュを前後でクリアする。
"""
import pytest

from app.services import rules as rules_service
from app.services.decision_engine import DEFAULT_TH, Reading, evaluate
from tests.conftest import login_token


@pytest.fixture(autouse=True)
def _fresh_cache():
    rules_service.clear_cache()
    yield
    rules_service.clear_cache()


def _eval_earthwork_rain10():
    """降雨10mm/hの土工判定を『現在のDB実効閾値』で実行する。"""
    return evaluate("earthwork", Reading(precip_mm_h=10.0, temp_c=20.0, wind_ms=3.0),
                    th=rules_service.effective_th())


def test_get_rules_returns_defaults(client):
    r = client.get("/api/admin/rules")
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert set(rules) == set(DEFAULT_TH)
    assert rules["rain_heavy"]["default"] == 5.0
    assert rules["rain_heavy"]["overridden"] is False
    assert rules["rain_heavy"]["label"]  # メタ情報が付く


def test_put_rules_overrides_and_affects_engine(client):
    # 豪雨閾値 5.0 のとき降雨10mm/h は「中止検討」
    assert _eval_earthwork_rain10()["overall_level"] == 2

    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": 20.0}})
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert rules["rain_heavy"]["value"] == 20.0
    assert rules["rain_heavy"]["overridden"] is True
    assert rules["rain_heavy"]["updated_by"] == "admin"

    rules_service.clear_cache()  # 評価側キャッシュを新値で引き直す
    assert _eval_earthwork_rain10()["overall_level"] == 1  # 20.0未満 → 注意止まり

    # 既定値へリセット（value=null。制約ペアの相手 rain_light も両方リセット）
    r = client.put("/api/admin/rules",
                   json={"updates": {"rain_heavy": None, "rain_light": None}})
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert rules["rain_heavy"]["overridden"] is False
    rules_service.clear_cache()
    assert _eval_earthwork_rain10()["overall_level"] == 2


def test_effective_th_follows_db_without_local_clear(client, monkeypatch):
    """他ワーカーの変更もTTL経過後に追従する（プロセスローカル状態に依存しない）。"""
    th0 = rules_service.effective_th()
    assert th0["gust_stop"] == DEFAULT_TH["gust_stop"]
    client.put("/api/admin/rules", json={"updates": {"gust_stop": 20.0}})
    # 「別ワーカー」を模擬: clear_cache は呼ばず、TTL切れだけで新値に到達できること
    monkeypatch.setattr(rules_service, "_CACHE_TTL", 0.0)
    assert rules_service.effective_th()["gust_stop"] == 20.0
    client.put("/api/admin/rules",
               json={"updates": {"gust_stop": None, "wind_strong": None}})


def test_only_explicit_keys_are_persisted(client):
    """明示指定したキーだけが上書き行になる（合成行を作ると将来の既定値変更を凍結するため）。"""
    r = client.put("/api/admin/rules", json={"updates": {"rain_light": 2.0}})
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert rules["rain_light"]["overridden"] is True
    assert rules["rain_heavy"]["overridden"] is False  # 相手キーは既定のまま（合成書き込みなし）
    client.put("/api/admin/rules", json={"updates": {"rain_light": None}})


def test_concurrent_puts_never_persist_inconsistent_pair(client):
    """空テーブル状態からの並行PUTでも、直列化により制約違反の組が永続しない（500も出さない）。"""
    import threading

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def put(payload):
        barrier.wait(timeout=10)
        r = client.put("/api/admin/rules", json={"updates": payload})
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=put, args=({"rain_light": 4.0},)),
               threading.Thread(target=put, args=({"rain_heavy": 2.0},))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(c in (200, 422) for c in results), f"500を出さない: {results}"
    rules = {x["key"]: x for x in client.get("/api/admin/rules").json()["rules"]}
    assert rules["rain_light"]["value"] < rules["rain_heavy"]["value"], \
        f"最終状態は必ず整合: light={rules['rain_light']['value']} heavy={rules['rain_heavy']['value']}"
    client.put("/api/admin/rules",
               json={"updates": {"rain_light": None, "rain_heavy": None}})


def test_put_rules_requires_admin(client):
    token = login_token(client, "yamada")  # site_manager
    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": 10.0}},
                   headers={"Authorization": "Bearer " + token})
    assert r.status_code == 403
    r = client.get("/api/admin/rules", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 403


def test_put_rules_validation(client):
    # 不明キー
    assert client.put("/api/admin/rules", json={"updates": {"nope": 1.0}}).status_code == 422
    # 範囲外
    assert client.put("/api/admin/rules", json={"updates": {"rain_heavy": 9999}}).status_code == 422
    # 大小関係の矛盾（注意 >= 中止検討 は不可）
    r = client.put("/api/admin/rules", json={"updates": {"rain_light": 6.0}})
    assert r.status_code == 422
    assert "rain_light" in r.json()["detail"]
    # 空更新
    assert client.put("/api/admin/rules", json={"updates": {}}).status_code == 422


def test_put_rules_rejects_boolean(client):
    """JSON真偽値はPydanticのfloat型で1.0/0.0に化けるため、境界で明示拒否する。"""
    r = client.put("/api/admin/rules", json={"updates": {"upstream_rain": False}})
    assert r.status_code == 422
    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": True}})
    assert r.status_code == 422


def test_rules_update_is_audited(client):
    client.put("/api/admin/rules", json={"updates": {"wbgt_danger": 33.0}})
    rows = client.get("/api/admin/audit-logs").json()
    assert any(x["action"] == "rules_update" and "wbgt_danger=33.0" in (x.get("message") or "")
               for x in rows)
    client.put("/api/admin/rules",
               json={"updates": {"wbgt_danger": None, "wbgt_caution": None}})


def test_persisted_decision_records_threshold_snapshot(client):
    """永続化された判定は使用閾値スナップショットを持ち、閾値変更後も当時のルールで監査できる。"""
    import json as _json

    from app.core.db import SessionLocal
    from app.models import DecisionResult

    r1 = client.post("/api/decisions/evaluate",
                     json={"site_id": "S01", "work_type": "earthwork"})
    rid1 = r1.json()["resultId"]

    client.put("/api/admin/rules", json={"updates": {"rain_heavy": 25.0}})
    r2 = client.post("/api/decisions/evaluate",
                     json={"site_id": "S01", "work_type": "earthwork"})
    rid2 = r2.json()["resultId"]

    db = SessionLocal()
    try:
        th1 = _json.loads(db.get(DecisionResult, rid1).thresholds_json)
        th2 = _json.loads(db.get(DecisionResult, rid2).thresholds_json)
    finally:
        db.close()
    assert th1["rain_heavy"] == 5.0   # 変更前の判定は当時の閾値を保持
    assert th2["rain_heavy"] == 25.0  # 変更後の判定は新閾値(freshバイパスで即時反映)
    client.put("/api/admin/rules", json={"updates": {"rain_heavy": None}})


def test_persisting_evaluation_bypasses_stale_cache(client, monkeypatch):
    """他ワーカーがTTL内の古いキャッシュを持っていても、永続化パスはDB直読みで最新閾値を使う。"""
    import json as _json

    from app.core.db import SessionLocal
    from app.models import DecisionResult

    rules_service.effective_th()  # キャッシュを既定値で温める
    client.put("/api/admin/rules", json={"updates": {"rain_heavy": 30.0}})
    # 「別ワーカー」を模擬: clear_cacheの効果を打ち消し、古いキャッシュが残った状態を再現
    rules_service._cache["at"] = __import__("time").monotonic()
    rules_service._cache["th"] = dict(DEFAULT_TH)
    assert rules_service.effective_th()["rain_heavy"] == 5.0  # 表示用は古いまま(TTL内)

    r = client.post("/api/decisions/evaluate",
                    json={"site_id": "S01", "work_type": "earthwork"})
    rid = r.json()["resultId"]
    db = SessionLocal()
    try:
        th = _json.loads(db.get(DecisionResult, rid).thresholds_json)
    finally:
        db.close()
    assert th["rain_heavy"] == 30.0  # 永続化パスはキャッシュをバイパスして最新を使用
    client.put("/api/admin/rules", json={"updates": {"rain_heavy": None}})


def test_thresholds_not_leaked_to_general_responses(client):
    """閾値dictは/api/admin/rules以外の応答に載らない（admin読み取り境界の維持）。"""
    token = login_token(client, "viewer")
    h = {"Authorization": "Bearer " + token}
    dash = client.get("/api/dashboard/site-risk", headers=h)
    assert dash.status_code == 200
    assert "thresholds" not in dash.text.lower()
    ev = client.post("/api/decisions/evaluate", headers=h,
                     json={"site_id": "S01", "work_type": "earthwork"})
    assert ev.status_code == 200
    assert "thresholds" not in ev.text.lower()
    detail = client.get("/api/sites/S01", headers=h)
    assert detail.status_code == 200
    assert "thresholds" not in detail.text.lower()


def test_rule_change_rolls_back_when_audit_fails(client, monkeypatch):
    """監査行の書き込みが失敗したら設定変更ごとロールバック（監査なき変更を残さない）。"""
    from app.api import routes as routes_mod

    def _boom(*a, **kw):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    # TestClient は未処理例外を再送出する(実運用では汎用500)。commit前に失敗する点が本質
    with pytest.raises(RuntimeError):
        client.put("/api/admin/rules", json={"updates": {"gust_stop": 22.0}})
    monkeypatch.undo()

    rules = {x["key"]: x for x in client.get("/api/admin/rules").json()["rules"]}
    assert rules["gust_stop"]["overridden"] is False, "監査失敗時は変更が残らない"
