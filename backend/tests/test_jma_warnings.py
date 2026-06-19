"""気象庁防災情報XML パース／突き合わせのテスト（ネット非依存）。"""
from app.services.data_collectors import jma_warnings as jw

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/">
    <Warning type="気象警報・注意報（市町村等）">
      <Item>
        <Kind><Name>大雨警報</Name><Status>発表</Status></Kind>
        <Kind><Name>洪水警報</Name><Status>発表</Status></Kind>
        <Area><Name>札幌市</Name><Code>0110000</Code></Area>
      </Item>
      <Item>
        <Kind><Name>大雨注意報</Name><Status>解除</Status></Kind>
        <Area><Name>仙台市</Name><Code>0410000</Code></Area>
      </Item>
    </Warning>
  </Body>
</Report>"""


def test_parse_warning_xml():
    rows = jw.parse_warning_xml(SAMPLE)
    assert ("札幌市", "大雨警報", "発表") in rows
    assert ("札幌市", "洪水警報", "発表") in rows
    assert ("仙台市", "大雨注意報", "解除") in rows


def test_parse_warning_xml_malformed():
    assert jw.parse_warning_xml("<not xml") == []


def test_warnings_for_site_matches_by_city():
    warnmap = {"札幌市": {"大雨警報", "洪水警報"}, "仙台市": {"雷注意報"}}
    assert jw.warnings_for_site(warnmap, "北海道 札幌圏") == {"大雨警報", "洪水警報"}
    assert jw.warnings_for_site(warnmap, "宮城県 仙台圏") == {"雷注意報"}
    # 該当しない所在地は空
    assert jw.warnings_for_site(warnmap, "X市 北川流域") == set()
