"""Re-encrypt app settings secrets with the current settings encryption key.

Usage:
  python -m app.tools.reencrypt_settings --dry-run
  python -m app.tools.reencrypt_settings --apply --actor ops

The command never prints secret material. It exists for SETTINGS_ENCRYPTION_KEY rotation:
configure the new key as SETTINGS_ENCRYPTION_KEY and the old key in
SETTINGS_ENCRYPTION_PREVIOUS_KEYS, verify with --dry-run, then run --apply.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core import crypto
from ..core.db import SessionLocal
from ..models import AppSetting
from ..services.audit import audit_add

_AI_KEY = "ai_api_key"
_JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class ReencryptResult:
    status: str
    changed: bool
    message: str


def reencrypt_ai_key(db: Session, *, apply: bool, actor: str = "ops") -> ReencryptResult:
    """Re-encrypt the stored AI API key with the current key.

    The value is first decrypted through crypto.decrypt(), which already tries the
    current key followed by SETTINGS_ENCRYPTION_PREVIOUS_KEYS. With apply=False this
    only proves whether re-encryption is possible. With apply=True it writes a new
    ciphertext and an audit row in the same transaction.
    """
    row = db.get(AppSetting, _AI_KEY)
    if row is None or not row.value:
        return ReencryptResult("missing", False, "ai_api_key is not configured")

    plaintext = crypto.decrypt(row.value)
    if plaintext is None:
        return ReencryptResult("undecryptable", False, "ai_api_key cannot be decrypted")

    if not apply:
        return ReencryptResult("dry-run", False, "ai_api_key can be re-encrypted")

    if not crypto.encryption_is_strong():
        return ReencryptResult("weak-key", False, "current settings encryption key is not strong")

    try:
        row.value = crypto.encrypt(plaintext)
        row.updated_at = datetime.now(_JST).strftime("%Y-%m-%d %H:%M:%S")
        row.updated_by = actor
        audit_add(
            db,
            SimpleNamespace(id=None, username=actor),
            "settings_reencrypt_ai_key",
            "ai_api_key re-encrypted with current settings key",
            component="ops",
        )
        db.commit()
    except (SQLAlchemyError, ValueError, TypeError):
        db.rollback()
        return ReencryptResult("error", False, "ai_api_key re-encryption failed")
    return ReencryptResult("reencrypted", True, "ai_api_key re-encrypted")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the re-encrypted value")
    mode.add_argument("--dry-run", action="store_true", help="check only (default)")
    parser.add_argument("--actor", default="ops", help="audit username for --apply")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with SessionLocal() as db:
        result = reencrypt_ai_key(db, apply=bool(args.apply), actor=args.actor)
    print(f"status={result.status} changed={str(result.changed).lower()} message={result.message}")
    return 2 if result.status in {"undecryptable", "weak-key", "error"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
