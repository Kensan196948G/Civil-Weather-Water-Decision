"""app settings (key-value store, #80)

Revision ID: b8e1f0a2c3d4
Revises: e3d9c2b1a7f0
Create Date: 2026-07-12 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e1f0a2c3d4'
down_revision: Union[str, Sequence[str], None] = 'e3d9c2b1a7f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 設定画面の汎用 key-value ストア。1キー=1行で通知/データ保存期間/ユーザー設定/AI設定を保持。
    # AI APIキーは Fernet 暗号文字列を value に格納する（平文は保存しない）。初期データ投入は不要。
    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.String(length=40), nullable=False),
        sa.Column('updated_by', sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('app_settings')
