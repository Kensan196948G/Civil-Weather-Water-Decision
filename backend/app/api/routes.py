"""API ルータ（詳細設計 §7）。WebUI 接続用エンドポイント。"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..core import crypto, ops_status, readiness
from ..core.config import settings
from ..core.db import get_db
from ..core.deps import get_current_user, require_role
from ..core.security import hash_password
from ..models import (
    AppSetting, AuditLog, DataSourceStatus, DecisionLog, DecisionReason, DecisionResult,
    IdCounter, ObservationStation, RiverObservation, Site, SiteLink, SiteStation,
    User, UserSiteAccess, WorkPlan, WorkType,
)
from ..services import assessment, extreme, notifications, rules as rules_service
from ..services import site_access
from ..services.audit import audit, audit_add
from ..services.data_collectors import (
    marine, open_meteo, river_collector, source_probe, wbgt_env,
)

# ルータ全体に認証を必須化（/auth と /health は別ルータ/main で公開）
router = APIRouter(dependencies=[Depends(get_current_user)])

WORK_KEYS = {"river", "concrete", "earthwork", "pavement", "crane", "heat", "marine"}
RIVER_STATES = {"none", "stable", "rising", "stale"}
USER_ROLES = {"admin", "tech_manager", "site_manager", "safety", "viewer"}
LEVEL_LABELS = {0: "通常", 1: "注意", 2: "中止検討", 3: "確認不能"}
ACTION_LABELS = {
    "execute": "実施", "postpone": "延期", "cancel": "中止",
    "monitor": "監視継続", "other": "その他",
}
_ID_COMMIT_ATTEMPTS = 5

# 帳票PDF用の日本語フォント（SIL OFL 1.1 で同梱。self-host 方針に合わせ CDN/システムフォント非依存）
_PDF_FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "MPlus1p-Regular.ttf"
_PDF_FONT_REGISTERED = False


def _pdf_font() -> str:
    """PDF用日本語TTFを一度だけ登録してフォント名を返す。"""
    global _PDF_FONT_REGISTERED
    if not _PDF_FONT_REGISTERED:
        pdfmetrics.registerFont(TTFont("MPlus1p", str(_PDF_FONT_PATH)))
        _PDF_FONT_REGISTERED = True
    return "MPlus1p"


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
_COUNTER_SPECS = ((Site, "S"), (DecisionResult, "DR"), (WorkPlan, "WP"),
                  (DecisionLog, "L"), (SiteLink, "SL"),
                  (ObservationStation, "OS"), (SiteStation, "SS"),
                  (RiverObservation, "RO"), (User, "U"))

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
        except Exception:
            # 非DB例外（監査書き込み失敗等）でも helper 内でトランザクション後始末を完結させる。
            # 現行は get_db() の close() でも rollback されるが、将来の長寿命 Session 利用時に
            # pending 変更が後続 commit へ混入しない防御（#63 対抗レビュー[low]）
            db.rollback()
            raise
    raise HTTPException(409, "同時登録が競合しました。再試行してください。")


# ---------- 現場 ----------
@router.get("/sites")
def list_sites(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    accessible = site_access.accessible_site_ids(db, user)
    sites = db.scalars(
        select(Site).where(Site.id.in_(accessible)).order_by(Site.id)).all()
    return [{"id": s.id, "code": s.site_code, "name": s.name, "loc": s.loc,
             "lat": s.latitude, "lon": s.longitude, "work": s.work_type,
             "project": s.project_type, "riverWork": s.river_work_flag,
             "manager": s.manager, "status": s.status} for s in sites]


@router.get("/sites/{site_id}")
async def get_site(site_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_read(db, user, site_id)
    card = await assessment.assess_site(site, db=db)
    plans = []
    for p in site.plans:
        d = await assessment.assess_decision(site, p.work_type, p.planned_start,
                                             p.planned_end, db=db)
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
            "links": [_site_link_dict(ln) for ln in site.links],
            "history": [{"datetime": h.decided_at, "action": h.action, "level": h.level,
                         "comment": h.comment, "by": h.decided_by} for h in history]}


@router.get("/sites/{site_id}/stations")
def site_stations(site_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_read(db, user, site_id)
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
_AI_PROVIDER_KEY = "ai_provider"
_NOTIFY_KEY = "notify"
_RETENTION_KEY = "data_retention_days"
_USER_PREFS_KEY = "user_prefs"
_RETENTION_DEFAULT = 365
_RETENTION_MIN, _RETENTION_MAX = 30, 3650
# 通知/ユーザー設定JSONの肥大化・悪用の保険（厳格スキーマ済みだが直列化サイズの保険）
_SETTINGS_JSON_MAX = 8192
# AI API 疎通確認先。キー値は認証ヘッダのみで送り、応答・ログ・監査には出さない
_AI_PROVIDERS = ("deepseek", "anthropic")
_AI_PROVIDER_DEFAULT = "deepseek"  # #72 段8: 既定を DeepSeek へ変更（2026-08-09 ユーザー指示）
_DEEPSEEK_MODELS_URL = "https://api.deepseek.com/models"
_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
_AI_TIMEOUT_SECONDS = 6  # 外部呼び出しの上限（#80 medium-2: 6秒）

# ai/test のプロセス内レート制限（#80 medium-2）。ユーザー単位 5回/60秒。本番はRedis等へ。
_AI_TEST_MAX = 5
_AI_TEST_WINDOW_SECONDS = 60
_ai_test_calls: dict[str, list[float]] = {}


class NotifySettings(BaseModel):
    """通知設定（固定2フラグ・厳格スキーマ）。任意ネスト dict の保存を禁じ、
    保存型XSSの入口を作らない（#80 medium-1）。"""
    model_config = ConfigDict(extra="forbid")
    slack_enabled: bool = False
    teams_enabled: bool = False


class UserPrefs(BaseModel):
    """ユーザー設定。現時点で許可キーなし（{} のみ受理）。将来キーを追加する際は、
    ここにフィールドを定義し個別に型・値域を検証すること（生 dict は保存しない。#80 medium-1）。"""
    model_config = ConfigDict(extra="forbid")


class SettingsUpdate(BaseModel):
    """設定の部分更新リクエスト（nested・厳格）。未知トップレベルキーは422。"""
    model_config = ConfigDict(extra="forbid")
    ai_api_key: str | None = None
    ai_provider: str | None = None
    notify: NotifySettings | None = None
    data_retention_days: int | None = None
    user_prefs: UserPrefs | None = None

    @field_validator("ai_provider", mode="before")
    @classmethod
    def _reject_bad_provider(cls, v):
        if v is None:
            return v
        v = (v or "").strip()
        if v not in _AI_PROVIDERS:
            raise ValueError(f"ai_provider は {sorted(_AI_PROVIDERS)} のいずれか")
        return v

    @field_validator("data_retention_days", mode="before")
    @classmethod
    def _reject_non_int(cls, v):
        if v is None:
            return v
        # int型そのもの以外（bool / 文字列"100" / 浮動小数100.0）を拒否（#80 low-2）。
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("data_retention_days: 整数（30〜3650）で指定してください")
        if not (_RETENTION_MIN <= v <= _RETENTION_MAX):
            raise ValueError(
                f"data_retention_days: {_RETENTION_MIN}〜{_RETENTION_MAX} の整数で指定してください")
        return v


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


def _current_ai_provider(db: Session) -> str:
    row = db.get(AppSetting, _AI_PROVIDER_KEY)
    if row is None or not row.value or row.value not in _AI_PROVIDERS:
        return _AI_PROVIDER_DEFAULT
    return row.value


def _ai_status(db: Session) -> dict:
    """AI APIキーの設定状態。configured と末尾4桁マスクのみ返す（平文は返さない）。"""
    row = db.get(AppSetting, _AI_KEY)
    if row is None or not row.value:
        return {"configured": False, "masked": None, "provider": _current_ai_provider(db)}
    plain = crypto.decrypt(row.value)
    if plain is None:
        # JWT_SECRET変更後などで復号不能 → 未設定扱いへ安全縮退（500にしない）
        return {"configured": False, "masked": None, "provider": _current_ai_provider(db)}
    return {"configured": True, "masked": _mask_tail(plain),
            "provider": _current_ai_provider(db)}


def _current_retention(db: Session) -> int:
    row = db.get(AppSetting, _RETENTION_KEY)
    if row is None or not row.value:
        return _RETENTION_DEFAULT
    try:
        return int(row.value)
    except (ValueError, TypeError):
        return _RETENTION_DEFAULT


def _current_notify(db: Session) -> dict:
    """通知設定を厳格スキーマに通して返す（過去の不正値・未知キーは落ちる。#80 medium-1）。"""
    stored = _load_json_setting(db, _NOTIFY_KEY, {})
    known = {k: stored[k] for k in NotifySettings.model_fields if k in stored}
    try:
        return NotifySettings.model_validate(known).model_dump()
    except ValidationError:
        return NotifySettings().model_dump()  # 不正値は既定 false/false へ縮退


def _current_user_prefs(db: Session) -> dict:
    """ユーザー設定を厳格スキーマに通して返す（許可キー外は落ちる。#80 medium-1）。"""
    stored = _load_json_setting(db, _USER_PREFS_KEY, {})
    try:
        return UserPrefs.model_validate(stored).model_dump()
    except ValidationError:
        return UserPrefs().model_dump()


def _settings_payload(db: Session) -> dict:
    """設定画面の表示用ペイロード（GET応答・PUT応答で共通。AI平文は含めない）。"""
    return {
        "ai": _ai_status(db),
        "notify": _current_notify(db),
        "data_retention_days": _current_retention(db),
        "user_prefs": _current_user_prefs(db),
    }


@router.get("/admin/settings")
def get_settings(db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    return _settings_payload(db)


def _store_json_setting(db: Session, key: str, obj: dict, username: str) -> None:
    """JSON設定を直列化・サイズ検証してUPSERT（commitしない。肥大化を防ぐ）。"""
    serialized = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > _SETTINGS_JSON_MAX:
        raise HTTPException(422, f"{key}: 設定が大きすぎます")
    _upsert_setting(db, key, serialized, username)


@router.put("/admin/settings")
def put_settings(req: SettingsUpdate, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    """設定の部分更新（nested・厳格スキーマ）。明示されたフィールドのみ更新する。
    変更は監査行と同一トランザクションで commit（#35/#63: 監査なき設定変更を残さない）。
    """
    # 明示指定（None は未指定扱い）されたフィールドのみ更新
    provided = {k for k in req.model_fields_set if getattr(req, k) is not None}
    if not provided:
        raise HTTPException(422, "更新するフィールドがありません")

    audit_keys: list[str] = []  # 監査にはキー名のみ（秘密値・マスクも載せない。#80 low-1）
    if "ai_api_key" in provided:
        secret = req.ai_api_key.strip()
        if not secret:
            # 空値での上書きは不可。解除は DELETE /admin/settings/ai を使う
            raise HTTPException(422, "ai_api_key: 空にできません（解除は DELETE を使用してください）")
        if not crypto.encryption_is_strong():
            # 弱鍵のまま平文同然で保存しない（#80 high-2）。メッセージに秘密値は含めない。
            raise HTTPException(
                422, "暗号鍵が未設定のため保存できません。"
                     "本番は SETTINGS_ENCRYPTION_KEY(32バイト以上)、local は専用鍵または強い JWT_SECRET を設定してください")
        _upsert_setting(db, _AI_KEY, crypto.encrypt(secret), user.username)
        audit_keys.append(f"{_AI_KEY}(updated)")  # マスク値も載せない（tech_manager露出防止）
    if "ai_provider" in provided:
        _upsert_setting(db, _AI_PROVIDER_KEY, req.ai_provider, user.username)
        audit_keys.append(f"{_AI_PROVIDER_KEY}={req.ai_provider}")
    if "data_retention_days" in provided:
        _upsert_setting(db, _RETENTION_KEY, str(req.data_retention_days), user.username)
        audit_keys.append(f"{_RETENTION_KEY}={req.data_retention_days}")
    if "notify" in provided:  # 厳格スキーマ済み（両フラグを完全置換）
        _store_json_setting(db, _NOTIFY_KEY, req.notify.model_dump(), user.username)
        audit_keys.append(_NOTIFY_KEY)
    if "user_prefs" in provided:
        _store_json_setting(db, _USER_PREFS_KEY, req.user_prefs.model_dump(), user.username)
        audit_keys.append(_USER_PREFS_KEY)

    # 監査行を同一トランザクションに含める（commit後のaudit失敗で監査欠落を防ぐ。#35/#63）
    audit_add(db, user, "settings_update", ", ".join(audit_keys))
    try:
        db.commit()
    except IntegrityError:
        # keyがPKのため通常は到達しないが、万一の同時INSERT衝突を500にしない保険
        db.rollback()
        raise HTTPException(409, "設定が競合しました。再試行してください。") from None

    return _settings_payload(db)


async def _provider_check(api_key: str, provider: str) -> dict:
    """DeepSeek / Anthropic の /models へ疎通し、キーの有効性を確かめる。

    キー値は認証ヘッダのみで送り、戻り値・例外・ログには一切出さない。
    ネットワーク/タイムアウト等は ok:false へ縮退させ、500 を出さない。
    """
    if provider == "anthropic":
        url = _ANTHROPIC_MODELS_URL
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        url = _DEEPSEEK_MODELS_URL
        headers = {"Authorization": "Bearer " + api_key}
    try:
        async with httpx.AsyncClient(timeout=_AI_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
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
    provider: str | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def _provider(cls, v):
        if v is None:
            return v
        v = (v or "").strip()
        if v not in _AI_PROVIDERS:
            raise ValueError(f"provider は {sorted(_AI_PROVIDERS)} のいずれか")
        return v


def _ai_test_rate_limited(username: str) -> bool:
    """ai/test のユーザー単位スライディングウィンドウ制限（#80 medium-2）。超過なら True。

    外部（Anthropic）への無制限・無監査な疎通確認オラクル化を防ぐ。プロセス内メモリのため
    PoC 相当（本番は Redis 等の共有ストアへ）。auth.py のログイン試行制限と同方針。
    """
    now = time.monotonic()
    recent = [t for t in _ai_test_calls.get(username, []) if now - t < _AI_TEST_WINDOW_SECONDS]
    if len(recent) >= _AI_TEST_MAX:
        _ai_test_calls[username] = recent  # 期限切れを掃除しつつ据え置き（新規は数えない）
        return True
    recent.append(now)
    _ai_test_calls[username] = recent
    return False


@router.post("/admin/settings/ai/test")
async def test_ai_key(req: AiKeyTest | None = None, db: Session = Depends(get_db),
                      user: User = Depends(require_role("admin"))):
    # 外部呼び出しオラクル化を防ぐレート制限（#80 medium-2）。HTTPは常に200で ok:false を返す。
    if _ai_test_rate_limited(user.username):
        return {"ok": False, "error": "試行回数が多すぎます。しばらく待って再試行してください。"}
    # body指定があればその値、なければ保存済みキーを復号して使用
    api_key = (req.api_key.strip() if req and req.api_key else "") or None
    if api_key is None:
        row = db.get(AppSetting, _AI_KEY)
        api_key = crypto.decrypt(row.value) if row and row.value else None
    if not api_key:
        return {"ok": False, "error": "APIキーが未設定です"}
    provider = (req.provider.strip() if req and req.provider else "") or _current_ai_provider(db)
    result = await _provider_check(api_key, provider)
    # テスト実施を監査（キー値・マスクは載せない。単独書き込みのため自己コミットの audit() 可。#80 medium-2）
    audit(db, user, "ai_key_test", "ok" if result.get("ok") else "ng")
    return result


@router.delete("/admin/settings/ai")
def delete_ai_key(db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin"))):
    row = db.get(AppSetting, _AI_KEY)
    if row is not None:
        db.delete(row)
        # 削除（ドメイン変更）と監査を同一トランザクションでcommit
        audit_add(db, user, "ai_key_removed", _AI_KEY)
        db.commit()
    return {"ok": True, "ai": {"configured": False, "masked": None,
                                "provider": _current_ai_provider(db)}}


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
        # 監査行を同一トランザクションへ（#63: commit後のaudit失敗で作成済みリソースが500になり、
        # クライアント再試行で二重登録するのを防ぐ）。リトライ時はrollbackで監査行も破棄され重複しない
        audit_add(db, user, "site_create", f"{site.id} {site.name}", site_id=site.id)
        return site

    site = _commit_with_retry(db, _build)
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
    # 監査行を更新と同一commitへ（#63: commit後のaudit失敗で「更新は永続・監査は欠落」を防ぐ）
    audit_add(db, user, "site_update", f"{site_id} {','.join(data.keys())}", site_id=site_id)
    db.commit()
    return {"id": site.id, "status": "updated"}


@router.delete("/sites/{site_id}")
def deactivate_site(site_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site.status = "inactive"
    # 監査行を無効化と同一commitへ（#63）
    audit_add(db, user, "site_deactivate", f"{site_id} {site.name}", site_id=site_id)
    db.commit()
    return {"id": site.id, "status": "inactive"}


# ---------- 現場別 公式リンク（#30 T2-02 / FR-035: 川の防災情報リンク管理） ----------
SITE_LINK_KINDS = {"river", "weather", "wbgt", "disaster", "other"}
_SITE_LINKS_MAX = 20  # 1現場あたりの上限（応答肥大化・DoS防止。対抗レビュー[medium]）


def _validate_https_url(v: str) -> str:
    """https のみ許可し javascript:/data:/http 等の危険・非暗号スキームや制御文字を拒否する。

    リンクは現場詳細でクリック導線として使われるため、スキームを厳格に検証する
    （危険スキーム/XSS 多層防御。SiteCreate 等の入力境界チェックと同方針）。
    """
    u = (v or "").strip()
    if not u:
        raise ValueError("URL は必須です")
    if len(u) > 500:
        raise ValueError("URL は 500 文字以内で指定してください")
    # 空白・制御文字（C0/DEL/C1 含む）の埋め込みはスキーム偽装/ヘッダインジェクションの温床のため拒否
    if any(c.isspace() or ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f for c in u):
        raise ValueError("URL に空白・制御文字は使用できません")
    # バックスラッシュはブラウザ/パーサ差で "/" と解釈されホスト偽装に使われるため拒否（対抗レビュー[high]）
    if "\\" in u:
        raise ValueError("URL にバックスラッシュは使用できません")
    parsed = urlparse(u)
    # scheme を小文字化して比較（"HTTPS" も許容しつつ http/javascript/data は拒否）
    if parsed.scheme.lower() != "https":
        raise ValueError("URL は https:// で始まる必要があります")
    # userinfo（https://river.go.jp@evil.example）は公式ドメインなりすましに使われるため拒否
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL にユーザー情報（@）は使用できません")
    if not parsed.hostname:
        raise ValueError("URL のホストが指定されていません")
    return u


def _validate_link_label(v: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError("リンク名称は必須です")
    if len(v) > 100:
        raise ValueError("リンク名称は 100 文字以内で指定してください")
    if "<" in v or ">" in v:  # XSS 多層防御（名称は現場詳細に表示される）
        raise ValueError("名称に < > は使用できません")
    return v


class SiteLinkCreate(BaseModel):
    label: str
    url: str
    kind: str = "river"
    sort_order: int = 0

    @field_validator("label")
    @classmethod
    def _label(cls, v):
        return _validate_link_label(v)

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        return _validate_https_url(v)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in SITE_LINK_KINDS:
            raise ValueError(f"kind は {sorted(SITE_LINK_KINDS)} のいずれか")
        return v


class SiteLinkUpdate(BaseModel):
    label: str | None = None
    url: str | None = None
    kind: str | None = None
    sort_order: int | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, v):
        return v if v is None else _validate_link_label(v)

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        return v if v is None else _validate_https_url(v)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v is not None and v not in SITE_LINK_KINDS:
            raise ValueError(f"kind は {sorted(SITE_LINK_KINDS)} のいずれか")
        return v


def _site_link_dict(ln: SiteLink) -> dict:
    return {"id": ln.id, "siteId": ln.site_id, "label": ln.label,
            "url": ln.url, "kind": ln.kind, "sortOrder": ln.sort_order}


@router.get("/sites/{site_id}/links")
def list_site_links(site_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_read(db, user, site_id)
    rows = db.scalars(select(SiteLink).where(SiteLink.site_id == site_id)
                      .order_by(SiteLink.sort_order, SiteLink.id)).all()
    return [_site_link_dict(ln) for ln in rows]


@router.post("/sites/{site_id}/links", status_code=201)
def create_site_link(site_id: str, req: SiteLinkCreate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    # 上限・重複チェック（対抗レビュー[medium]: 無制限登録による肥大化と同一URLの多重登録を防ぐ）
    count = db.scalar(select(func.count()).select_from(SiteLink)
                      .where(SiteLink.site_id == site_id))
    if count >= _SITE_LINKS_MAX:
        raise HTTPException(422, f"リンクは1現場あたり最大 {_SITE_LINKS_MAX} 件までです")
    dup = db.scalar(select(SiteLink).where(SiteLink.site_id == site_id,
                                           SiteLink.url == req.url))
    if dup:
        raise HTTPException(409, "同じURLのリンクが既に登録されています")

    def _build():
        link = SiteLink(id=_allocate_id(db, SiteLink, "SL", 3), site_id=site_id,
                        label=req.label, url=req.url, kind=req.kind, sort_order=req.sort_order)
        db.add(link)
        # 監査行を同一トランザクションへ（#63 と同方式。commit後のaudit失敗による不整合を防ぐ）
        audit_add(db, user, "site_link_create", f"{link.id} {site_id} {req.kind}", site_id=site_id)
        return link

    link = _commit_with_retry(db, _build)
    return {"id": link.id, "status": "created"}


@router.put("/site-links/{link_id}")
def update_site_link(link_id: str, req: SiteLinkUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager"))):
    link = db.get(SiteLink, link_id)
    if not link:
        raise HTTPException(404, "site link not found")
    data = req.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(422, "更新内容がありません")
    if "url" in data:  # URL変更時も同一現場内の重複を防ぐ（対抗レビュー[medium]）
        dup = db.scalar(select(SiteLink).where(SiteLink.site_id == link.site_id,
                                               SiteLink.url == data["url"],
                                               SiteLink.id != link_id))
        if dup:
            raise HTTPException(409, "同じURLのリンクが既に登録されています")
    for k, v in data.items():
        setattr(link, k, v)
    # 監査行を更新と同一commitへ（#63 と同方式）
    audit_add(db, user, "site_link_update", f"{link_id} {','.join(data.keys())}", site_id=link.site_id)
    db.commit()
    return {"id": link.id, "status": "updated"}


@router.delete("/site-links/{link_id}")
def delete_site_link(link_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager"))):
    link = db.get(SiteLink, link_id)
    if not link:
        raise HTTPException(404, "site link not found")
    site_id = link.site_id  # delete 後は参照できないため先に確保
    # 監査行を削除と同一commitへ（#63 と同方式）
    audit_add(db, user, "site_link_delete", f"{link_id} {site_id}", site_id=site_id)
    db.delete(link)
    db.commit()
    return {"id": link_id, "status": "deleted"}


# ---------- 河川観測所マスタ・現場紐付け・実測値（#29/#31 T2-01/T2-03） ----------
# 2026-08 時点の到達点: 観測所マスタ・現場紐付け・手動実測値の保存/時系列APIまで実装。
# 自動取得（水防災オープンデータ提供サービス等）は未接続のため、API 応答とUIで
# 「自動取得は未接続」を明示し、実測値と判定の取り違えを防ぐ（外部評価 P0 対応）。
OBS_KINDS = {"water", "rain", "water_rain", "wbgt"}
SITE_STATION_RELS = {"upstream", "nearest", "reference"}
OBS_QUALITIES = {"OK", "MISSING", "STALE", "ERROR"}
_SITE_STATIONS_MAX = 8  # 1現場あたりの観測所紐付け上限（応答肥大化・設定ミス防止）
_OBS_NOTE_MAX = 200


def _clean_station_text(v, max_len: int, label: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{label} は必須です")
    if len(v) > max_len:
        raise ValueError(f"{label} は {max_len} 文字以内で指定してください")
    if "<" in v or ">" in v:
        raise ValueError(f"{label} に < > は使用できません")
    return v


def _optional_float(v, label: str, lo: float, hi: float) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{label} は数値で指定してください")
    if not (lo <= float(v) <= hi):
        raise ValueError(f"{label} は {lo}〜{hi} の範囲で指定してください")
    return float(v)


def _normalize_observed_at(v: str | None) -> str:
    """観測時刻を JST の "%Y-%m-%d %H:%M:%S" へ正規化（未指定なら現在時刻）。"""
    if not v:
        return datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at は ISO 8601 形式で指定してください") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assessment.JST)
    return dt.astimezone(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")


class ObservationStationCreate(BaseModel):
    source_id: str = "MANUAL"
    station_code: str
    name: str
    agency: str = ""
    basin_name: str = ""
    kind: str = "water"
    latitude: float | None = None
    longitude: float | None = None

    @field_validator("source_id")
    @classmethod
    def _source(cls, v):
        return _clean_station_text(v, 40, "source_id")

    @field_validator("station_code")
    @classmethod
    def _code(cls, v):
        return _clean_station_text(v, 50, "station_code")

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return _clean_station_text(v, 200, "name")

    @field_validator("agency")
    @classmethod
    def _agency(cls, v):
        v = (v or "").strip()
        if len(v) > 100:
            raise ValueError("agency は 100 文字以内で指定してください")
        return v

    @field_validator("basin_name")
    @classmethod
    def _basin(cls, v):
        v = (v or "").strip()
        if len(v) > 100:
            raise ValueError("basinName は 100 文字以内で指定してください")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in OBS_KINDS:
            raise ValueError(f"kind は {sorted(OBS_KINDS)} のいずれか")
        return v

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v):
        return _optional_float(v, "latitude", -90.0, 90.0)

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v):
        return _optional_float(v, "longitude", -180.0, 180.0)


class ObservationStationUpdate(BaseModel):
    source_id: str | None = None
    station_code: str | None = None
    name: str | None = None
    agency: str | None = None
    basin_name: str | None = None
    kind: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    status: str | None = None

    @field_validator("source_id")
    @classmethod
    def _source(cls, v):
        return v if v is None else _clean_station_text(v, 40, "source_id")

    @field_validator("station_code")
    @classmethod
    def _code(cls, v):
        return v if v is None else _clean_station_text(v, 50, "station_code")

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return v if v is None else _clean_station_text(v, 200, "name")

    @field_validator("agency")
    @classmethod
    def _agency(cls, v):
        if v is None:
            return None
        v = v.strip()
        if len(v) > 100:
            raise ValueError("agency は 100 文字以内で指定してください")
        return v

    @field_validator("basin_name")
    @classmethod
    def _basin(cls, v):
        if v is None:
            return None
        v = v.strip()
        if len(v) > 100:
            raise ValueError("basinName は 100 文字以内で指定してください")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v is not None and v not in OBS_KINDS:
            raise ValueError(f"kind は {sorted(OBS_KINDS)} のいずれか")
        return v

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v):
        return _optional_float(v, "latitude", -90.0, 90.0)

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v):
        return _optional_float(v, "longitude", -180.0, 180.0)

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in {"active", "inactive"}:
            raise ValueError("status は active/inactive のいずれか")
        return v


class SiteStationLink(BaseModel):
    station_id: str
    rel: str = "nearest"
    sort_order: int = 0

    @field_validator("station_id")
    @classmethod
    def _station(cls, v):
        return _clean_station_text(v, 20, "station_id")

    @field_validator("rel")
    @classmethod
    def _rel(cls, v):
        if v not in SITE_STATION_RELS:
            raise ValueError(f"rel は {sorted(SITE_STATION_RELS)} のいずれか")
        return v

    @field_validator("sort_order")
    @classmethod
    def _sort(cls, v):
        if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 100):
            raise ValueError("sort_order は 0〜100 の整数で指定してください")
        return v


class RiverObservationCreate(BaseModel):
    station_id: str
    observed_at: str | None = None
    water_level_m: float | None = None
    rainfall_mm_h: float | None = None
    quality: str = "OK"
    note: str = ""

    @field_validator("station_id")
    @classmethod
    def _station(cls, v):
        return _clean_station_text(v, 20, "station_id")

    @field_validator("observed_at")
    @classmethod
    def _time(cls, v):
        return v if v is None else _normalize_observed_at(v)

    @field_validator("water_level_m")
    @classmethod
    def _level(cls, v):
        return _optional_float(v, "water_level_m", -5.0, 100.0)

    @field_validator("rainfall_mm_h")
    @classmethod
    def _rain(cls, v):
        return _optional_float(v, "rainfall_mm_h", 0.0, 500.0)

    @field_validator("quality")
    @classmethod
    def _quality(cls, v):
        if v not in OBS_QUALITIES:
            raise ValueError(f"quality は {sorted(OBS_QUALITIES)} のいずれか")
        return v

    @field_validator("note")
    @classmethod
    def _note(cls, v):
        v = (v or "").strip()
        if len(v) > _OBS_NOTE_MAX:
            raise ValueError(f"note は {_OBS_NOTE_MAX} 文字以内で指定してください")
        return v

    @model_validator(mode="after")
    def _has_value(self):
        if self.water_level_m is None and self.rainfall_mm_h is None:
            raise ValueError("water_level_m または rainfall_mm_h の少なくとも一方が必要です")
        return self


class RiverObservationUpdate(RiverObservationCreate):
    """手動入力の修正用。全項目任意（指定された項目だけ更新）。"""

    station_id: str | None = None
    observed_at: str | None = None
    water_level_m: float | None = None
    rainfall_mm_h: float | None = None
    quality: str | None = None
    note: str | None = None

    @field_validator("quality")
    @classmethod
    def _quality(cls, v):
        if v is not None and v not in OBS_QUALITIES:
            raise ValueError(f"quality は {sorted(OBS_QUALITIES)} のいずれか")
        return v

    @model_validator(mode="after")
    def _has_value(self):
        if self.model_dump(exclude_none=True):
            return self
        raise ValueError("更新内容がありません")


def _observation_station_dict(st: ObservationStation) -> dict:
    return {
        "id": st.id, "sourceId": st.source_id, "stationCode": st.station_code,
        "name": st.name, "agency": st.agency, "basinName": st.basin_name,
        "kind": st.kind, "latitude": st.latitude, "longitude": st.longitude,
        "status": st.status, "updatedAt": st.updated_at,
    }


def _latest_observation(db: Session, station_id: str) -> dict | None:
    row = db.scalar(
        select(RiverObservation)
        .where(RiverObservation.station_id == station_id)
        .order_by(RiverObservation.observed_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "id": row.id, "observedAt": row.observed_at,
        "waterLevelM": row.water_level_m, "rainfallMmH": row.rainfall_mm_h,
        "quality": row.quality, "source": row.source, "recordedAt": row.recorded_at,
        "recordedBy": row.recorded_by, "note": row.note,
    }


@router.get("/observation-stations")
def list_observation_stations(kind: str | None = None, site_id: str | None = None,
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    q = select(ObservationStation).order_by(ObservationStation.basin_name,
                                            ObservationStation.name)
    if kind:
        if kind not in OBS_KINDS:
            raise HTTPException(422, f"kind は {sorted(OBS_KINDS)} のいずれか")
        q = q.where(ObservationStation.kind == kind)
    if site_id:
        if not db.get(Site, site_id):
            raise HTTPException(404, "site not found")
        site_access.ensure_site_read(db, user, site_id)
        q = q.join(SiteStation, SiteStation.station_id == ObservationStation.id) \
             .where(SiteStation.site_id == site_id)
    return [_observation_station_dict(st) for st in db.scalars(q).all()]


@router.post("/admin/wbgt/stations/sync")
async def sync_wbgt_stations(db: Session = Depends(get_db),
                             user: User = Depends(require_role("admin", "tech_manager"))):
    """環境省 地点マスタCSVを observation_stations（kind=wbgt）へ同期する（#113）。"""
    result = await wbgt_env.sync_point_master(db)
    if result.get("status") != "OK":
        raise HTTPException(502, f"WBGT地点マスタの取得に失敗しました: {result.get('error')}")
    audit_add(db, user, "wbgt_station_sync",
              f"count={result.get('count')} upserted={result.get('upserted')} "
              f"updated={result.get('updated')}")
    db.commit()
    return result


@router.post("/observation-stations", status_code=201)
def create_observation_station(req: ObservationStationCreate, db: Session = Depends(get_db),
                               user: User = Depends(require_role("admin", "tech_manager"))):
    dup = db.scalar(select(ObservationStation).where(
        ObservationStation.source_id == req.source_id,
        ObservationStation.station_code == req.station_code))
    if dup:
        raise HTTPException(409, "同じ source_id + station_code の観測所が既に登録されています")

    def _build():
        now = datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")
        st = ObservationStation(
            id=_allocate_id(db, ObservationStation, "OS", 3),
            source_id=req.source_id, station_code=req.station_code, name=req.name,
            agency=req.agency, basin_name=req.basin_name, kind=req.kind,
            latitude=req.latitude, longitude=req.longitude, status="active",
            created_at=now, updated_at=now)
        db.add(st)
        audit_add(db, user, "observation_station_create",
                  f"{st.id} {req.source_id}:{req.station_code} {req.name}",
                  source_id=req.source_id)
        return st

    st = _commit_with_retry(db, _build)
    return {"id": st.id, "status": "created"}


@router.put("/observation-stations/{station_id}")
def update_observation_station(station_id: str, req: ObservationStationUpdate,
                               db: Session = Depends(get_db),
                               user: User = Depends(require_role("admin", "tech_manager"))):
    st = db.get(ObservationStation, station_id)
    if not st:
        raise HTTPException(404, "observation station not found")
    data = req.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(422, "更新内容がありません")
    if "source_id" in data or "station_code" in data:
        dup = db.scalar(select(ObservationStation).where(
            ObservationStation.source_id == data.get("source_id", st.source_id),
            ObservationStation.station_code == data.get("station_code", st.station_code),
            ObservationStation.id != station_id))
        if dup:
            raise HTTPException(409, "同じ source_id + station_code の観測所が既に登録されています")
    for k, v in data.items():
        setattr(st, k, v)
    st.updated_at = datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")
    audit_add(db, user, "observation_station_update",
              f"{station_id} {','.join(data.keys())}", source_id=st.source_id)
    db.commit()
    return {"id": station_id, "status": "updated"}


@router.delete("/observation-stations/{station_id}")
def delete_observation_station(station_id: str, db: Session = Depends(get_db),
                               user: User = Depends(require_role("admin"))):
    st = db.get(ObservationStation, station_id)
    if not st:
        raise HTTPException(404, "observation station not found")
    linked = db.scalar(select(func.count()).select_from(SiteStation)
                       .where(SiteStation.station_id == station_id))
    observed = db.scalar(select(func.count()).select_from(RiverObservation)
                         .where(RiverObservation.station_id == station_id))
    if linked or observed:
        raise HTTPException(409, "現場紐付けまたは実測値が存在するため削除できません（status=inactive で無効化してください）")
    audit_add(db, user, "observation_station_delete",
              f"{station_id} {st.source_id}:{st.station_code} {st.name}",
              source_id=st.source_id)
    db.delete(st)
    db.commit()
    return {"id": station_id, "status": "deleted"}


@router.get("/sites/{site_id}/observation-stations")
def list_site_observation_stations(site_id: str, db: Session = Depends(get_db),
                                   user: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_read(db, user, site_id)
    rows = db.execute(
        select(SiteStation, ObservationStation)
        .join(ObservationStation, ObservationStation.id == SiteStation.station_id)
        .where(SiteStation.site_id == site_id)
        .order_by(SiteStation.sort_order, SiteStation.id)
    ).all()
    stations = []
    for link, st in rows:
        item = _observation_station_dict(st)
        item["rel"] = link.rel
        item["sortOrder"] = link.sort_order
        item["latest"] = _latest_observation(db, st.id)
        stations.append(item)
    auto = any(
        (it.get("latest") or {}).get("source")
        and it["latest"]["source"] not in ("", "MANUAL")
        for it in stations
    )
    if auto:
        provider = ("デモ自動取得（DEMO-RIVER・シミュレーション）。"
                    "公式の水防災オープンデータ提供サービスは未接続")
    else:
        provider = "未接続（自動取得は未実装。水防災オープンデータ提供サービス等の接続が未設定）"
    return {
        "automatic": auto,
        "provider": provider,
        "stations": stations,
    }


@router.post("/sites/{site_id}/observation-stations", status_code=201)
def link_site_observation_station(site_id: str, req: SiteStationLink,
                                  db: Session = Depends(get_db),
                                  user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    st = db.get(ObservationStation, req.station_id)
    if not st or st.status != "active":
        raise HTTPException(404, "observation station not found")
    count = db.scalar(select(func.count()).select_from(SiteStation)
                      .where(SiteStation.site_id == site_id))
    if count >= _SITE_STATIONS_MAX:
        raise HTTPException(422, f"観測所は1現場あたり最大 {_SITE_STATIONS_MAX} 件までです")
    dup = db.scalar(select(SiteStation).where(SiteStation.site_id == site_id,
                                              SiteStation.station_id == req.station_id))
    if dup:
        raise HTTPException(409, "この観測所は既に現場へ紐付いています")

    def _build():
        link = SiteStation(
            id=_allocate_id(db, SiteStation, "SS", 3),
            site_id=site_id, station_id=req.station_id, rel=req.rel,
            sort_order=req.sort_order,
            created_at=datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S"))
        db.add(link)
        audit_add(db, user, "site_station_link", f"{link.id} {site_id} {req.station_id} {req.rel}",
                  site_id=site_id)
        return link

    link = _commit_with_retry(db, _build)
    return {"id": link.id, "status": "linked"}


@router.delete("/sites/{site_id}/observation-stations/{station_id}")
def unlink_site_observation_station(site_id: str, station_id: str,
                                    db: Session = Depends(get_db),
                                    user: User = Depends(require_role("admin", "tech_manager"))):
    link = db.scalar(select(SiteStation).where(SiteStation.site_id == site_id,
                                               SiteStation.station_id == station_id))
    if not link:
        raise HTTPException(404, "site station link not found")
    audit_add(db, user, "site_station_unlink",
              f"{link.id} {site_id} {station_id}", site_id=site_id)
    db.delete(link)
    db.commit()
    return {"status": "unlinked"}


@router.get("/sites/{site_id}/river-observations")
def list_river_observations(site_id: str, limit: int = Query(24, ge=1, le=500),
                            db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_read(db, user, site_id)
    station_ids = db.scalars(
        select(SiteStation.station_id).where(SiteStation.site_id == site_id)
    ).all()
    if not station_ids:
        return {"automatic": False, "provider": "未接続（観測所が紐付いていません）",
                "observations": []}
    rows = db.scalars(
        select(RiverObservation)
        .where(RiverObservation.station_id.in_(station_ids))
        .order_by(RiverObservation.observed_at.desc())
        .limit(limit)
    ).all()
    station_names = {
        st.id: st.name
        for st in db.scalars(
            select(ObservationStation).where(ObservationStation.id.in_(station_ids))
        ).all()
    }
    auto = any(r.source and r.source != "MANUAL" for r in rows)
    if auto:
        provider = ("デモ自動取得（DEMO-RIVER・シミュレーション）。"
                    "公式の水防災オープンデータ提供サービスは未接続")
    else:
        provider = "未接続（自動取得は未実装。現在は手動入力のみ）"
    return {
        "automatic": auto,
        "provider": provider,
        "observations": [{
            "id": r.id, "stationId": r.station_id,
            "stationName": station_names.get(r.station_id, ""),
            "observedAt": r.observed_at, "waterLevelM": r.water_level_m,
            "rainfallMmH": r.rainfall_mm_h, "quality": r.quality,
            "source": r.source, "recordedAt": r.recorded_at,
            "recordedBy": r.recorded_by, "note": r.note,
        } for r in rows],
    }


@router.post("/sites/{site_id}/river-observations", status_code=201)
def create_river_observation(site_id: str, req: RiverObservationCreate,
                             db: Session = Depends(get_db),
                             user: User = Depends(require_role("admin", "tech_manager"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    link = db.scalar(select(SiteStation).where(SiteStation.site_id == site_id,
                                               SiteStation.station_id == req.station_id))
    if not link:
        raise HTTPException(422, "この観測所は現場へ紐付いていません。先に紐付けを行ってください")

    def _build():
        now = datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")
        obs = RiverObservation(
            id=_allocate_id(db, RiverObservation, "RO", 5),
            station_id=req.station_id,
            observed_at=_normalize_observed_at(req.observed_at),
            water_level_m=req.water_level_m, rainfall_mm_h=req.rainfall_mm_h,
            quality=req.quality, source="MANUAL", recorded_at=now,
            recorded_by=user.username or user.id, note=req.note)
        db.add(obs)
        audit_add(db, user, "river_observation_create",
                  f"{obs.id} {site_id} {req.station_id} L={req.water_level_m} R={req.rainfall_mm_h}",
                  site_id=site_id)
        return obs

    obs = _commit_with_retry(db, _build)
    return {"id": obs.id, "status": "created"}


@router.put("/river-observations/{observation_id}")
def update_river_observation(observation_id: str, req: RiverObservationUpdate,
                             db: Session = Depends(get_db),
                             user: User = Depends(require_role("admin", "tech_manager"))):
    obs = db.get(RiverObservation, observation_id)
    if not obs:
        raise HTTPException(404, "river observation not found")
    data = req.model_dump(exclude_none=True)
    data.pop("station_id", None)  # 観測所の付け替えは不可（紐付け解除→再登録で対応）
    if not data:
        raise HTTPException(422, "更新内容がありません")
    if "observed_at" in data:
        data["observed_at"] = _normalize_observed_at(data["observed_at"])
    for k, v in data.items():
        setattr(obs, k, v)
    obs.recorded_by = f"{user.username or user.id} (update)"
    audit_add(db, user, "river_observation_update",
              f"{observation_id} {','.join(data.keys())}")
    db.commit()
    return {"id": observation_id, "status": "updated"}


@router.delete("/river-observations/{observation_id}")
def delete_river_observation(observation_id: str, db: Session = Depends(get_db),
                             user: User = Depends(require_role("admin"))):
    obs = db.get(RiverObservation, observation_id)
    if not obs:
        raise HTTPException(404, "river observation not found")
    audit_add(db, user, "river_observation_delete",
              f"{observation_id} {obs.observed_at}")
    db.delete(obs)
    db.commit()
    return {"id": observation_id, "status": "deleted"}


# ---------- ダッシュボード ----------
@router.get("/dashboard/site-risk")
async def dashboard_site_risk(db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    accessible = site_access.accessible_site_ids(db, user)
    sites = db.scalars(
        select(Site).where(Site.status == "active", Site.id.in_(accessible))
        .order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites), db=db)
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


# ---------- 海象データ：全国版（#72 段5） ----------
@router.get("/marine/national")
async def marine_national(db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """全アクセス可能現場の波浪・海上風・リスク概況（Open-Meteo Marine API 補完）。

    NOWPHAS / 気象庁潮位は利用条件確認前のため tide は None とし、画面側で公式リンクと
    未接続であることを明示する（実態以上に良く見せない方針）。
    """
    accessible = site_access.accessible_site_ids(db, user)
    sites = db.scalars(
        select(Site).where(Site.status == "active", Site.id.in_(accessible))
        .order_by(Site.id)).all()
    rows = await asyncio.gather(
        *[assessment.marine_site_summary(s) for s in sites])
    return {
        "source": {"marine": marine.SOURCE_ID, "tide": "DS-JMA-TIDE-UNCONNECTED"},
        "sites": rows,
        "fetchedAt": datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/marine/return-periods")
def marine_return_periods(db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """50年・100年再現期間波高の極値解析（デモ・シミュレーション版）。

    NOWPHAS 長期観測の蓄積前は地点IDから決定的に生成した年最大波高へ
    Gumbel / Weibull を当てはめる。warnings / dataType=synthetic で
    設計利用不可であることを明示する。
    """
    accessible = site_access.accessible_site_ids(db, user)
    sites = db.scalars(
        select(Site).where(Site.status == "active", Site.id.in_(accessible))
        .order_by(Site.id)).all()
    return extreme.analyze_sites([
        {"siteId": s.id, "name": s.name, "loc": s.loc,
         "latitude": s.latitude, "longitude": s.longitude} for s in sites
    ])


# ---------- 気象 ----------
@router.get("/weather/timeseries")
async def weather_timeseries(site_id: str = Query(...), db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_read(db, user, site_id)
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
                             thresholds: dict | None = None, *, user, audit_label: str) -> str:
    """判定結果と理由を永続化し、結果IDを返す（監査・実績分析の正本。設計§6.2.11/§6.2.12）。
    #49: 採番〜INSERTを _commit_with_retry() でラップし、同時リクエストによるID重複を防ぐ。
    #63: 監査行も同一トランザクションに含める（commit後のaudit失敗で「結果は永続・監査は欠落」となり、
    500応答でクライアントが再評価して結果を二重生成するのを防ぐ）。audit_label は評価対象の識別子
    （evaluate=work_type / 作業予定評価=plan_id）で、従来の監査メッセージ形式を維持する。"""
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
        audit_add(db, user, "evaluate", f"{rid} {audit_label} L{res['overall_level']}", site_id=site_id)
        return result

    result = _commit_with_retry(db, _build)
    return result.id


@router.post("/decisions/evaluate")
async def evaluate_decision(req: EvaluateReq, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_write(db, user, req.site_id, "decision")
    # 永続化する判定は閾値キャッシュをバイパスした fresh 値で評価し、同じ値をスナップショット
    # 保存する(古い閾値での保存を防ぐ)。応答には閾値を含めない(admin読み取り境界の維持)
    th = rules_service.effective_th(fresh=True)
    res = await assessment.assess_decision(site, req.work_type, req.start, req.end,
                                           th=th, db=db)
    rid = _persist_decision_result(db, site.id, req.work_type, res, thresholds=th,
                                   user=user, audit_label=req.work_type)
    res["resultId"] = rid
    return res


@router.get("/decision-results/{result_id}")
def get_decision_result(result_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    dr = db.get(DecisionResult, result_id)
    if not dr:
        raise HTTPException(404, "decision result not found")
    site_access.ensure_site_read(db, user, dr.site_id)
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
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    accessible = site_access.accessible_site_ids(db, user)
    q = select(WorkPlan).order_by(WorkPlan.id)
    q = q.where(WorkPlan.site_id.in_(accessible))
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
def get_work_plan(plan_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    site_access.ensure_site_read(db, user, plan.site_id)
    return _work_plan_dict(plan)


@router.post("/work-plans", status_code=201)
def create_work_plan(req: WorkPlanCreate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager", "site_manager"))):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_write(db, user, req.site_id, "editor")

    def _build():
        plan = WorkPlan(id=_allocate_id(db, WorkPlan, "WP", 2), site_id=req.site_id,
                        work_type=req.work_type, title=req.title,
                        planned_start=req.planned_start, planned_end=req.planned_end,
                        contractor=req.contractor, summary=req.summary, status=req.status)
        db.add(plan)
        # 監査行を同一トランザクションへ（#63）
        audit_add(db, user, "work_plan_create", f"{plan.id} {plan.title or plan.work_type}", site_id=site.id)
        return plan

    plan = _commit_with_retry(db, _build)
    return {"id": plan.id, "status": "created"}


@router.put("/work-plans/{plan_id}")
def update_work_plan(plan_id: str, req: WorkPlanUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "tech_manager", "site_manager"))):
    plan = db.get(WorkPlan, plan_id)
    if not plan:
        raise HTTPException(404, "work plan not found")
    site_access.ensure_site_write(db, user, plan.site_id, "editor")
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
    # 監査行を更新と同一commitへ（#63）
    audit_add(db, user, "work_plan_update", f"{plan_id} {','.join(data.keys())}", site_id=plan.site_id)
    db.commit()
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
    site_access.ensure_site_write(db, user, plan.site_id, "decision")
    # /api/decisions/evaluate と同じ fresh 閾値・スナップショット経路(第2の永続化パス)
    th = rules_service.effective_th(fresh=True)
    res = await assessment.assess_decision(site, plan.work_type, plan.planned_start,
                                           plan.planned_end, th=th, db=db)
    rid = _persist_decision_result(db, site.id, plan.work_type, res, thresholds=th,
                                   user=user, audit_label=plan_id)
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
def list_decision_logs(action: str | None = None,
                       site_id: str | None = Query(None, max_length=10),
                       work_type: str | None = Query(None, max_length=40),
                       q: str | None = Query(None, max_length=100),
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    accessible = site_access.accessible_site_ids(db, user)
    stmt = select(DecisionLog).order_by(DecisionLog.id.desc())
    stmt = stmt.where(DecisionLog.site_id.in_(accessible))
    if action and action != "all":
        stmt = stmt.where(DecisionLog.action == action)
    if site_id:
        if site_id not in accessible:
            return []
        stmt = stmt.where(DecisionLog.site_id == site_id)
    if work_type:
        stmt = stmt.where(DecisionLog.work_type == work_type)
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{escaped}%"
        stmt = stmt.where(
            DecisionLog.site_name.ilike(needle, escape="\\")
            | DecisionLog.comment.ilike(needle, escape="\\")
            | DecisionLog.decided_by.ilike(needle, escape="\\")
            | DecisionLog.work_type.ilike(needle, escape="\\")
        )
    rows = db.scalars(stmt).all()
    return [{"id": h.id, "datetime": h.decided_at, "siteId": h.site_id, "site": h.site_name,
             "workType": h.work_type, "level": h.level, "action": h.action,
             "comment": h.comment, "by": h.decided_by} for h in rows]


@router.get("/decision-logs/similar")
def similar_decision_logs(site_id: str | None = Query(None, max_length=10),
                          work_type: str | None = Query(None, max_length=40),
                          level: int | None = Query(None, ge=0, le=3),
                          limit: int = Query(8, ge=1, le=20),
                          db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """類似過去判断の参照（#67）。同一現場・同一工種・同一レベルを優先して類似ログを返す。

    スコアは 現場一致=3 / 工種一致=2 / レベル一致=1 / 直近7日=2 とし、
    スコア降順・ID降順で返す。いずれの条件も未指定の場合は400（無意味な全件返しを防ぐ）。
    """
    if site_id is None and work_type is None and level is None:
        raise HTTPException(400, "site_id / work_type / level のいずれかを指定してください")
    accessible = site_access.accessible_site_ids(db, user)
    if site_id and site_id not in accessible:
        return []
    rows = db.scalars(
        select(DecisionLog).where(DecisionLog.site_id.in_(accessible))
        .order_by(DecisionLog.id.desc())).all()
    now = datetime.now(assessment.JST)
    scored = []
    for h in rows:
        score = 0
        reasons = []
        if site_id and h.site_id == site_id:
            score += 3
            reasons.append("同一現場")
        if work_type and h.work_type == work_type:
            score += 2
            reasons.append("同一工種")
        if level is not None and h.level == level:
            score += 1
            reasons.append("同一判定レベル")
        try:
            decided = datetime.strptime(h.decided_at, "%m/%d %H:%M").replace(
                year=now.year, tzinfo=assessment.JST)
            if decided > now:  # 年またぎ（12月末シード等）は前年扱い
                decided = decided.replace(year=now.year - 1)
            if (now - decided).days <= 7:
                score += 2
                reasons.append("直近7日以内")
        except ValueError:
            pass
        if score > 0:
            scored.append({
                "id": h.id, "datetime": h.decided_at, "siteId": h.site_id,
                "site": h.site_name, "workType": h.work_type, "level": h.level,
                "action": h.action, "comment": h.comment, "by": h.decided_by,
                "score": score, "matchReasons": reasons,
            })
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    return scored[:limit]


@router.post("/decision-logs")
def create_decision_log(req: DecisionLogReq, db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin", "tech_manager", "site_manager", "safety"))):
    site = db.get(Site, req.site_id)
    if not site:
        raise HTTPException(404, "site not found")
    site_access.ensure_site_write(db, user, req.site_id, "decision")

    def _build():
        entry = DecisionLog(
            id=_allocate_id(db, DecisionLog, "L", 2), site_id=site.id, site_name=site.name,
            work_type=req.work_type, level=req.level, action=req.action,
            comment=req.comment or "（メモなし）", decided_by=user.display_name,
            decision_result_id=req.decision_result_id,
            decided_at=datetime.now(assessment.JST).strftime("%m/%d %H:%M"))
        db.add(entry)
        # 監査行を同一トランザクションへ（#63）
        audit_add(db, user, "decision_log", f"{entry.id} {req.action} L{req.level}", site_id=site.id)
        return entry

    entry = _commit_with_retry(db, _build)
    return {"id": entry.id, "status": "recorded"}


@router.get("/decision-logs/export.csv")
def export_decision_logs(db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    audit(db, user, "csv_export", "decision_logs.csv")
    accessible = site_access.accessible_site_ids(db, user)
    rows = db.scalars(
        select(DecisionLog).where(DecisionLog.site_id.in_(accessible))
        .order_by(DecisionLog.id.desc())).all()
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


@router.get("/decision-logs/export.pdf")
def export_decision_logs_pdf(db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """判断履歴のPDF帳票（発注者説明・監査用）。CSVと同じ認証・現場権限で出力する。"""
    audit(db, user, "pdf_export", "decision_logs.pdf")
    accessible = site_access.accessible_site_ids(db, user)
    rows = db.scalars(
        select(DecisionLog).where(DecisionLog.site_id.in_(accessible))
        .order_by(DecisionLog.id.desc())).all()
    font = _pdf_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="施工判断支援システム 判断履歴",
    )
    title_style = ParagraphStyle("cwTitle", fontName=font, fontSize=14,
                                 textColor=colors.HexColor("#13344f"),
                                 leading=18, spaceAfter=2)
    meta_style = ParagraphStyle("cwMeta", fontName=font, fontSize=9,
                                textColor=colors.HexColor("#697a88"), leading=12)
    note_style = ParagraphStyle("cwNote", fontName=font, fontSize=8,
                                textColor=colors.HexColor("#8a5a2b"), leading=11)
    cell_style = ParagraphStyle("cwCell", fontName=font, fontSize=8, leading=10.5)
    story = []
    story.append(Paragraph("施工判断支援システム 判断履歴", title_style))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f"生成日時: {datetime.now(assessment.JST):%Y-%m-%d %H:%M} ／ 対象件数: {len(rows)} 件",
        meta_style))
    story.append(Paragraph(
        "本帳票は判断を「支援」する記録であり、作業の実施・中止を自動決定するものではありません。"
        "最終判断は現場責任者が行ってください。",
        note_style))
    story.append(Spacer(1, 4 * mm))
    header = ["ID", "日時", "現場", "工種", "判定", "行動", "記録者", "判断理由・メモ"]
    data = [header]
    for h in rows:
        data.append([
            h.id, h.decided_at, h.site_name, h.work_type,
            f"L{h.level} {LEVEL_LABELS.get(h.level, '')}",
            ACTION_LABELS.get(h.action, h.action), h.decided_by,
            Paragraph(h.comment.replace("&", "&amp;").replace("<", "&lt;"),
                      cell_style),
        ])
    table = Table(data, colWidths=[
        14 * mm, 22 * mm, 42 * mm, 30 * mm, 24 * mm, 20 * mm, 32 * mm, 88 * mm],
        repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#13344f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, -1), font, 8),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c6d0d8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(table)
    doc.build(story)
    return Response(content=buf.getvalue(), media_type="application/pdf",
                    headers={
                        "Content-Disposition": "attachment; filename="
                        f"decision_logs_{datetime.now(assessment.JST):%Y%m%d}.pdf"
                    })


# ---------- データ取得（手動再取得） ----------
@router.post("/data-collectors/run")
async def run_collectors(db: Session = Depends(get_db),
                         user: User = Depends(require_role("admin", "tech_manager"))):
    # 実行「要求」の監査（後続プローブ結果の成否によらず要求事実を 1 件残す設計。
    # 「成功した更新の監査」へ変える場合は probe_all を no-commit 化して同一 tx へ寄せること — #63 対抗レビューで意図明示）
    audit(db, user, "collectors_run", "手動再取得")
    assessment.clear_cache()
    sites = db.scalars(select(Site).where(Site.status == "active")).all()
    cards = await assessment.assess_all(list(sites), db=db)
    ok = sum(1 for c in cards if c["weatherStatus"] == "OK")
    # 河川観測デモ自動取得も手動再取得に含める（観測所未投入でも冪等に整備）
    if settings.river_demo_enabled:
        river_collector.ensure_demo_stations(db)
        river = river_collector.collect_demo_observations(db)
        river_collector.refresh_demo_source_status(db)
    else:
        river = {"written": 0, "stations": 0}
    # 全データソースを実プローブして状態を更新（Open-Meteo含む）
    probed = await source_probe.probe_all(db)
    return {"refetched": len(cards), "weatherOk": ok, "total": len(cards),
            "probed": {k: v["status"] for k, v in probed.items()},
            "river": river}


# ---------- 通知（設計§14） ----------
@router.get("/notifications")
async def list_notifications(db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    accessible = site_access.accessible_site_ids(db, user)
    sites = db.scalars(
        select(Site).where(Site.status == "active", Site.id.in_(accessible))
        .order_by(Site.id)).all()
    cards = await assessment.assess_all(list(sites), db=db)
    src = db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.id)).all()
    sources = [{"id": d.id, "name": d.name, "status": d.status,
                "fails": d.fails, "lastOk": d.last_ok} for d in src]
    notifs = notifications.build_notifications(cards, sources)
    return {"count": len(notifs), "notifications": notifs}


# ---------- ユーザー管理（管理者専用。弱点 #17 解消: UI/APIでユーザーを管理できるようにする） ----------
def _validate_email(v: str) -> str:
    v = (v or "").strip()
    if v and (v.count("@") != 1 or len(v) > 255 or v.startswith("@") or v.endswith("@")):
        raise ValueError("email は空または有効なメールアドレス（例: name@example.com）を指定してください")
    return v


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    display_name: str
    email: str = ""
    role: str = "viewer"
    department: str = ""
    password: str

    @field_validator("username")
    @classmethod
    def _username(cls, v):
        v = (v or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,50}", v):
            raise ValueError("username は英数字・._- の3〜50文字で指定してください")
        return v.lower()

    @field_validator("display_name")
    @classmethod
    def _display(cls, v):
        v = (v or "").strip()
        if not v or len(v) > 100:
            raise ValueError("display_name は必須・100文字以内で指定してください")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        if v not in USER_ROLES:
            raise ValueError(f"role は {sorted(USER_ROLES)} のいずれかで指定してください")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return _validate_email(v)

    @field_validator("department")
    @classmethod
    def _department(cls, v):
        v = (v or "").strip()
        if len(v) > 100:
            raise ValueError("department は100文字以内で指定してください")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v):
        if len(v) < 8:
            raise ValueError("password は8文字以上で指定してください")
        if len(v) > 200:
            raise ValueError("password は200文字以内で指定してください")
        return v


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    email: str | None = None
    role: str | None = None
    department: str | None = None
    is_active: bool | None = None
    password: str | None = None

    @field_validator("display_name")
    @classmethod
    def _display(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v or len(v) > 100:
            raise ValueError("display_name は100文字以内で指定してください")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        if v is not None and v not in USER_ROLES:
            raise ValueError(f"role は {sorted(USER_ROLES)} のいずれかで指定してください")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return None if v is None else _validate_email(v)

    @field_validator("department")
    @classmethod
    def _department(cls, v):
        if v is not None and len((v or "").strip()) > 100:
            raise ValueError("department は100文字以内で指定してください")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v):
        if v is not None and not 8 <= len(v) <= 200:
            raise ValueError("password は8〜200文字で指定してください")
        return v


def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "displayName": u.display_name,
            "email": u.email, "role": u.role, "department": u.department,
            "isActive": u.is_active, "createdAt": u.created_at,
            "updatedAt": u.updated_at}


def _active_admin_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True))) or 0


def _guard_user_change(db: Session, actor: User, target: User,
                       new_role: str, new_active: bool) -> None:
    """管理者ロックアウト防止: 自分自身の降格/無効化と最終adminの降格/無効化を拒否する。"""
    if target.id == actor.id and (new_role != "admin" or not new_active):
        raise HTTPException(400, "自分自身を降格・無効化することはできません")
    if target.role == "admin" and (new_role != "admin" or not new_active):
        if _active_admin_count(db) <= 1:
            raise HTTPException(400, "最後の有効な管理者を降格・無効化することはできません")


@router.get("/admin/users")
def list_users(db: Session = Depends(get_db),
               user: User = Depends(require_role("admin"))):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [_user_dict(u) for u in rows]


@router.post("/admin/users", status_code=201)
def create_user(req: UserCreate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    if db.scalar(select(User).where(User.username == req.username)):
        raise HTTPException(409, "username already exists")
    now = datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")

    def _build():
        entry = User(
            id=_allocate_id(db, User, "U", 2), username=req.username,
            display_name=req.display_name, email=req.email, role=req.role,
            department=req.department, password_hash=hash_password(req.password),
            is_active=True, created_at=now, updated_at=now)
        db.add(entry)
        audit_add(db, user, "user_create",
                  f"{entry.id} {req.username} role={req.role}")
        return entry

    entry = _commit_with_retry(db, _build)
    return {"id": entry.id, "status": "created"}


@router.put("/admin/users/{user_id}")
def update_user(user_id: str, req: UserUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "user not found")
    new_role = req.role if req.role is not None else target.role
    new_active = req.is_active if req.is_active is not None else target.is_active
    _guard_user_change(db, user, target, new_role, new_active)
    changes = []
    if req.display_name is not None and req.display_name != target.display_name:
        target.display_name = req.display_name
        changes.append("display_name")
    if req.email is not None and req.email != target.email:
        target.email = req.email
        changes.append("email")
    if req.role is not None and req.role != target.role:
        target.role = req.role
        changes.append(f"role={req.role}")
    if req.department is not None and req.department != target.department:
        target.department = req.department
        changes.append("department")
    if req.is_active is not None and req.is_active != target.is_active:
        target.is_active = req.is_active
        changes.append(f"is_active={req.is_active}")
    if req.password is not None:
        target.password_hash = hash_password(req.password)
        changes.append("password=reset")
    target.updated_at = datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")
    audit_add(db, user, "user_update",
              f"{target.id} {target.username} " + (",".join(changes) or "no-change"))
    db.commit()
    return {"id": target.id, "status": "updated"}


@router.delete("/admin/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "user not found")
    if target.id == user.id:
        raise HTTPException(400, "自分自身を削除することはできません")
    _guard_user_change(db, user, target, "viewer", False)
    for row in db.scalars(select(UserSiteAccess).where(
            UserSiteAccess.user_id == target.id)):
        db.delete(row)
    audit_add(db, user, "user_delete",
              f"{target.id} {target.username} role={target.role}")
    db.delete(target)
    db.commit()
    return {"id": user_id, "status": "deleted"}


# ---------- 監査ログ（管理者・技術管理者） ----------
@router.get("/admin/audit-logs")
def list_audit_logs(limit: int = 100, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin", "tech_manager"))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": r.id, "timestamp": r.timestamp, "user": r.username, "action": r.action,
             "message": r.message, "siteId": r.site_id} for r in rows]


# ---------- 現場単位権限（#117） ----------
class UserSiteAccessGrant(BaseModel):
    user_id: str
    site_id: str
    role: str = "site_viewer"

    @field_validator("user_id", "site_id")
    @classmethod
    def _id(cls, v):
        v = (v or "").strip()
        if not v or len(v) > 20:
            raise ValueError("user_id / site_id は必須・20文字以内で指定してください")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        if v not in site_access.SITE_ACCESS_ROLES:
            raise ValueError(f"role は {sorted(site_access.SITE_ACCESS_ROLES)} のいずれか")
        return v


def _user_site_access_dict(row: UserSiteAccess) -> dict:
    return {"id": row.id, "userId": row.user_id, "siteId": row.site_id,
            "role": row.role, "grantedBy": row.granted_by,
            "createdAt": row.created_at, "updatedAt": row.updated_at}


@router.get("/admin/user-site-access")
def list_user_site_access(db: Session = Depends(get_db),
                          user: User = Depends(require_role("admin"))):
    rows = db.scalars(select(UserSiteAccess).order_by(
        UserSiteAccess.user_id, UserSiteAccess.site_id)).all()
    return [_user_site_access_dict(r) for r in rows]


@router.post("/admin/user-site-access", status_code=201)
def grant_user_site_access(req: UserSiteAccessGrant, db: Session = Depends(get_db),
                           user: User = Depends(require_role("admin"))):
    target = db.get(User, req.user_id)
    if not target:
        raise HTTPException(404, "user not found")
    if not db.get(Site, req.site_id):
        raise HTTPException(404, "site not found")
    row = db.scalar(select(UserSiteAccess).where(
        UserSiteAccess.user_id == req.user_id,
        UserSiteAccess.site_id == req.site_id))
    now = datetime.now(assessment.JST).strftime("%Y-%m-%d %H:%M:%S")
    if row:
        row.role = req.role
        row.granted_by = user.username or user.id
        row.updated_at = now
        audit_add(db, user, "user_site_access_update",
                  f"{row.id} {req.user_id} {req.site_id} role={req.role}")
        db.commit()
        return {"id": row.id, "status": "updated"}
    row = UserSiteAccess(
        id=_allocate_id(db, UserSiteAccess, "USA", 3),
        user_id=req.user_id, site_id=req.site_id, role=req.role,
        granted_by=user.username or user.id, created_at=now, updated_at=now)
    db.add(row)
    audit_add(db, user, "user_site_access_grant",
              f"{row.id} {req.user_id} {req.site_id} role={req.role}")
    db.commit()
    return {"id": row.id, "status": "granted"}


@router.delete("/admin/user-site-access/{access_id}")
def revoke_user_site_access(access_id: str, db: Session = Depends(get_db),
                            user: User = Depends(require_role("admin"))):
    row = db.get(UserSiteAccess, access_id)
    if not row:
        raise HTTPException(404, "user site access not found")
    audit_add(db, user, "user_site_access_revoke",
              f"{row.id} {row.user_id} {row.site_id} role={row.role}")
    db.delete(row)
    db.commit()
    return {"id": access_id, "status": "revoked"}


@router.get("/me/sites")
def my_sites(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    accessible = site_access.accessible_site_ids(db, user)
    rows = db.scalars(select(Site).where(Site.id.in_(accessible))
                      .order_by(Site.id)).all()
    grants = {}
    if not site_access.has_full_read(user):
        grants = {r.site_id: r.role for r in db.scalars(
            select(UserSiteAccess).where(UserSiteAccess.user_id == user.id)).all()}
    return [{"id": s.id, "code": s.site_code, "name": s.name,
             "role": grants.get(s.id, "full")} for s in rows]


# ---------- 運用監視（管理者・技術管理者、#95） ----------
@router.get("/admin/ops/readiness-detail")
def readiness_detail(user: User = Depends(require_role("admin", "tech_manager"))):
    return readiness.check_readiness_detail()


@router.get("/admin/ops/status-snapshot")
def status_snapshot(user: User = Depends(require_role("admin", "tech_manager"))):
    try:
        return ops_status.load_ops_status_snapshot()
    except ops_status.OpsStatusSnapshotError as exc:
        raise HTTPException(503, f"ops status snapshot unavailable: {exc.reason}") from exc
