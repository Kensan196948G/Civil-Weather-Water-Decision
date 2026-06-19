"""Alembic 環境（アプリの Base.metadata / settings.database_url に配線）。

SQLite と PostgreSQL の双方で同一マイグレーションを流せるよう、
- URL は app の settings から取得（engine 経由）
- SQLite の ALTER 制約回避のため render_as_batch=True
"""
from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (副作用で全モデルを Base.metadata に登録)
from app.core.db import Base, engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
