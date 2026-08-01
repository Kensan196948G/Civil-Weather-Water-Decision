"""notification_deliveries migration の冪等性回帰テスト。

本番では init_db の create_all によって migration 適用前にテーブルが存在し得る
（2026-08-01 実機確認）。f2a4d6c8e9b1 は既存テーブルを壊さずにスキップし、
alembic revision だけを進める必要がある。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_HEAD = "f2a4d6c8e9b1"


def _run_alembic(database_url: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["APP_ENV"] = "local"
    env["ENABLE_SCHEDULER"] = "false"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
    )


def _create_existing_table(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE notification_deliveries (
                id INTEGER NOT NULL PRIMARY KEY,
                channel VARCHAR(30) NOT NULL,
                fingerprint VARCHAR(255) NOT NULL,
                notification_id VARCHAR(100) NOT NULL,
                severity INTEGER NOT NULL,
                status VARCHAR(30) NOT NULL,
                last_sent_at FLOAT,
                last_attempt_at FLOAT,
                last_error TEXT NOT NULL,
                created_at VARCHAR(40) NOT NULL,
                updated_at VARCHAR(40) NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX uq_notification_delivery_channel_fingerprint "
            "ON notification_deliveries (channel, fingerprint)"
        )
        conn.execute(
            "INSERT INTO notification_deliveries "
            "(channel, fingerprint, notification_id, severity, status, last_error, created_at, updated_at) "
            "VALUES ('log', 'pre-existing', 'pre', 2, 'logged', '', 't', 't')"
        )
        conn.commit()
    finally:
        conn.close()


def test_upgrade_skips_pre_existing_table(tmp_path):
    db = tmp_path / "existing.db"
    _create_existing_table(db)

    result = _run_alembic(f"sqlite:///{db}")
    assert result.returncode == 0, result.stderr.decode(errors="replace")

    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version == (MIGRATION_HEAD,)
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notification_deliveries'"
        ).fetchone()
        assert table is not None
        rows = conn.execute(
            "SELECT channel, fingerprint, status FROM notification_deliveries"
        ).fetchall()
        assert rows == [("log", "pre-existing", "logged")], "既存行は保持されること"
    finally:
        conn.close()


def test_upgrade_creates_table_on_fresh_database(tmp_path):
    db = tmp_path / "fresh.db"

    result = _run_alembic(f"sqlite:///{db}")
    assert result.returncode == 0, result.stderr.decode(errors="replace")

    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version == (MIGRATION_HEAD,)
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notification_deliveries'"
        ).fetchone()
        assert table is not None
    finally:
        conn.close()


def test_upgrade_is_idempotent_when_run_twice(tmp_path):
    db = tmp_path / "twice.db"
    _create_existing_table(db)

    first = _run_alembic(f"sqlite:///{db}")
    assert first.returncode == 0, first.stderr.decode(errors="replace")
    second = _run_alembic(f"sqlite:///{db}")
    assert second.returncode == 0, second.stderr.decode(errors="replace")

    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version == (MIGRATION_HEAD,)
    finally:
        conn.close()
