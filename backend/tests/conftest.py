"""テスト共通設定: テスト用SQLite＋Open-Meteoのモック（ネット非依存）。"""
import os
import pathlib

# app インポート前にテスト用DBへ差し替え（本番DBを汚さない）＋スケジューラ無効化（ネット非依存）
os.environ["APP_ENV"] = "local"
os.environ["DATABASE_URL"] = "sqlite:///./_test_cw.db"
# ローカル実行時、稼働中サービスの backend/.env（APP_ENV=production 等）を
# pydantic-settings が拾ってしまい、デモユーザー未投入・JWT鍵不一致でテストが
# 総崩れになるのを防ぐ。CI には .env が存在せずコード既定値がそのまま使われるため
# 元々問題化しなかった（ローカル/CI 差異の解消であり本番設定には影響しない）。
os.environ["APP_ENV"] = "local"
# app.core.config._DEFAULT_JWT_SECRET と同じ値。test_settings_api.py の一部テストは
# 「ログイン時点の jwt_secret がこの既定値である」前提で意図的にこの値へ monkeypatch する。
os.environ["JWT_SECRET"] = "dev-secret-change-in-production-please-32+"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["ENABLE_JMA_WARNINGS"] = "false"  # 気象庁XML取得を無効化（ネット非依存）
# 河川観測デモ自動取得は本番/ローカル向け。既存テストは「自動取得未接続」前提の
# 検証を維持するため、テスト環境では無効化し、デモ用テストで個別に有効化する。
os.environ["RIVER_DEMO_ENABLED"] = "false"
# ローカルの .env に実値があってもテストを環境非依存にするため、JWT_SECRET を
# config.py の既定値へ固定する（弱鍵ケースを検証するテストが monkeypatch で
# 同じ既定値へ差し替える前提のため、ログイン時の署名鍵と一致させる必要がある）。
os.environ["JWT_SECRET"] = "dev-secret-change-in-production-please-32+"
# 設定暗号化の専用鍵（#80 high-2）。テストでは適正構成（32バイト以上）を既定にし、
# ai_api_key 保存の正常系を成立させる。弱鍵拒否は該当テストで settings を差し替えて検証。
os.environ["SETTINGS_ENCRYPTION_KEY"] = "test-only-settings-encryption-key-32bytes-plus-000"
_db = pathlib.Path("_test_cw.db")
for _path in (_db, pathlib.Path("_test_cw.db-wal"), pathlib.Path("_test_cw.db-shm")):
    if _path.exists():
        _path.unlink()

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
    from app.services.data_collectors import marine

    async def fake_fetch(lat, lon, **kw):
        norm = open_meteo.normalize(SAMPLE)
        norm.update(status="OK", fetched_at="2026-06-20T08:00:00Z", error=None)
        return norm

    monkeypatch.setattr(open_meteo, "fetch_forecast", fake_fetch)

    MARINE_SAMPLE = {
        "timezone": "Asia/Tokyo",
        "hourly": {
            "time": ["2026-06-20T08:00", "2026-06-20T09:00", "2026-06-20T10:00"],
            "wave_height": [0.6, 1.1, 2.4],
            "wave_period": [5.0, 7.0, 9.0],
            "wave_direction": [120.0, 140.0, 160.0],
            "wind_wave_height": [0.5, 0.9, 1.6],
            "wind_wave_period": [4.0, 5.5, 7.0],
            "wind_wave_direction": [110.0, 130.0, 150.0],
            "swell_wave_height": [0.3, 0.7, 1.2],
            "swell_wave_period": [8.0, 10.0, 12.0],
            "swell_wave_direction": [200.0, 210.0, 220.0],
            "sea_surface_temperature": [24.0, 24.5, 25.0],
        },
    }

    async def fake_marine(lat, lon, **kw):
        norm = marine.normalize(MARINE_SAMPLE)
        norm.update(status="OK", fetched_at="2026-06-20T08:00:00Z", error=None)
        return norm

    monkeypatch.setattr(marine, "fetch_marine", fake_marine)

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
