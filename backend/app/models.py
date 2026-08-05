"""SQLAlchemy モデル（詳細設計 §6 のサブセット, PoC/SQLite 版）。

PoC 簡素化: stations は site に直結（設計の site_stations 多対多は将来導入）。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.db import Base


class User(Base):
    """アプリ内ユーザー（設計§6.2.1 / §7 ロール）。本番候補では Entra ID に差し替え。"""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(30))  # admin/tech_manager/site_manager/safety/viewer
    department: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class AuditLog(Base):
    """監査ログ（設計§13）。ログイン・設定変更・判定・判断・CSV出力等を記録。"""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(40), default="")
    user_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    component: Mapped[str] = mapped_column(String(30), default="api")
    message: Mapped[str] = mapped_column(Text, default="")
    site_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    site_code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    loc: Mapped[str] = mapped_column(String(200), default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    work_type: Mapped[str] = mapped_column(String(30))
    project_type: Mapped[str] = mapped_column(String(30), default="公共")
    river_work_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    river_state: Mapped[str] = mapped_column(String(20), default="none")  # rising/stable/stale/none
    river_note: Mapped[str] = mapped_column(String(100), default="近接なし")
    flood_info: Mapped[bool] = mapped_column(Boolean, default=False)
    manager: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")

    stations: Mapped[list["Station"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    plans: Mapped[list["WorkPlan"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    # 現場別の公式リンク（#30 T2-02 / FR-035）。現場物理削除時は cascade で一緒に削除
    links: Mapped[list["SiteLink"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", order_by="SiteLink.sort_order")


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))   # weather/river/wbgt
    rel: Mapped[str] = mapped_column(String(20))    # 上流/最寄り/参照
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    site: Mapped["Site"] = relationship(back_populates="stations")


class SiteLink(Base):
    """現場別の公式リンク（#30 T2-02 / FR-035）。川の防災情報など外部公式ページへの導線。

    現場ごとに名称・URL・種別を CRUD 管理し、現場詳細に公式導線として表示する。
    観測所マスタ正規化(#29)とは独立: リンクは site_id への単純な紐付けで観測データを持たない。
    URL は https のみ許可する（生成側 API で検証）。表示側でクリック導線に使われるため。
    """
    __tablename__ = "site_links"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    label: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(30), default="river")  # river/weather/wbgt/disaster/other
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    site: Mapped["Site"] = relationship(back_populates="links")


class WorkType(Base):
    __tablename__ = "work_types"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(20), default="#566472")


class WorkPlan(Base):
    __tablename__ = "work_plans"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    work_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200), default="")
    planned_start: Mapped[str] = mapped_column(String(40), default="")
    planned_end: Mapped[str] = mapped_column(String(40), default="")
    contractor: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="planned")
    site: Mapped["Site"] = relationship(back_populates="plans")


class DecisionResult(Base):
    """判定エンジンの評価結果（設計 §6.2.11）。監査・実績分析の正本。"""
    __tablename__ = "decision_results"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(10))
    work_type: Mapped[str] = mapped_column(String(40))
    evaluated_at: Mapped[str] = mapped_column(String(40), default="")
    overall_level: Mapped[int] = mapped_column(Integer, default=0)
    overall_label: Mapped[str] = mapped_column(String(50), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    data_quality_summary: Mapped[str] = mapped_column(Text, default="")
    weather_status: Mapped[str] = mapped_column(String(20), default="")
    # 判定に使用した実効閾値のスナップショット(JSON)。閾値は#35で可変になったため、
    # 過去判定を当時のルールで監査・再現できるよう保存する(NULL=閾値可変化以前の行)
    thresholds_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons: Mapped[list["DecisionReason"]] = relationship(
        back_populates="result", cascade="all, delete-orphan")


class DecisionReason(Base):
    """判定理由（設計 §6.2.12）。reason_code を保持し閾値見直しに使える。"""
    __tablename__ = "decision_reasons"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    decision_result_id: Mapped[str] = mapped_column(ForeignKey("decision_results.id"))
    severity: Mapped[int] = mapped_column(Integer, default=0)
    reason_code: Mapped[str] = mapped_column(String(100), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(String(50), default="")
    observed_value: Mapped[str] = mapped_column(String(100), default="")
    result: Mapped["DecisionResult"] = relationship(back_populates="reasons")


class DecisionLog(Base):
    __tablename__ = "decision_logs"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    decision_result_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    site_id: Mapped[str] = mapped_column(String(10))
    site_name: Mapped[str] = mapped_column(String(200))
    work_type: Mapped[str] = mapped_column(String(40))
    level: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(20))  # execute/postpone/cancel/monitor/other
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(100), default="")
    decided_at: Mapped[str] = mapped_column(String(40), default="")


class DataSourceStatus(Base):
    __tablename__ = "data_source_statuses"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # source_id
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="OK")  # OK/Warning/Error
    last_ok: Mapped[str] = mapped_column(String(40), default="")
    fails: Mapped[int] = mapped_column(Integer, default=0)
    avg_ms: Mapped[int] = mapped_column(Integer, default=0)
    trust: Mapped[str] = mapped_column(String(20), default="補助")  # 公式/準公式/補完/補助
    note: Mapped[str] = mapped_column(Text, default="")


class IdCounter(Base):
    """ID採番カウンタ（#49）。

    max(id)+1 方式は読取〜INSERTの間に排他が無く同時実行で重複するため、
    本テーブルの行UPDATE（DB側で直列化される）で次番号を確保する。
    name はIDプレフィックス（S/DR/WP/L）、value は払い出し済みの最大番号。
    """
    __tablename__ = "id_counters"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DecisionRule(Base):
    """判定閾値の上書き設定（#34/#35, FR-054）。

    行が存在するキーのみ既定値（decision_engine.DEFAULT_TH）を上書きする。
    初回スコープは会社基準（グローバル）のみ。現場・工種別の階層は将来拡張。
    """
    __tablename__ = "decision_rules"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default="")
    updated_by: Mapped[str] = mapped_column(String(100), default="")


class AppSetting(Base):
    """設定画面の汎用 key-value ストア（#80, エピック#72段8）。

    1キー=1行で、通知設定(notify)・データ保存期間(data_retention_days)・
    ユーザー設定(user_prefs)・AI設定(ai_api_key)を保持する。value は文字列で、
    JSON設定はJSON文字列、AI APIキーは Fernet 暗号文字列（core/crypto.py）を格納する。
    本番では SETTINGS_ENCRYPTION_KEY 32B+ を起動時必須とし、JWT_SECRET と鍵用途を分離する。
    """
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class RevokedToken(Base):
    """JWT失効台帳。jti単位でログアウト/緊急失効を永続化する。"""
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(20), nullable=False)
    revoked_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), default="logout")


class LoginAttempt(Base):
    """ログイン試行制限の永続台帳。username+client IP のハッシュキーで失敗回数を共有する。"""
    __tablename__ = "login_attempts"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_failed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    locked_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class NotificationDelivery(Base):
    """外部通知/ログ通知の配送台帳。

    channel+fingerprint で同一ハザードの再送を抑止する。fingerprint は通知時刻を含めない安定キー。
    """
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("channel", "fingerprint",
                                       name="uq_notification_delivery_channel_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    notification_id: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    last_sent_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_attempt_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class ObservationStation(Base):
    """河川・雨量観測所マスタ（#29 T2-01 / FR-031, 詳細設計 §6.2.6 の正規化版）。

    site に直結していた PoC 版 Station と異なり、観測所を独立マスタとして管理し、
    SiteStation を介して複数現場へ紐付ける（上流/最寄り/参照 の区分付き）。
    将来の自動取得では source_id に水防災オープンデータ提供サービス等の提供元を、
    station_code に公式観測所コードを格納する。
    """
    __tablename__ = "observation_stations"
    __table_args__ = (
        UniqueConstraint("source_id", "station_code",
                         name="uq_observation_station_source_code"),
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40), default="MANUAL")
    station_code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    agency: Mapped[str] = mapped_column(String(100), default="")
    basin_name: Mapped[str] = mapped_column(String(100), default="")
    kind: Mapped[str] = mapped_column(String(20), default="water")  # water/rain/water_rain
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/inactive
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class SiteStation(Base):
    """現場と観測所の多対多紐付け（#29 T2-01 / FR-031）。

    rel は upstream(上流)/nearest(最寄り)/reference(参照)。同じ観測所を複数現場へ
    共有できる。観測所を削除する場合も、この行がある限り FK で保護される。
    """
    __tablename__ = "site_stations"
    __table_args__ = (
        UniqueConstraint("site_id", "station_id",
                         name="uq_site_station_site_station"),
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    station_id: Mapped[str] = mapped_column(ForeignKey("observation_stations.id"))
    rel: Mapped[str] = mapped_column(String(20), default="nearest")  # upstream/nearest/reference
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default="")


class RiverObservation(Base):
    """観測所の実測値（#31 T2-03 / FR-032〜034）。

    water_level_m / rainfall_mm_h は少なくとも一方を持つ。observed_at は観測時刻
    （JST ISO文字列）、recorded_at は本システムへの記録時刻。quality は
    OK/MISSING/STALE/ERROR を保持し、欠測・遅延を「安全」と誤認させない。
    source は MANUAL（手動入力）または将来の自動取得提供元ID。
    """
    __tablename__ = "river_observations"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("observation_stations.id"))
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    water_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    rainfall_mm_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str] = mapped_column(String(20), default="OK")
    source: Mapped[str] = mapped_column(String(40), default="MANUAL")
    recorded_at: Mapped[str] = mapped_column(String(40), default="")
    recorded_by: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(String(200), default="")
