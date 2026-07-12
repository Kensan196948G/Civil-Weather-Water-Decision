"""site links (per-site official links, #30 T2-02 / FR-035)

Revision ID: d5e8f1a2b3c4
Revises: e3d9c2b1a7f0
Create Date: 2026-07-12 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e8f1a2b3c4'
down_revision: Union[str, Sequence[str], None] = 'e3d9c2b1a7f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 現場別の公式リンク（川の防災情報等）。初期データは seed.py で投入するためテーブルのみ作成。
    # ID採番カウンタ("SL")は seed / _allocate_id 側で遅延初期化するためここでは投入しない。
    op.create_table(
        'site_links',
        sa.Column('id', sa.String(length=20), nullable=False),
        sa.Column('site_id', sa.String(length=10), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('site_links')
