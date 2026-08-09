"""50年確率波の極値統計解析（デモ・シミュレーション版）。

NOWPHAS 長期観測データの蓄積前段階として、地点IDから決定的に生成した年最大波高
（30年分・シミュレーション）へ Gumbel / Weibull 分布を当てはめ、50年・100年再現
期間波高を算出する。結果には data_type="synthetic" と警告を含め、設計利用には
NOWPHAS 実測データでの再解析が必要であることを常に明示する。
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
SAMPLE_YEARS = 30


def _seed(site_id: str) -> int:
    return int(hashlib.sha256(f"CWWD-EXTREME-{site_id}".encode()).hexdigest(), 16) % (2 ** 32)


def _annual_maxima(site_id: str, latitude: float, years: int = SAMPLE_YEARS) -> list[float]:
    """地点の緯度から想定する波浪規模を基に、決定的な年最大波高を生成する。"""
    base = max(1.2, 2.0 + (43.0 - latitude) * 0.055)
    loc = base * 0.88
    scale = base * 0.20
    rng = random.Random(_seed(site_id))
    vals = []
    for _ in range(years):
        u = rng.random()
        # Gumbel 逆関数（u は (0,1) を保証）
        u = min(max(u, 1e-9), 1 - 1e-9)
        vals.append(round(max(0.3, loc - scale * math.log(-math.log(u))), 2))
    return vals


def _gumbel_fit(x: list[float]) -> dict:
    n = len(x)
    mean = sum(x) / n
    var = sum((v - mean) ** 2 for v in x) / (n - 1) if n > 1 else 0.0
    scale = math.sqrt(max(var, 1e-9) * 6.0 / (math.pi ** 2))
    loc = mean - 0.5772156649015329 * scale
    return {"loc": round(loc, 3), "scale": round(scale, 3)}


def _gumbel_quantile(return_years: float, fit: dict) -> float:
    p = 1.0 - 1.0 / return_years
    return fit["loc"] - fit["scale"] * math.log(-math.log(p))


def _weibull_fit(x: list[float]) -> dict:
    """2母数 Weibull（位置0）を確率プロットの線形回帰で推定する。"""
    xs = sorted(v for v in x if v > 0)
    n = len(xs)
    if n < 3:
        return {"shape": 1.5, "scale": sum(x) / n if n else 1.0}
    lx = [math.log(v) for v in xs]
    ly = [math.log(-math.log(1.0 - (i + 0.3) / (n + 0.4))) for i in range(n)]
    mx = sum(lx) / n
    my = sum(ly) / n
    sxx = sum((a - mx) ** 2 for a in lx)
    sxy = sum((a - mx) * (b - my) for a, b in zip(lx, ly))
    shape = sxy / sxx if sxx > 0 else 1.5
    if shape <= 0:
        shape = 1.5
    scale = math.exp(mx - my / shape) if shape else 1.0
    return {"shape": round(shape, 3), "scale": round(scale, 3)}


def _weibull_quantile(return_years: float, fit: dict) -> float:
    p = 1.0 - 1.0 / return_years
    return fit["scale"] * (-math.log(1.0 - p)) ** (1.0 / fit["shape"])


def _sample_error(x: list[float], fit: dict, method: str) -> float:
    """当てはまり誤差（観測順序統計量と理論分位点のRMSE）。小さい方が良い。"""
    n = len(x)
    xs = sorted(x)
    errs = []
    for i, v in enumerate(xs):
        p = (i + 0.5) / n
        if method == "gumbel":
            q = fit["loc"] - fit["scale"] * math.log(-math.log(p))
        else:
            q = fit["scale"] * (-math.log(1.0 - p)) ** (1.0 / fit["shape"])
        errs.append((v - q) ** 2)
    return math.sqrt(sum(errs) / n) if errs else 0.0


def analyze_site(site_id: str, latitude: float, longitude: float,
                 years: int = SAMPLE_YEARS) -> dict:
    """1地点の極値解析を行い、Gumbel/Weibull 双方と代表値 h50/h100 を返す。"""
    maxima = _annual_maxima(site_id, latitude, years)
    gumbel = _gumbel_fit(maxima)
    weibull = _weibull_fit(maxima)
    g_err = _sample_error(maxima, gumbel, "gumbel")
    w_err = _sample_error(maxima, weibull, "weibull")
    primary = "weibull" if w_err <= g_err else "gumbel"
    g50 = _gumbel_quantile(50, gumbel)
    g100 = _gumbel_quantile(100, gumbel)
    w50 = _weibull_quantile(50, weibull)
    w100 = _weibull_quantile(100, weibull)
    h50 = w50 if primary == "weibull" else g50
    h100 = w100 if primary == "weibull" else g100
    return {
        "siteId": site_id,
        "latitude": latitude,
        "longitude": longitude,
        "dataType": "synthetic",
        "sampleYears": years,
        "primaryMethod": primary,
        "h50": round(h50, 2),
        "h100": round(h100, 2),
        "methods": {
            "gumbel": {**gumbel, "h50": round(g50, 2), "h100": round(g100, 2),
                       "rmse": round(g_err, 4)},
            "weibull": {**weibull, "h50": round(w50, 2), "h100": round(w100, 2),
                        "rmse": round(w_err, 4)},
        },
        "warnings": [
            "デモ・シミュレーションデータ（NOWPHAS 長期観測は未接続）",
            "設計利用には NOWPHAS 実測データでの再解析が必要です",
        ],
    }


def analyze_sites(sites: list[dict]) -> dict:
    """複数地点の一括解析。sites は {siteId, name, loc, latitude, longitude} の並び。"""
    rows = []
    for s in sites:
        row = analyze_site(s["siteId"], s["latitude"], s["longitude"])
        row["name"] = s["name"]
        row["loc"] = s.get("loc", "")
        rows.append(row)
    return {
        "source": "DEMO-EXTREME",
        "note": "極値統計（Gumbel / Weibull）による再現期間波高のデモ解析。"
                "データは地点IDから決定的に生成したシミュレーションのため、"
                "設計条件の決定には使用しないでください。",
        "generatedAt": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "sites": rows,
    }
