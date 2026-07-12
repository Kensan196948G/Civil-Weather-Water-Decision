"""環境省 WBGT コレクタのテスト（httpx.MockTransport でネット非依存）。

SAMPLE_CSV は実サービスから取得した実物（samples/wbgt-env-yohou-sample.csv と同形）。
"""
import httpx
import pytest

from app.services.data_collectors import wbgt_env as we

SAMPLE_CSV = (
    ",,2026071215,2026071218,2026071221,2026071224,2026071303,2026071306,2026071309,"
    "2026071312,2026071315,2026071318,2026071321,2026071324,2026071403,2026071406,"
    "2026071409,2026071412,2026071415,2026071418,2026071421,2026071424\n"
    "44132,2026/07/12 14:25, 280, 250, 240, 240, 230, 230, 260, 260, 270, 260, 250,"
    " 250, 240, 270, 300, 300, 310, 260, 260, 250\n"
)


def test_parse_points_scaling_and_flags():
    d = we.parse_forecast_csv(SAMPLE_CSV)
    assert d["source_id"] == "DS-WBGT"
    assert d["station"] == "44132"
    assert len(d["points"]) == 20
    assert d["points"][0] == {"time": "2026-07-12T15:00", "wbgt": 28.0, "quality_flag": "OK"}
    assert all(p["quality_flag"] == "OK" for p in d["points"])


def test_parse_hh24_rolls_over_to_next_day():
    d = we.parse_forecast_csv(SAMPLE_CSV)
    times = [p["time"] for p in d["points"]]
    # 2026071224 → 翌日00時 / 2026071424 → 7/15 00時
    assert "2026-07-13T00:00" in times
    assert "2026-07-15T00:00" in times
    assert not any(t.endswith("T24:00") for t in times)


def test_parse_missing_value_flagged():
    csv = ",,2026071215,2026071218\n44132,2026/07/12 14:25, 280,\n"
    d = we.parse_forecast_csv(csv)
    assert d["points"][0]["wbgt"] == 28.0
    assert d["points"][1]["wbgt"] is None
    assert d["points"][1]["quality_flag"] == "MISSING"


def test_parse_rejects_broken_shape():
    with pytest.raises(ValueError):
        we.parse_forecast_csv("only-one-line")


def test_window_max_filters_by_work_window():
    points = we.parse_forecast_csv(SAMPLE_CSV)["points"]
    # 7/14 09:00-15:00 の作業時間帯 → 300,300,310 の最大
    assert we.window_max(points, "2026-07-14T09:00", "2026-07-14T15:00") == 31.0
    # 時間帯に予報点が無い場合は None（公式値を引き延ばさない）
    assert we.window_max(points, "2026-07-14T13:00", "2026-07-14T13:30") is None
    # 指定なしは先頭8点（≒24時間）
    assert we.window_max(points) == 28.0


def test_window_max_all_missing_returns_none():
    assert we.window_max([{"time": "2026-07-12T15:00", "wbgt": None}]) is None
    assert we.window_max([]) is None


@pytest.mark.asyncio
async def test_fetch_forecast_ok_with_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "yohou_44132.csv" in str(request.url)
        return httpx.Response(200, text=SAMPLE_CSV)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await we.fetch_forecast("44132", client=client)
    assert data["status"] == "OK"
    assert len(data["points"]) == 20


@pytest.mark.asyncio
async def test_fetch_forecast_error_is_not_hidden():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)  # 夏期外はCSV自体が404になる想定

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await we.fetch_forecast("44132", client=client)
    assert data["status"] == "ERROR"
    assert data["points"] == []
    assert data["error"]


@pytest.mark.asyncio
async def test_fetch_forecast_unconfigured_station():
    data = await we.fetch_forecast(None)  # settings 既定は空文字
    assert data["status"] == "ERROR"
    assert "not configured" in data["error"]


@pytest.mark.asyncio
async def test_assess_site_overlays_official_wbgt(monkeypatch):
    """地点コード設定時のみ公式予報値が推定値を上書きし wbgtDerived が False になる。"""
    from app.core.config import settings
    from app.models import Site
    from app.services import assessment
    from app.services.data_collectors import open_meteo

    assessment.clear_cache()
    monkeypatch.setattr(settings, "wbgt_station_code", "44132")

    async def fake_wbgt(station_code=None, **kw):
        return {"source_id": "DS-WBGT", "status": "OK", "error": None,
                "points": [{"time": "2026-06-20T09:00", "wbgt": 31.5, "quality_flag": "OK"}]}

    monkeypatch.setattr(we, "fetch_forecast", fake_wbgt)

    om_sample = {
        "timezone": "Asia/Tokyo",
        "hourly": {
            "time": ["2026-06-20T08:00", "2026-06-20T09:00"],
            "temperature_2m": [28.0, 30.5], "precipitation": [0.0, 0.0],
            "wind_speed_10m": [3.0, 3.0], "wind_gusts_10m": [5.0, 5.0],
            "relative_humidity_2m": [70, 65], "weather_code": [1, 1],
        },
    }

    async def fake_om(lat, lon, **kw):
        norm = open_meteo.normalize(om_sample)
        norm.update(status="OK", fetched_at="2026-06-20T08:00:00Z", error=None)
        return norm

    site = Site(id="S99", site_code="CW-T", name="テスト現場", loc="X市",
                latitude=35.7, longitude=139.7, work_type="earthwork",
                project_type="公共", river_work_flag=False, river_state="none",
                river_note="", flood_info=False, manager="試験")
    card = await assessment.assess_site(site, fetch=fake_om, warnmap={})
    assert card["wbgt"] == 31.5
    assert card["wbgtDerived"] is False

    # 地点コード未設定なら従来どおり推定値（挙動不変）
    assessment.clear_cache()
    monkeypatch.setattr(settings, "wbgt_station_code", "")
    card2 = await assessment.assess_site(site, fetch=fake_om, warnmap={})
    assert card2["wbgtDerived"] is True
    assert card2["wbgt"] != 31.5
    assessment.clear_cache()
