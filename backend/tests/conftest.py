"""テスト共通設定: テスト用SQLite＋Open-Meteoのモック（ネット非依存）。"""
import os
import pathlib

# app インポート前にテスト用DBへ差し替え（本番DBを汚さない）＋スケジューラ無効化（ネット非依存）
os.environ["DATABASE_URL"] = "sqlite:///./_test_cw.db"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["ENABLE_JMA_WARNINGS"] = "false"  # 気象庁XML取得を無効化（ネット非依存）
_db = pathlib.Path("_test_cw.db")
if _db.exists():
    _db.unlink()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SAMPLE = {
    "timezone": "Asia/Tokyo",
    "hourly": {
        "time": ["2026-06-20T08:00", "2026-06-20T09:00", "2026-06-20T10:00", "2026-06-20T11:00"],
        "temperature_2m": [28.0, 30.5, 31.2, 30.0],
        "precipitation": [0.0, 1.5, 3.0, 0.5],
        "wind_speed_10m": [4.0, 6.5, 7.0, 5.0],
        "wind_gusts_10m": [8.0, 12.0, 14.0, 10.0],
        "relative_humidity_2m": [70, 65, 60, 63],
        "weather_code": [2, 61, 63, 61],
    },
}


@pytest.fixture
def client(monkeypatch):
    from app.services.data_collectors import open_meteo

    async def fake_fetch(lat, lon, **kw):
        norm = open_meteo.normalize(SAMPLE)
        norm.update(status="OK", fetched_at="2026-06-20T08:00:00Z", error=None)
        return norm

    monkeypatch.setattr(open_meteo, "fetch_forecast", fake_fetch)

    from app.services import assessment
    assessment.clear_cache()

    from app.main import app
    with TestClient(app) as c:
        # 既定は管理者でログイン（多くのテストは認証済み前提）
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        c.headers.update({"Authorization": "Bearer " + r.json()["token"]})
        yield c
    assessment.clear_cache()


def login_token(client, username, password="pass1234"):
    """指定ユーザーのトークンを取得（RBAC テスト用）。"""
    return client.post("/api/auth/login",
                       json={"username": username, "password": password}).json().get("token")
