"""通知サービス（設計§14）。判定結果・データソース状態から通知を導出。

初期は画面内通知（GET /api/notifications）。メール/Slack/Teams は Notifier 抽象で
拡張点を用意し、Webhook 未設定なら no-op に縮退する（FR-072 拡張できる設計）。
通知本文には現場名・作業・レベル・理由・データ元・時刻・「最終判断は現場責任者」を含める。
"""
from __future__ import annotations

import logging

import httpx

from ..core.config import settings

logger = logging.getLogger("cwwd.notify")

DISCLAIMER = "本通知は判断支援です。最終判断は現場責任者が公式情報・現地確認のうえ行ってください。"


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


async def dispatch(notifs: list[dict]) -> dict:
    """設定済みの外部通知先（Slack/Teams）へ送信。未設定は no-op。

    現状は手動/将来のスケジューラから呼ぶ拡張点。重大度2以上のみ送る。
    """
    targets = []
    if settings.slack_webhook_url:
        targets.append(("slack", settings.slack_webhook_url))
    if settings.teams_webhook_url:
        targets.append(("teams", settings.teams_webhook_url))
    high = [n for n in notifs if n["severity"] >= 2]
    if not targets:
        for n in high:
            logger.info("[notify] %s | %s", n["title"], n["message"])  # 既定はログ
        return {"sent": 0, "logged": len(high)}
    sent = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for n in high:
            text = f"[{n['title']}] {n['message']}\n{DISCLAIMER}"
            for kind, url in targets:
                try:
                    payload = {"text": text}  # Slack/Teams とも text フィールドで概ね通る
                    r = await client.post(url, json=payload)
                    if r.status_code < 400:
                        sent += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("notify %s failed: %s", kind, e)
    return {"sent": sent, "logged": 0}
