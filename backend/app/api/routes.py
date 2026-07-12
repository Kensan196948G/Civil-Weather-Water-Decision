"""API ルータ（詳細設計 §7）。WebUI 接続用エンドポイント。"""
from __future__ import annotations

import csv
import io
import json
import random
import re
import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..core import crypto
from ..core.db import get_db
from ..core.deps import get_current_user, require_role
from ..models import (
    AppSetting, AuditLog, DataSourceStatus, DecisionLog, DecisionReason, DecisionResult,
    IdCounter, Site, User, WorkPlan, WorkType,
)
from ..services import assessment, notifications, rules as rules_service
from ..services.audit import audit, audit_add
from ..services.data_collectors import open_meteo, source_probe

# ルータ全体に認証を必須化（/auth と /health は別ルータ/main で公開）
router = APIRouter(dependencies=[Depends(get_current_user)])

WORK_KEYS = {"river", "concrete", "earthwork", "pavement", "crane", "heat"}
RIVER_STATES = {"none", "stable", "rising", "stale"}
_ID_COMMIT_ATTEMPTS = 5


def _max_existing_numeric(db: Session, model, prefix: str) -> int:
    """既存IDを全件パースし数値maxを返す（#49: 文字列順ソートは桁上がりで崩れるため使わない）。"""
    ids = db.scalars(select(model.id)).all()
    nums = [int(x[len(prefix):]) for x in ids if x[len(prefix):].isdigit()]
    return max(nums) if nums else 0


def _allocate_id(db: Session, model, prefix: str, width: int) -> str:
    """id_counters の行UPDATE（DB側で直列化される）により一意な次番号を確保する（#49）。

    max(id)+1 の読取→INSERT方式は同時実行で同じ候補を計算し得るため、採番自体を
    カウンタ行の原子的 UPDATE ... RETURNING に寄せる（PostgreSQL は行ロック、
    SQLite は単一ライタで直列化）。カウンタ行が無い初回のみ既存IDのmaxから遅延初期化し、
    同時初期化の PK 衝突は _commit_with_retry 側のリトライで一方が UPDATE 経路に乗る。
    """
    nxt = db.execute(
        update(IdCounter)
        .where(IdCounter.name == prefix)
        .values(value=IdCounter.value + 1)
        .returning(IdCounter.value)
    ).scalar_one_or_none()
    if nxt is None:
        nxt = _max_existing_numeric(db, model, prefix) + 1
        db.add(IdCounter(name=prefix, value=nxt))
        db.flush()
    return f"{prefix}{nxt:0{width}d}"


# 採番対象の (モデル, プレフィックス)。重複検出時のカウンタ再同期に使う
_COUNTER_SPECS = ((Site, "S"), (DecisionResult, "DR"), (WorkPlan, "WP"), (DecisionLog, "L"))

# リトライしてよい一時エラーのみ許可（恒久障害を409に偽装しない: 対抗レビュー[medium]）
_SQLITE_TRANSIENT_MARKERS = ("database is locked", "database table is locked")
_PG_TRANSIENT_SQLSTATES = {"40001", "40P01", "55P03"}  # serialization/deadlock/lock_not_available


def _is_transient_db_error(e: OperationalError) -> bool:
    """ロック/直列化系の一時エラーのみ真。テーブル不存在・接続断などは偽（5xxで顕在化）。"""
    pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
    if pgcode:
        return pgcode in _PG_TRANSIENT_SQLSTATES
    msg = str(getattr(e, "orig", e)).lower()
    return any(m in msg for m in _SQLITE_TRANSIENT_MARKERS)


def _resync_counters(db: Session) -> None:
    """カウンタを実テーブルのmaxまで進める（対抗レビュー[high]: リストア・手動修復等で
    カウンタが実IDより遅れると採番が衝突し続けるため、重複検出時に自己修復する）。"""
    for model, prefix in _COUNTER_SPECS:
        real = _max_existing_numeric(db, model, prefix)
        db.execute(
            update(IdCounter)
            .where(IdCounter.name == prefix)
            .where(IdCounter.value < real)
            .values(value=real)
        )
    db.commit()


def _commit_with_retry(db: Session, build_and_add):
    """build_and_add() でID採番〜db.add()まで行い、競合時は採番からやり直す。

    通常は _allocate_id のカウンタ行更新がDB側で直列化されるため衝突しないが、
    カウンタ遅延（リストア等）による重複や SQLite のロック競合に備え、
    再同期＋ジッタ付きリトライを保険として持つ（#49 対抗レビュー指摘対応）。
    """
    for attempt in range(1, _ID_COMMIT_ATTEMPTS + 1):
        try:
            result = build_and_add()
            db.commit()
            return result
        except IntegrityError:
            db.rollback()
            _resync_counters(db)  # 重複＝カウンタ遅延の可能性。実maxまで進めて再採番
            if attempt < _ID_COMMIT_ATTEMPTS:
                time.sleep(random.uniform(0.01, 0.05) * attempt)  # jitter+backoff
        except OperationalError as e:
            db.rollback()
            if not _is_transient_db_error(e):
                raise  # 恒久障害（テーブル不存在・接続断等）は409に偽装せず5xxで顕在化
            if attempt < _ID_COMMIT_ATTEMPTS:
                time.sleep(random.uniform(0.01, 0.05) * attempt)
    raise HTTPException(409, "同時登録が競合しました。再試行してください。")


# ---------- 現場 ----------
@router.get("/sites")
def list_sites(db: Session = Depends(get_db)):
    sites = db.scalars(select(Site).order_by(Site.id)).all()
    return [{"id": s.id, "code": s.site_code, "name": s.name, "loc": s.loc,
             "lat": s.latitude, "lon": s.longitude, "work": s.work_type,
             "project": s.project_type, "riverWork": s.river_work_flag,
             "manager": s.manager, "status": s.status} for s in sites]


@router.get("/sites/{site_id}")
async def get_site(site_id: str, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    card = await assessment.assess_site(site)
    plans = []
    for p in site.plans:
        d = await assessment.assess_decision(site, p.work_type, p.planned_start, p.planned_end)
        plans.append({"id": p.id, "title": p.title, "time": f"{p.planned_start}–{p.planned_end}",
                      "contractor": p.contractor, "workType": p.work_type,
                      "level": d["overall_level"], "levelLabel": d["overall_label"],
                      "reason": d["summary"]})
    history = db.scalars(
        select(DecisionLog).where(DecisionLog.site_id == site_id)
        .order_by(DecisionLog.id.desc()).limit(4)).all()
    return {**card,
            "manager": site.manager + "（現場管理者）", "project": site.project_type,
            "hasRiver": site.work_type == "river" or site.river_work_flag,
            "plans": plans,
            "history": [{"datetime": h.decided_at, "action": h.action, "level": h.level,
                         "comment": h.comment, "by": h.decided_by} for h in history]}


@router.get("/sites/{site_id}/stations")
def site_stations(site_id: str, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    return [{"id": st.id, "name": st.name, "type": st.type, "rel": st.rel,
             "lat": st.latitude, "lon": st.longitude} for st in site.stations]


# ---------- 判定ルール（閾値）管理 (#34/#35, FR-054) ----------
class RulesUpdate(BaseModel):
    # {key: 数値 or null(=既定値へリセット)} の部分更新
    updates: dict[str, float | None]

    @field_validator("updates", mode="before")
    @classmethod
    def _reject_bool(cls, v):
        # Pydanticのfloat型はJSON真偽値を1.0/0.0へ暗黙変換するため、境界で明示拒否
        # (対抗レビュー[medium]: 誤入力が安全閾値として永続化されるのを防ぐ)
        if isinstance(v, dict):
            for key, val in v.items():
                if isinstance(val, bool):
                    raise ValueError(f"{key}: 数値を指定してください（真偽値は不可）")
        return v


@router.get("/admin/rules")
def get_rules(db: Session = Depends(get_db),
              user: User = Depends(require_role("admin", "tech_manager"))):
    return {"rules": rules_service.list_rules(db)}


@router.put("/admin/rules")
def put_rules(req: RulesUpdate, db: Session = Depends(get_db),
              user: User = Depends(require_role("admin"))):
    if not req.updates:
        raise HTTPException(422, "updates が空です")
    # 書き込みは直列化して実施(プロセス内=WRITE_LOCK、PostgreSQL間=advisory lock)。
    # ロック待ちは有限化(プロセス内5秒/PG lock_timeout 3秒)し、詰まりはワーカー占有でなく503で返す
    if not rules_service.WRITE_LOCK.acquire(timeout=5):
        raise HTTPException(503, "設定更新が混み合っています。しばらくして再試行してください。")
    try:
        try:
            errors = rules_service.apply_updates(db, req.updates, user.username)
        except OperationalError:
            # PG advisory lock の lock_timeout 超過等
            db.rollback()
            raise HTTPException(503, "設定更新が混み合っています。しばらくして再試行してください。") from None
        if errors:
            db.rollback()
            raise HTTPException(422, "; ".join(errors))
        # 監査行を同一トランザクションに含める: 監査記録なしの設定変更を残さない
        # (commit後のaudit失敗で「変更は永続・監査は欠落」となるのを防ぐ。対抗レビュー4巡目)
        audit_add(db, user, "rules_update",
                  ", ".join(f"{k}={'default' if v is None else v}" for k, v in req.updates.items()))
        try:
            db.commit()
        except IntegrityError:
            # 直列化により通常到達しない保険(万一の同時INSERT衝突を500にしない)
            db.rollback()
            raise HTTPException(409, "設定が競合しました。再試行してください。") from None
    finally:
        rules_service.WRITE_LOCK.release()
    rules_service.clear_cache()  # 自プロセスの実効閾値キャッシュを即時無効化
    return {"status": "updated", "rules": rules_service.list_rules(db)}


# ---------- 設定画面（app_settings, #80 エピック#72段8） ----------
# 1キー=1行の汎用 key-value ストア。AI APIキーは暗号化して格納し、応答では末尾4桁のみを
# マスク表示する（平文は返さない）。通知設定/データ保存期間/ユーザー設定はJSON/整数を素直に保持。
# 変更は audit_add と同一トランザクションで commit（#35/#63: 監査なき設定変更を残さない）。
# 通知Webhook自体は既存の環境変数（SLACK_/TEAMS_WEBHOOK_URL）と併存し、送信時はenv優先。
# 本テーブルの notify は画面から編集する付随設定（宛先・条件等）を保持する将来拡張の器。
_AI_KEY = "ai_api_key"
_NOTIFY_KEY = "notify"
_RETENTION_KEY = "data_retention_days"
_USER_PREFS_KEY = "user_prefs"
_RETENTION_DEFAULT = 365
_RETENTION_MIN, _RETENTION_MAX = 30, 3650
# 通知設定は固定2フラグ（API契約）。GET は常にこの2キーを bool で返す。
_NOTIFY_SUBKEYS = ("slack_enabled", "teams_enabled")
_NOTIFY_DEFAULT = {"slack_enabled": False, "teams_enabled": False}
# 通知/ユーザー設定JSONの肥大化・悪用の保険（キー名はwhitelist前提のため緩めの上限）
_SETTINGS_JSON_MAX = 8192
# Anthropic 疎通確認先。キー値は x-api-key ヘッダのみで送り、応答・ログ・監査には出さない
_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"


def _setting_ts() -> str:
    return datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")


def _upsert_setting(db: Session, key: str, value: str, username: str) -> None:
    """設定行をUPSERT（commitしない。呼び出し側が audit_add と同一txでcommitする）。"""
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_at=_setting_ts(), updated_by=username))
    else:
        row.value = value
        row.updated_at = _setting_ts()
        row.updated_by = username


def _load_json_setting(db: Session, key: str, default: dict) -> dict:
    row = db.get(AppSetting, key)
    if row is None or not row.value:
        return default
    try:
        return json.loads(row.value)
    except (ValueError, TypeError):
        return default


def _mask_tail(secret: str) -> str:
    """末尾4桁のみ見せる（例: ****wxyz）。4桁未満は全マスク（****）。"""
    return "****" + (secret[-4:] if len(secret) >= 4 else "")


def _ai_status(db: Session) -> dict:
    """AI APIキーの設定状態。configured と末尾4桁マスクのみ返す（平文は返さない）。"""
    row = db.get(AppSetting, _AI_KEY)
    if row is None or not row.value:
        return {"configured": False, "masked": None}
    plain = crypto.decrypt(row.value)
    if plain is None:
        # JWT_SECRET変更後などで復号不能 → 未設定扱いへ安全縮退（500にしない）
        return {"configured": False, "masked": None}
    return {"configured": True, "masked": _mask_tail(plain)}


def _current_retention(db: Session) -> int:
    row = db.get(AppSetting, _RETENTION_KEY)
    if row is None or not row.value:
        return _RETENTION_DEFAULT
    try:
        return int(row.value)
    except (ValueError, TypeError):
        return _RETENTION_DEFAULT


def _current_notify(db: Session) -> dict:
    """通知設定を既定値の上にマージして返す（常に slack_enabled/teams_enabled を bool で含む）。"""
    stored = _load_json_setting(db, _NOTIFY_KEY, {})
    return {k: bool(stored.get(k, _NOTIFY_DEFAULT[k])) for k in _NOTIFY_SUBKEYS}


def _settings_payload(db: Session) -> dict:
    """設定画面の表示用ペイロード（GET応答・PUT応答で共通。AI平文は含めない）。"""
    return {
        "ai": _ai_status(db),
        "notify": _current_notify(db),
        "data_retention_days": _current_retention(db),
        "user_prefs": _load_json_setting(db, _USER_PREFS_KEY, {}),
    }


@router.get("/admin/settings")
def get_settings(db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    return _settings_payload(db)


def _validate_retention(value: object) -> int:
    """data_retention_days を検証して int を返す（非整数・真偽値・範囲外は 422）。"""
    # JSONの true/false は Python では bool（int のサブクラス）。閾値管理#35と同様に明示拒否。
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(422, "data_retention_days: 整数を指定してください（真偽値は不可）")
    if not (_RETENTION_MIN <= value <= _RETENTION_MAX):
        raise HTTPException(
            422, f"data_retention_days: {_RETENTION_MIN}〜{_RETENTION_MAX} の整数で指定してください")
    return value


def _validate_notify_flag(key: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise HTTPException(422, f"{key}: 真偽値（true/false）で指定してください")
    return value


def _store_json_setting(db: Session, key: str, obj: dict, username: str) -> None:
    """JSON設定を直列化・サイズ検証してUPSERT（commitしない。肥大化を防ぐ）。"""
    serialized = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > _SETTINGS_JSON_MAX:
        raise HTTPException(422, f"{key}: 設定が大きすぎます")
    _upsert_setting(db, key, serialized, username)


@router.put("/admin/settings")
def put_settings(body: dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    """設定の部分更新。API契約はフラットな dotted key（notify.slack_enabled 等）だが、
    フロント現行実装との整合のため nested（notify:{...}）も等価に受理する。whitelist 外は 422。
    変更は監査行と同一トランザクションで commit する（#35/#63: 監査なき設定変更を残さない）。
    """
    if not body:
        raise HTTPException(422, "更新するフィールドがありません")

    # ---- フェーズ1: 全キーを検証・正規化（1つでも不正なら 422。部分適用しない） ----
    ai_secret: str | None = None
    retention: int | None = None
    notify_changes: dict[str, bool] = {}
    prefs_changes: dict[str, Any] = {}
    audit_keys: list[str] = []  # 監査にはキー名のみ（秘密値は載せない。AIは末尾4桁マスク可）

    for key, value in body.items():
        if key == _AI_KEY:  # ai_api_key（平文は監査・応答に出さない）
            if not isinstance(value, str) or not value.strip():
                # 空値での上書きは不可。解除は DELETE /admin/settings/ai を使う
                raise HTTPException(422, "ai_api_key: 空にできません（解除は DELETE を使用してください）")
            ai_secret = value.strip()
            audit_keys.append(f"{_AI_KEY}({_mask_tail(ai_secret)})")
        elif key == _RETENTION_KEY:  # data_retention_days
            retention = _validate_retention(value)
            audit_keys.append(f"{_RETENTION_KEY}={retention}")
        elif key == _NOTIFY_KEY:  # nested: {"notify": {...}}（フロント現行形・互換）
            if not isinstance(value, dict):
                raise HTTPException(422, "notify: オブジェクトで指定してください")
            for sub, sub_val in value.items():
                if sub not in _NOTIFY_SUBKEYS:
                    raise HTTPException(422, f"未知の設定キー: notify.{sub}")
                notify_changes[sub] = _validate_notify_flag(f"notify.{sub}", sub_val)
                audit_keys.append(f"notify.{sub}")
        elif key.startswith("notify."):  # dotted: {"notify.slack_enabled": true}（API契約）
            sub = key.split(".", 1)[1]
            if sub not in _NOTIFY_SUBKEYS:
                raise HTTPException(422, f"未知の設定キー: {key}")
            notify_changes[sub] = _validate_notify_flag(key, value)
            audit_keys.append(key)
        elif key == _USER_PREFS_KEY:  # nested: {"user_prefs": {...}}
            if not isinstance(value, dict):
                raise HTTPException(422, "user_prefs: オブジェクトで指定してください")
            prefs_changes.update(value)
            audit_keys.append(_USER_PREFS_KEY)
        elif key.startswith("user_prefs.") and len(key) > len("user_prefs."):
            prefs_changes[key.split(".", 1)[1]] = value
            audit_keys.append(key)
        else:
            raise HTTPException(422, f"未知の設定キー: {key}")

    # ---- フェーズ2: 適用（全検証通過後にのみ書き込む） ----
    if ai_secret is not None:
        _upsert_setting(db, _AI_KEY, crypto.encrypt(ai_secret), user.username)
    if retention is not None:
        _upsert_setting(db, _RETENTION_KEY, str(retention), user.username)
    if notify_changes:
        merged = _current_notify(db)  # 既定+既存の上に差分を重ねる（両フラグを常に保持）
        merged.update(notify_changes)
        _store_json_setting(db, _NOTIFY_KEY, merged, user.username)
    if prefs_changes:
        merged = _load_json_setting(db, _USER_PREFS_KEY, {})
        merged.update(prefs_changes)
        _store_json_setting(db, _USER_PREFS_KEY, merged, user.username)

    # 監査行を同一トランザクションに含める（commit後のaudit失敗で監査欠落を防ぐ。#35/#63）
    audit_add(db, user, "settings_update", ", ".join(audit_keys))
    try:
        db.commit()
    except IntegrityError:
        # keyがPKのため通常は到達しないが、万一の同時INSERT衝突を500にしない保険
        db.rollback()
        raise HTTPException(409, "設定が競合しました。再試行してください。") from None

    return _settings_payload(db)


async def _anthropic_check(api_key: str) -> dict:
    """Anthropic /v1/models へ疎通し、キーの有効性を確かめる。

    キー値は x-api-key ヘッダのみで送り、戻り値・例外・ログには一切出さない。
    ネットワーク/タイムアウト等は ok:false へ縮退させ、500 を出さない。
    """
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_ANTHROPIC_MODELS_URL, headers=headers)
    except Exception:  # noqa: BLE001 - 疎通不能は隠さず ok:false で返す（キー値は出さない）
        return {"ok": False, "error": "接続に失敗しました。ネットワークを確認してください。"}
    if resp.status_code == 200:
        try:
            models = [m.get("id") for m in (resp.json().get("data") or [])][:3]
        except (ValueError, TypeError, AttributeError):
            models = []  # 応答本文が想定外でも ok は維持
        return {"ok": True, "models": models}
    if resp.status_code in (401, 403):
        return {"ok": False, "error": "認証失敗（APIキーが無効です）"}
    return {"ok": False, "error": f"予期しない応答（HTTP {resp.status_code}）"}


class AiKeyTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str | None = None


@router.post("/admin/settings/ai/test")
async def test_ai_key(req: AiKeyTest | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require_role("admin"))):
    # body指定があればその値、なければ保存済みキーを復号して使用
    api_key = (req.api_key.strip() if req and req.api_key else "") or None
    if api_key is None:
        row = db.get(AppSetting, _AI_KEY)
        api_key = crypto.decrypt(row.value) if row and row.value else None
    if not api_key:
        return {"ok": False, "error": "APIキーが未設定です"}
    return await _anthropic_check(api_key)


@router.delete("/admin/settings/ai")
def delete_ai_key(db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin"))):
    row = db.get(AppSetting, _AI_KEY)
    if row is not None:
        db.delete(row)
        # 削除（ドメイン変更）と監査を同一トランザクションでcommit
        audit_add(db, user, "ai_key_removed", _AI_KEY)
        db.commit()
    return {"ok": True, "ai": {"configured": False, "masked": None}}


# ---------- 作業種別マスタ ----------
@router.get("/work-types")
def list_work_types(db: Session = Depends(get_db)):
    rows = db.scalars(select(WorkType).order_by(WorkType.id)).all()
    return [{"id": w.id, "name": w.name, "color": w.color} for w in rows]


# ---------- 現場 書き込み（登録/更新/無効化） ----------
class SiteCreate(BaseModel):
    name: str
    site_code: str | None = None
    loc: str = ""
    latitude: float
    longitude: float
    work_type: str
    project_type: str = "公共"
    river_work_flag: bool = False
    river_state: str = "none"
    river_note: str = "近接なし"
    flood_info: bool = False
    manager: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not v or not v.strip():
            raise ValueError("現場名は必須です")
        return v.strip()

    @field_validator("work_type")
    @classmethod
    def _work(cls, v):
        if v not in WORK_KEYS:
            raise ValueError(f"work_type は {sorted(WORK_KEYS)} のいずれか")
        return v

    @field_validator("river_state")
    @classmethod
    def _rstate(cls, v):
        if v not in RIVER_STATES:
            raise ValueError(f"river_state は {sorted(RIVER_STATES)} のいずれか")
        return v

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v):
        if not -90 <= v <= 90:
            raise ValueError("緯度は -90〜90")
        return v

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v):
        if not -180 <= v <= 180:
            raise ValueError("経度は -180〜180")
        return v

    @model_validator(mode="after")
    def _no_html(self):
        # 名称等の HTML 危険文字を入力境界で拒否（XSS 多層防御。地図ポップアップ等を保護）
        for f in ("name", "loc", "manager", "site_code"):
            v = getattr(self, f, None)
            if isinstance(v, str) and ("<" in v or ">" in v):
                raise ValueError("名称・所在地等に < > は使用できません")
        return self


class SiteUpdate(BaseModel):
    name: str | None = None
    loc: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    work_type: str | None = None
    project_type: str | None = None
    river_work_flag: bool | None = None
    river_state: str | None = None
    river_note: str | None = None
    flood_info: bool | None = None
    manager: str | None = None
    status: str | None = None


@router.post("/sites", status_code=201)
def create_site(req: SiteCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin", "tech_manager"))):
    def _build():
        sid = _allocate_id(db, Site, "S", 2)
        site = Site(
            id=sid, site_code=req.site_code or f"CW-{sid}", name=req.name, loc=req.loc,
            latitude=req.latitude, longitude=req.longitude, work_type=req.work_type,
            project_type=req.project_type, river_work_flag=req.river_work_flag,
            river_state=req.river_state, river_note=req.river_note, flood_info=req.flood_info,
            manager=req.manager, status="active",
        )
        db.add(site)
        return site

    site = _commit_with_retry(db, _build)
    audit(db, user, "site_create", f"{site.id} {site.name}", site_id=site.id)
    return {"id": site.id, "code": site.site_code, "name": site.name, "status": "created"}


@router.put("/sites/{site_id}")
def update_site(site_id: str, req: SiteUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    data = req.model_dump(exclude_none=True)
    if "work_type" in data and data["work_type"] not in WORK_KEYS:
        raise HTTPException(422, "invalid work_type")
    if "river_state" in data and data["river_state"] not in RIVER_STATES:
        raise HTTPException(422, "invalid river_state")
    for k, v in data.items():
        setattr(site, k, v)
    db.commit()
    audit(db, user, "site_update", f"{site_id} {','.join(data.keys())}", site_id=site_id)
    return {"id": site.id, "status": "updated"}


@router.delete("/sites/{site_id}")
def deactivate_site(site_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site.status = "inactive"
    db.commit()
    audit(db, user, "site_deactivate", f"{site_id} {site.name}", site_id=site_id)
    return {"id": site.id, "status": "inactive"}


# ---------- ダッシュボード ----------
@router.get("/dashboard/site-risk")
async def dashboard_site_risk(db: Session = Depends(get_db)):
    sites = db.scalars(
        select(Site).where(Site.status == "active").order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites))
    counts = [0, 0, 0, 0]
    for c in cards:
        counts[c["level"]] += 1
    return {"summary": counts, "sites": cards}


@router.get("/dashboard/data-sources")
def dashboard_data_sources(db: Session = Depends(get_db)):
    rows = db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.id)).all()
    return [{"id": d.id, "name": d.name, "kind": d.kind, "status": d.status,
             "lastOk": d.last_ok, "fails": d.fails, "ms": d.avg_ms,
             "trust": d.trust, "note": d.note} for d in rows]


# ---------- 気象 ----------
@router.get("/weather/timeseries")
async def weather_timeseries(site_id: str = Query(...), db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    data = await assessment._cached_fetch(site.latitude, site.longitude, site.id)
    return {"siteId": site.id, "source": open_meteo.SOURCE_ID, "status": data.get("status"),
            "fetchedAt": data.get("fetched_at"), "points": data.get("points", [])[:24]}


# ---------- 作業判断 ----------
class EvaluateReq(BaseModel):
    site_id: str
    work_type: str
    start: str | None = None
    end: str | None = None


def _persist_decision_result(db: Session, site_id: str, work_type: str, res: dict,
                             thresholds: dict | None = None) -> str:
    """判定結果と理由を永続化し、結果IDを返す（監査・実績分析の正本。設計§6.2.11/§6.2.12）。
    #49: 採番〜INSERTを _commit_with_retry() でラップし、同時リクエストによるID重複を防ぐ。"""
    def _build():
        rid = _allocate_id(db, DecisionResult, "DR", 5)
        result = DecisionResult(
            id=rid, site_id=site_id, work_type=work_type,
            evaluated_at=datetime.now(assessment.JST).strftime("%m/%d %H:%M"),
            overall_level=res["overall_level"], overall_label=res["overall_label"],
            summary=res["summary"], data_quality_summary=res["data_quality_summary"],
            weather_status=res.get("weatherStatus", ""),
            thresholds_json=json.dumps(thresholds or {}, ensure_ascii=False))
        db.add(result)
        for i, r in enumerate(res.get("reasonsRaw", []), 1):
            db.add(DecisionReason(
                id=f"{rid}-{i:02d}", decision_result_id=rid, severity=r["severity"],
                reason_code=r["reason_code"], message=r["message"],
                source_id=r["source_id"], observed_value=r["observed_value"]))
        return result

    result = _commit_with_retry(db, _build)
    return result.id


@router.post("/decisions/evaluate")
async def evaluate_decision(req: EvaluateReq, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    # 永続化する判定は閾値キャッシュをバイパスした fresh 値で評価し、同じ値をスナップショット
    # 保存する(古い閾値での保存を防ぐ)。応答には閾値を含めない(admin読み取り境界の維持)
    th = rules_service.effective_th(fresh=True)
    res = await assessment.assess_decision(site, req.work_type, req.start, req.end, th=th)
    rid = _persist_decision_result(db, site.id, req.work_type, res, thresholds=th)
    audit(db, user, "evaluate", f"{rid} {req.work_type} L{res['overall_level']}", site_id=site.id)
    res["resultId"] = rid
    return res


@router.get("/decision-results/{result_id}")
def get_decision_result(result_id: str, db: Session = Depends(get_db)):
    dr = db.get(DecisionResult, result_id)
    if not dr:
        raise HTTPException(404, "decision result not found")
    return {
        "id": dr.id, "siteId": dr.site_id, "workType": dr.work_type,
        "evaluatedAt": dr.evaluated_at, "overall_level": dr.overall_level,
        "overall_label": dr.overall_label, "summary": dr.summary,
        "data_quality_summary": dr.data_quality_summary, "weatherStatus": dr.weather_status,
        "reasons": [{"severity": r.severity, "reason_code": r.reason_code,
                     "message": r.message, "source_id": r.source_id,
                     "observed_value": r.observed_value} for r in dr.reasons],
    }


# ---------- 作業予定（#16 T1-05・詳細設計§7 /api/work-plans） ----------
WORK_PLAN_STATUSES = {"planned", "done", "postponed", "cancelled"}
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _work_plan_dict(p: WorkPlan) -> dict:
    return {"id": p.id, "siteId": p.site_id, "workType": p.work_type, "title": p.title,
            "plannedStart": p.planned_start, "plannedEnd": p.planned_end,
            "contractor": p.contractor, "summary": p.summary, "status": p.status}


class WorkPlanCreate(BaseModel):
    site_id: str
    work_type: str
    title: str = ""
    planned_start: str
    planned_end: str
    contractor: str = ""
    summary: str = ""
    status: str = "planned"

    @field_validator("work_type")
    @classmethod
    def _work(cls, v):
        if v not in WORK_KEYS:
            raise ValueError(f"work_type は {sorted(WORK_KEYS)} のいずれか")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in WORK_PLAN_STATUSES:
            raise ValueError(f"status は {sorted(WORK_PLAN_STATUSES)} のいずれか")
        return v

    @field_validator("planned_start", "planned_end")
    @classmethod
    def _iso(cls, v):
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("日時は ISO 8601（例: 2026-06-20T08:00）で指定してください") from None
        return v

    @model_validator(mode="after")
    def _order(self):
        # _iso が先に通っているため fromisoformat は必ず成功する（文字列比較はTZ有無混在で逆転し得るため使わない）
        if datetime.fromisoformat(self.planned_end) <= datetime.fromisoformat(self.planned_start):
            raise ValueError("planned_end は planned_start より後である必要があります")
        return self

    @model_validator(mode="after")
    def _no_html(self):
        # XSS多層防御（設計方針に準拠。SiteCreate と同様の入力境界チェック）
        for f in ("title", "contractor", "summary"):
            v = getattr(self, f, None)
            if isinstance(v, str) and ("<" in v or ">" in v):
                raise ValueError("タイトル・協力会社・概要に < > は使用できません")
        return self


class WorkPlanUpdate(BaseModel):
    work_type: str | None = None
    title: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    contractor: str | None = None
    summary: str | None = None
    status: str | None = None

    @field_validator("planned_start", "planned_end")
    @classmethod
    def _iso(cls, v):
        if v is None:
            return v
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("日時は ISO 8601（例: 2026-06-20T08:00）で指定してください") from None
        return v


@router.get("/work-plans")
def list_work_plans(site_id: str | None = None, date: str | None = None,
                    db: Session = Depends(get_db)):
    q = select(WorkPlan).order_by(WorkPlan.id)
    if site_id:
        q = q.where(WorkPlan.site_id == site_id)
    if date:
        if not _DATE_ONLY_RE.match(date):
            raise HTTPException(422, "date は YYYY-MM-DD 形式で指定してください")
        # 日別表示（FR-014）: planned_start の日付部分（YYYY-MM-DD）で絞り込み。
        # 厳密な数字4-2-2形式のみ許可するため LIKE ワイルドカード（% _）が紛れ込む余地はない。
        q = q.where(WorkPlan.planned_start.like(f"{date}%"))
    rows = db.scalars(q).all()
    return [_work_plan_dict(p) for p in rows]


@router.get("/work-plans/{plan_id}")
def get_work_plan(plan_id: str, db: Session = Depends(get_db)):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    return _work_plan_dict(plan)


@router.post("/work-plans", status_code=201)
def create_work_plan(req: WorkPlanCreate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager", "site_manager"))):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")

    def _build():
        plan = WorkPlan(id=_allocate_id(db, WorkPlan, "WP", 2), site_id=req.site_id,
                        work_type=req.work_type, title=req.title,
                        planned_start=req.planned_start, planned_end=req.planned_end,
                        contractor=req.contractor, summary=req.summary, status=req.status)
        db.add(plan)
        return plan

    plan = _commit_with_retry(db, _build)
    audit(db, user, "work_plan_create", f"{plan.id} {plan.title or plan.work_type}", site_id=site.id)
    return {"id": plan.id, "status": "created"}


@router.put("/work-plans/{plan_id}")
def update_work_plan(plan_id: str, req: WorkPlanUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager", "site_manager"))):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    data = req.model_dump(exclude_none=True)
    if "work_type" in data and data["work_type"] not in WORK_KEYS:
        raise HTTPException(422, "invalid work_type")
    if "status" in data and data["status"] not in WORK_PLAN_STATUSES:
        raise HTTPException(422, "invalid status")
    for f in ("title", "contractor", "summary"):
        if f in data and isinstance(data[f], str) and ("<" in data[f] or ">" in data[f]):
            raise HTTPException(422, f"{f} に < > は使用できません")
    start = data.get("planned_start", plan.planned_start)
    end = data.get("planned_end", plan.planned_end)
    if "planned_start" in data or "planned_end" in data:
        try:
            start_at = datetime.fromisoformat(start)
            end_at = datetime.fromisoformat(end)
        except ValueError:
            raise HTTPException(422, "日時は ISO 8601 で指定してください") from None
        if end_at <= start_at:
            raise HTTPException(422, "planned_end は planned_start より後である必要があります")
    for k, v in data.items():
        setattr(plan, k, v)
    db.commit()
    audit(db, user, "work_plan_update", f"{plan_id} {','.join(data.keys())}", site_id=plan.site_id)
    return {"id": plan.id, "status": "updated"}


@router.post("/work-plans/{plan_id}/evaluate")
async def evaluate_work_plan(plan_id: str, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    site = db.get(Site, plan.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    # /api/decisions/evaluate と同じ fresh 閾値・スナップショット経路(第2の永続化パス)
    th = rules_service.effective_th(fresh=True)
    res = await assessment.assess_decision(site, plan.work_type, plan.planned_start,
                                           plan.planned_end, th=th)
    rid = _persist_decision_result(db, site.id, plan.work_type, res, thresholds=th)
    audit(db, user, "evaluate", f"{rid} {plan_id} L{res['overall_level']}", site_id=site.id)
    res["resultId"] = rid
    res["workPlanId"] = plan_id
    return res


# ---------- 判断履歴 ----------
class DecisionLogReq(BaseModel):
    site_id: str
    work_type: str
    level: int = 1
    action: str
    comment: str = ""
    decision_result_id: str | None = None
    # decided_by はクライアントから受け取らず、認証ユーザーから導出（なりすまし防止 #8）


@router.get("/decision-logs")
def list_decision_logs(action: str | None = None, db: Session = Depends(get_db)):
    q = select(DecisionLog).order_by(DecisionLog.id.desc())
    if action and action != "all":
        q = q.where(DecisionLog.action == action)
    rows = db.scalars(q).all()
    return [{"id": h.id, "datetime": h.decided_at, "siteId": h.site_id, "site": h.site_name,
             "workType": h.work_type, "level": h.level, "action": h.action,
             "comment": h.comment, "by": h.decided_by} for h in rows]


@router.post("/decision-logs")
def create_decision_log(req: DecisionLogReq, db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin", "tech_manager", "site_manager", "safety"))):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")

    def _build():
        entry = DecisionLog(
            id=_allocate_id(db, DecisionLog, "L", 2), site_id=site.id, site_name=site.name,
            work_type=req.work_type, level=req.level, action=req.action,
            comment=req.comment or "（メモなし）", decided_by=user.display_name,
            decision_result_id=req.decision_result_id,
            decided_at=datetime.now(assessment.JST).strftime("%m/%d %H:%M"))
        db.add(entry)
        return entry

    entry = _commit_with_retry(db, _build)
    audit(db, user, "decision_log", f"{entry.id} {req.action} L{req.level}", site_id=site.id)
    return {"id": entry.id, "status": "recorded"}


@router.get("/decision-logs/export.csv")
def export_decision_logs(db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    audit(db, user, "csv_export", "decision_logs.csv")
    rows = db.scalars(select(DecisionLog).order_by(DecisionLog.id.desc())).all()
    buf = io.StringIO()
    buf.write("﻿")  # Excel(JP) 用 BOM
    w = csv.writer(buf)
    w.writerow(["decision_log_id", "site_id", "site_name", "work_type", "level",
                "action", "comment", "decided_by", "decided_at"])
    for h in rows:
        w.writerow([h.id, h.site_id, h.site_name, h.work_type, h.level,
                    h.action, h.comment, h.decided_by, h.decided_at])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=decision_logs.csv"})


# ---------- データ取得（手動再取得） ----------
@router.post("/data-collectors/run")
async def run_collectors(db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    audit(db, user, "collectors_run", "手動再取得")
    assessment.clear_cache()
    sites = db.scalars(select(Site).where(Site.status == "active")).all()
    cards = await assessment.assess_all(list(sites))
    ok = sum(1 for c in cards if c["weatherStatus"] == "OK")
    # 全データソースを実プローブして状態を更新（Open-Meteo含む）
    probed = await source_probe.probe_all(db)
    return {"refetched": len(cards), "weatherOk": ok, "total": len(cards),
            "probed": {k: v["status"] for k, v in probed.items()}}


# ---------- 通知（設計§14） ----------
@router.get("/notifications")
async def list_notifications(db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    sites = db.scalars(select(Site).where(Site.status == "active").order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites))
    src = db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.id)).all()
    sources = [{"id": d.id, "name": d.name, "status": d.status,
                "fails": d.fails, "lastOk": d.last_ok} for d in src]
    notifs = notifications.build_notifications(cards, sources)
    return {"count": len(notifs), "notifications": notifs}


# ---------- 監査ログ（管理者・技術管理者） ----------
@router.get("/admin/audit-logs")
def list_audit_logs(limit: int = 100, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin", "tech_manager"))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": r.id, "timestamp": r.timestamp, "user": r.username, "action": r.action,
             "message": r.message, "siteId": r.site_id} for r in rows]
