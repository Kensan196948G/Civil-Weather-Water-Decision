"""revoked token ledger

Revision ID: f6b7c8d9e0a1
Revises: f2a4d6c8e9b1
Create Date: 2026-07-13 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6b7c8d9e0a1'
down_revision: Union[str, Sequence[str], None] = 'f2a4d6c8e9b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'revoked_tokens',
        sa.Column('jti', sa.String(length=80), nullable=False),
        sa.Column('user_id', sa.String(length=20), nullable=False),
        sa.Column('revoked_at', sa.Float(), nullable=False),
        sa.Column('expires_at', sa.Float(), nullable=False),
        sa.Column('reason', sa.String(length=80), nullable=False),
        sa.PrimaryKeyConstraint('jti'),
    )
    op.create_index('ix_revoked_tokens_expires_at', 'revoked_tokens', ['expires_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_revoked_tokens_expires_at', table_name='revoked_tokens')
    op.drop_table('revoked_tokens')
