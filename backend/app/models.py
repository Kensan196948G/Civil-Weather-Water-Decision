"""SQLAlchemy モデル（詳細設計 §6 のサブセット, PoC/SQLite 版）。

PoC 簡素化: stations は site に直結（設計の site_stations 多対多は将来導入）。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
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
