"""50年確率波（Gumbel / Weibull 極値解析・デモ版）の単体テスト。"""
from app.services import extreme


def test_gumbel_fit_recovers_known_parameters():
    import math

    loc, scale = 3.0, 0.5
    xs = [loc - scale * math.log(-math.log((i - 0.5) / 100)) for i in range(1, 101)]
    fit = extreme._gumbel_fit(xs)
    assert abs(fit["loc"] - loc) < 0.2
    assert abs(fit["scale"] - scale) < 0.12
    q50 = extreme._gumbel_quantile(50, fit)
    assert 4.5 < q50 < 6.0


def test_weibull_fit_recovers_known_parameters():
    import math

    shape, scale = 2.0, 4.0
    xs = [scale * (-math.log(1.0 - (i - 0.5) / 100)) ** (1.0 / shape)
          for i in range(1, 101)]
    fit = extreme._weibull_fit(xs)
    assert abs(fit["shape"] - shape) < 0.5
    assert abs(fit["scale"] - scale) < 0.8
    q50 = extreme._weibull_quantile(50, fit)
    assert 7.5 < q50 < 13.0


def test_analyze_site_is_deterministic_and_flagged_synthetic():
    a = extreme.analyze_site("S12", 34.65, 135.43)
    b = extreme.analyze_site("S12", 34.65, 135.43)
    assert a == b
    assert a["dataType"] == "synthetic"
    assert a["sampleYears"] == extreme.SAMPLE_YEARS
    assert a["h100"] > a["h50"] > 0
    assert a["primaryMethod"] in ("gumbel", "weibull")
    assert set(a["methods"]) == {"gumbel", "weibull"}
    assert any("デモ" in w for w in a["warnings"])


def test_analyze_sites_shape():
    payload = extreme.analyze_sites([
        {"siteId": "S12", "name": "大阪港 ふ頭", "loc": "大阪府", "latitude": 34.65,
         "longitude": 135.43},
        {"siteId": "S16", "name": "那覇 臨港道路", "loc": "沖縄県", "latitude": 26.21,
         "longitude": 127.68},
    ])
    assert payload["source"] == "DEMO-EXTREME"
    assert len(payload["sites"]) == 2
    assert all(s["name"] and s["h50"] > 0 for s in payload["sites"])


def test_return_periods_endpoint(client):
    """GET /api/marine/return-periods が認証付きで解析結果を返す。"""
    r = client.get("/api/marine/return-periods")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "DEMO-EXTREME"
    assert len(body["sites"]) >= 1
    assert all(s["h100"] > s["h50"] > 0 for s in body["sites"])
