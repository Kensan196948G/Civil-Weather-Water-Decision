"""通知導出のテスト（設計§14）。"""
from app.services.notifications import build_notifications


def test_build_notifications_derives_from_cards_and_sources():
    cards = [
        {"id": "S01", "name": "現場A", "level": 2, "summary": "洪水情報",
         "wbgt": 29, "riverState": "rising", "updated": "08:00"},
        {"id": "S05", "name": "現場B", "level": 3, "summary": "",
         "wbgt": 26, "riverState": "stale", "updated": "06:00"},
        {"id": "S06", "name": "現場C", "level": 0, "summary": "",
         "wbgt": 20, "riverState": "none", "updated": "08:00"},
    ]
    sources = [
        {"id": "DS-X", "name": "X", "status": "Error", "fails": 3, "lastOk": "—"},
        {"id": "DS-Y", "name": "Y", "status": "Warning", "fails": 1, "lastOk": "06:00"},
        {"id": "DS-Z", "name": "Z", "status": "OK", "fails": 0, "lastOk": "08:00"},
    ]
    ns = build_notifications(cards, sources)
    kinds = {n["kind"] for n in ns}
    assert {"risk", "data", "wbgt", "river", "source"} <= kinds
    # 重大度降順
    sev = [n["severity"] for n in ns]
    assert sev == sorted(sev, reverse=True)
    # 各通知に免責文
    assert all(n.get("disclaimer") for n in ns)
    # OK ソース・通常現場は通知を生まない
    assert not any(n["id"] == "src-DS-Z" for n in ns)
