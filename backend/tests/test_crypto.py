"""設定値暗号化ヘルパーの回帰テスト。"""

from app.core import crypto
from app.core.config import Settings


def test_decrypt_accepts_previous_key_during_rotation(monkeypatch):
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    secret = "sk-ant-rotating-secret-1234"

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    token = crypto.encrypt(secret)

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    assert crypto.decrypt(token) is None

    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    assert crypto.decrypt(token) == secret


def test_encrypt_uses_current_key_not_previous_key(monkeypatch):
    old_key = "old-settings-encryption-key-32bytes-minimum-value"
    new_key = "new-settings-encryption-key-32bytes-minimum-value"
    secret = "sk-ant-current-key-secret-5678"

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", new_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", old_key)
    token = crypto.encrypt(secret)
    assert crypto.decrypt(token) == secret

    monkeypatch.setattr(crypto.settings, "settings_encryption_key", old_key)
    monkeypatch.setattr(crypto.settings, "settings_encryption_previous_keys", "")
    assert crypto.decrypt(token) is None


def test_production_rejects_short_previous_settings_encryption_key():
    base = dict(
        app_env="production",
        enable_auth=True,
        jwt_secret="production-jwt-secret-32bytes-minimum-value",
        settings_encryption_key="production-settings-encryption-key-32bytes-minimum-value",
        admin_password="production-admin-password",
        database_url="postgresql+psycopg2://user:pass@db.example.com:5432/cwwd",
        cors_origins="https://cwwd.mirai-dx-platform.com",
    )

    try:
        Settings(**base, settings_encryption_previous_keys="short-old-key")
    except RuntimeError:
        pass
    else:
        raise AssertionError("short previous encryption key should be rejected in production")

    settings = Settings(
        **base,
        settings_encryption_previous_keys="previous-settings-encryption-key-32bytes-minimum-value",
    )
    assert "previous-settings" in settings.settings_encryption_previous_keys


def test_previous_settings_encryption_keys_reject_any_short_entry():
    base = dict(
        app_env="local",
        database_url="sqlite:///./_test_cw.db",
        settings_encryption_previous_keys=(
            "previous-settings-encryption-key-32bytes-minimum-value,short"
        ),
    )

    try:
        Settings(**base)
    except RuntimeError:
        pass
    else:
        raise AssertionError("any short previous encryption key entry should be rejected")
