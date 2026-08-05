"""Entra ID OIDC 認証エンドポイント（#118）。

- GET  /api/auth/oidc/status     … OIDC 有効/無効（UI判定用）
- GET  /api/auth/oidc/authorize  … Entra ログインへリダイレクト（PKCE+state/nonce）
- GET  /api/auth/oidc/callback   … コード交換・id_token検証・アプリJWT発行
- POST /api/auth/oidc/logout     … アプリJWT失効＋Entra ログアウトURL

state/nonce/code_verifier はアプリJWT秘密鍵で署名した HttpOnly クッキーで運搬し、
callback で検証する（CSRF・リプレイ防止）。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import get_token_payload
from ..models import RevokedToken, User
from ..services import oidc
from ..services.audit import audit_add

router = APIRouter()


def _redirect_ok(redirect: str) -> str:
    redirect = redirect or "/"
    if not redirect.startswith("/") or redirect.startswith("//"):
        raise HTTPException(400, "redirect は同一オリジンの相対パスのみ許可します")
    return redirect


def _require_enabled() -> None:
    if not oidc.enabled():
        raise HTTPException(404, "OIDC is not enabled")


@router.get("/auth/oidc/status")
def oidc_status():
    return {"enabled": oidc.enabled(), "mode": settings.auth_mode,
            "clientId": settings.oidc_client_id if oidc.enabled() else None}


@router.get("/auth/oidc/authorize")
async def oidc_authorize(redirect: str = "/"):
    _require_enabled()
    redirect = _redirect_ok(redirect)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = oidc.generate_pkce()
    async with httpx.AsyncClient() as client:
        url = await oidc.authorize_url(
            state, nonce, challenge, settings.oidc_redirect_path, client)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "oidc_state", oidc.make_state_token(state, nonce, verifier, redirect),
        max_age=oidc.STATE_TTL_SECONDS, httponly=True, samesite="lax",
        secure=settings.app_env != "local", path="/")
    return response


@router.get("/auth/oidc/callback")
async def oidc_callback(code: str, state: str, request: Request,
                        db: Session = Depends(get_db)):
    _require_enabled()
    state_token = request.cookies.get("oidc_state")
    if not state_token:
        raise HTTPException(400, "OIDC state クッキーがありません（再ログインしてください）")
    payload = oidc.read_state_token(state_token)
    if payload["state"] != state:
        raise HTTPException(403, "OIDC state が一致しません")
    async with httpx.AsyncClient() as client:
        tokens = await oidc.exchange_code(
            code, payload["code_verifier"], settings.oidc_redirect_path, client)
        claims = await oidc.verify_id_token(
            tokens["id_token"], payload["nonce"], client)
    email = claims.get("email") or claims.get("preferred_username")
    user = oidc.provision_user(
        db, email, claims.get("name") or "", claims.get("groups") or [])
    audit_add(db, user, "oidc_login_success",
              f"email={email} role={user.role}")
    db.commit()
    app_token = oidc.issue_app_token(user)
    redirect = _redirect_ok(payload.get("redirect") or "/")
    response = RedirectResponse(f"{redirect}#oidc_token={app_token}",
                                status_code=302)
    response.delete_cookie("oidc_state", path="/")
    return response


@router.post("/auth/oidc/logout")
async def oidc_logout(payload: dict = Depends(get_token_payload),
                      db: Session = Depends(get_db)):
    _require_enabled()
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(400, "token has no revocation id")
    now = datetime.now(timezone.utc).timestamp()
    exp = payload.get("exp")
    expires_at = exp.timestamp() if isinstance(exp, datetime) else float(exp or 0)
    db.query(RevokedToken).filter(RevokedToken.expires_at < now).delete()
    user = db.get(User, payload.get("sub"))
    if db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti, user_id=str(payload.get("sub")),
                            revoked_at=now, expires_at=expires_at, reason="oidc_logout"))
        audit_add(db, user, "oidc_logout", "トークン失効")
    db.commit()
    end_url = None
    try:
        async with httpx.AsyncClient() as client:
            end_url = await oidc.end_session_url(client)
    except HTTPException:
        end_url = None
    return {"revoked": True, "endSessionUrl": end_url}
