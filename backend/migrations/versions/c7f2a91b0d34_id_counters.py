"""id counters (serialized id allocation, #49)

Revision ID: c7f2a91b0d34
Revises: a4bce9cef9d0
Create Date: 2026-07-12 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f2a91b0d34'
down_revision: Union[str, Sequence[str], None] = 'a4bce9cef9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # カウンタ行は存在しなければ実行時に既存IDのmaxから遅延初期化されるため、
    # ここではテーブル作成のみ行う（既存データのシードは不要）。
    op.create_table(
        'id_counters',
        sa.Column('name', sa.String(length=30), nullable=False),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('id_counters')
