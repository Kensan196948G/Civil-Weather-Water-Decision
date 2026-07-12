"""判定閾値の管理サービス（#34/#35, FR-054）。

設計原則（対抗レビュー2巡反映）:
- **DBが単一の真実**。プロセスローカルな TH の変異は行わず、評価時に effective_th() が
  DB上書き値を短TTLキャッシュ付きで解決して evaluate(th=...) へ渡す。
  マルチワーカー/レプリカ構成でも不整合窓は最大 _CACHE_TTL 秒に収まる。
- **上書き行は管理者が明示指定したキーのみ**（合成書き込みはしない。既定値と同値の行を
  勝手に作ると、将来の出荷時既定変更に追従できない「凍結された既定」が生まれるため）。
- 同時部分更新の混成不整合は**書き込みの直列化**で防ぐ: PostgreSQL は
  pg_advisory_xact_lock（トランザクション終了で自動解放）、プロセス内は WRITE_LOCK。
  SQLite はPoC・単一プロセス運用前提のため WRITE_LOCK で足りる（マルチプロセスSQLiteは非サポート）。

初回スコープは会社基準（グローバル）のみ。現場・工種別の階層は将来拡張。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..core.db import SessionLocal
from ..models import DecisionRule
from .decision_engine import DEFAULT_TH

JST = timezone(timedelta(hours=9))

# 画面表示・バリデーション用メタデータ（許可キーの単一情報源）
RULE_META = {
    "rain_light":   {"label": "降雨 注意",       "unit": "mm/h", "min": 0.0,   "max": 100.0,
                     "desc": "時間雨量がこの値以上で「注意」（土工・打設・舗装）"},
    "rain_heavy":   {"label": "降雨 中止検討",   "unit": "mm/h", "min": 0.0,   "max": 300.0,
                     "desc": "時間雨量がこの値以上で「中止検討」"},
    "wind_strong":  {"label": "平均風速 注意",   "unit": "m/s",  "min": 0.0,   "max": 60.0,
                     "desc": "平均風速がこの値以上で「注意」（クレーン・打設）"},
    "gust_stop":    {"label": "突風 中止検討",   "unit": "m/s",  "min": 0.0,   "max": 100.0,
                     "desc": "最大瞬間風速がこの値以上で「中止検討」（クレーン）"},
    "temp_high":    {"label": "高温 注意",       "unit": "℃",    "min": -10.0, "max": 50.0,
                     "desc": "最高気温がこの値以上で暑中対策の「注意」"},
    "temp_low":     {"label": "低温 注意",       "unit": "℃",    "min": -40.0, "max": 30.0,
                     "desc": "気温がこの値以下で低温施工の「注意」（打設・舗装）"},
    "wbgt_caution": {"label": "WBGT 厳重警戒",   "unit": "",     "min": 15.0,  "max": 40.0,
                     "desc": "WBGTがこの値以上で熱中症「注意」"},
    "wbgt_danger":  {"label": "WBGT 危険",       "unit": "",     "min": 20.0,  "max": 45.0,
                     "desc": "WBGTがこの値以上で熱中症「中止検討」"},
    "upstream_rain": {"label": "上流雨量 注意",  "unit": "mm/h", "min": 0.0,   "max": 100.0,
                      "desc": "上流域の雨量がこの値以上で水位上昇「注意」（河川内作業）"},
}

# 大小関係の整合制約（(小さい方, 大きい方) — 等号は不可）
_ORDER_CONSTRAINTS = [
    ("rain_light", "rain_heavy"),
    ("wind_strong", "gust_stop"),
    ("temp_low", "temp_high"),
    ("wbgt_caution", "wbgt_danger"),
]

# 書き込み直列化: プロセス内ロック（PUTハンドラが apply〜commit を包む）
WRITE_LOCK = threading.Lock()
# PostgreSQL advisory lock のキー（本アプリ固有の任意定数）
_PG_ADVISORY_KEY = 748_321

# 実効閾値の短TTLキャッシュ（プロセスごと。DBが真実のため、他ワーカーの変更も
# 最大 _CACHE_TTL 秒で追従する。PUT成功時は自プロセスのみ即時クリア）
_CACHE_TTL = 5.0
_cache: dict = {"at": 0.0, "th": None}


def clear_cache() -> None:
    _cache["at"] = 0.0
    _cache["th"] = None


def _load_overrides(db: Session) -> dict:
    return {r.key: r.value for r in db.scalars(select(DecisionRule)).all()
            if r.key in DEFAULT_TH}


def effective_th(db: Session | None = None) -> dict:
    """現在の実効閾値（既定値＋DB上書き）を返す。評価パスから毎回呼ばれる想定。"""
    now = time.monotonic()
    if _cache["th"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["th"]
    own = db is None
    if own:
        db = SessionLocal()
    try:
        th = dict(DEFAULT_TH)
        th.update(_load_overrides(db))
    finally:
        if own:
            db.close()
    _cache["at"] = now
    _cache["th"] = th
    return th


def list_rules(db: Session) -> list[dict]:
    """全閾値の現在有効値・既定値・上書き状態・メタ情報を返す（設定画面用）。"""
    rows = {r.key: r for r in db.scalars(select(DecisionRule)).all()}
    out = []
    for key, default in DEFAULT_TH.items():
        meta = RULE_META[key]
        row = rows.get(key)
        out.append({
            "key": key, "label": meta["label"], "unit": meta["unit"], "desc": meta["desc"],
            "min": meta["min"], "max": meta["max"],
            "default": default,
            "value": row.value if row else default,
            "overridden": row is not None,
            "updated_at": row.updated_at if row else None,
            "updated_by": row.updated_by if row else None,
        })
    return out


def _validate_shape(updates: dict) -> list[str]:
    """キー・型・範囲の単項検証（DB非依存）。"""
    errors = []
    for key, value in updates.items():
        if key not in RULE_META:
            errors.append(f"不明な閾値キー: {key}")
            continue
        if value is None:
            continue  # リセット
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{key}: 数値を指定してください")
            continue
        meta = RULE_META[key]
        if not (meta["min"] <= float(value) <= meta["max"]):
            errors.append(f"{key}: {meta['min']}〜{meta['max']} の範囲で指定してください")
    return errors


def apply_updates(db: Session, updates: dict, username: str) -> list[str]:
    """検証と書き込みを同一トランザクションで行う。エラー文リストを返す（空なら適用済み・要commit）。

    - PostgreSQL では pg_advisory_xact_lock で書き込みを直列化（空テーブルでも有効。
      行ロック(with_for_update)は既存行が無いと何も守らないため使わない）。
      呼び出し側は WRITE_LOCK（プロセス内）で apply〜commit を包むこと
    - 直列化の下で **DBの実効値** に updates を重ねて大小制約を検証し、
      **管理者が明示指定したキーだけ**を書き込む（合成上書き行は作らない）
    """
    errors = _validate_shape(updates)
    if errors:
        return errors

    if db.get_bind().dialect.name == "postgresql":
        # トランザクションスコープの排他ロック（commit/rollbackで自動解放）
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_ADVISORY_KEY})
    rows = {r.key: r for r in db.scalars(select(DecisionRule)).all() if r.key in DEFAULT_TH}
    effective = dict(DEFAULT_TH)
    effective.update({k: r.value for k, r in rows.items()})
    for key, value in updates.items():
        effective[key] = DEFAULT_TH[key] if value is None else float(value)

    for low, high in _ORDER_CONSTRAINTS:
        if effective[low] >= effective[high]:
            errors.append(
                f"{low}({effective[low]}) は {high}({effective[high]}) より小さい値にしてください")
    if errors:
        return errors

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    for key, value in updates.items():
        row = rows.get(key)
        if value is None:
            if row:
                db.delete(row)
        elif row:
            row.value = float(value)
            row.updated_at = now
            row.updated_by = username
        else:
            db.add(DecisionRule(key=key, value=float(value), updated_at=now, updated_by=username))
    return []
