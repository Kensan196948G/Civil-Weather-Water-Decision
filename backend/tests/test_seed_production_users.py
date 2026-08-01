"""production 起動時のユーザー同期テスト。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.security import verify_password
from app.models import User
from app import seed as seed_module


def test_sync_production_users_updates_admin_and_disables_demo_users(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(seed_module.settings, "admin_password", "new-production-password")
    with Session() as db:
        db.add(User(id="U01", username="admin", display_name="old", role="admin",
                    department="", email="", password_hash="old", is_active=True))
        db.add(User(id="U02", username="tanaka", display_name="demo", role="tech_manager",
                    department="", email="", password_hash="old", is_active=True))
        db.commit()

        seed_module._sync_production_users(db)
        db.commit()

        admin = db.get(User, "U01")
        demo = db.get(User, "U02")

        assert admin is not None
        assert admin.is_active is True
        assert verify_password("new-production-password", admin.password_hash)
        assert demo is not None
        assert demo.is_active is False


def test_seed_production_first_boot_does_not_duplicate_admin(monkeypatch):
    """本番設定・空DBでの初回 seed() 実行で admin が二重 add されないことを確認（#90 対抗レビュー critical-1）。

    seed() は関数冒頭で _sync_production_users() を呼んでから、WorkType 未投入
    （＝初回起動）なら全マスタデータを投入する。修正前はマスタ投入の末尾でも
    _sync_production_users() を再度呼んでいたため、同一主キー U01 の User が
    session 内に pending のまま2件でき、commit 時に IntegrityError となっていた。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # 本番の SessionLocal は autoflush=False（app/core/db.py）。既定の autoflush=True
    # のままだと pending な admin が db.get() の直前に自動 flush されてしまい、
    # このテストが検出すべき二重 add バグを再現できない。
    Session = sessionmaker(bind=engine, autoflush=False)

    monkeypatch.setattr(seed_module.settings, "app_env", "production")
    monkeypatch.setattr(seed_module.settings, "admin_password", "prod-boot-password")
    with Session() as db:
        seed_module.seed(db)  # IntegrityError を投げずに完走することを確認

        admin = db.get(User, "U01")
        assert admin is not None
        assert admin.is_active is True
        assert verify_password("prod-boot-password", admin.password_hash)
        # 本番はデモユーザー(tanaka等)を作成しない
        assert db.get(User, "U02") is None
