"""DB 初期化＋サンプルデータ投入（PoC）。実在現場名は使わない（設計 §16.3）。

サンプル現場・観測所・作業種別・データソース状態・判断履歴を投入する。
WebUI(ClaudeDesign) のモックと同じ S01〜S06 を用い、見た目を一致させる。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.config import settings
from .core.db import SessionLocal
from .core.security import hash_password
from .models import (
    DataSourceStatus, DecisionLog, IdCounter, Site, SiteLink, Station, User,
    UserSiteAccess, WorkPlan, WorkType,
)
from .services.data_collectors import river_collector

# デモ用ユーザー（5ロール）。本番では各自パスワード変更／Entra ID 連携。
USERS = [
    ("U01", "admin", "システム管理者", "admin", "IT・DX部門", "admin123"),
    ("U02", "tanaka", "田中（技術管理者）", "tech_manager", "土木技術部", "pass1234"),
    ("U03", "yamada", "山田（現場管理者）", "site_manager", "現場", "pass1234"),
    ("U04", "takahashi", "高橋（安全担当）", "safety", "安全衛生", "pass1234"),
    ("U05", "viewer", "閲覧 太郎", "viewer", "経営企画", "pass1234"),
]
DEMO_USER_IDS = {uid for uid, username, *_ in USERS if username != "admin"}

WORK_TYPES = [
    ("river", "河川内作業", "#0e7d8f"), ("concrete", "コンクリート打設", "#7a5cc0"),
    ("earthwork", "土工", "#8a6d3b"), ("pavement", "舗装", "#566472"),
    ("crane", "クレーン作業", "#c0682c"), ("heat", "熱中症対策", "#cc4444"),
    ("marine", "海上作業", "#0f6ea8"),
]

SITES = [
    # id, code, name, loc, lat, lon, work, project, river_flag, river_state, river_note, flood, manager
    ("S01", "CW-031", "北川 下流右岸 護岸工事", "X市 北川流域", 35.760, 139.780, "river", "公共", True, "rising", "上昇傾向 +0.4m/h", True, "山田"),
    ("S02", "CW-018", "東部丘陵 道路改良", "Y市 東部地区", 35.620, 139.460, "earthwork", "公共", False, "none", "近接なし", False, "鈴木"),
    ("S03", "CW-024", "中央高架橋 下部工", "Z市 中央地区", 35.460, 139.630, "concrete", "公共", False, "none", "近接なし", False, "佐藤"),
    ("S04", "CW-009", "南台ニュータウン 造成", "Y市 南台地区", 35.500, 139.410, "earthwork", "民間", False, "none", "近接なし", False, "伊藤"),
    ("S05", "CW-027", "西沢川 樋門設置", "X市 西沢川流域", 35.710, 139.520, "river", "公共", True, "stale", "データ更新遅延", False, "高橋"),
    ("S06", "CW-012", "港南 臨港道路 舗装", "Z市 港南地区", 35.440, 139.680, "pavement", "公共", False, "none", "近接なし", False, "田中"),
    # --- 全国展開（サンプル。実在現場名は使わない） ---
    ("S07", "CW-101", "石狩川 北部 河川改修", "北海道 札幌圏", 43.100, 141.330, "river", "公共", True, "rising", "上昇傾向", True, "中村"),
    ("S08", "CW-102", "仙台湾 沿岸道路 改良", "宮城県 仙台圏", 38.260, 140.900, "earthwork", "公共", False, "none", "近接なし", False, "小林"),
    ("S09", "CW-103", "信濃川 護岸 補強", "新潟県 新潟圏", 37.920, 139.050, "river", "公共", True, "stale", "データ更新遅延", False, "加藤"),
    ("S10", "CW-104", "金沢 中心街路 舗装", "石川県 金沢圏", 36.560, 136.660, "pavement", "民間", False, "none", "近接なし", False, "吉田"),
    ("S11", "CW-105", "名古屋 環状高架橋 下部工", "愛知県 名古屋圏", 35.170, 136.910, "concrete", "公共", False, "none", "近接なし", False, "山本"),
    ("S12", "CW-106", "大阪港 ふ頭 揚重", "大阪府 大阪圏", 34.650, 135.430, "crane", "公共", False, "none", "近接なし", False, "井上"),
    ("S13", "CW-107", "太田川 河川内 護床", "広島県 広島圏", 34.400, 132.470, "river", "公共", True, "stable", "平常", False, "松本"),
    ("S14", "CW-108", "高松 丘陵 造成", "香川県 高松圏", 34.340, 134.050, "earthwork", "民間", False, "none", "近接なし", False, "清水"),
    ("S15", "CW-109", "福岡 地下構造物 構築", "福岡県 福岡圏", 33.590, 130.400, "heat", "公共", False, "none", "近接なし", False, "森"),
    ("S16", "CW-110", "那覇 臨港道路 舗装", "沖縄県 那覇圏", 26.210, 127.680, "pavement", "公共", False, "none", "近接なし", False, "比嘉"),
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

# 現場別の公式リンク（#30 T2-02 / FR-035）。河川作業のある現場に「川の防災情報」への導線を用意。
# 現場別の精緻なURL(観測所ページ)は観測所正規化(#29)後に対応。初期は公式トップへの参照リンク。
SITE_LINKS = [
    # id, site_id, label, url, kind, sort_order
    ("SL001", "S01", "川の防災情報（国交省）", "https://www.river.go.jp/", "river", 1),
    ("SL002", "S05", "川の防災情報（国交省）", "https://www.river.go.jp/", "river", 1),
    ("SL003", "S07", "川の防災情報（国交省）", "https://www.river.go.jp/", "river", 1),
    ("SL004", "S09", "川の防災情報（国交省）", "https://www.river.go.jp/", "river", 1),
    ("SL005", "S13", "川の防災情報（国交省）", "https://www.river.go.jp/", "river", 1),
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
    ("DS-OPEN-METEO-MARINE", "Open-Meteo Marine", "外部API", "OK", "—", 0, 0, "補完",
     "全国の波高・周期・波向・うねり予報（APIキー不要）。NOWPHAS・気象庁潮位は利用条件確認後に接続予定。"),
    ("DS-NOWPHAS", "NOWPHAS（国交省 波浪・潮位）", "公式", "OK", "—", 0, 0, "公式",
     "全国港湾海洋波浪情報網のリアルタイム波高・周期・波向・潮位（10分更新）。"
     "WMCDSS統合で海象の一次情報として優先利用。"),
    ("DS-RIVER-DEMO", "河川観測 デモ自動取得", "準公式", "OK", "—", 0, 10, "補助",
     "デモ・シミュレーションによる自動取得（水防災オープンデータ提供サービスの接続前に"
     "暫定運用）。退避判断は必ず公式サイトと現地確認を優先。"),
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


def _sync_production_users(db: Session) -> None:
    """既存DBでも本番管理者を同期し、local専用デモユーザーを無効化する。"""
    admin = db.get(User, "U01")
    if admin is None:
        admin = User(id="U01", username="admin", display_name="システム管理者",
                     role="admin", department="IT・DX部門", email="admin@example.com")
        db.add(admin)
    admin.username = "admin"
    admin.display_name = "システム管理者"
    admin.role = "admin"
    admin.department = "IT・DX部門"
    admin.email = "admin@example.com"
    admin.password_hash = hash_password(settings.admin_password)
    admin.is_active = True
    for uid in DEMO_USER_IDS:
        demo = db.get(User, uid)
        if demo is not None:
            demo.is_active = False


def seed(db: Session) -> None:
    if settings.app_env != "local":
        _sync_production_users(db)
    # デモ・サンプルデータ投入の可否。未設定は local のみ true（本番の新規DBに
    # デモ現場・判断履歴を入れない安全側既定。評価 2026-08-12 対応）。
    demo_ok = (
        settings.seed_demo_data
        if settings.seed_demo_data is not None
        else settings.app_env == "local"
    )
    # 作業種別・データソースは「既投入でも不足分を追補」する冪等 upsert（#72 段5:
    # 既存本番DBへ marine 種別・海洋ソースを追加してもシード済み判定で無視されない）
    has_any_wt = db.scalar(select(WorkType).limit(1)) is not None
    existing_wt = {w.id for w in db.scalars(select(WorkType)).all()}
    for k, name, color in WORK_TYPES:
        if k in existing_wt:
            continue
        db.add(WorkType(id=k, name=name, color=color))
    existing_src = {s.id for s in db.scalars(select(DataSourceStatus)).all()}
    for (sidc, name, kind, status, lok, fails, ms, trust, note) in SOURCES:
        if sidc == "DS-RIVER-DEMO" and not settings.river_demo_enabled:
            continue  # デモ無効時はシードしない（テスト環境は自動取得未接続の検証を維持）
        if sidc in existing_src:
            continue
        db.add(DataSourceStatus(id=sidc, name=name, kind=kind, status=status, last_ok=lok,
                                fails=fails, avg_ms=ms, trust=trust, note=note))
    if has_any_wt:
        # 河川観測デモの観測所マスタ・現場紐付けは既存DBでも冪等に整備する
        # （#31 拡張: 自動取得の画面・判定を本番でも動作させるための初期データ投入）。
        if settings.river_demo_enabled:
            river_collector.ensure_demo_stations(db)
        db.commit()
        return  # 既投入DB: 不足種別・ソースの追補のみ行い、サンプルデータは再投入しない
    if demo_ok:
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
        for (lid, sid, label, url, kind, order) in SITE_LINKS:
            db.add(SiteLink(id=lid, site_id=sid, label=label, url=url, kind=kind, sort_order=order))
        # 新規DBでは現場投入後にデモ観測所と紐付けを投入（現場が存在する状態で1回だけ実行）
        if settings.river_demo_enabled:
            river_collector.ensure_demo_stations(db)
        for (lid, dt, sid, sname, wt, lv, act, comment, by) in HISTORY:
            db.add(DecisionLog(id=lid, site_id=sid, site_name=sname, work_type=wt, level=lv,
                               action=act, comment=comment, decided_by=by, decided_at=dt))
    # デモユーザー(admin/admin123 等)は local のみ。本番管理者は本関数冒頭の
    # _sync_production_users() で既に同期済み（#90 対抗レビュー critical-1: ここで再度
    # 呼ぶと同一主キー U01 の User が2件 pending になり commit 時に IntegrityError となる）。
    if settings.app_env == "local":
        for (uid, uname, disp, role, dept, pw) in USERS:
            db.add(User(id=uid, username=uname, display_name=disp, role=role,
                        department=dept, email=f"{uname}@example.com",
                        password_hash=hash_password(pw)))
        # #117: デモユーザーへ全現場を割当（現場単位権限の動作確認と既存UI維持用。
        # 本番では管理API/Entra同期で個別割当するため、このブロックは local のみ）
        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for uid, role in (("U03", "site_decision"), ("U04", "site_decision"),
                          ("U05", "site_viewer")):
            for sid, *_ in SITES:
                db.add(UserSiteAccess(
                    id=f"SA-{uid}-{sid}", user_id=uid, site_id=sid, role=role,
                    granted_by="seed", created_at=_now, updated_at=_now))

    # 固定IDシード後にID採番カウンタを実データのmaxへ同期（#49: カウンタ遅延による採番衝突を防ぐ）
    def _maxnum(ids: list[str], prefix: str) -> int:
        nums = [int(x[len(prefix):]) for x in ids
                if x.startswith(prefix) and x[len(prefix):].isdigit()]
        return max(nums) if nums else 0

    counter_values = {
        "S": _maxnum([s[0] for s in SITES], "S"),
        "WP": _maxnum([p[0] for p in PLANS], "WP"),
        "L": _maxnum([h[0] for h in HISTORY], "L"),
        "SL": _maxnum([sl[0] for sl in SITE_LINKS], "SL"),
        "DR": 0,
    }
    for cname, cval in counter_values.items():
        row = db.get(IdCounter, cname)
        if row is None:
            db.add(IdCounter(name=cname, value=cval))
        elif row.value < cval:
            row.value = cval
    db.commit()


def init_db() -> None:
    """Alembic マイグレーションを head まで適用し、サンプルを投入（冪等）。"""
    import os

    from alembic import command
    from alembic.config import Config

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    command.upgrade(cfg, "head")
    with SessionLocal() as db:
        seed(db)
