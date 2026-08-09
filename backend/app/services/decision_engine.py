"""判定エンジン（詳細設計 §8 / §9 準拠）

入力（作業種別＋作業時間帯の気象・河川・WBGT 値＋データ品質）から、
注意レベル（0通常 / 1注意 / 2中止検討 / 3確認不能）と理由・サマリを生成する。

設計原則:
- 「作業可能」と断定しない。レベル0は「主要注意条件なし」に留める。
- 欠測・遅延（MISSING/STALE）は隠さず data_quality_summary に明記する。
- 公式警報（気象庁）は外部予報より優先して注意レベルを引き上げる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

LEVEL_LABELS = {0: "通常", 1: "注意", 2: "中止検討", 3: "確認不能"}

# 初期閾値（仮値。社内安全基準・施工計画・発注者条件で調整する前提）
TH = {
    "rain_light": 1.0,    # mm/h 以上で注意
    "rain_heavy": 5.0,    # mm/h 以上で中止検討
    "wind_strong": 6.0,   # m/s 平均風速 注意
    "gust_stop": 13.0,    # m/s 突風 中止検討
    "temp_high": 31.0,    # ℃ 高温
    "temp_low": 4.0,      # ℃ 低温
    "wbgt_caution": 28.0, # 厳重警戒
    "wbgt_danger": 31.0,  # 危険相当
    "upstream_rain": 4.0, # mm/h 上流雨量
    "wave_caution": 1.0,  # m 有義波高 注意
    "wave_stop": 2.0,     # m 有義波高 中止検討
    "visibility_limited": 1000.0,  # m 視程 注意（現地確認推奨）
}

# 出荷時の既定閾値のエイリアス（#35: TH自体は変異させず、DB上書きは rules.effective_th() が
# 実効dictを都度構成して evaluate(th=...) へ渡す。マルチワーカーでも DB が単一の真実）
DEFAULT_TH = TH


@dataclass
class Reading:
    """作業時間帯に対応する代表値（予報/観測のピーク等）。値が None は欠測扱い。"""
    precip_mm_h: Optional[float] = None
    temp_c: Optional[float] = None
    wind_ms: Optional[float] = None
    gust_ms: Optional[float] = None
    humidity_pct: Optional[float] = None
    wbgt: Optional[float] = None
    upstream_rain_mm_h: Optional[float] = None
    upstream_rain_source: Optional[str] = None  # 上流雨量の実測提供元（補完元と一致させる）
    water_level_trend: Optional[str] = None  # 'rising' | 'stable' | 'stale' | None
    water_level_m: Optional[float] = None          # 直近実測の水位（理由の実測値表示用）
    water_level_rate_m_h: Optional[float] = None   # 直近2点から導出した上昇率
    flood_warning: bool = False              # 公式の洪水予報・水位到達情報・洪水警報等
    thunderstorm: bool = False               # 雷注意報等
    heavy_rain_warning: bool = False         # 気象庁 大雨警報
    # 海上作業（marine）用
    wave_height_m: Optional[float] = None     # 有義波高（最大）
    wave_period_s: Optional[float] = None     # 有義波周期
    swell_wave_height_m: Optional[float] = None  # うねり波高
    visibility_m: Optional[float] = None      # 視程（現状は気象コード由来の代理値/欠測）
    fog: bool = False                         # 濃霧・霧（気象コード45/48）
    source_marine: str = "DS-OPEN-METEO-MARINE"
    # 主要データが欠測/遅延しているフィールド名の集合（例 {"precip","wind"}）
    missing: set[str] = field(default_factory=set)
    # 気象データが前回取得値（更新遅延・STALE）であることを示す（§5.2/§5.3）
    stale_weather: bool = False
    source_weather: str = "DS-OPEN-METEO"
    source_river: str = "DS-RIVER-GO"
    source_wbgt: str = "DS-WBGT"
    source_official: str = "DS-JMA-XML"


@dataclass
class Rule:
    code: str
    work_types: tuple[str, ...]
    severity: int
    predicate: Callable[[Reading, dict], bool]  # (reading, 実効閾値dict) — #35: 閾値はDB上書き可
    message: str
    source: Callable[[Reading], str]
    value: Callable[[Reading], str]


def _has(r: Reading, attr: str) -> bool:
    return getattr(r, attr) is not None


# --- ルール定義（設計 §9 のルール例を閾値化） ---
RULES: list[Rule] = [
    # コンクリート打設
    Rule("rain_forecast", ("concrete", "earthwork", "pavement"), 1,
         lambda r, th: _has(r, "precip_mm_h") and th["rain_light"] <= r.precip_mm_h < th["rain_heavy"],
         "作業時間帯に降雨予報があります。施工・養生計画を確認してください。",
         lambda r: r.source_weather, lambda r: f"降雨 {r.precip_mm_h}mm/h"),
    Rule("heavy_rain_forecast", ("concrete", "earthwork", "pavement"), 2,
         lambda r, th: _has(r, "precip_mm_h") and r.precip_mm_h >= th["rain_heavy"],
         "降雨量が閾値を超える見込みです。延期または養生強化を検討してください。",
         lambda r: r.source_weather, lambda r: f"降雨 {r.precip_mm_h}mm/h"),
    Rule("high_temperature", ("concrete", "earthwork"), 1,
         lambda r, th: _has(r, "temp_c") and r.temp_c >= th["temp_high"],
         "気温が高くなる見込みです。暑中対策・養生計画を確認してください。",
         lambda r: r.source_weather, lambda r: f"最高気温 {r.temp_c}℃"),
    Rule("low_temperature", ("concrete", "pavement"), 1,
         lambda r, th: _has(r, "temp_c") and r.temp_c <= th["temp_low"],
         "気温が低くなる見込みです。低温時の施工条件を確認してください。",
         lambda r: r.source_weather, lambda r: f"気温 {r.temp_c}℃"),
    Rule("concrete_wind", ("concrete",), 1,
         lambda r, th: _has(r, "wind_ms") and r.wind_ms >= th["wind_strong"],
         "風速がやや高めです。養生・ポンプ作業条件を確認してください。",
         lambda r: r.source_weather, lambda r: f"最大風速 {r.wind_ms}m/s"),
    # クレーン作業
    Rule("strong_wind", ("crane",), 1,
         lambda r, th: _has(r, "wind_ms") and r.wind_ms >= th["wind_strong"],
         "風速が高くなる見込みです。クレーン仕様・吊荷条件を確認してください。",
         lambda r: r.source_weather, lambda r: f"平均風速 {r.wind_ms}m/s"),
    Rule("gust_risk", ("crane",), 2,
         lambda r, th: _has(r, "gust_ms") and r.gust_ms >= th["gust_stop"],
         "突風リスクがあります。作業中止・待機を検討してください。",
         lambda r: r.source_weather, lambda r: f"最大瞬間 {r.gust_ms}m/s"),
    Rule("thunderstorm", ("crane",), 2,
         lambda r, th: r.thunderstorm,
         "雷リスクがあります。屋外作業員の退避を含めて検討してください。",
         lambda r: r.source_official, lambda r: "雷注意報"),
    # 河川内作業
    Rule("upstream_rain", ("river",), 1,
         lambda r, th: _has(r, "upstream_rain_mm_h") and r.upstream_rain_mm_h >= th["upstream_rain"],
         "上流域で雨量が増加しています。水位上昇に注意してください（到達時間差）。",
         lambda r: r.upstream_rain_source or r.source_river,
         lambda r: f"上流雨量 {r.upstream_rain_mm_h}mm/h"),
    Rule("water_level_rising", ("river",), 1,
         lambda r, th: r.water_level_trend == "rising",
         "水位が上昇傾向です。退避基準と作業継続条件を確認してください。",
         lambda r: r.source_river,
         lambda r: (f"水位 {r.water_level_m}m（上昇 {r.water_level_rate_m_h:.2f}m/h）"
                    if r.water_level_m is not None and r.water_level_rate_m_h is not None
                    else (f"水位 {r.water_level_m}m" if r.water_level_m is not None
                          else "水位 上昇傾向"))),
    Rule("flood_warning", ("river",), 2,
         lambda r, th: r.flood_warning,
         "洪水関連情報が発表されています。河川内作業の中止・退避を検討してください。",
         lambda r: r.source_official, lambda r: "洪水注意情報"),
    # 公式警報（気象庁）優先（§8.3-6）: 大雨警報で河川・土工・打設・舗装を引き上げ
    Rule("heavy_rain_warning", ("river", "earthwork", "concrete", "pavement"), 2,
         lambda r, th: r.heavy_rain_warning,
         "気象庁が大雨警報を発表しています。作業中止・延期を含めて検討してください。",
         lambda r: r.source_official, lambda r: "大雨警報"),
    # 熱中症対策
    Rule("wbgt_caution", ("heat",), 1,
         lambda r, th: _has(r, "wbgt") and th["wbgt_caution"] <= r.wbgt < th["wbgt_danger"],
         "暑さ指数が上昇しています。水分・塩分補給と休憩を徹底してください。",
         lambda r: r.source_wbgt, lambda r: f"WBGT {r.wbgt}"),
    Rule("wbgt_danger", ("heat",), 2,
         lambda r, th: _has(r, "wbgt") and r.wbgt >= th["wbgt_danger"],
         "暑さ指数が高く危険域です。作業時間の変更・中止を検討してください。",
         lambda r: r.source_wbgt, lambda r: f"WBGT {r.wbgt}"),
    # 海上作業（#72 段5: 海象データ：全国版のデータソース確定に連動）
    Rule("wave_caution", ("marine",), 1,
         lambda r, th: _has(r, "wave_height_m") and th["wave_caution"] <= r.wave_height_m < th["wave_stop"],
         "有義波高が上昇しています。作業船・足場・荷役条件を確認してください。",
         lambda r: r.source_marine, lambda r: f"有義波高 {r.wave_height_m}m"),
    Rule("wave_stop", ("marine",), 2,
         lambda r, th: _has(r, "wave_height_m") and r.wave_height_m >= th["wave_stop"],
         "有義波高が閾値を超えています。海上作業の中止・待機を検討してください。",
         lambda r: r.source_marine, lambda r: f"有義波高 {r.wave_height_m}m"),
    Rule("swell_risk", ("marine",), 1,
         lambda r, th: _has(r, "swell_wave_height_m") and r.swell_wave_height_m >= th["wave_caution"],
         "うねりが入っています。作業船の動揺・係留条件を確認してください。",
         lambda r: r.source_marine, lambda r: f"うねり {r.swell_wave_height_m}m"),
    Rule("marine_wind", ("marine",), 1,
         lambda r, th: _has(r, "wind_ms") and r.wind_ms >= th["wind_strong"],
         "海上風が強まっています。作業船の運航・荷役条件を確認してください。",
         lambda r: r.source_weather, lambda r: f"海上風 {r.wind_ms}m/s"),
    Rule("marine_gust", ("marine",), 2,
         lambda r, th: _has(r, "gust_ms") and r.gust_ms >= th["gust_stop"],
         "突風リスクがあります。海上作業の中止・待機を検討してください。",
         lambda r: r.source_weather, lambda r: f"最大瞬間 {r.gust_ms}m/s"),
    Rule("fog_visibility", ("marine",), 1,
         lambda r, th: r.fog,
         "濃霧・霧が予想されます。視程が悪化するため作業船の航行・作業に注意してください。",
         lambda r: r.source_weather, lambda r: "濃霧・霧"),
]

# 作業種別ごとの「主要データ」。欠測時は確認不能理由を追加する。
MAIN_FIELDS = {
    "river": ["river"],
    "concrete": ["precip", "temp"],
    "earthwork": ["precip"],
    "pavement": ["precip", "temp"],
    "crane": ["wind"],
    "heat": ["wbgt"],
    "marine": ["wave", "wind"],
}

MISSING_MESSAGE = {
    "river": ("河川データが取得できません（更新遅延/欠測）。公式ページと現地確認を行ってください。", "DS-RIVER-GO"),
    "precip": ("主要気象データ（降雨）が取得できません。公式情報と現地確認を行ってください。", "DS-OPEN-METEO"),
    "temp": ("主要気象データ（気温）が取得できません。公式情報と現地確認を行ってください。", "DS-OPEN-METEO"),
    "wind": ("風速データが取得できません。現地風況と公式情報を確認してください。", "DS-OPEN-METEO"),
    "wbgt": ("WBGTデータが取得できません。気温・湿度・現場環境から追加確認してください。", "DS-WBGT"),
    "wave": ("波浪データが取得できません。NOWPHAS・気象庁等の公式情報と現地確認を優先してください。", "DS-OPEN-METEO-MARINE"),
    "wave_period": ("波周期データが取得できません。NOWPHAS・気象庁等の公式情報を確認してください。", "DS-OPEN-METEO-MARINE"),
    "swell": ("うねりデータが取得できません。NOWPHAS・気象庁等の公式情報を確認してください。", "DS-OPEN-METEO-MARINE"),
}

SUMMARY = {
    "river": {
        0: "河川・気象に主要な注意条件はありません。退避基準と公式情報の確認のうえ作業を検討できます。",
        1: "上流雨量・水位に注意が必要です。退避基準と公式情報を確認してください。",
        2: "上流雨量増加・水位上昇または洪水情報があります。河川内作業の中止・退避を検討してください。",
    },
    "concrete": {0: "主要な注意条件はありません。通常確認で打設を検討できます。",
                 1: "降雨・気温・風速に注意が必要です。打設・養生計画を確認してください。",
                 2: "降雨量が大きく打設条件に影響します。延期・養生強化を検討してください。"},
    "earthwork": {0: "主要な注意条件はありません。通常確認で作業を検討できます。",
                  1: "降雨・連続雨量に注意が必要です。法面・仮設排水・締固め品質を確認してください。",
                  2: "降雨量が大きく作業条件に影響します。延期・段取り変更を検討してください。"},
    "pavement": {0: "路面乾燥条件は良好です。予定どおり舗設を検討できます。",
                 1: "降雨・気温に注意が必要です。路面状態と材料条件を確認してください。",
                 2: "降雨量が大きく舗設条件に影響します。延期を検討してください。"},
    "crane": {0: "主要な注意条件はありません。クレーン作業を検討できます。",
              1: "風速が高めです。クレーン仕様・吊荷条件を確認してください。",
              2: "突風・雷リスクがあります。作業の中止・待機を検討してください。"},
    "heat": {0: "暑さ指数に主要な注意条件はありません。通常の水分補給で作業を検討できます。",
             1: "暑さ指数が上昇しています。水分補給・休憩を徹底してください。",
             2: "暑さ指数が危険域です。作業時間の変更・中止を検討してください。"},
    "marine": {0: "波浪・風に主要な注意条件はありません。通常の海上作業を検討できます。",
               1: "波高・うねり・風に注意が必要です。作業船・足場・荷役条件を確認してください。",
               2: "波高・突風が閾値を超えています。海上作業の中止・待機を検討してください。"},
}


def evaluate(work_type: str, reading: Reading, th: dict | None = None) -> dict:
    """判定を実行し、設計 §8.2 形式の結果 dict を返す。

    th: 実効閾値（未指定なら出荷時既定 TH）。#35: DB上書き値は呼び出し側が
    rules.effective_th() で解決して渡す（プロセスローカル変異を持たない）。
    """
    th = th or TH
    reasons = []
    for rule in RULES:
        if work_type not in rule.work_types:
            continue
        try:
            if rule.predicate(reading, th):
                reasons.append({
                    "severity": rule.severity,
                    "reason_code": rule.code,
                    "message": rule.message,
                    "source_id": rule.source(reading),
                    "observed_value": rule.value(reading),
                })
        except Exception:
            # 値不正は欠測同様に扱う（後段の欠測処理で拾う）
            continue

    # 欠測 → 確認不能理由
    missing_hit = [f for f in MAIN_FIELDS.get(work_type, []) if f in reading.missing]
    for f in missing_hit:
        msg, src = MISSING_MESSAGE.get(f, ("主要データが取得できません。", "DS-OPEN-METEO"))
        reasons.append({
            "severity": 3, "reason_code": f"missing_{f}", "message": msg,
            "source_id": src, "observed_value": "欠測/遅延",
        })

    # 更新遅延（STALE）→ 確認不能要因（§5.3: 値はあるが鮮度が古いことを隠さず明示）
    if reading.stale_weather:
        reasons.append({
            "severity": 3, "reason_code": "stale_weather",
            "message": "気象データが更新遅延しています（前回取得値で参考表示）。最新の公式情報・現地確認を優先してください。",
            "source_id": reading.source_weather, "observed_value": "更新遅延",
        })

    actionable = [r["severity"] for r in reasons if r["severity"] in (0, 1, 2)]
    max_actionable = max(actionable) if actionable else 0
    has_unavailable = any(r["severity"] == 3 for r in reasons)

    # 合成規則: 既知リスクが中止検討ならそれを優先、軽微なときだけ欠測→確認不能
    if max_actionable >= 2:
        overall = 2
    elif has_unavailable:
        overall = 3
    else:
        overall = max_actionable

    if overall == 3:
        summary = "主要データが不足しているため、システム上の判断材料が不足しています。公式情報・現地確認を優先してください。"
    else:
        summary = SUMMARY.get(work_type, {}).get(overall, "主要な注意条件はありません。")

    if has_unavailable:
        dq = "一部の主要データが欠測/遅延しています（確認不能要因あり）。公式情報・現地確認を併用してください。"
    else:
        dq = "主要データは取得済みです。"

    # 重大度の高い順に並べる
    reasons.sort(key=lambda r: (-(r["severity"] if r["severity"] != 3 else 1.5)))
    return {
        "work_type": work_type,
        "overall_level": overall,
        "thresholds_used": dict(th),  # 使用した実効閾値(監査再現用。persist側でJSON保存)
        "overall_label": LEVEL_LABELS[overall],
        "summary": summary,
        "reasons": reasons,
        "data_quality_summary": dq,
    }
