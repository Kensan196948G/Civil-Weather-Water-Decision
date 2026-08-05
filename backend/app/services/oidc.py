"""Entra ID OIDC サービス（#118: Authorization Code + PKCE）。

- OIDC discovery（issuer / authorization_endpoint / token_endpoint / jwks_uri）
- PKCE (S256) による認可コード交換
- id_token の issuer / audience / exp / 署名（JWKS） / nonce 検証
- Entra グループ → アプリロールのマッピングと auto-provision
- state / nonce はアプリJWT秘密鍵で署名した HttpOnly クッキーで運搬（CSRF・リプレイ防止）
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import create_access_token
from ..models import User

STATE_TTL_SECONDS = 300
_DISCOVERY_TTL = 3600.0
_discovery: dict | None = None
_discovery_at = 0.0

_ROLE_ORDER = ("admin", "tech_manager", "site_manager", "safety", "viewer")


def enabled() -> bool:
    return settings.auth_mode == "oidc"


def _role_groups() -> list[tuple[str, set[str]]]:
    """設定されたグループ→ロール対応（カンマ区切り）を優先順で返す。"""
    out = []
    for role in _ROLE_ORDER:
        raw = getattr(settings, f"oidc_group_role_{role}", "")
        groups = {g.strip() for g in raw.split(",") if g.strip()}
        if groups:
            out.append((role, groups))
    return out


def map_groups_to_role(groups: list[str]) -> str:
    """Entra グループからアプリロールを決める。未一致なら viewer（安全側）。"""
    group_set = set(groups or [])
    for role, candidates in _role_groups():
        if group_set & candidates:
            return role
    return "viewer"


def generate_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) を生成（S256）。"""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def make_state_token(state: str, nonce: str, code_verifier: str,
                     redirect: str = "/") -> str:
    """state/nonce をアプリJWT秘密鍵で署名（クッキー用）。"""
    now = int(time.time())
    return pyjwt.encode(
        {"state": state, "nonce": nonce, "code_verifier": code_verifier,
         "redirect": redirect, "iat": now, "exp": now + STATE_TTL_SECONDS},
        settings.jwt_secret, algorithm="HS256")


def read_state_token(token: str) -> dict:
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError as exc:
        raise HTTPException(400, "OIDC state の検証に失敗しました（再ログインしてください）") from exc
    if not payload.get("state") or not payload.get("nonce") or not payload.get("code_verifier"):
        raise HTTPException(400, "OIDC state の形式が不正です")
    return payload


async def discovery(client: httpx.AsyncClient) -> dict:
    global _discovery, _discovery_at
    now = time.monotonic()
    if _discovery and now - _discovery_at < _DISCOVERY_TTL:
        return _discovery
    if not settings.oidc_issuer_url:
        raise HTTPException(503, "OIDC_ISSUER_URL が未設定です")
    url = settings.oidc_issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = await client.get(url, timeout=settings.data_fetch_timeout_seconds)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 外部障害は隠さず503
        raise HTTPException(503, f"OIDC discovery の取得に失敗しました: {exc}") from exc
    try:
        meta = resp.json()
        required = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
        if any(not meta.get(k) for k in required):
            raise ValueError("required fields missing")
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "OIDC discovery の応答形式が不正です") from exc
    _discovery, _discovery_at = meta, now
    return meta


async def authorize_url(state: str, nonce: str, code_challenge: str,
                        redirect_uri: str, client: httpx.AsyncClient) -> str:
    meta = await discovery(client)
    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": settings.oidc_scopes,
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str, redirect_uri: str,
                        client: httpx.AsyncClient) -> dict:
    meta = await discovery(client)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "code_verifier": code_verifier,
    }
    try:
        resp = await client.post(
            meta["token_endpoint"], data=data,
            timeout=settings.data_fetch_timeout_seconds)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "OIDC トークン交換に失敗しました") from exc
    tokens = resp.json()
    if not tokens.get("id_token"):
        raise HTTPException(502, "OIDC 応答に id_token がありません")
    return tokens


async def _jwks(client: httpx.AsyncClient, jwks_uri: str) -> dict:
    try:
        resp = await client.get(jwks_uri, timeout=settings.data_fetch_timeout_seconds)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "OIDC JWKS の取得に失敗しました") from exc
    return resp.json()


async def verify_id_token(id_token: str, nonce: str,
                          client: httpx.AsyncClient) -> dict:
    """id_token の署名・iss・aud・exp・nonce を検証して claims を返す。"""
    meta = await discovery(client)
    try:
        header = pyjwt.get_unverified_header(id_token)
        jwks = await _jwks(client, meta["jwks_uri"])
        key = None
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == header.get("kid"):
                key = pyjwt.algorithms.RSAAlgorithm.from_jwk(jwk)
                break
        if key is None:
            raise ValueError("kid not found in JWKS")
        claims = pyjwt.decode(
            id_token, key, algorithms=["RS256"],
            audience=settings.oidc_client_id,
            issuer=meta.get("issuer") or settings.oidc_issuer_url,
            options={"require": ["exp", "iss", "aud"]},
        )
    except pyjwt.PyJWTError as exc:
        raise HTTPException(401, "OIDC id_token の検証に失敗しました") from exc
    except ValueError as exc:
        raise HTTPException(401, "OIDC 署名鍵が見つかりません") from exc
    if claims.get("nonce") != nonce:
        raise HTTPException(401, "OIDC nonce の検証に失敗しました")
    return claims


def provision_user(db: Session, email: str, display_name: str, groups: list[str]) -> User:
    """email でユーザーを突合し、無ければ auto-provision（#118）。"""
    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(400, "OIDC 応答に email がありません")
    role = map_groups_to_role(groups)
    user = db.scalar(select(User).where(User.email == email)) or db.scalar(
        select(User).where(User.username == email))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if user:
        user.email = email
        user.display_name = display_name or user.display_name
        user.role = role
        user.is_active = True
        user.updated_at = now
        return user
    if not settings.oidc_auto_provision:
        raise HTTPException(403, "OIDC ユーザーの自動登録が無効です（管理者へ連絡してください）")
    uid = "OIDC-" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:8]
    user = User(id=uid, username=email, display_name=display_name or email,
                email=email, role=role, department="OIDC",
                password_hash="", is_active=True, created_at=now, updated_at=now)
    db.add(user)
    return user


def issue_app_token(user: User) -> str:
    return create_access_token(user.id, user.role)


async def end_session_url(client: httpx.AsyncClient) -> str | None:
    meta = await discovery(client)
    return meta.get("end_session_endpoint")
