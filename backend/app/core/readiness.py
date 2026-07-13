"""Readiness checks for production monitoring.

The public response intentionally avoids connection strings and secret values.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from .config import settings
from .db import engine
from ..models import AppSetting, AuditLog, Site, User

logger = logging.getLogger("cwwd.readiness")
_TABLE_PROBES = (
    select(Site.id).limit(1),
    select(User.id).limit(1),
    select(AuditLog.id).limit(1),
    select(AppSetting.key).limit(1),
)


def _alembic_config() -> Config:
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    return cfg


def check_readiness() -> dict[str, Any]:
    """Public sanitized readiness: booleans only."""
    return _check_readiness(include_details=False)


def check_readiness_detail() -> dict[str, Any]:
    """Authenticated ops readiness with sanitized revision/table detail."""
    return _check_readiness(include_details=True)


def _check_readiness(*, include_details: bool) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "database": False,
        "migrations": False,
        "tables": False,
        "config": True,
    }
    details: dict[str, Any] = {}
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text("SELECT 1"))
                checks["database"] = True
                if include_details:
                    details["database"] = {"dialect": conn.dialect.name}
            except Exception:
                logger.exception("readiness database check failed")
                return _payload(checks, details if include_details else None)

            try:
                cfg = _alembic_config()
                script = ScriptDirectory.from_config(cfg)
                heads = set(script.get_heads())
                current = set(MigrationContext.configure(conn).get_current_heads())
                checks["migrations"] = current == heads
                if include_details:
                    details["migrations"] = {
                        "current": sorted(current),
                        "head": sorted(heads),
                    }
                if not checks["migrations"]:
                    logger.warning("readiness migration mismatch: current=%s head=%s",
                                   sorted(current), sorted(heads))
            except Exception:
                logger.exception("readiness migration check failed")
                if include_details:
                    details["migrations"] = {"error": "check_failed"}

            try:
                table_details = []
                for stmt in _TABLE_PROBES:
                    conn.execute(stmt).first()
                    if include_details:
                        table_details.append({"probe": str(stmt.selected_columns[0]), "ok": True})
                checks["tables"] = True
                if include_details:
                    details["tables"] = table_details
            except Exception:
                logger.exception("readiness table probe failed")
                if include_details:
                    details["tables"] = {"error": "check_failed"}
    except Exception:
        logger.exception("readiness connection setup failed")
    return _payload(checks, details if include_details else None)


def _payload(checks: dict[str, bool], details: dict[str, Any] | None = None) -> dict[str, Any]:
    ok = all(checks.values())
    payload = {
        "status": "ok" if ok else "not_ready",
        "app": settings.app_name,
        "env": settings.app_env,
        "checks": checks,
    }
    if details is not None:
        payload["details"] = details
    return payload
