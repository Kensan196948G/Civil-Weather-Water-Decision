"""認証・認可の依存（FastAPI Depends）。設計§12.2 RBAC。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .security import decode_token
from ..models import User


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not settings.enable_auth:
        # 認証無効時はダミー管理者（開発・テスト用）
        return User(id="_dev", username="_dev", display_name="dev", email="", role="admin",
                    department="", is_active=True)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "authentication required")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(401, "invalid or expired token")
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(401, "user not found or inactive")
    return user


def require_role(*roles: str):
    """指定ロールのみ許可する依存を生成。"""
    def checker(user: User = Depends(get_current_user)) -> User:
        # 空 roles は「全拒否」（誤って Depends(require_role()) と書いた時の安全側）#7
        if not roles or user.role not in roles:
            raise HTTPException(403, "forbidden: requires one of " + ",".join(roles or ["(none)"]))
        return user
    return checker
