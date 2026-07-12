"""判定ルール（閾値）管理APIのテスト（#34/#35, FR-054）。

TH は decision_engine のルール述語が直接参照するグローバル辞書のため、
各テスト末尾で既定値へ戻し他テストへ影響を残さない。
"""
import pytest

from app.services.decision_engine import DEFAULT_TH, TH, Reading, evaluate
from tests.conftest import login_token


@pytest.fixture(autouse=True)
def _restore_th():
    yield
    # テストが変更した閾値を出荷時既定へ戻す（DB行はテストごとのリセットPUTで消す）
    TH.update(DEFAULT_TH)


def test_get_rules_returns_defaults(client):
    r = client.get("/api/admin/rules")
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert set(rules) == set(DEFAULT_TH)
    assert rules["rain_heavy"]["default"] == 5.0
    assert rules["rain_heavy"]["overridden"] is False
    assert rules["rain_heavy"]["label"]  # メタ情報が付く


def test_put_rules_overrides_and_affects_engine(client):
    # 豪雨閾値を 5.0 → 20.0 へ緩和すると、降雨10mm/hの判定が「中止検討」から下がる
    before = evaluate("earthwork", Reading(precip_mm_h=10.0, temp_c=20.0, wind_ms=3.0))
    assert before["overall_level"] == 2

    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": 20.0}})
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert rules["rain_heavy"]["value"] == 20.0
    assert rules["rain_heavy"]["overridden"] is True
    assert rules["rain_heavy"]["updated_by"] == "admin"

    after = evaluate("earthwork", Reading(precip_mm_h=10.0, temp_c=20.0, wind_ms=3.0))
    assert after["overall_level"] == 1  # rain_light(1.0)以上 rain_heavy(20.0)未満 → 注意

    # 既定値へリセット（value=null）→ エンジンも元へ
    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": None}})
    assert r.status_code == 200
    rules = {x["key"]: x for x in r.json()["rules"]}
    assert rules["rain_heavy"]["overridden"] is False
    reset = evaluate("earthwork", Reading(precip_mm_h=10.0, temp_c=20.0, wind_ms=3.0))
    assert reset["overall_level"] == 2


def test_put_rules_requires_admin(client):
    token = login_token(client, "yamada")  # site_manager
    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": 10.0}},
                   headers={"Authorization": "Bearer " + token})
    assert r.status_code == 403
    # 閲覧は tech_manager 以上（site_manager は不可）
    r = client.get("/api/admin/rules", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 403


def test_put_rules_validation(client):
    # 不明キー
    r = client.put("/api/admin/rules", json={"updates": {"nope": 1.0}})
    assert r.status_code == 422
    # 範囲外
    r = client.put("/api/admin/rules", json={"updates": {"rain_heavy": 9999}})
    assert r.status_code == 422
    # 大小関係の矛盾（注意 >= 中止検討 は不可）
    r = client.put("/api/admin/rules", json={"updates": {"rain_light": 6.0}})
    assert r.status_code == 422
    assert "rain_light" in r.json()["detail"]
    # 空更新
    r = client.put("/api/admin/rules", json={"updates": {}})
    assert r.status_code == 422


def test_put_rules_pair_update_consistent(client):
    # 両方同時に動かす整合更新は許可される（実効値で検証している）
    r = client.put("/api/admin/rules",
                   json={"updates": {"rain_light": 2.0, "rain_heavy": 8.0}})
    assert r.status_code == 200
    # 後始末: 既定値へ
    r = client.put("/api/admin/rules",
                   json={"updates": {"rain_light": None, "rain_heavy": None}})
    assert r.status_code == 200


def test_rules_update_is_audited(client):
    client.put("/api/admin/rules", json={"updates": {"gust_stop": 15.0}})
    rows = client.get("/api/admin/audit-logs").json()
    assert any(x["action"] == "rules_update" and "gust_stop=15.0" in (x.get("message") or "")
               for x in rows)
    client.put("/api/admin/rules", json={"updates": {"gust_stop": None}})
