/* data-adapter 契約テスト（ブラウザ不要 / Node）
 *
 * dc-runtime が生成する Component に対し、createAdapter().patch() を適用し、
 * fetch をバックエンド応答形でモックして loadAll() したのち、
 * renderVals()/resultVM() が「API由来のデータ」を返すことを検証する。
 * 実ブラウザ描画は不可環境のため、これが配線の回帰テスト土台となる。
 *
 * 実行: node frontend/test/adapter-contract.cjs
 */
const fs = require("fs");
const path = require("path");

const dcPath = path.join(__dirname, "..", "design", "気象河川施工判断支援.dc.html");
const html = fs.readFileSync(dcPath, "utf8");
const m = html.match(/<script[^>]*data-dc-script[^>]*>([\s\S]*?)<\/script>/);
if (!m) { console.error("data-dc-script が見つかりません"); process.exit(2); }

// 信頼境界: 実行する src はリポジトリ内の自分の .dc.html。外部入力は連結しない（logic-smoke と同じ）。
class DCLogic { setState(o) { this.state = Object.assign({}, this.state, o); } }
const Component = new Function("DCLogic", m[1] + "\nreturn Component;")(DCLogic);

const { createAdapter } = require("../design/data-adapter.js");

// ---- バックエンド応答形の fetch モック ----
const SITES_LIST = [
  { id: "S01", project: "公共", manager: "山田", lat: 35.76, lon: 139.78 },
  { id: "S05", project: "公共", manager: "高橋", lat: 35.71, lon: 139.52 },
];
const DASH = {
  summary: [1, 0, 1, 1],
  sites: [
    { id: "S01", name: "北川 下流右岸 護岸工事", code: "CW-031", loc: "X市", work: "river",
      level: 2, levelLabel: "中止検討", rainNow: 0.0, rainPeak: 12, windMax: 6, gust: 11,
      tempHi: 28, tempLo: 21, wbgt: 30, river: "上昇傾向", riverState: "rising", updated: "08:32",
      reasons: [{ severity: 2, text: "洪水関連情報が発表されています", source: "DS-JMA-XML", value: "洪水注意情報" }] },
    { id: "S05", name: "西沢川 樋門設置", code: "CW-027", loc: "X市", work: "river",
      level: 3, levelLabel: "確認不能", rainNow: null, rainPeak: null, windMax: 4, gust: 8,
      tempHi: 27, tempLo: 21, wbgt: 26, river: "データ更新遅延", riverState: "stale", updated: "06:10",
      reasons: [{ severity: 3, text: "河川データが取得できません", source: "DS-RIVER-GO", value: "欠測/遅延" }] },
  ],
};
const SOURCES = [
  { id: "DS-OPEN-METEO", name: "Open-Meteo", kind: "外部API", status: "OK", lastOk: "08:35", fails: 0, ms: 240, trust: "補完", note: "" },
  { id: "DS-RIVER-GO", name: "川の防災情報", kind: "公式（参照）", status: "Warning", lastOk: "06:10", fails: 2, ms: 980, trust: "公式", note: "" },
  { id: "DS-WATER-OPEN", name: "水防災オープンデータ", kind: "準公式", status: "Error", lastOk: "—", fails: 12, ms: 0, trust: "準公式", note: "" },
];
const HISTORY = [
  { id: "L07", datetime: "06/19 08:10", siteId: "S01", site: "北川 下流右岸 護岸工事", workType: "河川内作業", level: 2, action: "cancel", comment: "中止", by: "山田" },
];
function points24() {
  const p = [];
  for (let i = 0; i < 24; i++) p.push({ time: "h" + i, temp_c: 20 + i % 5, precip_mm: i === 14 ? 6 : 0, wind_ms: 3, wbgt_derived: 25 });
  return p;
}
let lastPost = null;
function fetchMock(u, opts) {
  var method = (opts && opts.method) || "GET";
  let data = {}, status = 200;
  if (method === "POST" && u.indexOf("/api/sites") >= 0 && u.indexOf("/api/sites/") < 0) {
    lastPost = JSON.parse(opts.body); data = { id: "S07", code: "CW-S07", name: lastPost.name, status: "created" }; status = 201;
  } else if (u.indexOf("/api/work-types") >= 0) {
    data = [{ id: "river", name: "河川内作業", color: "#0e7d8f" }, { id: "crane", name: "クレーン作業", color: "#c0682c" }];
  } else if (u.indexOf("/api/admin/rules") >= 0 && method === "PUT") {
    lastPost = JSON.parse(opts.body);
    data = { status: "updated", rules: [{ key: "rain_heavy", value: 20.0, default: 5.0, overridden: true }] };
  } else if (u.indexOf("/api/admin/rules") >= 0) {
    data = { rules: [
      { key: "rain_heavy", label: "降雨 中止検討", unit: "mm/h", desc: "", min: 0, max: 300,
        default: 5.0, value: 5.0, overridden: false, updated_at: null, updated_by: null }] };
  } else if (u.indexOf("/api/sites/") >= 0 && u.indexOf("/stations") >= 0) {
    data = [{ id: "ST1", name: "北川 護岸地点 水位観測所", type: "river", rel: "最寄り", lat: 35.766, lon: 139.776 }];
  } else if (u.indexOf("/api/sites/") >= 0) {
    data = { plans: [{ title: "護岸ブロック据付", time: "08:00–12:00", contractor: "○○建設", level: 2, reason: "中止検討" }] };
  } else if (u.indexOf("/api/sites") >= 0) data = SITES_LIST;
  else if (u.indexOf("/api/dashboard/site-risk") >= 0) data = DASH;
  else if (u.indexOf("/api/dashboard/data-sources") >= 0) data = SOURCES;
  else if (u.indexOf("/api/decision-logs") >= 0) data = HISTORY;
  else if (u.indexOf("/api/weather/timeseries") >= 0) data = { points: points24() };
  return Promise.resolve({ ok: status < 400, status: status, json: function () { return Promise.resolve(data); } });
}

let pass = 0, fail = 0;
const ok = (c, msg) => { c ? pass++ : fail++; console.log((c ? "  ✓" : "  ✗") + " " + msg); };

(async function () {
  let afterRenderCalled = 0;
  const adapter = createAdapter({
    base: "", fetch: fetchMock, bump: function () {},
    hideNav: ["現場詳細"], // 現場詳細タブを廃しドリルダウン専用
    afterRender: function (inst, vals) {
      afterRenderCalled++;
      // 正式画面化: ナビへ「現場登録」を追加（ブラウザ実装と同等の検証）
      if (vals.nav && vals.nav.concat) {
        vals.nav = vals.nav.concat([{ label: "現場登録", onClick: function () { inst.go("register"); } }]);
      }
    }
  });
  adapter.patch(Component.prototype);
  await adapter.loadAll();

  const c = new Component();

  // ダッシュボード: API由来の現場
  let v = c.renderVals();
  ok(v.sites.length === 2, "dashboard sites が API 由来（件数=" + v.sites.length + "）");
  ok(afterRenderCalled > 0 && v.nav[v.nav.length - 1].label === "現場登録",
    "afterRender フックでナビに「現場登録」を追加（正式画面化）");
  ok(!v.nav.some(function (n) { return n.label === "現場詳細"; }),
    "ナビから「現場詳細」を除外（クリック時のみ表示=ドリルダウン専用）");
  ok(v.sites[0].name.indexOf("北川") === 0, "現場名が API 由来: " + v.sites[0].name);
  ok(v.summary.reduce((a, b) => a + b.count, 0) === 2, "サマリが API 現場数に追従");
  ok(Array.isArray(c.COORDS.S01) && c.COORDS.S01[0] === 35.76,
    "COORDS を API 緯度経度から構築（全国地図対応）");

  // データソース: API由来（Error/Warning 含む）
  ok(v.sources.length === 3 && v.sources.some(s => s.status === "Error") && v.sources.some(s => s.status === "Warning"),
    "data sources が API 由来（Error/Warning 含む）");

  // 判断履歴: API由来
  c.state = Object.assign({}, c.state, { screen: "history" });
  v = c.renderVals();
  ok(v.historyRows.length === 1 && v.historyRows[0].comment === "中止", "history が API 由来");

  // 気象グラフ: API時系列（precip ピーク 6 が反映される genHourly）
  const hr = c.genHourly({ id: "S01", rainy: true, windMax: 6 });
  ok(Math.max.apply(null, hr.rain) === 6, "genHourly が API 時系列を反映（雨ピーク=" + Math.max.apply(null, hr.rain) + "）");

  // 観測所ピン: openSite → ensureSiteDetail が GET /api/sites/{id}/stations を取得し STATIONS に反映（Issue #55）
  await adapter.ensureSiteDetail("S01");
  v = c.renderVals();
  ok(Array.isArray(c.STATIONS.S01) && c.STATIONS.S01.length === 1,
    "STATIONS が API から取得した観測所で更新される（件数=" + (c.STATIONS.S01 && c.STATIONS.S01.length) + "）");
  ok(c.STATIONS.S01[0].name === "北川 護岸地点 水位観測所" && Array.isArray(c.STATIONS.S01[0].d)
    && c.STATIONS.S01[0].d[0] === 35.766 && c.STATIONS.S01[0].d[1] === 139.776,
    "観測所の lat/lon が d:[lat,lon] 形式にマッピングされる");
  ok(Array.isArray(c.STATIONS.S06), "未取得の現場は既存(モック)の STATIONS を保持（S06="+ (c.STATIONS.S06 && c.STATIONS.S06.length) +"件）");

  // 判定結果: API result を resultVM が返す
  adapter._state.result = { key: "river|S01", level: 2, summary: "中止・退避を検討してください。",
    reasons: [{ severity: 2, text: "洪水関連情報", source: "DS-JMA-XML", value: "洪水注意情報" }] };
  c.state = Object.assign({}, c.state, { screen: "decision", dform: { siteId: "S01", workType: "river" } });
  const rv = c.resultVM();
  ok(rv.levelLabel === "中止検討", "resultVM が API 判定を返す: " + rv.levelLabel);
  ok(rv.reasons[0].chip && rv.reasons[0].msg === "洪水関連情報", "resultVM 理由が LEVELS 色で整形される");

  // フォールバック: API result 不一致なら元の resultVM
  adapter._state.result = null;
  const rv2 = c.resultVM();
  ok(rv2 && rv2.levelLabel, "API未取得時は元の resultVM にフォールバック: " + rv2.levelLabel);

  // 現場登録: createSite が POST し作成結果を返す
  const cres = await adapter.createSite({ name: "新設テスト", latitude: 35.5, longitude: 139.5, work_type: "crane" });
  ok(cres.ok && cres.body.id === "S07", "createSite が POST /api/sites で作成（id=" + (cres.body && cres.body.id) + "）");
  ok(lastPost && lastPost.name === "新設テスト", "createSite が payload を送信");

  // システム設定: 閾値の取得と保存（#34/#35 配線）
  const rr = await adapter.rules();
  ok(rr && rr.rules && rr.rules[0].key === "rain_heavy" && rr.rules[0].default === 5.0,
    "rules が GET /api/admin/rules から閾値一覧を取得");
  const sres = await adapter.saveRules({ rain_heavy: 20.0, rain_light: null });
  ok(sres.ok && lastPost && lastPost.updates && lastPost.updates.rain_heavy === 20.0
    && lastPost.updates.rain_light === null,
    "saveRules が PUT /api/admin/rules へ updates(null=リセット含む) を送信");

  console.log("\nRESULT: " + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
