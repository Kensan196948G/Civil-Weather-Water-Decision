"""認証エンドポイント（ログイン / 自分情報）。設計§7.1 / §12。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import get_current_user
from ..core.security import create_access_token, verify_password
from ..models import User
from ..services.audit import audit

router = APIRouter()


class LoginReq(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == req.username))
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        audit(db, None, "login_failed", f"username={req.username}")
        raise HTTPException(401, "ユーザー名またはパスワードが正しくありません")
    token = create_access_token(user.id, user.role)
    audit(db, user, "login", "ログイン成功")
    return {
        "token": token,
        "user": {"id": user.id, "username": user.username,
                 "displayName": user.display_name, "role": user.role,
                 "department": user.department},
    }


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "displayName": user.display_name,
            "role": user.role, "department": user.department}
