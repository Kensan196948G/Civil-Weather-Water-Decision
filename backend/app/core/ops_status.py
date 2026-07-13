"""Authenticated operations status snapshot access.

The snapshot is produced by deploy/scripts/ops-status-json-export.sh and is
intended to contain only whitelisted systemd state.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

_MAX_SNAPSHOT_BYTES = 256 * 1024
_TOP_LEVEL_KEYS = {"snapshot_utc", "services", "timers", "failed_units", "failed_units_count", "error", "status"}
_SERVICE_KEYS = {"unit", "active_state", "result", "n_restarts"}
_TIMER_KEYS = {"unit", "active_state", "unit_file_state", "last_trigger", "next_elapse", "service_result"}
_ERROR_KEYS = {"message", "unit", "property", "actual", "expected"}


class OpsStatusSnapshotError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def load_ops_status_snapshot() -> dict[str, Any]:
    path = Path(settings.ops_status_json_path)
    max_age_seconds = int(settings.ops_status_json_max_age_seconds)
    if max_age_seconds <= 0:
        raise OpsStatusSnapshotError("invalid_max_age")
    if not path.is_absolute():
        raise OpsStatusSnapshotError("invalid_path")
    if not path.exists():
        raise OpsStatusSnapshotError("missing")
    if not path.is_file():
        raise OpsStatusSnapshotError("not_regular_file")

    stat = path.stat()
    if stat.st_size > _MAX_SNAPSHOT_BYTES:
        raise OpsStatusSnapshotError("too_large")
    now = time.time()
    age_seconds = int(now - stat.st_mtime)
    if age_seconds < -60:
        raise OpsStatusSnapshotError("mtime_in_future")
    if age_seconds > max_age_seconds:
        raise OpsStatusSnapshotError("stale")

    try:
        with path.open("r", encoding="utf-8") as f:
            snapshot = json.load(f)
    except Exception as exc:
        raise OpsStatusSnapshotError("invalid_json") from exc

    snapshot = _sanitize_snapshot(snapshot)
    _validate_snapshot_shape(snapshot)
    return {
        "status": "ok",
        "snapshot": snapshot,
        "metadata": {
            "age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "size_bytes": stat.st_size,
        },
    }


def _validate_snapshot_shape(snapshot: Any) -> None:
    if not isinstance(snapshot, dict):
        raise OpsStatusSnapshotError("invalid_shape")
    if not isinstance(snapshot.get("status"), str):
        raise OpsStatusSnapshotError("missing_status")
    if not isinstance(snapshot.get("services"), list):
        raise OpsStatusSnapshotError("missing_services")
    if not isinstance(snapshot.get("timers"), list):
        raise OpsStatusSnapshotError("missing_timers")
    if not isinstance(snapshot.get("failed_units"), list):
        raise OpsStatusSnapshotError("missing_failed_units")
    if not isinstance(snapshot.get("failed_units_count"), int):
        raise OpsStatusSnapshotError("missing_failed_units_count")


def _sanitize_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise OpsStatusSnapshotError("invalid_shape")
    sanitized = {key: snapshot[key] for key in _TOP_LEVEL_KEYS if key in snapshot}
    if isinstance(sanitized.get("services"), list):
        sanitized["services"] = [
            {key: item[key] for key in _SERVICE_KEYS if key in item}
            for item in sanitized["services"] if isinstance(item, dict)
        ]
    if isinstance(sanitized.get("timers"), list):
        sanitized["timers"] = [
            {key: item[key] for key in _TIMER_KEYS if key in item}
            for item in sanitized["timers"] if isinstance(item, dict)
        ]
    if isinstance(sanitized.get("failed_units"), list):
        sanitized["failed_units"] = [
            item for item in sanitized["failed_units"] if isinstance(item, str)
        ]
    if isinstance(sanitized.get("error"), dict):
        error = sanitized["error"]
        sanitized["error"] = {key: error[key] for key in _ERROR_KEYS if key in error}
    return sanitized
