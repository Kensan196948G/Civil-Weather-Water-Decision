"""認証エンドポイント（ログイン / 自分情報）。設計§7.1 / §12。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import get_current_user
from ..core.security import DUMMY_HASH, create_access_token, verify_password
from ..models import User
from ..services.audit import audit

router = APIRouter()

# ログイン試行制限（対抗レビュー #4）。PoCはプロセス内メモリ。本番はRedis等へ。
_LOCK_THRESHOLD = 5
_LOCK_SECONDS = 300
_fails: dict[str, dict] = {}


def _is_locked(key: str) -> bool:
    rec = _fails.get(key)
    return bool(rec and rec.get("until", 0) > time.monotonic())


def _record_fail(key: str) -> None:
    rec = _fails.setdefault(key, {"n": 0, "until": 0.0})
    rec["n"] += 1
    if rec["n"] >= _LOCK_THRESHOLD:
        rec["until"] = time.monotonic() + _LOCK_SECONDS


def _clear_fail(key: str) -> None:
    _fails.pop(key, None)


class LoginReq(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@router.post("/auth/login")
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    key = req.username + "|" + ip
    if _is_locked(key):
        audit(db, None, "login_locked", f"username={req.username}")
        raise HTTPException(429, "試行回数が多すぎます。しばらくしてから再試行してください")
    user = db.scalar(select(User).where(User.username == req.username))
    # 常に bcrypt 検証を走らせタイミングを一定化（ユーザー列挙対策 #5）
    if user and user.is_active:
        valid = verify_password(req.password, user.password_hash)
    else:
        verify_password(req.password, DUMMY_HASH)
        valid = False
    if not valid:
        _record_fail(key)
        audit(db, None, "login_failed", f"username={req.username}")
        raise HTTPException(401, "ユーザー名またはパスワードが正しくありません")
    _clear_fail(key)
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
