"""notification delivery ledger

Revision ID: f2a4d6c8e9b1
Revises: g7c8d9e0f1a2
Create Date: 2026-07-13 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a4d6c8e9b1'
down_revision: Union[str, Sequence[str], None] = 'g7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notification_deliveries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('channel', sa.String(length=30), nullable=False),
        sa.Column('fingerprint', sa.String(length=255), nullable=False),
        sa.Column('notification_id', sa.String(length=100), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('last_sent_at', sa.Float(), nullable=True),
        sa.Column('last_attempt_at', sa.Float(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.Column('updated_at', sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel', 'fingerprint',
                            name='uq_notification_delivery_channel_fingerprint'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('notification_deliveries')
