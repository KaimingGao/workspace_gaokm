import { apiFetch } from "../api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart, renderNavBarChart } from "../lw_charts.js";
import { fmtScore, scoreCls } from "../paper/fmt.js";
import { renderT0Viz, wireT0SkipTips } from "../paper/t0_viz.js?v=p1924";
import { wireT0ProcessTips, wireT0DayDebugExpand } from "../paper/t0_table.js?v=p1986";
import { portfolioBtScoreFloorPayload as buildBtScoreFloorPayload, mergeScoringFloors } from "./scoring.js";
import { truncateStockName, watchingNameSpanHtml } from "./names.js";
import { downloadBlob } from "../shared.js";

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
  ledgerTradesCaptionHtml,
  buildLedgerTradesCsv,
} = await import(`./bt_trades.js?v=${encodeURIComponent(_V)}`);
const { renderNeutralCompareTable: renderNeutralCompareTableHtml } = await import(
  `./neutral_compare.js?v=${encodeURIComponent(_V)}`
);
const { buildUniversePanelHtml } = await import(
  `./universe_ui.js?v=${encodeURIComponent(_V)}`
);
const {
  fmtPct,
  metricClass,
  buildPortfolioBacktestSummaryText,
  buildPortfolioBacktestFailText,
  applyReplayOverviewKpis,
} = await import(`./bt_result.js?v=${encodeURIComponent(_V)}`);

/** Top-K 净值图横轴只展示最近 N 个自然日（含末日）。 */
export const TOPK_NAV_CHART_WINDOW_DAYS = 15;

/** Q1（灰）→ Qn（绿）；与 domain_watching 分层色板一致。 */
const Q_COLORS = [
  "#9ca3af",
  "#93c5fd",
  "#60a5fa",
  "#34d399",
  "#059669",
  "#047857",
  "#065f46",
];

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
  const { fmtPct, metricClass, renderMetricCards, renderBtScopeNote, renderFitGapPanel, renderRobustnessPanel, buildPortfolioBacktestCards, BT_SCOPE_LIVE, BT_SCOPE_FROZEN, readHorizonDays, quantBtBusyIds } = q;
  const { renderAttributionTablesHtml, renderIcEquityAlignHtml, renderQuantileTableHtml, buildT0BacktestMetrics, buildT0BacktestDaysHtml, buildCrossSectionResult, renderScoreIcHtml } = q;
  const { researchGridHtml, metricCell } = q;

  function buildIcAlignMarkers(align) {
    const periods = (align && align.ok && align.periods_tail) || [];
    const out = [];
    const seen = new Set();
    periods.forEach((p) => {
      const t = String(p.end_date || "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(t) || seen.has(t)) return;
      seen.add(t);
      const pos = p.bucket === "pos";
      out.push({
        time: t,
        position: "belowBar",
        color: pos ? "#059669" : "#9ca3af",
        shape: "circle",
        text: pos ? "+" : "−",
      });
    });
    return out.slice(-40);
  }

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
    const chip = document.getElementById("quant-bt-stock-chip");
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
    if (chip) {
      if (stock && (name || code)) {
        chip.hidden = false;
        chip.textContent = `日线 ${name || ""} ${code || ""} · 再点名称回到净值`.replace(
          /\s+/g,
          " "
        ).trim();
      } else {
        chip.hidden = true;
        chip.textContent = "";
      }
    }
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

  async function loadFitGapForBacktest(bt) {
    if (isReplayDesk()) return;
    const { ok, data } = await apiFetch("/api/ops/fit-gap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result: bt || {} }),
    });
    if (ok) renderFitGapPanel(data);
    else renderFitGapPanel(null);
  }

  function frozenBtCaliber(ps) {
    const p = (ps && ps.params) || {};
    const cost = ps && ps.cost_model === "zero" ? "零成本" : "含成本";
    const engine = String(p.engine || "");
    if (engine === "paper_replay" || p.rank_enter != null) {
      const lb = p.lookback != null ? p.lookback : "—";
      const alpha = p.y_on_alpha != null ? p.y_on_alpha : "—";
      const wt = p.fusion_w_trade != null ? p.fusion_w_trade : "—";
      const wn = p.fusion_w_nowcast != null ? p.fusion_w_nowcast : "—";
      const n = Number(p.rank_enter);
      const enter = Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
      return `lb${lb} · w${wt}/${wn} · α${alpha} · 入场${enter} · ${cost}`;
    }
    // 仅兼容旧日报冻结摘要（研究 Top-K 独立腿）
    return `K${p.top_k ?? "—"} · h${p.horizon_days ?? "—"} · ${cost}`;
  }

  function scoreToRankPct(score) {
    const n = Number(score);
    if (!Number.isFinite(n)) return "";
    return (n * 100).toFixed(1);
  }

  function applyPortfolioBtParams(req) {
    if (!req || typeof req !== "object") return;
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el || val == null || val === "") return;
      el.value = String(val);
    };
    if (req.lookback != null) setVal("quant-lookback", req.lookback);
    if (req.fusion_w_trade != null) setVal("quant-w-trade", req.fusion_w_trade);
    if (req.fusion_w_nowcast != null) setVal("quant-w-nowcast", req.fusion_w_nowcast);
    if (req.y_on_alpha != null) setVal("quant-on-alpha", req.y_on_alpha);
    const enterPct = scoreToRankPct(req.rank_enter);
    const strongPct = scoreToRankPct(req.rank_strong);
    if (enterPct) setVal("quant-rank-enter", enterPct);
    if (strongPct) setVal("quant-rank-strong", strongPct);
  }

  function markReplayRestored(at) {
    const meta = document.getElementById("replay-meta");
    if (meta) {
      meta.textContent = `历史验证 · rank_lots（09:30）· 上次结果 ${at} · 刷新未重跑`;
    }
  }

  async function restoreLastPortfolioBacktest() {
    if (!isReplayDesk()) return;
    if (state.btBusy) return;
    try {
      const res = await fetch("/api/quant/last-portfolio-backtest");
      const pack = await res.json();
      if (state.btBusy) return;
      if (!pack || pack.empty || !pack.result || !pack.result.success) return;
      const data = pack.result;
      const at = formatSnapshotAt(pack.saved_at);
      applyPortfolioBtParams(data.request || data.params);
      renderPortfolioBacktestResult(data);
      applyReplayOverviewKpis(data, { source: at ? `上次 ${at}` : "上次结果" });
      paintPortfolioChart(
        data.equity_curve,
        "回测无足够交易点",
        (data.benchmark && data.benchmark.ok && data.benchmark.equity_curve) || null,
        (data.benchmark && data.benchmark.benchmark_label) || null,
        data.ic_equity_align
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

  async function paintPortfolioChart(curve, emptyText, benchCurve, benchLabel, icAlign) {
    const host = els.quantPortfolioChart;
    if (!host) return;
    state.lastNavChart = {
      curve: Array.isArray(curve) ? curve : [],
      emptyText: emptyText || "",
      benchCurve: benchCurve || null,
      benchLabel: benchLabel || null,
      icAlign: icAlign || null,
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
    const markers = fullWindow ? [] : buildIcAlignMarkers(icAlign);
    const windowed = fullWindow ? (Array.isArray(curve) ? curve : []) : sliceCurveToDateWindow(curve);
    const windowedBench = fullWindow
      ? Array.isArray(benchCurve)
        ? benchCurve
        : []
      : sliceCurveToDateWindow(benchCurve);
    const winStart = _curvePointDate(windowed[0] || {});
    const pts = toPts(windowed);
    const bpts = toPts(windowedBench);
    const winMarkers =
      winStart && /^\d{4}-\d{2}-\d{2}$/.test(winStart)
        ? markers.filter((m) => String(m.time || "") >= winStart)
        : markers;
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
          : `rank_lots 净值（蓝）vs ${benchLabel || "基准"}（绿）。` +
            (markers.length
              ? `标记：绿点=正IC窗结束 · 灰点=非正IC窗。${axisHint}`
              : `起点 100；${axisHint}`) +
            "超额看指标卡，勿只看绝对累计。";
      }
      await renderDualLineChart(host, pts, bpts, {
        emptyText,
        disableZoom: !fullWindow,
        markers: winMarkers,
      });
      return;
    }
    if (legendEl) {
      legendEl.textContent = fullWindow
        ? `rank_lots 净值。起点 100。${axisHint}`
        : "rank_lots 净值（非研究独立腿）。起点 100。" +
          (markers.length
            ? " 标记：绿点=正IC窗结束 · 灰点=非正IC窗。"
            : " 单线=当次回测；对照时蓝=中性化、绿=未中性化ŷ。") +
          ` ${axisHint}`;
    }
    await renderLineChart(host, pts, { emptyText, disableZoom: !fullWindow, markers: winMarkers });
  }

  async function paintQuantileChart(qb) {
    const chartWrap = document.getElementById("quant-quantile-chart-wrap");
    const chartHost = document.getElementById("quant-quantile-chart");
    if (!chartHost || !chartWrap) return;
    try {
      const rows = (qb && qb.quantiles) || [];
      const series = rows
        .map((r, i) => {
          const curve = r.equity_curve_tail || r.equity_curve || [];
          if (!curve.length) return null;
          const n = rows.length;
          const isEdge = i === 0 || i === n - 1;
          return {
            label: r.label || `Q${r.quantile}`,
            color: Q_COLORS[Math.min(i, Q_COLORS.length - 1)],
            lineWidth: isEdge ? 2.5 : 1.5,
            points: curve.map((p, j) => {
              const t = String(p.date || "").slice(0, 10);
              return {
                time: /^\d{4}-\d{2}-\d{2}$/.test(t)
                  ? t
                  : new Date(Date.UTC(2020, 0, 1 + j)).toISOString().slice(0, 10),
                value: Number(p.equity ?? p.value),
              };
            }),
          };
        })
        .filter(Boolean);
      const ls = (qb && qb.long_short_equity_curve) || [];
      if (ls.length >= 2) {
        series.push({
          label: "Q高−Q低",
          color: "#b45309",
          lineWidth: 2.5,
          points: ls.map((p, j) => {
            const t = String(p.date || "").slice(0, 10);
            return {
              time: /^\d{4}-\d{2}-\d{2}$/.test(t)
                ? t
                : new Date(Date.UTC(2020, 0, 1 + j)).toISOString().slice(0, 10),
              value: Number(p.equity ?? p.value),
            };
          }),
        });
      }
      if (series.length < 2) {
        chartWrap.hidden = true;
        return;
      }
      chartWrap.hidden = false;
      await renderMultiLineChart(chartHost, series, {
        emptyText: "分层曲线不足",
        disableZoom: true,
      });
    } catch (_) {
      chartWrap.hidden = true;
    }
  }

  function readUnitWeight(id, fallback) {
    const el = document.getElementById(id);
    let v = fallback;
    if (el && el.value !== "") {
      const n = Number(el.value);
      if (Number.isFinite(n)) v = n;
    }
    return Math.round(Math.max(0, Math.min(1, v)) * 1000) / 1000;
  }

  function readYOnAlpha() {
    return readUnitWeight("quant-on-alpha", 0);
  }

  function readFusionWeights() {
    return {
      fusion_w_trade: readUnitWeight("quant-w-trade", 0.6),
      fusion_w_nowcast: readUnitWeight("quant-w-nowcast", 0.4),
    };
  }

  const RANK_PCT_DEFAULT = 1.2;

  /** 表单为百分数（1.2 → 1.2%）；引擎仍用 0.012。 */
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
    const enterEl = document.getElementById("quant-rank-enter");
    const strongEl = document.getElementById("quant-rank-strong");
    let enter = rankPctToScore(enterEl && enterEl.value);
    let strong = rankPctToScore(strongEl && strongEl.value);
    if (strong < enter) strong = enter;
    return { rank_enter: enter, rank_strong: strong };
  }

  function readPortfolioBtParams() {
    const lbEl = document.getElementById("quant-lookback");
    const wmEl = document.getElementById("quant-weight-mode");
    const dropEl = document.getElementById("quant-dropout-n");
    const stEl = document.getElementById("quant-exclude-st");
    const amtEl = document.getElementById("quant-min-amount-pctile");
    const benchEl = document.getElementById("quant-benchmark-code");
    let lookback = 30;
    let weightMode = "score_budget";
    let dropoutN = 0;
    let excludeSt = true;
    let minAvgAmountPctile = null;
    let benchmarkCode = "000300";
    if (lbEl && lbEl.value !== "") {
      const n = Number(lbEl.value);
      if (Number.isFinite(n)) lookback = Math.max(10, Math.min(500, Math.round(n)));
    }
    if (wmEl && wmEl.value) {
      const allowed = new Set(["equal", "score_budget", "risk_parity_lite"]);
      if (allowed.has(String(wmEl.value))) weightMode = String(wmEl.value);
    }
    if (dropEl && dropEl.value !== "") {
      const n = Number(dropEl.value);
      if (Number.isFinite(n)) dropoutN = Math.max(0, Math.min(10, Math.round(n)));
    }
    if (stEl) excludeSt = !!stEl.checked;
    if (amtEl && amtEl.value !== "") {
      const n = Number(amtEl.value);
      if (Number.isFinite(n) && n > 0) {
        minAvgAmountPctile = Math.max(0, Math.min(90, n));
      }
    }
    if (benchEl && benchEl.value) {
      const allowedB = new Set(["000300", "000905", "399006", "pool"]);
      if (allowedB.has(String(benchEl.value))) benchmarkCode = String(benchEl.value);
    }
    const rankMode = "predicted_score";
    return {
      lookback,
      horizon_days: 1,
      weight_mode: weightMode,
      dropout_n: dropoutN,
      exclude_st: excludeSt,
      min_avg_amount_pctile: minAvgAmountPctile,
      benchmark_code: benchmarkCode,
      rank_mode: rankMode,
      y_on_alpha: readYOnAlpha(),
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
  }

  function renderBtTradesTable(dataOrTrades) {
    if (!els.quantBtTrades) return;
    const fillEl = document.getElementById("quant-signal-fill");
    if (fillEl) fillEl.innerHTML = "";
    const legs = resolveSimTradeLegs(dataOrTrades);
    if (!legs.length) {
      state.lastLedgerTrades = false;
      setBtTradesCaption("暂无成交流水 · 请先跑回测");
      return;
    }
    state.lastSimTrades = legs;
    const ledger = isRankLotsLedger(legs, dataOrTrades);
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
      state.lastSimTrades = filledLegs;
      if (!filledLegs.length) {
        setBtTradesCaption("暂无成交记录");
        return;
      }
      const rows = buildLedgerTradeRows(filledLegs, rowDeps);
      els.quantBtTrades.innerHTML = ledgerTradesCaptionHtml();
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
    const csvBtn = document.getElementById("quant-bt-trades-csv");
    if (csvBtn) {
      csvBtn.addEventListener("click", () => downloadSimTradesCsv(state.lastSimTrades));
    }
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
    if (els.quantBtTrades.dataset.scoreTipWired !== "1") {
      btSimScoreTips.bindHost(els.quantBtTrades, {
        scoreSelector: ".bt-trade-score[data-score-detail], .paper-hold-score[data-score-detail]",
      });
    }
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

  function renderIcEquityAlign(align) {
    const el = document.getElementById("quant-ic-align");
    if (!el) return;
    el.innerHTML = renderIcEquityAlignHtml(align);
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
      renderUniversePanel(null);
      renderScoreIc(null);
      renderIcEquityAlign(null);
      renderQuantileTable(null);
      renderFitGapPanel(null);
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
    renderUniversePanel(data.universe);
    renderWfSlices(data.wf_slices);
    renderCostAssumptions(data.cost_assumptions);
    renderAttributionTables(data.attribution);
    renderRegimeBuckets(data.regime_buckets, data.macro_context_summary);
    renderScoreIc(data.score_ic);
    renderIcEquityAlign(data.ic_equity_align);
    renderQuantileTable(data.quantile_backtest);
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
  }

  function renderQuantileTable(qb) {
    const el = document.getElementById("quant-quantile-table");
    const chartWrap = document.getElementById("quant-quantile-chart-wrap");
    const chartHost = document.getElementById("quant-quantile-chart");
    if (!el) return;
    if (!qb) {
      el.innerHTML = "";
      if (chartWrap) chartWrap.hidden = true;
      if (chartHost) chartHost.replaceChildren();
      return;
    }
    const { html, showChart } = renderQuantileTableHtml(qb);
    el.innerHTML = html;
    if (chartWrap) chartWrap.hidden = !showChart;
    if (!showChart && chartHost) chartHost.replaceChildren();
    if (showChart) paintQuantileChart(qb);
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

  function renderT0BacktestResult(data) {
    if (!data || !data.success) {
      renderMetricCards(els.quantT0Metrics, []);
      if (els.quantT0Days) els.quantT0Days.innerHTML = "";
      renderT0Viz(els.quantT0Viz, null);
      return;
    }
    renderMetricCards(els.quantT0Metrics, buildT0BacktestMetrics(data));
    if (els.quantT0Days) els.quantT0Days.innerHTML = buildT0BacktestDaysHtml(data);
    renderT0Viz(els.quantT0Viz, data);
    if (els.quantT0Viz && btSimScoreTips) {
      wireT0SkipTips(els.quantT0Viz, btSimScoreTips);
    }
    if (els.quantT0Days && btSimScoreTips) {
      wireT0ProcessTips(els.quantT0Days, btSimScoreTips);
      wireT0DayDebugExpand(els.quantT0Days);
      if (els.quantT0Days.dataset.scoreTipWired !== "1") {
        btSimScoreTips.bindHost(els.quantT0Days, {
          scoreSelector:
            ".paper-t0-y-score[data-score-detail], .paper-t0-dir-score[data-score-detail]",
        });
      }
    }
  }

  function renderUniversePanel(uni) {
    const el = document.getElementById("quant-universe-panel");
    if (!el) return;
    el.innerHTML = buildUniversePanelHtml(uni);
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

  async function runPortfolioBacktest() {
    const watchN = Object.keys(state.watchingNameByCode || {}).length;
    const started = Date.now();
    const fmtElapsed = () => {
      const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
      return sec < 60
        ? `${sec}s`
        : `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, "0")}s`;
    };
    let busyBase = "回测中…";
    const refreshBusy = () => setQuantBtBusy(true, `${busyBase} ${fmtElapsed()}`);
    refreshBusy();
    const tick = setInterval(refreshBusy, 1000);
    try {
      await ensureScoringFloors();
      const {
        lookback,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        y_on_alpha,
        fusion_w_trade,
        fusion_w_nowcast,
        rank_enter,
        rank_strong,
      } = readPortfolioBtParams();
      busyBase =
        watchN >= 40
          ? `rank_lots 中（y_fuse/y_on · 09:30 · 观察池约 ${watchN} 只 · lookback 越大越慢）…`
          : `rank_lots 中（先读本地日线，缺的再补远端）…`;
      refreshBusy();
      const payload = {
        lookback,
        apply_costs: true,
        fetch_fundamentals: false,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        y_on_alpha,
        fusion_w_trade,
        fusion_w_nowcast,
        rank_enter,
        rank_strong,
        include_benchmark: true,
      };
      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const heavyRun = watchN >= 40 || lookback >= 90;
      const abortMs = heavyRun ? 1200000 : 480000;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), abortMs) : null;
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
          ? `回测超时未返回（满池 + lookback 长时常见，需 10～20 分钟）。可试：lookback 60，或等分组任务跑完再点。`
          : String((err && err.message) || err || "回测请求失败");
        writePortfolioSummary(failMsg, { error: true });
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "回测失败");
        return { success: false, error: failMsg };
      } finally {
        if (timer) clearTimeout(timer);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        writePortfolioSummary(buildPortfolioBacktestFailText(data, res.status), { error: true });
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "回测失败");
        return data;
      }
      const summary = buildPortfolioBacktestSummaryText(data);
      const elapsed = fmtElapsed();
      writePortfolioSummary(`${summary.text} · 耗时 ${elapsed}`, { error: !!summary.warn });
      // Top-K 单曲线与中性化对照无关：清掉日报/上次对照块，避免曲线前残留「绝对分…百分点」
      state.neutralCompareSource = null;
      renderNeutralCompareTable(null);
      renderPortfolioBacktestResult(data);
      paintPortfolioChart(
        data.equity_curve,
        "回测无足够交易点",
        (data.benchmark && data.benchmark.ok && data.benchmark.equity_curve) || null,
        (data.benchmark && data.benchmark.benchmark_label) || null,
        data.ic_equity_align
      );
      loadFitGapForBacktest(data).catch(() => {});

      // 北极星仪表化（前端缓存）：滚动夏普 / 卡玛 / TTM 等
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
    if (els.quantBtProgress) {
      els.quantBtProgress.hidden = !busy;
      els.quantBtProgress.classList.toggle("is-busy", !!busy);
      if (busy) {
        try {
          els.quantBtProgress.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (_) {
          /* ignore */
        }
      }
    }
    if (els.quantBtProgressText && message) els.quantBtProgressText.textContent = message;
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

  return {
    buildIcAlignMarkers,
    clearBtTradesTable,
    downloadSimTradesCsv,
    fitReturnScoreModel,
    flattenTradesToSimLegs,
    formatFactorWeightsNote,
    formatSimStatus,
    formatSnapshotAt,
    loadFitGapForBacktest,
    loadLastBacktestSnapshot,
    restoreLastPortfolioBacktest,
    paintDualPortfolioChart,
    paintIcChart,
    paintNeutralCompareChart,
    paintPortfolioChart,
    paintQuantileChart,
    portfolioBtScoreFloorPayload,
    readPortfolioBtParams,
    renderAttributionTables,
    renderBtTradesTable,
    renderCostAssumptions,
    renderIcEquityAlign,
    renderNeutralCompareTable,
    renderPortfolioBacktestResult,
    renderQuantileTable,
    renderRegimeBuckets,
    renderScoreIc,
    renderSignalFillTable,
    renderT0BacktestResult,
    renderUniversePanel,
    renderWfSlices,
    runPortfolioBacktest,
    setBtTradesCaption,
    setQuantBtBusy,
  };
}
