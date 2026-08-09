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
    var CW = { sites: null, meta: null, metaLoaded: false, dashboardError: null,
      sources: null, history: null, series: {}, stations: {}, result: null, ver: 0 };

    function url(p) { return base + p; }
    function esc(s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }

    // ---- 認証トークン（localStorage。Node テストでは null） ----
    function getToken() { try { return localStorage.getItem("cw_token"); } catch (e) { return null; } }
    function setToken(t) {
      try { if (t) localStorage.setItem("cw_token", t); else localStorage.removeItem("cw_token"); } catch (e) {}
    }
    function clearToken() { setToken(null); }
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
    function logout() {
      var t = getToken();
      if (!t) { clearToken(); return Promise.resolve({ ok: true, skipped: true }); }
      return _fetch(url("/api/auth/logout"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + t }
      }).catch(function () {}).then(function () { clearToken(); return { ok: true }; });
    }
    // ---- OIDC（#118）----
    function oidcStatus() {
      return _fetch(url("/api/auth/oidc/status")).then(function (r) { return r.json(); })
        .catch(function () { return { enabled: false }; });
    }

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
          riverSource: c.riverSource,
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
        CW.meta = r[0]; CW.sites = mapDashToSites(r[1], r[0]);
        CW.metaLoaded = true; CW.dashboardError = null; CW.ver++; bump();
      }).catch(function (e) {
        // 現場一覧画面にエラー状態を出せるよう、失敗を記録してから再送出（表示は継続）
        CW.dashboardError = "現場データの取得に失敗しました（API接続または権限を確認）";
        bump();
        throw e;
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
      // 観測所は正規化マスタ（observation-stations: 最新実測・コード・種別付き）を優先し、
      // 旧 stations API をフォールバックに使う（#29/#31 対応。詳細マップ出力用）
      var stations = j("/api/sites/" + id + "/observation-stations")
        .then(function (d) {
          var list = (d && Array.isArray(d.stations)) ? d.stations : null;
          if (!list) {
            // 旧API/モック互換: 正規化マスタAPIが未実装の場合は従来 /stations へ退避
            return j("/api/sites/" + id + "/stations").then(function (old) {
              CW.stations[id] = (old || []).map(function (st) {
                return { name: st.name, type: st.type, rel: st.rel, d: [st.lat, st.lon] };
              }).filter(function (st) { return st.d && st.d[0] != null && st.d[1] != null; });
              return CW.stations[id];
            });
          }
          CW.stations[id] = list.map(function (st) {
            return {
              name: st.name, type: st.kind === "water" || st.kind === "water_rain" ? "river" : st.kind,
              rel: st.rel, d: [st.latitude, st.longitude],
              stationCode: st.stationCode, kind: st.kind,
              latest: st.latest || null,
            };
          }).filter(function (st) { return st.d && st.d[0] != null && st.d[1] != null; });
        })
        .catch(function () {
          return j("/api/sites/" + id + "/stations").then(function (list) {
            CW.stations[id] = (list || []).map(function (st) {
              return { name: st.name, type: st.type, rel: st.rel, d: [st.lat, st.lon] };
            }).filter(function (st) { return st.d && st.d[0] != null && st.d[1] != null; });
          }).catch(function () {});
        }); // 観測所ピンは補助表示のため、取得失敗時も現場詳細自体は表示を継続
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
        origOpen = proto.openSite, origRefresh = proto.refresh, origEnsureMaps = proto.ensureMaps;

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

      // Leaflet 未読込でも確実に読み込んでから地図を構築する（ヘルメット注入失敗・遅延への保険。
      // ユーザー報告: ダッシュボード/作業判断/WBGTのマップが表示されない）
      proto.ensureMaps = function () {
        var self = this;
        if (!window.L) {
          if (typeof cwEnsureLeaflet === "function") {
            cwEnsureLeaflet(function () {
              try { origEnsureMaps.call(self); } catch (e) {}
            });
          } else {
            try { origEnsureMaps.call(self); } catch (e) {}
          }
          return;
        }
        try { origEnsureMaps.call(this); } catch (e) {}
      };

      // 作業種別マスタに無い種別（例: marine）の現場が混入しても地図構築が落ちないよう
      // WORK / LEVELS の欠落を既定値で補完してから元のビルダーを呼ぶ
      function cwSafeWorkLevels(self) {
        var wk = self.WORK = self.WORK || {};
        var lv = self.LEVELS = self.LEVELS || {};
        if (!wk.marine) wk.marine = { label: "海上作業", color: "#0f6ea8" };
        (self.SITES || []).forEach(function (s) {
          if (s && s.work && !wk[s.work]) {
            wk[s.work] = { label: s.work, color: "#566472" };
          }
        });
        [0, 1, 2, 3, 4].forEach(function (i) {
          if (!lv[i]) {
            lv[i] = { label: "確認不能", tag: "?", color: "#5a6b7b", ink: "#fff", bg: "#eef1f4", border: "#c9d3db" };
          }
        });
      }
      // 詳細マップ出力（#79 派生: タイル背景・現場詳細・観測所最新値・凡例を出す）
      function cwAddTiles(L, map) {
        var u = window.__CW_TILE_URL__ || "";
        if (!u) return;
        var has = false;
        map.eachLayer(function (layer) { if (layer instanceof L.TileLayer) has = true; });
        if (!has) L.tileLayer(u, {
          maxZoom: 18, attribution: window.__CW_TILE_ATTRIBUTION__ || ""
        }).addTo(map);
      }
      function cwLatestText(latest) {
        if (!latest) return "実測値なし";
        var parts = [];
        if (latest.waterLevelM != null) parts.push("水位 " + latest.waterLevelM + " m");
        if (latest.rainfallMmH != null) parts.push("雨量 " + latest.rainfallMmH + " mm/h");
        var txt = parts.length ? parts.join(" / ") : "値なし";
        return txt + "（" + esc(latest.observedAt || "") + " ・ " + esc(latest.source || "") + "）";
      }
      function cwIsUpstream(rel) {
        return rel === "upstream" || rel === "上流";
      }
      function cwSitePop(site) {
        var wk = (this.WORK && this.WORK[site.work] && this.WORK[site.work].label) || site.work || "";
        var lv = this.LEVELS && this.LEVELS[site.level] ? this.LEVELS[site.level] : null;
        var rows = [];
        if (site.rainPeak != null) rows.push("ピーク雨量 " + site.rainPeak + " mm/h");
        if (site.windMax != null) rows.push("最大風速 " + site.windMax + " m/s");
        if (site.gust != null) rows.push("突風 " + site.gust + " m/s");
        if (site.tempHi != null) rows.push("最高気温 " + site.tempHi + "℃");
        if (site.wbgt != null) rows.push("WBGT " + site.wbgt);
        if (site.river) {
          rows.push("河川: " + site.river
            + (site.riverSource ? "（" + site.riverSource + "）" : ""));
        }
        return '<div style="min-width:200px">'
          + '<div style="font-weight:800;font-size:13px;color:#1c2935;margin-bottom:2px">' + esc(site.name) + "</div>"
          + '<div style="font-size:11px;color:#62727f;margin-bottom:6px">' + esc(wk) + " ・ "
          + (lv ? '<b style="color:' + esc(lv.color) + '">' + esc(lv.label) + "</b>" : "—") + "</div>"
          + (rows.length
            ? '<div style="font-size:11.5px;line-height:1.7;color:#3a4854">'
              + rows.map(function (r) { return "・" + esc(r); }).join("<br>") + "</div>"
            : "")
          + '<button class="cw-open" style="width:100%;margin-top:8px;padding:6px;border:none;'
          + "background:#16527d;color:#fff;border-radius:6px;font-size:11.5px;font-weight:700;"
          + 'cursor:pointer;font-family:inherit">現場詳細を開く</button>'
          + "</div>";
      }
      proto.buildDashMap = function (L, el) {
        cwSafeWorkLevels(this);
        var self = this;
        var map = L.map(el, { scrollWheelZoom: false }).setView([35.58, 139.6], 10);
        cwAddTiles(L, map);
        var pts = [];
        (this.SITES || []).forEach(function (s) {
          var c = self.COORDS[s.id]; if (!c) return;
          var lv = self.LEVELS[s.level] || self.LEVELS[0];
          var m = L.marker(c, { icon: self.pin(L, lv.color, lv.tag) }).addTo(map);
          m.bindPopup(cwSitePop.call(self, s));
          m.on("popupopen", function (e) {
            var b = e.popup._contentNode.querySelector(".cw-open");
            if (b) b.onclick = function () { self.openSite(s.id); };
          });
          pts.push(c);
        });
        if (pts.length) map.fitBounds(pts, { padding: [46, 46] });
        return map;
      };
      proto.buildSiteMap = function (L, el, siteId) {
        cwSafeWorkLevels(this);
        var self = this;
        var s = this.SITES.filter(function (x) { return x.id === siteId; })[0] || this.SITES[0];
        var c = this.COORDS[s.id] || [35.58, 139.6];
        var map = L.map(el, { scrollWheelZoom: false }).setView(c, 13);
        cwAddTiles(L, map);
        var lv = this.LEVELS[s.level] || this.LEVELS[0];
        var pts = [c];
        var siteMk = L.marker(c, { icon: this.pin(L, lv.color, "現") }).addTo(map);
        siteMk.bindPopup(cwSitePop.call(this, s));
        siteMk.on("popupopen", function (e) {
          var b = e.popup._contentNode.querySelector(".cw-open");
          if (b) b.onclick = function () {
            self.setState({ screen: "decision",
              dform: Object.assign({}, self.state.dform || {}, { siteId: s.id }) });
          };
        });
        (this.STATIONS[s.id] || []).forEach(function (st) {
          var col = st.type === "river" ? "#0e7d8f"
            : st.type === "wbgt" ? "#cc4444"
              : st.type === "weather" ? "#e0892a" : "#566472";
          var mk = L.marker(st.d, { icon: self.dotPin(L, col) }).addTo(map);
          mk.bindPopup('<div style="min-width:180px"><b>' + esc(st.name) + "</b><br>"
            + '<span style="font-size:11px;color:#62727f">' + esc(st.rel || "") + " 観測所"
            + (st.stationCode ? " ・ " + esc(st.stationCode) : "")
            + (st.kind ? " ・ " + esc(st.kind) : "") + "</span><br>"
            + '<span style="font-size:12px;color:#3a4854">' + cwLatestText(st.latest) + "</span></div>");
          pts.push(st.d);
          if (st.type === "river" && cwIsUpstream(st.rel)) {
            var up = [st.d];
            L.polyline(up.concat([c]), { color: "#2a8fb0", weight: 3, opacity: .65, dashArray: "7 5" })
              .addTo(map);
          }
        });
        // 観測所凡例（詳細出力）
        var legend = L.control({ position: "bottomleft" });
        legend.onAdd = function () {
          var d = L.DomUtil.create("div", "cw-map-legend");
          d.style.cssText = "background:#fff;border:1px solid #d4dce2;border-radius:8px;"
            + "padding:6px 9px;font:11px 'Noto Sans JP',sans-serif;color:#3a4854;line-height:1.9";
          d.innerHTML = '<b style="color:#1c2935">観測所</b><br>'
            + '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#0e7d8f;margin-right:5px"></span>河川水位・雨量<br>'
            + '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#e0892a;margin-right:5px"></span>気象（アメダス等）<br>'
            + '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#cc4444;margin-right:5px"></span>WBGT';
          return d;
        };
        legend.addTo(map);
        map.fitBounds(pts, { padding: [40, 40], maxZoom: 14 });
        return map;
      };

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
      login: login, logout: logout, oidcStatus: oidcStatus,
      getToken: getToken, clearToken: clearToken,
      me: function () { return j("/api/auth/me"); },
      notifications: function () { return j("/api/notifications"); },
      rules: function () { return j("/api/admin/rules"); },
      saveRules: function (updates) {
        // PUT はエラー詳細(422の検証メッセージ等)を画面表示するため status も返す
        return _fetch(url("/api/admin/rules"), {
          method: "PUT",
          headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
          body: JSON.stringify({ updates: updates })
        }).then(function (r) {
          return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
        });
      },
      // ---- #79/#80: 管理系API（監査ログ・アプリ設定・AI設定） ----
      auditLogs: function (limit) {
        return j("/api/admin/audit-logs" + (limit ? "?limit=" + limit : ""));
      },
      opsStatusSnapshot: function () { return j("/api/admin/ops/status-snapshot"); },
      appSettings: function () { return j("/api/admin/settings"); },
      saveAppSettings: function (updates) {
        // saveRules と同様、エラー詳細表示のため status も返す（bodyはフラットなwhitelistキー）
        return _fetch(url("/api/admin/settings"), {
          method: "PUT",
          headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
          body: JSON.stringify(updates)
        }).then(function (r) {
          return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
        });
      },
      aiTest: function (apiKey, opts) {
        // api_key 省略時はサーバ保存済みキーで接続テスト（HTTP 200 で {ok:bool} が返る契約）
        var body = {};
        if (apiKey) body.api_key = apiKey;
        if (opts && opts.provider) body.provider = opts.provider;
        return _fetch(url("/api/admin/settings/ai/test"), {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
          body: JSON.stringify(body)
        }).then(function (r) {
          return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
        });
      },
      aiDisconnect: function () {
        return _fetch(url("/api/admin/settings/ai"), {
          method: "DELETE", headers: authHeaders()
        }).then(function (r) {
          return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
        });
      },
      // ---- #72 段5: 海象データ（全国版 / 海上作業判定） ----
      marineNational: function () { return j("/api/marine/national"); },
      returnPeriods: function () { return j("/api/marine/return-periods"); },
      evaluateMarine: function (siteId, start, end) {
        return _fetch(url("/api/decisions/evaluate"), {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
          body: JSON.stringify({ site_id: siteId, work_type: "marine",
            start: start || null, end: end || null })
        }).then(function (r) {
          return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
        });
      },
      // 認証ヘッダ付き素の fetch（CSVダウンロード等、JSON以外の応答向け）
      authedFetch: function (p, o) {
        o = o || {};
        o.headers = Object.assign({}, authHeaders(), o.headers || {});
        return _fetch(url(p), o);
      },
      mapDashToSites: mapDashToSites, mapSources: mapSources, _state: CW
    };
  }

  // ---- API接続先の許可判定（?api= 上書きによる認証情報の外部流出を防ぐ） ----
  // index.html/serve.py は ?api= の値を無検証で localStorage に永続化し window.__CW_API_BASE__
  // へ渡す。ここで弾かなければ、悪意あるURL(?api=https://evil.example)を一度開かせるだけで、
  // 以後のログインPOSTやJWT付きAPIコールが localStorage 永続化のまま外部へ送信され続ける。
  // LAN内運用を前提に、同一オリジン/localhost/プライベートIPのみ許可する。
  function isAllowedApiBase(v) {
    if (!v) return true; // "" = 同一オリジン
    // 正規表現による手作業パースは "user:pass@host" の userinfo を考慮できず、
    // 例えば "https://192.168.1.1:@evil.example" のような値を後段の split(":")[0]
    // に通すと "192.168.1.1" を抽出してしまい許可判定を誤る（実際に fetch/ブラウザが
    // 接続する先は @ 以降の evil.example）。実リクエストと同じ URL パーサーに判定を
    // 委譲し、userinfo が付与されている時点で拒否する（対抗レビュー起点 #91 派生）。
    var u;
    try {
      u = new URL(v);
    } catch (e) {
      return false;
    }
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    if (u.username || u.password) return false;
    if (u.pathname !== "/" && u.pathname !== "") return false;
    if (u.search || u.hash) return false;
    var host = u.hostname;
    if (host === "localhost" || host === "127.0.0.1" || host === "[::1]") return true;
    var ipv4 = /^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(host);
    if (!ipv4) return false;
    var a = +ipv4[1], b = +ipv4[2];
    return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  }

  // ---- ブラウザ自動起動 ----
  if (typeof window !== "undefined" && window.document) {
    var apiBase = window.__CW_API_BASE__ != null ? window.__CW_API_BASE__ : "";
    if (!isAllowedApiBase(apiBase)) {
      console.warn("[CW] 許可されない API 接続先のため同一オリジンへフォールバックしました: " + apiBase);
      try { localStorage.removeItem("cw_api"); } catch (e) {}
      apiBase = "";
    }
    var cwUser = null; // ログイン中ユーザー（ロールでナビ表示・編集可否を出し分け）
    // ナビへ「現場登録」等を追加し、該当画面のときだけ注入パネルを表示する
    var regInst = null;
    function cwToggleRegScreen(show) {
      var el = document.getElementById("cw-reg-screen");
      if (el) el.style.display = show ? "block" : "none";
    }
    function cwToggleSettingsScreen(show) {
      var el = document.getElementById("cw-settings-screen");
      if (el) {
        var was = el.style.display;
        el.style.display = show ? "block" : "none";
        if (show && was !== "block") cwLoadSettings(); // 表示のたびに最新値を取得
      }
    }
    // ---- メニュー体系（#79: 5グループ+ダッシュボード。単一の真実） ----
    // native: dc標準タブ（narrow幅のdcナビに既存） / go: 遷移先screen / preset: 作業種別プリセット
    // roles: 表示を許可するロール（未指定は全ロール）
    var CW_MENU = [
      { items: [{ key: "dashboard", label: "ダッシュボード", native: true }] },
      { title: "現場管理", items: [
        { key: "sites-list", label: "現場一覧" },
        { key: "register", label: "＋現場登録" }] },
      { title: "気象・海象データ", items: [
        { key: "wx-national", label: "気象データ：全国版" },
        { key: "marine-national", label: "海象データ：全国版" }] },
      { title: "施工判定", items: [
        { key: "decision", label: "作業判断", native: true },
        { key: "concrete-cast", label: "コンクリート打設", go: "decision", preset: "concrete" },
        { key: "marine-work", label: "海上作業" },
        { key: "wbgt", label: "熱中症・WBGT", native: true },
        { key: "river-obs", label: "河川観測" }] },
      { title: "分析", items: [
        { key: "history", label: "判断履歴", native: true },
        { key: "analytics", label: "過去データ分析" },
        { key: "wave50", label: "50年確率波" }] },
      { title: "管理", items: [
        { key: "settings", label: "閾値管理", roles: ["admin", "tech_manager"] },
        { key: "source", label: "データ取得状況", native: true },
        { key: "ops-status", label: "運用状態", roles: ["admin", "tech_manager"] },
        { key: "reports", label: "レポート出力" },
        { key: "audit", label: "監査ログ", roles: ["admin", "tech_manager"] },
        { key: "app-settings", label: "設定", roles: ["admin"] }] },
    ];
    function cwFindItem(key) {
      for (var gi = 0; gi < CW_MENU.length; gi++) {
        var items = CW_MENU[gi].items;
        for (var ii = 0; ii < items.length; ii++) if (items[ii].key === key) return items[ii];
      }
      return null;
    }
    var cwActiveKey = null; // プリセット項目(コンクリート打設等)の選択網掛け維持用
    function cwMenuGo(key) {
      var it = cwFindItem(key);
      if (!it || !regInst) return;
      cwActiveKey = key;
      try {
        regInst.go(it.go || it.key);
        if (it.preset) { // 作業判断画面を対象種別で開く（例: コンクリート打設）
          regInst.setState({ dform: Object.assign({}, regInst.state.dform, { workType: it.preset }) });
        }
      } catch (e) {}
    }
    function cwNavItem(inst, key, label) {
      var active = inst.state.screen === key;
      return {
        label: label, weight: active ? 800 : 600,
        color: active ? "#13344f" : "#697A88", bar: active ? "#13344f" : "transparent",
        onClick: function () { cwMenuGo(key); },
        badge: 0, badgeShow: "none", badgeBg: "#c62828", badgeColor: "#fff"
      };
    }
    function cwInjectNav(inst, vals) {
      // narrow幅フォールバック（dc上部タブ）: カスタム画面をロール別に追加（#34/#79）
      if (!vals.nav || !vals.nav.concat) return;
      var role = cwUser ? cwUser.role : "";
      var extra = [];
      CW_MENU.forEach(function (g) {
        g.items.forEach(function (it) {
          if (it.native || it.preset) return; // dc標準タブ/プリセット項目は追加しない
          if (it.roles && (!role || it.roles.indexOf(role) < 0)) return;
          extra.push(cwNavItem(inst, it.key, it.label));
        });
      });
      vals.nav = vals.nav.concat(extra);
    }
    var adapter = createAdapter({
      base: apiBase,
      fetch: window.fetch.bind(window),
      // CSV等のエクスポートURLは window.open だと Authorization ヘッダが付かず401になるため、
      // 認証付き fetch→Blob ダウンロードへ差し替える（#79。createAdapter/契約テストは無改修）
      open: function (u) { cwAuthedDownload(u); },
      // 401: トークン破棄＋機微画面(監査ログ/設定)の残留データを消してからログイン画面へ
      // （前ユーザーの表示が次ユーザーへ漏れない。#83 対抗レビュー[high]）
      onUnauthorized: function () { adapter.clearToken(); cwResetSensitiveUi(); showLogin(); },
      hideNav: ["現場詳細"], // 現場詳細はナビタブを廃し、現場クリックのドリルダウン専用にする
      bump: function () {
        try {
          var rn = (window.__dcRootName && window.__dcRootName()) || "Root";
          window.__dcSetProps(rn, { __cw: Date.now() });
        } catch (e) {}
      },
      afterRender: function (inst, vals) {
        regInst = inst;            // 登録成功後の画面遷移に使用
        cwInjectNav(inst, vals);   // narrow幅: dc上部タブへカスタム画面を追加
        cwRenderSidebar(inst);     // PC幅: グループ化サイドメニュー（#79）
        cwToggleRegScreen(inst.state.screen === "register"); // 該当画面のみパネル表示
        // データ更新でダッシュボード地図を作り直す（dc キャッシュは token='all' で再生成されないため）
        if (inst._dashMap && inst.__cwDashVer !== adapter._state.ver) {
          try { inst._dashMap.remove(); } catch (e) {}
          inst._dashMap = null; inst.__cwDashVer = adapter._state.ver;
        }
        cwToggleSourceNote(inst.state.screen === "source"); // データソース画面の更新間隔注記
        cwToggleSettingsScreen(inst.state.screen === "settings"); // 閾値管理画面（#34）
        cwSyncWbgtScreen(inst);                              // WBGT画面の地図
        cwSyncScreens(inst);                                 // #79 新画面群の表示同期
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
      // 未ログイン時は /api/work-types が401になることが確定しているため取得自体を省略する
      // （ログイン成功時は location.reload() で全体を再初期化するため、認証後は必ず再取得される）
      if (adapter.getToken()) {
        adapter.workTypes().then(function (wts) {
          sel.innerHTML = "";
          wts.forEach(function (w) {
            var opt = document.createElement("option");
            opt.value = w.id;
            opt.textContent = w.name;
            sel.appendChild(opt);
          });
        }).catch(function () { sel.innerHTML = '<option value="river">河川内作業</option>'; });
      } else {
        sel.innerHTML = '<option value="river">河川内作業</option>';
      }

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
    // 「システム設定」画面（#34: 判定閾値の閲覧・編集。API: /api/admin/rules）
    function cwLoadSettings() {
      var list = document.getElementById("cw-set-list");
      var msg = document.getElementById("cw-set-msg");
      if (!list) return;
      list.innerHTML = '<div class="cw-set-empty">読込中…</div>';
      if (msg) msg.textContent = "";
      adapter.rules().then(function (d) {
        var rules = (d && d.rules) || [];
        if (!rules.length) { list.innerHTML = '<div class="cw-set-empty">取得できませんでした</div>'; return; }
        var canEdit = cwUser && cwUser.role === "admin";
        list.innerHTML = rules.map(function (r) {
          return '<div class="cw-set-row" data-key="' + esc(r.key) + '" data-default="' + r.default + '">'
            + '<div class="cw-set-info"><div class="t">' + esc(r.label)
            + (r.overridden ? ' <span class="cw-set-badge">上書き中</span>' : "")
            + '</div><div class="d">' + esc(r.desc) + '</div>'
            + (r.overridden && r.updated_by
               ? '<div class="d">変更: ' + esc(r.updated_by) + '（' + esc(r.updated_at || "") + '）</div>' : "")
            + '</div>'
            + '<div class="cw-set-def">既定 ' + r.default + (r.unit ? " " + esc(r.unit) : "") + '</div>'
            + '<div class="cw-set-input"><input type="number" step="0.1" value="' + r.value + '"'
            + ' min="' + r.min + '" max="' + r.max + '"' + (canEdit ? "" : " disabled") + '>'
            + (r.unit ? '<span class="u">' + esc(r.unit) + "</span>" : "") + '</div>'
            + '</div>';
        }).join("");
        var save = document.getElementById("cw-set-save");
        if (save) save.style.display = canEdit ? "inline-block" : "none";
        var note = document.getElementById("cw-set-note");
        if (note) note.textContent = canEdit
          ? "既定値と同じ値にして保存すると上書きは解除されます。"
          : "閲覧のみ（変更は管理者アカウントで行ってください）。";
      }).catch(function () {
        list.innerHTML = '<div class="cw-set-empty">取得に失敗しました（権限またはAPIエラー）</div>';
      });
    }
    function cwSaveSettings() {
      var msg = document.getElementById("cw-set-msg");
      var updates = {};
      var rows = document.querySelectorAll("#cw-set-list .cw-set-row");
      rows.forEach(function (row) {
        var key = row.getAttribute("data-key");
        var def = parseFloat(row.getAttribute("data-default"));
        var input = row.querySelector("input");
        if (!key || !input || input.value === "") return;
        var v = parseFloat(input.value);
        if (isNaN(v)) return;
        // 既定値と同値なら上書き解除(null)、それ以外は上書き値として送信
        updates[key] = (v === def) ? null : v;
      });
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "保存中…"; }
      adapter.saveRules(updates).then(function (res) {
        if (res.ok) {
          if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "保存しました（判定へ即時反映されます）"; }
          cwLoadSettings();
        } else {
          var detail = (res.body && res.body.detail) || ("HTTP " + res.status);
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "保存できません: " + detail; }
        }
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラーで保存できませんでした"; }
      });
    }
    function installSettingsScreen() {
      if (document.getElementById("cw-settings-screen")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-settings-screen{display:none;position:fixed;left:0;right:0;top:56px;bottom:0;z-index:30;overflow:auto;"
        + "background:#eef1f4;font-family:'Noto Sans JP',system-ui,sans-serif;color:#16212c}"
        + ".cw-set-card{background:#fff;margin:22px auto;width:min(760px,94vw);border-radius:12px;padding:22px 24px;box-shadow:0 1px 4px rgba(0,0,0,.08)}"
        + ".cw-set-card h2{margin:0 0 4px;font-size:17px;color:#13344f}"
        + ".cw-set-card p.sub{margin:0 0 14px;font-size:11.5px;color:#7e8c99}"
        + ".cw-set-row{display:flex;align-items:center;gap:12px;padding:11px 4px;border-bottom:1px solid #f0f3f6}"
        + ".cw-set-info{flex:1;min-width:0}"
        + ".cw-set-info .t{font-size:13px;font-weight:700;color:#1c2935}"
        + ".cw-set-info .d{font-size:11px;color:#7e8c99;margin-top:2px}"
        + ".cw-set-badge{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:8px;background:#fdf6e0;"
        + "border:1px solid #ecdca0;color:#8a6d1a;font-size:10px;font-weight:700;vertical-align:1px}"
        + ".cw-set-def{flex:none;width:110px;text-align:right;font-size:11px;color:#7e8c99}"
        + ".cw-set-input{flex:none;display:flex;align-items:center;gap:5px}"
        + ".cw-set-input input{width:96px;padding:7px 8px;border:1px solid #d4dce2;border-radius:7px;"
        + "font:600 13px 'Noto Sans JP',sans-serif;text-align:right;box-sizing:border-box}"
        + ".cw-set-input input:disabled{background:#f4f6f8;color:#8a99a5}"
        + ".cw-set-input .u{font-size:11px;color:#7e8c99;min-width:34px}"
        + ".cw-set-empty{padding:20px;color:#7e8c99;font-size:12px;text-align:center}"
        + ".cw-set-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:16px}"
        + ".cw-set-actions button{padding:10px 18px;border-radius:7px;font:700 13px 'Noto Sans JP',sans-serif;cursor:pointer;border:none}"
        + "#cw-set-reload{background:#eef1f4;color:#5a6b7b}#cw-set-save{background:#16527d;color:#fff}"
        + ".cw-set-msg{font-size:12px;min-height:16px;flex:1}"
        + "#cw-set-note{margin:10px 0 0;font-size:11px;color:#7e8c99}";
      document.head.appendChild(css);
      var screen = document.createElement("div");
      screen.id = "cw-settings-screen";
      screen.innerHTML =
        '<div class="cw-set-card">'
        + "<h2>閾値管理 — 判定閾値（会社基準）</h2>"
        + '<p class="sub">注意・中止検討の判定に使う閾値です。変更は全現場の判定に即時反映されます（監査ログに記録）。</p>'
        + '<div id="cw-set-list"></div>'
        + '<p id="cw-set-note"></p>'
        + '<div class="cw-set-actions"><div class="cw-set-msg" id="cw-set-msg"></div>'
        + '<button id="cw-set-reload" type="button">再読込</button>'
        + '<button id="cw-set-save" type="button">保存</button></div>'
        + "</div>";
      document.body.appendChild(screen);
      screen.querySelector("#cw-set-reload").addEventListener("click", cwLoadSettings);
      screen.querySelector("#cw-set-save").addEventListener("click", cwSaveSettings);
    }

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

    // ---- 熱中症/WBGT 画面: 全国地図＋スケール＋ランキング ----
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
    function cwTileUrl() {
      return window.__CW_TILE_URL__ || "";
    }
    function cwTileAttribution() {
      return window.__CW_TILE_ATTRIBUTION__ || "";
    }
    // Leaflet を確実に読み込む（dc-runtime の helmet 注入が遅い/失敗した場合の保険）。
    // vendor/ は自己ホスト済みのため外部CDN依存はない（frontend/test/vendor-assets-policy.cjs）
    var cwLeafletLoading = false;
    function cwEnsureLeaflet(cb) {
      if (window.L) { cb(); return; }
      if (cwLeafletLoading) { setTimeout(function () { cwEnsureLeaflet(cb); }, 120); return; }
      cwLeafletLoading = true;
      var css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "./vendor/leaflet/leaflet.css";
      document.head.appendChild(css);
      var s = document.createElement("script");
      s.src = "./vendor/leaflet/leaflet.js";
      s.async = false;
      s.onload = function () { cwLeafletLoading = false; cb(); };
      s.onerror = function () {
        cwLeafletLoading = false;
        setTimeout(function () { cwEnsureLeaflet(cb); }, 500);
      };
      document.head.appendChild(s);
    }
    function buildWbgtScreen() {
      var CW = adapter._state; // 外からは _state 経由で参照
      if (!CW.sites) return;
      var mapEl = document.getElementById("cw-wbgt-map");
      if (!mapEl) return;
      buildWbgtRank();
      if (!window.L) { // Leaflet 未ロードなら再試行（dc の ensureMaps と同じ作法）
        if (typeof cwEnsureLeaflet === "function") {
          cwEnsureLeaflet(function () { wbgtBuildTries = 0; buildWbgtScreen(); });
          return;
        }
        if (wbgtBuildTries++ < 50) setTimeout(buildWbgtScreen, 150);
        return;
      }
      var L = window.L;
      if (!wbgtMap) {
        wbgtMap = L.map(mapEl, { scrollWheelZoom: false }).setView([37.5, 137.0], 4);
        var tileUrl = cwTileUrl();
        if (tileUrl) L.tileLayer(tileUrl,
          { maxZoom: 18, attribution: cwTileAttribution() }).addTo(wbgtMap);
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
          var extra = [];
          if (s.tempHi != null) extra.push("最高気温 " + esc(s.tempHi) + "℃");
          if (s.windMax != null) extra.push("風速 " + esc(s.windMax) + " m/s");
          mk.bindPopup("<b>" + esc(s.name) + "</b><br>WBGT "
            + (s.wbgt == null ? "—" : esc(s.wbgt))
            + ' ・ <b style="color:' + m.color + '">' + m.label + "</b>"
            + (extra.length ? "<br>" + extra.join(" ・ ") : ""));
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

    // ================= #79: グループ化サイドバー ＋ 新画面群 =================

    function installSidebar() {
      if (document.getElementById("cw-sidebar")) return;
      var el = document.createElement("div");
      el.id = "cw-sidebar";
      document.body.appendChild(el);
      el.addEventListener("click", function (e) {
        var t = e.target;
        // グループ見出し（アコーディオン開閉）の判定
        var h = t;
        while (h && h !== el && !(h.classList && h.classList.contains("cw-sb-h"))) h = h.parentNode;
        if (h && h !== el && h.getAttribute("data-group")) {
          var g = h.getAttribute("data-group");
          cwSbOpen = (cwSbOpen === g) ? null : g; // 同一グループは折りたたみ、他グループは1つだけ開く
          cwRenderSidebar(regInst);
          return;
        }
        // メニュー項目の判定
        while (t && t !== el && !(t.classList && t.classList.contains("cw-sb-i"))) t = t.parentNode;
        if (t && t !== el && t.getAttribute("data-key")) cwMenuGo(t.getAttribute("data-key"));
      });
    }
    var cwSbSig = "";
    var cwSbOpen = null; // アコーディオンで開いているグループのキー（null=全て閉じる）
    var cwSbLastAct = null;
    function cwRenderSidebar(inst) {
      var el = document.getElementById("cw-sidebar");
      if (!el || !inst) return;
      var screen = inst.state.screen;
      var act = screen; // 通常は screen キーがそのまま選択項目
      if (cwActiveKey) { // プリセット項目（例: コンクリート打設→decision）は明示クリックを優先表示
        var ai = cwFindItem(cwActiveKey);
        if (ai && (ai.go || ai.key) === screen) act = cwActiveKey;
      }
      var role = cwUser ? cwUser.role : "";
      // アクティブ項目を含むグループは自動で開く（外部遷移・プリセット遷移時も迷子にしない）
      var actGroup = null;
      CW_MENU.forEach(function (g) {
        g.items.forEach(function (it) {
          if (it.key === act) actGroup = g.title || g.items[0].key || null;
        });
      });
      // 画面遷移があった場合のみアクティブグループを自動で開く
      // （ユーザーが手動で開閉したアコーディオン状態を遷移以外で上書きしない）
      if (cwSbLastAct !== act && actGroup) cwSbOpen = actGroup;
      cwSbLastAct = act;
      var sig = screen + "|" + act + "|" + role + "|" + (cwSbOpen || "");
      if (sig === cwSbSig) return; // 変化がなければ再構築しない（afterRender毎の負荷回避）
      cwSbSig = sig;
      el.innerHTML = CW_MENU.map(function (g) {
        var items = g.items.filter(function (it) {
          return !it.roles || (role && it.roles.indexOf(role) >= 0);
        });
        if (!items.length) return "";
        // ダッシュボード（先頭グループ・titleなし）は常時表示の固定項目
        if (!g.title) {
          return items.map(function (it) {
            return '<button type="button" class="cw-sb-i' + (act === it.key ? " on" : "")
              + '" data-key="' + esc(it.key) + '">' + esc(it.label) + "</button>";
          }).join("");
        }
        var open = cwSbOpen === g.title;
        return '<button type="button" class="cw-sb-h" data-group="' + esc(g.title) + '">'
          + '<span class="cw-sb-h-t">' + esc(g.title) + "</span>"
          + '<span class="cw-sb-arrow">' + (open ? "▾" : "▸") + "</span></button>"
          + '<div class="cw-sb-body' + (open ? " on" : "") + '">'
          + items.map(function (it) {
            return '<button type="button" class="cw-sb-i' + (act === it.key ? " on" : "")
              + '" data-key="' + esc(it.key) + '">' + esc(it.label) + "</button>";
          }).join("")
          + "</div>";
      }).join("");
    }

    // ---- 画面フレームワーク（#79 新画面の器と表示同期） ----
    function cwMakeScreen(id, inner) {
      if (document.getElementById(id)) return null;
      var d = document.createElement("div");
      d.id = id;
      d.className = "cw-screen";
      d.innerHTML = inner;
      document.body.appendChild(d);
      return d;
    }
    var CW_SCREENS = {}; // key -> {id, show(inst), live}
    var cwScreenVer = {};
    function cwSyncScreens(inst) {
      for (var k in CW_SCREENS) {
        var sc = CW_SCREENS[k];
        var el = document.getElementById(sc.id);
        if (!el) continue;
        var on = inst.state.screen === k;
        var was = el.style.display === "block";
        el.style.display = on ? "block" : "none";
        // 表示された時、または（live画面は）データ版数が進んだ時に再描画
        if (on && sc.show && (!was || (sc.live && cwScreenVer[k] !== adapter._state.ver))) {
          cwScreenVer[k] = adapter._state.ver;
          try { sc.show(inst); } catch (e) {}
        }
      }
    }

    // ---- 認証付きダウンロード（CSV等。window.open ではトークンが付かないため） ----
    function cwAuthedDownload(fullUrl, filename) {
      var t = adapter.getToken();
      fetch(fullUrl, { headers: t ? { Authorization: "Bearer " + t } : {} }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.blob();
      }).then(function (b) {
        var name = filename;
        if (!name) {
          var pathPart = String(fullUrl).split("?")[0];
          name = pathPart.substring(pathPart.lastIndexOf("/") + 1) || "export.csv";
        }
        cwSaveBlob(b, name);
      }).catch(function () { alert("ダウンロードに失敗しました（権限またはAPIエラー）"); });
    }
    function cwSaveBlob(blob, filename) {
      var u = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = u; a.download = filename;
      document.body.appendChild(a); a.click();
      setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(u); }, 250);
    }
    function cwCsvText(rows) { // Excel(JP)向けBOM付きCSV
      function cell(v) {
        var s = String(v == null ? "" : v);
        // 表計算ソフトの式注入(=,+,-,@等で始まる値)を無害化（#83 対抗レビュー[medium]）
        if (/^\s*[=+\-@\t\r]/.test(s)) s = "'" + s;
        return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      }
      return "﻿" + rows.map(function (r) { return r.map(cell).join(","); }).join("\r\n");
    }

    // 機微画面（監査ログ・設定）の表示データと画面状態を破棄する（401時。#83 対抗レビュー[high]）
    function cwResetSensitiveUi() {
      cwAuditRows = null;
      cwUser = null;
      cwSbSig = "";       // サイドバーをロールなし状態で再構築させる
      cwSbOpen = null;     // アコーディオン開閉状態もリセット（前ユーザーの状態を残さない）
      cwSbLastAct = null;
      cwScreenVer = {};   // 次回表示時に必ず再読込させる
      var audit = document.getElementById("cw-audit-body");
      if (audit) audit.innerHTML = "";
      var ops = document.getElementById("cw-ops-body");
      if (ops) ops.innerHTML = "";
      var q = document.getElementById("cw-audit-q");
      if (q) q.value = "";
      var key = document.getElementById("cw-as-ai-key");
      if (key) key.value = "";
      var st = document.getElementById("cw-as-ai-status");
      if (st) st.textContent = "—";
      var u = document.getElementById("cw-as-user");
      if (u) u.textContent = "";
      ["cw-as-msg", "cw-as-ai-msg"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.textContent = "";
      });
    }

    // ---- 現場一覧（現場管理） ----
    var cwWorkNames = null;
    function cwEnsureWorkNames(cb) {
      if (cwWorkNames) { cb(cwWorkNames); return; }
      adapter.workTypes().then(function (w) {
        cwWorkNames = {};
        (w || []).forEach(function (x) { cwWorkNames[x.id] = x.name; });
        cb(cwWorkNames);
      }).catch(function () { cb({}); });
    }
    var CW_LV = ["通常", "注意", "中止検討", "確認不能"];
    var CW_LVC = ["#2e7d32", "#c2920a", "#c62828", "#5a6b7b"];
    function installSitesScreen() {
      var el = cwMakeScreen("cw-sites-screen",
        '<div class="cw-pg"><h2>現場一覧</h2>'
        + '<p class="sub">登録済みの全現場。行クリックで現場詳細（気象・河川・判定）へ移動します。</p>'
        + '<div class="cw-pg-bar"><button type="button" class="cw-btn" id="cw-sites-reload">再読込</button>'
        + '<button type="button" class="cw-btn cw-btn-pri" id="cw-sites-new">＋現場登録</button>'
        + '<span class="cw-msg" id="cw-sites-msg"></span></div>'
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(100vh - 220px)" id="cw-sites-body"></div></div>');
      if (!el) return;
      el.querySelector("#cw-sites-new").addEventListener("click", function () { cwMenuGo("register"); });
      el.querySelector("#cw-sites-reload").addEventListener("click", function () {
        var m = document.getElementById("cw-sites-msg");
        if (m) m.textContent = "更新中…";
        adapter.loadDashboard().then(function () {
          if (m) m.textContent = "";
          cwRenderSites();
        }).catch(function () { if (m) m.textContent = "更新に失敗しました"; });
      });
      el.querySelector("#cw-sites-body").addEventListener("click", function (e) {
        var t = e.target;
        while (t && !t.getAttribute) t = t.parentNode;
        while (t && t.tagName !== "TR") t = t.parentNode;
        var id = t && t.getAttribute("data-id");
        if (id && regInst) { cwActiveKey = null; try { regInst.openSite(id); } catch (e2) {} }
      });
    }
    function cwRenderSites() {
      var box = document.getElementById("cw-sites-body");
      if (!box) return;
      var st = adapter._state;
      var meta = st.meta;
      if (!st.metaLoaded && st.dashboardError) {
        box.innerHTML = '<div class="cw-empty" style="color:#c62828">' + esc(st.dashboardError)
          + '<br><button type="button" class="cw-btn" style="margin-top:10px" id="cw-sites-retry">再読込</button></div>';
        var retry = box.querySelector("#cw-sites-retry");
        if (retry) retry.addEventListener("click", function () {
          adapter.loadDashboard().then(cwRenderSites).catch(function () { cwRenderSites(); });
        });
        return;
      }
      if (!meta || (!st.metaLoaded && !meta.length)) {
        box.innerHTML = '<div class="cw-empty">現場データを読込中です（数秒後に再読込してください）</div>';
        return;
      }
      if (!meta.length) {
        box.innerHTML = '<div class="cw-empty">現場がまだ登録されていません。「＋現場登録」から登録してください。</div>';
        return;
      }
      var dash = {};
      (adapter._state.sites || []).forEach(function (s) { dash[s.id] = s; });
      cwEnsureWorkNames(function (wn) {
        box.innerHTML = '<table class="cw-tbl"><thead><tr><th>ID</th><th>現場名</th><th>所在地</th>'
          + "<th>種別</th><th>区分</th><th>担当</th><th>状態</th><th>現在判定</th></tr></thead><tbody>"
          + meta.map(function (s) {
            var d = dash[s.id];
            // API由来 level は 0-3 のみ配列参照（想定外値は「—」へフォールバック）
            var lvOk = d && CW_LV[d.level] != null && CW_LVC[d.level] != null;
            var lvHtml = lvOk
              ? '<span class="cw-badge" style="background:' + CW_LVC[d.level] + '18;color:' + CW_LVC[d.level] + '">' + CW_LV[d.level] + "</span>"
              : "—";
            var riverBadge = (s.hasRiver || s.work === "river")
              ? '<span class="cw-badge" style="background:#e7f3f4;color:#0e7d8f;margin-left:4px" title="河川観測はデモ自動取得または手動入力（公式の水防災オープンデータは未接続）">河川</span>'
              : "";
            return '<tr class="click" data-id="' + esc(s.id) + '"><td>' + esc(s.id) + "</td><td><b>"
              + esc(s.name) + "</b></td><td>" + esc(s.loc || "") + "</td><td>" + esc(wn[s.work] || s.work || "")
              + "</td><td>" + esc(s.project || "") + "</td><td>" + esc(s.manager || "") + "</td><td>"
              + (s.status === "active" ? "稼働中" : "休止") + riverBadge + "</td><td>" + lvHtml + "</td></tr>";
          }).join("") + "</tbody></table>";
      });
    }

    // ---- 気象データ：全国版（気象・海象データ） ----
    var CW_WX_COLS = [
      { k: "name", label: "現場", num: false },
      { k: "tempHi", label: "最高気温", num: true, unit: "℃" },
      { k: "tempLo", label: "最低気温", num: true, unit: "℃" },
      { k: "rainNow", label: "現在雨量", num: true, unit: "mm/h" },
      { k: "rainPeak", label: "ピーク雨量", num: true, unit: "mm/h" },
      { k: "windMax", label: "最大風速", num: true, unit: "m/s" },
      { k: "gust", label: "突風", num: true, unit: "m/s" },
      { k: "wbgt", label: "WBGT", num: true, unit: "" },
      { k: "updated", label: "更新", num: false },
    ];
    var cwWxSort = { k: "wbgt", desc: true };
    function installWxScreen() {
      var el = cwMakeScreen("cw-wx-screen",
        '<div class="cw-pg"><h2>気象データ：全国版</h2>'
        + '<p class="sub">全登録現場の気象概況（Open-Meteo 1時間毎予報＋気象庁警報を判定に使用）。列見出しクリックで並べ替え。</p>'
        + '<div class="cw-pg-bar"><button type="button" class="cw-btn" id="cw-wx-reload">再読込</button>'
        + '<span class="cw-msg" id="cw-wx-msg"></span></div>'
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(100vh - 220px)" id="cw-wx-body"></div></div>');
      if (!el) return;
      el.querySelector("#cw-wx-reload").addEventListener("click", function () {
        var m = document.getElementById("cw-wx-msg");
        if (m) m.textContent = "更新中…";
        adapter.loadDashboard().then(function () {
          if (m) m.textContent = "";
          cwRenderWx();
        }).catch(function () { if (m) m.textContent = "更新に失敗しました"; });
      });
      el.querySelector("#cw-wx-body").addEventListener("click", function (e) {
        var t = e.target;
        while (t && t.tagName !== "TH" && t.tagName !== "BODY") t = t.parentNode;
        var k = t && t.getAttribute && t.getAttribute("data-k");
        if (!k) return;
        cwWxSort = { k: k, desc: cwWxSort.k === k ? !cwWxSort.desc : true };
        cwRenderWx();
      });
    }
    function cwRenderWx() {
      var box = document.getElementById("cw-wx-body");
      if (!box) return;
      var st = adapter._state;
      var sites = st.sites;
      if (!st.metaLoaded && st.dashboardError) {
        box.innerHTML = '<div class="cw-empty" style="color:#c62828">' + esc(st.dashboardError) + "</div>";
        return;
      }
      if (!sites || !sites.length) {
        if (st.metaLoaded) {
          box.innerHTML = '<div class="cw-empty">現場がまだ登録されていません。</div>';
          return;
        }
        box.innerHTML = '<div class="cw-empty">気象データを読込中です（数秒後に再読込してください）</div>';
        return;
      }
      var k = cwWxSort.k, desc = cwWxSort.desc;
      var rows = sites.slice().sort(function (a, b) {
        var x = a[k], y = b[k];
        if (x == null && y == null) return 0; // 両方欠測は同順（比較の対称性を保つ）
        if (x == null) return 1;
        if (y == null) return -1;
        if (x < y) return desc ? 1 : -1;
        if (x > y) return desc ? -1 : 1;
        return 0;
      });
      box.innerHTML = '<table class="cw-tbl"><thead><tr>' + CW_WX_COLS.map(function (c) {
        var arrow = cwWxSort.k === c.k ? (cwWxSort.desc ? " ▼" : " ▲") : "";
        return '<th class="sort" data-k="' + c.k + '">' + c.label + arrow + "</th>";
      }).join("") + "</tr></thead><tbody>"
        + rows.map(function (s) {
          return "<tr>" + CW_WX_COLS.map(function (c) {
            if (c.k === "name") {
              return "<td><b>" + esc(s.name) + '</b><div style="font-size:10.5px;color:#7e8c99">' + esc(s.loc || "") + "</div></td>";
            }
            var v = s[c.k];
            var txt = v == null ? "—" : (c.num ? esc(v) + (c.unit ? " " + c.unit : "") : esc(v));
            if (c.k === "wbgt" && v != null) {
              var m = wbgtMeta(v);
              txt = '<b style="color:' + m.color + '">' + esc(v) + "</b> " + m.label;
            }
            return '<td class="' + (c.num ? "cw-num" : "") + '">' + txt + "</td>";
          }).join("") + "</tr>";
        }).join("") + "</tbody></table>";
    }

    // ---- 過去データ分析（分析） ----
    function installAnalyticsScreen() {
      cwMakeScreen("cw-analytics-screen",
        '<div class="cw-pg"><h2>過去データ分析</h2>'
        + '<p class="sub">判断履歴の集計（現時点はブラウザ内集計）。Neon 蓄積時系列を用いた統計・傾向分析は #72 段6 で拡充予定。</p>'
        + '<div id="cw-an-body"></div></div>');
    }
    function cwBarRows(counts, colorFn) {
      var keys = Object.keys(counts);
      var max = 1;
      keys.forEach(function (kk) { if (counts[kk] > max) max = counts[kk]; });
      return keys.sort(function (a, b) { return counts[b] - counts[a]; }).map(function (kk) {
        var c = counts[kk];
        var col = colorFn ? colorFn(kk) : "#16527d";
        return '<div style="display:flex;align-items:center;gap:8px;margin:6px 0;font-size:12px">'
          + '<span style="min-width:110px;font-weight:700;color:#3a4854">' + esc(kk) + "</span>"
          + '<div style="flex:1;background:#f0f3f6;border-radius:5px;height:14px;overflow:hidden">'
          + '<div style="width:' + Math.round(c / max * 100) + "%;height:100%;background:" + col + '"></div></div>'
          + '<b style="min-width:34px;text-align:right">' + c + "</b></div>";
      }).join("");
    }
    function cwRenderAnalytics() {
      var box = document.getElementById("cw-an-body");
      if (!box) return;
      var h = adapter._state.history;
      if (!h || !h.length) {
        box.innerHTML = '<div class="cw-pg-card"><div class="cw-empty">判断履歴がまだありません。作業判断画面から判断を記録すると集計されます。</div></div>';
        return;
      }
      var byAction = {}, byLevel = {}, bySite = {}, byMonth = {};
      h.forEach(function (r) {
        byAction[r.action || "不明"] = (byAction[r.action || "不明"] || 0) + 1;
        var lv = CW_LV[r.level] || String(r.level);
        byLevel[lv] = (byLevel[lv] || 0) + 1;
        bySite[r.site || r.siteId] = (bySite[r.site || r.siteId] || 0) + 1;
        var m = String(r.datetime || "").split("/")[0];
        if (m) byMonth[m + "月"] = (byMonth[m + "月"] || 0) + 1;
      });
      var lvColor = { "通常": CW_LVC[0], "注意": CW_LVC[1], "中止検討": CW_LVC[2], "確認不能": CW_LVC[3] };
      box.innerHTML =
        '<div class="cw-pg-card"><h3 style="margin:0 0 4px;font-size:13.5px;color:#13344f">総判断数: '
        + h.length + " 件</h3></div>"
        + '<div class="cw-pg-card"><h3 style="margin:0 0 10px;font-size:13.5px;color:#13344f">判断アクション別</h3>'
        + cwBarRows(byAction) + "</div>"
        + '<div class="cw-pg-card"><h3 style="margin:0 0 10px;font-size:13.5px;color:#13344f">判定レベル別</h3>'
        + cwBarRows(byLevel, function (kk) { return lvColor[kk] || "#16527d"; }) + "</div>"
        + '<div class="cw-pg-card"><h3 style="margin:0 0 10px;font-size:13.5px;color:#13344f">現場別（判断回数）</h3>'
        + cwBarRows(bySite) + "</div>"
        + '<div class="cw-pg-card"><h3 style="margin:0 0 10px;font-size:13.5px;color:#13344f">月別件数</h3>'
        + cwBarRows(byMonth) + "</div>";
    }

    // ---- 河川観測（#29/#31 基盤。自動取得は未接続のため未実装を明示） ----
    function installRiverScreen() {
      var el = cwMakeScreen("cw-river-screen",
        '<div class="cw-pg"><h2>河川観測</h2>'
        + '<p class="sub">河川内・河川近接現場の観測所と実測値。現在はデモ自動取得（シミュレーション）と手動入力で運用中。公式の水防災オープンデータ提供サービスは未接続です。</p>'
        + '<div class="cw-pg-bar"><button type="button" class="cw-btn" id="cw-river-reload">再読込</button>'
        + '<span class="cw-msg" id="cw-river-msg"></span></div>'
        + '<div class="cw-pg-card" style="padding:12px 14px">'
        + '<div id="cw-river-map" style="height:38vh;min-height:280px;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)"></div>'
        + '<div class="cw-wbgt-scale" style="margin:10px 0 0">'
        + '<span style="background:#0e7d8f">上流観測所</span>'
        + '<span style="background:#16527d">最寄り観測所</span>'
        + '<span style="background:#566472">参照観測所</span></div></div>'
        + '<div id="cw-river-body"></div></div>');
      if (!el) return;
      el.querySelector("#cw-river-reload").addEventListener("click", function () { cwRenderRiver(); });
    }
    window.__cwToggleRiverForm = function (sid, stationId) {
      var form = document.getElementById("cw-river-form-" + sid + "-" + stationId);
      if (form) form.style.display = form.style.display === "none" ? "block" : "none";
    };
    window.__cwSubmitRiver = function (sid, stationId) {
      cwSubmitRiverManual(sid, stationId);
    };
    function cwRiverBadge(rel) {
      var m = { upstream: "上流", nearest: "最寄り", reference: "参照" };
      return m[rel] || rel || "";
    }
    function cwRiverVal(obs) {
      if (!obs) return "";
      var parts = [];
      if (obs.waterLevelM != null) parts.push("水位 " + esc(obs.waterLevelM) + " m");
      if (obs.rainfallMmH != null) parts.push("雨量 " + esc(obs.rainfallMmH) + " mm/h");
      return parts.join(" / ");
    }
    var cwRiverMap = null, cwRiverMarkers = null;
    function cwRiverSiteColor(site) {
      if (!site) return "#0e7d8f";
      if (site.riverState === "rising") return "#c62828";
      if (site.riverState === "stale") return "#c2920a";
      return "#0e7d8f";
    }
    function cwBuildRiverMap(rows) {
      var mapEl = document.getElementById("cw-river-map");
      if (!mapEl || !rows || !rows.length) return;
      if (!window.L) { cwEnsureLeaflet(function () { cwBuildRiverMap(rows); }); return; }
      var L = window.L;
      if (!cwRiverMap) {
        cwRiverMap = L.map(mapEl, { scrollWheelZoom: false }).setView([37.5, 137.0], 5);
        var tileUrl = cwTileUrl();
        if (tileUrl) L.tileLayer(tileUrl, { maxZoom: 18, attribution: cwTileAttribution() }).addTo(cwRiverMap);
        cwRiverMarkers = L.layerGroup().addTo(cwRiverMap);
      }
      cwRiverMap.invalidateSize();
      cwRiverMarkers.clearLayers();
      var pts = [];
      rows.forEach(function (row) {
        var site = row.site;
        if (site == null || site.lat == null || site.lon == null) return;
        var st = (row.stations && row.stations.stations) || [];
        var col = cwRiverSiteColor(site);
        var mk = L.circleMarker([site.lat, site.lon], { radius: 10, color: "#fff", weight: 2,
          fillColor: col, fillOpacity: 0.95 });
        var vals = st.map(function (x) {
          return esc(x.name) + ": " + (x.latest ? cwRiverVal(x.latest) : "実測値なし");
        });
        mk.bindPopup("<b>" + esc(site.name) + "</b><br>"
          + '<span style="color:#62727f;font-size:11px">' + esc(site.code || site.id) + "</span><br>"
          + '<span style="color:' + col + ';font-weight:700">' + esc(site.river || "河川データなし") + "</span>"
          + (vals.length ? "<br>" + vals.join("<br>") : ""));
        cwRiverMarkers.addLayer(mk);
        pts.push([site.lat, site.lon]);
        var relColor = { upstream: "#0e7d8f", nearest: "#16527d", reference: "#566472" };
        st.forEach(function (x) {
          if (x.latitude == null || x.longitude == null) return;
          var c2 = relColor[x.rel] || "#566472";
          var sm = L.circleMarker([x.latitude, x.longitude], { radius: 6, color: "#fff", weight: 2,
            fillColor: c2, fillOpacity: 0.95 });
          sm.bindPopup("<b>" + esc(x.name) + "</b><br>"
            + '<span style="font-size:11px;color:#62727f">' + esc(x.rel || "") + " ・ " + esc(x.stationCode || "")
            + (x.kind ? " ・ " + esc(x.kind) : "") + "</span><br>"
            + (x.latest ? cwRiverVal(x.latest) + "（" + esc(x.latest.source || "") + "）" : "実測値なし"));
          cwRiverMarkers.addLayer(sm);
        });
      });
      if (pts.length) { try { cwRiverMap.fitBounds(pts, { padding: [40, 40], maxZoom: 8 }); } catch (e) {} }
      setTimeout(function () { try { cwRiverMap.invalidateSize(); } catch (e) {} }, 150);
    }
    function cwSubmitRiverManual(sid, stationId) {
      var msg = document.getElementById("cw-river-msg");
      var levelEl = document.getElementById("cw-river-lvl-" + sid + "-" + stationId);
      var rainEl = document.getElementById("cw-river-rain-" + sid + "-" + stationId);
      var noteEl = document.getElementById("cw-river-note-" + sid + "-" + stationId);
      var level = levelEl ? parseFloat(levelEl.value) : NaN;
      var rain = rainEl ? parseFloat(rainEl.value) : NaN;
      var body = { station_id: stationId };
      if (!isNaN(level)) body.water_level_m = level;
      if (!isNaN(rain)) body.rainfall_mm_h = rain;
      if (noteEl && noteEl.value) body.note = noteEl.value;
      if (body.water_level_m == null && body.rainfall_mm_h == null) {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "水位または雨量を入力してください"; }
        return;
      }
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "保存中…"; }
      adapter.authedFetch("/api/sites/" + sid + "/river-observations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json(); }).then(function (res) {
        if (res && res.status === "created") {
          if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "実測値を保存しました"; }
          cwRenderRiver();
        } else {
          var detail = (res && res.detail) || "保存できませんでした";
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "保存失敗: " + (Array.isArray(detail) ? detail.map(function (d) { return d.msg; }).join(" / ") : detail); }
        }
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "保存に失敗しました（権限または接続を確認）"; }
      });
    }
    function cwRenderRiver() {
      var box = document.getElementById("cw-river-body");
      if (!box) return;
      var msg = document.getElementById("cw-river-msg");
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "更新中…"; }
      var meta = adapter._state.meta || [];
      var riverSites = meta.filter(function (s) { return s.hasRiver || s.work === "river"; });
      if (!riverSites.length) {
        box.innerHTML = '<div class="cw-pg-card"><div class="cw-empty">河川内・河川近接の現場がありません。</div></div>';
        if (msg) msg.textContent = "";
        return;
      }
      var role = cwUser ? cwUser.role : "";
      var canWrite = role === "admin" || role === "tech_manager";
      Promise.all(riverSites.map(function (s) {
        return Promise.all([
          adapter.authedFetch("/api/sites/" + s.id + "/observation-stations")
            .then(function (r) { return r.json(); }).catch(function () { return null; }),
          adapter.authedFetch("/api/sites/" + s.id + "/river-observations?limit=10")
            .then(function (r) { return r.json(); }).catch(function () { return null; })
        ]).then(function (pair) { return { site: s, stations: pair[0], series: pair[1] }; });
      })).then(function (rows) {
        var autoEnabled = rows.some(function (row) {
          return row.stations && row.stations.automatic;
        });
        var notice = autoEnabled
          ? '<div class="cw-pg-card" style="border-left:4px solid #c2920a">'
            + '<div style="font-weight:800;color:#8a6410">河川観測: デモ自動取得（シミュレーション）稼働中</div>'
            + '<div style="font-size:11.5px;color:#5f7081;margin-top:4px">'
            + '現在の値は DEMO-RIVER によるシミュレーションです。公式の水防災オープンデータ提供サービスは未接続のため、'
            + '退避判断は必ず<a href="https://www.river.go.jp/" target="_blank" rel="noopener noreferrer" style="color:#16527d">川の防災情報（国交省）</a>と現地確認を優先してください。</div></div>'
          : '<div class="cw-pg-card" style="border-left:4px solid #c62828">'
            + '<div style="font-weight:800;color:#c62828">河川水位・雨量の自動取得は未接続です（手動入力のみ）</div>'
            + '<div style="font-size:11.5px;color:#5f7081;margin-top:4px">'
            + '退避判断は必ず<a href="https://www.river.go.jp/" target="_blank" rel="noopener noreferrer" style="color:#16527d">川の防災情報（国交省）</a>と現地確認を優先してください。</div></div>';
        cwBuildRiverMap(rows);
        box.innerHTML = notice + rows.map(function (row) {
          var st = row.stations && row.stations.stations ? row.stations.stations : [];
          var obs = row.series && row.series.observations ? row.series.observations : [];
          var stationHtml = st.length
            ? st.map(function (x) {
                var val = x.latest ? cwRiverVal(x.latest) : "";
                var formId = "cw-river-form-" + esc(row.site.id) + "-" + esc(x.id);
                var formHtml = canWrite
                  ? '<tr><td colspan="5" style="padding:6px">'
                    + '<button type="button" class="cw-btn" style="margin-bottom:4px" onclick="__cwToggleRiverForm(\'' + esc(row.site.id) + "','" + esc(x.id) + '\')">手動登録</button>'
                    + '<div id="' + formId + '" style="display:none;background:#f6f8fa;border:1px solid #e1e7ed;border-radius:8px;padding:10px">'
                    + '<div style="font-size:11.5px;font-weight:700;margin-bottom:6px">手動実測値の登録（' + esc(x.name) + "）</div>"
                    + '<input type="number" step="0.01" id="cw-river-lvl-' + esc(row.site.id) + "-" + esc(x.id)
                    + '" placeholder="水位 (m)" style="width:130px;margin-right:6px;padding:6px;border:1px solid #c9d3db;border-radius:6px">'
                    + '<input type="number" step="0.1" id="cw-river-rain-' + esc(row.site.id) + "-" + esc(x.id)
                    + '" placeholder="雨量 (mm/h)" style="width:140px;margin-right:6px;padding:6px;border:1px solid #c9d3db;border-radius:6px">'
                    + '<input type="text" id="cw-river-note-' + esc(row.site.id) + "-" + esc(x.id)
                    + '" placeholder="備考（任意）" style="width:220px;margin-right:6px;padding:6px;border:1px solid #c9d3db;border-radius:6px">'
                    + '<button type="button" class="cw-btn cw-btn-pri" onclick="__cwSubmitRiver(\'' + esc(row.site.id) + "','" + esc(x.id) + '\')">保存</button>'
                    + "</div></td></tr>"
                  : "";
                return '<tr><td>' + esc(x.name) + "</td><td>" + esc(cwRiverBadge(x.rel)) + "</td><td>"
                  + esc(x.stationCode) + "</td><td>" + esc(x.kind) + "</td><td>"
                  + (x.latest
                    ? '<b style="color:#13344f">' + val + ' <span style="font-weight:400;color:#7e8c99">(' + esc(x.latest.observedAt) + ")</span></b>"
                    : '<span style="color:#c62828">実測値なし</span>')
                  + "</td></tr>" + formHtml;
              }).join("")
            : '<tr><td colspan="5" style="color:#c62828">観測所が紐付いていません（管理APIからの登録が必要）</td></tr>';
          var seriesHtml = obs.length
            ? obs.map(function (o) {
                return '<div style="font-size:12px;padding:2px 0">' + esc(o.observedAt) + " — " + cwRiverVal(o)
                  + ' <span style="color:#7e8c99">[' + esc(o.source) + " " + esc(o.quality) + "]</span></div>";
              }).join("")
            : '<div class="cw-empty">実測値はまだありません。下の「手動登録」または管理APIで入力します。</div>';
          return '<div class="cw-pg-card">'
            + '<h3 style="margin:0 0 6px;font-size:13.5px;color:#13344f">' + esc(row.site.name)
            + ' <span style="font-weight:400;color:#7e8c99">(' + esc(row.site.code || row.site.id) + ")</span></h3>"
            + '<table class="cw-tbl"><thead><tr><th>観測所</th><th>区分</th><th>コード</th><th>種別</th><th>最新値</th></tr></thead><tbody>'
            + stationHtml + "</tbody></table>"
            + '<h4 style="margin:12px 0 4px;font-size:12px;color:#3a4854">直近の実測値（新しい順）</h4>' + seriesHtml
            + "</div>";
        }).join("");
        if (msg) msg.textContent = "";
      }).catch(function () {
        box.innerHTML = '<div class="cw-pg-card" style="border-left:4px solid #c62828">'
          + '<div style="font-weight:800;color:#c62828">河川観測データを取得できませんでした</div>'
          + '<div style="font-size:11.5px;color:#5f7081;margin-top:4px">権限または接続を確認してください。'
          + '退避判断は<a href="https://www.river.go.jp/" target="_blank" rel="noopener noreferrer" style="color:#16527d">川の防災情報（国交省）</a>と現地確認を優先。</div></div>';
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "取得失敗"; }
      });
    }

    // ---- レポート出力（管理） ----
    function installReportsScreen() {
      var el = cwMakeScreen("cw-reports-screen",
        '<div class="cw-pg"><h2>レポート出力</h2>'
        + '<p class="sub">CSV（Excel対応・BOM付き）でダウンロードします。</p>'
        + '<div id="cw-rep-body"></div></div>');
      if (!el) return;
      el.querySelector("#cw-rep-body").addEventListener("click", function (e) {
        var t = e.target;
        var act = t && t.getAttribute && t.getAttribute("data-act");
        if (!act) return;
        var msg = document.getElementById("cw-rep-msg");
        if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "生成中…"; }
        function done(err) {
          if (!msg) return;
          if (err) { msg.style.color = "#c62828"; msg.textContent = err; }
          else { msg.style.color = "#2e7d32"; msg.textContent = "ダウンロードを開始しました"; }
        }
        if (act === "decision-csv") {
          adapter.authedFetch("/api/decision-logs/export.csv").then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.blob();
          }).then(function (b) { cwSaveBlob(b, "decision_logs.csv"); done(); })
            .catch(function () { done("判断履歴CSVの取得に失敗しました"); });
        } else if (act === "sites-csv") {
          var meta = adapter._state.meta || [];
          if (!meta.length) { done("現場データが未読込です"); return; }
          var rows = [["site_id", "code", "name", "loc", "work_type", "project", "manager", "status", "lat", "lon"]];
          meta.forEach(function (s) {
            rows.push([s.id, s.code, s.name, s.loc, s.work, s.project, s.manager, s.status, s.lat, s.lon]);
          });
          cwSaveBlob(new Blob([cwCsvText(rows)], { type: "text/csv" }), "sites.csv");
          done();
        } else if (act === "audit-csv") {
          adapter.auditLogs(1000).then(function (rows) {
            if (!rows || !rows.map) { done("監査ログの取得に失敗しました（権限を確認）"); return; }
            var out = [["id", "timestamp", "user", "action", "message", "site_id"]];
            rows.forEach(function (r) { out.push([r.id, r.timestamp, r.user, r.action, r.message, r.siteId]); });
            cwSaveBlob(new Blob([cwCsvText(out)], { type: "text/csv" }), "audit_logs.csv");
            done();
          }).catch(function () { done("監査ログの取得に失敗しました"); });
        }
      });
    }
    function cwRenderReports() {
      var box = document.getElementById("cw-rep-body");
      if (!box) return;
      var role = cwUser ? cwUser.role : "";
      var isAdminish = role === "admin" || role === "tech_manager";
      function card(title, desc, act, disabled) {
        return '<div class="cw-pg-card" style="display:flex;align-items:center;gap:14px">'
          + '<div style="flex:1"><div style="font-size:13.5px;font-weight:800;color:#13344f">' + title + "</div>"
          + '<div style="font-size:11.5px;color:#7e8c99;margin-top:3px">' + desc + "</div></div>"
          + (disabled
            ? '<span style="font-size:11px;color:#8a99a5">権限なし（admin/技術管理者）</span>'
            : '<button type="button" class="cw-btn cw-btn-pri" data-act="' + act + '">CSVダウンロード</button>')
          + "</div>";
      }
      box.innerHTML =
        card("判断履歴レポート", "全判断履歴（判断ID・現場・種別・レベル・アクション・記録者・日時）", "decision-csv", false)
        + card("現場一覧レポート", "登録現場のマスタ情報（ID・名称・所在地・種別・担当・状態・座標）", "sites-csv", false)
        + card("監査ログレポート", "操作監査の証跡（時刻・ユーザー・操作・内容・現場）最新1000件", "audit-csv", !isAdminish)
        + '<div class="cw-msg" id="cw-rep-msg"></div>';
    }

    // ---- 運用状態（管理） ----
    function installOpsScreen() {
      var el = cwMakeScreen("cw-ops-screen",
        '<div class="cw-pg"><h2>運用状態</h2>'
        + '<p class="sub">本番 systemd service / timer と failed unit の snapshot。表示値はサーバ側 allowlist 済み。</p>'
        + '<div class="cw-pg-bar"><button type="button" class="cw-btn" id="cw-ops-reload">再読込</button>'
        + '<span class="cw-msg" id="cw-ops-msg"></span></div>'
        + '<div id="cw-ops-body"></div></div>');
      if (!el) return;
      el.querySelector("#cw-ops-reload").addEventListener("click", cwLoadOpsStatus);
    }
    function cwOpsBadge(value) {
      var v = String(value == null ? "" : value);
      var ok = /^(ok|active|success|enabled|0)$/i.test(v);
      var warn = /^(warning|inactive|disabled|skipped|unknown)$/i.test(v);
      var color = ok ? "#2e7d32" : warn ? "#c2920a" : "#c62828";
      var bg = ok ? "#e7f3e9" : warn ? "#fdf6e0" : "#fbe8e8";
      return '<span class="cw-badge" style="background:' + bg + ';color:' + color + '">' + esc(v || "-") + "</span>";
    }
    function cwOpsText(value) {
      return esc(value == null || value === "" ? "-" : value);
    }
    function cwOpsMetric(label, value, badge) {
      return '<div class="cw-pg-card" style="flex:1;min-width:170px;margin-bottom:0">'
        + '<div style="font-size:11px;color:#7e8c99;font-weight:700">' + esc(label) + "</div>"
        + '<div style="margin-top:6px;font-size:18px;font-weight:800;color:#13344f">'
        + (badge ? cwOpsBadge(value) : cwOpsText(value)) + "</div></div>";
    }
    function cwLoadOpsStatus() {
      var box = document.getElementById("cw-ops-body");
      var msg = document.getElementById("cw-ops-msg");
      if (!box) return;
      box.innerHTML = '<div class="cw-empty">読込中…</div>';
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "取得中…"; }
      adapter.opsStatusSnapshot().then(function (d) {
        if (!d || d.detail || d.status !== "ok") {
          var detail = (d && d.detail) || "権限/デプロイ状況を確認";
          box.innerHTML = '<div class="cw-empty">運用状態を取得できませんでした（' + esc(detail) + "）</div>";
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "取得失敗"; }
          return;
        }
        cwRenderOpsStatus(d);
        if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "更新しました"; }
      }).catch(function () {
        box.innerHTML = '<div class="cw-empty">取得に失敗しました（通信エラー）</div>';
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラー"; }
      });
    }
    function cwRenderOpsStatus(data) {
      var box = document.getElementById("cw-ops-body");
      if (!box) return;
      var snap = data.snapshot || {};
      var meta = data.metadata || {};
      var services = snap.services || [];
      var timers = snap.timers || [];
      var failed = snap.failed_units || [];
      var failedCount = snap.failed_units_count != null ? snap.failed_units_count : failed.length;
      var serviceRows = services.length ? services.map(function (s) {
        return "<tr><td>" + cwOpsText(s.unit) + "</td><td>" + cwOpsBadge(s.active_state)
          + "</td><td>" + cwOpsBadge(s.result) + '</td><td class="cw-num">' + cwOpsText(s.n_restarts) + "</td></tr>";
      }).join("") : '<tr><td colspan="4"><div class="cw-empty">service snapshot がありません</div></td></tr>';
      var timerRows = timers.length ? timers.map(function (t) {
        return "<tr><td>" + cwOpsText(t.unit) + "</td><td>" + cwOpsBadge(t.active_state)
          + "</td><td>" + cwOpsBadge(t.unit_file_state) + "</td><td>" + cwOpsBadge(t.service_result)
          + "</td><td>" + cwOpsText(t.last_trigger) + "</td><td>" + cwOpsText(t.next_elapse) + "</td></tr>";
      }).join("") : '<tr><td colspan="6"><div class="cw-empty">timer snapshot がありません</div></td></tr>';
      var failedHtml = failedCount > 0
        ? '<div class="cw-pg-card"><h3 style="margin:0 0 8px;font-size:13.5px;color:#c62828">Failed units</h3>'
          + '<div style="font-size:12.5px;line-height:1.9;color:#3a4854">'
          + failed.map(function (u) { return '<span class="cw-badge" style="background:#fbe8e8;color:#c62828;margin:0 6px 6px 0">' + esc(u) + "</span>"; }).join("")
          + "</div></div>"
        : '<div class="cw-pg-card"><div class="cw-empty">failed cwwd unit はありません</div></div>';
      if (snap.error) {
        failedHtml += '<div class="cw-pg-card"><h3 style="margin:0 0 8px;font-size:13.5px;color:#c62828">Snapshot error</h3>'
          + '<table class="cw-tbl"><tbody>'
          + "<tr><th>message</th><td>" + cwOpsText(snap.error.message) + "</td></tr>"
          + "<tr><th>unit</th><td>" + cwOpsText(snap.error.unit) + "</td></tr>"
          + "<tr><th>property</th><td>" + cwOpsText(snap.error.property) + "</td></tr>"
          + "<tr><th>actual</th><td>" + cwOpsText(snap.error.actual) + "</td></tr>"
          + "<tr><th>expected</th><td>" + cwOpsText(snap.error.expected) + "</td></tr>"
          + "</tbody></table></div>";
      }
      box.innerHTML =
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">'
        + cwOpsMetric("snapshot status", snap.status || data.status, true)
        + cwOpsMetric("failed units", failedCount, true)
        + cwOpsMetric("snapshot age", meta.age_seconds != null ? meta.age_seconds + " sec" : "-", false)
        + cwOpsMetric("snapshot utc", snap.snapshot_utc || meta.mtime || "-", false)
        + "</div>"
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(50vh - 90px)">'
        + '<table class="cw-tbl"><thead><tr><th>Service</th><th>Active</th><th>Result</th><th>Restarts</th></tr></thead><tbody>'
        + serviceRows + "</tbody></table></div>"
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(50vh - 70px)">'
        + '<table class="cw-tbl"><thead><tr><th>Timer</th><th>Active</th><th>Enabled</th><th>Service</th><th>Last</th><th>Next</th></tr></thead><tbody>'
        + timerRows + "</tbody></table></div>"
        + failedHtml;
    }

    // ---- 監査ログ（管理） ----
    var cwAuditRows = null;
    function installAuditScreen() {
      var el = cwMakeScreen("cw-audit-screen",
        '<div class="cw-pg"><h2>監査ログ</h2>'
        + '<p class="sub">ログイン・設定変更・判定・判断記録・CSV出力などの操作証跡（最新200件）。</p>'
        + '<div class="cw-pg-bar"><input type="text" id="cw-audit-q" placeholder="絞り込み（ユーザー/操作/内容）"'
        + ' style="flex:1;min-width:200px;padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;'
        + "font:400 13px 'Noto Sans JP',sans-serif\">"
        + '<button type="button" class="cw-btn" id="cw-audit-reload">再読込</button></div>'
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(100vh - 230px)" id="cw-audit-body"></div></div>');
      if (!el) return;
      el.querySelector("#cw-audit-reload").addEventListener("click", cwLoadAudit);
      el.querySelector("#cw-audit-q").addEventListener("input", cwRenderAuditRows);
    }
    function cwLoadAudit() {
      var box = document.getElementById("cw-audit-body");
      if (!box) return;
      box.innerHTML = '<div class="cw-empty">読込中…</div>';
      adapter.auditLogs(200).then(function (rows) {
        if (!rows || !rows.map) {
          box.innerHTML = '<div class="cw-empty">取得できませんでした（権限またはAPIエラー）</div>';
          return;
        }
        cwAuditRows = rows;
        cwRenderAuditRows();
      }).catch(function () {
        box.innerHTML = '<div class="cw-empty">取得に失敗しました（通信エラー）</div>';
      });
    }
    function cwRenderAuditRows() {
      var box = document.getElementById("cw-audit-body");
      if (!box || !cwAuditRows) return;
      var qEl = document.getElementById("cw-audit-q");
      var q = (qEl && qEl.value || "").toLowerCase();
      var rows = !q ? cwAuditRows : cwAuditRows.filter(function (r) {
        return ((r.user || "") + " " + (r.action || "") + " " + (r.message || "") + " " + (r.siteId || ""))
          .toLowerCase().indexOf(q) >= 0;
      });
      if (!rows.length) {
        box.innerHTML = '<div class="cw-empty">該当する監査ログがありません</div>';
        return;
      }
      box.innerHTML = '<table class="cw-tbl"><thead><tr><th>時刻</th><th>ユーザー</th><th>操作</th>'
        + "<th>内容</th><th>現場</th></tr></thead><tbody>"
        + rows.map(function (r) {
          return "<tr><td style=\"white-space:nowrap\">" + esc(r.timestamp) + "</td><td>" + esc(r.user || "—")
            + '</td><td><span class="cw-badge" style="background:#eef2f6;color:#3a4854">' + esc(r.action) + "</span></td><td>"
            + esc(r.message || "") + "</td><td>" + esc(r.siteId || "") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }

    // ---- 設定（管理: ユーザー設定/通知設定/データ保存期間/AI設定 #80連動） ----
    function installAppSettingsScreen() {
      var el = cwMakeScreen("cw-appset-screen",
        '<div class="cw-pg"><h2>設定</h2>'
        + '<p class="sub">アプリ全体の設定（Neon PostgreSQL に保存・変更は監査ログに記録）。</p>'

        + '<div class="cw-pg-card"><h3 style="margin:0 0 8px;font-size:13.5px;color:#13344f">👤 ユーザー設定</h3>'
        + '<div id="cw-as-user" style="font-size:12.5px;color:#3a4854"></div>'
        + '<div style="font-size:11px;color:#7e8c99;margin-top:6px">ユーザーの追加・ロール変更は今後のリリースで対応予定です。</div></div>'

        + '<div class="cw-pg-card"><h3 style="margin:0 0 8px;font-size:13.5px;color:#13344f">🔔 通知設定</h3>'
        + '<div class="cw-as-row"><label>Slack 通知</label><input type="checkbox" id="cw-as-slack"></div>'
        + '<div class="cw-as-row"><label>Teams 通知</label><input type="checkbox" id="cw-as-teams"></div>'
        + '<div style="font-size:11px;color:#7e8c99">Webhook URL はサーバ側環境変数（SLACK_WEBHOOK_URL / TEAMS_WEBHOOK_URL）で設定します。</div></div>'

        + '<div class="cw-pg-card"><h3 style="margin:0 0 8px;font-size:13.5px;color:#13344f">🗄️ データ保存期間</h3>'
        + '<div class="cw-as-row"><label>保存期間（日）</label>'
        + '<input type="number" id="cw-as-days" min="30" max="3650" step="1" style="width:110px"> '
        + '<span style="font-size:11px;color:#7e8c99">30〜3650日。判定結果・監査ログ等の保持期間（クリーンアップジョブは今後実装）。</span></div></div>'

        + '<div class="cw-pg-card"><h3 style="margin:0 0 8px;font-size:13.5px;color:#13344f">🤖 AI設定（DeepSeek API）</h3>'
        + '<div class="cw-as-row"><label>状態</label><span id="cw-as-ai-status" style="font-weight:700">—</span></div>'
        + '<div class="cw-as-row"><label>プロバイダ</label>'
        + '<select id="cw-as-ai-provider" style="padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px \'Noto Sans JP\',sans-serif">'
        + '<option value="deepseek">DeepSeek（既定）</option>'
        + '<option value="anthropic">Anthropic（Claude）</option></select></div>'
        + '<div class="cw-as-row"><label>APIキー</label>'
        + '<input type="password" id="cw-as-ai-key" class="cw-as-key" placeholder="sk-..." autocomplete="off"></div>'
        + '<div class="cw-as-row" style="justify-content:flex-end;gap:8px">'
        + '<button type="button" class="cw-btn" id="cw-as-ai-test">API接続テスト</button>'
        + '<button type="button" class="cw-btn cw-btn-pri" id="cw-as-ai-save">API設定保存</button>'
        + '<button type="button" class="cw-btn cw-btn-danger" id="cw-as-ai-del">API接続解除</button></div>'
        + '<div class="cw-msg" id="cw-as-ai-msg"></div>'
        + '<div style="font-size:11px;color:#7e8c99">キーは暗号化してDBに保存され、画面には末尾4桁のみ表示されます。'
        + '判定結果の現場向け文章生成（#68）で使用予定。接続テストは DeepSeek（既定）または Anthropic の /models へ疎通します。</div></div>'

        + '<div class="cw-pg-bar" style="justify-content:flex-end">'
        + '<span class="cw-msg" id="cw-as-msg" style="flex:1"></span>'
        + '<button type="button" class="cw-btn" id="cw-as-reload">再読込</button>'
        + '<button type="button" class="cw-btn cw-btn-pri" id="cw-as-save">設定を保存</button></div></div>');
      if (!el) return;
      el.querySelector("#cw-as-reload").addEventListener("click", cwLoadAppSettings);
      el.querySelector("#cw-as-save").addEventListener("click", cwSaveAppSettingsGeneral);
      el.querySelector("#cw-as-ai-test").addEventListener("click", cwAiTest);
      el.querySelector("#cw-as-ai-save").addEventListener("click", cwAiSave);
      el.querySelector("#cw-as-ai-del").addEventListener("click", cwAiDisconnect);
    }
    function cwSetAiStatus(ai) {
      var st = document.getElementById("cw-as-ai-status");
      if (!st) return;
      var providerLabel = ai && ai.provider === "anthropic" ? "Anthropic（Claude）" : "DeepSeek";
      if (ai && ai.configured) {
        st.textContent = "接続設定済み（" + providerLabel + " ・ " + (ai.masked || "****") + "）";
        st.style.color = "#2e7d32";
      } else {
        st.textContent = "未設定（" + providerLabel + "）";
        st.style.color = "#8a99a5";
      }
      var sel = document.getElementById("cw-as-ai-provider");
      if (sel && ai && ai.provider) sel.value = ai.provider;
    }
    function cwLoadAppSettings() {
      var u = document.getElementById("cw-as-user");
      if (u && cwUser) {
        u.textContent = (cwUser.displayName || cwUser.username || "") + "（" + roleLabel(cwUser.role) + "）"
          + (cwUser.department ? " ・ " + cwUser.department : "");
      }
      var msg = document.getElementById("cw-as-msg");
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "読込中…"; }
      adapter.appSettings().then(function (d) {
        if (!d || d.detail) {
          if (msg) {
            msg.style.color = "#c62828";
            msg.textContent = "設定APIを利用できません（" + ((d && d.detail) || "権限/デプロイ状況を確認") + "）";
          }
          return;
        }
        var slack = document.getElementById("cw-as-slack");
        var teams = document.getElementById("cw-as-teams");
        var days = document.getElementById("cw-as-days");
        if (slack) slack.checked = !!(d.notify && d.notify.slack_enabled);
        if (teams) teams.checked = !!(d.notify && d.notify.teams_enabled);
        if (days) days.value = d.data_retention_days != null ? d.data_retention_days : 365;
        cwSetAiStatus(d.ai);
        if (msg) msg.textContent = "";
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "設定の取得に失敗しました（通信エラー）"; }
      });
    }
    function cwSaveAppSettingsGeneral() {
      var msg = document.getElementById("cw-as-msg");
      var days = parseInt(document.getElementById("cw-as-days").value, 10);
      // PUT /api/admin/settings は nested 形式（PR #82 の SettingsUpdate: extra=forbid）
      var updates = {
        notify: {
          slack_enabled: !!document.getElementById("cw-as-slack").checked,
          teams_enabled: !!document.getElementById("cw-as-teams").checked,
        },
      };
      if (!isNaN(days)) updates.data_retention_days = days;
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "保存中…"; }
      adapter.saveAppSettings(updates).then(function (res) {
        if (res.ok) {
          if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "保存しました（監査ログに記録）"; }
          if (res.body && res.body.ai) cwSetAiStatus(res.body.ai);
        } else {
          var detail = (res.body && res.body.detail) || ("HTTP " + res.status);
          if (msg) {
            msg.style.color = "#c62828";
            msg.textContent = "保存できません: " + (Array.isArray(detail)
              ? detail.map(function (x) { return x.msg; }).join(" / ") : detail);
          }
        }
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラーで保存できませんでした"; }
      });
    }
    function cwAiTest() {
      var msg = document.getElementById("cw-as-ai-msg");
      var key = document.getElementById("cw-as-ai-key").value.trim();
      var provider = document.getElementById("cw-as-ai-provider");
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "接続テスト中…"; }
      var body = { provider: provider ? provider.value : "deepseek" };
      if (key) body.api_key = key;
      adapter.aiTest(key || undefined, body).then(function (res) {
        var b = res.body || {};
        if (res.ok && b.ok) {
          if (msg) {
            msg.style.color = "#2e7d32";
            msg.textContent = "接続OK" + (b.models && b.models.length ? "（利用可能: " + b.models.join(", ") + "）" : "");
          }
        } else if (msg) {
          msg.style.color = "#c62828";
          msg.textContent = "接続NG: " + (b.error || (b.detail || ("HTTP " + res.status)));
        }
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "接続テストに失敗しました（通信エラー）"; }
      });
    }
    function cwAiSave() {
      var msg = document.getElementById("cw-as-ai-msg");
      var input = document.getElementById("cw-as-ai-key");
      var key = input.value.trim();
      var provider = document.getElementById("cw-as-ai-provider");
      if (!key) {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "APIキーを入力してください"; }
        return;
      }
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "保存中…"; }
      adapter.saveAppSettings({ ai_api_key: key,
        ai_provider: provider ? provider.value : "deepseek" }).then(function (res) {
        if (res.ok) {
          input.value = "";
          if (res.body && res.body.ai) cwSetAiStatus(res.body.ai);
          if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "APIキーを保存しました（暗号化・監査記録）"; }
        } else {
          var detail = (res.body && res.body.detail) || ("HTTP " + res.status);
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "保存できません: " + detail; }
        }
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラーで保存できませんでした"; }
      });
    }
    function cwAiDisconnect() {
      if (!window.confirm("AI APIキーを削除（接続解除）しますか？")) return;
      var msg = document.getElementById("cw-as-ai-msg");
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "解除中…"; }
      adapter.aiDisconnect().then(function (res) {
        if (res.ok) {
          cwSetAiStatus(res.body && res.body.ai);
          if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "接続を解除しました"; }
        } else if (msg) {
          msg.style.color = "#c62828";
          msg.textContent = "解除できません: " + ((res.body && res.body.detail) || ("HTTP " + res.status));
        }
      }).catch(function () {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラーで解除できませんでした"; }
      });
    }

    // ---- 海象データ：全国版（#72 段5: 正式画面化） ----
    function cwMarineMeta(v) {
      if (v == null) return { label: "欠測", color: "#5a6b7b" };
      if (v < 1.0) return { label: "穏やか", color: "#2e7d32" };
      if (v < 2.0) return { label: "注意", color: "#e07d12" };
      return { label: "高波", color: "#c62828" };
    }
    function installMarineScreen() {
      var el = cwMakeScreen("cw-marine-screen",
        '<div class="cw-pg"><h2>海象データ：全国版</h2>'
        + '<p class="sub">全登録現場の波高・周期・波向・うねり概況（Open-Meteo Marine API 補完）。'
        + 'NOWPHAS・気象庁潮位は利用条件確認後に接続予定のため、潮位は公式サイトで確認してください。</p>'
        + '<div class="cw-pg-bar"><button type="button" class="cw-btn" id="cw-marine-reload">再読込</button>'
        + '<span class="cw-msg" id="cw-marine-msg"></span></div>'
        + '<div class="cw-pg-card" style="padding:12px 14px">'
        + '<div id="cw-marine-map" style="height:44vh;min-height:300px;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)"></div>'
        + '<div class="cw-wbgt-scale" style="margin:10px 0 0">'
        + '<span style="background:#2e7d32">~1.0m 穏やか</span>'
        + '<span style="background:#e07d12">1.0-2.0m 注意</span>'
        + '<span style="background:#c62828">2.0m~ 高波</span>'
        + '<span style="background:#5a6b7b">欠測</span></div></div>'
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(100vh - 300px)" id="cw-marine-body"></div>'
        + '<div class="cw-pg-card" style="border-left:4px solid #c2920a;font-size:11.5px;color:#5f7081;line-height:1.8">'
        + '<b style="color:#8a6410">データソースの状況（実態を明示）</b><br>'
        + '波浪・うねり: Open-Meteo Marine API（補完・検証中）　／　潮位: 気象庁潮位表・観測情報（未接続）　／　'
        + 'NOWPHAS: 利用条件確認中。<a href="https://www.mlit.go.jp/kowan/nowphas/index.html" target="_blank" rel="noopener noreferrer" style="color:#16527d">NOWPHAS（国交省）</a>・'
        + '<a href="https://www.data.jma.go.jp/gmd/kaiyou/db/tide/suisan/suisan.php" target="_blank" rel="noopener noreferrer" style="color:#16527d">気象庁 潮位表</a>を参照してください。</div></div>');
      if (!el) return;
      el.querySelector("#cw-marine-reload").addEventListener("click", cwRenderMarine);
    }
    function cwRenderMarine() {
      var box = document.getElementById("cw-marine-body");
      var mapEl = document.getElementById("cw-marine-map");
      var msg = document.getElementById("cw-marine-msg");
      if (!box) return;
      box.innerHTML = '<div class="cw-empty">読込中…</div>';
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "取得中…"; }
      adapter.marineNational().then(function (d) {
        if (!d || !d.sites) {
          var detail = (d && d.detail) || "APIエラー（権限または接続を確認）";
          box.innerHTML = '<div class="cw-empty" style="color:#c62828">海象データを取得できませんでした: ' + esc(detail) + "</div>";
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "取得失敗"; }
          return;
        }
        var rows = d.sites;
        box.innerHTML = '<table class="cw-tbl"><thead><tr>'
          + "<th>現場</th><th>有義波高</th><th>周期</th><th>波向</th><th>うねり</th>"
          + "<th>海上風</th><th>突風</th><th>判定</th><th>潮位</th><th>更新</th></tr></thead><tbody>"
          + rows.map(function (s) {
            var m = cwMarineMeta(s.waveHeight);
            var lvOk = s.level != null && CW_LV[s.level] != null;
            return "<tr><td><b>" + esc(s.name) + '</b><div style="font-size:10.5px;color:#7e8c99">' + esc(s.loc || "") + "</div></td>"
              + '<td class="cw-num"><b style="color:' + m.color + '">' + (s.waveHeight == null ? "—" : esc(s.waveHeight)) + "</b> "
              + '<span style="font-size:10.5px;color:' + m.color + '">' + m.label + "</span></td>"
              + '<td class="cw-num">' + (s.wavePeriod == null ? "—" : esc(s.wavePeriod) + " s") + "</td>"
              + '<td class="cw-num">' + (s.waveDirection == null ? "—" : esc(s.waveDirection) + "°") + "</td>"
              + '<td class="cw-num">' + (s.swellHeight == null ? "—" : esc(s.swellHeight) + " m") + "</td>"
              + '<td class="cw-num">' + (s.windMax == null ? "—" : esc(s.windMax) + " m/s") + "</td>"
              + '<td class="cw-num">' + (s.gust == null ? "—" : esc(s.gust) + " m/s") + "</td>"
              + "<td>" + (lvOk ? '<span class="cw-badge" style="background:' + CW_LVC[s.level] + '18;color:' + CW_LVC[s.level] + '">' + CW_LV[s.level] + "</span>" : "—") + "</td>"
              + '<td><span style="color:#8a99a5">未接続</span></td>'
              + '<td class="cw-num" style="white-space:nowrap">' + esc(s.updated || "") + "</td></tr>";
          }).join("") + "</tbody></table>";
        if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "更新しました（" + rows.length + "現場）"; }
        marineMapVer = -1; // 再取得時はマーカーも最新データで再構築
        cwBuildMarineMap(mapEl, rows);
      }).catch(function () {
        box.innerHTML = '<div class="cw-empty" style="color:#c62828">海象データの取得に失敗しました（通信エラー）</div>';
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラー"; }
      });
    }
    var marineMap = null, marineMarkers = null, marineMapVer = -1;
    function cwBuildMarineMap(mapEl, rows) {
      if (!mapEl) return;
      if (!window.L) {
        cwEnsureLeaflet(function () { cwBuildMarineMap(mapEl, rows); });
        return;
      }
      var L = window.L;
      if (!marineMap) {
        marineMap = L.map(mapEl, { scrollWheelZoom: false }).setView([37.5, 137.0], 4);
        var tileUrl = cwTileUrl();
        if (tileUrl) L.tileLayer(tileUrl, { maxZoom: 18, attribution: cwTileAttribution() }).addTo(marineMap);
        marineMarkers = L.layerGroup().addTo(marineMap);
      }
      marineMap.invalidateSize();
      if (marineMapVer !== adapter._state.ver || marineMapVer === -1) {
        marineMapVer = adapter._state.ver;
        marineMarkers.clearLayers();
        var pts = [];
        rows.forEach(function (s) {
          if (s.lat == null || s.lon == null) return;
          var m = cwMarineMeta(s.waveHeight);
          var mk = L.circleMarker([s.lat, s.lon], { radius: 9, color: "#fff", weight: 2, fillColor: m.color, fillOpacity: 0.95 });
          var detail = [];
          if (s.wavePeriod != null) detail.push("周期 " + esc(s.wavePeriod) + " s");
          if (s.waveDirection != null) detail.push("波向 " + esc(s.waveDirection) + "°");
          if (s.swellHeight != null) detail.push("うねり " + esc(s.swellHeight) + " m");
          if (s.swellPeriod != null) detail.push("うねり周期 " + esc(s.swellPeriod) + " s");
          if (s.windMax != null) detail.push("海上風 " + esc(s.windMax) + " m/s");
          if (s.gust != null) detail.push("突風 " + esc(s.gust) + " m/s");
          if (s.seaTemp != null) detail.push("水温 " + esc(s.seaTemp) + "℃");
          mk.bindPopup("<b>" + esc(s.name) + "</b><br>有義波高 "
            + (s.waveHeight == null ? "—" : esc(s.waveHeight) + " m") + " ・ 周期 "
            + (s.wavePeriod == null ? "—" : esc(s.wavePeriod) + " s")
            + '<br><b style="color:' + m.color + '">' + m.label + "</b>"
            + (detail.length ? "<br>" + detail.join(" ・ ") : "")
            + '<br><span style="font-size:10.5px;color:#8a99a5">潮位: 未接続（気象庁潮位表を参照）</span>');
          marineMarkers.addLayer(mk); pts.push([s.lat, s.lon]);
        });
        if (pts.length) { try { marineMap.fitBounds(pts, { padding: [40, 40], maxZoom: 7 }); } catch (e) {} }
      }
      setTimeout(function () { try { marineMap.invalidateSize(); } catch (e) {} }, 150);
    }

    // ---- 海上作業判定（#72 段5: 正式画面化） ----
    var cwMarineWorkResult = null;
    var cwMworkMap = null, cwMworkMapSite = null;
    function installMarineWorkScreen() {
      var el = cwMakeScreen("cw-mwork-screen",
        '<div class="cw-pg"><h2>海上作業判定</h2>'
        + '<p class="sub">有義波高・うねり・海上風・突風・濃霧の閾値判定。最終判断は現場責任者（公式情報・現地確認を優先）。</p>'
        + '<div class="cw-pg-card"><h3 style="margin:0 0 10px;font-size:13.5px;color:#13344f">作業条件</h3>'
        + '<div class="cw-as-row"><label>現場</label><select id="cw-mwork-site" style="flex:1;min-width:240px;padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px \'Noto Sans JP\',sans-serif"></select></div>'
        + '<div class="cw-as-row"><label>開始</label><input type="datetime-local" id="cw-mwork-start" style="flex:1;min-width:220px;padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px \'Noto Sans JP\',sans-serif"></div>'
        + '<div class="cw-as-row"><label>終了</label><input type="datetime-local" id="cw-mwork-end" style="flex:1;min-width:220px;padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px \'Noto Sans JP\',sans-serif"></div>'
        + '<div class="cw-pg-bar" style="margin:8px 0 0;justify-content:flex-end">'
        + '<span class="cw-msg" id="cw-mwork-msg" style="flex:1"></span>'
        + '<button type="button" class="cw-btn cw-btn-pri" id="cw-mwork-run">海象データで評価する</button></div></div>'
        + '<div class="cw-pg-card" style="padding:12px 14px"><div id="cw-mwork-map" style="height:260px;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)"></div></div>'
        + '<div id="cw-mwork-result"></div>'
        + '<div class="cw-pg-card" style="border-left:4px solid #c2920a;font-size:11.5px;color:#5f7081;line-height:1.8">'
        + '波浪データは Open-Meteo Marine API（補完・検証中）。視程は実測未接続のため濃霧予報（気象コード45/48）のみ判定し、'
        + 'それ以外の視程は現地確認で補完してください。潮位判定はデータソース確定後に追加予定（要件書追補で管理）。</div></div>');
      if (!el) return;
      el.querySelector("#cw-mwork-run").addEventListener("click", cwRunMarineWork);
    }
    function cwMarineWorkSiteOptions() {
      var sel = document.getElementById("cw-mwork-site");
      if (!sel) return;
      var meta = adapter._state.meta || [];
      if (!meta.length) {
        adapter.loadDashboard().then(function () {
          meta = adapter._state.meta || [];
          sel.innerHTML = meta.map(function (s) {
            return '<option value="' + esc(s.id) + '">' + esc(s.name) + "（" + esc(s.loc || "") + "）</option>";
          }).join("");
        }).catch(function () {});
        return;
      }
      sel.innerHTML = meta.map(function (s) {
        return '<option value="' + esc(s.id) + '">' + esc(s.name) + "（" + esc(s.loc || "") + "）</option>";
      }).join("");
    }
    function cwRenderMarineWork() {
      cwMarineWorkSiteOptions();
      var sel = document.getElementById("cw-mwork-site");
      if (sel && cwMarineWorkResult && cwMarineWorkResult.siteId === sel.value) {
        cwRenderMarineWorkResult(cwMarineWorkResult.body);
      }
    }
    function cwRunMarineWork() {
      var msg = document.getElementById("cw-mwork-msg");
      var sel = document.getElementById("cw-mwork-site");
      if (!sel || !sel.value) {
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "現場を選択してください"; }
        return;
      }
      var start = document.getElementById("cw-mwork-start");
      var end = document.getElementById("cw-mwork-end");
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "評価中…"; }
      adapter.evaluateMarine(sel.value, start && start.value || null, end && end.value || null)
        .then(function (res) {
          if (res.ok && res.body) {
            cwMarineWorkResult = { siteId: sel.value, body: res.body };
            cwRenderMarineWorkResult(res.body);
            if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "評価しました"; }
          } else {
            var detail = (res.body && (res.body.detail || res.body.error)) || ("HTTP " + res.status);
            if (msg) { msg.style.color = "#c62828"; msg.textContent = "評価失敗: " + (Array.isArray(detail) ? detail.map(function (x) { return x.msg; }).join(" / ") : detail); }
          }
        }).catch(function () {
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラー（バックエンド未起動の可能性）"; }
        });
    }
    function cwRenderMarineWorkResult(body) {
      var box = document.getElementById("cw-mwork-result");
      var mapEl = document.getElementById("cw-mwork-map");
      if (!box || !body) return;
      var lvOk = body.overall_level != null && CW_LVC[body.overall_level] != null;
      var color = lvOk ? CW_LVC[body.overall_level] : "#5a6b7b";
      var reasons = body.reasons || [];
      var chips = [];
      if (body.waveHeight != null) chips.push("有義波高 " + esc(body.waveHeight) + " m");
      if (body.wavePeriod != null) chips.push("周期 " + esc(body.wavePeriod) + " s");
      if (body.swellHeight != null) chips.push("うねり " + esc(body.swellHeight) + " m");
      if (body.windMax != null) chips.push("海上風 " + esc(body.windMax) + " m/s");
      if (body.gust != null) chips.push("突風 " + esc(body.gust) + " m/s");
      box.innerHTML =
        '<div class="cw-pg-card" style="border-top:6px solid ' + color + '">'
        + '<div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;flex-wrap:wrap">'
        + '<div style="flex:none;text-align:center;background:' + color + ';color:#fff;border-radius:11px;padding:10px 16px;min-width:110px">'
        + '<div style="font-size:10px;font-weight:700;opacity:.85">全体レベル</div>'
        + '<div style="font-size:22px;font-weight:900;line-height:1.2">' + esc(body.overall_label || CW_LV[body.overall_level] || "—") + "</div></div>"
        + '<div style="font-size:13px;color:#3a4854;line-height:1.7;flex:1;min-width:240px">' + esc(body.summary || "") + "</div></div>"
        + '<div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px">'
        + chips.map(function (c) { return '<span style="font-size:11px;color:#13344f;background:#eef4f9;border:1px solid #d5e2ec;padding:4px 9px;border-radius:6px">' + c + "</span>"; }).join("")
        + "</div>"
        + '<div style="font-size:12.5px;font-weight:800;color:#566472;margin-bottom:8px">判定理由</div>'
        + (reasons.length ? reasons.map(function (r) {
          var sev = r.severity == null ? 1 : r.severity;
          var c = sev === 3 ? "#5a6b7b" : sev === 2 ? "#c62828" : sev === 1 ? "#e07d12" : "#2e7d32";
          return '<div style="display:flex;align-items:flex-start;gap:10px;padding:9px 12px;background:#f6f8fa;border:1px solid #e2e8ee;border-radius:9px;margin-bottom:7px">'
            + '<span style="flex:none;font-size:10px;font-weight:900;color:#fff;background:' + c + ';border-radius:6px;padding:4px 7px">'
            + (sev === 3 ? "確認不能" : sev === 2 ? "中止検討" : sev === 1 ? "注意" : "通常") + "</span>"
            + '<div style="font-size:12px;color:#2c3a46;line-height:1.5">' + esc(r.text || r.message || "")
            + (r.source ? '<div style="font-size:10.5px;color:#8693a0;margin-top:2px">' + esc(r.source) + " ・ " + esc(r.value || "") + "</div>" : "")
            + "</div></div>";
        }).join("") : '<div class="cw-empty">判定理由がありません</div>')
        + '<div style="font-size:11px;color:#7e8c99;margin-top:8px">' + esc(body.data_quality_summary || "") + "</div></div>";
      // 対象現場の地図
      if (!mapEl) return;
      var meta = adapter._state.meta || [];
      var site = null;
      for (var i = 0; i < meta.length; i++) { if (meta[i].id === cwMarineWorkResult.siteId) site = meta[i]; }
      if (!window.L) { cwEnsureLeaflet(function () { cwRenderMarineWorkResult(body); }); return; }
      var L = window.L;
      var map = cwMworkMap;
      if (!map || !document.body.contains(map._container) || map._container !== mapEl) {
        if (map) { try { map.remove(); } catch (e) {} }
        map = L.map(mapEl, { scrollWheelZoom: false }).setView([37.5, 137.0], 4);
        var tileUrl = cwTileUrl();
        if (tileUrl) L.tileLayer(tileUrl, { maxZoom: 18, attribution: cwTileAttribution() }).addTo(map);
        cwMworkMap = map;
      }
      if (site && site.lat != null && site.lon != null) {
        var needMarker = cwMworkMapSite !== site.id;
        if (needMarker) {
          map.eachLayer(function (layer) { if (layer instanceof L.CircleMarker) map.removeLayer(layer); });
          L.circleMarker([site.lat, site.lon], { radius: 10, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.95 })
            .addTo(map).bindPopup("<b>" + esc(site.name) + "</b><br>海上作業判定: <b style=\"color:" + color + "\">"
              + esc(body.overall_label || "") + "</b>");
          cwMworkMapSite = site.id;
        }
        map.setView([site.lat, site.lon], 8);
      }
      map.invalidateSize();
      setTimeout(function () { try { map.invalidateSize(); } catch (e) {} }, 150);
    }

    // ---- 50年確率波（#72 段7: 極値統計のデモ実装） ----
    function cwWave50Meta(h) {
      if (h == null) return { label: "欠測", color: "#5a6b7b" };
      if (h < 3.0) return { label: "3m未満", color: "#2e7d32" };
      if (h < 5.0) return { label: "3-5m", color: "#c2920a" };
      return { label: "5m以上", color: "#c62828" };
    }
    function installWave50Screen() {
      var el = cwMakeScreen("cw-wave50-screen",
        '<div class="cw-pg"><h2>50年確率波</h2>'
        + '<p class="sub">Gumbel / Weibull 極値統計による再現期間波高（50年・100年）の解析。'
        + '現在はシミュレーションデータ（DEMO-EXTREME）のため、設計条件の決定には使用できません。</p>'
        + '<div class="cw-pg-bar"><button type="button" class="cw-btn" id="cw-wave50-reload">再解析</button>'
        + '<span class="cw-msg" id="cw-wave50-msg"></span></div>'
        + '<div class="cw-pg-card" style="padding:12px 14px">'
        + '<div id="cw-wave50-map" style="height:42vh;min-height:300px;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)"></div>'
        + '<div class="cw-wbgt-scale" style="margin:10px 0 0">'
        + '<span style="background:#2e7d32">~3m</span><span style="background:#c2920a">3-5m</span>'
        + '<span style="background:#c62828">5m~</span><span style="background:#5a6b7b">欠測</span></div></div>'
        + '<div class="cw-pg-card" style="padding:0;overflow:auto;max-height:calc(100vh - 320px)" id="cw-wave50-body"></div>'
        + '<div class="cw-pg-card" style="border-left:4px solid #c2920a;font-size:11.5px;color:#5f7081;line-height:1.8">'
        + '<b style="color:#8a6410">解析手法（デモ）</b><br>'
        + '・Gumbel 分布: 位置母数 loc / 尺度母数 scale をモーメント法で推定<br>'
        + '・Weibull 分布: 2母数（形状・尺度）を確率プロットの線形回帰で推定<br>'
        + '・標本年数30年（地点IDから決定的に生成した年最大波高）<br>'
        + '・NOWPHAS 長期観測データの蓄積（Neon PostgreSQL）後に実測ベースへ切替予定（Issue #72 段7）</div></div>');
      if (!el) return;
      el.querySelector("#cw-wave50-reload").addEventListener("click", cwRenderWave50);
    }
    var cwWave50Map = null, cwWave50Markers = null;
    function cwRenderWave50() {
      var box = document.getElementById("cw-wave50-body");
      var mapEl = document.getElementById("cw-wave50-map");
      var msg = document.getElementById("cw-wave50-msg");
      if (!box) return;
      box.innerHTML = '<div class="cw-empty">解析中…</div>';
      if (msg) { msg.style.color = "#5a6b7b"; msg.textContent = "極値解析を実行中…"; }
      adapter.returnPeriods().then(function (d) {
        if (!d || !d.sites) {
          var detail = (d && d.detail) || "APIエラー（権限または接続を確認）";
          box.innerHTML = '<div class="cw-empty" style="color:#c62828">50年確率波を取得できませんでした: ' + esc(detail) + "</div>";
          if (msg) { msg.style.color = "#c62828"; msg.textContent = "取得失敗"; }
          return;
        }
        var rows = d.sites;
        box.innerHTML = '<table class="cw-tbl"><thead><tr>'
          + "<th>現場</th><th>H50 (m)</th><th>H100 (m)</th><th>採用手法</th><th>標本</th>"
          + "<th>Gumbel (loc/scale)</th><th>Weibull (shape/scale)</th></tr></thead><tbody>"
          + rows.map(function (s) {
            var m = cwWave50Meta(s.h50);
            var g = s.methods && s.methods.gumbel;
            var w = s.methods && s.methods.weibull;
            return "<tr><td><b>" + esc(s.name) + '</b><div style="font-size:10.5px;color:#7e8c99">' + esc(s.loc || "") + "</div></td>"
              + '<td class="cw-num"><b style="color:' + m.color + '">' + (s.h50 == null ? "—" : esc(s.h50)) + "</b> "
              + '<span style="font-size:10.5px;color:' + m.color + '">' + m.label + "</span></td>"
              + '<td class="cw-num">' + (s.h100 == null ? "—" : esc(s.h100)) + "</td>"
              + '<td>' + esc(s.primaryMethod || "—") + "</td>"
              + '<td class="cw-num">' + esc(s.sampleYears || "—") + "年</td>"
              + '<td class="cw-num">' + (g ? esc(g.loc) + " / " + esc(g.scale) : "—") + "</td>"
              + '<td class="cw-num">' + (w ? esc(w.shape) + " / " + esc(w.scale) : "—") + "</td></tr>";
          }).join("") + "</tbody></table>"
          + '<div class="cw-pg-card" style="margin-top:12px;border-left:4px solid #c62828">'
          + '<div style="font-weight:800;color:#c62828;font-size:12px">注意（デモ）</div>'
          + '<div style="font-size:11.5px;color:#5f7081;margin-top:4px">' + esc(d.note || "") + "</div></div>";
        if (msg) { msg.style.color = "#2e7d32"; msg.textContent = "解析しました（" + rows.length + "地点）"; }
        cwBuildWave50Map(mapEl, rows);
      }).catch(function () {
        box.innerHTML = '<div class="cw-empty" style="color:#c62828">50年確率波の取得に失敗しました（通信エラー）</div>';
        if (msg) { msg.style.color = "#c62828"; msg.textContent = "通信エラー"; }
      });
    }
    function cwBuildWave50Map(mapEl, rows) {
      if (!mapEl) return;
      if (!window.L) { cwEnsureLeaflet(function () { cwBuildWave50Map(mapEl, rows); }); return; }
      var L = window.L;
      if (!cwWave50Map) {
        cwWave50Map = L.map(mapEl, { scrollWheelZoom: false }).setView([37.5, 137.0], 4);
        var tileUrl = cwTileUrl();
        if (tileUrl) L.tileLayer(tileUrl, { maxZoom: 18, attribution: cwTileAttribution() }).addTo(cwWave50Map);
        cwWave50Markers = L.layerGroup().addTo(cwWave50Map);
      }
      cwWave50Map.invalidateSize();
      cwWave50Markers.clearLayers();
      var pts = [];
      rows.forEach(function (s) {
        if (s.latitude == null || s.longitude == null) return;
        var m = cwWave50Meta(s.h50);
        var mk = L.circleMarker([s.latitude, s.longitude], { radius: 9, color: "#fff", weight: 2, fillColor: m.color, fillOpacity: 0.95 });
        var g = s.methods && s.methods.gumbel;
        var w = s.methods && s.methods.weibull;
        mk.bindPopup("<b>" + esc(s.name) + "</b><br>H50 <b style=\"color:" + m.color + "\">"
          + (s.h50 == null ? "—" : esc(s.h50)) + " m</b> ・ H100 "
          + (s.h100 == null ? "—" : esc(s.h100)) + " m<br>"
          + "手法 " + esc(s.primaryMethod || "—") + " ・ 標本 " + esc(s.sampleYears || "—") + "年<br>"
          + (g ? "Gumbel: loc " + esc(g.loc) + " / scale " + esc(g.scale) + "<br>" : "")
          + (w ? "Weibull: shape " + esc(w.shape) + " / scale " + esc(w.scale) : "")
          + '<br><span style="font-size:10.5px;color:#8a99a5">デモ・シミュレーション（設計不可）</span>');
        cwWave50Markers.addLayer(mk);
        pts.push([s.latitude, s.longitude]);
      });
      if (pts.length) { try { cwWave50Map.fitBounds(pts, { padding: [40, 40], maxZoom: 6 }); } catch (e) {} }
      setTimeout(function () { try { cwWave50Map.invalidateSize(); } catch (e) {} }, 150);
    }

    // 画面レジストリ登録（cwSyncScreens が参照）
    CW_SCREENS["sites-list"] = { id: "cw-sites-screen", show: cwRenderSites, live: true };
    CW_SCREENS["wx-national"] = { id: "cw-wx-screen", show: cwRenderWx, live: true };
    CW_SCREENS["river-obs"] = { id: "cw-river-screen", show: cwRenderRiver, live: true };
    CW_SCREENS["marine-national"] = { id: "cw-marine-screen", show: cwRenderMarine, live: true };
    CW_SCREENS["marine-work"] = { id: "cw-mwork-screen", show: cwRenderMarineWork, live: true };
    CW_SCREENS["analytics"] = { id: "cw-analytics-screen", show: cwRenderAnalytics, live: true };
    CW_SCREENS["wave50"] = { id: "cw-wave50-screen", show: cwRenderWave50, live: true };
    CW_SCREENS["reports"] = { id: "cw-reports-screen", show: cwRenderReports };
    CW_SCREENS["ops-status"] = { id: "cw-ops-screen", show: cwLoadOpsStatus };
    CW_SCREENS["audit"] = { id: "cw-audit-screen", show: cwLoadAudit };
    CW_SCREENS["app-settings"] = { id: "cw-appset-screen", show: cwLoadAppSettings };

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
      cwUser = u || cwUser; // ロール別のナビ表示・設定編集可否に使用（#34）
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
    // ---- レイアウト調整（#66: 左サイドメニュー化＋右上ヘッダ被り修正。.dc.html無改修） ----
    function mountHeaderTools() {
      // ベル・ユーザー表示をヘッダ右側の実DOMフローへ挿入（fixed座標の重なりを根治）。
      // dc-runtime がヘッダを再レンダーして外れた場合は MutationObserver 経由で再マウントする。
      var tools = document.getElementById("cw-hdr-tools");
      if (!tools) {
        tools = document.createElement("div");
        tools.id = "cw-hdr-tools";
      }
      var hdr = document.querySelector("header > div:nth-child(2)"); // 右側flexコンテナ
      if (hdr) {
        if (tools.parentNode !== hdr) hdr.appendChild(tools);
      } else if (!tools.parentNode) {
        document.body.appendChild(tools); // ヘッダ未描画時のフォールバック（fixedで右上へ）
      }
      return tools;
    }
    function installLayout() {
      if (document.getElementById("cw-layout-css")) return;
      var css = document.createElement("style");
      css.id = "cw-layout-css";
      css.textContent =
        // 右上ツール: ヘッダ内フローで整列（座標ハードコードなし）。body直下に落ちた時のみfixed
        "#cw-hdr-tools{display:flex;align-items:center;gap:8px;flex:none;"
        + "font-family:'Noto Sans JP',system-ui,sans-serif}"
        + "body>#cw-hdr-tools{position:fixed;top:8px;right:14px;z-index:50}"
        // モックのユーザー表示（山田 太郎）は非表示化（実ログインユーザーのピルに置き換わる）
        + "header>div:nth-child(2)>div:nth-child(4){display:none !important}"
        + "@media(max-width:700px){#cw-user-name{display:none}}"
        // 左サイドメニュー（#79: グループ化カスタムサイドバー。PC幅のみ。狭幅は従来の上部タブ）
        + "@media(min-width:960px){"
        + "nav{display:none !important}"          // dc上部タブは非表示（サイドバーに置換）
        // !important 必須: 後続のベース規則 #cw-sidebar{display:none} と同詳細度のため、
        // 付けないとソース順で display:none が勝ちPC幅でもサイドバーが消える（実ブラウザ検証で判明）
        + "#cw-sidebar{display:flex !important}"
        + "main{margin-left:240px !important;margin-right:18px !important}"
        + "#cw-reg-screen,#cw-wbgt-screen,#cw-settings-screen,.cw-screen{left:224px !important}"
        + "}"
        // サイドバー本体（選択中・クリック時は薄い網掛け）
        + "#cw-sidebar{display:none;position:fixed;top:56px;left:0;bottom:0;width:224px;z-index:31;"
        + "flex-direction:column;gap:1px;padding:10px 8px 20px;background:#fff;border-right:1px solid #dce3ea;"
        + "overflow:hidden auto;box-shadow:1px 0 3px rgba(20,40,60,.05);"
        + "font-family:'Noto Sans JP',system-ui,sans-serif;box-sizing:border-box}"
        // アコーディオン見出し（#79 派生: ダッシュボード以外は開閉式グループ）
        + ".cw-sb-h{display:flex;align-items:center;justify-content:space-between;width:100%;"
        + "margin:10px 0 2px;padding:8px 10px;font-size:10.5px;font-weight:800;color:#8a99a5;"
        + "letter-spacing:.06em;background:transparent;border:none;border-radius:8px;cursor:pointer;"
        + "flex:none;box-sizing:border-box}"
        + ".cw-sb-h:hover{background:rgba(19,52,79,.05);color:#697A88}"
        + ".cw-sb-h-t{font-size:10.5px;font-weight:800;letter-spacing:.06em}"
        + ".cw-sb-arrow{font-size:9px;color:#a2afba;margin-left:6px}"
        + ".cw-sb-body{display:none}"
        + ".cw-sb-body.on{display:block}"
        + ".cw-sb-i{display:block;width:100%;text-align:left;background:transparent;border:none;border-radius:8px;"
        + "padding:10px 12px;font:600 13px 'Noto Sans JP',sans-serif;color:#697A88;cursor:pointer;flex:none}"
        + ".cw-sb-i:hover{background:rgba(19,52,79,.05)}"
        + ".cw-sb-i:active{background:rgba(19,52,79,.12)}"   // クリック時: 薄い網掛け
        + ".cw-sb-i.on{background:rgba(19,52,79,.08);color:#13344f;font-weight:800}" // 選択中: 薄い網掛け
        // #79 新画面の共通スタイル（既存 #cw-reg-screen 等と同じ配色体系）
        + ".cw-screen{display:none;position:fixed;left:0;right:0;top:56px;bottom:0;z-index:30;overflow:auto;"
        + "background:#eef1f4;font-family:'Noto Sans JP',system-ui,sans-serif;color:#16212c}"
        + ".cw-pg{max-width:1100px;margin:0 auto;padding:18px 16px}"
        + ".cw-pg h2{margin:2px 0 4px;font-size:17px;color:#13344f}"
        + ".cw-pg p.sub{margin:0 0 14px;font-size:11.5px;color:#7e8c99}"
        + ".cw-pg-card{background:#fff;border-radius:12px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:14px}"
        + ".cw-pg-bar{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}"
        + ".cw-tbl{width:100%;border-collapse:collapse;font-size:12.5px}"
        + ".cw-tbl th{position:sticky;top:0;background:#f6f8fa;text-align:left;padding:9px 10px;color:#3a4854;"
        + "font-size:11.5px;border-bottom:2px solid #e2e8ee;white-space:nowrap}"
        + ".cw-tbl th.sort{cursor:pointer}.cw-tbl th.sort:hover{background:#eef2f6}"
        + ".cw-tbl td{padding:9px 10px;border-bottom:1px solid #f0f3f6}"
        + ".cw-tbl tr.click{cursor:pointer}.cw-tbl tr.click:hover td{background:#f6f9fc}"
        + ".cw-num{text-align:right;font-variant-numeric:tabular-nums}"
        + ".cw-empty{padding:26px;color:#7e8c99;font-size:12px;text-align:center}"
        + ".cw-btn{padding:9px 16px;border-radius:7px;border:none;cursor:pointer;"
        + "font:700 12.5px 'Noto Sans JP',sans-serif;background:#eef1f4;color:#5a6b7b}"
        + ".cw-btn-pri{background:#16527d;color:#fff}.cw-btn-danger{background:#fbe8e8;color:#c62828}"
        + ".cw-msg{font-size:12px;min-height:16px}"
        + ".cw-badge{display:inline-block;padding:1px 8px;border-radius:8px;font-size:10.5px;font-weight:700}"
        + ".cw-soon{padding:34px 22px;text-align:center}"
        + ".cw-soon .big{font-size:34px;margin-bottom:8px}"
        + ".cw-soon h3{margin:0 0 6px;font-size:15px;color:#13344f}"
        + ".cw-soon p{margin:0;color:#7e8c99;font-size:12.5px;line-height:1.9}"
        + ".cw-soon .plan{display:inline-block;margin-top:14px;text-align:left;background:#f6f8fa;"
        + "border:1px solid #e2e8ee;border-radius:10px;padding:12px 18px;font-size:12px;color:#3a4854;line-height:2}"
        + ".cw-as-row{display:flex;align-items:center;gap:10px;margin:10px 0;font-size:13px;flex-wrap:wrap}"
        + ".cw-as-row label{font-weight:700;color:#3a4854;min-width:130px;font-size:12px}"
        + ".cw-as-row input[type=number],.cw-as-row input[type=password],.cw-as-row input[type=text]{"
        + "padding:9px 10px;border:1px solid #d4dce2;border-radius:7px;font:400 13px 'Noto Sans JP',sans-serif;box-sizing:border-box}"
        + ".cw-as-key{flex:1;min-width:220px}"
        // タイル未設定（本番は外部タイル抑止のため none）でも地図領域が「地図」と
        // 判別できるようグリッド背景を付与（ユーザー報告: マップが表示されない）
        + ".leaflet-container{background:#dbe4ea;"
        + "background-image:linear-gradient(rgba(115,140,160,.16) 1px,transparent 1px),"
        + "linear-gradient(90deg,rgba(115,140,160,.16) 1px,transparent 1px);"
        + "background-size:46px 46px}";
      document.head.appendChild(css);
      mountHeaderTools();
      // ヘッダ再レンダーでツールが外れたら自動復元（コールバックは no-op 主体で軽量）
      new MutationObserver(function () { mountHeaderTools(); })
        .observe(document.body, { childList: true, subtree: true });
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
        + ".cw-login-card .cw-oidc-btn{margin-top:10px;background:#fff;color:#16527d;border:1px solid #b8c9d6}"
        + "#cw-login.oidc input,#cw-login.oidc button[type=submit]{display:none}"
        + "#cw-login.oidc .cw-oidc-btn{display:block}"
        + ".cw-login-msg{margin-top:10px;font-size:12px;min-height:16px;color:#c62828}"
        + ".cw-login-hint{margin-top:14px;font-size:11px;color:#7e8c99;line-height:1.7}"
        + "#cw-user{display:none;align-items:center;gap:8px;"
        + "font:600 11.5px 'Noto Sans JP',sans-serif;color:#cfe0ef;max-width:220px}"
        + "#cw-user-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        + "#cw-user button{background:#1d4d74;color:#fff;border:none;border-radius:6px;padding:4px 10px;font:700 11px 'Noto Sans JP',sans-serif;cursor:pointer;flex:none}";
      document.head.appendChild(css);
      var ov = document.createElement("div"); ov.id = "cw-login";
      ov.innerHTML =
        '<form class="cw-login-card" id="cw-login-form">'
        + "<h2>ログイン</h2><p class=\"sub\">気象・河川・施工判断支援システム</p>"
        + '<label>ユーザー名</label><input name="username" autocomplete="username" required>'
        + '<label>パスワード</label><input name="password" type="password" autocomplete="current-password" required>'
        + '<button type="submit">ログイン</button>'
        + '<div class="cw-login-msg" id="cw-login-msg"></div>'
        + '<div class="cw-login-hint" id="cw-login-hint"></div>'
        + "</form>";
      document.body.appendChild(ov);
      // OIDC コールバックのフラグメント（#oidc_token=...）を消費して再読込する
      try {
        if (typeof location !== "undefined" && location.hash
            && location.hash.indexOf("#oidc_token=") === 0) {
          setToken(decodeURIComponent(location.hash.slice("#oidc_token=".length)));
          if (typeof history !== "undefined" && history.replaceState) {
            history.replaceState(null, "", location.pathname + location.search);
          }
          location.reload();
          return;
        }
      } catch (_) {}
      // OIDC 有効時は Entra ログインボタンを表示し、パスワード入力は隠す
      adapter.oidcStatus().then(function (s) {
        if (s && s.enabled) {
          ov.classList.add("oidc");
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "cw-oidc-btn";
          btn.textContent = "Entra ID でログイン";
          btn.addEventListener("click", function () {
            location.href = apiBase + "/api/auth/oidc/authorize?redirect="
              + encodeURIComponent(location.pathname + location.search);
          });
          ov.querySelector(".cw-login-card").insertBefore(
            btn, ov.querySelector(".cw-login-msg"));
        }
      }).catch(function () {});
      // デモ資格情報のヒントは env=local（seed.py がデモユーザーを作成する環境）でのみ表示する。
      // 本番はデモ資格情報が存在しない/パスワードが異なり得るため、無条件表示は誤情報になる。
      // /health は認証不要・同一オリジンで安全に参照可能。取得失敗時は表示しない（fail-closed）。
      fetch(apiBase + "/health").then(function (r) { return r.json(); }).then(function (h) {
        if (h && h.env === "local") {
          var hint = ov.querySelector("#cw-login-hint");
          if (hint) hint.textContent = "デモ: admin / admin123（管理者） ・ yamada / pass1234（現場管理者） ・ viewer / pass1234（閲覧）";
        }
      }).catch(function () {});
      var pill = document.createElement("div"); pill.id = "cw-user";
      pill.innerHTML = '<span id="cw-user-name"></span><button id="cw-logout">ログアウト</button>';
      (document.getElementById("cw-hdr-tools") || document.body).appendChild(pill);
      ov.querySelector("#cw-login-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var f = e.target, msg = ov.querySelector("#cw-login-msg");
        msg.textContent = "認証中…";
        adapter.login(f.username.value, f.password.value).then(function (res) {
          if (res.ok) {
            // フルリロードで前ユーザーの画面状態・キャッシュを完全に破棄してから開始する
            // （管理画面DOM/変数の残留を根絶。#83 対抗レビュー[high]）
            msg.textContent = "";
            location.reload();
          } else { msg.textContent = (res.body && res.body.detail) || "ログインに失敗しました"; }
        }).catch(function () { msg.textContent = "通信エラー（バックエンド未起動の可能性）"; });
      });
      pill.querySelector("#cw-logout").addEventListener("click", function () {
        adapter.logout().then(function () { location.reload(); });
      });
    }

    // ---- 通知ベル（設計§14。注入） ----
    function notifColor(sev) { return sev >= 2 ? "#c62828" : sev === 1 ? "#e8930c" : "#5a6b7b"; }
    function installNotifyBell() {
      if (document.getElementById("cw-bell")) return;
      var css = document.createElement("style");
      css.textContent =
        "#cw-bell{display:none;cursor:pointer;font-size:17px;"
        + "color:#cfe0ef;background:#1d4d74;border:none;border-radius:8px;padding:4px 9px;flex:none}"
        + ".cw-bell-badge{background:#c62828;color:#fff;font:700 10px sans-serif;min-width:15px;height:15px;"
        + "border-radius:8px;display:none;align-items:center;justify-content:center;padding:0 3px;margin-left:3px}"
        + ".cw-bell-badge.on{display:inline-flex}"
        + "#cw-notif{position:fixed;right:14px;top:60px;z-index:51;display:none;width:min(380px,92vw);max-height:62vh;"
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
      var tools = document.getElementById("cw-hdr-tools");
      if (tools) tools.insertBefore(bell, tools.firstChild); else document.body.appendChild(bell);
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
          installLayout();  // 先にツールコンテナを用意（ログイン画面・ベルが append する）
          installSidebar(); // #79 グループ化サイドメニュー
          installRegisterScreen(adapter);
          installSettingsScreen();
          installSourceNote();
          installWbgtScreen();
          // #79 新画面群（現場一覧/気象全国版/分析/レポート/監査ログ/設定/準備中）
          installSitesScreen();
          installWxScreen();
          installAnalyticsScreen();
          installRiverScreen();
          installReportsScreen();
          installOpsScreen();
          installAuditScreen();
          installAppSettingsScreen();
          installMarineScreen();
          installMarineWorkScreen();
          installWave50Screen();
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
    module.exports = { createAdapter: createAdapter, isAllowedApiBase: isAllowedApiBase };
  }
  global.__cwCreateAdapter = createAdapter;
})(typeof globalThis !== "undefined" ? globalThis : this);
