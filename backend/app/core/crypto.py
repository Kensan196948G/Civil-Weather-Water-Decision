"""秘密値（AI APIキー等）の対称鍵暗号化（#80）。

鍵素材の優先順位（#80 対抗レビュー high-2）:
1. SETTINGS_ENCRYPTION_KEY（暗号化専用鍵。設定されていれば最優先）
2. JWT_SECRET（未設定時のフォールバック）

素材を SHA-256 → base64url 32バイト → Fernet 鍵へ決定的に導出する。専用鍵が無く
JWT_SECRET が既定値/32バイト未満のときは **実用強度に満たない**ため、ai_api_key の
新規保存は routes 側の保存ガードで 422 拒否する（encryption_is_strong を参照）。
本番チェックリスト（SETTINGS_ENCRYPTION_KEY または JWT_SECRET を 32バイト以上で設定）
が適用されて初めて保存が有効化される。

鍵素材を変更すると既存の暗号値は復号できなくなり、decrypt() は None を返す。
呼び出し側はこれを「未設定(configured=false)」として安全に縮退させ、500 を出さない。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import _DEFAULT_JWT_SECRET, settings

# 実用強度とみなす最小バイト長（Fernet鍵導出前の素材長。JWT_SECRET本番要件と同じ）
_MIN_KEY_BYTES = 32


def _key_source() -> str:
    """Fernet 鍵導出の素材。専用鍵があれば優先、なければ JWT_SECRET。"""
    dedicated = (settings.settings_encryption_key or "").strip()
    return dedicated if dedicated else settings.jwt_secret


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


def _fernet() -> Fernet:
    """鍵素材（専用鍵優先）から Fernet 鍵（32バイトの base64url）を決定的に導出する。"""
    key = base64.urlsafe_b64encode(hashlib.sha256(_key_source().encode()).digest())
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
