"""認証ユーティリティ（パスワードハッシュ＝bcrypt / トークン＝JWT）。設計§12。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings


_BCRYPT_MAX = 72  # bcrypt は72バイト超で例外。明示的に丸める。


def _pw_bytes(pw: str) -> bytes:
    return pw.encode("utf-8")[:_BCRYPT_MAX]


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(_pw_bytes(pw), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(pw), hashed.encode("utf-8"))
    except Exception:
        return False


# 存在しない/無効ユーザーでも bcrypt 検証を走らせ、ログインのタイミング差で
# ユーザー名を列挙されないようにするためのダミーハッシュ（対抗レビュー #5）。
DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


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
