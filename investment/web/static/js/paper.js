/** Paper dialog/page wiring. */
import {
  fmtMoney,
  fmtPriceUnit,
  escapeText,
  fmtPct,
  metricCls,
  fmtTableScore,
  scoreCls,
  resolveTradeScore,
  resolveEodScore,
  resolveTauScore,
  resolvePathScore,
  resolveOnScore,
  resolveNowcastScore,
  fmtPathScore,
  Y_EOD_TITLE,
  Y_TAU_TITLE,
  Y_PATH_TITLE,
  Y_ON_TITLE,
  Y_NOWCAST_TITLE,
  scoreSeriesStats,
  isHeuristicScoreScale,
} from "./paper/fmt.js?v=p1734";
import { drawSeries, appendLiveNavPoint } from "./paper/chart.js?v=p1163";
import { TRADE_TITLE } from "./quant/watching_quotes_ui.js?v=p1457";
import { renderOpsReport as renderOpsReportEl } from "./paper/ops_ui.js";
import {
  loadHoldingsSort,
  persistHoldingsSort as persistHoldingsSortSaved,
  sortHoldings as sortHoldingsRows,
} from "./paper/holdings_sort.js?v=p1457";
import {
  renderLineChart,
  loadLightweightCharts,
  disposeChart,
} from "./lw_charts.js";
import { apiFetch } from "./api_client.js";
import {
  buildPaperHoldingsTableHtml,
  buildPaperOriginBarHtml,
  buildPaperHoldActionBarHtml,
} from "./paper/holdings_ui.js?v=p1734";
import { renderPaperRulesHtml } from "./paper/rules_ui.js?v=p1658";
import {
  renderExecutionRulesHtml,
  fillExecutionForm,
  collectExecutionForm,
  fillPathMatrixForm,
  collectPathMatrixForm,
  persistT0Lookback,
  collectT0BacktestBody,
  resolveT0BacktestScope,
  normalizeExecutionView,
  renderExecutionDiffHtml,
} from "./paper/execution_ui.js?v=p1945";
import { buildPaperLogsView, buildPaperLogsCsv } from "./paper/logs_ui.js?v=p1658";
import { downloadBlob } from "./shared.js";
import {
  renderPaperT0 as renderPaperT0Ui,
  renderPaperT0Preview as renderPaperT0PreviewUi,
  renderPaperT0WorkerTrades as renderPaperT0WorkerTradesUi,
  renderPaperT0WorkerDesk as renderPaperT0WorkerDeskUi,
} from "./paper/t0_ui.js?v=p1924";
import { buildT0SummaryLine } from "./paper/t0_report.js?v=p1906";
import { wireT0SkipTips } from "./paper/t0_viz.js?v=p1924";
import { wireT0ProcessTips, wireT0DayDebugExpand } from "./paper/t0_table.js?v=p1945";
import { wireHoldingsChgTips } from "./paper/holding_chg_tip.js?v=p1924";
import { createHoldingsIslandController } from "./paper/holdings_island.js";
import { createClusterRebalanceController } from "./paper/cluster_rebalance.js?v=p1704";
import {
  getDataOfflineOnly,
  installDataOfflineToggle,
  offlineOnlyQuery,
} from "./data_offline.js";
import { rebalanceDataFoot, t0DataFoot } from "./data_policy.js?v=p1736";
import { ensureWarehouseTopup } from "./data_warehouse_topup.js";
import { createRebalanceReportController } from "./paper/rebalance_report.js?v=p1662";
import { waitPaperJob as waitPaperJobPoll } from "./paper/job_poll.js?v=p1416";
import { renderFollowNorthStar as renderFollowNorthStarUi } from "./paper/north_star_ui.js?v=p1416";
import {
  formatWeightSourceNote,
  formatFactorWeightsSection,
  formatFormulaTermsSection,
  formatScoreHero,
  formatRemScoreSection,
  formatBlendScoreSection,
  formatSentimentGateSection,
  formatMarketPriorSection,
  marketPriorDetailFields,
  tailAnomalyDetailFields,
  overheatDetailFields,
  createScoreTooltipController,
} from "./score_tooltip.js?v=p1737";

import { formatDailySteps, runDaily } from "./shared.js";

/** Paper dialog/page wiring. */
export function initPaper(ctx) {
  const page = document.body.dataset.page || "chat";
  const paperDialog = document.getElementById("paper-dialog");
  const paperMeta = document.getElementById("paper-meta");
  const paperStats = document.getElementById("paper-stats");
  const paperChart = document.getElementById("paper-chart");
  const paperChartFallback = document.getElementById("paper-chart-fallback");

  function paintPaperLine(points, { emptyText, ma, costLine, disableZoom } = {}) {
    const pts = points || [];
    const host = paperChart;
    const canvas = paperChartFallback;
    if (!host && !canvas) return Promise.resolve(null);

    // 宿主定高 + Shadow 隔离 LWC；CDN 失败再回退自绘 canvas
    if (host && host.tagName !== "CANVAS") {
      if (canvas) canvas.hidden = true;
      host.hidden = false;
      return loadLightweightCharts()
        .then(() => renderLineChart(host, pts, { emptyText, ma, disableZoom }))
        .catch((err) => {
          console.warn("[follow] Lightweight Charts 失败，回退 canvas", err);
          disposeChart(host);
          if (!canvas) return null;
          host.hidden = true;
          canvas.hidden = false;
          drawSeries(
            canvas,
            pts.map((p) => ({ x: p.time, y: p.value })),
            { emptyHint: emptyText, costLine }
          );
          return null;
        });
    }

    const c = host && host.tagName === "CANVAS" ? host : canvas;
    if (c) {
      c.hidden = false;
      drawSeries(
        c,
        pts.map((p) => ({ x: p.time, y: p.value })),
        { emptyHint: emptyText, costLine }
      );
    }
    return Promise.resolve(null);
  }
  let paperInitialized = false;
  let holdingsPage = 1;
  const HOLDINGS_PAGE_SIZE = 40;
  let lastSnapshots = [];
  let chartMode = "portfolio"; // portfolio | stock
  let chartStockCode = null;
  let lastAccountData = null;
  let paperLoadPromise = null;
  let pendingFocusCode = null;
  let selectedHoldCode = null;
  let paperLogShowAll = { trading: false, fund: false };
  let paperLogFilter = "all";
  let lastPaperTradingLogs = [];
  let holdingsGrid = null;
  let holdingsGridReady = false;
  const _sortInit = loadHoldingsSort();
  let holdingsSortKey = _sortInit.key;
  let holdingsSortDir = _sortInit.dir;
  /** code → 情绪徽章 HTML（与数据中心同源 API） */
  const holdingsSentimentHtmlByCode = Object.create(null);

  const holdingsIsland = createHoldingsIslandController({
    getAssetV: () =>
      (typeof window !== "undefined" && window.__ASSET_V__) || "p325",
    getSortState: () => ({ key: holdingsSortKey, dir: holdingsSortDir }),
    setSortState: (key, dir) => {
      holdingsSortKey = key;
      holdingsSortDir = dir;
    },
    persistSort: () => persistHoldingsSort(),
    onRowSelect: (el, opts) => selectHoldRow(el, opts),
    getRowContext: () => ({
      chartMode,
      chartStockCode,
      selectedHoldCode,
      pendingFocusCode,
    }),
    setSelectedHoldCode: (code) => {
      selectedHoldCode = code;
    },
    buildOriginBarHtml: buildPaperOriginBarHtml,
    buildActionBarHtml: buildPaperHoldActionBarHtml,
    getSentHtml: (code) => holdingsSentimentHtmlByCode[String(code || "").trim()] || null,
  });
  try {
    const params = new URLSearchParams(window.location.search || "");
    const fromUrl = String(params.get("code") || "").trim();
    if (fromUrl) pendingFocusCode = fromUrl;
  } catch (_) {
    /* ignore */
  }

  function focusPaperHolding(code) {
    pendingFocusCode = String(code || "").trim() || null;
    applyPendingFocus();
  }

  function syncHoldingsGridFlags({ focusCode = null } = {}) {
    if (!holdingsGrid || !holdingsGridReady) return;
    const rows = (holdingsGrid.getData() || []).map((row) => {
      const c = String(row.code || "");
      return {
        ...row,
        isAdjustActive: !!(selectedHoldCode && c === String(selectedHoldCode)),
        isChartActive: !!(
          chartMode === "stock" &&
          chartStockCode &&
          c === String(chartStockCode)
        ),
        isFocusHolding: !!(focusCode && c === String(focusCode)),
      };
    });
    holdingsGrid.setRows(rows);
  }

  function applyPendingFocus() {
    const code = String(pendingFocusCode || "").trim();
    if (!code) return false;
    const holdingsEl = document.getElementById("paper-holdings-table");
    if (!holdingsEl) return false;
    holdingsEl.querySelectorAll(".is-focus-holding").forEach((el) => {
      el.classList.remove("is-focus-holding");
    });
    const safe = String(code).replace(/"/g, "");
    let tr = holdingsEl.querySelector(`[data-code="${safe}"]`);
    if (!tr && holdingsGrid && holdingsGridReady) {
      const row = holdingsGrid.getRow(code);
      const d = row && row.getData();
      if (!d) return false;
      tr = {
        dataset: { code, shares: d.shares != null ? String(d.shares) : "" },
        querySelector(sel) {
          if (String(sel).includes("paper-wl-name-text")) {
            return {
              dataset: { fullName: d.name || "" },
              getAttribute() {
                return d.name || "";
              },
              textContent: d.name || code,
            };
          }
          return null;
        },
        scrollIntoView() {},
      };
    }
    if (!tr) return false;
    if (tr.classList) tr.classList.add("is-focus-holding");
    selectHoldRow(tr, { scroll: true, chart: true });
    syncHoldingsGridFlags({ focusCode: code });
    try {
      if (typeof tr.scrollIntoView === "function") {
        tr.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    } catch (_) {
      /* ignore */
    }
    pendingFocusCode = null;
    return true;
  }

  function selectHoldRow(tr, { scroll = false, chart = false } = {}) {
    const code = String((tr && tr.dataset.code) || "").trim();
    if (!code || !tr) return;
    selectedHoldCode = code;
    const holdingsEl = document.getElementById("paper-holdings-table");
    if (holdingsEl) {
      holdingsEl.querySelectorAll(".paper-hold-row").forEach((row) => {
        row.classList.toggle("is-adjust-active", row === tr || row.dataset.code === code);
      });
    }
    syncHoldingsGridFlags();
    const nameEl = tr.querySelector(".paper-wl-name-text");
    let name = nameEl
      ? nameEl.dataset.fullName || nameEl.getAttribute("title") || nameEl.textContent.trim()
      : code;
    let held = tr.dataset.shares || "";
    if (holdingsGrid && holdingsGridReady) {
      const d = holdingsGrid.getRow(code)?.getData();
      if (d) {
        if (!nameEl) name = d.name || code;
        if (!held && d.shares != null) held = String(d.shares);
      }
    }
    const bar = document.getElementById("paper-hold-action-bar");
    if (bar) {
      bar.hidden = false;
      bar.removeAttribute("hidden");
      const nameNode = bar.querySelector(".paper-hold-action-name");
      const codeNode = bar.querySelector(".paper-hold-action-code");
      if (nameNode) nameNode.textContent = name;
      if (codeNode) codeNode.textContent = code;
      bar.querySelectorAll("[data-code]").forEach((el) => {
        el.dataset.code = code;
      });
      const cutBtn = bar.querySelector(".paper-hold-cut");
      if (cutBtn) cutBtn.dataset.shares = held;
      const input = bar.querySelector(".paper-hold-shares");
      if (input && document.activeElement !== input) input.value = "";
    }
    const stockModeBtn = document.getElementById("paper-chart-stock-mode");
    if (stockModeBtn) {
      stockModeBtn.disabled = false;
      stockModeBtn.title = `${name} · 单票收盘价`;
    }
    if (chart) showStockChart(code, name);
    if (scroll) {
      try {
        tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) {
        /* ignore */
      }
    }
  }

  function clearHoldSelection() {
    selectedHoldCode = null;
    const holdingsEl = document.getElementById("paper-holdings-table");
    if (holdingsEl) {
      holdingsEl.querySelectorAll(".is-adjust-active").forEach((row) => {
        row.classList.remove("is-adjust-active");
      });
    }
    syncHoldingsGridFlags();
    const bar = document.getElementById("paper-hold-action-bar");
    if (bar) {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
    }
    // 取消选中时回到整账曲线，避免只剩微弱高亮、看起来像没反应
    if (chartMode === "stock") {
      showPortfolioChart();
    }
    setTradeStatus("已取消选中");
  }

  function persistHoldingsSort() {
    persistHoldingsSortSaved(holdingsSortKey, holdingsSortDir);
  }

  function setChartLabel(text) {
    const el = document.getElementById("paper-chart-label");
    if (el) el.textContent = text || "整账净值";
    const portfolioBtn = document.getElementById("paper-chart-portfolio");
    const stockBtn = document.getElementById("paper-chart-stock-mode");
    if (portfolioBtn) {
      portfolioBtn.classList.toggle("is-active", chartMode === "portfolio");
      // follow 页：按钮兼作模式切换，不再 hidden
      if (portfolioBtn.classList.contains("paper-chart-mode")) {
        portfolioBtn.hidden = false;
      } else {
        portfolioBtn.hidden = chartMode === "portfolio";
      }
    }
    if (stockBtn) {
      stockBtn.classList.toggle("is-active", chartMode === "stock");
      stockBtn.disabled = !chartStockCode && chartMode !== "stock";
    }
  }

  function setChartHeadStatus(text) {
    const el = document.getElementById("paper-chart-head-status");
    if (!el) return;
    const s = String(text || "").trim();
    el.hidden = !s;
    el.textContent = s;
  }

  function drawPaperChart(snapshots) {
    lastSnapshots = snapshots || [];
    if (chartMode !== "portfolio") return;
    const pts = lastSnapshots
      .map((s) => ({
        // 保留完整时间：同日多笔快照不能截成 YYYY-MM-DD（LWC 不允许重复 time）
        time: s.ts || s.date || "",
        value: Number(s.equity),
        live: !!s.live,
      }))
      .filter((p) => p.time && Number.isFinite(p.value));
    const liveEq = Number(
      lastAccountData && lastAccountData.summary && lastAccountData.summary.equity
    );
    const merged = appendLiveNavPoint(pts, liveEq);
    const hasLive = merged.length && merged[merged.length - 1].live;
    setChartLabel("整账净值");
    setChartHeadStatus(
      hasLive
        ? "末点按现价盯市，与总净值同一口径；历史点为成交/调仓快照"
        : ""
    );
    paintPaperLine(merged, {
      emptyText: merged.length
        ? "暂无足够数据"
        : "暂无净值快照 · 完成一笔买卖后会自动记录曲线",
      disableZoom: true,
    });
  }

  async function showStockChart(code, name) {
    if (!code) return;
    chartMode = "stock";
    chartStockCode = code;
    setChartHeadStatus("");
    setChartLabel(`${name || code} · 收盘价`);
    await paintPaperLine([], { emptyText: "加载日线…" });
    try {
      const { ok, data, error } = await apiFetch(
        `/api/paper/holding-chart?code=${encodeURIComponent(code)}&lookback=60`
      );
      if (!ok) throw new Error(error || data.detail || "加载失败");
      const pts = (data.points || [])
        .map((p) => ({
          time: String(p.date || "").slice(0, 10),
          value: Number(p.close),
        }))
        .filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.time) && Number.isFinite(p.value));
      const cost = Number(data.cost);
      setChartLabel(
        `${data.stock_name || name || code} · 收盘价` +
          (Number.isFinite(cost) && cost > 0 ? `（成本 ${cost}）` : "")
      );
      await paintPaperLine(pts, {
        emptyText: "暂无日线",
        ma: [5, 10, 20],
        costLine: Number.isFinite(cost) && cost > 0 ? cost : null,
        disableZoom: true,
      });
      document.querySelectorAll("#paper-holdings-table [data-code]").forEach((tr) => {
        tr.classList.toggle("is-chart-active", tr.dataset.code === code);
      });
      syncHoldingsGridFlags();
    } catch (err) {
      setChartLabel(`${name || code} · ${err.message || err}`);
      await paintPaperLine([], {
        emptyText: `日线加载失败：${String(err.message || err)}`,
      });
    }
  }

  function showPortfolioChart() {
    chartMode = "portfolio";
    // 保留 chartStockCode，方便再切回单票
    document.querySelectorAll("#paper-holdings-table .is-chart-active").forEach((tr) => {
      tr.classList.remove("is-chart-active");
    });
    syncHoldingsGridFlags();
    drawPaperChart(lastSnapshots);
  }

  const chartResetBtn = document.getElementById("paper-chart-portfolio");
  if (chartResetBtn && chartResetBtn.dataset.wired !== "1") {
    chartResetBtn.dataset.wired = "1";
    chartResetBtn.addEventListener("click", (e) => {
      e.preventDefault();
      showPortfolioChart();
    });
  }
  const chartStockModeBtn = document.getElementById("paper-chart-stock-mode");
  if (chartStockModeBtn && chartStockModeBtn.dataset.wired !== "1") {
    chartStockModeBtn.dataset.wired = "1";
    chartStockModeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      if (!chartStockCode && selectedHoldCode) chartStockCode = selectedHoldCode;
      if (!chartStockCode) {
        setTradeStatus("先点持仓行，再看单票曲线", { error: true });
        return;
      }
      const tr = document.querySelector(
        `#paper-holdings-table [data-code="${String(chartStockCode).replace(/"/g, "")}"]`
      );
      const nameEl = tr && tr.querySelector(".paper-wl-name-text");
      showStockChart(chartStockCode, nameEl ? nameEl.textContent.trim() : chartStockCode);
    });
  }

  const depositAmountEl = document.getElementById("paper-deposit-amount");
  const DEPOSIT_LS_KEY = "paper_deposit_amount";
  if (depositAmountEl) {
    // 默认始终 0；清掉历史记忆金额，避免上次注/减资残留
    try {
      localStorage.removeItem(DEPOSIT_LS_KEY);
    } catch (_) {}
    depositAmountEl.value = "0";
  }

  function readAdjustAmount() {
    const raw = depositAmountEl ? String(depositAmountEl.value || "").trim() : "";
    const amount = Number(raw);
    if (!Number.isFinite(amount) || amount <= 0) {
      setTradeStatus("请填写有效金额", { error: true });
      return null;
    }
    if (amount > 100_000_000) {
      setTradeStatus("单次不超过 1 亿", { error: true });
      return null;
    }
    return amount;
  }

  const depositBtn = document.getElementById("paper-deposit");
  if (depositBtn && depositBtn.dataset.wired !== "1") {
    depositBtn.dataset.wired = "1";
    depositBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const amount = readAdjustAmount();
      if (amount == null) return;
      try {
        setTradeStatus(`注资 ${amount.toLocaleString("zh-CN")} 中…`);
        await postPaperTrade("/api/paper/deposit", { amount });
      } catch (err) {
        setTradeStatus(String(err.message || err), { error: true });
      }
    });
  }

  const withdrawBtn = document.getElementById("paper-withdraw");
  if (withdrawBtn && withdrawBtn.dataset.wired !== "1") {
    withdrawBtn.dataset.wired = "1";
    withdrawBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const amount = readAdjustAmount();
      if (amount == null) return;
      try {
        setTradeStatus(`减资 ${amount.toLocaleString("zh-CN")} 中…`);
        await postPaperTrade("/api/paper/withdraw", { amount });
      } catch (err) {
        setTradeStatus(String(err.message || err), { error: true });
      }
    });
  }

  const resetBtn = document.getElementById("paper-reset");
  if (resetBtn && resetBtn.dataset.wired !== "1") {
    resetBtn.dataset.wired = "1";
    resetBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!window.confirm("确认回零？持仓与现金保留；累计盈亏与今日收益均从当前净值/现价重新起算（当日不再相对昨收）。")) {
        return;
      }
      try {
        setTradeStatus("回零中…");
        chartMode = "portfolio";
        chartStockCode = null;
        await postPaperTrade("/api/paper/reset", {});
        showPortfolioChart();
      } catch (err) {
        setTradeStatus(String(err.message || err), { error: true });
      }
    });
  }

  const costModelSel = document.getElementById("paper-cost-model");
  if (costModelSel && costModelSel.dataset.wired !== "1") {
    costModelSel.dataset.wired = "1";
    costModelSel.addEventListener("change", async () => {
      try {
        setTradeStatus("切换成本模型…");
        await postPaperTrade("/api/paper/cost-model", {
          cost_model: costModelSel.value,
        });
      } catch (err) {
        setTradeStatus(String(err.message || err), { error: true });
      }
    });
  }

  const openForm = document.getElementById("paper-open-form");
  if (openForm && openForm.dataset.wired !== "1") {
    openForm.dataset.wired = "1";
    openForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      setTradeStatus("请到观察页建仓来添加持仓", { error: true });
    });
  }

  // 带货币单位的金额：A股单位在后（383.01元），港美股单位在前（HK$42.1）。
  // 与观察页 quote_api 的 _fmt_price 规则保持一致。
  function renderPaperStats(summary) {
    const s = summary || {};
    const pnl = s.total_pnl_pct;
    const pnlCls =
      pnl == null || !Number.isFinite(Number(pnl)) || Number(pnl) === 0
        ? ""
        : Number(pnl) > 0
          ? "up"
          : "down";
    const dd =
      s.max_drawdown_pct != null && Number.isFinite(Number(s.max_drawdown_pct))
        ? `${Number(s.max_drawdown_pct).toFixed(1)}%`
        : "—";
    const compactItems = [
      ["净值", fmtMoney(s.equity), "", "现金 + 持仓市值"],
      ["现金", fmtMoney(s.cash), "", "可用现金余额"],
      [
        "盈亏",
        s.total_pnl_pct != null
          ? `${Number(s.total_pnl_pct) >= 0 ? "+" : ""}${Number(s.total_pnl_pct).toFixed(2)}%`
          : "—",
        pnlCls,
        `累计盈亏比例；历史最大回撤 ${dd}`,
      ],
      ["持仓", s.position_count != null ? `${s.position_count} 只` : "—", "", "当前持仓只数"],
    ];
    const followEl = document.getElementById("follow-stats");
    if (followEl) {
      followEl.innerHTML = compactItems
        .map(
          ([label, val, cls, title]) =>
            `<div class="paper-stat follow-stat" title="${escapeText(title || "")}">` +
            `<span class="label">${label}</span>` +
            `<span class="val ${cls}">${val}</span></div>`
        )
        .join("");
    }

    const paperEl = document.getElementById("paper-stats");
    if (paperEl) {
      const invested =
        s.invested_pct != null && Number.isFinite(Number(s.invested_pct))
          ? `${Number(s.invested_pct).toFixed(1)}%`
          : "—";
      const detailItems = [
        ["净值", fmtMoney(s.equity), ""],
        ["仓位", invested, ""],
        ["市值", fmtMoney(s.stock_value), ""],
        [
          "盈亏",
          s.total_pnl_pct != null
            ? `${Number(s.total_pnl_pct) >= 0 ? "+" : ""}${Number(s.total_pnl_pct).toFixed(2)}%`
            : "—",
          pnlCls,
        ],
        ["现金", fmtMoney(s.cash), ""],
        ["回撤", dd, Number(s.max_drawdown_pct) > 0 ? "down" : ""],
        ["持仓", s.position_count != null ? s.position_count : "—", ""],
      ];
      paperEl.innerHTML = detailItems
        .map(
          ([label, val, cls]) =>
            `<div class="paper-stat"><span class="label">${label}</span>` +
            `<span class="val ${cls}">${val}</span></div>`
        )
        .join("");
    }

    /* ── 研究枢纽风格 4 张 KPI 卡（id 在 value 节点上）── */
    function setKpi(id, val, sub, { cls = "", empty = false } = {}) {
      const el = document.getElementById(id);
      if (!el) return;
      const card = el.closest(".follow-kpi-card") || el;
      card.classList.toggle("is-empty", !!empty);
      const vEl =
        el.classList.contains("follow-kpi-value") || el.classList.contains("dashboard-kpi-value")
          ? el
          : card.querySelector(".follow-kpi-value, .dashboard-kpi-value");
      const sEl = card.querySelector(".follow-kpi-sub, .dashboard-kpi-sub");
      if (vEl) {
        vEl.textContent = val;
        vEl.classList.remove("up", "down");
        if (cls) vEl.classList.add(cls);
      }
      if (sEl && sub != null) sEl.textContent = sub;
    }
    const equity    = fmtMoney(s.equity);
    const cashTxt   = fmtMoney(s.cash);
    const stockTxt  = fmtMoney(s.stock_value);
    const hc = s.position_count != null ? `${s.position_count} 只` : "—";
    // 今日收益：优先 today_pnl_pct，其次 today_pnl 绝对额，都没就 —
    let todayVal = "—";
    let todayCls = "";
    let todaySub = "今日暂未结算";
    const todayBasis = String(s.today_pnl_basis || "prev_close");
    if (s.today_pnl_pct != null && Number.isFinite(Number(s.today_pnl_pct))) {
      const v = Number(s.today_pnl_pct);
      todayVal = `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
      todayCls = v === 0 ? "" : (v > 0 ? "up" : "down");
      if (todayBasis === "reset") {
        todaySub =
          s.today_pnl != null
            ? `自回零起 ${fmtMoney(s.today_pnl)}`
            : "相对回零价（已去掉昨收）";
      } else if (todayBasis === "prev_nav") {
        todaySub =
          s.today_pnl != null
            ? `相对昨收账本 ${fmtMoney(s.today_pnl)}`
            : "相对上一交易日净值";
      } else {
        todaySub = s.today_pnl != null
          ? `今日浮动 ${fmtMoney(s.today_pnl)}`
          : "按昨收对比今日现价估算";
      }
    } else if (s.today_pnl != null && Number.isFinite(Number(s.today_pnl))) {
      const v = Number(s.today_pnl);
      todayVal = fmtMoney(v);
      todayCls = v === 0 ? "" : (v > 0 ? "up" : "down");
      todaySub =
        todayBasis === "reset" ? "自回零起浮动" : "今日浮动盈亏（估算）";
    }
    const ddVal = s.max_drawdown_pct != null && Number.isFinite(Number(s.max_drawdown_pct))
      ? `${Number(s.max_drawdown_pct).toFixed(2)}%`
      : "—";
    const ddCls = s.max_drawdown_pct != null && Number(s.max_drawdown_pct) > 0 ? "down" : "";
    const ddEmpty = s.max_drawdown_pct == null || !Number.isFinite(Number(s.max_drawdown_pct));

    setKpi("paper-kpi-nav",      equity,    `现金 ${cashTxt} · 市值 ${stockTxt}`, { empty: s.equity == null });
    setKpi("paper-kpi-holdings", hc,        "当前账户活跃持仓",                     { empty: s.position_count == null });
    setKpi("paper-kpi-dd",       ddVal,     "期间历史最大回撤",                     { cls: ddCls, empty: ddEmpty });
    setKpi("paper-kpi-today",    todayVal,  todaySub,                               { cls: todayCls, empty: todayVal === "—" });

    const fundCash = document.getElementById("paper-fund-cash-text");
    if (fundCash) {
      fundCash.textContent =
        s.cash != null && Number.isFinite(Number(s.cash))
          ? `可用现金 ${cashTxt}`
          : "尚未加载 · 初始化后显示现金余额";
    }

    /* ── 路径 rail：默认高亮「3 调仓」；未初始化则「2 回测」── */
    document.querySelectorAll(".follow-path-step").forEach((st) => {
      const step = st.dataset.step;
      let active = step === "rebalance";
      if (!paperInitialized) active = step === "replay";
      st.classList.toggle("is-active", !!active);
      if (active) st.setAttribute("aria-current", "step");
      else st.removeAttribute("aria-current");
    });
  }

  function renderFollowNorthStar(ns) {
    renderFollowNorthStarUi(document.getElementById("follow-north-star"), ns);
  }

  const scoreTips = createScoreTooltipController();
  const hideScoreTooltip = () => scoreTips.hide();
  const showScoreTooltip = (cell, opts) => scoreTips.show(cell, opts);
  const showPlainTooltip = (anchor, text) => scoreTips.showPlain(anchor, text);

  function sortHoldings(list) {
    return sortHoldingsRows(list, holdingsSortKey, holdingsSortDir);
  }

  function destroyHoldingsGrid() {
    holdingsIsland.destroy();
    holdingsGrid = null;
    holdingsGridReady = false;
  }

  function renderHoldingsHtml(summary, holdings) {
    const built = buildPaperHoldingsTableHtml({
      summary,
      holdings,
      holdingsPage,
      holdingsPageSize: HOLDINGS_PAGE_SIZE,
      chartMode,
      chartStockCode,
      selectedHoldCode,
      pendingFocusCode,
      holdingsSortKey,
      holdingsSortDir,
      sentHtmlByCode: holdingsSentimentHtmlByCode,
    });
    holdingsPage = built.page;
    selectedHoldCode = built.selectedHoldCode;
    return built.originBarHtml + built.tableHtml + built.pagerHtml + built.actionBarHtml;
  }

  function applyHoldingsSentimentHtml(code, html) {
    const key = String(code || "").trim();
    if (!key || !html) return;
    holdingsSentimentHtmlByCode[key] = html;
    const holdingsEl = document.getElementById("paper-holdings-table");
    if (!holdingsEl) return;
    holdingsEl.querySelectorAll(`tr[data-code="${key}"] .paper-hold-sent`).forEach((cell) => {
      cell.innerHTML = html;
    });
  }

  function syncHoldingsSentimentToGrid() {
    if (!holdingsGrid || !holdingsGridReady) return;
    const rows = (holdingsGrid.getData() || []).map((row) => {
      const c = String(row.code || "").trim();
      const html = holdingsSentimentHtmlByCode[c];
      return html ? { ...row, sentHtml: html } : row;
    });
    holdingsGrid.setRows(rows);
  }

  async function fillHoldingsSentiment(holdings) {
    const codes = (holdings || [])
      .map((h) => String((h && h.stock_code) || "").trim())
      .filter(Boolean);
    if (!codes.length) return;
    const statusEl = document.getElementById("paper-holdings-load-status");
    const prev =
      statusEl && !statusEl.classList.contains("is-busy")
        ? String(statusEl.textContent || "").trim()
        : "";
    try {
      const V =
        (typeof window !== "undefined" && window.__ASSET_V__) || "p325";
      const mod = await import(`./holdings_table_island.js?v=${V}`);
      const res = await fetch(
        `/api/watching/sentiment?codes=${encodeURIComponent(codes.join(","))}&limit=3`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return;
      const byCode = {};
      for (const it of data.items || []) {
        const c = String((it && (it.stock_code || it.code)) || "").trim();
        if (c) byCode[c] = it;
      }
      codes.forEach((code) => {
        const row = byCode[code] || {};
        const html = mod.holdingSentimentHtml(row.sentiment || {}, code);
        applyHoldingsSentimentHtml(code, html);
      });
      syncHoldingsSentimentToGrid();
      if (prev && prev.startsWith("已刷新")) {
        setHoldingsLoadStatus(`${prev} · 情绪已更新`, { ok: true });
      }
    } catch (_) {
      /* 情绪填充失败时保留 … 占位 */
    }
  }

  async function upgradeHoldingsToIsland(summary, holdings) {
    await holdingsIsland.upgrade(summary, holdings);
    holdingsGrid = holdingsIsland.getGrid();
    holdingsGridReady = holdingsIsland.isReady();
    setTimeout(() => applyPendingFocus(), 0);
    const holdingsEl = document.getElementById("paper-holdings-table");
    if (!holdingsEl || !holdingsEl.querySelector(".paper-holdings-react-grid")) {
      throw new Error("island DOM missing");
    }
  }

  async function renderPaperAccountDetail(data) {
    lastAccountData = data || null;
    const holdingsEl = document.getElementById("paper-holdings-table");
    const rulesEl = document.getElementById("paper-rules-summary");
    const holdingsCountEl = document.getElementById("paper-holdings-count");
    const holdingsScoreStatsEl = document.getElementById("paper-holdings-score-stats");
    const paintHoldingsScoreStats = (rows) => {
      if (!holdingsScoreStatsEl) return;
      const list = Array.isArray(rows) ? rows : [];
      const eod = scoreSeriesStats(list.map(resolveEodScore));
      const trade = scoreSeriesStats(list.map(resolveTradeScore));
      const bit = (label, pack) =>
        pack.n
          ? `${label} μ ${Number(pack.mean).toFixed(2)}% · med ${Number(
              pack.median
            ).toFixed(2)}% · n=${pack.n}`
          : null;
      holdingsScoreStatsEl.textContent = [bit("ŷ_EOD", eod), bit("ŷ_trade", trade)]
        .filter(Boolean)
        .join(" · ");
    };
    if (!holdingsEl && !rulesEl) return;

    const summary = (data && data.summary) || {};
    const holdings = sortHoldings(summary.holdings || []);
    if (holdingsEl) {
      if (!data || !data.initialized) {
        destroyHoldingsGrid();
        holdingsEl.innerHTML = `<p class="watching-table-empty">账户未初始化</p>`;
        clearHoldSelection();
        if (holdingsCountEl) holdingsCountEl.textContent = "";
        paintHoldingsScoreStats([]);
      } else if (!holdings.length) {
        destroyHoldingsGrid();
        clearHoldSelection();
        if (holdingsCountEl) holdingsCountEl.textContent = "";
        paintHoldingsScoreStats([]);
        holdingsEl.innerHTML =
          `<div class="paper-holdings-empty">` +
          `<p class="paper-holdings-empty-title">暂无持仓</p>` +
          `<p class="paper-holdings-empty-text">本页只管理已有仓位；新仓请到观察页搜索建仓。</p>` +
          `<a class="dialog-btn" href="/watching" data-results-tab="watching">去观察建仓</a>` +
          `</div>`;
      } else {
        if (holdingsCountEl) holdingsCountEl.textContent = String(holdings.length);
        paintHoldingsScoreStats(holdings);
        holdingsEl.__pageSource = lastAccountData;
        // 先画原生表，再升级虚拟网格；失败保留原生表
        const html = renderHoldingsHtml(summary, holdings);
        holdingsEl.innerHTML = html;
        setTimeout(() => applyPendingFocus(), 0);
        try {
          await upgradeHoldingsToIsland(summary, holdings);
        } catch (err) {
          console.warn("[follow] 虚拟表升级跳过，保留原生表", err);
          destroyHoldingsGrid();
          holdingsEl.innerHTML = html;
          setTimeout(() => applyPendingFocus(), 0);
        }
        fillHoldingsSentiment(holdings).catch(() => {});
      }
    }

    if (rulesEl) rulesEl.innerHTML = renderPaperRulesHtml(data);

    const pathMatrixForm = document.getElementById("paper-path-matrix-form");
    const t0RulesEl = document.getElementById("paper-t0-rules");
    const t0Form = document.getElementById("paper-t0-form");
    let execution = data && data.execution;
    if (t0RulesEl) {
      if (execution && execution.ok !== false) {
        t0RulesEl.innerHTML = renderExecutionRulesHtml(execution);
        if (t0Form) fillExecutionForm(t0Form, execution);
        if (pathMatrixForm) fillPathMatrixForm(pathMatrixForm, execution);
      } else {
        t0RulesEl.innerHTML = `<p class="quant-sub">加载 ExecutionSpec…</p>`;
        // 旧进程未带 execution 字段时兜底拉专用接口
        apiFetch("/api/paper/execution")
          .then(({ ok, data: exe }) => {
            if (!ok || !exe || exe.ok === false) {
              t0RulesEl.innerHTML = renderExecutionRulesHtml(
                exe || { ok: false, error: "无法加载 ExecutionSpec（请重启 Web：python run_web.py）" }
              );
              return;
            }
            if (lastAccountData) lastAccountData.execution = exe;
            t0RulesEl.innerHTML = renderExecutionRulesHtml(exe);
            if (t0Form) fillExecutionForm(t0Form, exe);
            if (pathMatrixForm) fillPathMatrixForm(pathMatrixForm, exe);
          })
          .catch((err) => {
            t0RulesEl.innerHTML = renderExecutionRulesHtml({
              ok: false,
              error: String(err.message || err) + "（若刚升级请重启 Web）",
            });
          });
      }
    } else if (pathMatrixForm && execution && execution.ok !== false) {
      fillPathMatrixForm(pathMatrixForm, execution);
    }

    const logsView = buildPaperLogsView(data, paperLogShowAll, paperLogFilter);
    lastPaperTradingLogs = logsView.tradingLogs || [];
    const logEl = document.getElementById("paper-operation-log");
    if (logEl) {
      logEl.innerHTML = logsView.tradingHtml;
      // 同步研究枢纽风格卡头状态：交易条数 / 净交易额（若有）
      const headStatus = document.querySelector("#follow-ops-trades .follow-card-status");
      if (headStatus) {
        const cnt = logsView.tradingCount != null
          ? logsView.tradingCount
          : (logsView.tradingLogs || []).length;
        const total = logsView.tradingCountAll != null ? logsView.tradingCountAll : cnt;
        headStatus.textContent =
          paperLogFilter !== "all" && total !== cnt
            ? `筛选 ${cnt} 条 · 共 ${total} 条`
            : `共 ${cnt} 条`;
      }
    }
    renderT0LastRun(data);
    renderT0WorkerDetail(data);
    refreshT0RunPanel({ quiet: true }).catch(() => {});
    startT0RunPoll();
    const fundCard = document.getElementById("paper-fund-log-card");
    const fundEl = document.getElementById("paper-fund-log");
    if (fundCard && fundEl) {
      if (logsView.hasFundLogs) {
        fundCard.hidden = false;
        fundEl.innerHTML = logsView.fundHtml;
      } else {
        fundCard.hidden = true;
        fundEl.innerHTML = "";
      }
    }
  }

  function setPaperMetaText(text) {
    for (const id of ["paper-meta", "follow-meta"]) {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }
  }

  /** 开盘挂单摘要（盘前/收盘后确认调仓不会立刻改持仓）。 */
  function pendingOrdersBrief(data) {
    const po = data && data.pending_orders;
    if (!po || typeof po !== "object") return null;
    const legs = Array.isArray(po.legs) ? po.legs : [];
    if (!legs.length) return null;
    let buys = 0;
    let sells = 0;
    for (const leg of legs) {
      const side = String((leg && leg.side) || "").toLowerCase();
      if (side === "sell") sells += 1;
      else if (side === "buy") buys += 1;
    }
    const bits = [];
    if (sells) bits.push(`卖 ${sells}`);
    if (buys) bits.push(`买 ${buys}`);
    const target = String(po.target_fill_date || "").trim() || "开盘";
    const asOf = String(po.as_of || "").trim().slice(0, 10);
    const sameDay = asOf && target && asOf === target;
    return {
      n: legs.length,
      buys,
      sells,
      target,
      legs,
      asOf: po.as_of || po.staged_at || "",
      source: po.source || "",
      text: `挂开盘单 ${bits.join(" · ") || `${legs.length} 笔`} → ${target} 09:15–10:00 尝试成交` +
        (sameDay ? "（今日）" : ""),
    };
  }

  function renderPendingOrdersBanner(data) {
    const el = document.getElementById("paper-pending-orders");
    if (!el) return;
    const brief = pendingOrdersBrief(data);
    if (!brief) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    const cols =
      "56px 72px minmax(72px, 1.1fr) 64px 72px 84px 56px minmax(96px, 1.4fr)";
    const head = [
      ["方向", ""],
      ["代码", "watching-col-num"],
      ["名称", ""],
      ["股数", "watching-col-num"],
      ["意向价", "watching-col-num"],
      ["意向额", "watching-col-num"],
      ["ŷ", "watching-col-num"],
      ["备注", ""],
    ]
      .map(
        ([lab, cls]) =>
          `<div class="watching-react-grid-cell${cls ? ` ${cls}` : ""}">${escapeText(lab)}</div>`
      )
      .join("");
    const body = [...brief.legs]
      .sort((a, b) => {
        const sa = String((a && a.side) || "");
        const sb = String((b && b.side) || "");
        if (sa !== sb) return sa.localeCompare(sb);
        return String((a && a.stock_code) || "").localeCompare(
          String((b && b.stock_code) || "")
        );
      })
      .map((leg) => {
        const side = String((leg && leg.side) || "").toLowerCase();
        const sideLbl = side === "sell" ? "卖" : side === "buy" ? "买" : side || "—";
        const sideCls = side === "sell" ? "is-sell" : side === "buy" ? "is-buy" : "";
        const code = String((leg && (leg.stock_code || leg.code)) || "—");
        const name = String((leg && (leg.stock_name || leg.name)) || "");
        const shares = Number(leg && leg.shares);
        const px = Number(leg && (leg.intent_price ?? leg.price));
        const amt =
          Number.isFinite(shares) && Number.isFinite(px) ? shares * px : null;
        const note = String(
          (leg && (leg.skip_reason || leg.note)) || ""
        ).trim();
        const score =
          leg && leg.score != null && Number.isFinite(Number(leg.score))
            ? Number(leg.score)
            : null;
        const cells = [
          `<div class="watching-react-grid-cell follow-pending-side ${sideCls}">${escapeText(sideLbl)}</div>`,
          `<div class="watching-react-grid-cell watching-col-num follow-pending-code">${escapeText(code)}</div>`,
          `<div class="watching-react-grid-cell" title="${escapeText(name)}">${escapeText(name || "—")}</div>`,
          `<div class="watching-react-grid-cell watching-col-num">${Number.isFinite(shares) ? escapeText(String(Math.round(shares))) : "—"}</div>`,
          `<div class="watching-react-grid-cell watching-col-num">${Number.isFinite(px) ? escapeText(px.toFixed(2)) : "—"}</div>`,
          `<div class="watching-react-grid-cell watching-col-num">${amt != null ? escapeText(Math.round(amt).toLocaleString("zh-CN")) : "—"}</div>`,
          `<div class="watching-react-grid-cell watching-col-num">${score != null ? escapeText(score.toFixed(2)) : "—"}</div>`,
          `<div class="watching-react-grid-cell follow-pending-note" title="${escapeText(note)}">${escapeText(note || "—")}</div>`,
        ].join("");
        return (
          `<div class="watching-react-grid-row ${sideCls}" style="grid-template-columns:${cols}">` +
          cells +
          `</div>`
        );
      })
      .join("");
    const bits = [];
    if (brief.sells) bits.push(`卖 ${brief.sells}`);
    if (brief.buys) bits.push(`买 ${brief.buys}`);
    el.hidden = false;
    el.innerHTML =
      `<div class="follow-ops-subhead">` +
      `<span class="follow-ops-subtitle">开盘挂单` +
      (bits.length ? ` · ${bits.join(" · ")}` : ` · ${brief.n} 笔`) +
      `</span>` +
      `<span class="follow-pending-target">目标 ${escapeText(brief.target)} · 09:15–10:00</span>` +
      `</div>` +
      `<p class="follow-ops-note">意向价仅供参考。开盘窗按开盘价尝试成交；未成则盘中按「意向价与现价中点」追价，14:50 起改用现价强平。涨停买不到、跌停卖不出、停牌则跳过并留单。</p>` +
      `<div class="watching-react-grid-host follow-pending-grid-host">` +
      `<div class="watching-react-grid follow-pending-grid" role="table" aria-label="次日开盘挂单明细">` +
      `<div class="watching-react-grid-head">` +
      `<div class="watching-react-grid-row is-head" style="grid-template-columns:${cols}">${head}</div>` +
      `</div>` +
      `<div class="watching-react-grid-body follow-pending-grid-body">${body}</div>` +
      `</div></div>`;
  }

  function fmtT0AutoTs(ts) {
    if (ts == null || ts === "") return "";
    try {
      const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
      if (Number.isNaN(d.getTime())) return String(ts);
      const pad = (n) => String(n).padStart(2, "0");
      return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch (_) {
      return String(ts);
    }
  }

  function liveScoresByCodeFromHoldings() {
    const hs =
      (lastAccountData && lastAccountData.summary && lastAccountData.summary.holdings) ||
      [];
    const m = {};
    for (const h of hs) {
      const c = String((h && h.stock_code) || "").trim();
      if (!c) continue;
      if (
        h.predicted_score_eod == null &&
        h.decision_score == null &&
        h.y_path == null &&
        h.predicted_score_path == null
      ) {
        continue;
      }
      m[c] = h;
    }
    return m;
  }

  function renderT0WorkerDetail(data) {
    const el = document.getElementById("paper-t0-worker-trades");
    if (!el) return;
    try {
      const t0Auto =
        (data && data.rules && data.rules.t0_auto) || (data && data.t0_auto) || null;
      const execution = (data && data.execution) || lastAccountData?.execution || null;
      renderPaperT0WorkerTradesUi(el, {
        t0Auto,
        execution,
        liveScoresByCode: liveScoresByCodeFromHoldings(),
      });
    } catch (err) {
      console.error("[follow] worker trades render failed", err);
      el.hidden = false;
      el.innerHTML =
        `<p class="paper-t0-desk-empty">落账明细渲染失败：${escapeText(
          String(err && err.message ? err.message : err)
        )}</p>`;
    }
  }

  function renderT0LastRun(data) {
    const cfg = (data && data.rules && data.rules.t0_auto) || (data && data.t0_auto) || {};
    const lr = cfg.last_run;
    if (!lr || lr.ts == null) {
      setWorkerMetric("paper-t0-worker-m-lastrun", "尚无记录", { tone: "is-muted" });
      return;
    }
    const when = fmtT0AutoTs(lr.ts);
    if (lr.ok === false || lr.error) {
      setWorkerMetric(
        "paper-t0-worker-m-lastrun",
        `${when} · 失败：${lr.error || "未知"}`,
        { tone: "is-error" }
      );
      return;
    }
    const trades = lr.trade_count != null ? lr.trade_count : "—";
    const pnl = lr.pnl_total != null ? lr.pnl_total : "—";
    const tag = lr.source === "paper_t0_auto" ? "自动" : "手动";
    if (!trades || trades === 0 || trades === "0") {
      setWorkerMetric("paper-t0-worker-m-lastrun", "尚无成交", { tone: "is-muted" });
      return;
    }
    setWorkerMetric(
      "paper-t0-worker-m-lastrun",
      `${when} · ${tag} · 成交 ${trades} · PnL ${pnl}`,
      { tone: "" }
    );
  }

  function setWorkerMetric(id, text, { tone = "" } = {}) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text || "—";
    el.classList.remove("is-ok", "is-warn", "is-error", "is-muted");
    if (tone) el.classList.add(tone);
  }

  function renderT0WorkerBar(worker) {
    const panel = document.getElementById("paper-t0-worker-panel");
    const switchEl = document.getElementById("paper-t0-worker-enabled");
    const badgeEl = document.getElementById("paper-t0-worker-badge");
    const badgeLabelEl = document.getElementById("paper-t0-worker-badge-label");
    const hintEl = document.getElementById("paper-t0-worker-hint");
    const errEl = document.getElementById("paper-t0-worker-error");
    if (!panel && !switchEl && !badgeEl) return;

    const w = worker || {};
    if (switchEl && document.activeElement !== switchEl) {
      switchEl.checked = !!w.enabled;
      switchEl.disabled = false;
    }

    const running = !!w.running;
    const err = w.last_error ? String(w.last_error) : "";
    let state = w.state || (running ? "running" : w.enabled ? "starting" : "stopped");
    if (err && running) state = "degraded";

    const stateLabels = {
      running: "运行中",
      stopped: "已停止",
      starting: "启动中",
      degraded: "运行异常",
    };
    const badgeLabel = stateLabels[state] || "未知";

    if (badgeEl) {
      badgeEl.classList.remove("is-running", "is-stopped", "is-starting", "is-degraded");
      if (state === "running") badgeEl.classList.add("is-running");
      else if (state === "starting") badgeEl.classList.add("is-starting");
      else if (state === "degraded") badgeEl.classList.add("is-degraded");
      else badgeEl.classList.add("is-stopped");
    }
    if (badgeLabelEl) badgeLabelEl.textContent = badgeLabel;

    const tickTs = w.last_tick_ts ? fmtT0AutoTs(w.last_tick_ts) : "—";
    const tickIntervalSec =
      w.tick_interval_sec != null && Number.isFinite(Number(w.tick_interval_sec))
        ? Math.max(1, Math.round(Number(w.tick_interval_sec)))
        : 300;
    const nextSec =
      w.next_tick_in_sec != null && Number.isFinite(Number(w.next_tick_in_sec))
        ? `${Math.max(0, Math.round(Number(w.next_tick_in_sec)))}s`
        : running
          ? `≤${tickIntervalSec}s`
          : "—";
    const tickMsg = w.last_tick_message ? String(w.last_tick_message) : "—";
    const started =
      w.started_at != null && Number.isFinite(Number(w.started_at))
        ? fmtT0AutoTs(w.started_at)
        : null;

    setWorkerMetric(
      "paper-t0-worker-m-state",
      running ? "线程活跃" : w.enabled ? "待启动" : "未运行",
      { tone: running ? "is-ok" : w.enabled ? "is-warn" : "is-muted" }
    );
    setWorkerMetric("paper-t0-worker-m-tick", tickTs, { tone: tickTs !== "—" ? "" : "is-muted" });
    setWorkerMetric("paper-t0-worker-m-next", nextSec, {
      tone: running ? "" : "is-muted",
    });
    setWorkerMetric("paper-t0-worker-m-action", tickMsg, {
      tone: err ? "is-error" : tickMsg !== "—" ? "" : "is-muted",
    });

    if (hintEl) {
      const hints = [];
      if (!w.enabled) {
        hints.push("后台 worker 已关闭；打开右侧「运行」即可 5m 盯盘自动落账");
      } else if (!running) {
        hints.push("开关已开但线程未就绪，请稍候或重启 Web");
      } else {
        hints.push("Worker 运行中 · 5m 盯盘触达即落账");
      }
      if (started && running) hints.push(`启动于 ${started}`);
      hintEl.textContent = hints.join(" · ");
    }

    if (errEl) {
      if (err) {
        errEl.hidden = false;
        errEl.textContent = `异常：${err}`;
      } else {
        errEl.hidden = true;
        errEl.textContent = "";
      }
    }

    if (panel) {
      panel.classList.toggle("is-running", running && !err);
      panel.classList.toggle("is-degraded", !!err);
      panel.classList.remove("is-busy");
    }
  }

  async function refreshT0RunPanel({ quiet = false } = {}) {
    if (!document.getElementById("paper-t0-auto-bar")) return null;
    try {
      const [autoRes, workerRes] = await Promise.all([
        fetch("/api/paper/t0/auto"),
        fetch("/api/paper/t0/worker"),
      ]);
      const autoData = await autoRes.json();
      const workerData = await workerRes.json();
      if (!autoRes.ok) {
        const errEl = document.getElementById("paper-t0-worker-error");
        if (errEl && !quiet) {
          errEl.hidden = false;
          errEl.textContent = autoData.detail || autoData.error || "落账状态加载失败";
        }
      } else if (lastAccountData && autoData.t0_auto) {
        lastAccountData.rules = lastAccountData.rules || {};
        lastAccountData.rules.t0_auto = autoData.t0_auto;
        renderT0LastRun(lastAccountData);
        renderT0WorkerDetail(lastAccountData);
      }
      if (!workerRes.ok) {
        const errEl = document.getElementById("paper-t0-worker-error");
        if (errEl && !quiet) {
          errEl.hidden = false;
          errEl.textContent =
            workerData.detail || workerData.error || "后台状态加载失败";
        }
      } else {
        renderT0WorkerBar(workerData.worker);
        renderPaperT0WorkerDeskUi(
          document.getElementById("paper-t0-worker-desk"),
          workerData.intraday
        );
      }
      return { auto: autoData, worker: workerData };
    } catch (err) {
      const errEl = document.getElementById("paper-t0-worker-error");
      if (errEl && !quiet) {
        errEl.hidden = false;
        errEl.textContent = String(err.message || err);
      }
      return null;
    }
  }

  let t0RunPollTimer = null;
  function startT0RunPoll() {
    if (t0RunPollTimer) return;
    t0RunPollTimer = setInterval(() => {
      refreshT0RunPanel({ quiet: true }).catch(() => {});
    }, 10000);
  }
  function stopT0RunPoll() {
    if (!t0RunPollTimer) return;
    clearInterval(t0RunPollTimer);
    t0RunPollTimer = null;
  }

  async function postT0Worker(enabled) {
    const panel = document.getElementById("paper-t0-worker-panel");
    const switchEl = document.getElementById("paper-t0-worker-enabled");
    const badgeLabelEl = document.getElementById("paper-t0-worker-badge-label");
    if (panel) panel.classList.add("is-busy");
    if (switchEl) switchEl.disabled = true;
    if (badgeLabelEl) badgeLabelEl.textContent = enabled ? "启动中" : "停止中";
    try {
      const res = await fetch("/api/paper/t0/worker", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !!enabled }),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = data.detail || data.error || "操作失败";
        const errEl = document.getElementById("paper-t0-worker-error");
        if (errEl) {
          errEl.hidden = false;
          errEl.textContent = String(msg);
        }
        return { ok: false, data };
      }
      renderT0WorkerBar(data.worker);
      if (data.t0_auto && lastAccountData) {
        lastAccountData.rules = lastAccountData.rules || {};
        lastAccountData.rules.t0_auto = data.t0_auto;
        renderT0LastRun(lastAccountData);
        renderT0WorkerDetail(lastAccountData);
      }
      await refreshT0RunPanel({ quiet: true });
      return { ok: true, data };
    } catch (err) {
      const errEl = document.getElementById("paper-t0-worker-error");
      if (errEl) {
        errEl.hidden = false;
        errEl.textContent = String(err.message || err);
      }
      return { ok: false, error: err };
    } finally {
      if (switchEl) switchEl.disabled = false;
      if (panel) panel.classList.remove("is-busy");
    }
  }

  function showValidateNext(kind) {
    const el = document.getElementById("paper-validate-next");
    if (!el) return;
    el.hidden = false;
    if (kind === "t0") {
      el.innerHTML =
        `做 T 是<strong>底仓 overlay</strong>验证，不是独立选股。完整闭环：` +
        `<a class="follow-hero-link" href="/replay">历史回测</a> · ` +
        `<a class="follow-hero-link" href="/strategy">策略晋升</a> · ` +
        `<a class="follow-hero-link" href="/platform">北极星</a>`;
      return;
    }
    el.innerHTML =
      `纸面调仓是落地一步（非完整科学验证）。下一步：` +
      `<a class="follow-hero-link" href="/replay">历史回测对照</a> · ` +
      `改限额去 <a class="follow-hero-link" href="/strategy">策略中心晋升</a> · ` +
      `看拟合 <a class="follow-hero-link" href="/platform">北极星</a>`;
  }

  let holdingsScoreEnrichPromise = null;

  async function fetchHoldingScoresWithRetry(maxAttempts = 4) {
    let last = null;
    for (let i = 0; i < maxAttempts; i += 1) {
      const { ok, data, error } = await apiFetch(
        `/api/paper/holding-scores?${offlineOnlyQuery()}`
      );
      last = { ok, data, error };
      if (!ok) return last;
      if (data && data.busy) {
        if (i + 1 < maxAttempts) {
          setHoldingsLoadStatus("补 ŷ 进行中…", { busy: true });
          await new Promise((r) => setTimeout(r, 800 + i * 400));
          continue;
        }
        return last;
      }
      return last;
    }
    return last;
  }

  async function applyHoldingsScorePack(holdings) {
    if (!lastAccountData || !Array.isArray(holdings)) return;
    const sm = lastAccountData.summary || {};
    lastAccountData = {
      ...lastAccountData,
      summary: { ...sm, holdings },
    };
    if (holdingsGrid && holdingsGridReady) {
      try {
        const V =
          (typeof window !== "undefined" && window.__ASSET_V__) || "p1737";
        const mod = await import(`./holdings_table_island.js?v=${V}`);
        const patches = {};
        for (const h of holdings) {
          const code = String((h && h.stock_code) || "").trim();
          if (!code) continue;
          patches[code] = mod.holdingToRow(h, {
            chartMode,
            chartStockCode,
            selectedHoldCode,
            pendingFocusCode,
            sentHtml: holdingsSentimentHtmlByCode[code],
          });
        }
        if (Object.keys(patches).length) {
          holdingsGrid.patchRows(patches);
        }
        const holdingsScoreStatsEl = document.getElementById(
          "paper-holdings-score-stats"
        );
        if (holdingsScoreStatsEl) {
          const eod = scoreSeriesStats(holdings.map(resolveEodScore));
          const trade = scoreSeriesStats(holdings.map(resolveTradeScore));
          const bit = (label, pack) =>
            pack.n
              ? `${label} μ ${Number(pack.mean).toFixed(2)}% · med ${Number(
                  pack.median
                ).toFixed(2)}% · n=${pack.n}`
              : null;
          holdingsScoreStatsEl.textContent = [
            bit("ŷ_EOD", eod),
            bit("ŷ_trade", trade),
          ]
            .filter(Boolean)
            .join(" · ");
        }
        renderT0WorkerDetail(lastAccountData);
        return;
      } catch (err) {
        console.warn("[follow] patch holdings scores failed, fallback re-render", err);
      }
    }
    await renderPaperAccountDetail(lastAccountData);
    renderT0WorkerDetail(lastAccountData);
  }

  async function prepareFollowWarehouse({ force = false } = {}) {
    if (getDataOfflineOnly()) return { ok: true, skipped: true };
    try {
      return await ensureWarehouseTopup({
        force,
        watchingLimit: 100,
        onStatus: (msg) =>
          setHoldingsLoadStatus(msg || "增量补齐本地仓…", { busy: true }),
      });
    } catch (err) {
      const msg = String((err && err.message) || err || "增量补齐失败");
      setHoldingsLoadStatus(msg, { error: true });
      throw err;
    }
  }

  async function enrichHoldingsScores() {
    if (holdingsScoreEnrichPromise) return holdingsScoreEnrichPromise;
    holdingsScoreEnrichPromise = (async () => {
      setHoldingsLoadStatus("补 ŷ（缓存）…", { busy: true });
      try {
        // 写仓与读分并行：topup 不挡 ŷ 展示
        const topupPromise = getDataOfflineOnly()
          ? Promise.resolve(null)
          : prepareFollowWarehouse({ force: false }).catch(() => null);
        const { ok, data, error } = await fetchHoldingScoresWithRetry();
        await topupPromise;
        if (!ok) {
          setHoldingsLoadStatus(error || "补 ŷ 失败", { error: true });
          return;
        }
        if (data && data.busy) {
          setHoldingsLoadStatus("补 ŷ 仍忙 · 请点刷新重试", { error: true });
          return;
        }
        const byCode = (data && data.by_code) || {};
        const codes = Object.keys(byCode);
        if (!codes.length || !lastAccountData) {
          setHoldingsLoadStatus("ŷ 无缓存结果", { ok: true });
          return;
        }
        const sm = lastAccountData.summary || {};
        const holdings = Array.isArray(sm.holdings)
          ? sm.holdings.map((h) => {
              const code = String((h && h.stock_code) || "").trim();
              const pack = byCode[code];
              if (!pack || typeof pack !== "object") return h;
              const keep = {
                stock_code: h.stock_code,
                stock_name: h.stock_name,
                shares: h.shares,
                cost: h.cost,
                price: h.price,
                open: h.open,
                change_pct: h.change_pct,
                change_asof: h.change_asof,
                market_value: h.market_value,
                pnl_pct: h.pnl_pct,
                bought_at: h.bought_at,
                bought_date: h.bought_date,
                hold_days: h.hold_days,
                sellable_shares: h.sellable_shares,
                locked_shares: h.locked_shares,
                lots: h.lots,
                origin: h.origin,
                origin_label: h.origin_label,
                currency: h.currency,
                unit: h.unit,
                price_source: h.price_source,
                t0_intraday: h.t0_intraday,
              };
              return { ...pack, ...keep };
            })
          : [];
        await applyHoldingsScorePack(holdings);
        setHoldingsLoadStatus(`ŷ 已补 · ${codes.length} 只`, { ok: true });
      } catch (err) {
        setHoldingsLoadStatus(String((err && err.message) || err || "补 ŷ 失败"), {
          error: true,
        });
      } finally {
        holdingsScoreEnrichPromise = null;
      }
    })();
    return holdingsScoreEnrichPromise;
  }

  function syncFollowDataPolicyFoots() {
    const off = getDataOfflineOnly();
    const reb = document.getElementById("follow-rebalance-data-foot");
    if (reb) {
      const t = rebalanceDataFoot(off);
      reb.textContent = t;
      reb.title = t;
    }
    const t0 = document.getElementById("follow-t0-data-foot");
    if (t0) {
      const t = t0DataFoot();
      t0.textContent = t;
      t0.title = t;
    }
  }
  syncFollowDataPolicyFoots();

  installDataOfflineToggle(document.getElementById("follow-data-offline"), {
    onChange: (offlineOnly) => {
      syncFollowDataPolicyFoots();
      (async () => {
        try {
          if (!offlineOnly) await prepareFollowWarehouse({ force: true });
        } catch (_) {
          /* status already set */
        }
        enrichHoldingsScores().catch(() => {});
      })();
    },
  });

  async function loadPaper(opts = {}) {
    const quiet = !!(opts && opts.quiet);
    const force = !!(opts && opts.force);
    if (
      !document.getElementById("paper-meta") &&
      !document.getElementById("follow-meta") &&
      !document.getElementById("paper-stats") &&
      !document.getElementById("paper-holdings-table")
    ) {
      return null;
    }
    // 防重入：并发刷新共用同一请求，避免叠请求把状态卡在「刷新中…」
    if (paperLoadPromise && !force) return paperLoadPromise;

    const refreshBtn = document.getElementById("paper-holdings-refresh");
    const started = Date.now();
    paperLoadPromise = (async () => {
      if (!quiet) {
        setHoldingsLoadStatus("加载中…", { busy: true });
        if (refreshBtn) {
          refreshBtn.disabled = true;
          refreshBtn.textContent = "刷新中…";
        }
      }
      try {
        const { ok, data, error } = await apiFetch("/api/paper");
        if (!ok) {
          setPaperMetaText(error || "纸面加载失败");
          if (!quiet) {
            setHoldingsLoadStatus(error || "加载失败", { error: true });
          }
          return null;
        }
        await applyPaperData(data);
        const n = ((data.summary && data.summary.holdings) || []).length;
        const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
        if (!quiet) {
          setHoldingsLoadStatus(
            data.initialized
              ? `已刷新 · ${n} 只持仓 · ${sec}s`
              : "未初始化",
            { ok: !!data.initialized }
          );
        }
        // ŷ 异步补：不挡净值/持仓表；仅本地缓存算分
        if (data.initialized && n > 0) {
          enrichHoldingsScores().catch(() => {});
        }
        return data;
      } catch (err) {
        const msg = String((err && err.message) || err || "加载失败");
        setPaperMetaText(msg);
        if (!quiet) setHoldingsLoadStatus(msg, { error: true });
        throw err;
      } finally {
        if (refreshBtn) {
          refreshBtn.disabled = false;
          refreshBtn.textContent = "刷新";
        }
      }
    })();
    try {
      return await paperLoadPromise;
    } finally {
      paperLoadPromise = null;
    }
  }

  let holdingsLoadStatusTimer = null;
  function setHoldingsLoadStatus(msg, { busy = false, error = false, ok = false } = {}) {
    const el = document.getElementById("paper-holdings-load-status");
    if (!el) return;
    if (holdingsLoadStatusTimer) {
      clearTimeout(holdingsLoadStatusTimer);
      holdingsLoadStatusTimer = null;
    }
    const text = String(msg || "").trim();
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle("is-busy", !!busy && !error);
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!ok && !error && !busy);
    if (text && ok && !error && !busy) {
      holdingsLoadStatusTimer = setTimeout(() => {
        el.classList.remove("is-ok");
        holdingsLoadStatusTimer = null;
      }, 5000);
    }
  }

  function renderOpsReport(ops, { forceShow = false } = {}) {
    return renderOpsReportEl(ops, { forceShow });
  }

  async function applyPaperData(data) {
    if (!data) return data;
    const emptyEl = document.getElementById("paper-empty");
    const mainEl = document.getElementById("paper-main");
    paperInitialized = !!data.initialized;
    const costNote = document.getElementById("follow-cost-note");
    const costSelect = document.getElementById("paper-cost-model");
    const fundCostLabel = document.getElementById("follow-fund-cost-label");
    const stratHidden = document.getElementById("paper-strategy");
    const stratLabel = document.getElementById("paper-strategy-label");
    const stratVer = document.getElementById("paper-strategy-version");
    const stratRules = document.getElementById("paper-strategy-rules");
    const model = data.cost_model || (data.summary && data.summary.cost_model) || "zero";
    if (costSelect && costSelect.value !== model) costSelect.value = model;
    const STRATEGY_LABELS = {
      short_conservative: "保守短线",
      short: "短线评分",
    };
    const sid = data.strategy_id ? String(data.strategy_id) : "";
    if (sid) {
      if (stratHidden && stratHidden.value !== sid) stratHidden.value = sid;
      if (stratLabel) {
        stratLabel.textContent =
          data.strategy_label || STRATEGY_LABELS[sid] || sid;
      }
    } else if (stratLabel && !String(stratLabel.textContent || "").trim()) {
      stratLabel.textContent = "—";
    }
    if (stratVer) {
      const ver = String(data.strategy_version || "").trim();
      if (ver) {
        stratVer.hidden = false;
        stratVer.textContent = `@${ver}`;
      } else {
        stratVer.hidden = true;
        stratVer.textContent = "";
      }
    }
    if (stratRules) {
      const rules = data.rules || (data.config_preview && data.config_preview.rules) || {};
      const parts = [];
      const maxPos = Number(rules.max_positions);
      if (Number.isFinite(maxPos) && maxPos > 0) parts.push(`≤${maxPos}只`);
      const rawPct = Number(rules.position_pct);
      if (Number.isFinite(rawPct) && rawPct > 0) {
        const pct = rawPct <= 1 ? Math.round(rawPct * 100) : Math.round(rawPct);
        parts.push(`单票${pct}%`);
      }
      if (parts.length) {
        stratRules.hidden = false;
        stratRules.textContent = parts.join(" · ");
        stratRules.title = "来自当前策略纸面规则（组合限额）";
      } else {
        stratRules.hidden = true;
        stratRules.textContent = "";
        stratRules.removeAttribute("title");
      }
    }
    if (costNote) {
      costNote.textContent =
        model === "simple_cn"
          ? "模拟账户 · A股简化成本"
          : "模拟账户 · 零成本假设";
      costNote.title =
        model === "simple_cn"
          ? "佣金万 2.5（最低 5 元）+ 卖出印花税万 5"
          : "成交按现价，不计佣金、滑点与印花税";
    }
    if (fundCostLabel) {
      fundCostLabel.textContent = model === "simple_cn" ? "A股简化" : "零成本";
    }
    if (!data.initialized) {
      setPaperMetaText("未初始化");
      renderPendingOrdersBanner(null);
      if (emptyEl) emptyEl.hidden = false;
      if (mainEl) mainEl.hidden = true;
      renderPaperStats({});
      await renderPaperAccountDetail(data);
      drawPaperChart([]);
      return data;
    }
    if (emptyEl) emptyEl.hidden = true;
    if (mainEl) mainEl.hidden = false;
    const sm = data.summary || {};
    const pending = pendingOrdersBrief(data);
    setPaperMetaText(
      `持仓 ${(sm.holdings || []).length} 只 · 现金 ${sm.cash ?? "—"}` +
        (pending ? ` · ${pending.text}` : "")
    );
    renderPendingOrdersBanner(data);
    renderPaperStats(sm);
    renderFollowNorthStar(data.north_star || (data.ops_report && data.ops_report.north_star));
    await renderPaperAccountDetail(data);
    // 只缓存五问文案；不要因刷账户把「调仓报告」预演区再次展开
    if (data.ops_report) {
      renderOpsReport(data.ops_report, { forceShow: false });
    }
    applyPendingFocus();
    lastSnapshots = data.snapshots || [];
    if (chartMode === "stock" && chartStockCode) {
      const stillHeld = (sm.holdings || []).some(
        (h) => String(h.stock_code) === String(chartStockCode)
      );
      if (stillHeld) showStockChart(chartStockCode);
      else showPortfolioChart();
    } else {
      drawPaperChart(lastSnapshots);
    }
    return data;
  }

  // 研究枢纽同款：路径 rail 跳转按钮。仅绑定一次。
  (function bindFollowPathRailOnce() {
    if (window.__followPathRailBound) return;
    window.__followPathRailBound = true;
    document.addEventListener("click", (e) => {
      const step = e.target.closest(".follow-path-step");
      if (!step) return;
      const target = step.dataset.step;
      const href = step.getAttribute("href") || "";
      if (href && href.startsWith("/")) {
        e.preventDefault();
        location.href = href;
        return;
      }
      if (target === "watching") {
        e.preventDefault();
        location.href = "/watching";
      } else if (target === "replay") {
        e.preventDefault();
        location.href = "/replay";
      } else if (target === "rebalance") {
        e.preventDefault();
        const el = document.getElementById("follow-section-rebalance");
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (target === "t0") {
        e.preventDefault();
        const el = document.getElementById("follow-section-t0");
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (target === "strategy" || target === "promote") {
        e.preventDefault();
        location.href = "/strategy";
      }
    });

    const openSecondaryByHash = () => {
      const id = String(location.hash || "").replace(/^#/, "");
      if (!id) return;
      const sec = document.getElementById(id);
      if (!sec?.classList.contains("follow-section-secondary")) return;
      const fold = sec.querySelector("details.follow-secondary-fold");
      if (fold) fold.open = true;
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    openSecondaryByHash();
    window.addEventListener("hashchange", openSecondaryByHash);
  })();

  let tradeStatusTimer = null;
  function setTradeStatus(msg, { error } = {}) {
    const el = document.getElementById("paper-trade-status");
    if (!el) return;
    if (tradeStatusTimer) {
      clearTimeout(tradeStatusTimer);
      tradeStatusTimer = null;
    }
    el.textContent = msg || "";
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!(msg && !error));
    el.hidden = !msg;
    if (msg && !error) {
      tradeStatusTimer = setTimeout(() => {
        el.textContent = "";
        el.classList.remove("is-ok");
        el.hidden = true;
        tradeStatusTimer = null;
      }, 4200);
    }
  }

  function rowSharesInput(btn) {
    const bar = document.getElementById("paper-hold-action-bar");
    const input =
      (bar && !bar.hidden && bar.querySelector(".paper-hold-shares")) ||
      (btn.closest("tr") && btn.closest("tr").querySelector(".paper-hold-shares")) ||
      (btn.closest(".paper-hold-action-bar") &&
        btn.closest(".paper-hold-action-bar").querySelector(".paper-hold-shares"));
    const raw = input ? String(input.value || "").trim() : "";
    if (!raw) return null;
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Math.floor(n);
  }

  async function postPaperTrade(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    applyPaperData(data);
    setTradeStatus(data.message || "完成");
    // 加减仓 / 清仓后回写观察页「已持 / 未持」状态
    if (typeof ctx.reloadWatching === "function") {
      try {
        await ctx.reloadWatching();
      } catch (_) {
        /* ignore */
      }
    }
    const n = ((data.summary && data.summary.holdings) || []).length;
    if (n > 0) {
      enrichHoldingsScores().catch(() => {});
    }
    return data;
  }

  const holdingsTableEl = document.getElementById("paper-holdings-table");
  if (holdingsTableEl && holdingsTableEl.dataset.tradeWired !== "1") {
    holdingsTableEl.dataset.tradeWired = "1";
    holdingsTableEl.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter") return;
      const input = e.target.closest(".paper-hold-shares");
      if (!input) return;
      e.preventDefault();
      const bar = input.closest(".paper-hold-action-bar");
      const buyBtn =
        (bar && bar.querySelector(".paper-hold-buy")) ||
        (input.closest("tr") && input.closest("tr").querySelector(".paper-hold-buy"));
      if (buyBtn) buyBtn.click();
    });
    holdingsTableEl.addEventListener("click", async (e) => {
      const pagerBtn = e.target.closest && e.target.closest("[data-pager]");
      if (pagerBtn) {
        e.preventDefault();
        if (pagerBtn.getAttribute("data-pager") === "prev") holdingsPage -= 1;
        else holdingsPage += 1;
        if (lastAccountData) renderPaperAccountDetail(lastAccountData);
        return;
      }
      const moreBtn = e.target.closest(".paper-log-more");
      if (moreBtn) {
        // logs live outside; ignore if somehow nested
        return;
      }
      const dismissBtn = e.target.closest(".paper-hold-action-dismiss");
      if (dismissBtn) {
        e.preventDefault();
        clearHoldSelection();
        return;
      }
      const sortThEl = e.target.closest("th.paper-hold-sort");
      if (sortThEl) {
        e.preventDefault();
        e.stopPropagation();
        const key = sortThEl.dataset.sort;
        if (
          key === "code" ||
          key === "market_value" ||
          key === "score" ||
          key === "score_eod" ||
          key === "score_tau" ||
          key === "score_path" ||
          key === "score_on" ||
          key === "score_nowcast" ||
          key === "pnl" ||
          key === "chg"
        ) {
          if (holdingsSortKey === key) {
            holdingsSortDir = holdingsSortDir === "asc" ? "desc" : "asc";
          } else {
            holdingsSortKey = key;
            holdingsSortDir = key === "code" ? "asc" : "desc";
          }
          persistHoldingsSort();
          if (lastAccountData) renderPaperAccountDetail(lastAccountData);
        }
        return;
      }
      const buyBtn = e.target.closest(".paper-hold-buy");
      const cutBtn = e.target.closest(".paper-hold-cut");
      const flatBtn = e.target.closest(".paper-hold-flat");
      if (!buyBtn && !cutBtn && !flatBtn) {
        if (e.target.closest("input, button, .paper-hold-action-bar, thead, summary, .watching-react-grid-head")) {
          return;
        }
        const row = e.target.closest(".paper-hold-row[data-code], tr[data-code]");
        if (row && row.dataset.code) {
          selectHoldRow(row, { chart: true });
        }
        return;
      }
      e.preventDefault();
      try {
        if (buyBtn) {
          let shares = rowSharesInput(buyBtn);
          if (shares == null) {
            setTradeStatus("请填写加仓股数", { error: true });
            return;
          }
          const lot = Math.floor(shares / 100) * 100;
          if (lot < 100) {
            setTradeStatus("加仓至少 100 股", { error: true });
            return;
          }
          setTradeStatus(`加仓 ${lot} 股…`);
          await postPaperTrade("/api/paper/buy", {
            stock_code: buyBtn.dataset.code,
            shares: lot,
          });
        } else if (flatBtn) {
          setTradeStatus("清仓中…");
          await postPaperTrade("/api/paper/sell", {
            stock_code: flatBtn.dataset.code,
          });
        } else if (cutBtn) {
          const held = Number(cutBtn.dataset.shares || 0);
          let shares = rowSharesInput(cutBtn);
          if (shares == null) {
            // 未填：默认减一手
            shares = held > 0 && held < 100 ? held : 100;
          } else if (shares < held) {
            const lot = Math.floor(shares / 100) * 100;
            if (lot <= 0) {
              setTradeStatus("减仓至少 100 股（或点清仓）", { error: true });
              return;
            }
            shares = lot;
          }
          // shares >= held → 清仓
          if (held > 0 && shares >= held) {
            setTradeStatus("清仓中…");
            await postPaperTrade("/api/paper/sell", {
              stock_code: cutBtn.dataset.code,
            });
          } else {
            setTradeStatus(`减仓 ${shares} 股…`);
            await postPaperTrade("/api/paper/sell", {
              stock_code: cutBtn.dataset.code,
              shares,
            });
          }
        }
      } catch (err) {
        setTradeStatus(String(err.message || err), { error: true });
      }
    });
    scoreTips.bindHost(holdingsTableEl, {
      scoreSelector: ".paper-hold-score[data-score-detail]",
    });
    wireHoldingsChgTips(holdingsTableEl, scoreTips, { apiFetch });
  }

  const paperT0DaysHost = document.getElementById("paper-t0-days");
  if (paperT0DaysHost) {
    scoreTips.bindHost(paperT0DaysHost, {
      scoreSelector:
        ".paper-t0-y-score[data-score-detail], .paper-t0-dir-score[data-score-detail]",
    });
    wireT0ProcessTips(paperT0DaysHost, scoreTips);
    wireT0DayDebugExpand(paperT0DaysHost);
  }
  const paperT0VizHost = document.getElementById("paper-t0-viz");
  if (paperT0VizHost) {
    wireT0SkipTips(paperT0VizHost, scoreTips);
  }
  const paperT0WorkerTradesHost = document.getElementById("paper-t0-worker-trades");
  if (paperT0WorkerTradesHost) {
    scoreTips.bindHost(paperT0WorkerTradesHost, {
      scoreSelector:
        ".paper-t0-y-score[data-score-detail], .paper-t0-dir-score[data-score-detail]",
    });
    wireT0ProcessTips(paperT0WorkerTradesHost, scoreTips);
    wireT0DayDebugExpand(paperT0WorkerTradesHost);
    paperT0WorkerTradesHost.addEventListener("click", async (ev) => {
      const btn = ev.target && ev.target.closest
        ? ev.target.closest("button.paper-t0-ledger-del")
        : null;
      if (!btn || !paperT0WorkerTradesHost.contains(btn)) return;
      ev.preventDefault();
      const code = String(btn.getAttribute("data-code") || "").trim();
      const name = String(btn.getAttribute("data-name") || code).trim();
      if (!code) return;
      if (
        !window.confirm(
          `删除 ${name}（${code}）的落账明细，并冲正对应做 T 成交腿（现金/批次）？`
        )
      ) {
        return;
      }
      btn.disabled = true;
      try {
        const res = await fetch("/api/paper/t0/last-run/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stock_codes: [code], reverse_ledger: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          window.alert(data.detail || data.error || data.message || "删除失败");
          return;
        }
        if (lastAccountData) {
          lastAccountData.rules = lastAccountData.rules || {};
          if (data.t0_auto) lastAccountData.rules.t0_auto = data.t0_auto;
          if (data.summary) {
            Object.assign(lastAccountData, {
              cash: data.summary.cash ?? lastAccountData.cash,
              total_value: data.summary.total_value ?? lastAccountData.total_value,
            });
          }
          renderT0LastRun(lastAccountData);
          renderT0WorkerDetail(lastAccountData);
        }
        if (typeof loadPaper === "function") {
          await loadPaper({ quiet: true }).catch(() => {});
        }
        await refreshT0RunPanel({ quiet: true }).catch(() => {});
      } catch (err) {
        window.alert(String(err.message || err));
      } finally {
        btn.disabled = false;
      }
    });
  }

  const paperT0WorkerDeskHost = document.getElementById("paper-t0-worker-desk");
  if (paperT0WorkerDeskHost) {
    paperT0WorkerDeskHost.addEventListener("click", async (ev) => {
      const t = ev.target;
      if (!t || !t.closest) return;
      const clearBtn = t.closest("button.paper-t0-desk-clear");
      if (!clearBtn || !paperT0WorkerDeskHost.contains(clearBtn)) return;
      ev.preventDefault();

      const postClear = async (body, btn) => {
        if (btn) btn.disabled = true;
        try {
          const res = await fetch("/api/paper/t0/intraday/clear", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            window.alert(data.detail || data.error || data.message || "删除失败");
            return;
          }
          if (data.intraday) {
            renderPaperT0WorkerDeskUi(paperT0WorkerDeskHost, data.intraday);
          }
          if (data.skipped_with_legs && data.skipped_with_legs.length) {
            window.alert(
              (data.message || "已删除") +
                `\n已落账未删：${data.skipped_with_legs.join("、")}（可先删落账明细，或强制删除）`
            );
          }
          await refreshT0RunPanel({ quiet: true }).catch(() => {});
        } catch (err) {
          window.alert(String(err.message || err));
        } finally {
          if (btn) btn.disabled = false;
        }
      };

      const code = String(clearBtn.getAttribute("data-code") || "").trim();
      const name = String(clearBtn.getAttribute("data-name") || code).trim();
      const legs = Number(clearBtn.getAttribute("data-legs") || 0);
      if (!code) return;
      if (legs > 0) {
        const ok = window.confirm(
          `${name}（${code}）已落账 ${legs} 腿。\n` +
            `删除盯盘状态可能导致 Worker 重复落第一腿。\n` +
            `若要冲正账本请用「落账明细 → 删除」。\n仍要强制删除盯盘状态？`
        );
        if (!ok) return;
        await postClear({ stock_codes: [code], force: true }, clearBtn);
        return;
      }
      if (
        !window.confirm(
          `删除 ${name}（${code}）的盯盘状态？不改账本，Worker 将重新监视。`
        )
      ) {
        return;
      }
      await postClear({ stock_codes: [code], force: false }, clearBtn);
    });
  }

  async function openPaperDialog() {
    try {
      await loadPaper();
      if (paperDialog && typeof paperDialog.showModal === "function") paperDialog.showModal();
    } catch (err) {
      setPaperMetaText(String(err.message || err));
      if (paperDialog && typeof paperDialog.showModal === "function") paperDialog.showModal();
    }
  }

  const btnPaper = document.getElementById("btn-paper");
  if (btnPaper && btnPaper.tagName === "BUTTON" && !btnPaper.dataset.resultsTab) {
    btnPaper.addEventListener("click", () => openPaperDialog());
  }

  if (document.getElementById("paper-init") || document.getElementById("paper-run") || document.getElementById("paper-adjust")) {
    const opsStatusEl =
      document.getElementById("paper-path-matrix-status") ||
      document.getElementById("paper-ops-load-status");
    let opsStatusTimer = null;
    const runBtns = [
      "paper-run",
      "paper-adjust",
      "paper-daily",
      "paper-init",
    ]
      .map((id) => document.getElementById(id))
      .filter(Boolean);

    function setPaperBusy(busy) {
      runBtns.forEach((btn) => {
        btn.disabled = !!busy;
      });
    }

    function setOpsLoadStatus(msg, { busy = false, error = false, ok = false } = {}) {
      if (!opsStatusEl) return;
      if (opsStatusTimer) {
        clearTimeout(opsStatusTimer);
        opsStatusTimer = null;
      }
      const text = String(msg || "").trim();
      opsStatusEl.textContent = text;
      if ("hidden" in opsStatusEl) opsStatusEl.hidden = !text;
      opsStatusEl.classList.toggle("is-busy", !!busy && !error && !!text);
      opsStatusEl.classList.toggle("is-error", !!error && !!text);
      opsStatusEl.classList.toggle("is-ok", !!ok && !error && !busy && !!text);
      if (text && ok && !error && !busy) {
        opsStatusTimer = setTimeout(() => {
          opsStatusEl.classList.remove("is-ok", "is-busy", "is-error");
          if ("hidden" in opsStatusEl) opsStatusEl.hidden = true;
          opsStatusEl.textContent = "";
          opsStatusTimer = null;
        }, 5000);
      }
    }

    function showProgress(pct, message) {
      const msg = String(message || "").trim();
      const done = Number(pct) >= 100;
      if (!msg && !done) {
        setOpsLoadStatus("加载中…", { busy: true });
        return;
      }
      if (done) {
        setOpsLoadStatus(msg || "已完成", { ok: true });
        return;
      }
      setOpsLoadStatus(msg || "加载中…", { busy: true });
    }

    function hideProgress() {
      setOpsLoadStatus("");
    }

    let followClusterRef = null;
    const rebalanceReportUi = createRebalanceReportController({
      escapeText,
      fmtPct,
      fmtTableScore,
      scoreCls,
      resolveTradeScore,
      resolveEodScore,
      resolveTauScore,
      resolvePathScore,
      resolveOnScore,
      resolveNowcastScore,
      fmtPathScore,
      Y_EOD_TITLE,
      Y_TAU_TITLE,
      Y_PATH_TITLE,
      Y_ON_TITLE,
      Y_NOWCAST_TITLE,
      TRADE_TITLE,
      isHeuristicScoreScale,
      renderOpsReport,
      showValidateNext,
      formatWeightSourceNote,
      formatFactorWeightsSection,
      formatFormulaTermsSection,
      formatScoreHero,
      formatRemScoreSection,
      formatBlendScoreSection,
      formatSentimentGateSection,
      formatMarketPriorSection,
      marketPriorDetailFields,
      tailAnomalyDetailFields,
      overheatDetailFields,
      scoreTips,
      showPlainTooltip,
      hideScoreTooltip,
      metricCls,
      setFollowClusterPreviewPending: (v) => {
        if (followClusterRef && followClusterRef.setFollowClusterPreviewPending) {
          followClusterRef.setFollowClusterPreviewPending(v);
        }
      },
      setFollowMatrixPreviewPending: (v) => {
        if (followClusterRef) followClusterRef.setFollowMatrixPreviewPending(v);
      },
    });
    const dismissRebalancePreview = (...args) =>
      rebalanceReportUi.dismissRebalancePreview(...args);
    const emptyReasonLabel = (...args) => rebalanceReportUi.emptyReasonLabel(...args);
    const renderRebalanceReport = (...args) =>
      rebalanceReportUi.renderRebalanceReport(...args);

    async function waitPaperJob(jobId) {
      return waitPaperJobPoll({
        jobId,
        showProgress,
        setMeta: (msg) => {
          if (paperMeta && msg) setPaperMetaText(msg);
        },
      });
    }

    const paperInitBtn = document.getElementById("paper-init");
    if (paperInitBtn) {
    paperInitBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const res = await fetch("/api/paper/init", { method: "POST" });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || res.statusText);
        }
        await loadPaper();
      } catch (err) {
        setPaperMetaText(String(err.message || err));
      }
    });
    }

    async function runPaper(simulateBuy, { dryRun = false } = {}) {
      setPaperBusy(true);
      const strategy =
        document.getElementById("paper-strategy")?.value || "short_conservative";
      showProgress(
        1,
        dryRun ? "预演调仓…" : simulateBuy ? "启动模拟买入…" : "启动跑一日…"
      );
      try {
        const res = await fetch("/api/paper/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            simulate_buy: simulateBuy,
            background: true,
            strategy: strategy,
            dry_run: !!dryRun,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        if (!data.ok && data.error) throw new Error(data.error);
        
        let result;
        if (data.background && data.job && data.job.id) {
          // 后台模式，等待任务完成
          const job = await waitPaperJob(data.job.id);
          result = job.result || {};
        } else {
          // 同步模式，直接使用返回结果
          result = data;
        }
        const n = result.observation_pool_count;
        const buyTrades = result.new_trades || [];
        const sellTrades = result.sell_trades || [];
        const rebalanceReport = result.rebalance_report || [];
        const cashImpact = result.cash_impact || null;
        const riskGate = result.risk_gate || null;
        const riskBudgetSkips = result.risk_budget_skips || [];
        const dualScore = result.dual_score || null;
        const emptyReason = result.empty_reason || null;
        const minScore = result.min_score;
        const priorSkipN = riskBudgetSkips.filter((s) => s && s.sentiment_prior).length;
        const marketSkipN = riskBudgetSkips.filter((s) => s && s.market_prior).length;
        const opsReport =
          result.ops_report ||
          (result.data_quality || result.risk_blocks || result.cost_model
            ? {
                strategy_id: result.strategy_id,
                strategy_version: result.strategy_version,
                cost_model: result.cost_model,
                data_quality: result.data_quality,
                risk_blocks: result.risk_blocks,
                monitor_alerts: result.monitor_alerts,
                buys_blocked: result.buys_blocked,
                fallback_count: (result.data_quality || {}).fallback_count,
              }
            : null);

        let resultText = dryRun
          ? `预演完成 · 评分 ${n} 只`
          : `完成 · 评分 ${n} 只`;
        if (buyTrades.length > 0) {
            resultText += ` · 加仓 ${buyTrades.length} 只`;
        }
        if (sellTrades.length > 0) {
            resultText += ` · 减仓 ${sellTrades.length} 只`;
        }
        const priorTrimN = sellTrades.filter((t) => t && t.sentiment_prior).length;
        const marketTrimN = sellTrades.filter((t) => t && t.market_prior).length;
        if (priorTrimN > 0) {
            resultText += ` · 舆情缩仓 ${priorTrimN}`;
        }
        if (marketTrimN > 0) {
            resultText += ` · 市场缩仓 ${marketTrimN}`;
        }
        if (priorSkipN > 0) {
            resultText += ` · 舆情跳过 ${priorSkipN}`;
        }
        if (marketSkipN > 0) {
            resultText += ` · 市场跳过 ${marketSkipN}`;
        }
        if (riskGate && riskGate.ok === false) {
            resultText += " · 风控拦截加仓";
        }
        const fb = Number((opsReport && opsReport.fallback_count) || 0);
        if (fb > 0) {
            resultText += ` · 数据降级 ${fb} 只`;
        }
        if (buyTrades.length === 0 && sellTrades.length === 0) {
            resultText += dryRun ? " · 预演无调仓" : " · 无调仓操作";
            if (emptyReason) {
              const lab = emptyReasonLabel(emptyReason);
              if (lab) resultText += ` · ${lab}`;
            }
        }
        if (!dryRun && result.note) {
            resultText += ` · ${result.note}`;
        } else if (!dryRun && result.fill_action === "staged") {
            resultText += " · 已挂次日开盘单";
        }

        setPaperMetaText(resultText);
        showProgress(100, (document.getElementById("follow-meta")||document.getElementById("paper-meta")||{}).textContent || "");
        if (!dryRun) {
          dismissRebalancePreview();
          await loadPaper({ quiet: true });
          if (typeof ctx.reloadWatching === "function") {
            try {
              await ctx.reloadWatching();
            } catch (_) {}
          }
        } else if (simulateBuy && rebalanceReport.length > 0) {
          renderRebalanceReport(rebalanceReport, {
            preview: true,
            cashImpact,
            riskGate,
            opsReport,
            riskBudgetSkips,
            dualScore,
            emptyReason,
            minScore,
            marketContext: result.market_context,
          });
        } else if (simulateBuy) {
          renderRebalanceReport([], {
            preview: true,
            cashImpact,
            riskGate,
            opsReport,
            riskBudgetSkips,
            dualScore,
            emptyReason,
            minScore,
            marketContext: result.market_context,
          });
          setPaperMetaText(
            resultText + (riskGate && riskGate.ok === false ? "" : " · 无可执行变动")
          );
        } else if (opsReport) {
          const section = document.getElementById("paper-rebalance-section");
          if (section) section.hidden = false;
          renderOpsReport(opsReport, { forceShow: true });
        }
        setTimeout(hideProgress, 1200);
        return result;
      } catch (err) {
        hideProgress();
        throw err;
      } finally {
        setPaperBusy(false);
      }
    }

    const paperRunBtn = document.getElementById("paper-run");
    if (paperRunBtn) {
    paperRunBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        await runPaper(false);
      } catch (err) {
        setPaperMetaText(String(err.message || err));
      }
    });
    }

    const clusterRebalanceUi = createClusterRebalanceController({
      setPaperBusy,
      showProgress,
      hideProgress,
      setPaperMetaText,
      emptyReasonLabel,
      renderRebalanceReport,
      dismissRebalancePreview,
      loadPaper,
    });
    followClusterRef = clusterRebalanceUi;

    const paperAdjustBtn = document.getElementById("paper-adjust");
    if (paperAdjustBtn) {
    paperAdjustBtn.addEventListener("click", async (e) => {

      // 收起旧预演，再出新清单
      dismissRebalancePreview();
      e.preventDefault();
      try {
        // 手动预演：观察池实时算分 + path_matrix（唯一调仓路径）
        await followClusterRef.runWatchingMatrixPreview();
      } catch (err) {
        setPaperMetaText(String(err.message || err));
      }
    });
    }

    const rebalanceConfirmBtn = document.getElementById("paper-rebalance-confirm");
    if (rebalanceConfirmBtn) {
      rebalanceConfirmBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        if (rebalanceConfirmBtn.disabled) return;
        rebalanceConfirmBtn.disabled = true;
        try {
          const matrixPending =
            followClusterRef && followClusterRef.followMatrixPreviewPending;
          if (matrixPending && followClusterRef.runWatchingMatrixPreview) {
            if (
              !window.confirm(
                [
                  "确认按观察池 path_matrix 落账？",
                  "",
                  "将按预演清单改写 paper.json 持仓与现金。",
                  "确认时会重新算分与报价，清单可能与预演略有差异。",
                  "此为纸面模拟，非实盘委托。",
                ].join("\n")
              )
            ) {
              rebalanceConfirmBtn.disabled = false;
              return;
            }
            await followClusterRef.runWatchingMatrixPreview({ dryRun: false });
          } else {
            setPaperMetaText("请先点「预演调仓」，再确认落账");
            rebalanceConfirmBtn.disabled = false;
          }
        } catch (err) {
          setPaperMetaText(String(err.message || err));
          rebalanceConfirmBtn.disabled = false;
        }
      });
    }

    const rebalanceCloseBtn = document.getElementById("paper-rebalance-close");
    if (rebalanceCloseBtn) {
      rebalanceCloseBtn.addEventListener("click", () => {
        dismissRebalancePreview();
      });
    }

    const paperDailyBtn = document.getElementById("paper-daily");
    if (paperDailyBtn) {
    paperDailyBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const includePaper = paperInitialized;
      setPaperMetaText(includePaper
        ? "每日任务运行中（观察池 + checklist）…"
        : "纸面未初始化，仅运行 golden checklist…");
      setPaperBusy(true);
      showProgress(8, (document.getElementById("follow-meta")||document.getElementById("paper-meta")||{}).textContent || "");
      try {
        const data = await runDaily({
          paperRun: includePaper,
          paperBuy: false,
          evalMock: true,
          evalAgent: false,
        });
        const steps = formatDailySteps(data);
        if (!includePaper && data.eval_ok) {
          setPaperMetaText(`checklist 完成（已跳过纸面）· ${steps}`);
        } else if (data.ok) {
          setPaperMetaText(`每日任务完成 · ${steps}`);
        } else {
          const paperStep = (data.steps || []).find((s) => s.code === "paper_not_initialized");
          setPaperMetaText(paperStep
            ? `${paperStep.error} · ${steps}`
            : `部分失败 · ${steps || data.error || ""}`);
        }
        showProgress(100, (document.getElementById("follow-meta")||document.getElementById("paper-meta")||{}).textContent || "");
        if (includePaper) await loadPaper({ quiet: true });
        else {
          paperInitialized = false;
        }
        setTimeout(hideProgress, 1200);
      } catch (err) {
        hideProgress();
        setPaperMetaText(String(err.message || err));
      } finally {
        setPaperBusy(false);
      }
    });
    }

  }

  ctx.reloadPaper = loadPaper;
  ctx.focusPaperHolding = focusPaperHolding;

  window.__investmentGetCurrentStock = () => {
    const code = chartStockCode || selectedHoldCode;
    if (!code) return null;
    const holdings = (lastAccountData && lastAccountData.holdings) || [];
    const hit = holdings.find((h) => String(h.stock_code) === String(code));
    let name = hit?.stock_name || "";
    if (!name && holdingsGrid && holdingsGridReady) {
      const d = holdingsGrid.getRow(code)?.getData();
      if (d?.name) name = d.name;
    }
    return { code: String(code).trim(), name: String(name || "").trim() };
  };

  const paperRebalanceSummary = document.getElementById("paper-rebalance-summary");
  const paperT0Metrics = document.getElementById("paper-t0-metrics");
  const paperT0Viz = document.getElementById("paper-t0-viz");
  const paperT0Days = document.getElementById("paper-t0-days");
  const paperT0Preview = document.getElementById("paper-t0-preview");
  const paperT0Confirm = document.getElementById("paper-t0-confirm");

  function renderPaperT0(data) {
    // 回测成交明细必须用各日决策快照；勿盖持仓实时分（否则多日 ŷ 相同、负τ却正T）
    renderPaperT0Ui(
      {
        metricsEl: paperT0Metrics,
        vizEl: paperT0Viz,
        daysEl: paperT0Days,
        previewEl: paperT0Preview,
      },
      data
    );
  }

  function renderPaperT0Preview(data) {
    // 预演同行：用预演返回的会话快照，勿盖持仓实时分
    renderPaperT0PreviewUi(
      { previewEl: paperT0Preview, confirmEl: paperT0Confirm },
      data
    );
  }

  const paperRebalanceBtn = document.getElementById("paper-rebalance");
  if (paperRebalanceBtn) {
    paperRebalanceBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      if (paperRebalanceSummary) paperRebalanceSummary.textContent = "调仓中…";
      try {
        const res = await fetch("/api/paper/rebalance", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ top_k: 3, limit: 10 }),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          if (paperRebalanceSummary) {
            paperRebalanceSummary.textContent = data.error || data.detail || "调仓失败";
          }
          return;
        }
        if (paperRebalanceSummary) {
          paperRebalanceSummary.textContent =
            `卖出 ${(data.sell_trades || []).length} · 买入 ${(data.buy_trades || []).length} · 净值 ${(data.summary || {}).equity ?? "—"}`;
        }
        await loadPaper({ quiet: true });
      } catch (err) {
        if (paperRebalanceSummary) paperRebalanceSummary.textContent = String(err.message || err);
      }
    });
  }

  const paperT0Bt = document.getElementById("paper-t0-backtest");
  const paperT0Run = document.getElementById("paper-t0-run");
  const paperT0ActionStatus = document.getElementById("paper-t0-action-status");

  /** fetch 失败：Chrome 超时是 AbortError；Safari/网络断开常是 TypeError Failed to fetch */
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

  /** 手动预演 / 回测 / 落账共用：状态行 spinner + 当前按钮禁用 */
  const setT0ActionBusy = (btn, busy, msg) => {
    if (btn) {
      btn.disabled = !!busy;
      btn.classList.toggle("is-busy", !!busy);
      if (busy) btn.setAttribute("aria-busy", "true");
      else btn.removeAttribute("aria-busy");
    }
    if (paperT0ActionStatus) {
      paperT0ActionStatus.classList.toggle("is-busy", !!busy);
      if (busy) paperT0ActionStatus.setAttribute("aria-busy", "true");
      else paperT0ActionStatus.removeAttribute("aria-busy");
      if (msg != null) paperT0ActionStatus.textContent = msg;
    }
  };
  const clearT0ActionBusy = (btn) => {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-busy");
      btn.removeAttribute("aria-busy");
    }
    if (paperT0ActionStatus) {
      paperT0ActionStatus.classList.remove("is-busy");
      paperT0ActionStatus.removeAttribute("aria-busy");
    }
  };

  if (paperT0Bt) {
    paperT0Bt.addEventListener("click", async (e) => {
      e.preventDefault();
      if (paperT0Bt.disabled) return;
      const formRoot = document.getElementById("paper-t0-form");
      const scope = resolveT0BacktestScope(formRoot, {
        altKey: e.altKey,
        selectedCode: selectedHoldCode,
      });
      if (scope.error) {
        const fold = document.getElementById("follow-section-t0");
        if (fold) fold.scrollIntoView({ behavior: "smooth", block: "start" });
        if (paperT0ActionStatus) {
          paperT0ActionStatus.classList.remove("is-busy");
          paperT0ActionStatus.textContent = scope.error;
        }
        return;
      }
      const { onlySelected, useMinute } = scope;
      const busyLabel = onlySelected
        ? `回测中（${selectedHoldCode}·5m/选向）…`
        : "回测中（全部持仓·5m/选向）…";
      setT0ActionBusy(paperT0Bt, true, busyLabel);
      const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer =
        ac &&
        setTimeout(() => {
          try {
            ac.abort();
          } catch (_) {
            /* ignore */
          }
        }, 300000);
      try {
        const body = collectT0BacktestBody(formRoot, {
          useMinute,
          onlySelected,
          selectedCode: selectedHoldCode,
        });
        const res = await fetch("/api/quant/t0-backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: ac ? ac.signal : undefined,
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          const errMsg = data.error || data.detail || "回测失败";
          setT0ActionBusy(paperT0Bt, false, errMsg);
          renderPaperT0(null);
          return;
        }
        const summary = buildT0SummaryLine(data);
        setT0ActionBusy(paperT0Bt, false, summary || "做 T 回测完成");
        renderPaperT0(data);
        showValidateNext("t0");
        const t0RulesEl = document.getElementById("paper-t0-rules");
        if (t0RulesEl && data.execution) {
          t0RulesEl.innerHTML = renderExecutionRulesHtml(
            normalizeExecutionView(data.execution, data.rules)
          );
        }
      } catch (err) {
        setT0ActionBusy(
          paperT0Bt,
          false,
          t0FetchErrorMessage(
            err,
            "回测连接中断或超时。服务刚重启、持仓过多或 5m 未预热时会出现；请刷新后重试，持仓多请勾「仅选中」或缩小回看窗"
          )
        );
      } finally {
        if (timer) clearTimeout(timer);
        clearT0ActionBusy(paperT0Bt);
      }
    });
  }

  if (paperT0Run) {
    paperT0Run.addEventListener("click", async (e) => {
      e.preventDefault();
      if (paperT0Run.disabled) return;
      setT0ActionBusy(paperT0Run, true, "预演中…");
      const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer =
        ac &&
        setTimeout(() => {
          try {
            ac.abort();
          } catch (_) {
            /* ignore */
          }
        }, 180000);
      try {
        const res = await fetch("/api/paper/t0", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: true }),
          signal: ac ? ac.signal : undefined,
        });
        let data = {};
        try {
          data = await res.json();
        } catch (_) {
          data = {};
        }
        const detail = data.detail;
        const detailText =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((x) => x?.msg || x).filter(Boolean).join("；")
              : "";
        if (!res.ok || data.success === false) {
          const errMsg =
            data.error ||
            detailText ||
            (res.status === 503
              ? "账本正被其他操作占用，请稍后再试"
              : res.status === 404
                ? "请先初始化纸面账户"
                : "预演失败");
          setT0ActionBusy(paperT0Run, false, errMsg);
          renderPaperT0Preview(null);
          return;
        }
        setT0ActionBusy(
          paperT0Run,
          false,
          `预演完成 · 成交 ${(data.trades || []).length} · PnL ${data.pnl_total ?? 0} · ` +
            `跳过 ${data.skip_count ?? 0}` +
            (data.coupling_skip_count
              ? `（耦合 ${data.coupling_skip_count}）`
              : "") +
            ` · 确认后才会改账`
        );
        if (data.execution) {
          const t0RulesEl = document.getElementById("paper-t0-rules");
          if (t0RulesEl) {
            t0RulesEl.innerHTML = renderExecutionRulesHtml(
              normalizeExecutionView(data.execution, data.rules)
            );
          }
        }
        renderPaperT0Preview(data);
      } catch (err) {
        setT0ActionBusy(
          paperT0Run,
          false,
          t0FetchErrorMessage(
            err,
            "预演连接中断或超时。服务刚重启或持仓过多时会出现；请刷新后重试，也可先预热 5 分钟线"
          )
        );
      } finally {
        if (timer) clearTimeout(timer);
        clearT0ActionBusy(paperT0Run);
      }
    });
  }

  const paperT0Form = document.getElementById("paper-t0-form");
  const paperT0EditStatus = document.getElementById("paper-t0-edit-status");
  const paperT0DiffBox = document.getElementById("paper-t0-diff-box");
  async function refreshPaperAfterExecution(exec) {
    const t0RulesEl = document.getElementById("paper-t0-rules");
    if (t0RulesEl && exec) t0RulesEl.innerHTML = renderExecutionRulesHtml(exec);
    if (paperT0Form && exec) fillExecutionForm(paperT0Form, exec);
    const pathMatrixForm = document.getElementById("paper-path-matrix-form");
    if (pathMatrixForm && exec) fillPathMatrixForm(pathMatrixForm, exec);
    try {
      const { ok, data } = await apiFetch("/api/paper");
      if (ok) await renderPaperAccountDetail(data);
    } catch (_) {
      /* ignore */
    }
  }
  if (paperT0Form) {
    paperT0Form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = collectExecutionForm(paperT0Form);
      if (!body) return;
      // 回测窗不进 Spec：保存前先记住，避免 fill 用旧 localStorage 盖成 30
      persistT0Lookback(paperT0Form);
      if (paperT0EditStatus) paperT0EditStatus.textContent = "保存中…";
      try {
        const res = await fetch("/api/paper/execution", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, note: "follow UI" }),
        });
        const data = await res.json();
        if (!res.ok || data.ok === false) {
          const err = data.detail || data.errors || data.error || "保存失败";
          if (paperT0EditStatus) {
            paperT0EditStatus.textContent = Array.isArray(err) ? err.join("; ") : String(err);
          }
          return;
        }
        if (paperT0EditStatus) {
          paperT0EditStatus.textContent = data.message || "已保存";
        }
        await refreshPaperAfterExecution(data.execution);
      } catch (err) {
        if (paperT0EditStatus) paperT0EditStatus.textContent = String(err.message || err);
      }
    });
  }
  const paperPathMatrixForm = document.getElementById("paper-path-matrix-form");
  const paperPathMatrixStatus = document.getElementById("paper-path-matrix-status");
  const setPathMatrixStatus = (msg, { error = false } = {}) => {
    if (!paperPathMatrixStatus) return;
    const text = String(msg || "").trim();
    paperPathMatrixStatus.classList.remove("is-busy", "is-ok");
    paperPathMatrixStatus.classList.toggle("is-error", !!error && !!text);
    paperPathMatrixStatus.textContent = text;
    paperPathMatrixStatus.hidden = !text;
  };
  if (paperPathMatrixForm) {
    paperPathMatrixForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = collectPathMatrixForm(paperPathMatrixForm);
      if (!body) return;
      setPathMatrixStatus("保存中…");
      try {
        const res = await fetch("/api/paper/execution", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, note: "follow path_matrix UI" }),
        });
        const data = await res.json();
        if (!res.ok || data.ok === false) {
          const err = data.detail || data.errors || data.error || "保存失败";
          setPathMatrixStatus(
            Array.isArray(err) ? err.join("; ") : String(err),
            { error: true }
          );
          return;
        }
        setPathMatrixStatus(data.message || "已保存择时");
        await refreshPaperAfterExecution(data.execution);
      } catch (err) {
        setPathMatrixStatus(String(err.message || err), { error: true });
      }
    });
  }
  const paperT0Reset = document.getElementById("paper-t0-reset");
  if (paperT0Reset) {
    paperT0Reset.addEventListener("click", async (e) => {
      e.preventDefault();
      if (paperT0EditStatus) paperT0EditStatus.textContent = "重置中…";
      try {
        const res = await fetch("/api/paper/execution/reset", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          if (paperT0EditStatus) paperT0EditStatus.textContent = data.detail || "重置失败";
          return;
        }
        if (paperT0EditStatus) paperT0EditStatus.textContent = data.message || "已恢复默认";
        if (paperT0DiffBox) {
          paperT0DiffBox.hidden = true;
          paperT0DiffBox.innerHTML = "";
        }
        persistT0Lookback(paperT0Form);
        await refreshPaperAfterExecution(data.execution);
      } catch (err) {
        if (paperT0EditStatus) paperT0EditStatus.textContent = String(err.message || err);
      }
    });
  }
  const paperT0DiffBtn = document.getElementById("paper-t0-diff");
  if (paperT0DiffBtn) {
    paperT0DiffBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      if (paperT0EditStatus) paperT0EditStatus.textContent = "对照中…";
      try {
        const res = await fetch("/api/paper/execution/diff");
        const data = await res.json();
        if (!res.ok) {
          if (paperT0EditStatus) paperT0EditStatus.textContent = data.detail || "对照失败";
          return;
        }
        if (paperT0DiffBox) {
          paperT0DiffBox.hidden = false;
          paperT0DiffBox.innerHTML = renderExecutionDiffHtml(data);
        }
        if (paperT0EditStatus) {
          paperT0EditStatus.textContent = data.changed
            ? `相对 Spec 有 ${(data.t0_changes || []).length + (data.coupling_changes || []).length} 项差异`
            : "与策略默认一致";
        }
      } catch (err) {
        if (paperT0EditStatus) paperT0EditStatus.textContent = String(err.message || err);
      }
    });
  }

  if (paperT0Confirm) {
    paperT0Confirm.addEventListener("click", async (e) => {
      e.preventDefault();
      if (paperT0Confirm.disabled) return;
      setT0ActionBusy(paperT0Confirm, true, "落账中…");
      try {
        const res = await fetch("/api/paper/t0", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true, dry_run: false }),
        });
        const data = await res.json();
        if (!res.ok || data.success === false) {
          setT0ActionBusy(
            paperT0Confirm,
            false,
            data.error || data.detail || "写入失败"
          );
          return;
        }
        setT0ActionBusy(
          paperT0Confirm,
          false,
          `已手动落账 · 成交 ${(data.trades || []).length} · PnL ${data.pnl_total ?? 0}`
        );
        renderPaperT0Preview(null);
        await loadPaper({ quiet: true });
      } catch (err) {
        setT0ActionBusy(paperT0Confirm, false, String(err.message || err));
      } finally {
        clearT0ActionBusy(paperT0Confirm);
      }
    });
  }

  const paperT0WorkerEnabled = document.getElementById("paper-t0-worker-enabled");

  if (paperT0WorkerEnabled) {
    paperT0WorkerEnabled.addEventListener("change", () => {
      postT0Worker(!!paperT0WorkerEnabled.checked);
    });
  }

  const paperGotoQuant = document.getElementById("paper-goto-quant");
  if (paperGotoQuant) {
    paperGotoQuant.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        if (typeof ctx.showResultsTab === "function" && page === "chat") {
          await ctx.showResultsTab("quant", { openMobile: true, load: true });
        }
      } catch (err) {
        setPaperMetaText(String(err.message || err));
      }
    });
  }

  const paperTradesCsvBtn = document.getElementById("paper-trades-csv");
  if (paperTradesCsvBtn) {
    paperTradesCsvBtn.addEventListener("click", (e) => {
      e.preventDefault();
      const logs =
        lastPaperTradingLogs.length > 0
          ? lastPaperTradingLogs
          : (lastAccountData && lastAccountData.operation_log) || [];
      if (!logs.length) {
        alert("暂无交易记录可导出");
        return;
      }
      const csv = buildPaperLogsCsv(logs, { tradingOnly: true });
      const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
      downloadBlob(
        blob,
        `paper_trades_${new Date().toISOString().slice(0, 10)}.csv`
      );
    });
  }

  document.querySelectorAll(".paper-clear-records").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const category = btn.dataset.category;
      const label = category === "trading" ? "交易记录" : "资金记录";
      const confirmed = window.confirm(
        `确定清除全部「${label}」？此操作不可恢复，持仓与资金余额不受影响。`
      );
      if (!confirmed) return;
      btn.disabled = true;
      const originalText = btn.textContent;
      btn.textContent = "清除中…";
      try {
        const res = await fetch("/api/paper/clear-records", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category }),
        });
        const data = await res.json();
        if (!res.ok) {
          alert(`清除失败：${data.detail || data.error || "未知错误"}`);
          return;
        }
        paperLogShowAll[category === "fund" ? "fund" : "trading"] = false;
        await loadPaper({ quiet: true });
      } catch (err) {
        alert(`清除失败：${err.message || err}`);
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });
  });

  document.addEventListener("click", (e) => {
    const filterBtn = e.target.closest(".paper-log-filter-btn");
    if (filterBtn) {
      e.preventDefault();
      const next = filterBtn.dataset.logFilter || "all";
      paperLogFilter = next;
      document.querySelectorAll(".paper-log-filter-btn").forEach((btn) => {
        btn.classList.toggle("is-active", btn.dataset.logFilter === next);
      });
      if (lastAccountData) renderPaperAccountDetail(lastAccountData);
      return;
    }
    const moreBtn = e.target.closest(".paper-log-more");
    if (!moreBtn) return;
    e.preventDefault();
    const kind = moreBtn.dataset.logKind;
    if (kind !== "trading" && kind !== "fund") return;
    paperLogShowAll[kind] = !paperLogShowAll[kind];
    if (lastAccountData) renderPaperAccountDetail(lastAccountData);
  });

  if (page === "paper" || page === "follow") {
    const refreshBtn = document.getElementById("paper-holdings-refresh");
    if (refreshBtn && refreshBtn.dataset.wired !== "1") {
      refreshBtn.dataset.wired = "1";
      refreshBtn.addEventListener("click", async () => {
        try {
          await loadPaper();
        } catch (_) {
          /* status 已在 loadPaper 内处理 */
        }
      });
    }
    setHoldingsLoadStatus("加载中…", { busy: true });
    loadPaper().catch((err) => {
      setPaperMetaText(String(err.message || err));
    });
  }
}
