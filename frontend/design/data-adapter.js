/* CW data-adapter — WebUI(ClaudeDesign .dc.html) を実APIに接続する外部アダプタ。
 *
 * 方針: .dc.html は無改修。dc-runtime が生成する Component(window.__dcRegistry[root].Logic)
 *       の prototype を「ラップ」し、モックデータ(SITES/genHourly/resultVM/sources/...)を
 *       バックエンドAPIの fetch 結果へ差し替える。ClaudeDesign 再取り込みでもこのファイルは残る。
 *
 * API ベースURL: window.__CW_API_BASE__（index.html ローダが ?api= 等から設定）。空なら同一オリジン。
 *
 * テスト容易性: createAdapter() を export し、Node から fetch/bump を注入して契約検証する
 *               （frontend/test/adapter-contract.cjs）。
 */
(function (global) {
  "use strict";

  function createAdapter(opts) {
    opts = opts || {};
    var base = opts.base != null ? opts.base : "";
    var _fetch = opts.fetch || (typeof fetch !== "undefined" ? fetch : null);
    var bump = opts.bump || function () {};
    var _open = opts.open || function () {};
    var CW = { sites: null, meta: null, sources: null, history: null, series: {}, result: null };

    function url(p) { return base + p; }
    function j(p, o) { return _fetch(url(p), o).then(function (r) { return r.json(); }); }

    // ---- マッピング（API形 → dc が期待する形）----
    function mapReasons(rs) {
      return (rs || []).map(function (r) { return { sev: r.severity, text: r.text }; });
    }
    function mapDashToSites(dash, list) {
      var meta = {}; (list || []).forEach(function (s) { meta[s.id] = s; });
      var prev = {}; (CW.sites || []).forEach(function (s) { prev[s.id] = s; });
      return (dash.sites || []).map(function (c) {
        return {
          id: c.id, name: c.name, code: c.code, loc: c.loc, work: c.work, level: c.level,
          rainNow: c.rainNow, rainPeak: c.rainPeak, windMax: c.windMax, gust: c.gust,
          tempHi: c.tempHi, tempLo: c.tempLo, wbgt: c.wbgt, river: c.river, riverState: c.riverState,
          updated: c.updated, rainy: (c.rainPeak || 0) > 0,
          project: (meta[c.id] && meta[c.id].project) || "公共",
          manager: (meta[c.id] && meta[c.id].manager) || "",
          reasons: mapReasons(c.reasons),
          plans: (prev[c.id] && prev[c.id].plans) || []
        };
      });
    }
    function mapSources(src) {
      return (src || []).map(function (d) {
        var m = d.status === "OK" ? { color: "#2e7d32", bg: "#e7f3e9", border: "#bcdcc0", dot: "#2e7d32" }
          : d.status === "Warning" ? { color: "#c2920a", bg: "#fdf6e0", border: "#ecdca0", dot: "#e8930c" }
            : { color: "#c62828", bg: "#fbe8e8", border: "#f0bcbc", dot: "#c62828" };
        return {
          name: d.name, id: d.id, kind: d.kind, status: d.status, lastOk: d.lastOk,
          fails: d.fails, ms: d.ms, trust: d.trust, note: d.note,
          color: m.color, bg: m.bg, border: m.border, dot: m.dot,
          pulse: d.status === "Error" ? "animation:cwpulse 1.4s ease infinite" : "",
          failColor: d.fails > 0 ? (d.status === "Error" ? "#c62828" : "#c2920a") : "#2a3641"
        };
      });
    }
    function ser24(points, key, baseArr) {
      var out = [];
      for (var i = 0; i < 24; i++) {
        var p = points[i];
        out.push(p && p[key] != null ? p[key] : baseArr[i]);
      }
      return out;
    }

    // ---- データ取得 ----
    function loadDashboard() {
      return Promise.all([j("/api/sites"), j("/api/dashboard/site-risk")]).then(function (r) {
        CW.meta = r[0]; CW.sites = mapDashToSites(r[1], r[0]); bump();
      });
    }
    function loadSources() { return j("/api/dashboard/data-sources").then(function (d) { CW.sources = d; bump(); }); }
    function loadHistory() { return j("/api/decision-logs").then(function (d) { CW.history = d; bump(); }); }
    function loadSeries() {
      if (!CW.sites) return Promise.resolve();
      return Promise.all(CW.sites.map(function (s) {
        return j("/api/weather/timeseries?site_id=" + s.id)
          .then(function (d) { CW.series[s.id] = d.points || []; })
          .catch(function () {});
      })).then(bump);
    }
    function ensureSiteDetail(id) {
      return j("/api/sites/" + id).then(function (d) {
        var s = CW.sites && CW.sites.filter(function (x) { return x.id === id; })[0];
        if (s) {
          s.plans = (d.plans || []).map(function (p) {
            return { title: p.title, time: p.time, contractor: p.contractor, level: p.level, reason: p.reason };
          });
        }
        bump();
      }).catch(function () {});
    }
    function loadAll() {
      return loadDashboard().then(function () {
        return Promise.all([loadSources(), loadHistory(), loadSeries()]);
      });
    }
    function workTypes() { return j("/api/work-types"); }
    function createSite(payload) {
      return _fetch(url("/api/sites"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(function (r) {
        return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
      }).then(function (res) {
        if (res.ok) return loadDashboard().then(function () { return res; });
        return res;
      });
    }

    // ---- プロトタイプ・パッチ ----
    function patch(proto) {
      if (proto.__cwPatched) return;
      proto.__cwPatched = true;
      var origRender = proto.renderVals, origGen = proto.genHourly, origResult = proto.resultVM,
        origOpen = proto.openSite, origRefresh = proto.refresh;

      proto.renderVals = function () {
        if (CW.sites) this.SITES = CW.sites;            // ダッシュボード/現場詳細/グラフが API データを参照
        if (CW.history) this.state.history = CW.history; // 判断履歴
        var vals = origRender.call(this);
        if (CW.sources) vals.sources = mapSources(CW.sources); // データソース状態
        vals.exportCsv = function () { try { _open(url("/api/decision-logs/export.csv"), "_blank"); } catch (e) {} };
        return vals;
      };

      proto.genHourly = function (s) {
        var b = origGen.call(this, s);
        var ser = CW.series[s.id];
        if (ser && ser.length) {
          b.temp = ser24(ser, "temp_c", b.temp);
          b.rain = ser24(ser, "precip_mm", b.rain);
          b.wind = ser24(ser, "wind_ms", b.wind);
          b.wbgt = ser24(ser, "wbgt_derived", b.wbgt);
          // b.level（河川水位）は実河川API未接続のため元の推定を維持
        }
        return b;
      };

      proto.resultVM = function () {
        var df = this.state.dform, r = CW.result;
        if (r && r.key === df.workType + "|" + df.siteId) {
          var L = this.LEVELS;
          var site = this.SITES.filter(function (x) { return x.id === df.siteId; })[0] || this.SITES[0];
          return {
            mapNote: site ? site.name : "",
            levelColor: L[r.level].color, levelInk: L[r.level].ink, levelLabel: L[r.level].label,
            summary: r.summary,
            reasons: (r.reasons || []).map(function (x) {
              var m = L[x.severity];
              return { sevLabel: m.label, chip: m.chip, ink: m.ink, bg: m.bg, border: m.border,
                msg: x.text, source: x.source, value: x.value, time: "" };
            }),
            refs: r.refs || ["気象: Open-Meteo", "河川: 川の防災情報", "WBGT: 環境省(推定)", "警報: 気象庁"]
          };
        }
        return origResult.call(this);
      };

      proto.openSite = function (id) { origOpen.call(this, id); ensureSiteDetail(id); };

      proto.evaluate = function () {
        var df = this.state.dform, self = this;
        self.setState({ result: "_pending" });
        j("/api/decisions/evaluate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ site_id: df.siteId, work_type: df.workType, start: df.start, end: df.end })
        }).then(function (d) {
          CW.result = { key: df.workType + "|" + df.siteId, level: d.overall_level,
            summary: d.summary, reasons: d.reasons, refs: d.refs };
          self.setState({ result: "_api" }); bump();
          if (self.showToast) self.showToast("気象・河川データで評価しました");
        }).catch(function () { if (self.showToast) self.showToast("評価に失敗しました（APIエラー）"); });
      };

      proto.record = function () {
        var S = this.state, self = this;
        var site = this.SITES.filter(function (x) { return x.id === S.dform.siteId; })[0] || this.SITES[0];
        var r = this.resultVM();
        var lab = { "通常": 0, "注意": 1, "中止検討": 2, "確認不能": 3 };
        j("/api/decision-logs", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ site_id: site.id, work_type: this.WORK[S.dform.workType].label,
            level: lab[r.levelLabel] != null ? lab[r.levelLabel] : 1,
            action: S.memoAction, comment: S.memoComment })
        }).then(loadHistory).then(function () {
          self.setState({ memoComment: "" }); bump();
          if (self.showToast) self.showToast("判断履歴に記録しました");
        }).catch(function () { if (self.showToast) self.showToast("記録に失敗しました（APIエラー）"); });
      };

      proto.refresh = function () {
        var self = this;
        if (self.state.refreshing) return;
        self.setState({ refreshing: true });
        j("/api/data-collectors/run", { method: "POST" }).then(loadDashboard).then(function () {
          self.setState({ refreshing: false }); bump();
          if (self.showToast) self.showToast("最新データを取得しました");
        }).catch(function () {
          self.setState({ refreshing: false });
          if (self.showToast) self.showToast("再取得に失敗しました（APIエラー）");
        });
      };
    }

    return {
      patch: patch, loadAll: loadAll, loadDashboard: loadDashboard, loadSources: loadSources,
      loadHistory: loadHistory, loadSeries: loadSeries, ensureSiteDetail: ensureSiteDetail,
      workTypes: workTypes, createSite: createSite,
      mapDashToSites: mapDashToSites, mapSources: mapSources, _state: CW
    };
  }

  // ---- ブラウザ自動起動 ----
  if (typeof window !== "undefined" && window.document) {
    var apiBase = window.__CW_API_BASE__ != null ? window.__CW_API_BASE__ : "";
    var adapter = createAdapter({
      base: apiBase,
      fetch: window.fetch.bind(window),
      open: window.open.bind(window),
      bump: function () {
        try {
          var rn = (window.__dcRootName && window.__dcRootName()) || "Root";
          window.__dcSetProps(rn, { __cw: Date.now() });
        } catch (e) {}
      }
    });
    // 「＋現場登録」UI を .dc.html を触らず DOM 注入（FR-001/002, SC-009 の簡易版）
    function installRegisterUI(adapter) {
      if (document.getElementById("cw-reg-btn")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-reg-btn{position:fixed;right:18px;bottom:18px;z-index:9998;background:#16527d;color:#fff;border:none;"
        + "border-radius:24px;padding:12px 18px;font:700 13px 'Noto Sans JP',sans-serif;box-shadow:0 3px 10px rgba(0,0,0,.3);cursor:pointer}"
        + "#cw-reg-btn:hover{background:#13344f}"
        + "#cw-reg-ov{position:fixed;inset:0;z-index:9999;background:rgba(10,20,30,.45);display:none;align-items:flex-start;justify-content:center;overflow:auto}"
        + "#cw-reg-ov.on{display:flex}"
        + ".cw-reg-card{background:#fff;margin:6vh 0;width:min(520px,92vw);border-radius:12px;padding:20px 22px;font-family:'Noto Sans JP',sans-serif;color:#16212c}"
        + ".cw-reg-card h2{margin:0 0 4px;font-size:16px;color:#13344f}"
        + ".cw-reg-card p.sub{margin:0 0 14px;font-size:11.5px;color:#7e8c99}"
        + ".cw-reg-card label{display:block;font-size:11.5px;font-weight:700;color:#3a4854;margin:10px 0 4px}"
        + ".cw-reg-card input,.cw-reg-card select{width:100%;padding:8px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px 'Noto Sans JP',sans-serif;box-sizing:border-box}"
        + ".cw-reg-row{display:flex;gap:10px}.cw-reg-row>div{flex:1}"
        + ".cw-reg-chk{display:flex;align-items:center;gap:7px;margin-top:10px;font-size:12.5px}"
        + ".cw-reg-chk input{width:auto}"
        + ".cw-reg-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}"
        + ".cw-reg-actions button{padding:9px 16px;border-radius:7px;font:700 13px 'Noto Sans JP',sans-serif;cursor:pointer;border:none}"
        + ".cw-reg-cancel{background:#eef1f4;color:#5a6b7b}.cw-reg-save{background:#2e7d32;color:#fff}"
        + ".cw-reg-msg{margin-top:10px;font-size:12px;min-height:16px}";
      document.head.appendChild(css);

      var btn = document.createElement("button");
      btn.id = "cw-reg-btn"; btn.textContent = "＋ 現場登録";
      var ov = document.createElement("div"); ov.id = "cw-reg-ov";
      ov.innerHTML =
        '<form class="cw-reg-card" id="cw-reg-form">'
        + '<h2>現場登録</h2><p class="sub">公開データ中心のPoC。実在現場名・個人情報は登録しないでください。</p>'
        + '<label>現場名 *</label><input name="name" required placeholder="例: 北川 下流右岸 護岸工事">'
        + '<div class="cw-reg-row"><div><label>現場コード</label><input name="site_code" placeholder="任意（空欄で自動）"></div>'
        + '<div><label>所在地</label><input name="loc" placeholder="例: X市 北川流域"></div></div>'
        + '<div class="cw-reg-row"><div><label>緯度 *</label><input name="latitude" type="number" step="any" required placeholder="35.76"></div>'
        + '<div><label>経度 *</label><input name="longitude" type="number" step="any" required placeholder="139.78"></div></div>'
        + '<div class="cw-reg-row"><div><label>作業種別 *</label><select name="work_type" id="cw-reg-wt"></select></div>'
        + '<div><label>発注区分</label><select name="project_type"><option>公共</option><option>民間</option><option>その他</option></select></div></div>'
        + '<label class="cw-reg-chk"><input type="checkbox" name="river_work_flag">河川内・河川近接作業</label>'
        + '<div class="cw-reg-row" id="cw-reg-river" style="display:none"><div><label>河川状態</label>'
        + '<select name="river_state"><option value="none">近接なし</option><option value="stable">安定</option>'
        + '<option value="rising">上昇傾向</option><option value="stale">データ更新遅延</option></select></div>'
        + '<div><label>担当者</label><input name="manager" placeholder="例: 山田"></div></div>'
        + '<div id="cw-reg-mgr2"><label>担当者</label><input name="manager2" placeholder="例: 山田"></div>'
        + '<div class="cw-reg-msg" id="cw-reg-msg"></div>'
        + '<div class="cw-reg-actions"><button type="button" class="cw-reg-cancel" id="cw-reg-cancel">キャンセル</button>'
        + '<button type="submit" class="cw-reg-save">登録</button></div></form>';
      document.body.appendChild(btn); document.body.appendChild(ov);

      var sel = ov.querySelector("#cw-reg-wt");
      adapter.workTypes().then(function (wts) {
        sel.innerHTML = wts.map(function (w) { return '<option value="' + w.id + '">' + w.name + "</option>"; }).join("");
      }).catch(function () { sel.innerHTML = '<option value="river">河川内作業</option>'; });

      var chk = ov.querySelector('[name=river_work_flag]');
      var riverRow = ov.querySelector("#cw-reg-river");
      var mgr2 = ov.querySelector("#cw-reg-mgr2");
      function syncRiver() { var on = chk.checked; riverRow.style.display = on ? "flex" : "none"; mgr2.style.display = on ? "none" : "block"; }
      chk.addEventListener("change", syncRiver); syncRiver();

      function close() { ov.classList.remove("on"); ov.querySelector("#cw-reg-msg").textContent = ""; }
      btn.addEventListener("click", function () { ov.classList.add("on"); });
      ov.querySelector("#cw-reg-cancel").addEventListener("click", close);
      ov.addEventListener("click", function (e) { if (e.target === ov) close(); });

      ov.querySelector("#cw-reg-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var f = e.target, msg = ov.querySelector("#cw-reg-msg");
        var payload = {
          name: f.name.value, site_code: f.site_code.value || null, loc: f.loc.value,
          latitude: parseFloat(f.latitude.value), longitude: parseFloat(f.longitude.value),
          work_type: f.work_type.value, project_type: f.project_type.value,
          river_work_flag: f.river_work_flag.checked,
          river_state: f.river_work_flag.checked ? f.river_state.value : "none",
          manager: (f.river_work_flag.checked ? f.manager.value : f.manager2.value) || ""
        };
        msg.style.color = "#5a6b7b"; msg.textContent = "登録中…";
        adapter.createSite(payload).then(function (res) {
          if (res.ok) {
            msg.style.color = "#2e7d32"; msg.textContent = "登録しました（" + res.body.id + "）。ダッシュボードを更新しました。";
            try { window.__dcSetProps((window.__dcRootName && window.__dcRootName()) || "Root", { __cw: Date.now() }); } catch (_) {}
            setTimeout(close, 1100); f.reset(); syncRiver();
          } else {
            var d = res.body && res.body.detail;
            msg.style.color = "#c62828";
            msg.textContent = "登録できません: " + (Array.isArray(d) ? d.map(function (x) { return x.msg; }).join(" / ") : (d || ("HTTP " + res.status)));
          }
        }).catch(function (err) { msg.style.color = "#c62828"; msg.textContent = "登録に失敗しました（APIエラー）: " + err; });
      });
    }

    (function whenReady() {
      var n = 0;
      var t = setInterval(function () {
        n++;
        var reg = window.__dcRegistry;
        var rn = (window.__dcRootName && window.__dcRootName()) || "Root";
        var e = reg && reg[rn];
        if (e && e.Logic && e.Logic.prototype && e.Logic.prototype.renderVals) {
          clearInterval(t);
          adapter.patch(e.Logic.prototype);
          adapter.loadAll().then(function () {
            try { window.__dcSetProps(rn, { __cw: Date.now() }); } catch (_) {}
          });
          installRegisterUI(adapter);
          // 定期自動更新（5分ごとにダッシュボード/ソースを再取得）
          setInterval(function () {
            adapter.loadDashboard().then(function () { return adapter.loadSources(); })
              .then(function () { try { window.__dcSetProps(rn, { __cw: Date.now() }); } catch (_) {} })
              .catch(function () {});
          }, 300000);
          window.__cwAdapter = adapter;
        } else if (n > 300) {
          clearInterval(t);
          console.warn("[cw-adapter] dc runtime が見つかりません（__dcRegistry 未準備）");
        }
      }, 50);
    })();
  }

  // ---- Node からのテスト用 export ----
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { createAdapter: createAdapter };
  }
  global.__cwCreateAdapter = createAdapter;
})(typeof globalThis !== "undefined" ? globalThis : this);
