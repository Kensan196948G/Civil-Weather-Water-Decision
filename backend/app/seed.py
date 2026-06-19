"""DB 初期化＋サンプルデータ投入（PoC）。実在現場名は使わない（設計 §16.3）。

サンプル現場・観測所・作業種別・データソース状態・判断履歴を投入する。
WebUI(ClaudeDesign) のモックと同じ S01〜S06 を用い、見た目を一致させる。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.db import Base, SessionLocal, engine
from .models import DataSourceStatus, DecisionLog, Site, Station, WorkPlan, WorkType

WORK_TYPES = [
    ("river", "河川内作業", "#0e7d8f"), ("concrete", "コンクリート打設", "#7a5cc0"),
    ("earthwork", "土工", "#8a6d3b"), ("pavement", "舗装", "#566472"),
    ("crane", "クレーン作業", "#c0682c"), ("heat", "熱中症対策", "#cc4444"),
]

SITES = [
    # id, code, name, loc, lat, lon, work, project, river_flag, river_state, river_note, flood, manager
    ("S01", "CW-031", "北川 下流右岸 護岸工事", "X市 北川流域", 35.760, 139.780, "river", "公共", True, "rising", "上昇傾向 +0.4m/h", True, "山田"),
    ("S02", "CW-018", "東部丘陵 道路改良", "Y市 東部地区", 35.620, 139.460, "earthwork", "公共", False, "none", "近接なし", False, "鈴木"),
    ("S03", "CW-024", "中央高架橋 下部工", "Z市 中央地区", 35.460, 139.630, "concrete", "公共", False, "none", "近接なし", False, "佐藤"),
    ("S04", "CW-009", "南台ニュータウン 造成", "Y市 南台地区", 35.500, 139.410, "earthwork", "民間", False, "none", "近接なし", False, "伊藤"),
    ("S05", "CW-027", "西沢川 樋門設置", "X市 西沢川流域", 35.710, 139.520, "river", "公共", True, "stale", "データ更新遅延", False, "高橋"),
    ("S06", "CW-012", "港南 臨港道路 舗装", "Z市 港南地区", 35.440, 139.680, "pavement", "公共", False, "none", "近接なし", False, "田中"),
]

STATIONS = {
    "S01": [("北川 上流 雨量・水位観測所", "river", "上流", 35.787, 139.769),
            ("北川 護岸地点 水位観測所", "river", "最寄り", 35.766, 139.776),
            ("東部 アメダス", "weather", "参照", 35.754, 139.792),
            ("X市 WBGT地点", "wbgt", "参照", 35.758, 139.770)],
    "S02": [("丘陵 アメダス", "weather", "最寄り", 35.625, 139.452),
            ("Y市 WBGT地点", "wbgt", "参照", 35.615, 139.467)],
    "S03": [("中央 アメダス", "weather", "最寄り", 35.464, 139.622),
            ("Z市 WBGT地点", "wbgt", "参照", 35.456, 139.636)],
    "S04": [("南台 アメダス", "weather", "最寄り", 35.504, 139.405),
            ("Y市 WBGT地点", "wbgt", "参照", 35.496, 139.416)],
    "S05": [("西沢川 上流 観測所", "river", "上流", 35.724, 139.511),
            ("西沢川 樋門地点 水位観測所", "river", "最寄り", 35.713, 139.517),
            ("西部 アメダス", "weather", "参照", 35.706, 139.527),
            ("X市 WBGT地点", "wbgt", "参照", 35.714, 139.514)],
    "S06": [("港南 アメダス", "weather", "最寄り", 35.444, 139.674),
            ("Z市 WBGT地点", "wbgt", "参照", 35.436, 139.686)],
}

PLANS = [
    ("WP01", "S01", "river", "護岸ブロック据付", "2026-06-20T08:00", "2026-06-20T12:00", "○○建設"),
    ("WP02", "S01", "river", "法面整形", "2026-06-20T13:00", "2026-06-20T16:00", "○○建設"),
    ("WP03", "S03", "concrete", "橋脚 コンクリート打設", "2026-06-20T09:00", "2026-06-20T15:00", "□□工業"),
    ("WP04", "S02", "earthwork", "掘削・残土搬出", "2026-06-20T08:00", "2026-06-20T17:00", "△△土建"),
    ("WP05", "S06", "pavement", "表層 舗設", "2026-06-20T08:00", "2026-06-20T15:00", "◎◎道路"),
]

SOURCES = [
    ("DS-OPEN-METEO", "Open-Meteo", "外部API", "OK", "—", 0, 240, "補完", "予報・時系列の補完データ。APIキー不要。公式警報を優先します。"),
    ("DS-JMA-XML", "気象庁 防災情報XML", "公式", "OK", "—", 0, 410, "公式", "警報・注意報・防災気象情報。最優先で扱います。"),
    ("DS-WBGT", "環境省 WBGT", "公式", "OK", "—", 0, 320, "公式", "暑さ指数 実況・予測。夏期重点監視。PoCでは気温湿度からの推定で代替中。"),
    ("DS-RIVER-GO", "川の防災情報", "公式（参照）", "Warning", "—", 2, 980, "公式", "一部観測所で更新遅延。該当現場は確認不能とし公式ページ参照を案内します。"),
    ("DS-WATER-OPEN", "水防災オープンデータ", "準公式", "Error", "—", 12, 0, "準公式", "本格利用には契約・利用条件確認が必要。PoCでは未接続。"),
    ("DS-NASA-POWER", "NASA POWER", "公式", "OK", "—", 0, 660, "補助", "日射・長期傾向。日次更新の参考データ。"),
    ("DS-JMA-CSV", "気象庁 気象データ高度利用", "公式", "OK", "—", 0, 0, "公式", "アメダス・気温・雨量・風速（CSV/機械判読）。"),
    ("DS-JAXA", "JAXA G-Portal / Earth API", "公式", "OK", "—", 0, 0, "補助", "衛星データ・水循環。初期は将来拡張扱い。"),
    ("DS-NOAA", "NOAA", "公式", "OK", "—", 0, 0, "補助", "海外気象・研究・補完。国内現場では初期必須ではない。"),
]

HISTORY = [
    ("L07", "06/19 08:10", "S01", "北川 下流右岸 護岸工事", "河川内作業", 2, "cancel", "上流雨量増加・水位上昇傾向のため午前のブロック据付を中止。午後再評価。", "山田（現場管理者）"),
    ("L06", "06/19 07:55", "S03", "中央高架橋 下部工", "コンクリート打設", 1, "execute", "高温だが暑中コンクリート対策を確認し打設実施。養生強化。", "佐藤（主任技術者）"),
    ("L05", "06/18 16:40", "S02", "東部丘陵 道路改良", "土工", 1, "postpone", "翌日午前の降雨予報により法面整形を午後へ変更。", "鈴木（現場管理者）"),
    ("L04", "06/18 13:20", "S05", "西沢川 樋門設置", "河川内作業", 3, "monitor", "河川データ更新遅延。公式ページと現地目視で監視継続。", "高橋（安全担当）"),
    ("L03", "06/18 09:05", "S03", "中央高架橋 下部工", "クレーン作業", 2, "cancel", "突風リスクのため大型クレーン揚重を中止・待機。", "佐藤（主任技術者）"),
    ("L02", "06/17 14:10", "S06", "港南 臨港道路 舗装", "舗装", 0, "execute", "気象条件良好。予定どおり舗設実施。", "田中（現場管理者）"),
    ("L01", "06/17 10:30", "S02", "東部丘陵 道路改良", "土工", 0, "execute", "注意条件なし。通常確認で作業実施。", "鈴木（現場管理者）"),
]


def seed(db: Session) -> None:
    if db.scalar(select(WorkType).limit(1)):
        return  # 既に投入済み
    for k, name, color in WORK_TYPES:
        db.add(WorkType(id=k, name=name, color=color))
    for (sid, code, name, loc, lat, lon, work, proj, rflag, rstate, rnote, flood, mgr) in SITES:
        db.add(Site(id=sid, site_code=code, name=name, loc=loc, latitude=lat, longitude=lon,
                    work_type=work, project_type=proj, river_work_flag=rflag, river_state=rstate,
                    river_note=rnote, flood_info=flood, manager=mgr))
        for i, (sname, stype, srel, slat, slon) in enumerate(STATIONS.get(sid, []), 1):
            db.add(Station(id=f"{sid}-ST{i}", site_id=sid, name=sname, type=stype, rel=srel,
                           latitude=slat, longitude=slon))
    for (pid, sid, wt, title, st, en, con) in PLANS:
        db.add(WorkPlan(id=pid, site_id=sid, work_type=wt, title=title,
                        planned_start=st, planned_end=en, contractor=con))
    for (sidc, name, kind, status, lok, fails, ms, trust, note) in SOURCES:
        db.add(DataSourceStatus(id=sidc, name=name, kind=kind, status=status, last_ok=lok,
                                fails=fails, avg_ms=ms, trust=trust, note=note))
    for (lid, dt, sid, sname, wt, lv, act, comment, by) in HISTORY:
        db.add(DecisionLog(id=lid, site_id=sid, site_name=sname, work_type=wt, level=lv,
                           action=act, comment=comment, decided_by=by, decided_at=dt))
    db.commit()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed(db)
