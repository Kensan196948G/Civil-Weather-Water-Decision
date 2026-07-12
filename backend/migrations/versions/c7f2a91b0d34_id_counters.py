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
    op.create_table(
        'id_counters',
        sa.Column('name', sa.String(length=30), nullable=False),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )
    # 稼働中DBの既存IDからカウンタを初期化（対抗レビュー: カウンタが実テーブルより
    # 遅れていると採番が既存IDに衝突し続けるため、移行時点のmaxを起点にする）
    bind = op.get_bind()
    for table, prefix in (('sites', 'S'), ('decision_results', 'DR'),
                          ('work_plans', 'WP'), ('decision_logs', 'L')):
        ids = [r[0] for r in bind.execute(sa.text(f'SELECT id FROM {table}'))]  # noqa: S608 - 固定テーブル名
        nums = [int(x[len(prefix):]) for x in ids
                if isinstance(x, str) and x.startswith(prefix) and x[len(prefix):].isdigit()]
        bind.execute(
            sa.text('INSERT INTO id_counters (name, value) VALUES (:n, :v)'),
            {'n': prefix, 'v': max(nums) if nums else 0},
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('id_counters')
