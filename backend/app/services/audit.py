"""監査ログ記録（設計§13.2）。ログイン・設定変更・判定・判断・CSV出力等を残す。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AuditLog

JST = timezone(timedelta(hours=9))


def audit_add(db: Session, user, action: str, message: str = "",
              component: str = "api", site_id: str | None = None,
              source_id: str | None = None) -> None:
    """監査行を現在のトランザクションへ追加する（commitしない）。

    設定変更など「変更と監査記録を原子的に残す」必要がある箇所で使い、
    呼び出し側がドメイン変更と同一の commit に含める（#35 対抗レビュー / #63）。
    """
    db.add(AuditLog(
        timestamp=datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        action=action, component=component, message=message,
        site_id=site_id, source_id=source_id,
    ))


def audit(db: Session, user, action: str, message: str = "",
          component: str = "api", site_id: str | None = None,
          source_id: str | None = None) -> None:
    audit_add(db, user, action, message, component=component,
              site_id=site_id, source_id=source_id)
    db.commit()
