"""気象庁 防災情報XML（警報・注意報）コレクタ（設計§4 DS-JMA-XML / §8.3-6）。

atom フィード → 個別の気象警報・注意報XML を取得・パースし、市町村エリア別の
発表中の警報・注意報を集約する。判定エンジンで公式警報を優先反映する。
名前空間が複雑なため ElementTree の {*}（名前空間ワイルドカード）で抽出する。
"""
from __future__ import annotations

import time

import httpx
# XXE / billion-laughs 対策のため defusedxml を使用（外部XMLをパースするため）
from defusedxml.ElementTree import fromstring as _xml_fromstring

from ...core.config import settings

SOURCE_ID = "DS-JMA-XML"
ACTIVE = {"発表", "継続"}


def parse_warning_xml(text: str) -> list[tuple[str, str, str]]:
    """警報XMLから (エリア名, 警報種別, 状態) のリストを抽出（純粋関数・テスト可能）。"""
    out: list[tuple[str, str, str]] = []
    try:
        root = _xml_fromstring(text)
    except Exception:
        return out
    # iter() は {*} を解釈しないため findall のパス式（{*}＝名前空間ワイルドカード）を使う
    for item in root.findall(".//{*}Item"):
        area_el = item.find("{*}Area/{*}Name")
        area = area_el.text if area_el is not None else None
        if not area:
            continue
        for kind in item.findall("{*}Kind"):
            kn = kind.find("{*}Name")
            ks = kind.find("{*}Status")
            if kn is not None and kn.text:
                out.append((area, kn.text, ks.text if ks is not None else ""))
    return out


def warnings_for_site(warnmap: dict[str, set[str]], loc: str) -> set[str]:
    """現場の所在地文字列から市町村キーを作り、発表中警報を突き合わせる。"""
    keys = []
    for t in (loc or "").split():
        k = t
        for suf in ("圏", "地区", "流域", "地方"):
            k = k.replace(suf, "")
        if len(k) >= 2:
            keys.append(k)
    out: set[str] = set()
    for area, kinds in warnmap.items():
        for k in keys:
            if k and k in area:
                out |= kinds
                break
    return out


async def fetch_active_warnings(client: httpx.AsyncClient | None = None,
                                max_docs: int = 8) -> dict[str, set[str]]:
    """フィードから気象警報・注意報XMLを取得・集約。失敗時は空（縮退）。"""
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=settings.data_fetch_timeout_seconds)
    warnmap: dict[str, set[str]] = {}
    try:
        feed = await client.get(settings.jma_feed_url, follow_redirects=True)
        feed.raise_for_status()
        root = _xml_fromstring(feed.text)
        links = []
        for entry in root.findall(".//{*}entry"):
            title = entry.find("{*}title")
            if title is not None and title.text and "気象警報・注意報" in title.text:
                link = entry.find("{*}link")
                if link is not None and link.get("href"):
                    links.append(link.get("href"))
            if len(links) >= max_docs:
                break
        for href in links[:max_docs]:
            try:
                doc = await client.get(href, follow_redirects=True)
                if doc.status_code != 200:
                    continue
                for area, kind, status in parse_warning_xml(doc.text):
                    if status in ACTIVE:
                        warnmap.setdefault(area, set()).add(kind)
            except Exception:
                continue
        return warnmap
    except Exception:
        return warnmap
    finally:
        if own:
            await client.aclose()


_cache: dict = {"t": 0.0, "data": {}}


async def get_active_warnings() -> dict[str, set[str]]:
    """10分キャッシュ付きで発表中警報マップを返す。無効時は空。"""
    if not settings.enable_jma_warnings:
        return {}
    now = time.monotonic()
    if _cache["data"] and now - _cache["t"] < 600:
        return _cache["data"]
    data = await fetch_active_warnings()
    _cache["t"] = now
    _cache["data"] = data
    return data


def clear_cache() -> None:
    _cache["t"] = 0.0
    _cache["data"] = {}
