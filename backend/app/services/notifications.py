"""通知サービス（設計§14）。判定結果・データソース状態から通知を導出。

初期は画面内通知（GET /api/notifications）。メール/Slack/Teams は Notifier 抽象で
拡張点を用意し、Webhook 未設定なら no-op に縮退する（FR-072 拡張できる設計）。
通知本文には現場名・作業・レベル・理由・データ元・時刻・「最終判断は現場責任者」を含める。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
import json

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import AppSetting, DataSourceStatus, NotificationDelivery, Site
from . import assessment

logger = logging.getLogger("cwwd.notify")

DISCLAIMER = "本通知は判断支援です。最終判断は現場責任者が公式情報・現地確認のうえ行ってください。"
_NOTIFY_KEY = "notify"


def build_notifications(cards: list[dict], sources: list[dict]) -> list[dict]:
    """評価済みカード＋データソース状態から通知一覧を導出する。"""
    out: list[dict] = []
    for c in cards:
        sid, name = c["id"], c["name"]
        if c["level"] == 2:  # リスク通知（中止検討）
            out.append(_n(f"risk-{sid}", "risk", 2, "中止検討", sid,
                          f"{name}：{c['summary']}", c.get("updated")))
        elif c["level"] == 3:  # データ不確実通知（確認不能）
            out.append(_n(f"data-{sid}", "data", 3, "確認不能", sid,
                          f"{name}：主要データ不足。公式情報・現地確認を優先してください。", c.get("updated")))
        if (c.get("wbgt") or 0) >= 28:  # WBGT通知（厳重警戒以上）
            out.append(_n(f"wbgt-{sid}", "wbgt", 1, "暑さ指数 厳重警戒以上", sid,
                          f"{name}：WBGT {c.get('wbgt')}。休憩・水分補給・作業時間調整を。", c.get("updated")))
        if c.get("riverState") == "rising":  # 河川通知
            out.append(_n(f"river-{sid}", "river", 2, "河川 水位上昇", sid,
                          f"{name}：水位が上昇傾向です。退避基準と公式情報を確認してください。", c.get("updated")))
    for s in sources:
        if s["status"] == "Error":
            out.append(_n(f"src-{s['id']}", "source", 2, "データソース障害", None,
                          f"{s['name']}：取得失敗（連続{s.get('fails', 0)}回）。公式ページ参照を案内します。", s.get("lastOk")))
        elif s["status"] == "Warning":
            out.append(_n(f"src-{s['id']}", "source", 1, "データソース遅延", None,
                          f"{s['name']}：更新遅延。古いデータの可能性があります。", s.get("lastOk")))
    # 重大度の高い順
    out.sort(key=lambda n: -n["severity"])
    return out


def _n(nid, kind, severity, title, site_id, message, time):
    return {"id": nid, "kind": kind, "severity": severity, "title": title,
            "siteId": site_id, "message": message, "time": time, "disclaimer": DISCLAIMER}


async def build_current_notifications(db: Session) -> list[dict]:
    """現在の現場リスク・データソース状態から通知一覧を生成する。APIとschedulerで共有。"""
    sites = db.scalars(select(Site).where(Site.status == "active").order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites), db=db)
    src = db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.id)).all()
    sources = [{"id": d.id, "name": d.name, "status": d.status,
                "fails": d.fails, "lastOk": d.last_ok} for d in src]
    return build_notifications(cards, sources)


def _env_default_flags() -> dict:
    # UI設定未初期化時の既定値。環境変数URLが設定済みなら有効（Track C以前の後方互換）。
    return {
        "slack_enabled": bool(settings.slack_webhook_url),
        "teams_enabled": bool(settings.teams_webhook_url),
    }


def _notify_flags(db: Session) -> dict:
    row = db.get(AppSetting, _NOTIFY_KEY)
    if row is None or not row.value:
        return _env_default_flags()
    try:
        data = json.loads(row.value)
    except (TypeError, ValueError):
        return _env_default_flags()
    if not isinstance(data, dict):
        return _env_default_flags()
    return {
        "slack_enabled": bool(data.get("slack_enabled")),
        "teams_enabled": bool(data.get("teams_enabled")),
    }


def enabled_targets(db: Session) -> list[tuple[str, str]]:
    """UI設定と環境変数の両方で有効な通知先。無ければログにフォールバックする。"""
    flags = _notify_flags(db)
    targets: list[tuple[str, str]] = []
    if flags["slack_enabled"] and settings.slack_webhook_url:
        targets.append(("slack", settings.slack_webhook_url))
    if flags["teams_enabled"] and settings.teams_webhook_url:
        targets.append(("teams", settings.teams_webhook_url))
    return targets or [("log", "")]


def _signature(n: dict) -> str:
    return "|".join(str(n.get(k) or "") for k in ("kind", "id", "siteId", "severity", "title"))


def _ts(now: float) -> str:
    return datetime.fromtimestamp(now, tz=assessment.JST).strftime("%Y-%m-%d %H:%M:%S")


def _delivery_row(db: Session, channel: str, notification: dict, now: float) -> NotificationDelivery:
    fp = _signature(notification)
    row = db.scalar(select(NotificationDelivery).where(
        NotificationDelivery.channel == channel,
        NotificationDelivery.fingerprint == fp,
    ))
    if row is not None:
        return row
    row = NotificationDelivery(
        channel=channel,
        fingerprint=fp,
        notification_id=str(notification.get("id") or ""),
        severity=int(notification.get("severity") or 0),
        status="pending",
        last_error="",
        created_at=_ts(now),
        updated_at=_ts(now),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # 複数プロセスが同時実行された場合の (channel, fingerprint) 一意制約違反。
        # 自分の挿入を諦めて他プロセスが挿入した既存行に乗り換える（dispatch全体を止めない）。
        db.rollback()
        row = db.scalar(select(NotificationDelivery).where(
            NotificationDelivery.channel == channel,
            NotificationDelivery.fingerprint == fp,
        ))
        if row is None:
            raise
    return row


def _suppressed(row: NotificationDelivery, now: float) -> bool:
    ttl = settings.notification_dedup_seconds
    return bool(ttl > 0 and row.last_sent_at is not None and now - row.last_sent_at < ttl)


async def dispatch(db: Session, notifs: list[dict],
                   *, client: httpx.AsyncClient | None = None, now: float | None = None) -> dict:
    """設定済みの外部通知先（Slack/Teams）へ送信。未設定は no-op。

    スケジューラから呼ぶ。重大度2以上のみ送る。同一通知は一定時間抑止する。
    配送1件ごとに commit する（複数配送の一括commit/rollackだと、外部送信が
    成功した直後に後続の配送で例外が起きた場合、rollbackで送信済み記録ごと
    失われて次回実行時に再送してしまうため）。
    """
    now = time.time() if now is None else now
    targets = enabled_targets(db)
    high = [n for n in notifs if n["severity"] >= 2]
    sent = logged = suppressed = failed = 0
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10)
    try:
        for n in high:
            text = f"[{n['title']}] {n['message']}\n{DISCLAIMER}"
            for kind, url in targets:
                row = _delivery_row(db, kind, n, now)
                if _suppressed(row, now):
                    suppressed += 1
                    continue
                row.last_attempt_at = now
                row.updated_at = _ts(now)
                db.commit()  # 試行記録を先に確定
                try:
                    if kind == "log":
                        logger.info("[notify] %s | %s", n["title"], n["message"])
                        logged += 1
                        ok = True
                    else:
                        payload = {"text": text}  # Slack/Teams とも text フィールドで概ね通る
                        r = await client.post(url, json=payload)
                        ok = 200 <= r.status_code < 300  # 3xx等を成功扱いにしない
                        if ok:
                            sent += 1
                        else:
                            failed += 1
                            row.last_error = f"HTTP {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    ok = False
                    failed += 1
                    row.last_error = str(e)[:1000]
                    logger.warning("notify %s failed: %s", kind, e)
                if ok:
                    row.status = "logged" if kind == "log" else "sent"
                    row.last_sent_at = now
                    row.last_error = ""
                else:
                    row.status = "failed"
                row.updated_at = _ts(now)
                db.commit()  # 送信結果を配送単位で確定
    finally:
        if own_client:
            await client.aclose()
    return {"sent": sent, "logged": logged, "suppressed": suppressed, "failed": failed}
