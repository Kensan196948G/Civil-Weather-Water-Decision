"""login attempt lockout ledger

Revision ID: g7c8d9e0f1a2
Revises: f6b7c8d9e0a1
Create Date: 2026-07-13 11:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'f6b7c8d9e0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'login_attempts',
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False),
        sa.Column('ip_hash', sa.String(length=64), nullable=False),
        sa.Column('fail_count', sa.Integer(), nullable=False),
        sa.Column('first_failed_at', sa.Float(), nullable=True),
        sa.Column('last_failed_at', sa.Float(), nullable=True),
        sa.Column('locked_until', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_index('ix_login_attempts_locked_until', 'login_attempts', ['locked_until'])
    op.create_index('ix_login_attempts_updated_at', 'login_attempts', ['updated_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_login_attempts_updated_at', table_name='login_attempts')
    op.drop_index('ix_login_attempts_locked_until', table_name='login_attempts')
    op.drop_table('login_attempts')
