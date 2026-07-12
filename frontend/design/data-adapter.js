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
    var CW = { sites: null, meta: null, sources: null, history: null, series: {}, stations: {}, result: null, ver: 0 };

    function url(p) { return base + p; }

    // ---- 認証トークン（localStorage。Node テストでは null） ----
    function getToken() { try { return localStorage.getItem("cw_token"); } catch (e) { return null; } }
    function setToken(t) {
      try { if (t) localStorage.setItem("cw_token", t); else localStorage.removeItem("cw_token"); } catch (e) {}
    }
    function authHeaders() {
      var t = getToken();
      return t ? { Authorization: "Bearer " + t } : {};
    }

    function j(p, o) {
      o = o || {};
      o.headers = Object.assign({}, authHeaders(), o.headers || {});
      return _fetch(url(p), o).then(function (r) {
        if (r.status === 401 && opts.onUnauthorized) opts.onUnauthorized();
        return r.json();
      });
    }

    function login(username, password) {
      return _fetch(url("/api/auth/login"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username, password: password }),
      }).then(function (r) {
        return r.json().then(function (b) {
          if (r.ok && b.token) setToken(b.token);
          return { ok: r.ok, status: r.status, body: b };
        });
      });
    }
    function logout() { setToken(null); }

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
          lat: meta[c.id] && meta[c.id].lat, lon: meta[c.id] && meta[c.id].lon, // 地図用
          project: (meta[c.id] && meta[c.id].project) || "公共",
          manager: (meta[c.id] && meta[c.id].manager) || "",
          reasons: mapReasons(c.reasons),
          plans: (prev[c.id] && prev[c.id].plans) || []
        };
      });
    }
    function buildCoords(sites, base) {
      var c = {}; if (base) { for (var k in base) c[k] = base[k]; }
      sites.forEach(function (s) { if (s.lat != null && s.lon != null) c[s.id] = [s.lat, s.lon]; });
      return c;
    }
    function buildStations(stations, base) {
      var s = {}; if (base) { for (var k in base) s[k] = base[k]; }
      for (var id in stations) { if (stations[id]) s[id] = stations[id]; }
      return s;
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
        CW.meta = r[0]; CW.sites = mapDashToSites(r[1], r[0]); CW.ver++; bump();
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
      var detail = j("/api/sites/" + id).then(function (d) {
        var s = CW.sites && CW.sites.filter(function (x) { return x.id === id; })[0];
        if (s) {
          s.plans = (d.plans || []).map(function (p) {
            return { title: p.title, time: p.time, contractor: p.contractor, level: p.level, reason: p.reason };
          });
        }
      }).catch(function () {});
      var stations = j("/api/sites/" + id + "/stations").then(function (list) {
        CW.stations[id] = (list || []).map(function (st) {
          return { name: st.name, type: st.type, rel: st.rel, d: [st.lat, st.lon] };
        });
      }).catch(function () {}); // 観測所ピンは補助表示のため、取得失敗時も現場詳細自体は表示を継続
      return Promise.all([detail, stations]).then(bump);
    }
    function loadAll() {
      return loadDashboard().then(function () {
        return Promise.all([loadSources(), loadHistory(), loadSeries()]);
      });
    }
    function workTypes() { return j("/api/work-types"); }
    function createSite(payload) {
      return _fetch(url("/api/sites"), {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
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
        if (CW.sites) {
          this.SITES = CW.sites;                          // ダッシュボード/現場詳細/グラフが API データを参照
          this.COORDS = buildCoords(CW.sites, this.COORDS); // 地図ピン用に全現場の緯度経度を供給（全国対応）
          this.STATIONS = buildStations(CW.stations, this.STATIONS); // 現場詳細の観測所ピン（openSite時に遅延取得）
        }
        if (CW.history) this.state.history = CW.history; // 判断履歴
        var vals = origRender.call(this);
        if (CW.sources) vals.sources = mapSources(CW.sources); // データソース状態
        vals.exportCsv = function () { try { _open(url("/api/decision-logs/export.csv"), "_blank"); } catch (e) {} };
        // 指定ラベルのナビを非表示（例: 「現場詳細」はナビタブを廃しダッシュボードからのドリルダウン専用に）
        if (opts.hideNav && vals.nav && vals.nav.filter) {
          vals.nav = vals.nav.filter(function (n) { return opts.hideNav.indexOf(n.label) < 0; });
        }
        if (opts.afterRender) { try { opts.afterRender(this, vals); } catch (e) {} } // ナビ追加/画面トグル等
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
            summary: d.summary, reasons: d.reasons, refs: d.refs, id: d.resultId };
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
            action: S.memoAction, comment: S.memoComment,
            decision_result_id: (CW.result && CW.result.key === S.dform.workType + "|" + S.dform.siteId) ? CW.result.id : null })
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
      login: login, logout: logout, getToken: getToken,
      me: function () { return j("/api/auth/me"); },
      notifications: function () { return j("/api/notifications"); },
      mapDashToSites: mapDashToSites, mapSources: mapSources, _state: CW
    };
  }

  // ---- ブラウザ自動起動 ----
  if (typeof window !== "undefined" && window.document) {
    var apiBase = window.__CW_API_BASE__ != null ? window.__CW_API_BASE__ : "";
    // ナビへ「現場登録」を追加し、register 画面のときだけ注入パネルを表示する
    var regInst = null;
    function cwToggleRegScreen(show) {
      var el = document.getElementById("cw-reg-screen");
      if (el) el.style.display = show ? "block" : "none";
    }
    function cwInjectNav(inst, vals) {
      if (!vals.nav || !vals.nav.concat) return;
      var active = inst.state.screen === "register";
      vals.nav = vals.nav.concat([{
        label: "現場登録", weight: active ? 800 : 600,
        color: active ? "#13344f" : "#697A88", bar: active ? "#13344f" : "transparent",
        onClick: function () { inst.go("register"); },
        badge: 0, badgeShow: "none", badgeBg: "#c62828", badgeColor: "#fff"
      }]);
    }
    var adapter = createAdapter({
      base: apiBase,
      fetch: window.fetch.bind(window),
      open: window.open.bind(window),
      onUnauthorized: function () { showLogin(); }, // 401 で自動ログアウト→ログイン画面
      hideNav: ["現場詳細"], // 現場詳細はナビタブを廃し、現場クリックのドリルダウン専用にする
      bump: function () {
        try {
          var rn = (window.__dcRootName && window.__dcRootName()) || "Root";
          window.__dcSetProps(rn, { __cw: Date.now() });
        } catch (e) {}
      },
      afterRender: function (inst, vals) {
        regInst = inst;            // 登録成功後の画面遷移に使用
        cwInjectNav(inst, vals);   // ナビに「現場登録」項目を追加
        cwToggleRegScreen(inst.state.screen === "register"); // 該当画面のみパネル表示
        // データ更新でダッシュボード地図を作り直す（dc キャッシュは token='all' で再生成されないため）
        if (inst._dashMap && inst.__cwDashVer !== adapter._state.ver) {
          try { inst._dashMap.remove(); } catch (e) {}
          inst._dashMap = null; inst.__cwDashVer = adapter._state.ver;
        }
        cwToggleSourceNote(inst.state.screen === "source"); // データソース画面の更新間隔注記
        cwSyncWbgtScreen(inst);                              // WBGT画面の地図
      }
    });

    // 「現場登録」正式画面（ヘッダー直下の全面パネル）を .dc.html を触らず DOM 注入。
    // ナビの「現場登録」で state.screen='register' になり、afterRender がこのパネルを表示する。
    function installRegisterScreen(adapter) {
      if (document.getElementById("cw-reg-screen")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-reg-screen{display:none;position:fixed;left:0;right:0;top:56px;bottom:0;z-index:30;overflow:auto;"
        + "background:#eef1f4;font-family:'Noto Sans JP',system-ui,sans-serif;color:#16212c}"
        + ".cw-reg-card{background:#fff;margin:22px auto;width:min(620px,92vw);border-radius:12px;padding:22px 24px;box-shadow:0 1px 4px rgba(0,0,0,.08)}"
        + ".cw-reg-card h2{margin:0 0 4px;font-size:17px;color:#13344f}"
        + ".cw-reg-card p.sub{margin:0 0 16px;font-size:11.5px;color:#7e8c99}"
        + ".cw-reg-card label{display:block;font-size:11.5px;font-weight:700;color:#3a4854;margin:11px 0 4px}"
        + ".cw-reg-card input,.cw-reg-card select{width:100%;padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px 'Noto Sans JP',sans-serif;box-sizing:border-box}"
        + ".cw-reg-row{display:flex;gap:10px}.cw-reg-row>div{flex:1}"
        + ".cw-reg-chk{display:flex;align-items:center;gap:7px;margin-top:12px;font-size:12.5px}"
        + ".cw-reg-chk input{width:auto}"
        + ".cw-reg-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}"
        + ".cw-reg-actions button{padding:10px 18px;border-radius:7px;font:700 13px 'Noto Sans JP',sans-serif;cursor:pointer;border:none}"
        + ".cw-reg-cancel{background:#eef1f4;color:#5a6b7b}.cw-reg-save{background:#2e7d32;color:#fff}"
        + ".cw-reg-msg{margin-top:12px;font-size:12px;min-height:16px}";
      document.head.appendChild(css);

      var screen = document.createElement("div");
      screen.id = "cw-reg-screen";
      screen.innerHTML =
        '<form class="cw-reg-card" id="cw-reg-form">'
        + '<h2>現場登録</h2><p class="sub">公開データ中心のPoC。実在現場名・個人情報は登録しないでください。緯度経度で気象を取得し判定します。</p>'
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
        + '<div class="cw-reg-actions"><button type="button" class="cw-reg-cancel" id="cw-reg-cancel">ダッシュボードへ</button>'
        + '<button type="submit" class="cw-reg-save">登録</button></div></form>';
      document.body.appendChild(screen);

      var sel = screen.querySelector("#cw-reg-wt");
      adapter.workTypes().then(function (wts) {
        sel.innerHTML = wts.map(function (w) { return '<option value="' + w.id + '">' + w.name + "</option>"; }).join("");
      }).catch(function () { sel.innerHTML = '<option value="river">河川内作業</option>'; });

      var chk = screen.querySelector('[name=river_work_flag]');
      var riverRow = screen.querySelector("#cw-reg-river");
      var mgr2 = screen.querySelector("#cw-reg-mgr2");
      function syncRiver() { var on = chk.checked; riverRow.style.display = on ? "flex" : "none"; mgr2.style.display = on ? "none" : "block"; }
      chk.addEventListener("change", syncRiver); syncRiver();

      function gotoDashboard() { if (regInst) try { regInst.go("dashboard"); } catch (e) {} }
      screen.querySelector("#cw-reg-cancel").addEventListener("click", gotoDashboard);

      screen.querySelector("#cw-reg-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var f = e.target, msg = screen.querySelector("#cw-reg-msg");
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
            msg.style.color = "#2e7d32"; msg.textContent = "登録しました（" + res.body.id + "）。ダッシュボードへ移動します。";
            f.reset(); syncRiver();
            setTimeout(gotoDashboard, 900);
          } else {
            var d = res.body && res.body.detail;
            msg.style.color = "#c62828";
            msg.textContent = "登録できません: " + (Array.isArray(d) ? d.map(function (x) { return x.msg; }).join(" / ") : (d || ("HTTP " + res.status)));
          }
        }).catch(function (err) { msg.style.color = "#c62828"; msg.textContent = "登録に失敗しました（APIエラー）: " + err; });
      });
    }

    // ---- データソース画面: 5分更新の注記バー ----
    function installSourceNote() {
      if (document.getElementById("cw-src-note")) return;
      var n = document.createElement("div");
      n.id = "cw-src-note";
      n.style.cssText = "display:none;position:fixed;left:0;right:0;bottom:0;z-index:35;background:#13344f;"
        + "color:#fff;font:600 12px 'Noto Sans JP',sans-serif;padding:9px 16px;text-align:center;box-shadow:0 -1px 4px rgba(0,0,0,.25)";
      n.textContent = "データソース状態は5分ごとに自動更新（サーバ側プローブ）。右上「再取得」で即時更新も可能。";
      document.body.appendChild(n);
    }
    function cwToggleSourceNote(show) {
      var el = document.getElementById("cw-src-note");
      if (el) el.style.display = show ? "block" : "none";
    }

    // ---- 熱中症/WBGT 画面: 全国地図（OpenStreetMap）＋スケール＋ランキング ----
    function wbgtMeta(v) {
      if (v == null) return { label: "欠測", color: "#5a6b7b" };
      if (v < 21) return { label: "ほぼ安全", color: "#2e7d32" };
      if (v < 25) return { label: "注意", color: "#c2920a" };
      if (v < 28) return { label: "警戒", color: "#e07d12" };
      if (v < 31) return { label: "厳重警戒", color: "#d6481f" };
      return { label: "危険", color: "#c62828" };
    }
    var wbgtMap = null, wbgtMarkers = null, wbgtVer = -1, wbgtBuildTries = 0;
    function installWbgtScreen() {
      if (document.getElementById("cw-wbgt-screen")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-wbgt-screen{display:none;position:fixed;left:0;right:0;top:56px;bottom:0;z-index:30;overflow:auto;"
        + "background:#eef1f4;font-family:'Noto Sans JP',system-ui,sans-serif;color:#16212c}"
        + ".cw-wbgt-wrap{max-width:1100px;margin:0 auto;padding:16px}"
        + ".cw-wbgt-h{font-size:15px;font-weight:800;color:#13344f;margin:2px 0 10px}"
        + "#cw-wbgt-map{height:46vh;min-height:300px;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)}"
        + ".cw-wbgt-scale{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}"
        + ".cw-wbgt-scale span{font-size:11px;font-weight:700;color:#fff;padding:4px 9px;border-radius:5px}"
        + ".cw-wbgt-row{display:flex;align-items:center;gap:10px;background:#fff;border:1px solid #e2e8ee;border-radius:8px;padding:8px 12px;margin-bottom:6px;font-size:12.5px}"
        + ".cw-wbgt-dot{width:12px;height:12px;border-radius:50%;flex:none}"
        + ".cw-wbgt-row b{font-size:15px;min-width:34px}"
        + ".cw-wbgt-lab{font-weight:700;min-width:64px}.cw-wbgt-name{font-weight:700;color:#1c2935}"
        + ".cw-wbgt-loc{color:#7e8c99;margin-left:auto}";
      document.head.appendChild(css);
      var screen = document.createElement("div");
      screen.id = "cw-wbgt-screen";
      screen.innerHTML =
        '<div class="cw-wbgt-wrap">'
        + '<button id="cw-wbgt-back" style="background:#16527d;color:#fff;border:none;border-radius:7px;'
        + "padding:8px 14px;font:700 12.5px 'Noto Sans JP',sans-serif;cursor:pointer;margin-bottom:10px\">"
        + "← ダッシュボードに戻る</button>"
        + '<div class="cw-wbgt-h">熱中症 / 暑さ指数 WBGT ・ 全国マップ</div>'
        + '<div id="cw-wbgt-map"></div>'
        + '<div class="cw-wbgt-scale">'
        + '<span style="background:#2e7d32">~21 ほぼ安全</span><span style="background:#c2920a">21-25 注意</span>'
        + '<span style="background:#e07d12">25-28 警戒</span><span style="background:#d6481f">28-31 厳重警戒</span>'
        + '<span style="background:#c62828">31~ 危険</span><span style="background:#5a6b7b">欠測</span></div>'
        + '<div class="cw-wbgt-h" style="margin-top:6px">現場別ランキング（WBGT高い順）</div>'
        + '<div id="cw-wbgt-rank"></div></div>';
      document.body.appendChild(screen);
      var back = screen.querySelector("#cw-wbgt-back");
      if (back) back.addEventListener("click", function () {
        if (regInst) { try { regInst.go("dashboard"); } catch (e) {} }
      });
    }
    function buildWbgtRank() {
      var CW = adapter._state; // CW は createAdapter のクロージャ内。外からは _state 経由で参照
      var rank = document.getElementById("cw-wbgt-rank");
      if (!rank || !CW.sites) return;
      var sorted = CW.sites.slice().sort(function (a, b) { return (b.wbgt || 0) - (a.wbgt || 0); });
      rank.innerHTML = sorted.map(function (s) {
        var m = wbgtMeta(s.wbgt);
        return '<div class="cw-wbgt-row"><span class="cw-wbgt-dot" style="background:' + m.color + '"></span>'
          + "<b>" + (s.wbgt == null ? "—" : s.wbgt) + '</b><span class="cw-wbgt-lab" style="color:' + m.color + '">' + m.label + "</span>"
          + '<span class="cw-wbgt-name">' + esc(s.name) + '</span><span class="cw-wbgt-loc">' + esc(s.loc || "") + "</span></div>";
      }).join("");
    }
    function buildWbgtScreen() {
      var CW = adapter._state; // 外からは _state 経由で参照
      if (!CW.sites) return;
      var mapEl = document.getElementById("cw-wbgt-map");
      if (!mapEl) return;
      buildWbgtRank();
      if (!window.L) { // Leaflet 未ロードなら再試行（dc の ensureMaps と同じ作法）
        if (wbgtBuildTries++ < 50) setTimeout(buildWbgtScreen, 150);
        return;
      }
      var L = window.L;
      if (!wbgtMap) {
        wbgtMap = L.map(mapEl, { scrollWheelZoom: false }).setView([37.5, 137.0], 4);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
          { maxZoom: 18, attribution: "&copy; OpenStreetMap contributors" }).addTo(wbgtMap);
        wbgtMarkers = L.layerGroup().addTo(wbgtMap);
      }
      wbgtMap.invalidateSize(); // パネル表示後の実コンテナサイズを反映（非表示時に作ると0px化するため）
      if (wbgtVer !== CW.ver) {
        wbgtVer = CW.ver;
        wbgtMarkers.clearLayers();
        var pts = [];
        CW.sites.forEach(function (s) {
          if (s.lat == null || s.lon == null) return;
          var m = wbgtMeta(s.wbgt);
          var mk = L.circleMarker([s.lat, s.lon], { radius: 8, color: "#fff", weight: 2, fillColor: m.color, fillOpacity: 0.95 });
          mk.bindPopup("<b>" + esc(s.name) + "</b><br>WBGT " + (s.wbgt == null ? "—" : s.wbgt) + ' ・ <b style="color:' + m.color + '">' + m.label + "</b>");
          wbgtMarkers.addLayer(mk); pts.push([s.lat, s.lon]);
        });
        if (pts.length) { try { wbgtMap.fitBounds(pts, { padding: [40, 40], maxZoom: 7 }); } catch (e) {} }
      }
      setTimeout(function () { try { wbgtMap.invalidateSize(); } catch (e) {} }, 150);
    }
    function cwSyncWbgtScreen(inst) {
      var active = inst.state.screen === "wbgt";
      var el = document.getElementById("cw-wbgt-screen");
      if (el) el.style.display = active ? "block" : "none";
      if (active) { wbgtBuildTries = 0; setTimeout(buildWbgtScreen, 0); } // レイアウト確定後に構築
    }

    // 注入HTMLに入るユーザー制御値（現場名等）をエスケープし XSS を防ぐ
    function esc(s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }

    // ---- ログイン画面（注入。.dc.html 無改修） ----
    function roleLabel(r) {
      return ({ admin: "管理者", tech_manager: "技術管理者", site_manager: "現場管理者",
        safety: "安全担当", viewer: "閲覧" })[r] || r;
    }
    function showLogin() {
      var el = document.getElementById("cw-login"); if (el) el.classList.add("on");
      var p = document.getElementById("cw-user"); if (p) p.style.display = "none";
    }
    function hideLogin() {
      var el = document.getElementById("cw-login"); if (el) el.classList.remove("on");
      var p = document.getElementById("cw-user"); if (p) p.style.display = "flex";
    }
    function setUserPill(u) {
      var n = document.getElementById("cw-user-name");
      if (n && u) n.textContent = (u.displayName || "") + "（" + roleLabel(u.role) + "）";
    }
    function startApp() {
      var rn = (window.__dcRootName && window.__dcRootName()) || "Root";
      adapter.loadAll().then(function () {
        try { window.__dcSetProps(rn, { __cw: Date.now() }); } catch (_) {}
        loadNotifications();
      });
      adapter.me().then(function (u) { if (u && u.role) { hideLogin(); setUserPill(u); } }).catch(function () {});
    }
    function installLoginScreen() {
      if (document.getElementById("cw-login")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-login{display:none;position:fixed;inset:0;z-index:10000;background:#13344f;"
        + "align-items:center;justify-content:center;font-family:'Noto Sans JP',system-ui,sans-serif}"
        + "#cw-login.on{display:flex}"
        + ".cw-login-card{background:#fff;width:min(380px,92vw);border-radius:14px;padding:26px 28px;box-shadow:0 8px 30px rgba(0,0,0,.35)}"
        + ".cw-login-card h2{margin:0 0 4px;font-size:18px;color:#13344f}"
        + ".cw-login-card p.sub{margin:0 0 18px;font-size:11.5px;color:#7e8c99}"
        + ".cw-login-card label{display:block;font-size:11.5px;font-weight:700;color:#3a4854;margin:11px 0 4px}"
        + ".cw-login-card input{width:100%;padding:10px;border:1px solid #d4dce2;border-radius:7px;font:400 14px sans-serif;box-sizing:border-box}"
        + ".cw-login-card button{width:100%;margin-top:18px;padding:11px;border:none;border-radius:8px;background:#16527d;color:#fff;font:700 14px 'Noto Sans JP',sans-serif;cursor:pointer}"
        + ".cw-login-msg{margin-top:10px;font-size:12px;min-height:16px;color:#c62828}"
        + ".cw-login-hint{margin-top:14px;font-size:11px;color:#7e8c99;line-height:1.7}"
        + "#cw-user{position:fixed;right:14px;top:9px;z-index:50;display:none;align-items:center;gap:8px;"
        + "font:600 11.5px 'Noto Sans JP',sans-serif;color:#cfe0ef}"
        + "#cw-user button{background:#1d4d74;color:#fff;border:none;border-radius:6px;padding:4px 10px;font:700 11px 'Noto Sans JP',sans-serif;cursor:pointer}";
      document.head.appendChild(css);
      var ov = document.createElement("div"); ov.id = "cw-login";
      ov.innerHTML =
        '<form class="cw-login-card" id="cw-login-form">'
        + "<h2>ログイン</h2><p class=\"sub\">気象・河川・施工判断支援システム</p>"
        + '<label>ユーザー名</label><input name="username" autocomplete="username" required>'
        + '<label>パスワード</label><input name="password" type="password" autocomplete="current-password" required>'
        + '<button type="submit">ログイン</button>'
        + '<div class="cw-login-msg" id="cw-login-msg"></div>'
        + '<div class="cw-login-hint">デモ: admin / admin123（管理者） ・ yamada / pass1234（現場管理者） ・ viewer / pass1234（閲覧）</div>'
        + "</form>";
      document.body.appendChild(ov);
      var pill = document.createElement("div"); pill.id = "cw-user";
      pill.innerHTML = '<span id="cw-user-name"></span><button id="cw-logout">ログアウト</button>';
      document.body.appendChild(pill);
      ov.querySelector("#cw-login-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var f = e.target, msg = ov.querySelector("#cw-login-msg");
        msg.textContent = "認証中…";
        adapter.login(f.username.value, f.password.value).then(function (res) {
          if (res.ok) { msg.textContent = ""; setUserPill(res.body.user); hideLogin(); f.reset(); startApp(); }
          else { msg.textContent = (res.body && res.body.detail) || "ログインに失敗しました"; }
        }).catch(function () { msg.textContent = "通信エラー（バックエンド未起動の可能性）"; });
      });
      pill.querySelector("#cw-logout").addEventListener("click", function () { adapter.logout(); location.reload(); });
    }

    // ---- 通知ベル（設計§14。注入） ----
    function notifColor(sev) { return sev >= 2 ? "#c62828" : sev === 1 ? "#e8930c" : "#5a6b7b"; }
    function installNotifyBell() {
      if (document.getElementById("cw-bell")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-bell{position:fixed;right:150px;top:8px;z-index:50;display:none;cursor:pointer;font-size:17px;"
        + "color:#cfe0ef;background:#1d4d74;border:none;border-radius:8px;padding:4px 9px;position:fixed}"
        + ".cw-bell-badge{background:#c62828;color:#fff;font:700 10px sans-serif;min-width:15px;height:15px;"
        + "border-radius:8px;display:none;align-items:center;justify-content:center;padding:0 3px;margin-left:3px}"
        + ".cw-bell-badge.on{display:inline-flex}"
        + "#cw-notif{position:fixed;right:14px;top:44px;z-index:51;display:none;width:min(380px,92vw);max-height:62vh;"
        + "overflow:auto;background:#fff;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.28);font-family:'Noto Sans JP',system-ui,sans-serif}"
        + "#cw-notif.on{display:block}"
        + "#cw-notif h3{margin:0;padding:13px 16px;font-size:13px;color:#13344f;border-bottom:1px solid #eef1f4}"
        + ".cw-notif-row{padding:10px 16px;border-bottom:1px solid #f1f4f7;font-size:12px;color:#16212c}"
        + ".cw-notif-row .t{font-weight:700;margin-bottom:2px}"
        + ".cw-notif-empty{padding:22px 16px;color:#7e8c99;font-size:12px;text-align:center}";
      document.head.appendChild(css);
      var bell = document.createElement("button");
      bell.id = "cw-bell";
      bell.innerHTML = '🔔<span class="cw-bell-badge" id="cw-bell-badge">0</span>';
      var panel = document.createElement("div");
      panel.id = "cw-notif";
      panel.innerHTML = "<h3>通知</h3><div id=\"cw-notif-list\"></div>";
      document.body.appendChild(bell);
      document.body.appendChild(panel);
      bell.addEventListener("click", function () { panel.classList.toggle("on"); });
    }
    function loadNotifications() {
      if (!adapter.getToken()) return;
      adapter.notifications().then(function (d) {
        if (!d || !d.notifications) return;
        var ns = d.notifications;
        var high = ns.filter(function (n) { return n.severity >= 2; }).length;
        var bell = document.getElementById("cw-bell");
        var badge = document.getElementById("cw-bell-badge");
        if (bell) bell.style.display = "inline-block";
        if (badge) { badge.textContent = high; badge.className = "cw-bell-badge" + (high > 0 ? " on" : ""); }
        var list = document.getElementById("cw-notif-list");
        if (list) {
          list.innerHTML = ns.length
            ? ns.map(function (n) {
                return '<div class="cw-notif-row"><div class="t" style="color:' + notifColor(n.severity)
                  + '">' + esc(n.title) + "</div>" + esc(n.message) + "</div>";
              }).join("")
            : '<div class="cw-notif-empty">現在、通知はありません</div>';
        }
      }).catch(function () {});
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
          installRegisterScreen(adapter);
          installSourceNote();
          installWbgtScreen();
          installLoginScreen();
          installNotifyBell();
          // 認証ゲート: トークンがあればデータ取得、無ければログイン画面
          if (adapter.getToken()) startApp(); else showLogin();
          // 定期自動更新（5分ごと, 認証済み時のみ）
          setInterval(function () {
            if (!adapter.getToken()) return;
            adapter.loadDashboard().then(function () { return adapter.loadSources(); })
              .then(function () { loadNotifications(); try { window.__dcSetProps(rn, { __cw: Date.now() }); } catch (_) {} })
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
