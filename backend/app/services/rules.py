"""判定閾値の管理サービス（#34/#35, FR-054）。

decision_engine.TH（判定ルールが直接参照する閾値辞書）を DB（decision_rules）の
上書き値と同期する。行が無いキーは出荷時既定（DEFAULT_TH）のまま。
初回スコープは会社基準（グローバル）のみ。現場・工種別の階層は将来拡張。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DecisionRule
from .decision_engine import DEFAULT_TH, TH

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


def apply_overrides(db: Session) -> None:
    """DBの上書き値を decision_engine.TH に反映する（起動時・設定変更時に呼ぶ）。

    行が無いキーは既定値へ戻す（リセットの取りこぼし防止のため毎回全キーを再構成）。
    """
    overrides = {r.key: r.value for r in db.scalars(select(DecisionRule)).all()
                 if r.key in DEFAULT_TH}
    for key, default in DEFAULT_TH.items():
        TH[key] = overrides.get(key, default)


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


def validate_updates(updates: dict) -> list[str]:
    """更新内容を検証しエラー文のリストを返す（空なら妥当）。value=None はリセット指示。"""
    errors = []
    for key, value in updates.items():
        if key not in RULE_META:
            errors.append(f"不明な閾値キー: {key}")
            continue
        if value is None:
            continue  # リセット
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{key}: 数値を指定してください")
            continue
        meta = RULE_META[key]
        if not (meta["min"] <= float(value) <= meta["max"]):
            errors.append(f"{key}: {meta['min']}〜{meta['max']} の範囲で指定してください")
    if errors:
        return errors

    # 大小関係は「適用後の実効値」で検証（片側だけ変更しても矛盾を見逃さない）
    effective = dict(TH)
    for key, value in updates.items():
        effective[key] = DEFAULT_TH[key] if value is None else float(value)
    for low, high in _ORDER_CONSTRAINTS:
        if effective[low] >= effective[high]:
            errors.append(
                f"{low}({effective[low]}) は {high}({effective[high]}) より小さい値にしてください")
    return errors


def update_rules(db: Session, updates: dict, username: str) -> None:
    """検証済みの更新を適用する（value=None は行削除＝既定値へリセット）。commit は呼び出し側。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    for key, value in updates.items():
        row = db.get(DecisionRule, key)
        if value is None:
            if row:
                db.delete(row)
        elif row:
            row.value = float(value)
            row.updated_at = now
            row.updated_by = username
        else:
            db.add(DecisionRule(key=key, value=float(value), updated_at=now, updated_by=username))
