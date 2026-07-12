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


def test_pair_keys_are_written_atomically(client):
    """制約ペアの片方だけ更新しても、相手キーが実効値で同時に書かれる（混成不整合の防止）。"""
    r = client.put("/api/admin/rules", json={"updates": {"rain_light": 2.0}})
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert rules["rain_light"]["overridden"] is True
    assert rules["rain_heavy"]["overridden"] is True   # ペア固定書き込み
    assert rules["rain_heavy"]["value"] == 5.0          # 実効値（既定）で固定
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
