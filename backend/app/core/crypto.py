"""秘密値（AI APIキー等）の対称鍵暗号化（#80）。

鍵は JWT_SECRET から決定的に導出する（SHA-256 → base64url 32バイト → Fernet）。
そのため **暗号強度は JWT_SECRET の秘匿に完全に依存する**。本番チェックリスト
（JWT_SECRET を 32バイト以上のランダム値で必ず上書き。config._guard_production が
起動時に強制）が適用されて初めて実用強度になる。

JWT_SECRET を変更すると既存の暗号値は復号できなくなり、decrypt() は None を返す。
呼び出し側はこれを「未設定(configured=false)」として安全に縮退させ、500 を出さない。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    """JWT_SECRET から Fernet 鍵（32バイトの base64url）を決定的に導出する。"""
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.jwt_secret.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """平文を暗号化してトークン文字列を返す。"""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    """暗号トークンを復号する。鍵不一致・破損時は None（呼び出し側で未設定扱いへ縮退）。"""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None
