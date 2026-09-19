import { apiFetch } from "../api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart, renderNavBarChart } from "../lw_charts.js";
import { fmtScore, scoreCls } from "../paper/fmt.js";
import { renderPaperT0 } from "../paper/t0_ui.js?v=p2513";
import { portfolioBtScoreFloorPayload as buildBtScoreFloorPayload, mergeScoringFloors } from "./scoring.js";
import { truncateStockName, watchingNameSpanHtml } from "./names.js";
import { ensureFitTierMap } from "./fit_tier_ui.js";
import { downloadBlob } from "../shared.js";
import { collectPathMatrixForm, collectExecutionForm, readT0BtSizing, fillT0BtSizing } from "../paper/execution_ui.js?v=p2512";
import { initExecutionRuleForms } from "../paper/execution_forms.js?v=p2512";

const _V =
  (typeof window !== "undefined" && window.__ASSET_V__) || "dev";
const { mountVirtualTable } = await import(
  `../virtual_table.js?v=${encodeURIComponent(_V)}`
);
const { createScoreTooltipController } = await import(
  `../score_tooltip.js?v=${encodeURIComponent(_V)}`
);
const {
  simTradesIntentDiffers,
  btSimTradeColumns,
  buildSimTradesCsv,
  resolveSimTradeLegs,
  buildSimTradeRows,
  simTradesCaptionHtml,
  btTradesNumCompare,
  btTradesCellHtml,
  flattenTradesToSimLegs,
  formatFactorWeightsNote,
  formatSimStatus,
  isRankLotsLedger,
  BT_LEDGER_TRADE_COLS,
  buildLedgerTradeRows,
  curveLedgerDays,
  ledgerTradesCaptionHtml,
  buildLedgerTradesCsv,
} = await import(`./bt_trades.js?v=${encodeURIComponent(_V)}`);
const { renderNeutralCompareTable: renderNeutralCompareTableHtml } = await import(
  `./neutral_compare.js?v=${encodeURIComponent(_V)}`
);
const {
  fmtPct,
  metricClass,
  buildPortfolioBacktestSummaryText,
  buildPortfolioBacktestFailText,
  applyReplayOverviewKpis,
  applyReplayT0Kpis,
} = await import(`./bt_result.js?v=${encodeURIComponent(_V)}`);

/** Top-K 净值图横轴只展示最近 N 个自然日（含末日）。 */
export const TOPK_NAV_CHART_WINDOW_DAYS = 15;

function _curvePointDate(p) {
  return String((p && (p.date || p.ts || p.time)) || "").slice(0, 10);
}

export function sliceCurveToDateWindow(series, days = TOPK_NAV_CHART_WINDOW_DAYS) {
  const rows = Array.isArray(series) ? series : [];
  if (!rows.length) return rows;
  const last = _curvePointDate(rows[rows.length - 1]);
  const n = Math.max(2, Number(days) || TOPK_NAV_CHART_WINDOW_DAYS);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(last)) return rows.slice(-n);
  const endMs = Date.parse(`${last}T00:00:00Z`);
  if (!Number.isFinite(endMs)) return rows.slice(-n);
  const startStr = new Date(endMs - (n - 1) * 86400000).toISOString().slice(0, 10);
  const sliced = rows.filter((p) => _curvePointDate(p) >= startStr);
  return sliced.length >= 2 ? sliced : rows.slice(-n);
}

/** Quant domain: backtest */
export function installBacktest(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText, btSimScoreTips, watchingNameFromEl } = q;
  const { fmtPct, metricClass, renderMetricCards, renderBtScopeNote, renderRobustnessPanel, buildPortfolioBacktestCards, BT_SCOPE_LIVE, BT_SCOPE_FROZEN, readHorizonDays, quantBtBusyIds } = q;
  const { renderAttributionTablesHtml, buildCrossSectionResult, renderScoreIcHtml, renderReplayStockContribHtml } = q;
  const { researchGridHtml, metricCell } = q;

  function isReplayDesk() {
    return typeof document !== "undefined" && document.body?.getAttribute("data-page") === "replay";
  }

  const BT_NAV_TITLE = "rank_lots 净值曲线";
  const BT_NAV_HINT =
    "纵轴：净值（起始=100）· 横轴全部交易日 · 蓝=策略 · 绿=基准 · 可缩放";
  let btStockChartSeq = 0;

  function btTradeRowAttrs(d) {
    if (d && d.kind === "day") {
      return { "data-kind": "day", "data-date": d.date || "" };
    }
    return {
      "data-code": d.stock_code || "",
      "data-name": d.name || "",
      "data-date": d.date || "",
    };
  }

  function btTradeRowClass(d) {
    if (d && d.kind === "day") return "bt-ledger-day";
    const parts = ["bt-ledger-leg"];
    if (d.actionKey) parts.push(`is-${d.actionKey}`);
    if (d.skipped) parts.push("bt-trade-skip");
    if (state.btChartStockCode && d.stock_code === state.btChartStockCode) {
      parts.push("is-chart-active");
    }
    return parts.join(" ");
  }

  function syncBtChartChrome({ mode, name, code, error } = {}) {
    const titleEl = document.getElementById("quant-chart-title");
    const hintEl = document.getElementById("quant-chart-axis-hint");
    const navBtn = document.getElementById("quant-chart-nav-mode");
    const stock = mode === "stock";
    if (titleEl) {
      titleEl.textContent = stock
        ? `${name || code || "单票"} · 收盘价`
        : isReplayDesk()
          ? "rank_lots 日收益"
          : BT_NAV_TITLE;
    }
    if (hintEl) {
      hintEl.textContent = stock
        ? error
          ? `日线加载失败：${error}`
          : isReplayDesk()
            ? "纵轴：收盘价 · 近 60 个交易日 · 点「净值」回到日收益柱"
            : "纵轴：收盘价 · 近 60 个交易日 · 点「净值」回到组合曲线"
        : isReplayDesk()
          ? "纵轴：日收益%（红涨绿跌）· 悬停看净值与个股明细 · 横轴全部交易日 · 可缩放"
          : BT_NAV_HINT;
    }
    if (navBtn) navBtn.hidden = !stock;
  }

  function restoreNavChart() {
    btStockChartSeq += 1;
    const saved = state.lastNavChart;
    if (saved) {
      paintPortfolioChart(
        saved.curve,
        saved.emptyText,
        saved.benchCurve,
        saved.benchLabel,
        saved.icAlign
      );
      return;
    }
    state.btChartMode = "nav";
    state.btChartStockCode = null;
    state.btChartStockName = "";
    syncBtChartChrome({ mode: "nav" });
    if (state.btTradesTableApi && typeof state.btTradesTableApi.repaint === "function") {
      state.btTradesTableApi.repaint();
    }
  }

  async function paintStockKline(code, name) {
    const host = els.quantPortfolioChart;
    if (!host || !code) return;
    const seq = ++btStockChartSeq;
    const label = name || code;
    syncBtChartChrome({ mode: "stock", name: label, code });
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (legendEl) {
      legendEl.textContent = `${label} 日线 · 近 60 日收盘 · 再点名称或「净值」回到 rank_lots。`;
    }
    await renderLineChart(host, [], { emptyText: "加载日线…" });
    if (seq !== btStockChartSeq) return;
    try {
      const res = await fetch(
        `/api/watching/daily-chart?code=${encodeURIComponent(code)}&lookback=60`
      );
      const data = await res.json().catch(() => ({}));
      if (seq !== btStockChartSeq) return;
      if (!res.ok) throw new Error(data.detail || res.statusText || "加载失败");
      const pts = (data.points || [])
        .map((p) => ({
          time: String(p.date || "").slice(0, 10),
          value: Number(p.close),
        }))
        .filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.time) && Number.isFinite(p.value));
      const stockName = String(data.stock_name || name || code).trim();
      if (state.btChartStockCode === code) state.btChartStockName = stockName;
      syncBtChartChrome({ mode: "stock", name: stockName, code });
      await renderLineChart(host, pts, {
        emptyText: "暂无日线",
        ma: [5, 10, 20],
        disableZoom: false,
      });
    } catch (err) {
      if (seq !== btStockChartSeq) return;
      const msg = String((err && err.message) || err || "加载失败");
      syncBtChartChrome({ mode: "stock", name: label, code, error: msg });
      await renderLineChart(host, [], { emptyText: `日线加载失败：${msg}` });
    }
  }

  function focusBtTradeStock(code, name) {
    const c = String(code || "").trim();
    if (!c) return;
    if (state.btChartMode === "stock" && state.btChartStockCode === c) {
      restoreNavChart();
      return;
    }
    state.btChartMode = "stock";
    state.btChartStockCode = c;
    state.btChartStockName = String(name || "").trim();
    if (state.btTradesTableApi && typeof state.btTradesTableApi.repaint === "function") {
      state.btTradesTableApi.repaint();
    }
    const host = els.quantPortfolioChart;
    if (host && typeof host.scrollIntoView === "function") {
      try {
        host.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) {
        /* ignore */
      }
    }
    paintStockKline(c, state.btChartStockName || c);
  }

  function bindBtTradesStockClicks(api) {
    if (!api || typeof api.on !== "function") return;
    api.on("rowClick", ({ code, event, row }) => {
      const t = event && event.target;
      if (!t || typeof t.closest !== "function") return;
      if (!t.closest(".watching-stock, .watching-col-name")) return;
      const stockEl = t.closest(".watching-stock");
      const nameEl = stockEl && stockEl.querySelector(".watching-name-text");
      const fromEl =
        typeof watchingNameFromEl === "function"
          ? watchingNameFromEl(nameEl, code)
          : "";
      const name =
        (row && row.dataset && row.dataset.name) || fromEl || code;
      focusBtTradeStock(code, name);
    });
  }

  const navModeBtn = document.getElementById("quant-chart-nav-mode");
  if (navModeBtn && navModeBtn.dataset.wired !== "1") {
    navModeBtn.dataset.wired = "1";
    navModeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      restoreNavChart();
    });
  }

  function writePortfolioSummary(text, { error = false } = {}) {
    const el = els.quantPortfolioSummary;
    const shown = String(text || "");
    if (!el) return shown;
    if (isReplayDesk() && !error) {
      el.textContent = "";
      el.hidden = true;
      el.classList.remove("down");
      return shown;
    }
    el.hidden = !shown;
    el.textContent = shown;
    el.classList.toggle("down", !!error);
    return shown;
  }

  function clearBtTradesTable() {
    state.btTradesTableApi = null;
    state.lastSimTrades = [];
    state.lastLedgerTrades = false;
    if (els.quantBtTrades) els.quantBtTrades.innerHTML = "";
    renderStockContribTable(null);
  }

  function renderStockContribTable(data) {
    const el = document.getElementById("quant-bt-stock-contrib");
    if (!el) return;
    const rows = data && Array.isArray(data.stock_contrib) ? data.stock_contrib : [];
    const renderHtml =
      typeof renderReplayStockContribHtml === "function"
        ? renderReplayStockContribHtml
        : q.renderReplayStockContribHtml;
    const html =
      rows.length && typeof renderHtml === "function" ? renderHtml(rows) : "";
    if (!html) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    el.innerHTML = html;
    bindStockContribClicks(el);
    void ensureFitTierMap(el);
  }

  function bindStockContribClicks(host) {
    if (!host || host.dataset.wired === "1") return;
    host.dataset.wired = "1";
    host.addEventListener("click", (e) => {
      const t = e.target;
      if (!t || typeof t.closest !== "function") return;
      const cell = t.closest(".watching-stock");
      if (!cell) return;
      const row = cell.closest("tr");
      const code = (row && row.getAttribute("data-code")) || "";
      if (!code) return;
      const nameEl = cell.querySelector(".watching-name-text");
      const fromEl =
        typeof watchingNameFromEl === "function"
          ? watchingNameFromEl(nameEl, code)
          : "";
      const name = (row && row.getAttribute("data-name")) || fromEl || code;
      focusBtTradeStock(code, name);
    });
  }

  function downloadSimTradesCsv(rows) {
    const ledger = !!state.lastLedgerTrades;
    const blob = new Blob(
      [
        ledger
          ? buildLedgerTradesCsv(rows, state.watchingNameByCode)
          : buildSimTradesCsv(rows, state.watchingNameByCode),
      ],
      { type: "text/csv;charset=utf-8" }
    );
    const tag = ledger ? "rank_lots_trades" : "topk_sim_trades";
    downloadBlob(blob, `${tag}_${new Date().toISOString().slice(0, 10)}.csv`);
  }

  async function fitReturnScoreModel() {
    setQuantBtBusy(true, "拟合收益排序模型…");
    try {
      const { lookback, horizon_days } = readPortfolioBtParams();
      const { ok, data, error } = await apiFetch("/api/quant/return-model/fit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback,
          horizon_days,
          watching_limit: 12,
          save_draft: true,
        }),
      });
      if (!ok || !data || !data.success) {
        const msg = (data && (data.error || data.detail)) || error || "拟合失败";
        writePortfolioSummary(msg, { error: true });
        return;
      }
      const draft = data.draft || {};
      const msg =
        `ŷ模型已拟合并落草稿 n=${data.sample_count} R²=${(data.ols && data.ols.r_squared) ?? "—"}` +
        (draft.path ? ` · ${draft.path}` : "") +
        " · 人审 promote 后配合 scoring.rank_mode=predicted_score";
      writePortfolioSummary(msg);
    } finally {
      setQuantBtBusy(false);
    }
  }

  function formatSnapshotAt(iso) {
    if (!iso) return "—";
    const s = String(iso);
    return s.length > 19 ? s.slice(0, 19).replace("T", " ") : s.replace("T", " ");
  }

  function frozenBtCaliber(ps) {
    const p = (ps && ps.params) || {};
    const cost = ps && ps.cost_model === "zero" ? "零成本" : "含成本";
    const engine = String(p.engine || "");
    if (engine === "paper_replay" || p.rank_enter != null) {
      const lb = p.lookback != null ? p.lookback : "—";
      const clock = p.fill_clock || "09:30";
      const alpha = p.y_on_alpha != null ? p.y_on_alpha : "—";
      const wt = p.fusion_w_oo != null ? p.fusion_w_oo : p.fusion_w_trade != null ? p.fusion_w_trade : "—";
      const wn = p.fusion_w_oc != null ? p.fusion_w_oc : p.fusion_w_nowcast != null ? p.fusion_w_nowcast : "—";
      const lotB = p.lot_base != null ? p.lot_base : "—";
      const n = Number(p.rank_enter);
      const enter = Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : "—";
      const tiers = Array.isArray(p.universe_fit_tiers)
        ? p.universe_fit_tiers.join("")
        : "";
      const tierBit = tiers && tiers !== "ABC" ? ` · 档${tiers}` : "";
      const psBit = p.price_space_gate === false ? " · 日分价关" : "";
      const role = String(p.score_model_role || "").toLowerCase() === "live" ? "执行" : "研究";
      return `lb${lb} · ${clock} · ${lotB}股 · w${wt}/${wn} · α${alpha} · 入场${enter}${tierBit}${psBit} · ${role} · ${cost}`;
    }
    // 仅兼容旧日报冻结摘要（研究 Top-K 独立腿）
    return `K${p.top_k ?? "—"} · h${p.horizon_days ?? "—"} · ${cost}`;
  }

  function scoreToRankPct(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return "";
    return (n * 100).toFixed(2);
  }

  function applyPortfolioBtParams(req, { rules = true, desk = true } = {}) {
    if (!req || typeof req !== "object") return;
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el || val == null || val === "") return;
      el.value = String(val);
    };
    const form = document.getElementById("paper-path-matrix-form");
    const setName = (name, val) => {
      if (!form || val == null || val === "") return;
      const el = form.querySelector(`[name="${name}"]`);
      if (el) el.value = String(val);
    };
    if (desk) {
      if (req.lookback != null) setVal("quant-lookback", req.lookback);
      if (req.score_model_role != null) applyScoreModelRole("quant-score-model-role", req.score_model_role);
      if (req.fill_clock != null) setVal("quant-fill-clock", clampFillClock(req.fill_clock));
      if (req.initial_cash != null) setName("pm_initial_cash", req.initial_cash);
      if (req.lot_base != null) setName("pm_lot_base", req.lot_base);
      if (req.lot_strong != null) setName("pm_lot_strong", req.lot_strong);
      if (req.universe_fit_tiers != null) applyUniverseFitTiers(req.universe_fit_tiers);
      if (req.price_space_gate != null) {
        const el = form && form.querySelector('[name="pm_price_space_gate"]');
        if (el && el.type === "checkbox") el.checked = req.price_space_gate !== false;
      }
    }
    if (!rules) return;
    const wOo = req.fusion_w_oo != null ? req.fusion_w_oo : req.fusion_w_trade;
    const wOc = req.fusion_w_oc != null ? req.fusion_w_oc : req.fusion_w_nowcast;
    if (wOo != null) {
      setName("pm_fusion_w_oo", wOo);
    }
    if (wOc != null) {
      setName("pm_fusion_w_nc", wOc);
    }
    if (req.y_on_alpha != null) {
      setName("pm_y_on_alpha", req.y_on_alpha);
    }
    const enterPct = scoreToRankPct(req.rank_enter);
    if (enterPct) {
      setName("pm_rank_enter", enterPct);
    }
    const strongPct = scoreToRankPct(req.rank_strong != null ? req.rank_strong : req.rank_enter);
    if (strongPct) setName("pm_rank_strong", strongPct);
    const setChk = (name, val, fallback) => {
      const el = form && form.querySelector(`[name="${name}"]`);
      if (!el || el.type !== "checkbox") return;
      el.checked = val != null ? !!val : fallback;
    };
    setChk(
      "pm_y_oo_gt0",
      req.y_oo_gt0,
      req.y_oo_oc_enabled === true
    );
    setChk(
      "pm_y_oc_gt0",
      req.y_oc_gt0,
      req.y_oo_oc_enabled === true
    );
    setChk(
      "pm_y_hl_gt0",
      req.y_hl_gt0,
      req.y_hl_enabled !== false
    );
  }

  function applyUniverseFitTiers(tiers) {
    const allowed = new Set(["A", "B", "C"]);
    const list = Array.isArray(tiers)
      ? tiers.map((t) => String(t || "").toUpperCase()).filter((t) => allowed.has(t))
      : [];
    const on = list.length ? new Set(list) : new Set(["A", "B", "C"]);
    ["A", "B", "C"].forEach((t) => {
      const el = document.getElementById(`quant-universe-tier-${t.toLowerCase()}`);
      if (el) el.checked = on.has(t);
    });
  }

  function readUniverseFitTiers() {
    const out = [];
    ["A", "B", "C"].forEach((t) => {
      const el = document.getElementById(`quant-universe-tier-${t.toLowerCase()}`);
      if (el && el.checked) out.push(t);
    });
    return out.length ? out : ["A", "B", "C"];
  }

  async function hydrateReplayUniverseFromLive() {
    try {
      const res = await fetch("/api/quant/cluster-live/status?light=1");
      const data = await res.json();
      const tiers =
        data && data.cluster_scoring && data.cluster_scoring.universe_fit_tiers;
      if (Array.isArray(tiers) && tiers.length) applyUniverseFitTiers(tiers);
    } catch (_) {
      /* ignore */
    }
  }

  function markReplayRestored(at) {
    const meta = document.getElementById("replay-meta");
    if (meta) {
      meta.textContent = `历史验证 · rank_lots（${readFillClock()} 5m）· 上次结果 ${at} · 刷新未重跑`;
    }
  }

  async function restoreLastPortfolioBacktest() {
    if (!isReplayDesk()) return;
    if (state.btBusy) return;
    try {
      const resumed = await resumePortfolioBacktestJobIfRunning();
      if (resumed || state.btBusy) return;
    } catch (_) {
      /* fall through to snapshot */
    }
    try {
      const res = await fetch("/api/quant/last-portfolio-backtest");
      const pack = await res.json();
      if (state.btBusy) return;
      if (!pack || pack.empty || !pack.result || !pack.result.success) {
        if (!restoreReplayDeskPrefs()) await hydrateReplayUniverseFromLive();
        return;
      }
      const data = pack.result;
      const at = formatSnapshotAt(pack.saved_at);
      const req = data.request || data.params || {};
      const hasPrefs = !!readReplayDeskPrefs();
      // 上次回测只恢复净值/成交；表单以「保存」为准，勿用上次跑参盖掉已存配置
      applyPortfolioBtParams(req, { rules: false, desk: !hasPrefs });
      restoreReplayDeskPrefs();
      if (!hasPrefs && !Array.isArray(req.universe_fit_tiers)) {
        await hydrateReplayUniverseFromLive();
      }
      renderPortfolioBacktestResult(data);
      applyReplayOverviewKpis(data, { source: at ? `上次 ${at}` : "上次结果" });
      paintPortfolioChart(
        data.equity_curve,
        "回测无足够交易点",
        (data.benchmark && data.benchmark.ok && data.benchmark.equity_curve) || null,
        (data.benchmark && data.benchmark.benchmark_label) || null
      );
      markReplayRestored(at);
    } catch (_) {
      /* ignore */
    }
  }

  async function loadLastBacktestSnapshot() {
    if (isReplayDesk()) return;
    try {
      const res = await fetch("/api/quant/last");
      const data = await res.json();
      if (!data || data.empty) return;
      if (data.portfolio_backtest_summary && data.portfolio_backtest_summary.success) {
        const ps = data.portfolio_backtest_summary;
        const nc = data.portfolio_neutral_compare_summary;
        const meta = data.snapshot_meta || {};
        const at = formatSnapshotAt(meta.generated_at || data.generated_at);
        let summary = `日报冻结摘要 · ${at} · 累计 ${ps.total_return_pct ?? "—"}% · 胜率 ${ps.win_rate_pct ?? "—"}% · 交易 ${ps.trade_count ?? "—"}`;
        if (nc && nc.success) {
          summary += ` · 中性化 Δ${nc.delta?.total_return_pct ?? "—"}% (${nc.winner})`;
        }
        writePortfolioSummary(summary);
        renderBtScopeNote(`${BT_SCOPE_FROZEN} · 冻结于 ${at}`, { warn: true });
        renderMetricCards(els.quantBtMetrics, [
          { label: "累计收益", value: fmtPct(ps.total_return_pct), cls: metricClass(ps.total_return_pct) },
          { label: "胜率", value: fmtPct(ps.win_rate_pct) },
          { label: "交易次数", value: escapeHtml(String(ps.trade_count ?? "—")) },
          { label: "结果来源", value: "日报冻结" },
          { label: "冻结时间", value: escapeHtml(at) },
          {
            label: "口径",
            value: escapeHtml(frozenBtCaliber(ps)),
          },
        ]);
        if (nc && nc.success) {
          state.neutralCompareSource = "frozen";
          renderNeutralCompareTable(nc, null, {
            frozen: true,
            frozenAt: at,
          });
        } else {
          renderNeutralCompareTable(null);
        }
        paintPortfolioChart(ps.equity_curve_tail, "无组合摘要曲线");
        applyReplayOverviewKpis(ps, { source: `冻结 ${at}` });
        setBtTradesCaption("日报仅含摘要曲线；点击「跑回测」加载完整模拟成交账");
      }
    } catch (_) {
      /* ignore */
    }
  }

  async function paintDualPortfolioChart(seriesA, seriesB, emptyText) {
    const host = els.quantPortfolioChart;
    if (!host) return;
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (legendEl) {
      legendEl.textContent =
        "中性化对照双曲线。起点 100；横轴近15日（持有期结束日）。蓝=截面中性化ŷ · 绿=未中性化ŷ（同一观察池与参数）。";
    }
    await renderDualLineChart(
      host,
      sliceCurveToDateWindow(seriesA),
      sliceCurveToDateWindow(seriesB),
      { emptyText, disableZoom: true }
    );
  }

  async function paintIcChart(sic) {
    const chartWrap = document.getElementById("quant-ic-chart-wrap");
    const chartHost = document.getElementById("quant-ic-chart");
    if (!chartHost || !chartWrap) return;
    const daily = sic.ic_series_tail || [];
    const rolling = sic.ic_rolling_tail || [];
    const toPts = (rows, key) =>
      (rows || []).map((p, i) => {
        const t = String(p.date || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p[key] ?? p.ic),
        };
      });
    const dPts = toPts(daily, "ic");
    const rPts = toPts(rolling, "ic");
    if (dPts.length < 2 && rPts.length < 2) {
      chartWrap.hidden = true;
      return;
    }
    chartWrap.hidden = false;
    const series = [];
    if (dPts.length >= 2) {
      series.push({ label: "日度IC", color: "#93c5fd", lineWidth: 1.5, points: dPts });
    }
    if (rPts.length >= 2) {
      series.push({ label: "滚动IC", color: "#059669", lineWidth: 2.5, points: rPts });
    }
    // 零轴参考：用极短水平线不合适；双线足够。若仅一条也画。
    if (series.length === 1) {
      await renderLineChart(chartHost, series[0].points, {
        emptyText: "IC 序列不足",
        disableZoom: true,
        zeroLine: true,
        color: series[0].color,
      });
      return;
    }
    await renderMultiLineChart(chartHost, series, {
      emptyText: "IC 序列不足",
      disableZoom: true,
      zeroLine: true,
    });
  }

  async function paintNeutralCompareChart(data) {
    const nCurve = (data.neutralized && data.neutralized.equity_curve) || [];
    const aCurve = (data.absolute && data.absolute.equity_curve) || [];
    const toPts = (series) =>
      (series || []).map((p, i) => {
        const t = String(p.date || p.ts || p.time || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p.equity_nav ?? p.equity_norm ?? p.equity ?? p.value),
        };
      });
    const nPts = toPts(sliceCurveToDateWindow(nCurve));
    const aPts = toPts(sliceCurveToDateWindow(aCurve));
    const benchCurve =
      (data.neutralized && data.neutralized.benchmark && data.neutralized.benchmark.equity_curve) ||
      (data.absolute && data.absolute.benchmark && data.absolute.benchmark.equity_curve) ||
      [];
    const bPts = toPts(sliceCurveToDateWindow(benchCurve));
    const host = els.quantPortfolioChart;
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (!host) return;
    const series = [];
    if (nPts.length >= 2) {
      series.push({ label: "中性化", color: "#2563eb", lineWidth: 2, points: nPts });
    }
    if (aPts.length >= 2) {
      series.push({ label: "未中性化ŷ", color: "#059669", lineWidth: 2, points: aPts });
    }
    if (bPts.length >= 2) {
      series.push({ label: "基准", color: "#9ca3af", lineWidth: 1.5, points: bPts });
    }
    if (series.length >= 2) {
      if (legendEl) {
        legendEl.textContent =
          "中性化对照：蓝=截面中性化 · 绿=未中性化ŷ" +
          (bPts.length >= 2 ? " · 灰=同一基准买持" : "") +
          "。起点 100；横轴近15日；超额见对照表。";
      }
      await renderMultiLineChart(host, series, {
        emptyText: "对照曲线不足",
        disableZoom: true,
      });
      return;
    }
    await paintDualPortfolioChart(nCurve, aCurve, "对照曲线不足");
  }

  function buildDayDetailsMap(curve) {
    const m = new Map();
    for (const p of curve || []) {
      if (!p || typeof p !== "object") continue;
      const d = String(p.date || p.ts || p.time || "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) continue;
      const legs = Array.isArray(p.legs) ? p.legs : [];
      const more = Number(p.legs_more);
      if (!legs.length && !(more > 0)) continue;
      m.set(d, {
        legs,
        n_more: Number.isFinite(more) && more > 0 ? more : 0,
      });
    }
    return m;
  }

  async function paintPortfolioChart(curve, emptyText, benchCurve, benchLabel) {
    const host = els.quantPortfolioChart;
    if (!host) return;
    state.lastNavChart = {
      curve: Array.isArray(curve) ? curve : [],
      emptyText: emptyText || "",
      benchCurve: benchCurve || null,
      benchLabel: benchLabel || null,
    };
    const hadStock = state.btChartMode === "stock" || !!state.btChartStockCode;
    state.btChartMode = "nav";
    state.btChartStockCode = null;
    state.btChartStockName = "";
    syncBtChartChrome({ mode: "nav" });
    if (hadStock && state.btTradesTableApi && typeof state.btTradesTableApi.repaint === "function") {
      state.btTradesTableApi.repaint();
    }
    const legendEl = document.getElementById("quant-portfolio-legend");
    const toPts = (series) =>
      (series || []).map((p, i) => {
        const t = String(p.date || p.ts || p.time || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p.equity_nav ?? p.equity_norm ?? p.equity ?? p.value),
        };
      });
    const fullWindow = isReplayDesk();
    const windowed = fullWindow ? (Array.isArray(curve) ? curve : []) : sliceCurveToDateWindow(curve);
    const windowedBench = fullWindow
      ? Array.isArray(benchCurve)
        ? benchCurve
        : []
      : sliceCurveToDateWindow(benchCurve);
    const winStart = _curvePointDate(windowed[0] || {});
    const pts = toPts(windowed);
    const bpts = toPts(windowedBench);
    const axisHint = fullWindow
      ? `横轴全部 ${pts.length || 0} 个交易日。`
      : "横轴近15日。";
    if (fullWindow) {
      if (legendEl) {
        legendEl.textContent = bpts.length
          ? `日收益柱（红涨绿跌）。悬停看净值、基准与个股明细。${axisHint}`
          : `日收益柱（红涨绿跌）。悬停看净值与个股明细。起点净值 100。${axisHint}`;
      }
      await renderNavBarChart(host, pts, bpts, {
        emptyText,
        disableZoom: false,
        labelA: "策略",
        labelB: benchLabel || "基准",
        dayDetails: buildDayDetailsMap(windowed),
        nameByCode: state.watchingNameByCode,
      });
      return;
    }
    if (bpts.length >= 2 && pts.length >= 2) {
      if (legendEl) {
        legendEl.textContent = fullWindow
          ? `rank_lots（蓝）vs ${benchLabel || "基准"}（绿）。起点 100。${axisHint}`
          : `rank_lots 净值（蓝）vs ${benchLabel || "基准"}（绿）。起点 100；${axisHint}` +
            "超额看指标卡，勿只看绝对累计。";
      }
      await renderDualLineChart(host, pts, bpts, {
        emptyText,
        disableZoom: !fullWindow,
      });
      return;
    }
    if (legendEl) {
      legendEl.textContent = fullWindow
        ? `rank_lots 净值。起点 100。${axisHint}`
        : "rank_lots 净值（非研究独立腿）。起点 100。单线=当次回测；对照时蓝=中性化、绿=未中性化ŷ。" +
          ` ${axisHint}`;
    }
    await renderLineChart(host, pts, { emptyText, disableZoom: !fullWindow });
  }

  function readInitialCash() {
    const form = document.getElementById("paper-path-matrix-form");
    const el = form && form.querySelector('[name="pm_initial_cash"]');
    let v = 200000;
    if (el && el.value !== "") {
      const n = Number(el.value);
      if (Number.isFinite(n)) v = n;
    }
    return Math.round(Math.max(10000, Math.min(1e8, v)));
  }

  function clampReplayLot(raw, fallback) {
    let v = fallback;
    if (raw != null && raw !== "") {
      const n = Number(raw);
      if (Number.isFinite(n)) v = n;
    }
    v = Math.round(Math.max(100, Math.min(10000, v)));
    return Math.floor(v / 100) * 100;
  }

  function readReplayLots() {
    const form = document.getElementById("paper-path-matrix-form");
    const val = (name, fallback) => {
      const el = form && form.querySelector(`[name="${name}"]`);
      return clampReplayLot(el && el.value, fallback);
    };
    let lot_base = val("pm_lot_base", 200);
    let lot_strong = val("pm_lot_strong", lot_base);
    if (lot_strong < lot_base) lot_strong = lot_base;
    const setIf = (name, v) => {
      const el = form && form.querySelector(`[name="${name}"]`);
      if (el && el.value !== String(v)) el.value = String(v);
    };
    setIf("pm_lot_base", lot_base);
    setIf("pm_lot_strong", lot_strong);
    return { lot_base, lot_strong };
  }

  function readPriceSpaceGate() {
    const form = document.getElementById("paper-path-matrix-form");
    const el = form && form.querySelector('[name="pm_price_space_gate"]');
    if (!el) return true;
    return !!el.checked;
  }

  function readYOnAlpha() {
    const form = document.getElementById("paper-path-matrix-form");
    const el = form && form.querySelector('[name="pm_y_on_alpha"]');
    if (el && el.value !== "") {
      const n = Number(el.value);
      if (Number.isFinite(n)) return Math.max(0, Math.min(10, n));
    }
    return 1;
  }

  function readFusionWeights() {
    const form = document.getElementById("paper-path-matrix-form");
    if (form) {
      const pack = collectPathMatrixForm(form);
      const lots = pack && pack.rebalance_timing && pack.rebalance_timing.rank_lots;
      if (lots) {
        return {
          fusion_w_oo: lots.fusion_w_oo,
          fusion_w_oc: lots.fusion_w_oc,
          fusion_w_trade: lots.fusion_w_oo,
          fusion_w_nowcast: lots.fusion_w_oc,
        };
      }
    }
    return {
      fusion_w_oo: 0.6,
      fusion_w_oc: 0.4,
      fusion_w_trade: 0.6,
      fusion_w_nowcast: 0.4,
    };
  }

  const RANK_PCT_DEFAULT = 0.1;

  /** 表单为百分数（0.1 → 0.1%）；引擎仍用 0.001。 */
  function rankPctToScore(raw, fallbackPct = RANK_PCT_DEFAULT) {
    let pct = fallbackPct;
    if (raw != null && raw !== "") {
      const n = Number(raw);
      if (Number.isFinite(n)) pct = n;
    }
    const score = pct / 100;
    return Math.round(Math.max(0, Math.min(1, score)) * 10000) / 10000;
  }

  function readRankThresholds() {
    const form = document.getElementById("paper-path-matrix-form");
    if (form) {
      const pack = collectPathMatrixForm(form);
      const lots = pack && pack.rebalance_timing && pack.rebalance_timing.rank_lots;
      if (lots) {
        return {
          rank_enter: lots.rank_enter,
          rank_strong: lots.rank_strong,
          rank_enter_alt: lots.rank_enter_alt,
          y_enter_enabled: lots.y_enter_enabled,
          y_enter_alt_enabled: lots.y_enter_alt_enabled,
          y_oo_gt0: lots.y_oo_gt0 === true,
          y_oc_gt0: lots.y_oc_gt0 === true,
          y_hl_gt0: lots.y_hl_gt0 !== false,
        };
      }
    }
    const enter = rankPctToScore();
    return { rank_enter: enter, rank_strong: enter };
  }

  const FILL_CLOCK_KEY = "paper.replay.fill_clock";
  const FILL_CLOCKS = [
    "09:30",
    "09:35",
    "09:40",
    "09:45",
    "09:50",
    "09:55",
    "10:00",
  ];

  function clampFillClock(raw) {
    const s = String(raw || "")
      .replace("：", ":")
      .trim()
      .slice(0, 5);
    return FILL_CLOCKS.includes(s) ? s : "09:30";
  }

  function readFillClock() {
    const el = document.getElementById("quant-fill-clock");
    const v = clampFillClock(el && el.value);
    if (el && el.value !== v) el.value = v;
    try {
      localStorage.setItem(FILL_CLOCK_KEY, v);
    } catch (_err) {
      /* ignore */
    }
    return v;
  }

  function restoreFillClock(reqClock) {
    const el = document.getElementById("quant-fill-clock");
    let v = reqClock;
    if (v == null || v === "") {
      try {
        v = localStorage.getItem(FILL_CLOCK_KEY);
      } catch (_err) {
        v = "";
      }
    }
    if (el) el.value = clampFillClock(v);
  }

  const REPLAY_DESK_PREFS_KEY = "paper.replay.desk_prefs";

  function readReplayDeskPrefs() {
    try {
      const raw = localStorage.getItem(REPLAY_DESK_PREFS_KEY);
      if (!raw) return null;
      const o = JSON.parse(raw);
      return o && typeof o === "object" ? o : null;
    } catch (_err) {
      return null;
    }
  }

  function persistReplayDeskPrefs() {
    if (!isReplayDesk()) return;
    try {
      const p = readPortfolioBtParams();
      localStorage.setItem(
        REPLAY_DESK_PREFS_KEY,
        JSON.stringify({
          lookback: p.lookback,
          score_model_role: p.score_model_role,
          t0_score_model_role: readScoreModelRole("paper-t0-score-model-role"),
          fill_clock: p.fill_clock,
          universe_fit_tiers: p.universe_fit_tiers,
          initial_cash: p.initial_cash,
          lot_base: p.lot_base,
          lot_strong: p.lot_strong,
          price_space_gate: p.price_space_gate !== false,
        })
      );
    } catch (_err) {
      /* ignore */
    }
  }

  function restoreReplayDeskPrefs() {
    const prefs = readReplayDeskPrefs();
    if (!prefs) return false;
    applyPortfolioBtParams(prefs, { rules: false, desk: true });
    if (prefs.t0_score_model_role != null) {
      applyScoreModelRole("paper-t0-score-model-role", prefs.t0_score_model_role);
    }
    restoreFillClock(prefs.fill_clock);
    return true;
  }

  function readScoreModelRole(id) {
    const el = document.getElementById(id);
    const fallback = id === "paper-t0-score-model-role" ? "live" : "research";
    const v = String((el && el.value) || fallback).toLowerCase();
    return v === "live" ? "live" : "research";
  }

  function applyScoreModelRole(id, role) {
    const el = document.getElementById(id);
    if (!el || role == null || role === "") return;
    el.value = String(role).toLowerCase() === "live" ? "live" : "research";
  }

  function readPortfolioBtParams() {
    const lbEl = document.getElementById("quant-lookback");
    let lookback = 30;
    if (lbEl && lbEl.value !== "") {
      const n = Number(lbEl.value);
      if (Number.isFinite(n)) lookback = Math.max(10, Math.min(500, Math.round(n)));
    }
    return {
      lookback,
      score_model_role: readScoreModelRole("quant-score-model-role"),
      horizon_days: 1,
      exclude_st: true,
      min_avg_amount_pctile: null,
      benchmark_code: "000300",
      y_on_alpha: readYOnAlpha(),
      initial_cash: readInitialCash(),
      fill_clock: readFillClock(),
      universe_fit_tiers: readUniverseFitTiers(),
      price_space_gate: readPriceSpaceGate(),
      ...readReplayLots(),
      ...readFusionWeights(),
      ...readRankThresholds(),
    };
  }

  function renderAttributionTables(attr) {
    const el = document.getElementById("quant-attr-tables");
    if (!el) return;
    if (!attr || !attr.ok) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = renderAttributionTablesHtml(attr);
    void ensureFitTierMap(el);
  }

  function wireBtTradesScoreTips() {
    if (!els.quantBtTrades || els.quantBtTrades.dataset.scoreTipWired === "1") return;
    btSimScoreTips.bindHost(els.quantBtTrades, {
      scoreSelector:
        ".bt-trade-score[data-score-detail], .paper-hold-score[data-score-detail], .bt-stack-score[data-score-detail]",
    });
  }

  function renderBtTradesTable(dataOrTrades) {
    if (!els.quantBtTrades) return;
    const fillEl = document.getElementById("quant-signal-fill");
    if (fillEl) fillEl.innerHTML = "";
    const legs = resolveSimTradeLegs(dataOrTrades);
    const curve = !Array.isArray(dataOrTrades) && Array.isArray(dataOrTrades?.equity_curve)
      ? dataOrTrades.equity_curve
      : [];
    const ledger = isRankLotsLedger(legs, dataOrTrades);
    if (!legs.length && !(ledger && curveLedgerDays(curve).length)) {
      state.lastLedgerTrades = false;
      setBtTradesCaption("暂无成交流水 · 请先跑回测");
      return;
    }
    state.lastSimTrades = legs;
    state.lastLedgerTrades = ledger;
    const rowDeps = {
      nameByCode: state.watchingNameByCode,
      fmtPct,
      metricClass,
      fmtScore,
      scoreCls,
    };
    const cellDeps = { escapeHtml, watchingNameSpanHtml };
    if (ledger) {
      const filledLegs = legs.filter(
        (r) => String(r.status || "filled") !== "skipped"
      );
      const showLegs = legs;
      state.lastSimTrades = showLegs;
      if (!showLegs.length && !curveLedgerDays(curve).length) {
        setBtTradesCaption("暂无成交记录");
        return;
      }
      const rows = buildLedgerTradeRows(showLegs, {
        ...rowDeps,
        curve,
        metaLegs: legs,
        params:
          (!Array.isArray(dataOrTrades) &&
            (dataOrTrades.params || dataOrTrades.request)) ||
          {},
      });
      const skipNote = !filledLegs.length && legs.length
        ? `<p class="quant-trades-caption">暂无成交 · ${legs.length} 笔跳过（现金不足 / T+1 等）</p>`
        : "";
      els.quantBtTrades.innerHTML = skipNote + ledgerTradesCaptionHtml();
      const host = els.quantBtTrades.querySelector(".quant-bt-trades-host");
      state.btTradesTableApi = mountVirtualTable(host, {
        columns: BT_LEDGER_TRADE_COLS,
        emptyText: "暂无成交流水",
        rowHeight: 48,
        fit: "host",
        shrinkTracks: false,
        rootClass: "watching-react-grid bt-ledger-grid",
        bodyClass: "quant-bt-trades-body",
        compare: btTradesNumCompare,
        cellHtml: (col, d) => btTradesCellHtml(col, d, cellDeps),
        rowClass: btTradeRowClass,
        rowAttrs: btTradeRowAttrs,
      });
      bindBtTradesStockClicks(state.btTradesTableApi);
      state.btTradesTableApi.setRows(rows);
      void ensureFitTierMap(els.quantBtTrades);
      wireBtTradesScoreTips();
      return;
    }
    const filled = legs.filter((r) => (r.status || "filled") === "filled").length;
    const skipped = legs.length - filled;
    const showIntent = simTradesIntentDiffers(legs);
    const rows = buildSimTradeRows(legs, rowDeps);
    els.quantBtTrades.innerHTML = simTradesCaptionHtml({
      rowsLen: rows.length,
      filled,
      skipped,
      showIntent,
    });
    const host = els.quantBtTrades.querySelector(".quant-bt-trades-host");
    state.btTradesTableApi = mountVirtualTable(host, {
      columns: btSimTradeColumns(showIntent),
      emptyText: "暂无模拟成交记录",
      rowHeight: 38,
      fit: "host",
      rootClass: "watching-react-grid",
      bodyClass: "quant-bt-trades-body",
      compare: btTradesNumCompare,
      cellHtml: (col, d) => btTradesCellHtml(col, d, cellDeps),
      rowClass: btTradeRowClass,
      rowAttrs: btTradeRowAttrs,
    });
    bindBtTradesStockClicks(state.btTradesTableApi);
    state.btTradesTableApi.setRows(rows);
    void ensureFitTierMap(els.quantBtTrades);
    wireBtTradesScoreTips();
  }

  function renderCostAssumptions(ca) {
    const el = document.getElementById("quant-cost-assumptions");
    if (!el) return;
    if (!ca || !ca.ok) {
      el.innerHTML = ca && ca.reason
        ? `<p class="quant-attr-note">成本假设不可用：${escapeHtml(ca.reason)}</p>`
        : "";
      return;
    }
    const model =
      ca.model === "zero"
        ? "零成本（教学）"
        : ca.cost_mode === "turnover"
          ? "A股简化·按换手"
          : "A股简化";
    const slipLabel =
      ca.max_slippage_bps != null
        ? `${ca.base_slippage_bps ?? "—"}≤${ca.max_slippage_bps}`
        : String(ca.base_slippage_bps ?? "—");
    const turn =
      ca.turnover_cost_sum_pct != null && ca.cost_mode === "turnover"
        ? `${Number(ca.turnover_cost_sum_pct).toFixed(2)}%`
        : "—";
    el.innerHTML =
      researchGridHtml(
        [
          { id: "model", label: "成本模型", flex: true },
          { id: "commission", label: "佣金bps", widthPct: 12, num: true },
          { id: "stamp", label: "印花税bps(卖)", widthPct: 14, num: true },
          { id: "slip", label: "滑点bps", widthPct: 14, num: true },
          { id: "turn", label: "累计换手成本", widthPct: 14, num: true },
          { id: "rt", label: "示意往返%", widthPct: 12, num: true },
        ],
        [
          {
            model,
            commission: String(ca.commission_bps ?? "—"),
            stamp: String(ca.stamp_duty_bps_sell ?? "—"),
            slip: slipLabel,
            turn,
            rt: String(ca.round_trip_pct_on_100x100 ?? "—"),
          },
        ]
      ) + `<p class="quant-attr-note">${escapeHtml(ca.note || "")}</p>`;
  }

  function renderNeutralCompareTable(source, targetEl, opts = {}) {
    renderNeutralCompareTableHtml(source, targetEl || els.quantNeutralCompareTable, opts);
  }

  function renderPortfolioBacktestResult(data) {
    if (!data || !data.success) {
      applyReplayOverviewKpis(null);
      renderMetricCards(els.quantBtMetrics, []);
      renderRobustnessPanel(null);
      renderWfSlices(null);
      renderCostAssumptions(null);
      renderAttributionTables(null);
      renderRegimeBuckets(null);
      renderSignalFillTable(null);
      renderScoreIc(null);
      const expBtn = document.getElementById("quant-backtest-report-export");
      if (expBtn) expBtn.disabled = true;
      clearBtTradesTable();
      return;
    }
    const m = data.metrics || {};
    ctx.lastBacktestMetrics = {
      max_drawdown_pct: m.max_drawdown_pct,
      win_rate_pct: m.win_rate_pct,
      total_return_pct: m.total_return_pct,
    };
    applyReplayOverviewKpis(data);
    if (isReplayDesk()) {
      renderStockContribTable(data);
      renderBtTradesTable(data);
      return;
    }
    if (state.neutralCompareSource === "frozen") {
      renderBtScopeNote(
        BT_SCOPE_LIVE + " · 下方中性化对照仍为日报冻结，请点「中性化对照」刷新。",
        { warn: true }
      );
    } else {
      const bench = data.benchmark || {};
      const excessWarn =
        bench.warn_abs_pos_excess_neg
          ? " · ⚠ 绝对收益为正但超额为负（可能只是 beta/池涨）"
          : "";
      renderBtScopeNote(BT_SCOPE_LIVE + excessWarn, {
        warn: !!bench.warn_abs_pos_excess_neg,
      });
    }
    renderMetricCards(els.quantBtMetrics, buildPortfolioBacktestCards(data));
    renderRobustnessPanel(data);
    renderWfSlices(data.wf_slices);
    renderCostAssumptions(data.cost_assumptions);
    renderAttributionTables(data.attribution);
    renderRegimeBuckets(data.regime_buckets, data.macro_context_summary);
    renderScoreIc(data.score_ic);
    // 信号–成交已并入模拟成交账
    renderSignalFillTable(null);
    state.lastBacktestPack = {
      kind: "portfolio_backtest",
      exported_at: new Date().toISOString(),
      result: data,
    };
    const expBtn = document.getElementById("quant-backtest-report-export");
    if (expBtn) expBtn.disabled = false;

    renderBtTradesTable(data);
    renderStockContribTable(data);
  }

  function renderRegimeBuckets(rb, macroSummary) {
    const el = document.getElementById("quant-regime-buckets");
    if (!el) return;
    if (!rb || !rb.ok || !(rb.buckets || []).length) {
      el.innerHTML = rb && rb.reason
        ? `<p class="quant-attr-note">Regime 分桶不可用：${escapeHtml(rb.reason)}</p>`
        : "";
      return;
    }
    const hasMacro = !!(rb.macro_history_available || (macroSummary && macroSummary.ok));
    const macroStrip =
      macroSummary && macroSummary.ok
        ? `<p class="quant-trades-caption quant-macro-regime-strip">宏观对齐 ${escapeHtml(
            String(macroSummary.date_from || "")
          )}→${escapeHtml(String(macroSummary.date_to || ""))} · 海外科技均 ${fmtPct(
            macroSummary.avg_overseas_tech_1d_pct
          )} · A50 ${fmtPct(macroSummary.avg_a50_1d_pct)} · 流动性压力 ${(
            macroSummary.avg_liquidity_stress_score ?? "—"
          ).toString()}</p>`
        : hasMacro
          ? `<p class="quant-attr-note">宏观 history 已加载，本区间无重叠信号日</p>`
          : `<p class="quant-attr-note">宏观 history 未就绪 · 运行 macro_backfill</p>`;
    const cols = [
      { id: "regime", label: "Regime", flex: true },
      { id: "n", label: "笔数", widthPct: 12, num: true },
      { id: "ret", label: "均收益", widthPct: 16, num: true },
      { id: "win", label: "胜率", widthPct: 14, num: true },
    ];
    if (hasMacro) {
      cols.push(
        { id: "otech", label: "海外科技", widthPct: 14, num: true },
        { id: "a50", label: "A50", widthPct: 12, num: true }
      );
    }
    el.innerHTML =
      `<p class="quant-trades-caption">Regime 分桶 · 已标注 ${escapeHtml(
        String(rb.tagged_trades ?? 0)
      )} 笔</p>` +
      macroStrip +
      researchGridHtml(
        cols,
        (rb.buckets || []).map((b) => ({
          regime: b.regime || "—",
          n: String(b.trade_count ?? "—"),
          retText: fmtPct(b.avg_return_pct),
          retCls: metricClass(b.avg_return_pct),
          win: fmtPct(b.win_rate_pct),
          otechText: fmtPct(b.avg_overseas_tech_1d_pct),
          otechCls: metricClass(b.avg_overseas_tech_1d_pct),
          a50Text: fmtPct(b.avg_a50_1d_pct),
          a50Cls: metricClass(b.avg_a50_1d_pct),
        })),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          if (col.id === "win") return escapeHtml(d.win);
          if (col.id === "otech") return metricCell(d.otechText, d.otechCls);
          if (col.id === "a50") return metricCell(d.a50Text, d.a50Cls);
          return escapeHtml(d[col.id] ?? "—");
        }
      ) +
      `<p class="quant-attr-note">${escapeHtml(rb.note || "")}</p>`;
  }

  function renderScoreIc(sic) {
    const el = document.getElementById("quant-score-ic");
    const chartWrap = document.getElementById("quant-ic-chart-wrap");
    const chartHost = document.getElementById("quant-ic-chart");
    if (!el) return;
    if (!sic) {
      el.innerHTML = "";
      if (chartWrap) chartWrap.hidden = true;
      if (chartHost) chartHost.replaceChildren();
      return;
    }
    const { html, hideChart } = renderScoreIcHtml(sic);
    el.innerHTML = html;
    if (chartWrap) chartWrap.hidden = !!hideChart;
    if (hideChart && chartHost) chartHost.replaceChildren();
    else if (!hideChart) paintIcChart(sic);
  }

  function renderSignalFillTable(_rows) {
    const el = document.getElementById("quant-signal-fill");
    if (el) el.innerHTML = "";
  }

  function replayT0PaintEls() {
    return {
      metricsEl: els.quantT0Metrics || document.getElementById("paper-t0-metrics"),
      vizEl: els.quantT0Viz || document.getElementById("paper-t0-viz"),
      daysEl: els.quantT0Days || document.getElementById("paper-t0-days"),
      previewEl: null,
      tipCtrl: btSimScoreTips,
    };
  }

  function paintReplayT0(data) {
    renderPaperT0(replayT0PaintEls(), data);
    applyReplayT0Kpis(data);
    fillT0BtSizing(document.getElementById("paper-t0-form"), data);
    const req = (data && data.request) || {};
    const lbEl = document.getElementById("paper-t0-lookback");
    if (lbEl && req.lookback != null && Number.isFinite(Number(req.lookback))) {
      lbEl.value = String(req.lookback);
    }
    applyScoreModelRole(
      "paper-t0-score-model-role",
      req.score_model_role || data.score_model_role
    );
  }

  function renderT0BacktestResult(data) {
    paintReplayT0(data);
  }

  function renderWfSlices(wf) {
    const host = document.getElementById("quant-wf-slices");
    if (!host) return;
    if (!wf) {
      host.innerHTML = "";
      return;
    }
    if (!wf.ok && !(wf.folds || []).length) {
      host.innerHTML = `<p class="quant-trades-caption">Walk-forward：${escapeHtml(wf.reason || "不可用")}</p>`;
      return;
    }
    const folds = wf.folds || [];
    const mean = wf.mean_test_return_pct;
    const pos = wf.positive_test_folds;
    const meas = wf.measured_test_folds;
    const head =
      `<p class="quant-trades-caption">Walk-forward ${folds.length} 折` +
      (mean != null ? ` · 测试段均收益 ${Number(mean).toFixed(2)}%` : "") +
      (pos != null && meas != null ? ` · 正窗 ${pos}/${meas}` : "") +
      (wf.fail_folds ? ` · 失败 ${wf.fail_folds}` : "") +
      `</p>`;
    if (!folds.length) {
      host.innerHTML = head;
      return;
    }
    host.innerHTML =
      head +
      researchGridHtml(
        [
          { id: "fold", label: "折", widthPct: 8, num: true, center: true },
          { id: "range", label: "测试段", flex: true },
          { id: "ret", label: "测试收益", widthPct: 14, num: true },
          { id: "dd", label: "窗口回撤", widthPct: 14, num: true },
          { id: "trades", label: "交易", widthPct: 10, num: true },
          { id: "status", label: "状态", widthPct: 14, center: true },
        ],
        folds.map((f) => {
          const ret = f.test_return_pct;
          return {
            fold: String(f.fold ?? "—"),
            range: `${f.test_start_date || "—"} → ${f.test_end_date || "—"}`,
            retText: ret != null ? `${Number(ret).toFixed(2)}%` : "—",
            retCls: metricClass(ret),
            dd:
              f.max_drawdown_pct != null ? `${Number(f.max_drawdown_pct).toFixed(2)}%` : "—",
            trades: String(f.trade_count ?? "—"),
            status: f.ok ? "ok" : f.reason || "失败",
          };
        }),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          return escapeHtml(d[col.id] ?? "—");
        }
      );
  }

  function paintPortfolioBacktestFail(failText) {
    const text = String(failText || "回测失败");
    writePortfolioSummary(text, { error: true });
    renderPortfolioBacktestResult(null);
    paintPortfolioChart([], text);
  }

  function applyLivePortfolioResult(data, elapsed) {
    const summary = buildPortfolioBacktestSummaryText(data);
    const elapsedBit = elapsed ? ` · 耗时 ${elapsed}` : "";
    writePortfolioSummary(`${summary.text}${elapsedBit}`, { error: !!summary.warn });
    state.neutralCompareSource = null;
    renderNeutralCompareTable(null);
    renderPortfolioBacktestResult(data);
    paintPortfolioChart(
      data.equity_curve,
      "回测无足够交易点",
      (data.benchmark && data.benchmark.ok && data.benchmark.equity_curve) || null,
      (data.benchmark && data.benchmark.benchmark_label) || null
    );
    try {
      const curve =
        data.equity_curve_tail ||
        (Array.isArray(data.equity_curve) ? data.equity_curve.slice(-120) : []);
      if (Array.isArray(curve) && curve.length) {
        localStorage.setItem(
          "investment_northstar_last_backtest",
          JSON.stringify({ at: Date.now(), curve })
        );
      }
    } catch (_) {
      /* ignore */
    }
    persistReplayDeskPrefs();
  }

  function fmtBtElapsed(started) {
    const sec = Math.max(1, Math.round((Date.now() - Number(started || Date.now())) / 1000));
    return sec < 60
      ? `${sec}s`
      : `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, "0")}s`;
  }

  function jobStartedMs(job, fallback) {
    const n = Number(job && job.started_at);
    if (Number.isFinite(n) && n > 0) return n < 1e12 ? n * 1000 : n;
    return fallback;
  }

  function portfolioJobHint(job, ctx) {
    const msg = String((job && job.message) || "").trim();
    const cur = Number((job && job.current) || 0);
    const tot = Number((job && job.total) || 0);
    const bits = [];
    if (msg && msg !== "回测中…") bits.push(msg);
    if (Number.isFinite(tot) && tot > 1) {
      const pct = Math.max(0, Math.min(100, Math.round((Math.max(0, cur) / tot) * 100)));
      bits.push(`${Math.max(0, Math.round(cur))}/${Math.round(tot)}（${pct}%）`);
    }
    if (ctx) bits.push(ctx);
    return bits.filter(Boolean).join(" · ");
  }

  async function runPortfolioBacktest() {
    const watchN = Object.keys(state.watchingNameByCode || {}).length;
    const started = Date.now();
    let lastHint = "";
    const refreshBusy = () => {
      const hint = lastHint ? ` · ${lastHint}` : "";
      setQuantBtBusy(true, `回测中… ${fmtBtElapsed(started)}${hint}`);
    };
    refreshBusy();
    const tick = setInterval(refreshBusy, 1000);
    try {
      await ensureScoringFloors();
      const {
        lookback,
        score_model_role,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        y_on_alpha,
        fusion_w_oo,
        fusion_w_oc,
        fusion_w_trade,
        fusion_w_nowcast,
        rank_enter,
        rank_strong,
        rank_enter_alt,
        y_enter_enabled,
        y_enter_alt_enabled,
        y_oo_gt0,
        y_oc_gt0,
        y_hl_gt0,
        initial_cash,
        fill_clock,
        lot_base,
        lot_strong,
        universe_fit_tiers,
        price_space_gate,
      } = readPortfolioBtParams();
      const cashWan = Number.isFinite(Number(initial_cash))
        ? String(Number(initial_cash) / 10000).replace(/\.0$/, "")
        : "20";
      const roleLbl = score_model_role === "live" ? "执行" : "研究";
      const ctx = [watchN ? `${watchN}只` : "", `${lookback}日`, fill_clock, roleLbl, `本金${cashWan}万`]
        .filter(Boolean)
        .join(" · ");
      lastHint = ctx;
      refreshBusy();
      const payload = {
        lookback,
        score_model_role,
        apply_costs: true,
        fetch_fundamentals: false,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        y_on_alpha,
        fusion_w_oo: fusion_w_oo != null ? fusion_w_oo : fusion_w_trade,
        fusion_w_oc: fusion_w_oc != null ? fusion_w_oc : fusion_w_nowcast,
        fusion_w_trade: fusion_w_oo != null ? fusion_w_oo : fusion_w_trade,
        fusion_w_nowcast: fusion_w_oc != null ? fusion_w_oc : fusion_w_nowcast,
        rank_enter,
        rank_strong,
        rank_enter_alt,
        y_enter_enabled,
        y_enter_alt_enabled,
        y_oo_gt0: y_oo_gt0 === true,
        y_oc_gt0: y_oc_gt0 === true,
        y_hl_gt0: y_hl_gt0 !== false,
        initial_cash,
        fill_clock,
        lot_base,
        lot_strong,
        universe_fit_tiers,
        price_space_gate,
        include_benchmark: true,
      };
      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), 30000) : null;
      let res;
      try {
        res = await fetch("/api/quant/portfolio-backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: ctrl ? ctrl.signal : undefined,
        });
      } catch (err) {
        const aborted = Boolean(
          err && (err.name === "AbortError" || /abort/i.test(String(err)))
        );
        const failMsg = aborted
          ? "回测入队失败或连接中断。服务刚重启时请稍候刷新再试"
          : String((err && err.message) || err || "回测请求失败");
        paintPortfolioBacktestFail(failMsg);
        return { success: false, error: failMsg };
      } finally {
        if (timer) clearTimeout(timer);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const failText = buildPortfolioBacktestFailText(data, res.status);
        paintPortfolioBacktestFail(failText);
        return data;
      }
      if (data.background) {
        const jobId = data.job && data.job.id;
        const jobStart = jobStartedMs(data.job, started);
        try {
          const done = await awaitPortfolioBacktestJob(jobId, jobStart, ctx, (hint) => {
            lastHint = hint || ctx;
            refreshBusy();
          });
          await applyFinishedPortfolioJob(done, jobStart);
          return (done && done.result) || data;
        } catch (err) {
          const failMsg = String((err && err.message) || err || "回测失败");
          paintPortfolioBacktestFail(failMsg);
          return { success: false, error: failMsg };
        }
      }
      if (!data.success) {
        const failText = buildPortfolioBacktestFailText(data, res.status);
        paintPortfolioBacktestFail(failText);
        return data;
      }
      applyLivePortfolioResult(data, fmtBtElapsed(started));
      return data;
    } finally {
      clearInterval(tick);
      setQuantBtBusy(false);
    }
  }

  function setBtTradesCaption(text) {
    state.btTradesTableApi = null;
    state.lastSimTrades = [];
    state.lastLedgerTrades = false;
    if (els.quantBtTrades) {
      els.quantBtTrades.innerHTML = `<p class="quant-trades-caption">${escapeHtml(text)}</p>`;
    }
  }

  function setQuantBtBusy(busy, message) {
    state.btBusy = !!busy;
    const progress =
      document.getElementById("quant-bt-progress") || els.quantBtProgress;
    const textEl =
      document.getElementById("quant-bt-progress-text") || els.quantBtProgressText;
    if (progress) {
      progress.classList.toggle("is-busy", !!busy);
      progress.hidden = !busy;
      if (!busy) progress.removeAttribute("title");
      if (busy) {
        try {
          progress.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (_) {
          /* ignore */
        }
      }
    }
    if (textEl && message) textEl.textContent = message;
    for (const id of quantBtBusyIds) {
      const el = document.getElementById(id);
      if (el) el.disabled = !!busy;
    }
  }

  async function ensureScoringFloors() {
    if (state._scoringFloorsHydrated) return state.quantScoringFloors;
    try {
      const res = await fetch("/api/quant/strategies");
      const data = await res.json();
      const floors = data && data.scoring_floors;
      if (floors) {
        state.quantScoringFloors = mergeScoringFloors(state.quantScoringFloors, floors);
        state._scoringFloorsHydrated = true;
      }
    } catch (_) {
      /* 未水合则 payload 省略，后端用配置 */
    }
    return state.quantScoringFloors;
  }

  function portfolioBtScoreFloorPayload(rankMode) {
    return buildBtScoreFloorPayload(state.quantScoringFloors, rankMode);
  }

  function restoreLastT0Backtest() {
    const metricsEl = els.quantT0Metrics || document.getElementById("paper-t0-metrics");
    if (!isReplayDesk() || !metricsEl) return Promise.resolve();
    if (metricsEl.dataset.liveRun === "1") return Promise.resolve();
    setReplayT0Progress({ busy: false, message: "恢复上次结果…", lockBtn: false });
    return resumeReplayT0JobIfRunning()
      .then((resumed) => {
        if (resumed || metricsEl.dataset.liveRun === "1") return;
        return fetch("/api/quant/last-t0-backtest")
          .then((res) => res.json())
          .then((pack) => {
            if (!pack || pack.empty || !pack.result || !pack.result.success) {
              setReplayT0Progress({ busy: false, hide: true, lockBtn: false });
              return;
            }
            if (metricsEl.dataset.liveRun === "1") return;
            paintReplayT0(pack.result);
            setReplayT0Progress({ busy: false, hide: true, lockBtn: false });
          });
      })
      .catch(() => {
        setReplayT0Progress({ busy: false, hide: true, lockBtn: false });
      });
  }

  function collectReplayT0BacktestBody() {
    const lbEl = document.getElementById("paper-t0-lookback");
    let lookback = Number(lbEl && lbEl.value);
    if (!Number.isFinite(lookback) || lookback < 10) lookback = 10;
    lookback = Math.min(60, Math.max(10, lookback));
    const t0Form = document.getElementById("paper-t0-form");
    const pack = t0Form ? collectExecutionForm(t0Form) : null;
    const t0 = (pack && pack.t0) || {};
    const sizing = readT0BtSizing(t0Form);
    return {
      from_paper: true,
      lookback,
      use_minute: true,
      ...t0,
      score_model_role: readScoreModelRole("paper-t0-score-model-role"),
      t0_stop_on_close: true,
      initial_shares: sizing.shares,
      initial_cash: sizing.cash,
    };
  }

  function setReplayT0Progress({ busy, message, hide, lockBtn } = {}) {
    const progress = document.getElementById("paper-t0-progress");
    const textEl = document.getElementById("paper-t0-progress-text");
    const btn = document.getElementById("paper-t0-backtest");
    if (btn && lockBtn !== false) btn.disabled = !!busy;
    if (textEl && message != null) textEl.textContent = message;
    if (!progress) return;
    progress.classList.toggle("is-busy", !!busy);
    if (hide) {
      progress.hidden = true;
      progress.removeAttribute("title");
      return;
    }
    if (message) progress.title = message;
    progress.hidden = false;
  }

  let t0BtPollInflight = null;

  function fmtT0JobSec(ms) {
    const s = Math.max(1, Math.round(Number(ms) / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}分${String(r).padStart(2, "0")}秒`;
  }

  function sleepMs(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  let portfolioBtPollInflight = null;

  function portfolioJobPaintable(result) {
    return !!(result && result.success && !result.persisted_truncated);
  }

  async function fetchLastPortfolioSnapshotResult() {
    const pack = await fetch("/api/quant/last-portfolio-backtest").then((res) => res.json());
    if (pack && pack.result && pack.result.success) return pack.result;
    return null;
  }

  async function applyFinishedPortfolioJob(job, started) {
    const elapsed = fmtBtElapsed(started || Date.now());
    if (job && (job.status === "failed" || job.error) && !portfolioJobPaintable(job.result)) {
      paintPortfolioBacktestFail(job.error || "回测失败");
      return;
    }
    let data = portfolioJobPaintable(job && job.result) ? job.result : null;
    if (!data) {
      try {
        data = await fetchLastPortfolioSnapshotResult();
      } catch (_) {
        data = null;
      }
    }
    if (data && data.success) {
      applyLivePortfolioResult(data, elapsed);
      return;
    }
    paintPortfolioBacktestFail((job && job.error) || (data && data.error) || "回测失败");
  }

  async function pollPortfolioBacktestJob(jobId, started, ctx, onHint) {
    const pollStarted = started || Date.now();
    const absoluteCapMs = 40 * 60 * 1000;
    let netFailStreak = 0;
    let sawOwnJob = false;
    while (true) {
      if (Date.now() - pollStarted > absoluteCapMs) {
        throw new Error("回测超时（>40 分钟）。可先缩小回看窗，或等日线/分钟预热后再点");
      }
      let data = {};
      let ok = false;
      let status = 0;
      try {
        const res = await fetch("/api/jobs/portfolio-backtest?progress=1");
        status = res.status;
        data = await res.json().catch(() => ({}));
        ok = res.ok;
      } catch (_) {
        netFailStreak += 1;
        lastHintSafe(
          onHint,
          `服务短暂断开，重连中（${netFailStreak}）${ctx ? ` · ${ctx}` : ""}`
        );
        setQuantBtBusy(
          true,
          `回测中… ${fmtBtElapsed(pollStarted)} · 服务短暂断开，重连中（${netFailStreak}）`
        );
        await sleepMs(Math.min(4000, 500 * netFailStreak));
        continue;
      }
      if (!ok && (!status || status === 0 || status >= 500)) {
        netFailStreak += 1;
        lastHintSafe(
          onHint,
          `服务短暂断开，重连中（${netFailStreak}）${ctx ? ` · ${ctx}` : ""}`
        );
        setQuantBtBusy(
          true,
          `回测中… ${fmtBtElapsed(pollStarted)} · 服务短暂断开，重连中（${netFailStreak}）`
        );
        await sleepMs(Math.min(4000, 500 * netFailStreak));
        continue;
      }
      netFailStreak = 0;
      const job = (data && data.job) || {};
      const sameJob = !jobId || !job.id || job.id === jobId;
      if (sameJob && job.id) sawOwnJob = true;
      if (job.status === "idle" || !job.id) {
        if (sawOwnJob || Date.now() - pollStarted > 2500) {
          throw new Error("回测已中断（可能服务重启），请再点调仓回测");
        }
        await sleepMs(400);
        continue;
      }
      if (!sameJob) {
        throw new Error("调仓回测已被其它任务覆盖，请重试");
      }
      if (job.status === "failed") {
        throw new Error(job.error || "回测失败");
      }
      if (job.status === "done") {
        const full = await fetch("/api/jobs/portfolio-backtest").then((res) =>
          res.json().catch(() => ({}))
        );
        return (full && full.job) || job;
      }
      const hint = portfolioJobHint(job, ctx) || "回测中…";
      lastHintSafe(onHint, hint);
      setQuantBtBusy(true, `回测中… ${fmtBtElapsed(pollStarted)} · ${hint}`);
      await sleepMs(1200);
    }
  }

  function lastHintSafe(onHint, hint) {
    if (typeof onHint === "function") onHint(hint);
  }

  function awaitPortfolioBacktestJob(jobId, started, ctx, onHint) {
    if (portfolioBtPollInflight) return portfolioBtPollInflight;
    portfolioBtPollInflight = pollPortfolioBacktestJob(jobId, started, ctx, onHint).finally(
      () => {
        portfolioBtPollInflight = null;
      }
    );
    return portfolioBtPollInflight;
  }

  async function resumePortfolioBacktestJobIfRunning() {
    let tookBusy = false;
    try {
      const jr = await fetch("/api/jobs/portfolio-backtest?progress=1").then((res) =>
        res.json().catch(() => ({}))
      );
      const job = (jr && jr.job) || {};
      if (job.status !== "running" || !job.id) return false;
      const started = jobStartedMs(job, Date.now());
      tookBusy = true;
      setQuantBtBusy(true, job.message || "回测中…");
      const done = await awaitPortfolioBacktestJob(job.id, started, "");
      await applyFinishedPortfolioJob(done, started);
      return true;
    } catch (err) {
      paintPortfolioBacktestFail(String((err && err.message) || err || "回测中断"));
      return true;
    } finally {
      if (tookBusy) setQuantBtBusy(false);
    }
  }

  function t0JobPaintable(result) {
    return !!(
      result &&
      result.success &&
      !result.persisted_truncated
    );
  }

  async function fetchLastT0Snapshot() {
    const pack = await fetch("/api/quant/last-t0-backtest").then((res) => res.json());
    if (pack && pack.result && pack.result.success) return pack.result;
    return null;
  }

  async function applyFinishedT0Job(job) {
    const metricsEl = els.quantT0Metrics || document.getElementById("paper-t0-metrics");
    if (job && (job.status === "failed" || job.error) && !t0JobPaintable(job.result)) {
      setReplayT0Progress({
        busy: false,
        message: job.error || "回测失败",
      });
      renderT0BacktestResult(null);
      return;
    }
    let data = t0JobPaintable(job && job.result) ? job.result : null;
    if (!data) {
      try {
        data = await fetchLastT0Snapshot();
      } catch (_) {
        data = null;
      }
    }
    if (data && data.success) {
      if (metricsEl) metricsEl.dataset.liveRun = "1";
      paintReplayT0(data);
      setReplayT0Progress({ busy: false, hide: true });
      return;
    }
    setReplayT0Progress({
      busy: false,
      message: (job && job.error) || (data && data.error) || "回测失败",
    });
    renderT0BacktestResult(null);
  }

  async function pollT0BacktestJob(jobId) {
    const pollStarted = Date.now();
    const absoluteCapMs = 40 * 60 * 1000;
    let netFailStreak = 0;
    let sawOwnJob = false;
    while (true) {
      if (Date.now() - pollStarted > absoluteCapMs) {
        throw new Error("回测超时（>40 分钟）。可先在研究页预热 5m，或缩小回看窗后重试");
      }
      let data = {};
      let ok = false;
      let status = 0;
      try {
        const res = await fetch("/api/jobs/t0-backtest?progress=1");
        status = res.status;
        data = await res.json().catch(() => ({}));
        ok = res.ok;
      } catch (_) {
        netFailStreak += 1;
        setReplayT0Progress({
          busy: true,
          message: `回测中… ${fmtT0JobSec(Date.now() - pollStarted)} · 服务短暂断开，重连中（${netFailStreak}）`,
        });
        await sleepMs(Math.min(4000, 500 * netFailStreak));
        continue;
      }
      if (!ok && (!status || status === 0 || status >= 500)) {
        netFailStreak += 1;
        setReplayT0Progress({
          busy: true,
          message: `回测中… ${fmtT0JobSec(Date.now() - pollStarted)} · 服务短暂断开，重连中（${netFailStreak}）`,
        });
        await sleepMs(Math.min(4000, 500 * netFailStreak));
        continue;
      }
      netFailStreak = 0;
      const job = (data && data.job) || {};
      const sameJob = !jobId || !job.id || job.id === jobId;
      if (sameJob && job.id) sawOwnJob = true;
      if (job.status === "idle" || !job.id) {
        if (sawOwnJob || Date.now() - pollStarted > 2500) {
          throw new Error("回测已中断（可能服务重启），请再点做 T 回测");
        }
        await sleepMs(400);
        continue;
      }
      if (!sameJob) {
        throw new Error("做 T 回测已被其它任务覆盖，请重试");
      }
      if (job.status === "failed") {
        throw new Error(job.error || "回测失败");
      }
      if (job.status === "done") {
        const full = await fetch("/api/jobs/t0-backtest").then((res) => res.json().catch(() => ({})));
        return ((full && full.job) || job);
      }
      const hint = job.message || "回测中（持仓·5m/选向）…";
      setReplayT0Progress({
        busy: true,
        message: `回测中… ${fmtT0JobSec(Date.now() - pollStarted)} · ${hint}`,
      });
      await sleepMs(1200);
    }
  }

  function awaitT0BacktestJob(jobId) {
    if (t0BtPollInflight) return t0BtPollInflight;
    t0BtPollInflight = pollT0BacktestJob(jobId).finally(() => {
      t0BtPollInflight = null;
    });
    return t0BtPollInflight;
  }

  async function resumeReplayT0JobIfRunning() {
    try {
      const jr = await fetch("/api/jobs/t0-backtest?progress=1").then((res) =>
        res.json().catch(() => ({}))
      );
      const job = (jr && jr.job) || {};
      if (job.status !== "running" || !job.id) return false;
      setReplayT0Progress({ busy: true, message: job.message || "回测中…" });
      const done = await awaitT0BacktestJob(job.id);
      await applyFinishedT0Job(done);
      return true;
    } catch (err) {
      setReplayT0Progress({
        busy: false,
        message: String((err && err.message) || err || "回测中断"),
      });
      return true;
    }
  }

  function wirePaperT0Backtest() {
    const btn = document.getElementById("paper-t0-backtest");
    if (!btn || btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    const t0FetchErrorMessage = (err, timeoutHint) => {
      const name = String((err && err.name) || "");
      const msg = String((err && err.message) || err || "");
      const aborted = name === "AbortError" || /abort/i.test(msg);
      const network =
        name === "TypeError" ||
        /failed to fetch|networkerror|load failed|network request failed/i.test(msg);
      if (aborted || network) return timeoutHint;
      return msg || "请求失败";
    };
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      if (btn.disabled) return;
      setReplayT0Progress({ busy: true, message: "回测入队…" });
      const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer =
        ac &&
        setTimeout(() => {
          try {
            ac.abort();
          } catch (_) {
            /* ignore */
          }
        }, 30000);
      try {
        const body = collectReplayT0BacktestBody();
        const res = await fetch("/api/quant/t0-backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: ac ? ac.signal : undefined,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setReplayT0Progress({
            busy: false,
            message: data.error || data.detail || "回测入队失败",
          });
          renderT0BacktestResult(null);
          return;
        }
        if (data.background) {
          const jobId = data.job && data.job.id;
          const done = await awaitT0BacktestJob(jobId);
          await applyFinishedT0Job(done);
          return;
        }
        if (!data.success) {
          setReplayT0Progress({
            busy: false,
            message: data.error || data.detail || "回测失败",
          });
          renderT0BacktestResult(null);
          return;
        }
        const metricsEl = els.quantT0Metrics || document.getElementById("paper-t0-metrics");
        if (metricsEl) metricsEl.dataset.liveRun = "1";
        paintReplayT0(data);
        setReplayT0Progress({ busy: false, hide: true });
      } catch (err) {
        setReplayT0Progress({
          busy: false,
          message: t0FetchErrorMessage(
            err,
            "回测入队失败或连接中断。服务刚重启时请稍候刷新再试"
          ),
        });
      } finally {
        if (timer) clearTimeout(timer);
        btn.disabled = false;
      }
    });
  }

  function wireReplayFillClock() {
    const el = document.getElementById("quant-fill-clock");
    if (el && el.dataset.wired !== "1") {
      el.dataset.wired = "1";
      restoreFillClock();
      el.addEventListener("change", () => {
        readFillClock();
      });
    }
  }

  function wireReplayPriceSpaceGate() {
    const form = document.getElementById("paper-path-matrix-form");
    const el = form && form.querySelector('[name="pm_price_space_gate"]');
    if (!el || el.dataset.wired === "1") return;
    el.dataset.wired = "1";
    el.addEventListener("change", () => persistReplayDeskPrefs());
  }

  function wireReplayModelRole() {
    ["quant-score-model-role", "paper-t0-score-model-role"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el || el.dataset.wired === "1") return;
      el.dataset.wired = "1";
      el.addEventListener("change", () => persistReplayDeskPrefs());
    });
  }

  wirePaperT0Backtest();
  wireReplayFillClock();
  wireReplayPriceSpaceGate();
  wireReplayModelRole();
  if (typeof window !== "undefined") {
    window.addEventListener("investment-replay-desk-persist", () => persistReplayDeskPrefs());
  }
  initExecutionRuleForms()
    .then(() => restoreReplayDeskPrefs())
    .catch(() => {});

  return {
    clearBtTradesTable,
    downloadSimTradesCsv,
    fitReturnScoreModel,
    flattenTradesToSimLegs,
    formatFactorWeightsNote,
    formatSimStatus,
    formatSnapshotAt,
    loadLastBacktestSnapshot,
    restoreLastPortfolioBacktest,
    restoreLastT0Backtest,
    paintDualPortfolioChart,
    paintIcChart,
    paintNeutralCompareChart,
    paintPortfolioChart,
    portfolioBtScoreFloorPayload,
    readPortfolioBtParams,
    renderAttributionTables,
    renderBtTradesTable,
    renderCostAssumptions,
    renderNeutralCompareTable,
    renderPortfolioBacktestResult,
    renderRegimeBuckets,
    renderScoreIc,
    renderSignalFillTable,
    renderT0BacktestResult,
    renderWfSlices,
    runPortfolioBacktest,
    setBtTradesCaption,
    setQuantBtBusy,
  };
}
