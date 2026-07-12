"""#30 T2-02 / FR-035: 現場別の川の防災情報リンク管理の検証。

新テーブル site_links に対する CRUD、admin/tech_manager 限定の RBAC 境界、
https 限定の URL バリデーション（javascript:/data:/http/空白 等の拒否）、
現場詳細(GET /api/sites/{id})の links への反映を確認する。
監査は #63 と同じ同一トランザクション方式のため、監査失敗時にリンクが作成されないことも確認する。

共有テストDBを汚さないよう、作成した site_links と増えた監査行は各テスト末尾で削除する
（seed 済みの SL001..SL005 は他テスト前提のため残す）。RBAC 用の別ロールは Authorization
ヘッダの差し替えで演じる（conftest の client は既定で admin トークンを持つ）。
"""
import pytest
from sqlalchemy import func, select

from app.api import routes as routes_mod
from app.core.db import SessionLocal
from app.models import AuditLog, SiteLink


def _boom(*args, **kwargs):
    """audit_add の差し替え用: 監査書き込み失敗を模擬する。"""
    raise RuntimeError("simulated audit failure (#30 test)")


def _token(client, username, password="pass1234"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return r.json().get("token")


def _auth_headers(client, username, password="pass1234"):
    return {"Authorization": f"Bearer {_token(client, username, password)}"}


def _count_links(site_id: str) -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(SiteLink)
                         .where(SiteLink.site_id == site_id))
    finally:
        db.close()


def _max_audit_id() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.max(AuditLog.id))) or 0
    finally:
        db.close()


def _cleanup(link_ids, baseline_audit: int) -> None:
    """作成した site_links と、テスト中に増えた監査行(baselineより後)を削除。"""
    db = SessionLocal()
    try:
        if link_ids:
            for row in db.scalars(select(SiteLink).where(SiteLink.id.in_(link_ids))):
                db.delete(row)
        for row in db.scalars(select(AuditLog).where(AuditLog.id > baseline_audit)):
            db.delete(row)
        db.commit()
    finally:
        db.close()


def test_create_list_and_reflect_in_site_detail(client):
    """POST でリンク作成 → 一覧・現場詳細(GET /api/sites/{id})の links に反映される。"""
    baseline = _max_audit_id()
    before = _count_links("S01")  # seed の SL001 が1件ある
    created = []
    try:
        r = client.post("/api/sites/S01/links",
                        json={"label": "気象庁 防災情報", "url": "https://www.jma.go.jp/bosai/",
                              "kind": "weather", "sort_order": 2})
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        created.append(lid)
        assert _count_links("S01") == before + 1

        rows = client.get("/api/sites/S01/links").json()
        assert any(x["id"] == lid and x["kind"] == "weather" for x in rows), rows

        detail = client.get("/api/sites/S01").json()
        assert "links" in detail, "現場詳細に links キーがあるべき"
        assert any(x["id"] == lid for x in detail["links"]), detail["links"]
    finally:
        _cleanup(created, baseline)


def test_site_link_rbac_boundary(client):
    """作成/更新/削除は admin・tech_manager 限定。site_manager/safety/viewer は 403。閲覧は認証で可。"""
    baseline = _max_audit_id()
    created = []
    payload = {"label": "RBACテストリンク", "url": "https://example.com/", "kind": "other"}
    try:
        # 書き込み権限のないロールは 403
        for uname in ("yamada", "takahashi", "viewer"):  # site_manager/safety/viewer
            r = client.post("/api/sites/S01/links", json=payload,
                            headers=_auth_headers(client, uname))
            assert r.status_code == 403, f"{uname} はリンク作成不可であるべき: {r.status_code}"

        # tech_manager は可
        r = client.post("/api/sites/S01/links", json=payload,
                        headers=_auth_headers(client, "tanaka"))
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])
        # admin(既定)も可（同一URLは重複409になるため別URLで検証）
        r = client.post("/api/sites/S01/links",
                        json={**payload, "url": "https://example.com/rbac-admin"})
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])

        # 閲覧は認証があれば viewer でも可
        r = client.get("/api/sites/S01/links", headers=_auth_headers(client, "viewer"))
        assert r.status_code == 200, r.text
    finally:
        _cleanup(created, baseline)


def test_site_link_url_validation(client):
    """https 以外・危険スキーム・空白埋め込みの URL は 422 で拒否、正当な https は 201。"""
    baseline = _max_audit_id()
    created = []
    bad_urls = [
        "http://example.com/",        # 非https（非暗号）
        "javascript:alert(1)",        # 危険スキーム
        "data:text/html,<script>",    # dataスキーム
        "https://exa mple.com/",      # 空白埋め込み
        "ftp://example.com/",         # 非https
        "//example.com/",             # スキームなし
        "",                           # 空
    ]
    try:
        for u in bad_urls:
            r = client.post("/api/sites/S01/links",
                            json={"label": "x", "url": u, "kind": "other"})
            assert r.status_code == 422, f"不正URLは422であるべき: {u!r} -> {r.status_code}"

        r = client.post("/api/sites/S01/links",
                        json={"label": "正常リンク", "url": "https://www.example.com/path",
                              "kind": "other"})
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])

        # kind の範囲外も 422
        r = client.post("/api/sites/S01/links",
                        json={"label": "y", "url": "https://example.com/", "kind": "unknown"})
        assert r.status_code == 422, "範囲外kindは422であるべき"
    finally:
        _cleanup(created, baseline)


def test_update_site_link(client):
    """PUT で label/url/kind を更新でき、不正URLでの更新は 422。"""
    baseline = _max_audit_id()
    created = []
    try:
        r = client.post("/api/sites/S01/links",
                        json={"label": "更新前", "url": "https://example.com/a", "kind": "other"})
        lid = r.json()["id"]
        created.append(lid)

        r = client.put(f"/api/site-links/{lid}",
                       json={"label": "更新後", "url": "https://example.com/b", "kind": "disaster"})
        assert r.status_code == 200, r.text
        row = next(x for x in client.get("/api/sites/S01/links").json() if x["id"] == lid)
        assert row["label"] == "更新後"
        assert row["url"] == "https://example.com/b"
        assert row["kind"] == "disaster"

        # 不正URLでの更新は 422（＝更新されない）
        r = client.put(f"/api/site-links/{lid}", json={"url": "javascript:void(0)"})
        assert r.status_code == 422
    finally:
        _cleanup(created, baseline)


def test_delete_site_link(client):
    """DELETE でリンクが一覧から消える。削除も admin/tech_manager 限定（viewer は 403）。"""
    baseline = _max_audit_id()
    r = client.post("/api/sites/S01/links",
                    json={"label": "削除対象", "url": "https://example.com/del", "kind": "other"})
    lid = r.json()["id"]
    try:
        # viewer は削除不可（seed の SL001 を対象にしても 403 で守られる）
        r = client.delete("/api/site-links/SL001", headers=_auth_headers(client, "viewer"))
        assert r.status_code == 403, "viewer はリンク削除不可であるべき"

        r = client.delete(f"/api/site-links/{lid}")
        assert r.status_code == 200, r.text
        rows = client.get("/api/sites/S01/links").json()
        assert all(x["id"] != lid for x in rows), "削除後は一覧に無いべき"
    finally:
        _cleanup([lid], baseline)


def test_site_link_create_atomic_on_audit_failure(client, monkeypatch):
    """#63 と同方式: 監査失敗時にリンク行が残らず、復旧後の作成はちょうど +1。"""
    baseline = _max_audit_id()
    before = _count_links("S01")
    original = routes_mod.audit_add
    payload = {"label": "原子性テスト", "url": "https://example.com/atomic", "kind": "other"}

    monkeypatch.setattr(routes_mod, "audit_add", _boom)
    with pytest.raises(RuntimeError):
        client.post("/api/sites/S01/links", json=payload)
    assert _count_links("S01") == before, "監査失敗時にリンク行が残ってはならない（原子性）"

    monkeypatch.setattr(routes_mod, "audit_add", original)
    r = client.post("/api/sites/S01/links", json=payload)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    try:
        assert _count_links("S01") == before + 1
    finally:
        _cleanup([lid], baseline)


# ---------------------------------------------------------------------------
# 対抗レビュー対応（#30）: URL偽装バイパス[high]・件数上限/重複[medium]・FK整合[medium]
# ---------------------------------------------------------------------------

def test_site_link_url_spoofing_rejected(client):
    """[high] userinfo/バックスラッシュ/DEL・C1制御文字/超長URLによるホスト偽装・注入を拒否する。"""
    bads = [
        "https://river.go.jp@evil.example/path",     # userinfo によるドメイン偽装
        "https://river.go.jp\\@evil.example/path",   # バックスラッシュのパーサ差悪用
        "https://example.com/\x7f",                   # DEL(0x7f) 制御文字
        "https://example.com/\x9f",                   # C1(0x9f) 制御文字
        "https://" + "a" * 500 + ".example.com/",    # 500文字超
        "https://@example.com/",                      # 空 userinfo も拒否
    ]
    for bad in bads:
        r = client.post("/api/sites/S01/links",
                        json={"label": "偽装テスト", "url": bad, "kind": "river"})
        assert r.status_code == 422, f"{bad!r} は拒否されるべき（実際: {r.status_code}）"


def test_site_link_duplicate_url_rejected(client):
    """[medium] 同一現場への同一URLは作成・更新とも 409。"""
    r1 = client.post("/api/sites/S03/links",
                     json={"label": "重複元", "url": "https://example.com/dup", "kind": "other"})
    assert r1.status_code == 201, r1.text
    id1 = r1.json()["id"]
    r2 = client.post("/api/sites/S03/links",
                     json={"label": "重複その2", "url": "https://example.com/dup", "kind": "other"})
    assert r2.status_code == 409
    r3 = client.post("/api/sites/S03/links",
                     json={"label": "別URL", "url": "https://example.com/dup2", "kind": "other"})
    assert r3.status_code == 201
    id3 = r3.json()["id"]
    # 更新で既存URLへ変更するのも 409（自分自身への更新は許可）
    ru = client.put(f"/api/site-links/{id3}", json={"url": "https://example.com/dup"})
    assert ru.status_code == 409
    ru_self = client.put(f"/api/site-links/{id1}", json={"url": "https://example.com/dup"})
    assert ru_self.status_code == 200
    for lid in (id1, id3):
        assert client.delete(f"/api/site-links/{lid}").status_code == 200


def test_site_link_max_per_site(client):
    """[medium] 1現場あたり最大20件。21件目は 422。"""
    existing = client.get("/api/sites/S04/links").json()
    created = []
    try:
        for i in range(20 - len(existing)):
            r = client.post("/api/sites/S04/links",
                            json={"label": f"上限テスト{i}", "url": f"https://example.com/cap/{i}",
                                  "kind": "other"})
            assert r.status_code == 201, r.text
            created.append(r.json()["id"])
        over = client.post("/api/sites/S04/links",
                           json={"label": "21件目", "url": "https://example.com/cap/over",
                                 "kind": "other"})
        assert over.status_code == 422
        assert "最大" in over.json()["detail"]
    finally:
        for lid in created:
            client.delete(f"/api/site-links/{lid}")


def test_site_link_fk_orphan_rejected():
    """[medium] PRAGMA foreign_keys=ON により存在しない site_id の直接INSERTが失敗する（孤児防止）。"""
    from sqlalchemy.exc import IntegrityError
    db = SessionLocal()
    try:
        db.add(SiteLink(id="SL999", site_id="NOPE_SITE", label="孤児テスト",
                        url="https://example.com/orphan", kind="other", sort_order=0))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()
