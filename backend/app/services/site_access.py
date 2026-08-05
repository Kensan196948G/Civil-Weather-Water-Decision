"""現場単位権限（#117）のアプリ層フィルタ。

設計 docs/design/site-level-permissions.md に基づき:
- admin / tech_manager / safety は全現場を閲覧できる（safety の判断書込は割当が必要）
- site_manager / viewer は user_site_access の割当現場のみ閲覧できる
- 書込（作業予定=editor / 判定・判断記録=decision）は割当ロールで判定
- PostgreSQL RLS は将来層（本モジュールは第一層）
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Site, User, UserSiteAccess

SITE_ACCESS_ROLES = {"site_viewer", "site_editor", "site_decision"}
_FULL_READ_ROLES = {"admin", "tech_manager", "safety"}

# 書込アクションごとに必要な user_site_access.role
_WRITE_ROLES = {
    "editor": {"site_editor", "site_decision"},
    "decision": {"site_decision"},
}


def accessible_site_ids(db: Session, user: User) -> set[str]:
    """ユーザーがアクセス可能な現場ID集合を返す。"""
    if user.role in _FULL_READ_ROLES:
        return set(db.scalars(select(Site.id)).all())
    return set(db.scalars(
        select(UserSiteAccess.site_id).where(UserSiteAccess.user_id == user.id)).all())


def has_full_read(user: User) -> bool:
    return user.role in _FULL_READ_ROLES


def ensure_site_read(db: Session, user: User, site_id: str) -> None:
    """閲覧可否を検査し、不可なら 403（存在しない現場は呼び出し側で404を先に返す）。"""
    if user.role in _FULL_READ_ROLES:
        return
    row = db.scalar(select(UserSiteAccess).where(
        UserSiteAccess.user_id == user.id,
        UserSiteAccess.site_id == site_id))
    if row is None:
        raise HTTPException(403, "この現場へのアクセス権限がありません")


def ensure_site_write(db: Session, user: User, site_id: str, action: str) -> None:
    """書込可否（editor/decision）を検査し、不可なら 403。"""
    if user.role in ("admin", "tech_manager"):
        return
    if user.role not in ("site_manager", "safety"):
        raise HTTPException(403, "この操作の権限がありません")
    row = db.scalar(select(UserSiteAccess).where(
        UserSiteAccess.user_id == user.id,
        UserSiteAccess.site_id == site_id))
    if row is None or row.role not in _WRITE_ROLES.get(action, set()):
        raise HTTPException(403, "この現場での操作権限がありません")
