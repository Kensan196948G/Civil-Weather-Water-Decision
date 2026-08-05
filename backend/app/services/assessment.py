"""評価サービス: 気象取得（Open-Meteo）→ Reading 構築 → 判定エンジン を束ねる。

外部取得失敗は隠さず、欠測として判定に反映する（設計 §5.3 / §15.2）。
fetch は注入可能（テストはネットワーク非依存）。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import rules as rules_service
from .data_collectors import jma_warnings, open_meteo, wbgt_env
from .decision_engine import LEVEL_LABELS, Reading, evaluate
from ..core.config import settings
from ..models import ObservationStation, RiverObservation, Site, SiteStation

JST = timezone(timedelta(hours=9))
_CACHE: dict[str, tuple[float, dict]] = {}
_LAST_GOOD: dict[str, dict] = {}  # 取得成功時の最終良品（STALE縮退用、TTL対象外）
_TTL = 300       # 秒（成功キャッシュ）
_FAIL_TTL = 30   # 秒（失敗由来 STALE/ERROR の再試行間隔。復旧を5分待たせない）

# #112: 河川実測の鮮度・トレンド判定（詳細設計 §6/§8。閾値は将来設定化できるよう定数に集約）
RIVER_MAX_AGE_SECONDS = 30 * 60   # 実測がこれより古ければ「確認不能」扱い
RIVER_RISING_RATE_M_H = 0.2       # 直近2点の水位上昇率がこれを超えれば rising


def _cache_valid(hit: tuple[float, dict] | None, now: float) -> bool:
    """成功は _TTL、失敗由来（STALE/ERROR）は短い _FAIL_TTL で失効させる。"""
    if not hit:
        return False
    ttl = _TTL if hit[1].get("status") == "OK" else _FAIL_TTL
    return now - hit[0] < ttl


async def _cached_fetch(lat: float, lon: float, key: str) -> dict:
    now = time.monotonic()
    hit = _CACHE.get(key)
    if _cache_valid(hit, now):
        return hit[1]
    data = await open_meteo.fetch_forecast(lat, lon)
    if data.get("status") == "OK":
        _LAST_GOOD[key] = data
    elif key in _LAST_GOOD:
        # 取得失敗時は前回良品へ縮退（§5.2 STALE / §15.2 画面を落とさない）。
        # fetched_at は前回取得時刻のまま残し、鮮度が古いことを隠さない。
        data = {**_LAST_GOOD[key], "status": "STALE", "error": data.get("error")}
    _CACHE[key] = (now, data)
    return data


async def _cached_wbgt(station: str) -> dict:
    """環境省WBGT予報のキャッシュ付き取得（#113: 全地点CSV優先→地点別フォールバック）。"""
    now = time.monotonic()
    all_key = "wbgt:all"
    hit = _CACHE.get(all_key)
    if _cache_valid(hit, now):
        all_data = hit[1]
    else:
        all_data = await wbgt_env.fetch_forecast_all()
        _CACHE[all_key] = (now, all_data)
    if all_data.get("status") == "OK":
        points = all_data.get("points_by_station", {}).get(station)
        if points:
            return {"source_id": wbgt_env.SOURCE_ID, "station": station,
                    "points": points, "status": "OK", "error": None,
                    "fetched_at": all_data.get("fetched_at")}

    # 一括取得が失敗/対象地点なしの場合は地点別CSVへフォールバック（既存挙動を維持）
    key = f"wbgt:{station}"
    hit = _CACHE.get(key)
    if _cache_valid(hit, now):
        return hit[1]
    data = await wbgt_env.fetch_forecast(station)
    _CACHE[key] = (now, data)
    return data


def clear_cache() -> None:
    _CACHE.clear()
    _LAST_GOOD.clear()
    wbgt_env.clear_wbgt_station_cache()


def _parse_jst(value: str | None) -> datetime | None:
    """JST ISO文字列をパース（未指定/不正は None）。DB保存値は JST "%Y-%m-%d %H:%M:%S"。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _is_fresh_observation(entry: dict | None, now: datetime) -> bool:
    """最新実測が「OKかつ鮮度基準内」かを判定。欠測・エラー・古い値は安全誤認させない。"""
    if not entry:
        return False
    if entry.get("quality") != "OK":
        return False
    dt = _parse_jst(entry.get("observed_at"))
    return dt is not None and (now - dt).total_seconds() <= RIVER_MAX_AGE_SECONDS


def _load_river_context(db: Session, site_id: str) -> dict:
    """現場に紐付く最寄り/上流観測所の最新実測（最大2件）を読み込む。紐付けなしは空dict。"""
    links = db.scalars(
        select(SiteStation)
        .where(SiteStation.site_id == site_id)
        .order_by(SiteStation.sort_order, SiteStation.id)
    ).all()
    ctx: dict = {}
    for link in links:
        if link.rel not in ("nearest", "upstream") or link.rel in ctx:
            continue
        station = db.get(ObservationStation, link.station_id)
        if station is None or station.status != "active":
            continue
        history = db.scalars(
            select(RiverObservation)
            .where(RiverObservation.station_id == station.id)
            .order_by(RiverObservation.observed_at.desc())
            .limit(2)
        ).all()
        if not history:
            continue
        last, prev = history[0], (history[1] if len(history) > 1 else None)
        ctx[link.rel] = {
            "station_id": station.id,
            "name": station.name,
            "observed_at": last.observed_at,
            "water_level_m": last.water_level_m,
            "rainfall_mm_h": last.rainfall_mm_h,
            "quality": last.quality,
            "source": last.source or "MANUAL",
            "prev_observed_at": prev.observed_at if prev else None,
            "prev_water_level_m": prev.water_level_m if prev else None,
            "prev_quality": prev.quality if prev else None,
        }
    return ctx


def _derive_trend(entry: dict) -> tuple[str | None, float | None]:
    """最新2点の水位から上昇率を計算し ('rising'|'stable', m/h) を返す。1点のみは None。"""
    if entry.get("prev_quality") != "OK":
        return None, None
    if entry.get("water_level_m") is None or entry.get("prev_water_level_m") is None:
        return None, None
    t1 = _parse_jst(entry.get("observed_at"))
    t0 = _parse_jst(entry.get("prev_observed_at"))
    if t1 is None or t0 is None or t1 <= t0:
        return None, None
    delta_h = (t1 - t0).total_seconds() / 3600.0
    rate = (entry["water_level_m"] - entry["prev_water_level_m"]) / delta_h
    return ("rising" if rate >= RIVER_RISING_RATE_M_H else "stable"), rate


def _river_view(river_ctx: dict | None, site: Site) -> tuple[dict, set[str]]:
    """実測コンテキストから Reading 用の河川項目と欠測セットを導出（#112）。"""
    now = datetime.now(JST)
    view = {
        "upstream_rain_mm_h": None,
        "upstream_rain_source": None,
        "water_level_trend": None,
        "water_level_m": None,
        "water_level_rate_m_h": None,
        "source_river": "DS-RIVER-GO",
        "river_note": site.river_note,
        "river_state": site.river_state,
    }
    missing: set[str] = set()

    # 観測所・実測がない場合: 手動 river_state は後方互換で尊重しつつ、
    # 「データなし」を安全側（確認不能）として扱う。
    if not river_ctx or not (river_ctx.get("nearest") or river_ctx.get("upstream")):
        if site.river_state in ("rising", "stable"):
            view["source_river"] = "MANUAL"
            view["water_level_trend"] = site.river_state
            view["river_state"] = site.river_state
            view["river_note"] = f"手動入力: {site.river_state}（{site.river_note}）"
        elif site.river_state == "stale":
            view["river_state"] = "stale"
            missing.add("river")
        else:
            view["river_state"] = "stale"
            view["river_note"] = "河川観測所が未設定/実測なし（公式ページ・現地確認を実施）"
            missing.add("river")
        return view, missing

    nearest, upstream = river_ctx.get("nearest"), river_ctx.get("upstream")
    if nearest:
        view["source_river"] = (
            "SUIBOSAI-OPEN" if nearest["source"] == "SUIBOSAI-OPEN" else nearest["source"])
        if not _is_fresh_observation(nearest, now):
            missing.add("river")
            view["river_note"] = f"{nearest['name']}: 更新遅延/欠測"
            view["river_state"] = "stale"
        else:
            view["water_level_m"] = nearest["water_level_m"]
            trend, rate = _derive_trend(nearest)
            view["water_level_trend"] = trend
            view["water_level_rate_m_h"] = rate
            if view["water_level_m"] is None:
                # 水位実測のない観測所は河川判定の主要データを欠測扱い
                missing.add("river")
                view["river_note"] = f"{nearest['name']}: 水位データなし"
                view["river_state"] = "stale"
            else:
                view["river_note"] = (
                    f"{nearest['name']}: {view['water_level_m']}m ({nearest['observed_at']})")
                view["river_state"] = trend or ("rising" if site.river_state == "rising"
                                                else "none")
    else:
        missing.add("river")
        view["river_note"] = "最寄り観測所の実測なし（公式ページ・現地確認を実施）"
        view["river_state"] = "stale"

    # 上流雨量: 上流観測所を優先、無ければ最寄り観測所の雨量で補完（手動入力の検証を容易化）
    for entry in (upstream, nearest):
        if entry and _is_fresh_observation(entry, now) and entry.get("rainfall_mm_h") is not None:
            view["upstream_rain_mm_h"] = entry["rainfall_mm_h"]
            view["upstream_rain_source"] = entry["source"]
            break

    if view["water_level_trend"] is None:
        view["water_level_trend"] = "rising" if site.river_state == "rising" else None
    return view, missing


def build_reading(work_type: str, wr: dict, site: Site,
                  pref_warnings: set | None = None,
                  river_ctx: dict | None = None,
                  river_view: tuple[dict, set[str]] | None = None) -> Reading:
    pref_warnings = pref_warnings or set()
    r = Reading(
        precip_mm_h=wr.get("precip_mm_h"), temp_c=wr.get("temp_c"),
        wind_ms=wr.get("wind_ms"), gust_ms=wr.get("gust_ms"),
        humidity_pct=wr.get("humidity_pct"), wbgt=wr.get("wbgt"),
        missing=set(wr.get("missing") or set()),
    )
    if work_type == "river":
        if river_view is None:
            river_view = _river_view(river_ctx, site)
        view, river_missing = river_view
        r.upstream_rain_mm_h = view["upstream_rain_mm_h"]
        r.upstream_rain_source = view["upstream_rain_source"]
        r.water_level_trend = view["water_level_trend"]
        r.water_level_m = view["water_level_m"]
        r.water_level_rate_m_h = view["water_level_rate_m_h"]
        r.source_river = view["source_river"]
        # 公式優先: 気象庁の洪水警報/注意報があれば洪水フラグ（§8.3-6）
        r.flood_warning = (bool(site.flood_info)
                           or "洪水警報" in pref_warnings or "洪水注意報" in pref_warnings)
        r.missing = set(r.missing) | river_missing
    if "大雨警報" in pref_warnings:
        r.heavy_rain_warning = True
    return r


async def assess_site(site: Site, *, fetch=None, work_type: str | None = None,
                      start: str | None = None, end: str | None = None,
                      warnmap: dict | None = None, th: dict | None = None,
                      river_ctx: dict | None = None, db: Session | None = None) -> dict:
    """1現場を評価してダッシュボード/詳細用 dict を返す。"""
    wt = work_type or site.work_type
    if fetch is None:
        data = await _cached_fetch(site.latitude, site.longitude, site.id)
    else:
        data = await fetch(site.latitude, site.longitude)
    if warnmap is None:
        warnmap = await jma_warnings.get_active_warnings()
    pref_warnings = jma_warnings.warnings_for_site(warnmap, site.loc)
    if river_ctx is None and db is not None and wt == "river":
        # #74 注記: Session はスレッド非安全のため共有セッションは to_thread 化しない。
        # 複数現場評価では asyncio.gather 内でも同期読取は直列化され、競合しない。
        river_ctx = _load_river_context(db, site.id)
    river_view = _river_view(river_ctx, site) if wt == "river" else None

    status = data.get("status", "ERROR")
    points = data.get("points", [])
    wr = open_meteo.window_reading(points, start, end) if points else {
        "precip_mm_h": None, "temp_c": None, "temp_lo": None, "wind_ms": None,
        "gust_ms": None, "humidity_pct": None, "wbgt": None,
        "missing": {"precip", "temp", "wind", "wbgt"},
    }

    # 公式優先（§5.3）: 環境省WBGT予報が時間帯内で取得できた場合のみ推定値を公式値で上書き。
    # 取得失敗・時間帯外・未設定なら従来の推定値のまま（フォールバックを隠さない）。
    wbgt_official = None
    wbgt_station_code = settings.wbgt_station_code
    if db is not None:
        resolved = wbgt_env.resolve_site_wbgt_station(
            db, site.id, site.latitude, site.longitude)
        if resolved:
            wbgt_station_code = resolved["station_code"]
    if wbgt_station_code:
        wdata = await _cached_wbgt(wbgt_station_code)
        wbgt_official = wbgt_env.window_max(wdata.get("points", []), start, end)
        if wbgt_official is not None:
            wr = dict(wr)
            wr["wbgt"] = wbgt_official
            wr["missing"] = set(wr.get("missing") or set()) - {"wbgt"}

    reading = build_reading(wt, wr, site, pref_warnings, river_ctx=river_ctx,
                            river_view=river_view)
    if status == "STALE":
        reading.stale_weather = True  # 前回取得値での参考表示を判定理由に明示（§5.3）
    # #35: 実効閾値はDBが単一の真実。永続化を伴う評価は呼び出し側が fresh 解決した
    # th を注入する(表示用は th=None → 短TTLキャッシュ解決で足りる)
    # #74 緩和: effective_th は内部で SessionLocal を生成するためスレッド化しても安全。
    # 共有セッション（river/wbgt）はスレッド非安全のため対象外（上記コメント参照）。
    effective = th or await asyncio.to_thread(rules_service.effective_th)
    decision = evaluate(wt, reading, th=effective)

    return {
        "id": site.id, "name": site.name, "code": site.site_code, "loc": site.loc,
        "work": wt, "level": decision["overall_level"], "levelLabel": decision["overall_label"],
        "summary": decision["summary"],
        "rainNow": wr.get("precip_mm_h"), "rainPeak": wr.get("precip_mm_h"),
        "windMax": wr.get("wind_ms"), "gust": wr.get("gust_ms"),
        "tempHi": wr.get("temp_c"), "tempLo": wr.get("temp_lo"),
        "wbgt": wr.get("wbgt"), "wbgtDerived": wbgt_official is None,
        "wbgtStation": wbgt_station_code or None,
        "river": river_view[0]["river_note"] if river_view else site.river_note,
        "riverState": river_view[0]["river_state"] if river_view else site.river_state,
        "riverSource": river_view[0]["source_river"] if river_view else "DS-RIVER-GO",
        "reasons": [{"severity": x["severity"], "text": x["message"],
                     "source": x["source_id"], "value": x["observed_value"]}
                    for x in decision["reasons"]],
        "reasonsRaw": decision["reasons"],  # reason_code 付きの生出力（永続化用）
        "dataQuality": decision["data_quality_summary"],
        "weatherStatus": status,
        "fetchedAt": data.get("fetched_at"),
        "updated": datetime.now(JST).strftime("%H:%M"),
    }


async def assess_all(sites: list[Site], *, fetch=None,
                     db: Session | None = None) -> list[dict]:
    # 警報マップは一度だけ取得して全現場に渡す（サンダリングハード回避）
    warnmap = await jma_warnings.get_active_warnings()
    return await asyncio.gather(
        *[assess_site(s, fetch=fetch, warnmap=warnmap, db=db) for s in sites])


async def assess_decision(site: Site, work_type: str, start: str | None, end: str | None,
                          *, fetch=None, th: dict | None = None,
                          db: Session | None = None) -> dict:
    """作業判断画面の評価。判定エンジン出力（設計 §8.2）＋参照情報を返す。

    閾値dict(th)は応答へ含めない: 閾値の閲覧は /api/admin/rules の権限境界に限定し、
    一般ユーザー向け応答からの漏えいを防ぐ（#35 対抗レビュー4巡目）。
    """
    card = await assess_site(site, fetch=fetch, work_type=work_type, start=start, end=end,
                             th=th, db=db)
    return {
        "siteId": site.id, "siteName": site.name, "workType": work_type,
        "overall_level": card["level"], "overall_label": card["levelLabel"],
        "summary": card["summary"], "reasons": card["reasons"],
        "reasonsRaw": card["reasonsRaw"],
        "data_quality_summary": card["dataQuality"],
        "weatherStatus": card["weatherStatus"], "fetchedAt": card["fetchedAt"],
        "refs": ["気象: Open-Meteo",
                 f"河川: {card['riverSource']}",
                 "WBGT: 環境省(公式予報)" if not card["wbgtDerived"] else "WBGT: 環境省(推定)",
                 "警報: 気象庁"],
        "levelLabels": LEVEL_LABELS,
    }
