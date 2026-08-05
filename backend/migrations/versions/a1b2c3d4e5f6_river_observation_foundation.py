"""river observation foundation (station master / site link / observations)

Revision ID: a1b2c3d4e5f6
Revises: f2a4d6c8e9b1
Create Date: 2026-08-05 12:00:00.000000

Issue #29/#31（T2-01/T2-03）: 観測所マスタ・現場紐付け・実測値保存の基盤。
自動取得プロバイダ（水防災オープンデータ提供サービス等）は接続前のため、
まず手動入力・管理APIで運用できる状態を作る。実テーブルが init_db 由来で
既に存在する本番環境ではスキップし、履歴整合を保つ（既存マイグレーションと同方式）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f2a4d6c8e9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, name: str) -> bool:
    return bind.dialect.has_table(bind, name)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if not _table_exists(bind, "observation_stations"):
        op.create_table(
            "observation_stations",
            sa.Column("id", sa.String(length=20), nullable=False),
            sa.Column("source_id", sa.String(length=40), nullable=False),
            sa.Column("station_code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("agency", sa.String(length=100), nullable=False),
            sa.Column("basin_name", sa.String(length=100), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_id", "station_code",
                name="uq_observation_station_source_code",
            ),
        )
    if not _table_exists(bind, "site_stations"):
        op.create_table(
            "site_stations",
            sa.Column("id", sa.String(length=20), nullable=False),
            sa.Column("site_id", sa.String(length=10), nullable=False),
            sa.Column("station_id", sa.String(length=20), nullable=False),
            sa.Column("rel", sa.String(length=20), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["site_id"], ["sites.id"]),
            sa.ForeignKeyConstraint(["station_id"], ["observation_stations.id"]),
            sa.UniqueConstraint(
                "site_id", "station_id",
                name="uq_site_station_site_station",
            ),
        )
    if not _table_exists(bind, "river_observations"):
        op.create_table(
            "river_observations",
            sa.Column("id", sa.String(length=20), nullable=False),
            sa.Column("station_id", sa.String(length=20), nullable=False),
            sa.Column("observed_at", sa.String(length=40), nullable=False),
            sa.Column("water_level_m", sa.Float(), nullable=True),
            sa.Column("rainfall_mm_h", sa.Float(), nullable=True),
            sa.Column("quality", sa.String(length=20), nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False),
            sa.Column("recorded_at", sa.String(length=40), nullable=False),
            sa.Column("recorded_by", sa.String(length=100), nullable=False),
            sa.Column("note", sa.String(length=200), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["station_id"], ["observation_stations.id"]),
        )
        op.create_index(
            "ix_river_observations_station_observed",
            "river_observations",
            ["station_id", "observed_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    for table in ("river_observations", "site_stations", "observation_stations"):
        if _table_exists(bind, table):
            op.drop_table(table)
