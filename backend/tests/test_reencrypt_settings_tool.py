"""Settings re-encryption management command tests."""

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import _DEFAULT_JWT_SECRET
from app.core import crypto
from app.core.db import SessionLocal
from app.models import AppSetting, AuditLog
from app.tools.reencrypt_settings import main, reencrypt_ai_key

_SECRET = "sk-ant-rotation-tool-secret-2468"


def _wipe():
    with SessionLocal() as db:
        db.execute(delete(AuditLog))
        db.execute(delete(AppSetting))
        db.commit()


def _store_ai_key_with_current_crypto(secret: str) -> str:
    token = crypto.encrypt(secret)
    with SessionLocal() as db:
        db.add(AppSetting(key="ai_api_key", value=token, updated_at="before", updated_by="admin"))
        db.commit()
    return token


def test_reencrypt_ai_key_dry_run_does_not_mutate_or_audit(client, monkeypatch):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    token = _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    with SessionLocal() as db:
        result = reencrypt_ai_key(db, apply=False, actor="ops-test")

    assert result.status == "dry-run"
    with SessionLocal() as db:
        row = db.get(AppSetting, "ai_api_key")
        assert row.value == token
        assert db.scalar(select(AuditLog).where(AuditLog.action == "settings_reencrypt_ai_key")) is None


def test_reencrypt_ai_key_apply_uses_current_key_and_writes_audit(client, monkeypatch):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    with SessionLocal() as db:
        result = reencrypt_ai_key(db, apply=True, actor="ops-test")

    assert result.status == "reencrypted"
    with SessionLocal() as db:
        row = db.get(AppSetting, "ai_api_key")
        token = row.value
        assert row.updated_at != "before"
        assert row.updated_by == "ops-test"
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "settings_reencrypt_ai_key"))
        assert audit is not None
        assert audit.component == "ops"
        assert audit.username == "ops-test"
        assert _SECRET not in audit.message

    assert crypto.decrypt(token) == _SECRET
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    assert crypto.decrypt(token) is None


def test_reencrypt_ai_key_undecryptable_does_not_mutate_or_audit(client, monkeypatch):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    wrong_old_key = "wrong-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    token = _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", wrong_old_key)
    with SessionLocal() as db:
        result = reencrypt_ai_key(db, apply=True, actor="ops-test")

    assert result.status == "undecryptable"
    with SessionLocal() as db:
        row = db.get(AppSetting, "ai_api_key")
        assert row.value == token
        assert db.scalar(select(AuditLog).where(AuditLog.action == "settings_reencrypt_ai_key")) is None


def test_reencrypt_ai_key_missing_is_noop(client):
    _wipe()

    with SessionLocal() as db:
        result = reencrypt_ai_key(db, apply=True, actor="ops-test")

    assert result.status == "missing"
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "settings_reencrypt_ai_key")) is None


def test_reencrypt_ai_key_rejects_weak_current_key(client, monkeypatch):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    token = _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", "")
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    monkeypatch.setattr(crypto.settings, "jwt_secret", _DEFAULT_JWT_SECRET)
    with SessionLocal() as db:
        result = reencrypt_ai_key(db, apply=True, actor="ops-test")

    assert result.status == "weak-key"
    with SessionLocal() as db:
        row = db.get(AppSetting, "ai_api_key")
        assert row.value == token
        assert row.updated_at == "before"
        assert db.scalar(select(AuditLog).where(AuditLog.action == "settings_reencrypt_ai_key")) is None


def test_reencrypt_ai_key_commit_failure_rolls_back(client, monkeypatch):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    token = _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    with SessionLocal() as db:
        def fail_commit():
            raise SQLAlchemyError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        result = reencrypt_ai_key(db, apply=True, actor="ops-test")

    assert result.status == "error"
    with SessionLocal() as db:
        row = db.get(AppSetting, "ai_api_key")
        assert row.value == token
        assert row.updated_at == "before"
        assert db.scalar(select(AuditLog).where(AuditLog.action == "settings_reencrypt_ai_key")) is None


def test_reencrypt_settings_cli_output_does_not_include_secret(client, monkeypatch, capsys):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "status=dry-run" in out
    assert _SECRET not in out


def test_reencrypt_settings_cli_apply_output_does_not_include_secret_or_ciphertext(
    client, monkeypatch, capsys
):
    _wipe()
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    old_token = _store_ai_key_with_current_crypto(_SECRET)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    assert main(["--apply", "--actor", "ops-test"]) == 0
    out = capsys.readouterr().out
    assert "status=reencrypted" in out
    assert _SECRET not in out
    assert _SECRET[-4:] not in out
    assert old_token not in out
