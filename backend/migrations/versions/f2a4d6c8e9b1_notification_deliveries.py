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
    bind = op.get_bind()
    if bind.dialect.has_table(bind, "notification_deliveries"):
        # 本番では create_all（init_db）由来で既に存在し得る（2026-08-01 実機確認）。
        # alembic は upgrade 成功後に revision を記録するため、スキップでも履歴は整合する。
        # スキーマ整合の最低担保として一意制約だけ確認・補完する。
        inspector = sa.inspect(bind)
        # SQLite は名前付きテーブル制約を inspector に返さないため、
        # 一意制約（constraint）と一意インデックスの両方を検出対象にする。
        unique_names = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("notification_deliveries")
        }
        unique_names.update(
            index["name"]
            for index in inspector.get_indexes("notification_deliveries")
            if index.get("unique")
        )
        if "uq_notification_delivery_channel_fingerprint" not in unique_names:
            op.create_unique_constraint(
                "uq_notification_delivery_channel_fingerprint",
                "notification_deliveries",
                ["channel", "fingerprint"],
            )
        return
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
    if op.get_bind().dialect.has_table(op.get_bind(), "notification_deliveries"):
        op.drop_table('notification_deliveries')
