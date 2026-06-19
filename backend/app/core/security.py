"""認証ユーティリティ（パスワードハッシュ＝bcrypt / トークン＝JWT）。設計§12。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(sub: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub, "role": role, "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception:
        return None
