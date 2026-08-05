"""user_site_access (site-level permissions)

Revision ID: h1i2j3k4l5m6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-05 14:00:00.000000

Issue #117（P0）: 協力会社・現場管理者向けの現場単位権限。
user_site_access で「ユーザー×現場×ロール」を管理し、アプリ層フィルタで
割当外の現場へ 403 を返す。PostgreSQL RLS は将来層（二重防御）。
実テーブルが init_db 由来で既に存在する本番環境ではスキップし、履歴整合を保つ。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h1i2j3k4l5m6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, name: str) -> bool:
    return bind.dialect.has_table(bind, name)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if not _table_exists(bind, "user_site_access"):
        op.create_table(
            "user_site_access",
            sa.Column("id", sa.String(length=20), nullable=False),
            sa.Column("user_id", sa.String(length=20), nullable=False),
            sa.Column("site_id", sa.String(length=10), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("granted_by", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["site_id"], ["sites.id"]),
            sa.UniqueConstraint(
                "user_id", "site_id",
                name="uq_user_site_access_user_site",
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if _table_exists(bind, "user_site_access"):
        op.drop_table("user_site_access")
