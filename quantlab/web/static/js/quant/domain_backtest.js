import { apiFetch } from "../api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart, renderNavBarChart } from "../lw_charts.js";
import { fmtScore, scoreCls } from "../paper/fmt.js";
import { renderPaperT0 } from "../paper/t0_ui.js?v=p3319";
import { repaintT0Portrait } from "../paper/t0_report.js?v=p3319";
import { mergeScoringFloors } from "./scoring.js";
import { watchingNameSpanHtml } from "./names.js";
import { ensureFitTierMap } from "./fit_tier_ui.js";
import { downloadBlob, escapeHtml } from "../shared.js";
import { collectPathMatrixForm, collectExecutionForm, readT0BtSizing, fillT0BtSizing } from "../paper/execution_ui.js";
import { initExecutionRuleForms } from "../paper/execution_forms.js";
import { TAU_END_CLOCKS, clampTauEndHm } from "./params.js";

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
const {
  fmtPct,
  metricClass,
  buildPortfolioBacktestSummaryText,
  buildPortfolioBacktestFailText,
  buildReplaySkipSummary,
  applyReplayOverviewKpis,
  applyReplayT0Kpis,
} = await import(`./bt_result.js?v=${encodeURIComponent(_V)}`);

/** Top-K 净值图横轴只展示最近 N 个自然日（含末日）。 */
const TOPK_NAV_CHART_WINDOW_DAYS = 15;

function _curvePointDate(p) {
  return String((p && (p.date || p.ts || p.time)) || "").slice(0, 10);
}

function sliceCurveToDateWindow(series, days = TOPK_NAV_CHART_WINDOW_DAYS) {
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
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText, btSimScoreTips, watchingNameFromEl, readHoldoutTradingDays } = q;
  const { fmtPct, metricClass, renderMetricCards, renderBtScopeNote, renderRobustnessPanel, buildPortfolioBacktestCards, BT_SCOPE_LIVE, BT_SCOPE_FROZEN, readHorizonDays, quantBtBusyIds } = q;
  const { renderAttributionTablesHtml, renderScoreIcHtml, renderReplayStockContribHtml } = q;
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

  function writeReplaySkipSummary(data) {
    const el = els.quantPortfolioSummary;
    if (!el || !isReplayDesk()) return;
    const text = buildReplaySkipSummary(data);
    el.hidden = !text;
    el.textContent = text;
    el.classList.remove("down");
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

  function formatSnapshotAt(iso) {
    if (!iso) return "—";
    const s = String(iso);
    return s.length > 19 ? s.slice(0, 19).replace("T", " ") : s.replace("T", " ");
  }

  function frozenBtCaliber(ps) {
    const p = (ps && ps.params) || {};
    const cost = ps && ps.cost_model === "zero" ? "零成本" : "含成本";
    const engine = String(p.engine || "");
    if (engine === "paper_replay" || p.fuse_enter != null) {
      const lb = p.lookback != null ? p.lookback : "—";
      const clock = p.fill_clock || "09:30";
      const alpha = p.fusion_w_co != null ? p.fusion_w_co : "—";
      const wt = p.fusion_w_oo != null ? p.fusion_w_oo : "—";
      const wn = p.fusion_w_oc != null ? p.fusion_w_oc : "—";
      const lotB = p.lot_base_amount != null ? p.lot_base_amount : "—";
      const n = Number(p.fuse_enter);
      const enter = Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : "—";
      const predOn = p.use_predictability_tiers === true;
      const predT = Array.isArray(p.predictability_tiers)
        ? p.predictability_tiers.join("")
        : "AB";
      const tierBit = predOn ? ` · 分档${predT}` : "";
      const psBit = p.price_space_gate === false ? " · 日分价关" : "";
      const role = String(p.score_model_role || "").toLowerCase() === "live" ? "执行" : "研究";
      return `lb${lb} · ${clock} · ${lotB}元 · w${wt}/${wn} · α${alpha} · 入场${enter}${tierBit}${psBit} · ${role} · ${cost}`;
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
      if (req.score_model_role != null) applyScoreModelRole("quant-score-model-role", req.score_model_role);
      if (req.fill_clock != null) setVal("quant-fill-clock", clampFillClock(req.fill_clock));
      if (req.initial_cash != null) setName("pm_initial_cash", req.initial_cash);
      if (req.lot_base_amount != null) setName("pm_lot_base_amount", req.lot_base_amount);
      if (req.lot_strong_amount != null) setName("pm_lot_strong_amount", req.lot_strong_amount);
      // 历史回测不再按拟合档过滤；只用观察池分档勾选
      if (Array.isArray(req.predictability_tiers)) {
        const set = new Set(
          req.predictability_tiers.map((t) => String(t || "").toUpperCase())
        );
        for (const [id, t] of [
          ["quant-pred-tier-a", "A"],
          ["quant-pred-tier-b", "B"],
          ["quant-pred-tier-c", "C"],
        ]) {
          const el = document.getElementById(id);
          if (el) el.checked = set.has(t);
        }
      } else if (req.use_predictability_tiers === false) {
        for (const id of ["quant-pred-tier-a", "quant-pred-tier-b", "quant-pred-tier-c"]) {
          const el = document.getElementById(id);
          if (el) el.checked = false;
        }
      }
      if (req.price_space_gate != null) {
        const el = form && form.querySelector('[name="pm_price_space_gate"]');
        if (el && el.type === "checkbox") el.checked = req.price_space_gate !== false;
      }
    }
    if (!rules) return;
    const clamp01 = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return null;
      return Math.max(0, Math.min(1, n));
    };
    const wOo = clamp01(req.fusion_w_oo);
    const wOc = clamp01(req.fusion_w_oc);
    if (wOo != null) {
      setName("pm_fusion_w_oo", wOo);
    }
    if (wOc != null) {
      setName("pm_fusion_w_nc", wOc);
    }
    if (req.fusion_w_co != null) {
      const n = Number(req.fusion_w_co);
      if (Number.isFinite(n)) setName("pm_fusion_w_co", Math.max(0, Math.min(10, n)));
    }
    const enterScore = req.fuse_enter;
    const strongScore =
      req.fuse_strong != null ? req.fuse_strong : enterScore;
    const enterPct = scoreToRankPct(enterScore);
    if (enterPct) {
      setName("pm_fuse_enter", enterPct);
    }
    const strongPct = scoreToRankPct(strongScore);
    if (strongPct) {
      setName("pm_fuse_strong", strongPct);
    }
    const setChk = (name, val, fallback) => {
      const el = form && form.querySelector(`[name="${name}"]`);
      if (!el || el.type !== "checkbox") return;
      el.checked = val != null ? !!val : fallback;
    };
    setChk("pm_y_oo_gt0", req.y_oo_gt0, false);
    setChk("pm_y_τc_gt0", req.y_τc_gt0, false);
    const ooMax = Number(req.oo_rank_max);
    if (Number.isFinite(ooMax) && ooMax > 0) {
      setName("pm_oo_rank_max", Math.max(0, Math.min(500, Math.round(ooMax))));
    } else {
      const ooFrac = Number(req.oo_rank_top_frac);
      setName(
        "pm_oo_rank_max",
        Number.isFinite(ooFrac) && ooFrac > 0 ? 10 : 0
      );
    }
  }

  function readPredictabilityTiers() {
    const out = [];
    for (const id of ["quant-pred-tier-a", "quant-pred-tier-b", "quant-pred-tier-c"]) {
      const el = document.getElementById(id);
      if (el && el.checked) out.push(String(el.value || "").toUpperCase());
    }
    return out;
  }

  let _livePredTierSyncTimer = null;

  /** 历史回测分档勾选 → live 调仓闸（空=关）。仅 /replay 桌同步，避免研究枢纽误关闸。 */
  function syncLivePredTiersFromDesk() {
    if (!isReplayDesk()) return;
    if (typeof apiFetch !== "function") return;
    if (!document.getElementById("quant-pred-tier-checks")) return;
    if (_livePredTierSyncTimer) {
      clearTimeout(_livePredTierSyncTimer);
      _livePredTierSyncTimer = null;
    }
    const tiers = readPredictabilityTiers();
    _livePredTierSyncTimer = setTimeout(() => {
      _livePredTierSyncTimer = null;
      void apiFetch("/api/quant/research-universe/predictability-tiers/live-sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allowed_tiers: tiers }),
      }).catch(() => {});
    }, 180);
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
        restoreReplayDeskPrefs();
        return;
      }
      const data = pack.result;
      const at = formatSnapshotAt(pack.saved_at);
      const req = data.request || data.params || {};
      const hasPrefs = !!readReplayDeskPrefs();
      // 上次回测只恢复净值/成交；表单以「保存」为准，勿用上次跑参盖掉已存配置
      applyPortfolioBtParams(req, { rules: false, desk: !hasPrefs });
      restoreReplayDeskPrefs();
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
        const meta = data.snapshot_meta || {};
        const at = formatSnapshotAt(meta.generated_at || data.generated_at);
        const summary = `日报冻结摘要 · ${at} · 累计 ${ps.total_return_pct ?? "—"}% · 胜率 ${ps.win_rate_pct ?? "—"}% · 交易 ${ps.trade_count ?? "—"}`;
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
        paintPortfolioChart(ps.equity_curve_tail, "无组合摘要曲线");
        applyReplayOverviewKpis(ps, { source: `冻结 ${at}` });
        setBtTradesCaption("日报仅含摘要曲线；点击「跑回测」加载完整模拟成交账");
      }
    } catch (_) {
      /* ignore */
    }
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
        : `rank_lots 净值（非研究独立腿）。起点 100。单线=当次回测。 ${axisHint}`;
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

  function clampReplayAmount(raw, fallback) {
    let v = fallback;
    if (raw != null && raw !== "") {
      const n = Number(raw);
      if (Number.isFinite(n)) v = n;
    }
    v = Math.round(Math.max(1000, Math.min(1000000, v)));
    return Math.round(v / 100) * 100;
  }

  function readReplayLots() {
    const form = document.getElementById("paper-path-matrix-form");
    const val = (name, fallback) => {
      const el = form && form.querySelector(`[name="${name}"]`);
      return clampReplayAmount(el && el.value, fallback);
    };
    let lot_base_amount = val("pm_lot_base_amount", 10000);
    let lot_strong_amount = val("pm_lot_strong_amount", lot_base_amount);
    if (lot_strong_amount < lot_base_amount) lot_strong_amount = lot_base_amount;
    const setIf = (name, v) => {
      const el = form && form.querySelector(`[name="${name}"]`);
      if (el && el.value !== String(v)) el.value = String(v);
    };
    setIf("pm_lot_base_amount", lot_base_amount);
    setIf("pm_lot_strong_amount", lot_strong_amount);
    return { lot_base_amount, lot_strong_amount };
  }

  function readPriceSpaceGate() {
    const form = document.getElementById("paper-path-matrix-form");
    const el = form && form.querySelector('[name="pm_price_space_gate"]');
    if (!el) return true;
    return !!el.checked;
  }

  function readFusionWCo() {
    const form = document.getElementById("paper-path-matrix-form");
    const el = form && form.querySelector('[name="pm_fusion_w_co"]');
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
          fusion_w_co: lots.fusion_w_co,
        };
      }
    }
    return {
      fusion_w_oo: 0.6,
      fusion_w_oc: 0.4,
      fusion_w_co: 1,
    };
  }

  const FUSE_PCT_DEFAULT = 0.1;

  /** 表单为百分数（0.1 → 0.1%）；引擎仍用 0.001。 */
  function fusePctToScore(raw, fallbackPct = FUSE_PCT_DEFAULT) {
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
        const ooMax =
          lots.oo_rank_max != null && Number(lots.oo_rank_max) > 0
            ? Math.max(1, Math.min(500, Math.round(Number(lots.oo_rank_max))))
            : null;
        const enter = lots.fuse_enter;
        const strong =
          lots.fuse_strong != null ? lots.fuse_strong : enter;
        return {
          fuse_enter: enter,
          fuse_strong: strong,
          y_oo_gt0: lots.y_oo_gt0 === true,
          y_τc_gt0: lots.y_τc_gt0 === true,
          oo_rank_max: ooMax,
          oo_rank_top_frac: null,
        };
      }
    }
    const enter = fusePctToScore();
    return {
      fuse_enter: enter,
      fuse_strong: enter,
      oo_rank_max: 10,
      oo_rank_top_frac: null,
    };
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
  const REPLAY_RUN_CTX_KEY = "paper.replay.run_ctx";

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
          score_backend: p.score_backend,
          t0_score_model_role: readScoreModelRole("paper-t0-score-model-role"),
          fill_clock: p.fill_clock,
          initial_cash: p.initial_cash,
          lot_base_amount: p.lot_base_amount,
          lot_strong_amount: p.lot_strong_amount,
          price_space_gate: p.price_space_gate !== false,
          use_predictability_tiers: !!p.use_predictability_tiers,
          predictability_tiers: p.predictability_tiers || [],
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
    syncLivePredTiersFromDesk();
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
    let lookback = 10;
    if (lbEl && lbEl.value !== "") {
      const n = Number(lbEl.value);
      if (Number.isFinite(n)) lookback = Math.max(10, Math.min(500, Math.round(n)));
    }
    const predTiers = readPredictabilityTiers();
    const usePred = predTiers.length > 0;
    const holdoutDays =
      typeof readHoldoutTradingDays === "function"
        ? Math.max(2, Math.min(60, Number(readHoldoutTradingDays("oo")) || 20))
        : 20;
    return {
      lookback,
      score_model_role: readScoreModelRole("quant-score-model-role"),
      score_backend: "ridge",
      horizon_days: 1,
      exclude_st: true,
      min_avg_amount_pctile: null,
      benchmark_code: "pool",
      fusion_w_co: readFusionWCo(),
      initial_cash: readInitialCash(),
      fill_clock: readFillClock(),
      use_predictability_tiers: usePred,
      predictability_tiers: usePred ? predTiers : null,
      // 勾了分档时把 ŷ_oo 卡片 Holdout 传给后端做一致性校验；回测天数仍用 lookback
      holdout_trading_days: usePred ? holdoutDays : null,
      predictability_head: "oo",
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
      const filledLegs = legs.filter((r) => {
        const st = String(r.status || "filled").toLowerCase();
        const act = String(r.action || r.matrix_action || "").toLowerCase();
        return st !== "skipped" && act !== "skip";
      });
      const showLegs = filledLegs;
      state.lastSimTrades = showLegs;
      if (!showLegs.length && !curveLedgerDays(curve).length) {
        const note = buildReplaySkipSummary(
          !Array.isArray(dataOrTrades) ? dataOrTrades : null
        );
        setBtTradesCaption(note || "暂无成交记录");
        return;
      }
      const rows = buildLedgerTradeRows(showLegs, {
        ...rowDeps,
        curve,
        metaLegs: showLegs,
        params:
          (!Array.isArray(dataOrTrades) &&
            (dataOrTrades.params || dataOrTrades.request)) ||
          {},
      });
      const skipSummary =
        !filledLegs.length && legs.length
          ? buildReplaySkipSummary(!Array.isArray(dataOrTrades) ? dataOrTrades : null)
          : "";
      const skipNote = skipSummary
        ? `<p class="quant-trades-caption">${escapeHtml(skipSummary)}</p>`
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

  function renderPortfolioBacktestResult(data) {
    const wfHost = document.getElementById("quant-wf-slices");
    if (wfHost) wfHost.innerHTML = "";
    if (!data || !data.success) {
      applyReplayOverviewKpis(null);
      renderMetricCards(els.quantBtMetrics, []);
      renderRobustnessPanel(null);
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
      writeReplaySkipSummary(data);
      renderStockContribTable(data);
      renderBtTradesTable(data);
      return;
    }
    {
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

  let lastReplayT0Data = null;

  function syncT0PortraitToPrefix() {
    const metricsEl = document.getElementById("paper-t0-metrics");
    if (!metricsEl || !lastReplayT0Data) return;
    repaintT0Portrait(metricsEl, lastReplayT0Data);
  }

  function paintReplayT0(data) {
    lastReplayT0Data = data && data.success ? data : null;
    renderPaperT0(replayT0PaintEls(), data);
    applyReplayT0Kpis(data);
    fillT0BtSizing(document.getElementById("paper-t0-form"), data);
    const req = (data && data.request) || {};
    applyScoreModelRole(
      "paper-t0-score-model-role",
      req.score_model_role || data.score_model_role
    );
  }

  function renderT0BacktestResult(data) {
    paintReplayT0(data);
  }

  function paintPortfolioBacktestFail(failText) {
    clearPortfolioRunContext();
    const text = String(failText || "回测失败");
    writePortfolioSummary(text, { error: true });
    renderPortfolioBacktestResult(null);
    paintPortfolioChart([], text);
  }

  function applyLivePortfolioResult(data, elapsed) {
    clearPortfolioRunContext();
    const summary = buildPortfolioBacktestSummaryText(data);
    const elapsedBit = elapsed ? ` · 耗时 ${elapsed}` : "";
    writePortfolioSummary(`${summary.text}${elapsedBit}`, { error: !!summary.warn });
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
          "quantlab_northstar_last_backtest",
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

  /** 调仓回测状态里的窗口 / 分档 / 成交钟 / 模型槽 / 本金。 */
  function portfolioRunContextLabel(params) {
    const p = params || readPortfolioBtParams();
    const cash = Number(p.initial_cash);
    const cashWan = Number.isFinite(cash)
      ? String(cash / 10000).replace(/\.0$/, "")
      : "20";
    const roleLbl = p.score_model_role === "live" ? "执行" : "研究";
    const tiers = Array.isArray(p.predictability_tiers) ? p.predictability_tiers : [];
    const usePred = p.use_predictability_tiers === true || tiers.length > 0;
    const tierBit = usePred ? `分档${tiers.join("") || "A"}` : "";
    const days = Number(p.lookback);
    return [
      Number.isFinite(days) && days > 0 ? `${Math.round(days)}日` : "",
      tierBit,
      p.fill_clock || "",
      roleLbl,
      `本金${cashWan}万`,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  function rememberPortfolioRunContext(ctx) {
    try {
      sessionStorage.setItem(REPLAY_RUN_CTX_KEY, String(ctx || ""));
    } catch (_) {
      /* ignore */
    }
  }

  function clearPortfolioRunContext() {
    try {
      sessionStorage.removeItem(REPLAY_RUN_CTX_KEY);
    } catch (_) {
      /* ignore */
    }
  }

  /** 刷新后接着看正在跑的任务：优先本次提交的文案，否则用已存桌面参数。逐日阶段用任务总日数对齐窗口。 */
  function recallPortfolioRunContext(job) {
    let saved = "";
    try {
      saved = String(sessionStorage.getItem(REPLAY_RUN_CTX_KEY) || "");
    } catch (_) {
      saved = "";
    }
    if (saved) return saved;
    const prefs = readReplayDeskPrefs();
    const label = portfolioRunContextLabel(prefs || undefined);
    const tot = Number(job && job.total);
    const msg = String((job && job.message) || "");
    if (!Number.isFinite(tot) || tot <= 1 || !/^\d{4}-\d{2}-\d{2}/.test(msg)) return label;
    const days = `${Math.round(tot)}日`;
    if (!label) return days;
    if (/^\d+日/.test(label)) return label.replace(/^\d+日/, days);
    return `${days} · ${label}`;
  }

  async function runPortfolioBacktest() {
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
        score_backend,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        fusion_w_co,
        fusion_w_oo,
        fusion_w_oc,
        fuse_enter,
        fuse_strong,
        y_oo_gt0,
        y_τc_gt0,
        oo_rank_max,
        initial_cash,
        fill_clock,
        lot_base_amount,
        lot_strong_amount,
        use_predictability_tiers,
        predictability_tiers,
        holdout_trading_days,
        price_space_gate,
      } = readPortfolioBtParams();
      if (use_predictability_tiers && holdout_trading_days != null) {
        const { ok: tierOk, data: tierLast } = await apiFetch(
          "/api/quant/research-universe/predictability-tiers/last?slim=1"
        );
        const lastHold =
          tierOk && tierLast && tierLast.success && tierLast.holdout_n != null
            ? Number(tierLast.holdout_n)
            : null;
        if (
          lastHold != null &&
          Number.isFinite(lastHold) &&
          Number(lastHold) !== Number(holdout_trading_days)
        ) {
          const failMsg =
            `ŷ_oo Holdout=${holdout_trading_days} 与枢纽分档 Holdout=${lastHold} 不一致；` +
            "请先在研究枢纽用当前 Holdout 重跑「观察池分档」再回测。";
          paintPortfolioBacktestFail(failMsg);
          return { success: false, error: failMsg };
        }
      }
      const enterThresh = fuse_enter;
      const strongThresh =
        fuse_strong != null ? fuse_strong : enterThresh;
      const ctx = portfolioRunContextLabel({
        lookback,
        score_model_role,
        fill_clock,
        initial_cash,
        use_predictability_tiers,
        predictability_tiers,
      });
      rememberPortfolioRunContext(ctx);
      persistReplayDeskPrefs();
      lastHint = ctx;
      refreshBusy();
      const payload = {
        lookback,
        score_model_role,
        score_backend,
        apply_costs: true,
        fetch_fundamentals: false,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        fusion_w_co,
        fusion_w_oo,
        fusion_w_oc,
        fuse_enter: enterThresh,
        fuse_strong: strongThresh,
        y_oo_gt0: y_oo_gt0 === true,
        y_τc_gt0: y_τc_gt0 === true,
        oo_rank_max:
          oo_rank_max != null && Number(oo_rank_max) > 0
            ? Math.max(1, Math.min(500, Math.round(Number(oo_rank_max))))
            : null,
        initial_cash,
        fill_clock,
        lot_base_amount,
        lot_strong_amount,
        use_predictability_tiers: !!use_predictability_tiers,
        predictability_tiers: use_predictability_tiers
          ? predictability_tiers || ["A"]
          : null,
        holdout_trading_days: use_predictability_tiers
          ? holdout_trading_days
          : null,
        predictability_head: "oo",
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
    const wasBusy = !!state.btBusy;
    const nowBusy = !!busy;
    state.btBusy = nowBusy;
    const progress =
      document.getElementById("quant-bt-progress") || els.quantBtProgress;
    const textEl =
      document.getElementById("quant-bt-progress-text") || els.quantBtProgressText;
    if (progress) {
      progress.classList.toggle("is-busy", nowBusy);
      progress.hidden = !nowBusy;
      if (!nowBusy) progress.removeAttribute("title");
      // 仅在刚进入 busy 时滚一次；轮询/秒表每秒刷新不得再 scrollIntoView，否则会锁住页面位置
      if (nowBusy && !wasBusy) {
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
      if (el) el.disabled = !!nowBusy;
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

  /** 做 T 回测 leg1 前缀终点：与 ŷ_τ* 拟合滑条同网格（params.TAU_END_CLOCKS）。 */
  const T0_BT_DEFAULT_LAST_LEG1_HM = "10:30";
  const T0_BT_LAST_LEG1_KEY = "paper.t0.last_leg1_hm";
  const T0_BT_DEFAULT_LAST_LEG1_IDX = Math.max(
    0,
    TAU_END_CLOCKS.indexOf(T0_BT_DEFAULT_LAST_LEG1_HM)
  );

  function paintT0BtLastLeg1Slider(hm) {
    const el = document.getElementById("paper-t0-last-leg1");
    if (!el) return;
    const clock = clampTauEndHm(hm, T0_BT_DEFAULT_LAST_LEG1_HM);
    const idx = Math.max(0, TAU_END_CLOCKS.indexOf(clock));
    el.value = String(idx);
    el.setAttribute("aria-valuenow", String(idx));
    el.setAttribute("aria-valuetext", clock);
    const lab = el.closest(".quant-tau-end-slider");
    if (lab) lab.classList.toggle("is-zero-prefix", clock === "09:30");
    const out = document.getElementById("paper-t0-last-leg1-val");
    if (out) {
      out.value = clock;
      out.textContent = clock;
    }
  }

  function readT0BtLastLeg1Hm({ persist } = {}) {
    const el = document.getElementById("paper-t0-last-leg1");
    let hm = T0_BT_DEFAULT_LAST_LEG1_HM;
    if (el) {
      const raw = Number(el.value);
      const idx = Math.max(
        0,
        Math.min(
          TAU_END_CLOCKS.length - 1,
          Math.round(Number.isFinite(raw) ? raw : T0_BT_DEFAULT_LAST_LEG1_IDX)
        )
      );
      hm = TAU_END_CLOCKS[idx] || T0_BT_DEFAULT_LAST_LEG1_HM;
    }
    hm = clampTauEndHm(hm, T0_BT_DEFAULT_LAST_LEG1_HM);
    paintT0BtLastLeg1Slider(hm);
    if (persist) {
      try {
        localStorage.setItem(T0_BT_LAST_LEG1_KEY, hm);
      } catch (_) {
        /* ignore */
      }
    }
    return hm;
  }

  function hydrateT0BtLastLeg1Slider() {
    let hm = T0_BT_DEFAULT_LAST_LEG1_HM;
    try {
      const saved = localStorage.getItem(T0_BT_LAST_LEG1_KEY);
      if (saved) hm = clampTauEndHm(saved, T0_BT_DEFAULT_LAST_LEG1_HM);
    } catch (_) {
      /* ignore */
    }
    paintT0BtLastLeg1Slider(hm);
    syncT0PortraitToPrefix();
    const el = document.getElementById("paper-t0-last-leg1");
    if (!el || el.dataset.wired === "1") return;
    el.dataset.wired = "1";
    const sync = () => {
      readT0BtLastLeg1Hm({ persist: true });
      syncT0PortraitToPrefix();
    };
    el.addEventListener("input", sync);
    el.addEventListener("change", sync);
  }

  /** 做 T 区块：真 header 对齐调仓；正文折叠（默认展开）。 */
  const T0_FOLD_OPEN_KEY = "paper.t0.fold.open";

  function wireT0SectionFold() {
    const sec = document.getElementById("replay-section-t0");
    const btn = document.getElementById("paper-t0-fold-toggle");
    const body = document.getElementById("replay-t0-fold-body");
    if (!sec || !btn || !body || btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    let open = true;
    try {
      if (localStorage.getItem(T0_FOLD_OPEN_KEY) === "0") open = false;
    } catch (_) {
      /* ignore */
    }
    const apply = (next) => {
      sec.classList.toggle("is-collapsed", !next);
      btn.setAttribute("aria-expanded", next ? "true" : "false");
      try {
        localStorage.setItem(T0_FOLD_OPEN_KEY, next ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
    };
    apply(open);
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      apply(btn.getAttribute("aria-expanded") !== "true");
    });
  }

  const T0_POOL_MAX = 8;
  const T0_POOL_KEY = "replay.t0.pool";

  function t0PoolCodeKey(raw) {
    let s = String(raw || "").trim().toUpperCase();
    if (s.includes(".")) s = s.split(".", 1)[0];
    return s;
  }

  function readReplayT0Pool() {
    try {
      const raw = JSON.parse(localStorage.getItem(T0_POOL_KEY) || "[]");
      if (!Array.isArray(raw)) return [];
      const out = [];
      const seen = new Set();
      raw.forEach((row) => {
        const code = t0PoolCodeKey(row && (row.code || row.stock_code));
        if (!code || seen.has(code) || out.length >= T0_POOL_MAX) return;
        seen.add(code);
        const name = String((row && (row.name || row.stock_name)) || "").trim();
        out.push({ code, name });
      });
      return out;
    } catch (_) {
      return [];
    }
  }

  function writeReplayT0Pool(pool) {
    const next = (pool || []).slice(0, T0_POOL_MAX);
    try {
      localStorage.setItem(T0_POOL_KEY, JSON.stringify(next));
    } catch (_) {
      /* ignore */
    }
    paintReplayT0Pool(next);
    return next;
  }

  function paintReplayT0Pool(pool) {
    const list = pool || readReplayT0Pool();
    const slots = document.getElementById("replay-t0-pool-slots");
    const count = document.getElementById("replay-t0-pool-count");
    if (count) count.textContent = `${list.length}/${T0_POOL_MAX}`;
    if (slots) slots.setAttribute("aria-label", `候选 ${list.length}/${T0_POOL_MAX}`);
    if (!slots) return;
    const seats = [];
    for (let i = 0; i < T0_POOL_MAX; i += 1) {
      const idx = String(i + 1).padStart(2, "0");
      const row = list[i];
      if (!row) {
        seats.push(
          `<span class="replay-t0-pool-slot is-empty" aria-hidden="true">` +
            `<span class="replay-t0-pool-slot-idx">${idx}</span>` +
            `</span>`
        );
        continue;
      }
      const name = row.name || row.code;
      seats.push(
        `<span class="replay-t0-pool-slot" data-code="${escapeHtml(row.code)}" title="${escapeHtml(name)} ${escapeHtml(row.code)}">` +
          `<span class="replay-t0-pool-slot-top">` +
          `<span class="replay-t0-pool-slot-idx">${idx}</span>` +
          `<span class="replay-t0-pool-slot-name">${escapeHtml(name)}</span>` +
          `</span>` +
          `<span class="replay-t0-pool-slot-code">${escapeHtml(row.code)}</span>` +
          `<button type="button" class="replay-t0-pool-slot-x" data-code="${escapeHtml(row.code)}" aria-label="移出 ${escapeHtml(name)}">×</button>` +
          `</span>`
      );
    }
    slots.innerHTML = seats.join("");
  }

  function addReplayT0PoolItem(code, name) {
    const key = t0PoolCodeKey(code);
    if (!key) return readReplayT0Pool();
    const pool = readReplayT0Pool();
    if (pool.some((row) => row.code === key)) return pool;
    if (pool.length >= T0_POOL_MAX) {
      setReplayT0Progress({
        busy: false,
        error: true,
        message: `候选池已满（${T0_POOL_MAX} 只）`,
      });
      return pool;
    }
    return writeReplayT0Pool(pool.concat([{ code: key, name: String(name || "").trim() }]));
  }

  function hideReplayT0PoolResults() {
    const box = document.getElementById("replay-t0-pool-results");
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
  }

  let t0PoolSearchSeq = 0;
  let t0PoolSearchTimer = null;

  function paintReplayT0PoolResults(items, query) {
    const box = document.getElementById("replay-t0-pool-results");
    if (!box) return;
    const list = Array.isArray(items) ? items : [];
    box.hidden = false;
    if (!list.length) {
      box.innerHTML = `<p class="replay-t0-pool-empty">未找到「${escapeHtml(query || "")}」</p>`;
      return;
    }
    const held = new Set(readReplayT0Pool().map((row) => row.code));
    box.innerHTML = list
      .map((it) => {
        const code = t0PoolCodeKey(it.stock_code || it.code);
        const name = String(it.stock_name || it.name || code);
        if (!code) return "";
        const taken = held.has(code);
        return (
          `<button type="button" class="replay-t0-pool-hit${taken ? " is-in" : ""}" role="option" data-code="${escapeHtml(code)}" data-name="${escapeHtml(name)}"${taken ? " disabled" : ""}>` +
          `<span class="replay-t0-pool-hit-code">${escapeHtml(code)}</span>` +
          `<span class="replay-t0-pool-hit-name">${escapeHtml(name)}</span>` +
          `<span class="replay-t0-pool-hit-mark">${taken ? "已入" : ""}</span>` +
          `</button>`
        );
      })
      .join("");
  }

  async function runReplayT0PoolSearch(raw) {
    const query = String(raw || "").trim();
    const box = document.getElementById("replay-t0-pool-results");
    if (!query) {
      hideReplayT0PoolResults();
      return;
    }
    const seq = ++t0PoolSearchSeq;
    if (box && box.hidden) {
      box.hidden = false;
      box.innerHTML = `<p class="replay-t0-pool-empty">搜索中…</p>`;
    }
    try {
      const res = await fetch(`/api/watching/search?q=${encodeURIComponent(query)}&limit=8`);
      const data = await res.json().catch(() => ({}));
      if (seq !== t0PoolSearchSeq) return;
      if (!res.ok) throw new Error(data.detail || res.statusText || "搜索失败");
      paintReplayT0PoolResults(data.items || [], query);
    } catch (err) {
      if (seq !== t0PoolSearchSeq) return;
      if (box) {
        box.hidden = false;
        box.innerHTML = `<p class="replay-t0-pool-empty">${escapeHtml(String((err && err.message) || err || "搜索失败"))}</p>`;
      }
    }
  }

  function wireReplayT0Pool() {
    const root = document.getElementById("replay-t0-pool");
    const input = document.getElementById("replay-t0-pool-input");
    if (!root || !input || root.dataset.wired === "1") return;
    root.dataset.wired = "1";
    paintReplayT0Pool(readReplayT0Pool());
    root.addEventListener("click", (e) => {
      const hit = e.target && e.target.closest && e.target.closest(".replay-t0-pool-hit:not(.is-in)");
      if (hit) {
        addReplayT0PoolItem(hit.getAttribute("data-code"), hit.getAttribute("data-name"));
        input.value = "";
        hideReplayT0PoolResults();
        input.focus();
        return;
      }
      const remove = e.target && e.target.closest && e.target.closest(".replay-t0-pool-slot-x");
      if (!remove) return;
      const code = t0PoolCodeKey(remove.getAttribute("data-code"));
      writeReplayT0Pool(readReplayT0Pool().filter((row) => row.code !== code));
    });
    input.addEventListener("input", () => {
      if (t0PoolSearchTimer) clearTimeout(t0PoolSearchTimer);
      t0PoolSearchTimer = setTimeout(() => {
        runReplayT0PoolSearch(input.value);
      }, 180);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        hideReplayT0PoolResults();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const hits = [...root.querySelectorAll(".replay-t0-pool-hit:not(.is-in)")];
        if (!hits.length) return;
        e.preventDefault();
        const cur = hits.findIndex((el) => el.classList.contains("is-active"));
        const next =
          e.key === "ArrowDown"
            ? hits[cur < 0 ? 0 : (cur + 1) % hits.length]
            : hits[cur <= 0 ? hits.length - 1 : cur - 1];
        hits.forEach((el) => el.classList.toggle("is-active", el === next));
        next.scrollIntoView({ block: "nearest" });
        return;
      }
      if (e.key !== "Enter") return;
      e.preventDefault();
      const first =
        root.querySelector(".replay-t0-pool-hit.is-active") ||
        root.querySelector(".replay-t0-pool-hit:not(.is-in)");
      if (first) {
        addReplayT0PoolItem(first.getAttribute("data-code"), first.getAttribute("data-name"));
        input.value = "";
        hideReplayT0PoolResults();
        return;
      }
      const typed = t0PoolCodeKey(input.value);
      if (/^\d{6}$/.test(typed)) {
        addReplayT0PoolItem(typed, "");
        input.value = "";
        hideReplayT0PoolResults();
      }
    });
    document.addEventListener("mousedown", (e) => {
      if (!root.contains(e.target)) hideReplayT0PoolResults();
    });
  }

  function collectReplayT0BacktestBody() {
    const lbEl = document.getElementById("paper-t0-lookback");
    let lookback = Number(lbEl && lbEl.value);
    if (!Number.isFinite(lookback) || lookback < 5) lookback = 10;
    lookback = Math.min(60, Math.max(5, lookback));
    const t0Form = document.getElementById("paper-t0-form");
    const pack = t0Form ? collectExecutionForm(t0Form) : null;
    const t0 = (pack && pack.t0) || {};
    const sizing = readT0BtSizing(t0Form);
    return {
      from_paper: true,
      codes: readReplayT0Pool().map((x) => ({ stock_code: x.code, stock_name: x.name })),
      lookback,
      use_minute: true,
      ...t0,
      t0_last_leg1_hm: readT0BtLastLeg1Hm({ persist: true }),
      score_model_role: readScoreModelRole("paper-t0-score-model-role"),
      t0_stop_on_close: true,
      initial_shares: sizing.shares,
      initial_cash: sizing.cash,
    };
  }

  function setReplayT0Progress({ busy, message, hide, lockBtn, error } = {}) {
    const progress = document.getElementById("paper-t0-progress");
    const textEl = document.getElementById("paper-t0-progress-text");
    const btn = document.getElementById("paper-t0-backtest");
    if (btn && lockBtn !== false) btn.disabled = !!busy;
    if (textEl && message != null) textEl.textContent = message;
    if (!progress) return;
    progress.classList.toggle("is-busy", !!busy);
    progress.classList.toggle("is-error", !!error);
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

  function jobUpdatedMs(job, fallback) {
    const n = Number(job && job.updated_at);
    if (Number.isFinite(n) && n > 0) return n < 1e12 ? n * 1000 : n;
    return fallback;
  }

  async function fetchPortfolioBacktestJobFull(progressJob) {
    const full = await fetch("/api/jobs/portfolio-backtest").then((res) =>
      res.json().catch(() => ({}))
    );
    return (full && full.job) || progressJob;
  }

  async function pollPortfolioBacktestJob(jobId, started, ctx, onHint) {
    const pollStarted = started || Date.now();
    // 满池冷缓存按日打分可达数小时；用「进度停滞」判超时，不用死板墙钟。
    const stallCapMs = 25 * 60 * 1000;
    const absoluteCapMs = 6 * 60 * 60 * 1000;
    let netFailStreak = 0;
    let sawOwnJob = false;
    let lastProgressMs = pollStarted;
    let lastProgressKey = "";
    while (true) {
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
        return await fetchPortfolioBacktestJobFull(job);
      }
      const progressKey = `${job.current || 0}|${job.total || 0}|${job.message || ""}`;
      if (progressKey !== lastProgressKey) {
        lastProgressKey = progressKey;
        lastProgressMs = jobUpdatedMs(job, Date.now());
      } else {
        lastProgressMs = Math.max(lastProgressMs, jobUpdatedMs(job, lastProgressMs));
      }
      const now = Date.now();
      const stalled = now - lastProgressMs > stallCapMs;
      const tooLong = now - pollStarted > absoluteCapMs;
      if (stalled || tooLong) {
        // 墙钟到了仍可能刚好 done：再拉一次再抛
        try {
          const again = await fetch("/api/jobs/portfolio-backtest?progress=1").then((res) =>
            res.json().catch(() => ({}))
          );
          const j2 = (again && again.job) || {};
          if (j2.status === "done" && (!jobId || !j2.id || j2.id === jobId)) {
            return await fetchPortfolioBacktestJobFull(j2);
          }
        } catch (_) {
          /* ignore */
        }
        throw new Error(
          stalled
            ? "回测进度停滞（>25 分钟无更新）。可先缩小回看窗，或等日线/分钟预热后再点"
            : "回测超时（>6 小时）。可先缩小回看窗，或等日线/分钟预热后再点"
        );
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
      if (!job.id) return false;
      // 前端曾超时放弃时，后台可能已 done：挂回结果，避免误报失败
      if (job.status === "done") {
        const full = await fetchPortfolioBacktestJobFull(job);
        if (portfolioJobPaintable(full && full.result)) {
          const started = jobStartedMs(full, Date.now());
          await applyFinishedPortfolioJob(full, started);
          return true;
        }
        return false;
      }
      if (job.status !== "running") return false;
      const started = jobStartedMs(job, Date.now());
      const ctx = recallPortfolioRunContext(job);
      tookBusy = true;
      setQuantBtBusy(
        true,
        `回测中… ${fmtBtElapsed(started)} · ${portfolioJobHint(job, ctx)}`
      );
      const done = await awaitPortfolioBacktestJob(job.id, started, ctx);
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
        error: true,
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
      error: true,
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
      const hint = job.message || "回测中（候选池·5m/选向）…";
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
        error: true,
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
      if (!readReplayT0Pool().length) {
        setReplayT0Progress({
          busy: false,
          error: true,
          message: `请先在候选池加入股票（最多 ${T0_POOL_MAX} 只）`,
        });
        return;
      }
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
            error: true,
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
            error: true,
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
          error: true,
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

  function wirePredTierChecks() {
    for (const id of ["quant-pred-tier-a", "quant-pred-tier-b", "quant-pred-tier-c"]) {
      const el = document.getElementById(id);
      if (!el || el.dataset.wired === "1") continue;
      el.dataset.wired = "1";
      el.addEventListener("change", () => {
        persistReplayDeskPrefs();
        syncLivePredTiersFromDesk();
      });
    }
  }

  hydrateT0BtLastLeg1Slider();
  wireT0SectionFold();
  wireReplayT0Pool();
  wirePaperT0Backtest();
  wireReplayFillClock();
  wireReplayPriceSpaceGate();
  wireReplayModelRole();
  wirePredTierChecks();
  if (typeof window !== "undefined") {
    window.addEventListener("quantlab-replay-desk-persist", () => persistReplayDeskPrefs());
  }
  initExecutionRuleForms()
    .then(() => {
      const restored = restoreReplayDeskPrefs();
      // 无本地偏好时也按当前勾选同步 live（默认 A）
      if (!restored) syncLivePredTiersFromDesk();
    })
    .catch(() => {});

  return {
    clearBtTradesTable,
    downloadSimTradesCsv,
    flattenTradesToSimLegs,
    formatFactorWeightsNote,
    formatSimStatus,
    formatSnapshotAt,
    loadLastBacktestSnapshot,
    restoreLastPortfolioBacktest,
    restoreLastT0Backtest,
    paintIcChart,
    paintPortfolioChart,
    readPortfolioBtParams,
    renderAttributionTables,
    renderBtTradesTable,
    renderCostAssumptions,
    renderPortfolioBacktestResult,
    renderRegimeBuckets,
    renderScoreIc,
    renderSignalFillTable,
    renderT0BacktestResult,
    runPortfolioBacktest,
    setBtTradesCaption,
    setQuantBtBusy,
  };
}
