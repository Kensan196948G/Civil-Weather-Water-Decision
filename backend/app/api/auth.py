"""認証エンドポイント（ログイン / 自分情報）。設計§7.1 / §12。"""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import get_current_user, get_token_payload
from ..core.security import DUMMY_HASH, create_access_token, verify_password
from ..models import LoginAttempt, RevokedToken, User
from ..services.audit import audit_add

router = APIRouter()

# ログイン試行制限（対抗レビュー #4）。DB永続台帳で複数プロセス/再起動をまたいで共有する。
_LOCK_THRESHOLD = 5
_LOCK_SECONDS = 300
_LOCK_WINDOW_SECONDS = 300
_LOGIN_ATTEMPT_RETENTION_SECONDS = 86_400
_TRUSTED_PROXY_PEERS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    if peer in _TRUSTED_PROXY_PEERS:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return peer


def _login_attempt_key(username: str, ip: str) -> tuple[str, str, str]:
    normalized_username = username.strip().casefold()
    ip_hash = sha256(ip.encode("utf-8")).hexdigest()
    key = sha256(f"{normalized_username}|{ip}".encode("utf-8")).hexdigest()
    return key, normalized_username, ip_hash


def _attempt_row(db: Session, key: str) -> LoginAttempt | None:
    return db.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())


def _prune_old_attempts(db: Session, now: float) -> None:
    cutoff = now - _LOGIN_ATTEMPT_RETENTION_SECONDS
    db.query(LoginAttempt).filter(LoginAttempt.updated_at < cutoff).delete()


def _is_locked(db: Session, key: str, now: float) -> bool:
    row = _attempt_row(db, key)
    return bool(row and row.locked_until and row.locked_until > now)


def _record_fail(db: Session, key: str, username: str, ip_hash: str, now: float) -> None:
    row = _attempt_row(db, key)
    if row is None:
        db.add(LoginAttempt(
            key=key, username=username[:100], ip_hash=ip_hash, fail_count=1,
            first_failed_at=now, last_failed_at=now, locked_until=None, updated_at=now,
        ))
        return
    if row.locked_until and row.locked_until > now:
        row.updated_at = now
        return
    if row.first_failed_at is None or row.first_failed_at < now - _LOCK_WINDOW_SECONDS:
        row.fail_count = 1
        row.first_failed_at = now
        row.locked_until = None
    else:
        row.fail_count += 1
    row.username = username[:100]
    row.ip_hash = ip_hash
    row.last_failed_at = now
    row.updated_at = now
    if row.fail_count >= _LOCK_THRESHOLD:
        row.locked_until = now + _LOCK_SECONDS


def _clear_fail(db: Session, key: str) -> None:
    row = _attempt_row(db, key)
    if row is not None:
        db.delete(row)


class LoginReq(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@router.post("/auth/login")
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)):
    now = _now_ts()
    _prune_old_attempts(db, now)
    key, normalized_username, ip_hash = _login_attempt_key(req.username, _client_ip(request))
    if _is_locked(db, key, now):
        audit_add(db, None, "login_locked", f"username={req.username}")
        db.commit()
        raise HTTPException(429, "試行回数が多すぎます。しばらくしてから再試行してください")
    user = db.scalar(select(User).where(User.username == req.username))
    # 常に bcrypt 検証を走らせタイミングを一定化（ユーザー列挙対策 #5）
    if user and user.is_active:
        valid = verify_password(req.password, user.password_hash)
    else:
        verify_password(req.password, DUMMY_HASH)
        valid = False
    if not valid:
        # 同一 (username, ip) キーへの初回失敗が並行して複数届くと、_attempt_row が揃って
        # 「行なし」と判定し INSERT が競合し得る。1回のリトライだけでは3並行以上で再度
        # 競合し得るため、最大2回まで試し、それでも競合するなら記録を諦めて401を返す
        # （試行制限の記録より401応答の確実な返却を優先。対抗レビュー #90 high-2）。
        for _ in range(2):
            _record_fail(db, key, normalized_username, ip_hash, _now_ts())
            audit_add(db, None, "login_failed", f"username={req.username}")
            try:
                db.commit()
                break
            except IntegrityError:
                db.rollback()
        raise HTTPException(401, "ユーザー名またはパスワードが正しくありません")
    _clear_fail(db, key)
    token = create_access_token(user.id, user.role)
    audit_add(db, user, "login", "ログイン成功")
    db.commit()
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


@router.post("/auth/logout")
def logout(payload: dict = Depends(get_token_payload),
           db: Session = Depends(get_db)):
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(400, "token has no revocation id")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, "invalid token")
    exp = payload.get("exp")
    if isinstance(exp, datetime):
        expires_at = exp.timestamp()
    else:
        expires_at = float(exp or 0)
    now = datetime.now(timezone.utc).timestamp()
    db.query(RevokedToken).filter(RevokedToken.expires_at < now).delete()
    user = db.get(User, sub)
    if db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti, user_id=str(sub), revoked_at=now,
                            expires_at=expires_at, reason="logout"))
        audit_add(db, user, "logout", "トークン失効")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"revoked": True}
