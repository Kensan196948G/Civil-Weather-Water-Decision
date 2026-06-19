"""アプリ設定（環境変数 / .env から読み込み）。詳細設計 §20 準拠。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "Civil-Weather-Water-Decision"
    app_env: str = "local"
    app_timezone: str = "Asia/Tokyo"

    # PoC は SQLite（DBサーバ不要）。本番候補は PostgreSQL に切替。
    database_url: str = "sqlite:///./civil_weather_water.db"

    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    data_fetch_timeout_seconds: int = 20
    data_fetch_retry_count: int = 3

    # CORS（フロントエンドの自動割り当てIP/ポートからの接続を許可するため既定は全許可。本番では絞る）
    cors_origins: str = "*"

    log_level: str = "INFO"


settings = Settings()
