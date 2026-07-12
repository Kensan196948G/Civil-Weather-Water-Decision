"""評価サービス: 気象取得（Open-Meteo）→ Reading 構築 → 判定エンジン を束ねる。

外部取得失敗は隠さず、欠測として判定に反映する（設計 §5.3 / §15.2）。
fetch は注入可能（テストはネットワーク非依存）。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from .data_collectors import jma_warnings, open_meteo, wbgt_env
from .decision_engine import LEVEL_LABELS, Reading, evaluate
from ..core.config import settings
from ..models import Site

JST = timezone(timedelta(hours=9))
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 300  # 秒


async def _cached_fetch(lat: float, lon: float, key: str) -> dict:
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    data = await open_meteo.fetch_forecast(lat, lon)
    _CACHE[key] = (now, data)
    return data


async def _cached_wbgt(station: str) -> dict:
    """環境省WBGT予報のキャッシュ付き取得（地点は全現場共通のためTTL共有で十分）。"""
    key = f"wbgt:{station}"
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    data = await wbgt_env.fetch_forecast(station)
    _CACHE[key] = (now, data)
    return data


def clear_cache() -> None:
    _CACHE.clear()


def build_reading(work_type: str, wr: dict, site: Site,
                  pref_warnings: set | None = None) -> Reading:
    pref_warnings = pref_warnings or set()
    r = Reading(
        precip_mm_h=wr.get("precip_mm_h"), temp_c=wr.get("temp_c"),
        wind_ms=wr.get("wind_ms"), gust_ms=wr.get("gust_ms"),
        humidity_pct=wr.get("humidity_pct"), wbgt=wr.get("wbgt"),
        missing=set(wr.get("missing") or set()),
    )
    if work_type == "river":
        r.upstream_rain_mm_h = wr.get("precip_mm_h")        # PoC: 降雨を上流雨量proxy
        r.water_level_trend = "rising" if site.river_state == "rising" else None
        # 公式優先: 気象庁の洪水警報/注意報があれば洪水フラグ（§8.3-6）
        r.flood_warning = (bool(site.flood_info)
                           or "洪水警報" in pref_warnings or "洪水注意報" in pref_warnings)
        if site.river_state == "stale":
            r.missing = set(r.missing) | {"river"}
    if "大雨警報" in pref_warnings:
        r.heavy_rain_warning = True
    return r


async def assess_site(site: Site, *, fetch=None, work_type: str | None = None,
                      start: str | None = None, end: str | None = None,
                      warnmap: dict | None = None) -> dict:
    """1現場を評価してダッシュボード/詳細用 dict を返す。"""
    wt = work_type or site.work_type
    if fetch is None:
        data = await _cached_fetch(site.latitude, site.longitude, site.id)
    else:
        data = await fetch(site.latitude, site.longitude)
    if warnmap is None:
        warnmap = await jma_warnings.get_active_warnings()
    pref_warnings = jma_warnings.warnings_for_site(warnmap, site.loc)

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
    if settings.wbgt_station_code:
        wdata = await _cached_wbgt(settings.wbgt_station_code)
        wbgt_official = wbgt_env.window_max(wdata.get("points", []), start, end)
        if wbgt_official is not None:
            wr = dict(wr)
            wr["wbgt"] = wbgt_official
            wr["missing"] = set(wr.get("missing") or set()) - {"wbgt"}

    reading = build_reading(wt, wr, site, pref_warnings)
    decision = evaluate(wt, reading)

    return {
        "id": site.id, "name": site.name, "code": site.site_code, "loc": site.loc,
        "work": wt, "level": decision["overall_level"], "levelLabel": decision["overall_label"],
        "summary": decision["summary"],
        "rainNow": wr.get("precip_mm_h"), "rainPeak": wr.get("precip_mm_h"),
        "windMax": wr.get("wind_ms"), "gust": wr.get("gust_ms"),
        "tempHi": wr.get("temp_c"), "tempLo": wr.get("temp_lo"),
        "wbgt": wr.get("wbgt"), "wbgtDerived": wbgt_official is None,
        "river": site.river_note, "riverState": site.river_state,
        "reasons": [{"severity": x["severity"], "text": x["message"],
                     "source": x["source_id"], "value": x["observed_value"]}
                    for x in decision["reasons"]],
        "reasonsRaw": decision["reasons"],  # reason_code 付きの生出力（永続化用）
        "dataQuality": decision["data_quality_summary"],
        "weatherStatus": status,
        "fetchedAt": data.get("fetched_at"),
        "updated": datetime.now(JST).strftime("%H:%M"),
    }


async def assess_all(sites: list[Site], *, fetch=None) -> list[dict]:
    # 警報マップは一度だけ取得して全現場に渡す（サンダリングハード回避）
    warnmap = await jma_warnings.get_active_warnings()
    return await asyncio.gather(*[assess_site(s, fetch=fetch, warnmap=warnmap) for s in sites])


async def assess_decision(site: Site, work_type: str, start: str | None, end: str | None,
                          *, fetch=None) -> dict:
    """作業判断画面の評価。判定エンジン出力（設計 §8.2）＋参照情報を返す。"""
    card = await assess_site(site, fetch=fetch, work_type=work_type, start=start, end=end)
    return {
        "siteId": site.id, "siteName": site.name, "workType": work_type,
        "overall_level": card["level"], "overall_label": card["levelLabel"],
        "summary": card["summary"], "reasons": card["reasons"],
        "reasonsRaw": card["reasonsRaw"],
        "data_quality_summary": card["dataQuality"],
        "weatherStatus": card["weatherStatus"], "fetchedAt": card["fetchedAt"],
        "refs": ["気象: Open-Meteo", "河川: 川の防災情報",
                 "WBGT: 環境省(公式予報)" if not card["wbgtDerived"] else "WBGT: 環境省(推定)",
                 "警報: 気象庁"],
        "levelLabels": LEVEL_LABELS,
    }
