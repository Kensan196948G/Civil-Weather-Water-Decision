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
