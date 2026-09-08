import { apiFetch } from "../api_client.js";
import { getDataOfflineOnly, installDataOfflineToggle, offlineOnlyQuery } from "../data_offline.js";
import { scoresPolicyLine } from "../data_policy.js?v=p1736";
import { ensureWarehouseTopup } from "../data_warehouse_topup.js";
import { renderLineChart } from "../lw_charts.js";
import { syncOverviewUniverse } from "./factor_corr_ui.js";
import { mountVirtualTable, colStyle } from "../virtual_table.js";
import { fmtScore, fmtTableScore, scoreCls, resolveTradeScore, resolveEodScore } from "../paper/fmt.js?v=p1734";
import { truncateStockName, watchingNameSpanHtml, watchingNameFromEl, applyWatchingNameEl, normalizeProbeCode } from "./names.js";
import { renderWatchingHoldings as renderWatchingHoldingsHtml } from "./watching_holdings.js";
import { buildWatchingDqMetaText, buildWatchingDqFoldSummary, buildWatchingDqTableHtml } from "./watching_dq_ui.js";
import { watchingScoreDetail, sentimentBadgeHtml, renderWatchingBuildPlan as renderWatchingBuildPlanHtml, renderWatchingWatchTableFallback, buildWatchingWatchRows, buildWatchingNewsTitleHtml, buildWatchingNewsMetaText, buildWatchingNewsListHtml, WATCHING_NEWS_AI_LOADING_HTML, buildWatchingNewsAiAnalysisHtml, buildWatchingNewsAiErrorHtml, describeWatchingSource, matchWatchlistSource, truncateText, sentimentLabelZh, shortOriginLabel } from "./watching_render.js?v=p1734";
import {
  buildWatchingScoreDisplay,
  buildWatchingInsightsGridPatch,
  buildWatchingScoreCellHtml,
  buildWatchingCalScoreCellHtml,
  buildWatchingTauScoreCellHtml,
  buildWatchingOnScoreCellHtml,
  buildWatchingNowcastScoreCellHtml,
  buildWatchingInsightsStatusText,
  buildWatchingInsightsErrorStatus,
  buildWatchingInsightsGridErrorPatch,
  buildWatchingInsightsNativeFields,
  isOosFailedItem,
  oosFailedBadgeHtml,
} from "./watching_insights_ui.js?v=p2025";
import {
  parseWatchingVolume,
  formatWatchingChg,
  formatOpenDisplay,
  buildWatchingQuoteGridPatch,
  buildWatchingQuotesStatusText,
  buildWatchingQuotesErrorStatus,
  withQuoteGap,
} from "./watching_quotes_ui.js?v=p1221";
import {
  watchingBuildInvalidTip,
  watchingBuildTitleText,
  buildWatchingBuildPayload,
  watchingBuildModeUiConfig,
} from "./watching_build_ui.js";
import { mountScoreHistogram, summarizeScores } from "./yhat_viz.js";
import { defaultScoringFloors } from "./scoring.js";
import {
  formatRefreshStats,
  buildWatchingNameByCode,
  watchingPoolMetaText,
  applyWatchingOverviewKpis,
  formatYhatLayerMeta,
  watchingQuantListHtml,
  watchingPanelShellFlags,
  applyWatchingPanelShell,
  watchingChartSeriesFromPoints,
  watchingChartLabelText,
} from "./watching_panel_ui.js?v=p1221";

const WATCHING_SORT_STORAGE = "watching_table_sort_v1";
const WATCHING_SORT_KEYS = new Set([
  "name",
  "chg",
  "score",
  "score_eod",
  "score_tau",
  "score_on",
  "score_nowcast",
  "excess",
  "vol",
]);
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

  function syncWatchingDataPolicyMeta() {
    const meta = document.getElementById("watching-page-meta");
    if (meta) {
      meta.textContent = `观察池 · ${scoresPolicyLine(getDataOfflineOnly())} · 写仓=增量补齐`;
    }
  }

  async function prepareWatchingWarehouse({ force = false } = {}) {
    if (getDataOfflineOnly()) return { ok: true, skipped: true };
    try {
      return await ensureWarehouseTopup({
        force,
        watchingLimit: 100,
        onStatus: (msg) =>
          setWatchingRefreshStatus(msg || "增量补齐本地仓…", {
            busy: true,
            owner: "insights",
          }),
      });
    } catch (err) {
      const msg = String((err && err.message) || err || "增量补齐失败");
      setWatchingRefreshStatus(msg, { error: true, owner: "insights" });
      throw err;
    }
  }

  installDataOfflineToggle(document.getElementById("watching-data-offline"), {
    onChange: (offlineOnly) => {
      syncWatchingDataPolicyMeta();
      (async () => {
        try {
          if (!offlineOnly) await prepareWatchingWarehouse({ force: true });
        } catch (_) {
          /* status already set */
        }
        try {
          fillWatchingInsights();
        } catch (_) {
          /* fill may not be ready on first tick */
        }
      })();
    },
  });
  syncWatchingDataPolicyMeta();

  function collectWatchingYhatItems() {
    const insightBy = state.watchingInsightByCode || {};
    const mergeLayer = (code, row) => {
      const it = insightBy[watchingCodeKey(code)] || row;
      return {
        scoreEod: resolveEodScore(it) ?? resolveEodScore(row),
      };
    };
    const out = [];
    if (state.watchingGrid && state.watchingGridReady) {
      for (const r of state.watchingGrid.getData() || []) {
        const n =
          r.scoreNum != null
            ? Number(r.scoreNum)
            : r.predicted_score != null
              ? Number(r.predicted_score)
              : Number(r.score);
        if (!Number.isFinite(n) || !r.code) continue;
        const code = String(r.code);
        out.push({
          code,
          name: r.name || "",
          score: n,
          ...mergeLayer(code, r),
        });
      }
      return out;
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    watchTable?.querySelectorAll("tr[data-code]").forEach((tr) => {
      const n = Number(tr.dataset.score);
      const code = String(tr.dataset.code || "").trim();
      if (!code || !Number.isFinite(n)) return;
      const name =
        tr.querySelector(".watching-name-text")?.getAttribute("data-full-name") ||
        tr.querySelector(".watching-name-text")?.textContent ||
        "";
      const row = {
        scoreEodNum: Number(tr.dataset.scoreEod),
      };
      out.push({
        code,
        name,
        score: n,
        ...mergeLayer(code, row),
      });
    });
    return out;
  }

  function applyYhatHistTableFilter(sel) {
    const clearBtn = document.getElementById("watching-yhat-hist-clear");
    const filterMeta = document.getElementById("watching-yhat-hist-filter");
    if (clearBtn) clearBtn.hidden = !sel;
    if (filterMeta) {
      if (sel) {
        filterMeta.hidden = false;
        filterMeta.textContent = `已筛 ${sel.count} 只 · ŷ ∈ [${sel.x0.toFixed(2)}, ${sel.x1.toFixed(2)}] · 再点同柱或 Esc 清除`;
      } else {
        filterMeta.hidden = true;
        filterMeta.textContent = "";
      }
    }
    const hit = sel ? new Set(sel.codes || []) : null;
    if (state.watchingGrid && typeof state.watchingGrid.patchRows === "function") {
      const patches = {};
      for (const r of state.watchingGrid.getData() || []) {
        const code = String(r.code || "");
        if (!code) continue;
        patches[code] = {
          yhatHistHit: hit ? hit.has(code) : false,
          yhatHistDim: hit ? !hit.has(code) : false,
        };
      }
      state.watchingGrid.patchRows(patches);
      return;
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    watchTable?.querySelectorAll("tr[data-code]").forEach((tr) => {
      const code = String(tr.dataset.code || "");
      tr.classList.toggle("is-yhat-hist-hit", !!(hit && hit.has(code)));
      tr.classList.toggle("is-yhat-hist-dim", !!(hit && !hit.has(code)));
    });
  }

  function ensureWatchingYhatHist() {
    if (state.watchingYhatHist) return state.watchingYhatHist;
    const canvas = document.getElementById("watching-yhat-hist");
    if (!canvas) return null;
    const floors = state.scoringFloors || defaultScoringFloors();
    state.watchingYhatHist = mountScoreHistogram(canvas, {
      bins: 14,
      floors: {
        buy: floors.min_predicted_score,
        hold: floors.min_hold_predicted_score,
      },
      onSelect: (sel) => applyYhatHistTableFilter(sel),
    });
    const clearBtn = document.getElementById("watching-yhat-hist-clear");
    if (clearBtn && !clearBtn.dataset.bound) {
      clearBtn.dataset.bound = "1";
      clearBtn.addEventListener("click", () => {
        state.watchingYhatHist?.clearSelection();
        applyYhatHistTableFilter(null);
      });
    }
    return state.watchingYhatHist;
  }

  function paintWatchingYhatHist(scoresOrItems) {
    const wrap = document.getElementById("watching-yhat-hist-wrap");
    const meta = document.getElementById("watching-yhat-hist-meta");
    if (!wrap) return;
    let items;
    if (Array.isArray(scoresOrItems) && scoresOrItems.length) {
      if (typeof scoresOrItems[0] === "number") {
        // 兼容旧调用：尽量回落成带 code 的 items
        items = collectWatchingYhatItems();
        if (!items.length) {
          items = scoresOrItems
            .filter((n) => Number.isFinite(n))
            .map((score, i) => ({ code: `n${i}`, score }));
        }
      } else {
        items = scoresOrItems;
      }
    } else {
      items = collectWatchingYhatItems();
    }
    if (!items.length) {
      wrap.hidden = true;
      applyYhatHistTableFilter(null);
      applyWatchingOverviewKpis({
        buyPct: null,
        mu: null,
        med: null,
        n: null,
        eodMu: null,
        eodMed: null,
        eodN: null,
      });
      const metaEod = document.getElementById("watching-yhat-hist-meta-eod");
      if (meta) meta.textContent = "";
      if (metaEod) metaEod.textContent = "";
      return;
    }
    wrap.hidden = false;
    const floors = state.scoringFloors || defaultScoringFloors();
    const hist = ensureWatchingYhatHist();
    if (!hist) return;
    hist.setFloors({
      buy: floors.min_predicted_score,
      hold: floors.min_hold_predicted_score,
    });
    requestAnimationFrame(() => {
      const pack = hist.setData(items);
      applyYhatHistTableFilter(hist.getSelection());
      if (pack) {
        const f = (x, d = 2) =>
          x != null && Number.isFinite(x) ? Number(x).toFixed(d) : "—";
        const floorsBuy = floors.min_predicted_score;
        const eodPack = summarizeScores(
          items.map((it) => it.scoreEod),
          { buyFloor: floorsBuy }
        );
        const pos =
          pack.pct_pos != null
            ? `ŷ_trade>0 ${(pack.pct_pos * 100).toFixed(0)}%`
            : null;
        if (meta) {
          meta.textContent = [pos, `n=${pack.n}`].filter(Boolean).join(" · ");
        }
        const metaAux = document.getElementById("watching-yhat-hist-meta-aux");
        if (metaAux) {
          metaAux.textContent = [
            `ŷ_trade μ ${f(pack.mean)}%`,
            `med ${f(pack.median)}%`,
            `IQR [${f(pack.p25)}, ${f(pack.p75)}]`,
            pack.std != null && Number.isFinite(pack.std)
              ? `σ ${f(pack.std)}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ");
        }
        const metaEod = document.getElementById("watching-yhat-hist-meta-eod");
        if (metaEod) {
          metaEod.textContent = formatYhatLayerMeta("ŷ_EOD", eodPack, {
            buy: true,
          });
        }
        applyWatchingOverviewKpis({
          buyPct: eodPack.n ? eodPack.pct_above_buy : null,
          mu: pack.mean,
          med: pack.median,
          n: eodPack.n || pack.n,
          eodMu: eodPack.mean,
          eodMed: eodPack.median,
          eodN: eodPack.n || null,
        });
      }
    });
  }

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

  async function drawWatchingYhatSeries(code) {
    const wrap = document.getElementById("watching-yhat-series-wrap");
    const host = document.getElementById("watching-yhat-series");
    const cap = document.getElementById("watching-yhat-series-caption");
    if (!wrap || !host || !code) {
      if (wrap) wrap.hidden = true;
      return;
    }
    try {
      const res = await fetch(
        `/api/quant/score-ledger/series?code=${encodeURIComponent(code)}&limit=40`
      );
      const data = await res.json().catch(() => ({}));
      const pts = (data.points || [])
        .map((p) => ({
          time: String(p.date || "").slice(0, 10),
          value: Number(p.yhat),
        }))
        .filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.time) && Number.isFinite(p.value));
      if (!data.success || pts.length < 2) {
        wrap.hidden = true;
        return;
      }
      wrap.hidden = false;
      if (cap) {
        cap.textContent = `ŷ 时间线（账本 · ${pts.length} 日）`;
      }
      await renderLineChart(host, pts, {
        emptyText: "账本 ŷ 不足",
        color: "#2563eb",
        zeroLine: true,
        disableZoom: true,
        mainLabel: "ŷ%",
      });
    } catch (_) {
      wrap.hidden = true;
    }
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
    const gen = (state.watchingNewsAiGen = (state.watchingNewsAiGen || 0) + 1);
    aiSection.hidden = false;
    aiContent.innerHTML = WATCHING_NEWS_AI_LOADING_HTML;
    if (aiStatus) aiStatus.textContent = "分析中…";
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 50000);
    try {
      const res = await fetch(
        `/api/watching/sentiment/${encodeURIComponent(stock_code || code)}/analysis`,
        { signal: ctrl.signal }
      );
      const data = await res.json().catch(() => ({}));
      if (gen !== state.watchingNewsAiGen) return;
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
      if (gen !== state.watchingNewsAiGen) return;
      const aborted = err && (err.name === "AbortError" || /abort/i.test(String(err)));
      aiContent.innerHTML = buildWatchingNewsAiErrorHtml(
        aborted
          ? "AI 分析超时，请稍后重试"
          : `AI 分析请求失败: ${String((err && err.message) || err)}`,
        escapeHtml
      );
      if (aiStatus) aiStatus.textContent = aborted ? "分析超时" : "分析失败";
    } finally {
      clearTimeout(timer);
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
    const gen = (state.watchingInsightsGen = (state.watchingInsightsGen || 0) + 1);
    state.watchingInsightByCode = {};
    // 每批单独超时（对齐后端 batch≈90s）；勿用总时钟，否则 100 票 3 批必触发「摘要超时」
    const chunkTimeoutMs = 95000;
    const insightDeps = { fmtScore, scoreCls, parseWatchingVolume, watchingScoreDetail };
    const readInsightsStore = () => {
      try {
        const raw = localStorage.getItem("watching_insights_cache");
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || !parsed.timestamp) return null;
        if (Date.now() - parsed.timestamp >= 4 * 3600 * 1000) return null;
        return parsed;
      } catch (_) {
        return null;
      }
    };
    const writeInsightsStore = (items, extra) => {
      try {
        const prev = readInsightsStore() || {};
        const scoresByCode = { ...(prev.scoresByCode || {}) };
        const itemsByCode = { ...(prev.itemsByCode || {}) };
        for (const it of items || []) {
          const code = String((it && (it.stock_code || it.code)) || "").trim();
          if (!code) continue;
          if (it && it.ok !== false) itemsByCode[code] = it;
          const trade = resolveTradeScore(it);
          if (trade != null && Math.abs(Number(trade)) <= 20) {
            scoresByCode[code] = trade;
          }
        }
        localStorage.setItem(
          "watching_insights_cache",
          JSON.stringify({
            timestamp: Date.now(),
            scoresByCode,
            itemsByCode,
            cacheStamp: (extra && extra.cacheStamp) || prev.cacheStamp || "",
          })
        );
      } catch (_) {}
    };
    setWatchingRefreshStatus("正在加载评分…", { busy: true, owner: "insights" });
    try {
      await prepareWatchingWarehouse({ force: false });
      if (gen !== state.watchingInsightsGen) return;
    } catch (_) {
      if (gen !== state.watchingInsightsGen) return;
      /* 增量失败仍尝试只读现算，便于对照缺仓票 */
    }
    setWatchingRefreshStatus("正在加载评分…", { busy: true, owner: "insights" });

    const applyInsightItems = (items) => {
      if (gen !== state.watchingInsightsGen) return;
      if (useGrid && !(state.watchingGrid && state.watchingGridReady)) return;
      const byCode = indexByWatchingCode(items);
      // 超时/失败空壳 ok:false：不要把已有 ŷ 刷成「—」
      const ready = {};
      Object.keys(byCode).forEach((k) => {
        const it = byCode[k];
        if (it && it.ok !== false && (it.stock_code || it.code)) ready[k] = it;
      });
      state.watchingInsightByCode = {
        ...(state.watchingInsightByCode || {}),
        ...ready,
      };
      if (useGrid && typeof state.watchingGrid.patchRows === "function") {
        const patches = {};
        codes.forEach((code) => {
          const it = ready[watchingCodeKey(code)];
          if (!it) return;
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          patches[code] = buildWatchingInsightsGridPatch(it, row, insightDeps);
        });
        state.watchingGrid.patchRows(patches);
        sortWatchingTableRows();
      } else if (useGrid) {
        codes.forEach((code) => {
          const it = ready[watchingCodeKey(code)];
          if (!it) return;
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          row.update(buildWatchingInsightsGridPatch(it, row, insightDeps));
        });
        sortWatchingTableRows();
      } else {
        codes.forEach((code) => {
          const it = ready[watchingCodeKey(code)];
          if (!it) return;
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
          const scoreCalEl = tr.querySelector(`[data-q='score_eod']`);
          if (scoreCalEl) {
            scoreCalEl.innerHTML = buildWatchingCalScoreCellHtml(
              disp,
              scoreCls,
              escapeHtml
            );
          }
          const scoreTauEl = tr.querySelector(`[data-q='score_tau']`);
          if (scoreTauEl) {
            scoreTauEl.innerHTML = buildWatchingTauScoreCellHtml(
              disp,
              scoreCls,
              escapeHtml
            );
          }
          const scoreOnEl = tr.querySelector(`[data-q='score_on']`);
          if (scoreOnEl) {
            scoreOnEl.innerHTML = buildWatchingOnScoreCellHtml(
              disp,
              scoreCls,
              escapeHtml
            );
          }
          const scoreNowcastEl = tr.querySelector(`[data-q='score_nowcast']`);
          if (scoreNowcastEl) {
            scoreNowcastEl.innerHTML = buildWatchingNowcastScoreCellHtml(
              disp,
              scoreCls,
              escapeHtml
            );
          }
          const eod = resolveEodScore(it);
          if (disp.scoreNum != null) tr.dataset.score = String(disp.scoreNum);
          if (eod != null) tr.dataset.scoreEod = String(eod);
          else delete tr.dataset.scoreEod;
          const fields = buildWatchingInsightsNativeFields(it);
          setTxt("stance", fields.stance);
          setTxt("excess", fields.excess);
          if (fields.vol) setTxt("vol", fields.vol);
          setTxt("volr", fields.volr);
          setTxt("pe", fields.pe);
          setTxt("pb", fields.pb);
          const nameRow = tr.querySelector(".watching-name-row");
          if (nameRow) {
            const failed = isOosFailedItem(it);
            let badge = nameRow.querySelector(".watching-oos-badge");
            if (failed && !badge) {
              nameRow.insertAdjacentHTML("beforeend", oosFailedBadgeHtml(escapeHtml));
            } else if (!failed && badge) {
              badge.remove();
            }
            tr.classList.toggle("is-oos-failed", failed);
          }
        });
      }
      paintWatchingYhatHist();
    };

    const cachedItems = [];
    try {
      const store = readInsightsStore();
      const by = (store && store.itemsByCode) || {};
      for (const code of codes) {
        const it = by[String(code)] || by[watchingCodeKey(code)];
        if (it && it.ok !== false) cachedItems.push(it);
      }
    } catch (_) {}
    if (cachedItems.length) {
      applyInsightItems(cachedItems);
      setWatchingRefreshStatus(
        `摘要缓存 ${cachedItems.length}/${codes.length} · 正在核对…`,
        { busy: true, owner: "insights" }
      );
    }

    const items = [];
    let timedOut = false;
    let cachedCount = 0;
    try {
      // P2：首批 20 只快速回填，后续批 40 只复用连接
      const FIRST_CHUNK = 20;
      const chunkSize = 40;
      const totalChunks = Math.ceil(codes.length / chunkSize) || 1;
      let chunkIdx = 0;
      for (let i = 0; i < codes.length; ) {
        if (gen !== state.watchingInsightsGen) return;
        const curSize = i === 0 ? FIRST_CHUNK : chunkSize;
        const chunk = codes.slice(i, i + curSize);
        chunkIdx += 1;
        setWatchingRefreshStatus(
          `正在加载评分… ${chunkIdx}/${totalChunks}（${items.length}/${codes.length}）`,
          { busy: true, owner: "insights" }
        );
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), chunkTimeoutMs);
        let res;
        try {
          res = await fetch(
            `/api/watching/insights?codes=${encodeURIComponent(chunk.join(","))}&${offlineOnlyQuery()}`,
            { signal: ctrl.signal }
          );
        } catch (err) {
          if (err && err.name === "AbortError") {
            timedOut = true;
            break;
          }
          throw err;
        } finally {
          clearTimeout(timer);
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        const batch = data.items || [];
        items.push(...batch);
        cachedCount += Number(data.cached_count) || 0;
        applyInsightItems(batch);
        writeInsightsStore(batch, { cacheStamp: data.cache_stamp });
        i += curSize;
      }
      if (gen !== state.watchingInsightsGen) return;
      const okN = items.filter((x) => x.ok).length;
      if (timedOut && items.length) {
        setWatchingRefreshStatus(
          `摘要部分超时 · 已更新 ${okN}/${codes.length}（页面仍可用）`,
          { error: true, owner: "insights" }
        );
      } else if (timedOut) {
        setWatchingRefreshStatus(buildWatchingInsightsErrorStatus({ name: "AbortError" }), {
          error: true,
          owner: "insights",
        });
      } else {
        setWatchingRefreshStatus(buildWatchingInsightsStatusText(okN, codes.length, items, {
          cachedCount,
        }), {
          ok: true,
          owner: "insights",
        });
      }
      writeInsightsStore(items);
      paintWatchingYhatHist();
    } catch (err) {
      if (gen !== state.watchingInsightsGen) return;
      if (items.length) {
        applyInsightItems(items);
        const okN = items.filter((x) => x.ok).length;
        setWatchingRefreshStatus(
          `摘要部分失败 · 已更新 ${okN}/${codes.length}：${String((err && err.message) || err)}`,
          { error: true, owner: "insights" }
        );
        return;
      }
      if (useGrid && state.watchingGrid && state.watchingGridReady) {
        const patches = {};
        codes.forEach((code) => {
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          patches[code] = buildWatchingInsightsGridErrorPatch(row.getData());
        });
        if (typeof state.watchingGrid.patchRows === "function") {
          state.watchingGrid.patchRows(patches);
        } else {
          Object.keys(patches).forEach((code) => {
            const row = state.watchingGrid.getRow(code);
            if (row) row.update(patches[code]);
          });
        }
      }
      setWatchingRefreshStatus(buildWatchingInsightsErrorStatus(err), {
        error: true,
        owner: "insights",
      });
    }
  }

  async function fillWatchingQuotes() {
    if (state.watchingGrid && state.watchingGridReady) {
      const codes = (state.watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
      if (!codes.length) return;
      // P2：分批加载——首批 20 只快速回填，剩余后台补齐
      const FIRST_BATCH = 20;
      const firstCodes = codes.slice(0, FIRST_BATCH);
      const restCodes = codes.slice(FIRST_BATCH);
      let totalOk = 0;
      const insightDeps = { fmtScore, scoreCls, parseWatchingVolume, watchingScoreDetail };
      const applyQuoteBatch = (items) => {
        const byCode = indexByWatchingCode(items || []);
        codes.forEach((code) => {
          const it = byCode[watchingCodeKey(code)] || {};
          if (!it.stock_code) return;
          const row = state.watchingGrid.getRow(code);
          if (!row) return;
          const insight = (state.watchingInsightByCode || {})[watchingCodeKey(code)];
          const quotePatch = buildWatchingQuoteGridPatch(it, row, parseWatchingVolume);
          if (insight && insight.stock_code) {
            const merged = withQuoteGap(insight, it);
            const scorePatch = buildWatchingInsightsGridPatch(
              merged,
              { getData: () => ({ ...(row.getData() || {}), ...quotePatch }) },
              insightDeps
            );
            row.update({ ...quotePatch, ...scorePatch });
          } else {
            row.update(quotePatch);
          }
        });
        return (items || []).filter((x) => x.ok).length;
      };
      setWatchingRefreshStatus("正在拉取行情…", { busy: true, owner: "quotes" });
      const fetchQuoteBatch = async (codeList, timeoutMs) => {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), timeoutMs);
        try {
          const res = await fetch(
            `/api/watching/quotes?codes=${encodeURIComponent(codeList.join(","))}`,
            { signal: ctrl.signal }
          );
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || res.statusText);
          return data;
        } finally {
          clearTimeout(timer);
        }
      };
      try {
        let data1;
        try {
          data1 = await fetchQuoteBatch(firstCodes, 15000);
        } catch (_err) {
          data1 = await fetchQuoteBatch(firstCodes, 12000);
        }
        totalOk += applyQuoteBatch(data1.items || []);
        sortWatchingTableRows();
        if (restCodes.length) {
          setWatchingRefreshStatus(
            `行情 ${totalOk}/${codes.length} · 补齐剩余 ${restCodes.length} 只…`,
            { busy: true, owner: "quotes" }
          );
        }
      } catch (err) {
        setWatchingRefreshStatus(buildWatchingQuotesErrorStatus(err), {
          error: true,
          owner: "quotes",
        });
        return;
      }
      // 剩余：后台补齐（失败仅告警，不覆盖首批已回填行）
      if (restCodes.length) {
        try {
          const data2 = await fetchQuoteBatch(restCodes, 20000);
          totalOk += applyQuoteBatch(data2.items || []);
          sortWatchingTableRows();
        } catch (_) {
          /* 首批已回填，剩余失败仅降级 */
        }
      }
      if (totalOk === 0) {
        setWatchingRefreshStatus("行情暂不可用，请稍后刷新", {
          error: true,
          owner: "quotes",
        });
      } else {
        setWatchingRefreshStatus(buildWatchingQuotesStatusText(totalOk, codes.length), {
          ok: true,
          owner: "quotes",
        });
      }
      return;
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    const trs = watchTable.querySelectorAll("tr[data-code]");
    if (!trs.length) return;
    const codes = Array.from(trs).map((r) => r.dataset.code).filter(Boolean);
    setWatchingRefreshStatus("正在拉取行情…", { busy: true, owner: "quotes" });
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
        setTxt(
          "open",
          it.ok
            ? formatOpenDisplay(it, { unit: it.unit, currency: it.currency })
            : null
        );
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
      setWatchingRefreshStatus(buildWatchingQuotesStatusText(okN, codes.length), {
        ok: true,
        owner: "quotes",
      });
    } catch (err) {
      setWatchingRefreshStatus(buildWatchingQuotesErrorStatus(err), {
        error: true,
        owner: "quotes",
      });
    }
  }

  async function fillWatchingSentiment() {
    if (!state.watchingGrid || !state.watchingGridReady) return;
    const codes = (state.watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
    if (!codes.length) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 95000);
    try {
      const res = await fetch(
        `/api/watching/sentiment?codes=${encodeURIComponent(codes.join(","))}&limit=3`,
        { signal: ctrl.signal }
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
    } finally {
      clearTimeout(timer);
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
      const foldDesc = document.querySelector(
        "#watching-data-quality-fold > summary .dashboard-section-desc"
      );
      if (foldDesc) {
        const fallback = dq && dq.fallback_count != null ? Number(dq.fallback_count) : null;
        foldDesc.textContent = warnings.length
          ? `告警 ${warnings.length} 条`
          : fallback && fallback > 0
            ? `fallback ${fallback}`
            : "良好 · fallback · gated";
      } else {
        const foldSummary = document.querySelector(
          "#watching-data-quality-fold > summary"
        );
        if (foldSummary) {
          const fallback = dq && dq.fallback_count != null ? Number(dq.fallback_count) : null;
          const tail =
            warnings.length ? `告警 ${warnings.length} 条` : fallback && fallback > 0 ? `fallback ${fallback}` : "良好";
          foldSummary.textContent = `数据质量 · ${tail}`;
        }
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
      const foldDesc = document.querySelector(
        "#watching-data-quality-fold > summary .dashboard-section-desc"
      );
      if (foldDesc) foldDesc.textContent = "加载失败";
      else {
        const foldSummary = document.querySelector(
          "#watching-data-quality-fold > summary"
        );
        if (foldSummary) foldSummary.textContent = "数据质量 · 加载失败";
      }
    }
  }

  async function loadWatchingPanel() {
    const shellEls = {
      emptyEl: document.getElementById("watching-empty"),
      gridEl: document.getElementById("watching-align-grid"),
      mainActions: document.getElementById("watching-main-actions"),
      searchWrap: document.getElementById("watching-search-wrap"),
      sectionEls: [
        document.getElementById("watching-section-intake"),
        document.getElementById("watching-section-yhat"),
        document.getElementById("watching-section-watchlist"),
        document.getElementById("watching-section-build-log"),
        document.getElementById("watching-section-dq"),
      ],
    };
    setWatchingRefreshStatus("正在加载观察名单…", { busy: true, owner: "panel" });
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
      setWatchingRefreshStatus(msg, { error: true, owner: "panel" });
      throw err;
    } finally {
      clearTimeout(timer);
    }
    const paperCodes = paperCtx.held || new Map();
    const data = uniRes;
    applyWatchingPanelShell(watchingPanelShellFlags(!!data.exists), shellEls);
    if (!data.exists) {
      setPoolMeta("尚未创建");
      applyWatchingOverviewKpis({
        pool: null,
        held: null,
        maxSize: null,
        buyPct: null,
        mu: null,
        med: null,
        n: null,
        eodMu: null,
        eodMed: null,
        eodN: null,
      });
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
    // 合并而非整表替换：避免空/伪名覆盖探针已 hydrate 的真名
    state.watchingNameByCode = {
      ...(state.watchingNameByCode || {}),
      ...buildWatchingNameByCode(wl, names),
    };
    const paperN = wl.filter((c) => paperCodes.has(String(c))).length;
    setPoolMeta(watchingPoolMetaText(wl.length, paperN, uni.max_size));
    applyWatchingOverviewKpis({
      pool: wl.length,
      held: paperN,
      maxSize: uni.max_size,
    });
    try {
      syncOverviewUniverse(
        wl.length,
        paperN ? `纸面重叠 ${paperN}` : "观察池"
      );
    } catch (_) {
      /* overview optional */
    }
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
      // quotes 会接管忙碌条；先交还 panel owner
      setWatchingRefreshStatus("正在拉取行情…", { busy: true, owner: "quotes" });
      // 行情优先；舆情徽章稍后补，避免与行情抢线程池导致前端 20s 超时
      fillWatchingQuotes()
        .catch(() => {})
        .finally(() => {
          fillWatchingSentiment().catch(() => {});
        });
      fillWatchingInsights().catch(() => {});
    } else {
      setWatchingRefreshStatus("", { owner: "panel" });
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
    const aiSection = document.getElementById("watching-news-ai-section");
    const aiContent = document.getElementById("watching-news-ai-content");
    const aiStatus = document.getElementById("watching-news-ai-status");
    if (!panel || !listEl) return;
    const gen = (state.watchingNewsDetailGen = (state.watchingNewsDetailGen || 0) + 1);
    // 打断上一轮 AI，避免切票后仍显示「正在分析」
    state.watchingNewsAiGen = (state.watchingNewsAiGen || 0) + 1;
    panel.hidden = false;
    if (titleEl) titleEl.textContent = `资讯 · ${code}`;
    if (metaEl) metaEl.textContent = "加载中…";
    listEl.innerHTML = "";
    if (aiSection) aiSection.hidden = true;
    if (aiContent) aiContent.innerHTML = "";
    if (aiStatus) aiStatus.textContent = "";
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 20000);
    try {
      const res = await fetch(
        `/api/watching/sentiment/${encodeURIComponent(code)}?limit=8`,
        { signal: ctrl.signal }
      );
      const data = await res.json().catch(() => ({}));
      if (gen !== state.watchingNewsDetailGen) return;
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const name = data.stock_name || code;
      const sent = data.sentiment || {};
      const sc = data.stock_code || code;
      if (titleEl) {
        titleEl.innerHTML = buildWatchingNewsTitleHtml(name, sc, sent, sentimentBadgeHtml);
      }
      let meta = buildWatchingNewsMetaText(data, sent);
      if (data.stale || (data.error && data.from_cache)) {
        meta = `${meta} · 缓存`;
      }
      if (metaEl) metaEl.textContent = meta;
      listEl.innerHTML = buildWatchingNewsListHtml(data.items || [], data);
      // 详情用 limit=8 可能与表格批量 limit=3 标签不一致；勿回写行，避免点「中」变成「多」
      watchingSentimentByCode[String(code)] = data;
      if (data.ok && (data.items || []).length) {
        fetchWatchingNewsAI(code, sc);
      } else if (aiSection && aiContent) {
        aiSection.hidden = false;
        aiContent.innerHTML = buildWatchingNewsAiErrorHtml(
          data.error || "暂无资讯，跳过 AI 分析",
          escapeHtml
        );
        if (aiStatus) aiStatus.textContent = "无资讯";
      }
    } catch (err) {
      if (gen !== state.watchingNewsDetailGen) return;
      const aborted = err && (err.name === "AbortError" || /abort/i.test(String(err)));
      if (metaEl) {
        metaEl.textContent = aborted
          ? "资讯拉取超时，请稍后重试"
          : String((err && err.message) || err);
      }
      listEl.innerHTML = `<li class="watching-news-empty">${
        aborted ? "加载超时" : "加载失败"
      }</li>`;
    } finally {
      clearTimeout(timer);
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
    state.watchingInsightsGen = (state.watchingInsightsGen || 0) + 1;
    state.watchingInsightByCode = {};
    if (!wl.length) {
      watchTable.innerHTML = `<p class="watching-table-empty">暂无观察 · 上方搜索加入</p>`;
      hideWatchingNewsDetail();
      updateWatchingPickCount();
      paintWatchingYhatHist([]);
      return;
    }
    const inPaper =
      paperCodes instanceof Map
        ? paperCodes
        : new Map(Array.from(paperCodes || []).map((c) => [String(c), null]));
    // 读取 localStorage 缓存的 insights（4h 内有效），用于初始化评分列
    let cachedScores = {};
    try {
      const raw = localStorage.getItem("watching_insights_cache");
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.timestamp && Date.now() - parsed.timestamp < 4 * 3600 * 1000) {
          cachedScores = parsed.scoresByCode || {};
        }
      }
    } catch (_) {}
    const rowByCode = new Map();
    for (let i = 0; i < wl.length; i++) {
      const code = String(wl[i] || "").trim();
      if (!code) continue;
      const name = (names[i] && String(names[i]).trim()) || "—";
      const scoreRaw = scores && scores[code];
      const cachedRaw = cachedScores[code];
      const cachedN =
        cachedRaw != null && !Number.isNaN(Number(cachedRaw))
          ? Number(cachedRaw)
          : null;
      // 缓存若是 heuristic 0–100，丢弃（避免与 ŷ% 混列）
      const cached =
        cachedN != null && Math.abs(cachedN) <= 20 ? cachedN : null;
      const scoreNum =
        scoreRaw != null && !Number.isNaN(Number(scoreRaw)) && Math.abs(Number(scoreRaw)) <= 20
          ? Number(scoreRaw)
          : cached;
      const onPaper = inPaper.has(code);
      const heldShares = onPaper ? inPaper.get(code) : null;
      rowByCode.set(code, {
        code,
        name,
        picked: false,
        market: "",
        paper: onPaper ? (heldShares != null ? `${heldShares} 股` : "已持") : "建仓",
        onPaper,
        inBook: false,
        sentHtml: `<span class="watching-sent-badge is-neutral" data-code="${escapeHtml(code)}" title="加载中">…</span>`,
        price: "—",
        prev_close: "—",
        open: "—",
        openNum: null,
        chg: "—",
        chgCls: "",
        score: scoreNum == null ? "…" : fmtTableScore(null, scoreNum),
        scoreNum,
        scoreCls: scoreCls(scoreNum),
        scoreEod: "…",
        scoreTau: "…",
        scoreOn: "…",
        scoreNowcast: "…",
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
      paintWatchingYhatHist(
        rows
          .filter((r) => Number.isFinite(r.scoreNum) && r.code)
          .map((r) => ({ code: r.code, name: r.name || "", score: r.scoreNum }))
      );
    } catch (err) {
      console.warn("[watching] 虚拟表挂载失败，回退原生表", err);
      state.watchingGrid = null;
      state.watchingGridReady = false;
      renderWatchingWatchTableFallback(rows);
      paintWatchingYhatHist(
        rows
          .filter((r) => Number.isFinite(r.scoreNum) && r.code)
          .map((r) => ({ code: r.code, name: r.name || "", score: r.scoreNum }))
      );
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
    const msg = String(text || "");
    const busy = /正在|加载中|拉取|刷新中|分析中/.test(msg);
    for (const id of ["quant-watching-meta", "replay-pool-meta"]) {
      const el = document.getElementById(id);
      if (!el) continue;
      let shown = msg;
      if (id === "replay-pool-meta") {
        const mPool = msg.match(/观察\s+(\d+)\s*只/);
        if (mPool) shown = `观察池 ${mPool[1]} 只`;
        else if (/尚未创建|未创建/.test(msg)) shown = "观察池：—";
      }
      el.textContent = shown;
      el.classList.toggle("is-busy", busy);
    }
    // 与卡头 meta 同步概览 KPI（嵌套模块若被缓存漏掉显式调用时仍能写上）
    const m = msg.match(
      /观察\s+(\d+)\s*只\s*·\s*已持\s+(\d+)\/\d+\s*·\s*上限\s+(\d+|—)/
    );
    if (m) {
      applyWatchingOverviewKpis({
        pool: Number(m[1]),
        held: Number(m[2]),
        maxSize: m[3] === "—" ? null : Number(m[3]),
      });
    } else if (/尚未创建|未创建/.test(msg)) {
      applyWatchingOverviewKpis({
        pool: null,
        held: null,
        maxSize: null,
        buyPct: null,
        mu: null,
        med: null,
        n: null,
        eodMu: null,
        eodMed: null,
        eodN: null,
      });
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

  let watchingRefreshStatusTimer = null;
  let watchingRefreshBusyOwner = null;
  let watchingRefreshBusySince = 0;
  const WATCHING_BUSY_MIN_MS = 600;

  function setWatchingRefreshStatus(
    text,
    { error = false, busy = null, ok = false, owner = null } = {}
  ) {
    const el = document.getElementById("watching-refresh-status");
    if (!el) return;
    if (watchingRefreshStatusTimer) {
      clearTimeout(watchingRefreshStatusTimer);
      watchingRefreshStatusTimer = null;
    }
    const msg = String(text || "").trim();
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("is-error", "is-busy", "is-ok");
      if (!owner || watchingRefreshBusyOwner === owner) {
        watchingRefreshBusyOwner = null;
        watchingRefreshBusySince = 0;
      }
      return;
    }
    const isBusy =
      busy == null
        ? /正在|加载中|拉取|刷新中|分析中|移除中|加入中/.test(msg)
        : !!busy;
    // 行情拉取进行中时，其它并行任务的「已更新」勿冲掉忙碌态
    if (
      !isBusy &&
      !error &&
      watchingRefreshBusyOwner &&
      owner &&
      watchingRefreshBusyOwner !== owner
    ) {
      return;
    }

    const apply = () => {
      if (isBusy) {
        watchingRefreshBusyOwner = owner || "default";
        if (!watchingRefreshBusySince) watchingRefreshBusySince = Date.now();
      } else if (!owner || watchingRefreshBusyOwner === owner) {
        watchingRefreshBusyOwner = null;
        watchingRefreshBusySince = 0;
      }

      el.hidden = false;
      el.textContent = msg;
      el.classList.toggle("is-error", !!error);
      el.classList.toggle("is-busy", !!isBusy && !error);
      el.classList.toggle("is-ok", !!ok && !error && !isBusy);
      const bar = document.getElementById("watching-main-actions");
      if (bar && bar.hidden && msg) bar.hidden = false;
      if (msg && (ok || (!error && !isBusy))) {
        watchingRefreshStatusTimer = setTimeout(() => {
          if (el.classList.contains("is-busy") || el.classList.contains("is-error")) {
            return;
          }
          el.hidden = true;
          el.textContent = "";
          el.classList.remove("is-ok");
          watchingRefreshStatusTimer = null;
        }, 6000);
      }
    };

    // 忙碌→完成过快时强制至少亮一会
    if (
      !isBusy &&
      !error &&
      watchingRefreshBusySince &&
      (!owner || watchingRefreshBusyOwner === owner)
    ) {
      const left = WATCHING_BUSY_MIN_MS - (Date.now() - watchingRefreshBusySince);
      if (left > 0) {
        watchingRefreshStatusTimer = setTimeout(() => {
          watchingRefreshStatusTimer = null;
          apply();
        }, left);
        return;
      }
    }
    apply();
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
    drawWatchingYhatSeries(code).catch(() => {});

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
