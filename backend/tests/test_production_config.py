"""本番起動ガードの回帰テスト。"""

import pytest

from app.core.config import Settings


def _valid_production_settings(**overrides):
    values = {
        "app_env": "production",
        "enable_auth": True,
        "jwt_secret": "production-jwt-secret-32bytes-minimum-value",
        "settings_encryption_key": "production-settings-encryption-key-32bytes-minimum-value",
        "admin_password": "production-admin-password",
        "database_url": "postgresql+psycopg2://user:pass@db.example.com:5432/cwwd",
        "cors_origins": "https://cwwd.mirai-dx-platform.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_config_accepts_hardened_settings():
    settings = _valid_production_settings()

    assert settings.app_env == "production"
    assert settings.cors_origins == "https://cwwd.mirai-dx-platform.com"


def test_production_config_accepts_multiple_https_origins_with_ports():
    settings = _valid_production_settings(
        cors_origins="https://cwwd.mirai-dx-platform.com,https://ops.example.com:8443"
    )

    assert "ops.example.com:8443" in settings.cors_origins


def test_production_config_rejects_jwt_secret_fallback_for_settings_encryption():
    with pytest.raises(RuntimeError):
        _valid_production_settings(
            jwt_secret="production-jwt-secret-32bytes-minimum-value",
            settings_encryption_key="",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_password", ""),
        ("settings_encryption_key", ""),
        ("settings_encryption_key", "short-settings-key"),
        ("database_url", "sqlite:///./civil_weather_water.db"),
        ("database_url", "mysql+pymysql://user:pass@db.example.com:3306/cwwd"),
        ("database_url", "not-a-url"),
        ("cors_origins", "*"),
        ("cors_origins", "http://cwwd.mirai-dx-platform.com"),
        ("cors_origins", "https://"),
        ("cors_origins", "https:example.com"),
        ("cors_origins", "https://cwwd.mirai-dx-platform.com/"),
        ("cors_origins", "https://cwwd.mirai-dx-platform.com/path"),
        ("cors_origins", "https://cwwd.mirai-dx-platform.com?x=1"),
        ("cors_origins", "https://user:pass@cwwd.mirai-dx-platform.com"),
        ("cors_origins", "https://192.0.2.10"),
        ("cors_origins", "https://[2001:db8::1]"),
    ],
)
def test_production_config_rejects_unsafe_operational_defaults(field, value):
    with pytest.raises(RuntimeError):
        _valid_production_settings(**{field: value})
