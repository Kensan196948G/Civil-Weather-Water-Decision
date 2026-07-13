"""アプリ設定（環境変数 / .env から読み込み）。詳細設計 §20 準拠。"""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_JWT_SECRET = "dev-secret-change-in-production-please-32+"


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

    # 気象庁 防災情報XML（警報・注意報を判定に反映。公式優先 §8.3-6）
    enable_jma_warnings: bool = True
    jma_feed_url: str = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"

    # 環境省 WBGT（暑さ指数）予報の実接続（#9）。地点コード（例: 44132=東京）を設定した
    # 場合のみ公式予報値を採用し、未設定なら従来どおり気温湿度からの推定のみ（挙動不変）。
    # 現場ごとの最寄り地点自動選定は観測所マスタ正規化（#29）で対応予定。
    wbgt_base_url: str = "https://www.wbgt.env.go.jp"
    wbgt_station_code: str = ""

    # CORS（フロントエンドの自動割り当てIP/ポートからの接続を許可するため既定は全許可。本番では絞る）
    cors_origins: str = "*"

    log_level: str = "INFO"

    # 通知（設計§14）。未設定なら画面内通知のみ（外部送信は no-op に縮退）
    slack_webhook_url: str = ""
    teams_webhook_url: str = ""

    # 認証（PoC: アプリ内ユーザー＋JWT。本番候補は Entra ID OIDC へ差し替え）
    enable_auth: bool = True
    # 本番は環境変数 JWT_SECRET で必ず上書き（32バイト以上）
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_expire_minutes: int = 480

    # 設定値（AI APIキー等）の暗号化専用鍵（#80 対抗レビュー high-2）。32バイト以上を推奨。
    # 設定されていれば鍵導出でこちらを優先する。未設定なら JWT_SECRET から導出するが、
    # JWT_SECRET が既定値/32バイト未満のときは ai_api_key の保存を拒否する
    # （crypto.encryption_is_strong と routes の保存ガードで強制）。
    settings_encryption_key: str = ""

    # スケジューラ（定期プローブ＋予報リフレッシュ）。テストでは false。
    enable_scheduler: bool = True
    probe_interval_seconds: int = 300   # データソース状態を5分ごとに実プローブ更新
    forecast_refresh_seconds: int = 300  # 予報キャッシュも5分ごとにウォーム
    probe_timeout_seconds: int = 8

    # 運用監視スナップショット（#95）。deploy/scripts/ops-status-json-export.sh が書き出す
    # secret-free な systemd 状態JSONの読み出し元。鮮度チェックの許容秒数も併せて設定。
    ops_status_json_path: str = "/var/lib/cwwd/ops-status.json"
    ops_status_json_max_age_seconds: int = 3600

    @model_validator(mode="after")
    def _guard_production(self):
        # 本番(app_env != local)では危険な既定を起動時に拒否（対抗レビュー #1/#3）
        if self.app_env != "local":
            if not self.enable_auth:
                raise RuntimeError("本番では ENABLE_AUTH=true 必須（認証を無効化できません）")
            if self.jwt_secret == _DEFAULT_JWT_SECRET or len(self.jwt_secret.encode()) < 32:
                raise RuntimeError("本番では JWT_SECRET を 32バイト以上で必ず上書きしてください")
        return self


settings = Settings()
