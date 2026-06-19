// ClaudeDesign プロトタイプのロジック層スモークテスト
//
// .dc.html の <script data-dc-script> ブロック（class Component extends DCLogic）を
// 取り出し、DCLogic をスタブして Node 上で renderVals()/resultVM() を実行検証する。
// DOM・Leaflet・React を一切使わずに「画面ごとのビューモデルが正しく組み上がるか」を確認できる。
//
// 実行: node frontend/test/logic-smoke.mjs
// 用途: データ接続フェーズ（SITES/genHourly/resultVM/sources を実APIに差し替え）の回帰テスト土台。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const dcPath = path.join(here, '..', 'design', '気象河川施工判断支援.dc.html');
const html = fs.readFileSync(dcPath, 'utf8');

const m = html.match(/<script[^>]*data-dc-script[^>]*>([\s\S]*?)<\/script>/);
if (!m) { console.error('data-dc-script ブロックが見つかりません'); process.exit(2); }
const src = m[1];

// dc-runtime が提供する基底クラスの最小スタブ（renderVals は state と純粋計算のみに依存）
class DCLogic { setState(o) { this.state = Object.assign({}, this.state, o); } }

// セキュリティ注記: ここで実行する `src` は「リポジトリに取り込んだ自分たちの
// design/気象河川施工判断支援.dc.html 内のスクリプト」であり、ブラウザにそのまま配信して
// 実行しているコードと同一（=信頼境界の内側）。外部入力・ユーザー入力は一切連結しない。
// このテストの目的はその設計ロジックを実行検証することなので new Function での実行は意図的。
// 未信頼の文字列を src に流し込まないこと。
const Component = new Function('DCLogic', src + '\nreturn Component;')(DCLogic);
const c = new Component();

let pass = 0, fail = 0;
const ok = (cond, msg) => { cond ? pass++ : fail++; console.log((cond ? '  ✓' : '  ✗') + ' ' + msg); };

let v = c.renderVals();
ok(v.nav.length === 6, 'ナビ6画面: ' + v.nav.map(n => n.label).join(' / '));
ok(v.summary.reduce((a, b) => a + b.count, 0) === 6, 'サマリ合計6現場 counts=' + v.summary.map(s => s.count).join(','));
ok(v.sites.length === 6, 'ダッシュボード現場数=' + v.sites.length);
ok(v.sources.length === 6, 'データソース数=' + v.sources.length);
ok(v.sources.some(s => s.status === 'Error') && v.sources.some(s => s.status === 'Warning'), 'ソース状態に Error/Warning を含む');

for (const scr of ['dashboard', 'site', 'decision', 'wbgt', 'history', 'source']) {
  c.state = Object.assign({}, c.state, { screen: scr });
  try { ok(!!c.renderVals(), '画面 "' + scr + '" renderVals 正常'); }
  catch (e) { ok(false, '画面 "' + scr + '" で例外: ' + e.message); }
}

c.state = Object.assign({}, c.state, { screen: 'site', siteId: 'S01' });
v = c.renderVals();
ok(typeof v.wx.tempPts === 'string' && v.wx.tempPts.includes(','), '気象グラフ tempPts 生成');
ok(v.rv && v.rv.thresholds.length === 3, '河川グラフ閾値3本=' + (v.rv ? v.rv.thresholds.map(t => t.label).join(',') : 'なし'));
ok(v.site.plans.length >= 1 && v.threeDay.length === 3, '3日予報＋作業予定あり');

const expect = { river: '中止検討', concrete: '注意', earthwork: '注意', pavement: '通常', crane: '中止検討', heat: '中止検討' };
for (const [wt, lbl] of Object.entries(expect)) {
  c.state = Object.assign({}, c.state, { screen: 'decision', dform: Object.assign({}, c.state.dform, { workType: wt }) });
  const r = c.resultVM();
  ok(r.levelLabel === lbl, '判定 ' + wt + ' => ' + r.levelLabel + '（期待 ' + lbl + '） 理由数=' + r.reasons.length);
}

ok(c.wbgtMeta(null).label === '欠測' && c.wbgtMeta(32).label === '危険' && c.wbgtMeta(20).label === 'ほぼ安全', 'WBGT境界（欠測/危険/ほぼ安全）');

console.log('\nRESULT: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
