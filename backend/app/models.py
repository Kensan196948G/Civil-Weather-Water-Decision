"""SQLAlchemy モデル（詳細設計 §6 のサブセット, PoC/SQLite 版）。

PoC 簡素化: stations は site に直結（設計の site_stations 多対多は将来導入）。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.db import Base


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


class DecisionLog(Base):
    __tablename__ = "decision_logs"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
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
