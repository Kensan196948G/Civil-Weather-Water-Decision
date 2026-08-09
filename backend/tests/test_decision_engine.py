"""判定エンジンの単体テスト（詳細設計 §18.2 TC-003〜006 を含む）。"""
from app.services.decision_engine import Reading, evaluate


def test_river_flood_is_stop():
    r = Reading(upstream_rain_mm_h=8, water_level_trend="rising", flood_warning=True)
    res = evaluate("river", r)
    assert res["overall_label"] == "中止検討"
    assert res["overall_level"] == 2
    assert any(x["reason_code"] == "flood_warning" for x in res["reasons"])


def test_river_upstream_rain_records_provider_and_value():
    r = Reading(upstream_rain_mm_h=5.0, source_river="SUIBOSAI-OPEN")
    res = evaluate("river", r)
    reason = next(x for x in res["reasons"] if x["reason_code"] == "upstream_rain")
    assert reason["source_id"] == "SUIBOSAI-OPEN"
    assert reason["observed_value"] == "上流雨量 5.0mm/h"
    assert res["overall_level"] == 1


def test_river_upstream_rain_source_prefers_rainfall_provider():
    """上流雨量は値を提供した観測所の出典を記録する（最寄りと異なる場合）。"""
    r = Reading(upstream_rain_mm_h=5.0, source_river="MANUAL",
                upstream_rain_source="SUIBOSAI-OPEN")
    res = evaluate("river", r)
    reason = next(x for x in res["reasons"] if x["reason_code"] == "upstream_rain")
    assert reason["source_id"] == "SUIBOSAI-OPEN"


def test_river_rising_records_measured_level_and_rate():
    r = Reading(water_level_trend="rising", water_level_m=2.35,
                water_level_rate_m_h=0.6, source_river="MANUAL")
    res = evaluate("river", r)
    reason = next(x for x in res["reasons"] if x["reason_code"] == "water_level_rising")
    assert reason["source_id"] == "MANUAL"
    assert reason["observed_value"] == "水位 2.35m（上昇 0.60m/h）"


def test_river_all_missing_is_unavailable():
    r = Reading(missing={"river"})
    res = evaluate("river", r)
    assert res["overall_label"] == "確認不能"
    assert res["overall_level"] == 3


def test_river_known_risk_dominates_over_missing():
    # 洪水(2) + 河川データ欠測(3) → 既知リスク(中止検討)を優先しつつ欠測を品質サマリに明記
    r = Reading(flood_warning=True, missing={"river"})
    res = evaluate("river", r)
    assert res["overall_level"] == 2
    assert "欠測" in res["data_quality_summary"]


def test_official_heavy_rain_warning_raises_level():
    # 気象庁 大雨警報（公式優先）→ 河川/土工で中止検討
    assert evaluate("river", Reading(heavy_rain_warning=True))["overall_level"] == 2
    assert evaluate("earthwork", Reading(heavy_rain_warning=True))["overall_label"] == "中止検討"


def test_concrete_high_temp_is_caution():
    r = Reading(precip_mm_h=0.4, temp_c=31, wind_ms=7)
    res = evaluate("concrete", r)
    assert res["overall_label"] == "注意"
    assert any(x["reason_code"] == "high_temperature" for x in res["reasons"])


def test_concrete_heavy_rain_is_stop():
    r = Reading(precip_mm_h=6.0, temp_c=25)
    res = evaluate("concrete", r)
    assert res["overall_level"] == 2


def test_earthwork_light_rain_is_caution():
    r = Reading(precip_mm_h=1.5)
    res = evaluate("earthwork", r)
    assert res["overall_label"] == "注意"


def test_pavement_clear_is_normal_not_workable():
    r = Reading(precip_mm_h=0.0, temp_c=25)
    res = evaluate("pavement", r)
    assert res["overall_level"] == 0
    assert res["overall_label"] == "通常"
    # 「作業可能」と断定しない
    assert "作業可能" not in res["summary"]


def test_crane_gust_is_stop():
    r = Reading(wind_ms=9, gust_ms=14)
    res = evaluate("crane", r)
    assert res["overall_level"] == 2


def test_crane_wind_only_is_caution():
    r = Reading(wind_ms=9, gust_ms=8)
    res = evaluate("crane", r)
    assert res["overall_label"] == "注意"


def test_crane_wind_missing_is_unavailable():
    r = Reading(missing={"wind"})
    res = evaluate("crane", r)
    assert res["overall_level"] == 3


def test_heat_wbgt_caution_and_danger():
    assert evaluate("heat", Reading(wbgt=29))["overall_label"] == "注意"
    assert evaluate("heat", Reading(wbgt=31))["overall_label"] == "中止検討"
    assert evaluate("heat", Reading(missing={"wbgt"}))["overall_level"] == 3


def test_marine_wave_caution():
    r = Reading(wave_height_m=1.5, wave_period_s=8.0, wind_ms=5.0, gust_ms=8.0)
    res = evaluate("marine", r)
    assert res["overall_label"] == "注意"
    assert any(x["reason_code"] == "wave_caution" for x in res["reasons"])


def test_marine_wave_stop():
    r = Reading(wave_height_m=2.5, wave_period_s=10.0, wind_ms=4.0, gust_ms=6.0)
    res = evaluate("marine", r)
    assert res["overall_level"] == 2
    assert any(x["reason_code"] == "wave_stop" for x in res["reasons"])


def test_marine_swell_and_wind_are_caution():
    r = Reading(wave_height_m=0.5, swell_wave_height_m=1.2, wind_ms=7.0)
    res = evaluate("marine", r)
    assert res["overall_label"] == "注意"
    codes = {x["reason_code"] for x in res["reasons"]}
    assert "swell_risk" in codes
    assert "marine_wind" in codes


def test_marine_gust_is_stop():
    r = Reading(wave_height_m=0.5, gust_ms=14.0)
    res = evaluate("marine", r)
    assert res["overall_level"] == 2
    assert any(x["reason_code"] == "marine_gust" for x in res["reasons"])


def test_marine_fog_is_caution():
    r = Reading(wave_height_m=0.5, fog=True)
    res = evaluate("marine", r)
    assert res["overall_level"] == 1
    assert any(x["reason_code"] == "fog_visibility" for x in res["reasons"])


def test_marine_missing_wave_and_wind_is_unavailable():
    r = Reading(missing={"wave", "wind"})
    res = evaluate("marine", r)
    assert res["overall_level"] == 3
    assert res["overall_label"] == "確認不能"


def test_marine_wave_dominates_over_missing_wind():
    # 波高が中止検討なら風欠測（確認不能）より既知リスクを優先
    r = Reading(wave_height_m=2.5, missing={"wind"})
    res = evaluate("marine", r)
    assert res["overall_level"] == 2


def test_output_shape_matches_design():
    res = evaluate("river", Reading(flood_warning=True))
    for key in ("work_type", "overall_level", "overall_label", "summary",
                "reasons", "data_quality_summary"):
        assert key in res
    for reason in res["reasons"]:
        for key in ("severity", "reason_code", "message", "source_id", "observed_value"):
            assert key in reason
