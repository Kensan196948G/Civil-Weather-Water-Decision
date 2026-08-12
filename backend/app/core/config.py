"""アプリ設定（環境変数 / .env から読み込み）。詳細設計 §20 準拠。"""
from ipaddress import ip_address
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_DEFAULT_JWT_SECRET = "dev-secret-change-in-production-please-32+"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "Civil-Weather-Water-Decision"
    # 既定値を持たせない（必須項目）。既定 "local" だと .env 読み込み漏れ・記載ミスの際に
    # 本番相当のデプロイが気づかれないまま local 分岐（デモユーザー投入・弱鍵許容等）へ
    # フェイルオープンしてしまう（対抗レビュー #90 critical-2）。未設定なら起動時に
    # pydantic の ValidationError で即座に失敗させる。
    app_env: str
    app_timezone: str = "Asia/Tokyo"

    # PoC は SQLite（DBサーバ不要）。本番候補は PostgreSQL に切替。
    database_url: str = "sqlite:///./civil_weather_water.db"

    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    # 海洋予報（波高・周期・波向・うねり等）。Open-Meteo Marine API（無料・APIキー不要）。
    # NOWPHAS / 気象庁潮位の正式接続は利用条件確認後に別ソースとして追加する。
    open_meteo_marine_base_url: str = "https://marine-api.open-meteo.com/v1"
    data_fetch_timeout_seconds: int = 20
    data_fetch_retry_count: int = 3

    # 気象庁 防災情報XML（警報・注意報を判定に反映。公式優先 §8.3-6）
    enable_jma_warnings: bool = True
    jma_feed_url: str = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"

    # 環境省 WBGT（暑さ指数）予報の実接続（#9 / #113）。
    # 地点コード（例: 44132=東京）を設定した場合のみ公式予報値を採用し、未設定なら
    # 観測所マスタ（WBGT-ENV）からの最近傍地点解決へフォールバックする。最終的には
    # 現場ごとの最寄り地点自動選定を優先する。
    wbgt_base_url: str = "https://www.wbgt.env.go.jp"
    wbgt_station_code: str = ""
    # 環境省 地点マスタCSV（#113）。年度更新時はURLを差し替える運用
    wbgt_point_master_url: str = (
        "https://www.wbgt.env.go.jp/man15NH/wbgt_point_master-20260515.csv")

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
    admin_password: str = ""

    # Entra ID OIDC（#118）。auth_mode=oidc のときのみ OIDC ログインを有効化する。
    # 既定は app（アプリ内ユーザー＋パスワード）で、本番切替時は必ず明示する。
    auth_mode: str = "app"  # app | oidc
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_redirect_path: str = "/api/auth/oidc/callback"
    oidc_auto_provision: bool = True
    # Entra グループ（カンマ区切り）→ アプリロールのマッピング
    oidc_group_role_admin: str = ""
    oidc_group_role_tech_manager: str = ""
    oidc_group_role_site_manager: str = ""
    oidc_group_role_safety: str = ""
    oidc_group_role_viewer: str = ""

    # 設定値（AI APIキー等）の暗号化専用鍵（#80 対抗レビュー high-2）。
    # 本番では32バイト以上を起動時に必須化し、JWT_SECRET のローテーションと分離する。
    # local/test では未設定時のみ JWT_SECRET へフォールバックするが、弱鍵では ai_api_key 保存を拒否する。
    settings_encryption_key: str = ""
    # ローテーション期間中だけ旧暗号化鍵で復号するためのカンマ区切りリスト。
    # 暗号化は常に settings_encryption_key（またはlocal/testのJWT fallback）で行う。
    settings_encryption_previous_keys: str = ""

    # スケジューラ（定期プローブ＋予報リフレッシュ）。テストでは false。
    enable_scheduler: bool = True
    probe_interval_seconds: int = 300   # データソース状態を5分ごとに実プローブ更新
    forecast_refresh_seconds: int = 300  # 予報キャッシュも5分ごとにウォーム
    notification_dispatch_seconds: int = Field(default=300, gt=0)  # 外部通知/ログ通知も5分ごと
    notification_dedup_seconds: int = Field(default=3600, ge=0)  # 同一通知は1時間抑止（0で無効化）
    probe_timeout_seconds: int = 8
    # 河川観測デモ自動取得（#31 拡張）。水防災オープンデータ提供サービスの接続前は
    # 決定的シミュレーション値を自動投入し、公式未接続であることをUI/APIで明示する。
    river_demo_enabled: bool = True
    river_refresh_seconds: int = 600

    # 運用監視スナップショット（#95）。deploy/scripts/ops-status-json-export.sh が書き出す
    # secret-free な systemd 状態JSONの読み出し元。鮮度チェックの許容秒数も併せて設定。
    ops_status_json_path: str = "/var/lib/cwwd/ops-status.json"
    ops_status_json_max_age_seconds: int = 3600

    # デモ・サンプルデータ（現場・作業予定・判断履歴・公式リンク等）を seed するか。
    # 未設定(None)の場合は local のみ true、本番(app_env != local)は false と解釈する。
    # 本番の新規DBへデモ現場を誤投入しないための安全側既定（評価 2026-08-12 対応）。
    # 検証環境で明示的にデモデータが必要な場合は SEED_DEMO_DATA=true を設定する。
    seed_demo_data: bool | None = None

    @model_validator(mode="after")
    def _guard_production(self):
        previous_keys = [
            k.strip() for k in self.settings_encryption_previous_keys.split(",") if k.strip()
        ]
        if any(len(k.encode()) < 32 for k in previous_keys):
            raise RuntimeError("SETTINGS_ENCRYPTION_PREVIOUS_KEYS は各32バイト以上で設定してください")
        # 本番(app_env != local)では危険な既定を起動時に拒否（対抗レビュー #1/#3）
        if self.app_env != "local":
            if not self.enable_auth:
                raise RuntimeError("本番では ENABLE_AUTH=true 必須（認証を無効化できません）")
            if self.auth_mode == "oidc" and not (
                    self.oidc_issuer_url and self.oidc_client_id and self.oidc_client_secret):
                raise RuntimeError(
                    "本番で AUTH_MODE=oidc の場合は OIDC_ISSUER_URL / OIDC_CLIENT_ID / "
                    "OIDC_CLIENT_SECRET が必須です")
            if self.jwt_secret == _DEFAULT_JWT_SECRET or len(self.jwt_secret.encode()) < 32:
                raise RuntimeError("本番では JWT_SECRET を 32バイト以上で必ず上書きしてください")
            if len((self.settings_encryption_key or "").strip().encode()) < 32:
                raise RuntimeError("本番では SETTINGS_ENCRYPTION_KEY を 32バイト以上で必ず設定してください")
            if not self.admin_password:
                raise RuntimeError("本番では ADMIN_PASSWORD を必ず設定してください")
            try:
                db_driver = make_url(self.database_url).drivername
            except Exception as exc:
                raise RuntimeError("本番では DATABASE_URL に有効な PostgreSQL 接続文字列を設定してください") from exc
            if not db_driver.startswith("postgresql"):
                raise RuntimeError("本番では DATABASE_URL に PostgreSQL 接続文字列を設定してください")
            origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
            if not origins or "*" in origins:
                raise RuntimeError("本番では CORS_ORIGINS を本番フロントのオリジンに限定してください")
            malformed = []
            for origin in origins:
                parsed = urlparse(origin)
                is_ip_literal = False
                if parsed.hostname:
                    try:
                        ip_address(parsed.hostname)
                        is_ip_literal = True
                    except ValueError:
                        pass
                if (
                    parsed.scheme != "https"
                    or not parsed.netloc
                    or parsed.path
                    or parsed.params
                    or parsed.query
                    or parsed.fragment
                    or parsed.username
                    or parsed.password
                    or is_ip_literal
                ):
                    malformed.append(origin)
            if malformed:
                raise RuntimeError(
                    "本番では CORS_ORIGINS は https のドメイン名オリジンのみ許可してください"
                    "（IPアドレス直指定は不可。対抗レビュー #90 high-1）"
                )
        return self


settings = Settings()
