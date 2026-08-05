import { apiFetch } from "../api_client.js";
import { renderLineChart } from "../lw_charts.js";
import { mountVirtualTable, colStyle } from "../virtual_table.js";
import { fmtScore, scoreCls } from "../paper/fmt.js";
import { truncateStockName, watchingNameSpanHtml, watchingNameFromEl, applyWatchingNameEl, normalizeProbeCode } from "./names.js";
import { renderWatchingHoldings as renderWatchingHoldingsHtml } from "./watching_holdings.js";
import { buildWatchingDqMetaText, buildWatchingDqFoldSummary, buildWatchingDqTableHtml } from "./watching_dq_ui.js";
import { watchingScoreDetail, sentimentBadgeHtml, renderWatchingBuildPlan as renderWatchingBuildPlanHtml, renderWatchingWatchTableFallback, buildWatchingWatchRows, buildWatchingNewsTitleHtml, buildWatchingNewsMetaText, buildWatchingNewsListHtml, WATCHING_NEWS_AI_LOADING_HTML, buildWatchingNewsAiAnalysisHtml, buildWatchingNewsAiErrorHtml, describeWatchingSource, matchWatchlistSource, truncateText, sentimentLabelZh, shortOriginLabel } from "./watching_render.js";
import {
  buildWatchingScoreDisplay,
  buildWatchingInsightsGridPatch,
  buildWatchingScoreCellHtml,
  buildWatchingInsightsStatusText,
  buildWatchingInsightsErrorStatus,
  buildWatchingInsightsGridErrorPatch,
  buildWatchingInsightsNativeFields,
} from "./watching_insights_ui.js";
import {
  parseWatchingVolume,
  formatWatchingChg,
  buildWatchingQuoteGridPatch,
  buildWatchingQuotesStatusText,
  buildWatchingQuotesErrorStatus,
} from "./watching_quotes_ui.js";
import {
  watchingBuildInvalidTip,
  watchingBuildTitleText,
  buildWatchingBuildPayload,
  watchingBuildModeUiConfig,
} from "./watching_build_ui.js";
import {
  formatRefreshStats,
  buildWatchingNameByCode,
  watchingPoolMetaText,
  watchingQuantListHtml,
  watchingPanelShellFlags,
  applyWatchingPanelShell,
  watchingChartSeriesFromPoints,
  watchingChartLabelText,
} from "./watching_panel_ui.js";

const WATCHING_SORT_STORAGE = "watching_table_sort_v1";
const WATCHING_SORT_KEYS = new Set(["name", "chg", "score", "excess", "vol"]);
const Q_COLORS = ["#9ca3af", "#93c5fd", "#60a5fa", "#34d399", "#059669", "#047857", "#065f46"];

/** Quant domain: watching */
export function installWatching(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText, watchingScoreTips } = q;

  let watchingAlertCodes = new Set();
  let watchingSentimentByCode = {};
  let watchingBuildCodes = [];
  let watchingBuildSeq = 0;
  try {
    const saved = JSON.parse(sessionStorage.getItem(WATCHING_SORT_STORAGE) || "null");
    if (saved && WATCHING_SORT_KEYS.has(saved.key)) {
      state.watchingSortKey = saved.key;
      state.watchingSortDir = saved.dir === "asc" ? "asc" : "desc";
    }
  } catch (_) { /* ignore */ }

  async function addWatchingWatchItem(query) {
    const queryText = String(query || "").trim();
    if (!queryText) return;
    setWatchingRefreshStatus(`正在加入「${queryText}」…`);
    try {
      const res = await fetch("/api/watching/watchlist/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: queryText, sync_paper: false }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      hideWatchingSearchResults();
      const input = document.getElementById("watching-search-input");
      if (input) input.value = "";
      await loadWatchingPanel();
      setWatchingRefreshStatus(data.message || "已加入观察");
    } catch (err) {
      const msg = String(err.message || err);
      setWatchingRefreshStatus(msg, { error: true });
    }
  }

  function applySentimentAlertRows(data) {
    const alerts = (data && data.alerts) || [];
    const isScan = !data || !data.kind || data.kind === "sentiment_scan";
    watchingAlertCodes = new Set(
      isScan
        ? alerts.map((a) => String(a.stock_code || "").trim()).filter(Boolean)
        : []
    );
    if (state.watchingGrid && state.watchingGridReady) {
      (state.watchingGrid.getData() || []).forEach((row) => {
        const code = String((row && row.code) || "");
        const comp = state.watchingGrid.getRow(code);
        if (comp) comp.update({ isSentimentAlert: watchingAlertCodes.has(code) });
      });
    }
    return alerts;
  }

  function applySentimentToRow(code, row) {
    if (!state.watchingGrid || !state.watchingGridReady) return;
    const key = String(code || "").trim();
    if (!key) return;
    const comp = state.watchingGrid.getRow(key);
    if (!comp) return;
    const alert = watchingAlertCodes.has(key);
    comp.update({
      sentHtml: sentimentBadgeHtml((row && row.sentiment) || {}, key),
      isSentimentAlert: alert,
    });
  }

  function closeWatchingBuildLayer() {
    const layer = document.getElementById("watching-build-layer");
    if (layer) layer.hidden = true;
    watchingBuildCodes = [];
    state.watchingBuildSharesByCode = {};
    state.watchingBuildAmountByCode = {};
    watchingBuildSeq += 1;
    setWatchingBuildStatus("");
  }

  async function confirmWatchingBuild() {
    if (!watchingBuildCodes.length) return;
    const payload = watchingBuildPayload();
    if (!payload) {
      setWatchingBuildStatus("请检查定量参数", { error: true });
      return;
    }
    const codes = watchingBuildCodes.slice();
    const confirmBtn = document.getElementById("watching-build-confirm");
    if (confirmBtn) confirmBtn.disabled = true;
    setWatchingBuildStatus("建仓中…");
    try {
      const res = await fetch("/api/watching/sync-paper", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          codes,
          ...payload,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const sync = data.paper_sync || {};
      const summary = sync.message || `已买入 ${sync.bought_count || 0} 只`;
      closeWatchingBuildLayer();
      await loadWatchingPanel();
      setPoolMeta(summary);
      setWatchingRefreshStatus(summary, { error: !sync.bought_count });
      await loadFollowCard();
      if (typeof ctx.reloadPaper === "function") {
        try {
          await ctx.reloadPaper();
        } catch (_) {}
      }
    } catch (err) {
      setWatchingBuildStatus(String(err.message || err), { error: true });
      if (confirmBtn) confirmBtn.disabled = false;
    }
  }


  function drawWatchingChart(pts) {
    const host = document.getElementById("watching-chart");
    if (!host) return;
    renderLineChart(host, watchingChartSeriesFromPoints(pts), {
      emptyText: "暂无足够日线数据",
      ma: [5, 10, 20],
      disableZoom: true,
    }).catch(() => {});
  }

  function ensureResearchDock() {
    if (typeof window.__investmentInitResearchDock === "function") {
      try {
        window.__investmentInitResearchDock();
      } catch (_) {
        /* ignore */
      }
      return;
    }
    import(
      `../research_dock.js?v=${
        typeof window !== "undefined" && window.__ASSET_V__ ? window.__ASSET_V__ : "p283"
      }`
    )
      .then((m) => {
        window.__investmentInitResearchDock = m.initResearchDock;
        m.initResearchDock();
      })
      .catch(() => {});
  }

  async function fetchPaperHeldMap() {
    const ctx = await fetchPaperWatchContext();
    return ctx.held;
  }

  async function fetchPaperWatchContext() {
    const held = new Map();
    let buildLogs = [];
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 8000);
      let res;
      try {
        res = await fetch("/api/paper?lite=1", { signal: ctrl.signal });
      } finally {
        clearTimeout(timer);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.initialized) return { held, buildLogs };
      for (const h of (data.summary && data.summary.holdings) || data.holdings || []) {
        const code = String((h && h.stock_code) || "").trim();
        if (!code) continue;
        const shares = Number(h.shares);
        held.set(code, Number.isFinite(shares) ? shares : null);
      }
      const logs = Array.isArray(data.operation_log) ? data.operation_log : [];
      buildLogs = logs.filter((l) => l && l.type === "sync_paper");
      return { held, buildLogs };
    } catch (_) {
      return { held, buildLogs };
    }
  }

  async function fetchWatchingNewsAI(code, stock_code) {
    const aiSection = document.getElementById("watching-news-ai-section");
    const aiContent = document.getElementById("watching-news-ai-content");
    const aiStatus = document.getElementById("watching-news-ai-status");
    if (!aiSection || !aiContent) return;
    aiSection.hidden = false;
    aiContent.innerHTML = WATCHING_NEWS_AI_LOADING_HTML;
    if (aiStatus) aiStatus.textContent = "分析中…";
    try {
      const res = await fetch(
        `/api/watching/sentiment/${encodeURIComponent(stock_code || code)}/analysis`
      );
      const data = await res.json().catch(() => ({}));
      if (data.ok && data.analysis) {
        aiContent.innerHTML = buildWatchingNewsAiAnalysisHtml(data.analysis, escapeHtml);
        if (aiStatus) aiStatus.textContent = "分析完成";
      } else {
        aiContent.innerHTML = buildWatchingNewsAiErrorHtml(
          data.analysis || "分析失败",
          escapeHtml
        );
        if (aiStatus) aiStatus.textContent = "分析失败";
      }
    } catch (err) {
      aiContent.innerHTML = buildWatchingNewsAiErrorHtml(
        `AI 分析请求失败: ${String(err.message || err)}`,
        escapeHtml
      );
      if (aiStatus) aiStatus.textContent = "分析失败";
    }
  }

  async function fillWatchingInsights() {
    const useGrid = !!(state.watchingGrid && state.watchingGridReady);
    const watchTable = document.getElementById("watching-watchlist-table");
    const codes = useGrid
      ? (state.watchingGrid.getData() || []).map((r) => r.code).filter(Boolean)
      : Array.from(watchTable?.querySelectorAll("tr[data-code]") || [])
          .map((r) => r.dataset.code)
          .filter(Boolean);
    if (!codes.length) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 100000);
    const insightDeps = { fmtScore, scoreCls, parseWatchingVolume, watchingScoreDetail };
    try {
      const res = await fetch(
        `/api/watching/insights?codes=${encodeURIComponent(codes.join(","))}`,
        { signal: ctrl.signal }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const byCode = indexByWatchingCode(data.items || []);
      codes.forEach((code) => {
        const it = byCode[watchingCodeKey(code)] || {};
        if (useGrid) {
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          row.update(buildWatchingInsightsGridPatch(it, row, insightDeps));
          return;
        }
        const tr = watchTable?.querySelector(
          `tr[data-code="${String(code).replace(/"/g, "")}"]`
        );
        if (!tr) return;
        const setTxt = (key, val) => {
          const el = tr.querySelector(`[data-q='${key}']`);
          if (el) el.textContent = val != null && val !== "" ? String(val) : "—";
        };
        const disp = buildWatchingScoreDisplay(it, fmtScore, watchingScoreDetail);
        const scoreEl = tr.querySelector(`[data-q='score']`);
        if (scoreEl) {
          scoreEl.innerHTML = buildWatchingScoreCellHtml(disp, scoreCls, escapeHtml);
        }
        const fields = buildWatchingInsightsNativeFields(it);
        setTxt("stance", fields.stance);
        setTxt("excess", fields.excess);
        if (fields.vol) setTxt("vol", fields.vol);
        setTxt("volr", fields.volr);
        setTxt("pe", fields.pe);
        setTxt("pb", fields.pb);
      });
      const items = data.items || [];
      const okN = items.filter((x) => x.ok).length;
      setWatchingRefreshStatus(buildWatchingInsightsStatusText(okN, codes.length, items));
      if (useGrid) sortWatchingTableRows();
    } catch (err) {
      if (useGrid) {
        codes.forEach((code) => {
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          row.update(buildWatchingInsightsGridErrorPatch(row.getData()));
        });
      }
      setWatchingRefreshStatus(buildWatchingInsightsErrorStatus(err), { error: true });
    } finally {
      clearTimeout(timer);
    }
  }

  async function fillWatchingQuotes() {
    if (state.watchingGrid && state.watchingGridReady) {
      const codes = (state.watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
      if (!codes.length) return;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 20000);
      setWatchingRefreshStatus("正在拉取行情…");
      try {
        const res = await fetch(
          `/api/watching/quotes?codes=${encodeURIComponent(codes.join(","))}`,
          { signal: ctrl.signal }
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        const byCode = indexByWatchingCode(data.items || []);
        codes.forEach((code) => {
          const it = byCode[watchingCodeKey(code)] || {};
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          row.update(buildWatchingQuoteGridPatch(it, row, parseWatchingVolume));
        });
        const okN = (data.items || []).filter((x) => x.ok).length;
        setWatchingRefreshStatus(buildWatchingQuotesStatusText(okN, codes.length));
        sortWatchingTableRows();
      } catch (err) {
        setWatchingRefreshStatus(buildWatchingQuotesErrorStatus(err), { error: true });
      } finally {
        clearTimeout(timer);
      }
      return;
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    const trs = watchTable.querySelectorAll("tr[data-code]");
    if (!trs.length) return;
    const codes = Array.from(trs).map((r) => r.dataset.code).filter(Boolean);
    try {
      const res = await fetch(`/api/watching/quotes?codes=${encodeURIComponent(codes.join(","))}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const byCode = indexByWatchingCode(data.items || []);
      trs.forEach((tr) => {
        const it = byCode[watchingCodeKey(tr.dataset.code)] || {};
        const setTxt = (key, val) => {
          const el = tr.querySelector(`[data-q='${key}']`);
          if (el) el.textContent = val != null && val !== "" ? String(val) : "—";
        };
        setTxt("price", it.ok ? it.price : null);
        setTxt("vol", it.ok ? it.volume : null);
        const chgEl = tr.querySelector(`[data-q='chg']`);
        if (chgEl) {
          const { chgTxt, chgCls } = formatWatchingChg(it.ok ? it.change_percent : null);
          chgEl.textContent = chgTxt;
          chgEl.classList.remove("is-up", "is-down");
          if (chgCls) chgEl.classList.add(chgCls);
        }
        if (it.ok && it.stock_name) {
          applyWatchingNameEl(tr.querySelector(".watching-name-text"), it.stock_name);
        }
      });
      const okN = (data.items || []).filter((x) => x.ok).length;
      setWatchingRefreshStatus(buildWatchingQuotesStatusText(okN, codes.length));
    } catch (err) {
      setWatchingRefreshStatus(buildWatchingQuotesErrorStatus(err), { error: true });
    }
  }

  async function fillWatchingSentiment() {
    if (!state.watchingGrid || !state.watchingGridReady) return;
    const codes = (state.watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
    if (!codes.length) return;
    try {
      const res = await fetch(
        `/api/watching/sentiment?codes=${encodeURIComponent(codes.join(","))}&limit=3`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const byCode = indexByWatchingCode(data.items || []);
      watchingSentimentByCode = byCode;
      codes.forEach((code) => {
        applySentimentToRow(code, byCode[watchingCodeKey(code)] || { ok: false, items: [] });
      });
    } catch (_) {
      // 填充失败时保持默认状态
    }
  }

  function getSelectedWatchingCodes() {
    if (state.watchingGrid && state.watchingGridReady) {
      return (state.watchingGrid.getData() || [])
        .filter((r) => r && r.picked)
        .map((r) => String(r.code || "").trim())
        .filter(Boolean);
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return [];
    return Array.from(watchTable.querySelectorAll(".watching-pick:checked"))
      .map((el) => String(el.value || el.dataset.code || "").trim())
      .filter(Boolean);
  }

  function gotoFollowPage(code) {
    const c = String(code || "").trim();
    if (c && typeof ctx.focusPaperHolding === "function") {
      ctx.focusPaperHolding(c);
    }
    if (typeof ctx.showResultsTab === "function") {
      ctx.showResultsTab("follow");
      return;
    }
    const q = c ? `?code=${encodeURIComponent(c)}` : "";
    window.location.href = `/follow${q}`;
  }

  async function gotoFollowTab() {
    const page = document.body.dataset.page;
    if (typeof ctx.showResultsTab === "function" && (page === "chat" || !page)) {
      await ctx.showResultsTab("follow", { openMobile: true, load: true });
    }
  }

  function hideWatchingChart() {
    const section = document.getElementById("watching-chart-section");
    if (section) section.hidden = true;
    state.watchingFocusCode = null;
    state.watchingFocusName = "";
    document.querySelectorAll("#watching-watchlist-table .is-chart-active").forEach((rowEl) => {
      rowEl.classList.remove("is-chart-active");
    });
  }

  function hideWatchingNewsDetail() {
    const panel = document.getElementById("watching-news-detail");
    if (panel) panel.hidden = true;
  }

  function hideWatchingSearchResults() {
    const box = document.getElementById("watching-search-results");
    if (box) {
      box.hidden = true;
      box.innerHTML = "";
    }
  }

  function indexByWatchingCode(items) {
    const byCode = {};
    for (const it of items || []) {
      const c = watchingCodeKey(it.stock_code || it.code);
      if (c) byCode[c] = it;
    }
    return byCode;
  }

  async function loadFollowCard() {
    const el = document.getElementById("quant-follow-summary");
    if (!el) return null;
    el.textContent = "纸面：加载中…";
    try {
      const res = await fetch("/api/paper");
      const data = await res.json();
      if (!data.initialized) {
        el.textContent = "纸面：未初始化 · 打开纸面 Tab 创建";
        return data;
      }
      const wl = (data.watchlist || []).length;
      const eq = data.equity ?? data.nav ?? data.summary?.equity;
      el.textContent = [
        data.name || "paper",
        `观察 ${wl} 只`,
        eq != null ? `净值 ${eq}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return data;
    } catch (err) {
      el.textContent = `纸面：${err.message || err}`;
      return null;
    }
  }

  async function loadWatchingDataQuality() {
    const meta = document.getElementById("watching-dq-meta");
    const table = document.getElementById("watching-dq-table");
    if (!table) return;
    if (meta) meta.textContent = "加载中…";
    try {
      const watching = await apiFetch("/api/watching");
      if (!watching.ok) throw new Error(watching.error || "观察接口失败");
      const wl = watching.data.watchlist || [];
      const paper = await apiFetch("/api/paper");
      const dq =
        (paper.ok && paper.data && paper.data.ops_report && paper.data.ops_report.data_quality) ||
        {};
      const health = await apiFetch("/api/daily/health");
      const warnings =
        (health.ok && (health.data.warnings || [])) || [];
      if (meta) {
        meta.textContent =
          `观察 ${wl.length} 只` +
          (paper.ok ? "" : " · 纸面缓存不可用") +
          (warnings.length ? ` · 告警 ${warnings.length}` : "");
      }
      // 不打开折叠也要能读到一句结论（折叠 summary 可见）
      const foldSummary = document.querySelector(
        "#watching-data-quality-fold > summary"
      );
      if (foldSummary) {
        const fallback = dq && dq.fallback_count != null ? Number(dq.fallback_count) : null;
        const tail =
          warnings.length ? `告警 ${warnings.length} 条` : fallback && fallback > 0 ? `fallback ${fallback}` : "良好";
        foldSummary.textContent = `数据质量 · ${tail}`;
      }
      const warnRows = warnings
        .slice(0, 8)
        .map((w) => `<tr><td colspan="2">${escapeHtml(String(w))}</td></tr>`)
        .join("");
      table.innerHTML =
        `<table class="quant-weight-table"><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>` +
        `<tr><td>样本数</td><td class="num">${escapeHtml(String(dq.count ?? "—"))}</td></tr>` +
        `<tr><td>fallback</td><td class="num">${escapeHtml(String(dq.fallback_count ?? "—"))}</td></tr>` +
        `<tr><td>gated</td><td class="num">${escapeHtml(String(dq.gated_count ?? "—"))}</td></tr>` +
        `<tr><td>复权策略</td><td>${escapeHtml(String(dq.adjust_policy ?? "—"))}</td></tr>` +
        (warnRows
          ? `<tr><td colspan="2"><strong>健康警告</strong></td></tr>${warnRows}`
          : "") +
        `</tbody></table>` +
        `<p class="quant-attr-note">质量摘要来自最近纸面调仓五问；专用逐票质量 API 后续可接。</p>`;
    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      const foldSummary = document.querySelector(
        "#watching-data-quality-fold > summary"
      );
      if (foldSummary) foldSummary.textContent = "数据质量 · 加载失败";
    }
  }

  async function loadWatchingPanel() {
    const shellEls = {
      emptyEl: document.getElementById("watching-empty"),
      gridEl: document.getElementById("watching-align-grid"),
      mainActions: document.getElementById("watching-main-actions"),
      searchWrap: document.getElementById("watching-search-wrap"),
    };
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    let uniRes;
    let paperCtx = { held: new Map(), buildLogs: [] };
    try {
      // 名单与纸面上下文解耦：纸面失败/超时不挡主表
      const watchingP = fetch("/api/watching", { signal: ctrl.signal }).then((r) =>
        r.json()
      );
      const paperP = fetchPaperWatchContext().catch(() => ({
        held: new Map(),
        buildLogs: [],
      }));
      uniRes = await watchingP;
      paperCtx = await paperP;
    } catch (err) {
      const msg =
        err && err.name === "AbortError"
          ? "观察名单加载超时，请刷新"
          : String((err && err.message) || err);
      setPoolMeta(msg);
      throw err;
    } finally {
      clearTimeout(timer);
    }
    const paperCodes = paperCtx.held || new Map();
    const data = uniRes;
    applyWatchingPanelShell(watchingPanelShellFlags(!!data.exists), shellEls);
    if (!data.exists) {
      setPoolMeta("尚未创建");
      const watchTable = document.getElementById("watching-watchlist-table");
      if (state.watchingGrid && typeof state.watchingGrid.destroy === "function") {
        state.watchingGrid.destroy();
      }
      state.watchingGrid = null;
      state.watchingGridReady = false;
      if (watchTable) watchTable.innerHTML = "";
      if (els.quantWatchingList) els.quantWatchingList.innerHTML = "";
      renderWatchingHoldings({}, [], []);
      return data;
    }
    wireWatchingSearch();
    ensureResearchDock();
    const uni = data.watching || {};
    const wl = uni.watchlist || [];
    const names = Array.isArray(uni.watchlist_names) ? uni.watchlist_names : [];
    state.watchingNameByCode = buildWatchingNameByCode(wl, names);
    const paperN = wl.filter((c) => paperCodes.has(String(c))).length;
    setPoolMeta(watchingPoolMetaText(wl.length, paperN, uni.max_size));
    await renderWatchingWatchTable(wl, names, paperCodes, uni.watchlist_scores || {});
    renderWatchingHoldings(
      uni.watchlist_holdings || {},
      names,
      paperCtx.buildLogs || []
    );
    if (els.quantWatchingList) {
      els.quantWatchingList.innerHTML = watchingQuantListHtml(wl, names, escapeHtml);
    }
    if (wl.length) {
      fillWatchingQuotes().catch(() => {});
      fillWatchingInsights().catch(() => {});
      fillWatchingSentiment().catch(() => {});
    }
    loadWatchingSentimentAlerts().catch(() => {});
    return data;
  }

  async function loadWatchingSentimentAlerts() {
    try {
      const res = await fetch("/api/watching/sentiment/alerts");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return;
      applySentimentAlertRows(data);
    } catch (_) {
      /* ignore */
    }
  }


  function openWatchingBuildLayer(codes, label) {
    const list = Array.from(
      new Set((codes || []).map((c) => String(c || "").trim()).filter(Boolean))
    );
    if (!list.length) {
      setWatchingRefreshStatus("请先勾选要建仓的股票", { error: true });
      return;
    }
    const layer = document.getElementById("watching-build-layer");
    if (!layer) return;
    watchingBuildCodes = list;
    state.watchingBuildSharesByCode = {};
    state.watchingBuildAmountByCode = {};
    state.watchingBuildMode = "amount";
    syncWatchingBuildModeUI();
    const titleEl = document.getElementById("watching-build-title");
    if (titleEl) titleEl.textContent = watchingBuildTitleText(label, list.length);
    const body = document.getElementById("watching-build-body");
    if (body) body.innerHTML = "";
    layer.hidden = false;
    const input = document.getElementById("watching-build-shares-input");
    if (input) input.focus();
    refreshWatchingBuildPreview().catch(() => {});
  }

  async function openWatchingNewsDetail(code) {
    const panel = document.getElementById("watching-news-detail");
    const titleEl = document.getElementById("watching-news-detail-title");
    const metaEl = document.getElementById("watching-news-detail-meta");
    const listEl = document.getElementById("watching-news-detail-list");
    if (!panel || !listEl) return;
    panel.hidden = false;
    if (titleEl) titleEl.textContent = `资讯 · ${code}`;
    if (metaEl) metaEl.textContent = "加载中…";
    listEl.innerHTML = "";
    try {
      const res = await fetch(
        `/api/watching/sentiment/${encodeURIComponent(code)}?limit=8`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const name = data.stock_name || code;
      const sent = data.sentiment || {};
      const sc = data.stock_code || code;
      if (titleEl) {
        titleEl.innerHTML = buildWatchingNewsTitleHtml(name, sc, sent, sentimentBadgeHtml);
      }
      if (metaEl) metaEl.textContent = buildWatchingNewsMetaText(data, sent);
      listEl.innerHTML = buildWatchingNewsListHtml(data.items || [], data);
      watchingSentimentByCode[String(code)] = data;
      applySentimentToRow(code, data);
      fetchWatchingNewsAI(code, sc);
    } catch (err) {
      if (metaEl) metaEl.textContent = String(err.message || err);
      listEl.innerHTML = `<li class="watching-news-empty">加载失败</li>`;
    }
  }

  function persistWatchingSort() {
    try {
      if (!state.watchingSortKey) {
        sessionStorage.removeItem(WATCHING_SORT_STORAGE);
        return;
      }
      sessionStorage.setItem(
        WATCHING_SORT_STORAGE,
        JSON.stringify({ key: state.watchingSortKey, dir: state.watchingSortDir })
      );
    } catch (_) {
      /* ignore */
    }
  }

  async function refreshWatchingBuildPreview() {
    const body = document.getElementById("watching-build-body");
    const confirmBtn = document.getElementById("watching-build-confirm");
    if (!watchingBuildCodes.length || !body) return;
    const payload = watchingBuildPayload();
    if (!payload) {
      body.innerHTML = "";
      setWatchingBuildStatus(watchingBuildInvalidTip(state.watchingBuildMode), { error: true });
      if (confirmBtn) confirmBtn.disabled = true;
      return;
    }
    const seq = ++watchingBuildSeq;
    setWatchingBuildStatus("按现价试算中…");
    if (confirmBtn) confirmBtn.disabled = true;
    try {
      const res = await fetch("/api/watching/sync-paper/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          codes: watchingBuildCodes,
          ...payload,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== watchingBuildSeq) return;
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderWatchingBuildPlan(data);
      setWatchingBuildStatus("");
    } catch (err) {
      if (seq !== watchingBuildSeq) return;
      body.innerHTML = "";
      setWatchingBuildStatus(String(err.message || err), { error: true });
    }
  }

  function refreshWatchingSortHeaders() {
    // 虚拟表表头自带排序状态
  }

  async function removeSelectedWatchingItems() {
    const codes = getSelectedWatchingCodes();
    if (!codes.length) {
      setWatchingRefreshStatus("请先勾选要移除的股票", { error: true });
      return;
    }
    const ok = window.confirm(
      codes.length === 1
        ? `确定从观察名单移除「${codes[0]}」？\n（不影响模拟持仓）`
        : `确定从观察名单移除已勾选的 ${codes.length} 只？\n（不影响模拟持仓）`
    );
    if (!ok) return;
    setWatchingRefreshStatus(`正在移除 ${codes.length} 只…`);
    let done = 0;
    const errors = [];
    for (const c of codes) {
      try {
        const res = await fetch("/api/watching/watchlist/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: c, sync_paper: false }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        done += 1;
      } catch (err) {
        errors.push(`${c}: ${String(err.message || err)}`);
      }
    }
    await loadWatchingPanel();
    if (errors.length) {
      setWatchingRefreshStatus(
        `已移除 ${done} 只 · 失败 ${errors.length}：${errors[0]}`,
        { error: true }
      );
    } else {
      setWatchingRefreshStatus(`已从观察名单移除 ${done} 只`);
    }
  }

  async function removeWatchingWatchItem(code) {
    const c = String(code || "").trim();
    if (!c) return;
    setWatchingRefreshStatus(`正在移除「${c}」…`);
    try {
      const res = await fetch("/api/watching/watchlist/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: c, sync_paper: false }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      await loadWatchingPanel();
      setWatchingRefreshStatus(data.message || "已移除");
    } catch (err) {
      const msg = String(err.message || err);
      setWatchingRefreshStatus(msg, { error: true });
    }
  }

  function renderWatchingBuildPlan(plan) {
    renderWatchingBuildPlanHtml(plan, {
      bodyEl: document.getElementById("watching-build-body"),
      confirmBtnEl: document.getElementById("watching-build-confirm"),
      mode: state.watchingBuildMode,
      defaultVal: watchingBuildDefaultValue(),
      editableCodes: watchingBuildCodes.slice(),
      amountByCode: state.watchingBuildAmountByCode,
      sharesByCode: state.watchingBuildSharesByCode,
    });
  }

  function renderWatchingHoldings(holdingsMap, names, buildLogs) {
    renderWatchingHoldingsHtml(holdingsMap, names, buildLogs, {
      sectionEl: document.getElementById("watching-holdings-section"),
      tableEl: document.getElementById("watching-holdings-table"),
    });
  }

  function renderWatchingSearchResults(items, query, note) {
    const box = document.getElementById("watching-search-results");
    if (!box) return;
    const list = Array.isArray(items) ? items : [];
    if (!list.length) {
      box.hidden = false;
      const tip = note
        ? `<p class="watching-search-empty">${escapeHtml(String(note))}</p>`
        : "";
      box.innerHTML =
        `<p class="watching-search-empty">未找到「${escapeHtml(query || "")}」</p>` + tip;
      return;
    }
    box.hidden = false;
    box.innerHTML = list
      .map((it) => {
        const code = String(it.stock_code || "");
        const name = String(it.stock_name || code);
        const hint = String(it.hint || "加入");
        return (
          `<button type="button" class="watching-search-item" data-code="${escapeHtml(code)}" data-name="${escapeHtml(name)}">` +
          `<span class="watching-search-item-main">` +
          `<span class="watching-search-item-name">${escapeHtml(name)}</span>` +
          `<span class="watching-search-item-code">${escapeHtml(code)}</span>` +
          `</span>` +
          `<span class="watching-search-item-hint">${escapeHtml(hint)}</span>` +
          `</button>`
        );
      })
      .join("");
  }

  async function renderWatchingWatchTable(wl, names, paperCodes, scores) {
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    if (state.watchingGrid && typeof state.watchingGrid.destroy === "function") {
      try {
        state.watchingGrid.destroy();
      } catch (_) {
        /* ignore */
      }
    }
    state.watchingGrid = null;
    state.watchingGridReady = false;
    if (!wl.length) {
      watchTable.innerHTML = `<p class="watching-table-empty">暂无观察 · 上方搜索加入</p>`;
      hideWatchingNewsDetail();
      updateWatchingPickCount();
      return;
    }
    const inPaper =
      paperCodes instanceof Map
        ? paperCodes
        : new Map(Array.from(paperCodes || []).map((c) => [String(c), null]));
    const rowByCode = new Map();
    for (let i = 0; i < wl.length; i++) {
      const code = String(wl[i] || "").trim();
      if (!code) continue;
      const name = (names[i] && String(names[i]).trim()) || "—";
      const scoreRaw = scores && scores[code];
      const scoreNum = scoreRaw == null || Number.isNaN(Number(scoreRaw)) ? null : Number(scoreRaw);
      const onPaper = inPaper.has(code);
      const heldShares = onPaper ? inPaper.get(code) : null;
      rowByCode.set(code, {
        code,
        name,
        picked: false,
        market: "",
        paper: onPaper ? (heldShares != null ? `${heldShares} 股` : "已持") : "建仓",
        onPaper,
        sentHtml: `<span class="watching-sent-badge is-neutral" data-code="${escapeHtml(code)}" title="加载中">…</span>`,
        price: "—",
        chg: "—",
        chgCls: "",
        score: scoreNum == null ? "…" : fmtScore(scoreNum),
        scoreNum,
        scoreCls: scoreCls(scoreNum),
        stance: "…",
        excess: "…",
        excessNum: null,
        vol: "…",
        volNum: null,
        volr: "…",
        pe: "…",
        pb: "…",
        isHardReject: false,
        isSentimentAlert: watchingAlertCodes.has(code),
      });
    }
    const rows = Array.from(rowByCode.values());
    try {
      const V =
        (typeof window !== "undefined" && window.__ASSET_V__) || "p315";
      const mod = await import(`../watching_table_island.js?v=${V}`);
      watchTable.innerHTML =
        `<div id="watching-react-root" class="watching-react-grid-host"></div>`;
      const host = document.getElementById("watching-react-root");
      if (!host) throw new Error("watching host missing");
      const grid = await mod.mountWatchingTableIsland(host, {
        initialSort: state.watchingSortKey
          ? [{ column: state.watchingSortKey, dir: state.watchingSortDir === "asc" ? "asc" : "desc" }]
          : [],
      });
      grid.on("sortChanged", (sorters) => {
        const s = Array.isArray(sorters) && sorters.length ? sorters[0] : null;
        const key = s && s.field;
        if (key === "name" || key === "score" || key === "excess" || key === "vol") {
          state.watchingSortKey = key;
          state.watchingSortDir = s.dir === "asc" ? "asc" : "desc";
          persistWatchingSort();
        }
      });
      grid.setRows(rows);
      state.watchingGrid = grid;
      state.watchingGridReady = true;
      updateWatchingPickCount();
      syncWatchingSelectAllState();
    } catch (err) {
      console.warn("[watching] 虚拟表挂载失败，回退原生表", err);
      state.watchingGrid = null;
      state.watchingGridReady = false;
      renderWatchingWatchTableFallback(rows);
    }
  }

  async function runWatchingSearch(raw) {
    const queryText = String(raw || "").trim();
    const box = document.getElementById("watching-search-results");
    if (!queryText) {
      hideWatchingSearchResults();
      return;
    }
    const seq = ++state.watchingSearchSeq;
    // 有旧结果时不闪「搜索中」，避免体感卡顿
    if (box && box.hidden) {
      box.hidden = false;
      box.innerHTML = `<p class="watching-search-empty">搜索中…</p>`;
    }
    try {
      const res = await fetch(`/api/watching/search?q=${encodeURIComponent(queryText)}&limit=8`);
      const data = await res.json();
      if (seq !== state.watchingSearchSeq) return;
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderWatchingSearchResults(data.items || [], queryText, data.note || "");
    } catch (err) {
      if (seq !== state.watchingSearchSeq) return;
      if (box) {
        box.hidden = false;
        box.innerHTML = `<p class="watching-search-empty">${escapeHtml(String(err.message || err))}</p>`;
      }
    }
  }

  function scheduleWatchingBuildPreview() {
    if (state.watchingBuildPreviewTimer) clearTimeout(state.watchingBuildPreviewTimer);
    state.watchingBuildPreviewTimer = setTimeout(() => {
      refreshWatchingBuildPreview().catch(() => {});
    }, 300);
  }


  function setPoolMeta(text) {
    for (const id of ["quant-watching-meta", "replay-pool-meta"]) {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }
  }

  function setWatchingBuildStatus(text, { error = false } = {}) {
    const el = document.getElementById("watching-build-status");
    if (!el) return;
    const msg = String(text || "").trim();
    el.hidden = !msg;
    el.textContent = msg;
    el.classList.toggle("is-error", !!error);
  }

  function setWatchingRefreshStatus(text, { error = false } = {}) {
    const el = document.getElementById("watching-refresh-status");
    if (!el) return;
    const msg = String(text || "").trim();
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("is-error");
      return;
    }
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("is-error", !!error);
  }


  async function showWatchingChart(code, name) {
    if (!code) return;
    state.watchingFocusCode = String(code).trim();
    state.watchingFocusName = String(
      name || state.watchingNameByCode[state.watchingFocusCode] || ""
    ).trim();
    const section = document.getElementById("watching-chart-section");
    const labelEl = document.getElementById("watching-chart-label");
    if (!section) return;
    section.hidden = false;
    if (typeof window.__investmentEnsureDockSide === "function") {
      try {
        window.__investmentEnsureDockSide();
      } catch (_) {
        /* ignore */
      }
    }
    if (labelEl) labelEl.textContent = watchingChartLabelText(name, code);
    drawWatchingChart([]);

    try {
      const res = await fetch(
        `/api/watching/daily-chart?code=${encodeURIComponent(code)}&lookback=60`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const pts = (data.points || [])
        .map((p) => ({ x: p.date, y: Number(p.close) }))
        .filter((p) => Number.isFinite(p.y));
      if (labelEl) {
        labelEl.textContent = watchingChartLabelText(
          data.stock_name || name,
          code,
          { count: pts.length }
        );
      }
      if (data.stock_name) state.watchingFocusName = String(data.stock_name).trim();
      drawWatchingChart(pts);
      document.querySelectorAll("#watching-watchlist-table .is-chart-active").forEach((rowEl) => {
        rowEl.classList.remove("is-chart-active");
      });
      const activeRow = document.querySelector(
        `#watching-watchlist-table [data-code="${String(code).replace(/"/g, "")}"]`
      );
      if (activeRow) activeRow.classList.add("is-chart-active");
    } catch (err) {
      if (labelEl) {
        labelEl.textContent = watchingChartLabelText(name, code, { error: true });
      }
      drawWatchingChart([]);
    }
  }

  function sortWatchingTableRows() {
    if (!state.watchingGrid || !state.watchingGridReady) return;
    if (!state.watchingSortKey) {
      state.watchingGrid.clearSort();
      return;
    }
    state.watchingGrid.setSort([{ column: state.watchingSortKey, dir: state.watchingSortDir }]);
  }

  function syncWatchingBuildModeUI() {
    const input = document.getElementById("watching-build-shares-input");
    const label = document.getElementById("watching-build-default-label");
    const unit = document.getElementById("watching-build-default-unit");
    document.querySelectorAll(".watching-build-mode-btn").forEach((btn) => {
      const on = btn.dataset.mode === state.watchingBuildMode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (!input || !label || !unit) return;
    const cfg = watchingBuildModeUiConfig(state.watchingBuildMode);
    label.textContent = cfg.label;
    unit.textContent = cfg.unit;
    input.min = cfg.min;
    input.step = cfg.step;
    input.title = cfg.title;
    if (cfg.shouldReset(Number(input.value))) input.value = cfg.defaultValue;
  }

  function syncWatchingSelectAllState() {
    const master = document.getElementById("watching-select-all");
    updateWatchingPickCount();
    if (!master) return;
    if (state.watchingGrid && state.watchingGridReady) {
      const rows = state.watchingGrid.getData() || [];
      if (!rows.length) {
        master.checked = false;
        master.indeterminate = false;
        return;
      }
      const n = rows.filter((r) => r && r.picked).length;
      master.checked = n === rows.length;
      master.indeterminate = n > 0 && n < rows.length;
      return;
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    const boxes = Array.from(watchTable.querySelectorAll(".watching-pick"));
    if (!boxes.length) {
      master.checked = false;
      master.indeterminate = false;
      return;
    }
    const n = boxes.filter((b) => b.checked).length;
    master.checked = n === boxes.length;
    master.indeterminate = n > 0 && n < boxes.length;
  }


  function updateWatchingPickCount() {
    const el = document.getElementById("watching-pick-count");
    const n = getSelectedWatchingCodes().length;
    if (el) {
      el.textContent = n ? `已勾选 ${n} 只` : "未勾选";
      el.classList.toggle("is-active", n > 0);
    }
    const removeBtn = document.getElementById("quant-watching-remove");
    const syncBtn = document.getElementById("quant-watching-sync");
    if (removeBtn) removeBtn.disabled = n === 0;
    if (syncBtn) syncBtn.disabled = n === 0;
  }

  function watchingBuildDefaultValue() {
    const input = document.getElementById("watching-build-shares-input");
    const n = Number((input && input.value) || 0);
    return Number.isFinite(n) ? n : 0;
  }

  function watchingBuildPayload() {
    return buildWatchingBuildPayload(
      state.watchingBuildMode,
      watchingBuildDefaultValue(),
      watchingBuildCodes,
      state.watchingBuildAmountByCode,
      state.watchingBuildSharesByCode
    );
  }

  function watchingCodeKey(code) {
    const c = String(code || "").trim();
    if (!c) return "";
    if (/^\d{1,5}$/.test(c)) return c.padStart(5, "0"); // 港股常见
    if (/^\d{6}$/.test(c)) return c;
    return c;
  }

  function wireWatchingSearch() {
    const input = document.getElementById("watching-search-input");
    const box = document.getElementById("watching-search-results");
    if (!input || input.dataset.wired === "1") return;
    input.dataset.wired = "1";
    input.addEventListener("input", () => {
      const q = input.value.trim();
      if (state.watchingSearchTimer) clearTimeout(state.watchingSearchTimer);
      if (!q) {
        hideWatchingSearchResults();
        return;
      }
      state.watchingSearchTimer = setTimeout(() => runWatchingSearch(q), 120);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        hideWatchingSearchResults();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const first = box && box.querySelector(".watching-search-item");
        if (first) {
          addWatchingWatchItem(first.dataset.code || first.dataset.name || input.value);
        } else if (input.value.trim()) {
          addWatchingWatchItem(input.value.trim());
        }
      }
    });
    if (box) {
      box.addEventListener("click", (e) => {
        const btn = e.target.closest(".watching-search-item");
        if (!btn) return;
        e.preventDefault();
        addWatchingWatchItem(btn.dataset.code || btn.dataset.name);
      });
    }
    document.addEventListener("click", (e) => {
      const wrap = document.getElementById("watching-search-wrap");
      if (!wrap || wrap.contains(e.target)) return;
      hideWatchingSearchResults();
    });
  }

  return {
    addWatchingWatchItem,
    applySentimentAlertRows,
    applySentimentToRow,
    closeWatchingBuildLayer,
    confirmWatchingBuild,
    describeWatchingSource,
    drawWatchingChart,
    fetchPaperHeldMap,
    fetchPaperWatchContext,
    fetchWatchingNewsAI,
    fillWatchingInsights,
    fillWatchingQuotes,
    fillWatchingSentiment,
    formatRefreshStats,
    getSelectedWatchingCodes,
    gotoFollowPage,
    gotoFollowTab,
    hideWatchingChart,
    hideWatchingNewsDetail,
    hideWatchingSearchResults,
    indexByWatchingCode,
    loadFollowCard,
    loadWatchingDataQuality,
    loadWatchingPanel,
    loadWatchingSentimentAlerts,
    matchWatchlistSource,
    openWatchingBuildLayer,
    openWatchingNewsDetail,
    parseWatchingVolume,
    persistWatchingSort,
    refreshWatchingBuildPreview,
    refreshWatchingSortHeaders,
    removeSelectedWatchingItems,
    removeWatchingWatchItem,
    renderWatchingBuildPlan,
    renderWatchingHoldings,
    renderWatchingSearchResults,
    renderWatchingWatchTable,
    runWatchingSearch,
    scheduleWatchingBuildPreview,
    sentimentLabelZh,
    setPoolMeta,
    setWatchingBuildStatus,
    setWatchingRefreshStatus,
    shortOriginLabel,
    showWatchingChart,
    sortWatchingTableRows,
    syncWatchingBuildModeUI,
    syncWatchingSelectAllState,
    truncateText,
    updateWatchingPickCount,
    watchingBuildDefaultValue,
    watchingBuildPayload,
    watchingCodeKey,
    watchingNameFromEl,
    applyWatchingNameEl,
    wireWatchingSearch,
  };
}
