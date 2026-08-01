"""秘密値（AI APIキー等）の対称鍵暗号化（#80）。

鍵素材の優先順位（#80 対抗レビュー high-2）:
1. SETTINGS_ENCRYPTION_KEY（暗号化専用鍵。設定されていれば最優先）
2. JWT_SECRET（未設定時のフォールバック）
3. SETTINGS_ENCRYPTION_PREVIOUS_KEYS（復号専用。ローテーション期間だけ利用）

素材を SHA-256 → base64url 32バイト → Fernet 鍵へ決定的に導出する。本番は起動ガードで
SETTINGS_ENCRYPTION_KEY（32バイト以上）を必須化し、JWT_SECRET とは鍵ローテーションを分離する。
local/test では専用鍵が無くても、JWT_SECRET が既定値でなく32バイト以上なら保存を許可する。
どちらも満たさないときは routes 側の保存ガードで ai_api_key の新規保存を 422 拒否する
（encryption_is_strong を参照）。

鍵素材を変更すると既存の暗号値は、旧鍵を SETTINGS_ENCRYPTION_PREVIOUS_KEYS に一時設定した間だけ
復号できる。旧鍵にも一致しない場合、decrypt() は None を返す。呼び出し側はこれを
「未設定(configured=false)」として安全に縮退させ、500 を出さない。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import _DEFAULT_JWT_SECRET, settings

# 実用強度とみなす最小バイト長（Fernet鍵導出前の素材長。JWT_SECRET本番要件と同じ）
_MIN_KEY_BYTES = 32


def _current_key_source() -> str:
    """暗号化に使うFernet鍵素材。専用鍵があれば優先、なければlocal/test用にJWT_SECRET。"""
    dedicated = (settings.settings_encryption_key or "").strip()
    return dedicated if dedicated else settings.jwt_secret


def _previous_key_sources() -> list[str]:
    """復号専用の旧鍵素材。カンマ区切りで複数世代を短期間だけ許可する。"""
    raw = settings.settings_encryption_previous_keys or ""
    current = _current_key_source()
    return [
        key for key in (part.strip() for part in raw.split(","))
        if key and key != current
    ]


def encryption_is_strong() -> bool:
    """ai_api_key を実用強度で暗号化できるか。

    専用鍵(SETTINGS_ENCRYPTION_KEY, 32バイト以上)があれば True。無ければ JWT_SECRET が
    既定値でなく 32バイト以上のときのみ True。False の間は新規保存を拒否する。
    """
    dedicated = (settings.settings_encryption_key or "").strip()
    if len(dedicated.encode()) >= _MIN_KEY_BYTES:
        return True
    secret = settings.jwt_secret or ""
    if secret == _DEFAULT_JWT_SECRET:
        return False
    return len(secret.encode()) >= _MIN_KEY_BYTES


def _fernet(source: str) -> Fernet:
    """鍵素材から Fernet 鍵（32バイトの base64url）を決定的に導出する。"""
    key = base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """平文を現行鍵で暗号化してトークン文字列を返す。"""
    return _fernet(_current_key_source()).encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    """暗号トークンを復号する。現行鍵→旧鍵の順に試し、不一致・破損時は None。"""
    try:
        token_bytes = token.encode()
    except (AttributeError, UnicodeError):
        return None
    for source in [_current_key_source(), *_previous_key_sources()]:
        try:
            return _fernet(source).decrypt(token_bytes).decode()
        except (InvalidToken, ValueError, TypeError):
            continue
    return None
