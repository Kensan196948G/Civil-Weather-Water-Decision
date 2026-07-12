"""decision rules (threshold overrides, #34/#35)

Revision ID: e3d9c2b1a7f0
Revises: c7f2a91b0d34
Create Date: 2026-07-12 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3d9c2b1a7f0'
down_revision: Union[str, Sequence[str], None] = 'c7f2a91b0d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 行が存在するキーのみ既定閾値を上書きする方式のため、初期データ投入は不要
    op.create_table(
        'decision_rules',
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.String(length=40), nullable=False),
        sa.Column('updated_by', sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('decision_rules')
