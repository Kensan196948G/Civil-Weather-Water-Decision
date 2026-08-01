"""API セキュリティヘッダと本番ドキュメント無効化の回帰テスト。"""

from __future__ import annotations

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
    "strict-transport-security": "max-age=31536000",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-site",
    "x-permitted-cross-domain-policies": "none",
    "x-download-options": "noopen",
    "cache-control": "no-store",
}


def _assert_security_headers(headers) -> None:
    for key, value in EXPECTED_HEADERS.items():
        assert headers.get(key) == value, f"{key}: {headers.get(key)!r}"
    assert "default-src 'none'" in headers.get("content-security-policy", "")


def test_health_response_has_security_headers(client):
    r = client.get("/health")
    assert r.status_code == 200
    _assert_security_headers(r.headers)


def test_readyz_response_is_no_store(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"
    _assert_security_headers(r.headers)


def test_auth_guard_response_has_security_headers(client):
    # 既定 client フィクスチャは管理者ログイン済みのため、Authorization を外して
    # 未認証ガード(401)の応答ヘッダを検証する。
    auth = client.headers.pop("Authorization", None)
    try:
        r = client.get("/api/sites")
        assert r.status_code == 401
        _assert_security_headers(r.headers)
    finally:
        if auth is not None:
            client.headers["Authorization"] = auth


def test_cors_preflight_response_has_security_headers(client):
    r = client.options(
        "/api/sites",
        headers={
            "Origin": "https://cwwd.mirai-dx-platform.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    _assert_security_headers(r.headers)


def test_docs_enabled_locally_with_security_headers(client):
    # テスト環境は APP_ENV=local のため docs は有効のまま（開発利便性）。
    # 本番では docs_url/redoc_url/openapi_url=None で無効化され、
    # deploy/scripts/security-surface-check.sh が /docs 等の 404 を継続監視する。
    for path in ("/docs", "/openapi.json"):
        r = client.get(path)
        assert r.status_code == 200, path
        _assert_security_headers(r.headers)


def test_error_response_has_security_headers(client):
    r = client.get("/api/sites/not-a-valid-uuid")
    assert r.status_code in (404, 422)
    _assert_security_headers(r.headers)
