import {
  escapeHtml,
  renderReadmeLinksHtml,
  attachReadmeLinkHandler,
  postQuantCiEval,
  downloadBlob,
  downloadJson,
  setUiBusy,
} from "./shared.js";
import { apiFetch } from "./api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart } from "./lw_charts.js";
import { mountVirtualTable, colStyle } from "./virtual_table.js";
import { createScoreTooltipController } from "./score_tooltip.js?v=p1128";
import { fmtScore, scoreCls } from "./paper/fmt.js?v=p1128";
import {
  defaultScoringFloors,
  mergeScoringFloors,
  portfolioBtScoreFloorPayload as buildBtScoreFloorPayload,
} from "./quant/scoring.js";
import {
  truncateStockName,
  watchingNameSpanHtml,
  watchingNameFromEl,
  applyWatchingNameEl,
  normalizeProbeCode,
} from "./quant/names.js";
import { createResearchParams } from "./quant/params.js";
import {
  readPromoteTtlHours,
  promoteHintsTtlMs,
  persistPromoteTtlHours,
  readPromoteExpireHard,
  persistPromoteExpireHard,
  readPromoteHardGate,
  persistPromoteHardGate,
  loadCachedPromoteHints,
  buildResearchPromoteMeta,
  PROMOTE_HARD_GATE_KEY,
  PROMOTE_HINTS_TTL_HOURS_KEY,
  PROMOTE_EXPIRE_HARD_KEY,
} from "./quant/promote_cache.js";
import { createFactorMetaCache } from "./quant/factor_meta.js";
import { buildUniversePanelHtml } from "./quant/universe_ui.js";
import { researchGridHtml, metricCell } from "./quant/research_grid.js";
import { createPromoteHintsRenderer } from "./quant/promote_hints_ui.js";
import { createFactorIcUi } from "./quant/factor_ic_ui.js";
import { createBtTablesUi } from "./quant/bt_tables.js";
import { installClusterProbe } from "./quant/domain_cluster.js";
import { installSuggest } from "./quant/domain_suggest.js";
import { installStrategy } from "./quant/domain_strategy.js";
import { installExportInterpret } from "./quant/domain_export.js";
import { installScoreReview } from "./quant/domain_score_review.js?v=p1093";
import { installFitGapHub } from "./quant/domain_fit_gap.js";
import { loadAndRenderFactorCorr, loadAndRenderFactorIR, loadAndRenderFactorICSeries, setProStatusChip, syncOverviewFromClusters, syncOverviewTau, renderFactorSummaryCards } from "./quant/factor_corr_ui.js";

const _QV =
  (typeof window !== "undefined" && window.__ASSET_V__) || "dev";
const {
  createBtResultRenderers,
  fmtPct,
  metricClass,
  buildResearchCurves,
  buildPortfolioBacktestSummaryText,
  buildPortfolioBacktestFailText,
  applyReplayOverviewKpis,
} = await import(`./quant/bt_result.js?v=${encodeURIComponent(_QV)}`);
const { createOlsUi } = await import(
  `./quant/ols_ui.js?v=${encodeURIComponent(_QV)}`
);
const { installWatching } = await import(
  `./quant/domain_watching.js?v=${encodeURIComponent(_QV)}`
);
const { installBacktest } = await import(
  `./quant/domain_backtest.js?v=${encodeURIComponent(_QV)}`
);

/** Quant research panel — shell + domain installs. */
export function initQuant(ctx) {
  const researchParams = createResearchParams();
  const {
    clampHorizonDays,
    syncHorizonInputs,
    readHorizonDays,
    readRidgeLambda,
    readClusterK,
    readWatchingLimit,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  } = researchParams;
  const factorMeta = createFactorMetaCache();
  const {
    factorMetaByName,
    factorMetaByLabel,
    rememberFactorMeta,
    ensureFactorMeta,
    factorDescription,
    factorNameCellHtml,
    factorTaxonomyCellHtml,
  } = factorMeta;

  const BT_SCOPE_LIVE =
    "口径：日线 PIT + 可选财务；不含舆情加减分；无 live 质量门禁（thin/fallback 仍可能进分）。" +
    "历史 Top-K 仅用 ŷ_EOD 排序，关 ŷ_τ 买入闸（日线无可靠分钟 τ；与 live ŷ_trade 不同）。" +
    "成本按换手计费。有效≠正确：先看上方 IC/分层/超额，再解读 Top-K 累计收益。";
  const BT_SCOPE_FROZEN =
    "以下为 quant_daily 冻结摘要，不是刚才点的 Top-K；点「Top-K 回测」或「中性化对照」刷新当次结果。";
  const quantBtBusyIds = [
    "quant-portfolio-run",
    "quant-portfolio-compare",
    "quant-return-model-fit",
  ];
  const PRESET_FLAG_LABELS = {
    paper_run: "纸面持仓日更",
    paper_holding_cycle: "纸面持仓日更",
    paper_buy: "纸面模拟买入",
    eval_mock: "golden mock",
    eval_agent: "Agent 回归",
    quant_report: "量化日报",
    watching_refresh: "刷新 watching",
    cross_section: "横截面",
    sync_paper_watchlist: "模拟建仓同步",
    paper_rebalance: "纸面截面调仓",
    paper_cross_section_rebalance: "纸面截面调仓",
    export_quant_report: "导出 MD/HTML",
    portfolio_neutral_compare: "中性化对照",
  };
  const QUANT_EXPORT_PRESETS = new Set(["quant", "quant_paper", "full"]);

  function on(id, type, handler) {
    const el = typeof id === "string" ? document.getElementById(id) : id;
    if (!el) return null;
    el.addEventListener(type, handler);
    return el;
  }
  function setQuantMeta(text, { busy = false, error = false } = {}) {
    setUiBusy(els.quantMeta, text, { busy, error });
  }
  function setBusyText(el, text, { busy = true, html = false } = {}) {
    setUiBusy(el, text, { busy, html });
  }

  const els = {
    quantDialog: document.getElementById("quant-dialog"),
    quantMeta: document.getElementById("quant-meta"),
    quantWatchingMeta: document.getElementById("quant-watching-meta"),
    quantWatchingList: document.getElementById("quant-watching-list"),
    strategyList: document.getElementById("strategy-list"),
    strategyListLoading: document.getElementById("strategy-list-loading"),
    quantDiffSummary: document.getElementById("quant-diff-summary"),
    quantDiffTable: document.getElementById("quant-diff-table"),
    quantCrossSummary: document.getElementById("quant-cross-summary"),
    quantCrossList: document.getElementById("quant-cross-list"),
    quantFactorList: document.getElementById("quant-factor-list"),
    quantWeightSuggest: document.getElementById("quant-weight-suggest"),
    quantWeightTable: document.getElementById("quant-weight-table"),
    quantOlsSummary: document.getElementById("quant-ols-summary"),
    quantOlsClusters: document.getElementById("quant-ols-clusters"),
    quantProbeSummary: document.getElementById("quant-probe-summary"),
    quantProbeResult: document.getElementById("quant-probe-result"),
    quantOlsTable: document.getElementById("quant-ols-table"),
    quantPortfolioSummary: document.getElementById("quant-portfolio-summary"),
    quantBtProgress: document.getElementById("quant-bt-progress"),
    quantBtProgressText: document.getElementById("quant-bt-progress-text"),
    quantBtMetrics: document.getElementById("quant-bt-metrics"),
    quantBtTrades: document.getElementById("quant-bt-trades"),
    quantNeutralCompareTable: document.getElementById("quant-neutral-compare-table"),
    quantPortfolioChart: document.getElementById("quant-portfolio-chart"),
    quantT0Summary: document.getElementById("paper-t0-summary"),
    quantT0Metrics: document.getElementById("paper-t0-metrics"),
    quantT0Days: document.getElementById("paper-t0-days"),
    quantThresholdSummary: document.getElementById("quant-threshold-summary"),
    quantThresholdTable: document.getElementById("quant-threshold-table"),
    quantInterpretBody: document.getElementById("quant-interpret-body"),
    quantInterpretNeutral: document.getElementById("quant-interpret-neutral"),
    quantExportPreviewMeta: document.getElementById("quant-export-preview-meta"),
    quantExportPreviewToc: document.getElementById("quant-export-preview-toc"),
    quantExportPreviewBody: document.getElementById("quant-export-preview-body"),
    quantDailyArchive: document.getElementById("quant-daily-archive"),
    quantDailyArchiveMeta: document.getElementById("quant-daily-archive-meta"),
    quantDailyArchiveChips: document.getElementById("quant-daily-archive-chips"),
    quantOpsSummary: document.getElementById("quant-ops-summary"),
    quantOpsPackage: document.getElementById("quant-ops-package"),
    quantOpsPreset: document.getElementById("quant-ops-preset"),
    quantOpsPresetFlags: document.getElementById("quant-ops-preset-flags"),
    readmeDialog: document.getElementById("readme-dialog"),
    readmeTitle: document.getElementById("readme-title"),
    readmeMeta: document.getElementById("readme-meta"),
    readmeBody: document.getElementById("readme-body"),
    readmeDocLinks: document.getElementById("readme-doc-links"),
  };

  const state = {
    lastBacktestPack: null,
    lastParamGrid: null,
    neutralCompareSource: null,
    quantScoringFloors: defaultScoringFloors(),
    prefsHorizonDays: getPrefsHorizonDays(),
    btTradesTableApi: null,
    watchingNameByCode: {},
    probeSelectValueToCode: {},
    probePickerRows: [],
    watchingFocusCode: null,
    watchingFocusName: "",
    quantLastWeightDiff: null,
    quantLastOlsClusters: null,
    quantLastThresholdDiff: null,
    quantLastThresholdSuggest: null,
    strategyLastIcExport: null,
    strategyLastWeightDiff: null,
    dailyPresetsCache: [],
    watchingSearchTimer: null,
    watchingSearchSeq: 0,
    watchingSortKey: null,
    watchingSortDir: "desc",
    watchingGrid: null,
    watchingGridReady: false,
    watchingBuildMode: "amount",
    watchingBuildSharesByCode: {},
    watchingBuildAmountByCode: {},
    watchingBuildPreviewTimer: null,
    lastSimTrades: null,
    lastFactorPanelForMerge: null,
    lastWeightSuggestForMerge: null,
    lastOlsForMerge: null,
    dailyPreviewRequested: false,
    quantOpsPackageLoaded: false,
  };

  const watchingScoreTips = createScoreTooltipController();
  const btSimScoreTips = createScoreTooltipController();

  const q = {
    ctx, on, els, state, escapeHtml, apiFetch, downloadBlob, downloadJson,
    fmtPct, metricClass, researchGridHtml, metricCell,
    clampHorizonDays, syncHorizonInputs, readHorizonDays, readRidgeLambda, readClusterK,
    readWatchingLimit, setPrefsHorizonDays, getPrefsHorizonDays,
    factorMetaByName, factorMetaByLabel, rememberFactorMeta, ensureFactorMeta,
    factorDescription, factorNameCellHtml, factorTaxonomyCellHtml,
    BT_SCOPE_LIVE, BT_SCOPE_FROZEN, quantBtBusyIds, PRESET_FLAG_LABELS, QUANT_EXPORT_PRESETS,
    setQuantMeta, setBusyText, watchingScoreTips, btSimScoreTips,
    buildUniversePanelHtml, buildResearchCurves, buildResearchPromoteMeta,
    readPromoteHardGate, readPromoteTtlHours, persistPromoteTtlHours,
    persistPromoteExpireHard, persistPromoteHardGate, loadCachedPromoteHints,
    truncateStockName, watchingNameSpanHtml, watchingNameFromEl, applyWatchingNameEl, normalizeProbeCode,
    fmtScore, scoreCls, mountVirtualTable, colStyle,
    renderLineChart, renderDualLineChart, renderMultiLineChart,
    buildPortfolioBacktestSummaryText, buildPortfolioBacktestFailText,
    buildBtScoreFloorPayload, defaultScoringFloors, mergeScoringFloors,
    attachReadmeLinkHandler, renderReadmeLinksHtml,
  };

  Object.assign(q, createBtResultRenderers({ escapeHtml, fmtPct, metricClass, researchGridHtml }));
  q.renderPromoteHintsPanel = createPromoteHintsRenderer({ escapeHtml, researchGridHtml, promoteHintsTtlMs });
  Object.assign(q, createFactorIcUi({ escapeHtml, researchGridHtml, metricCell, metricClass, factorMetaByName, factorMetaByLabel, factorNameCellHtml, factorTaxonomyCellHtml }));
  Object.assign(q, createOlsUi({
    escapeHtml, researchGridHtml, metricCell, metricClass, factorNameCellHtml, factorTaxonomyCellHtml, factorMetaByName,
    factorIcWeightMergedHtml: q.factorIcWeightMergedHtml,
    getWatchingNameByCode: () => state.watchingNameByCode,
    normalizeProbeCode,
  }));
  // domain_cluster 沿用旧名 Html 后缀
  q.renderProbeStockVsGroupTableHtml = q.renderProbeStockVsGroupTable;
  Object.assign(q, createBtTablesUi({
    escapeHtml, researchGridHtml, metricCell, fmtPct, metricClass,
    getWatchingNameByCode: () => state.watchingNameByCode,
  }));

  const watching = installWatching(q);
  const backtest = installBacktest(q);
  const cluster = installClusterProbe(q);
  const suggest = installSuggest(q);
  const strategy = installStrategy(q);
  const exportDomain = installExportInterpret(q);
  const scoreReviewDomain = installScoreReview(q);
  const fitGapHub = installFitGapHub(q);
  q.watching = watching;
  q.backtest = backtest;
  q.cluster = cluster;
  q.suggest = suggest;
  q.strategy = strategy;
  q.exportDomain = exportDomain;
  q.scoreReviewDomain = scoreReviewDomain;
  q.fitGapHub = fitGapHub;

  async function renderRemCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-rem-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore · 仍用内置中文兜底 */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, opts)
        : "";
    host.innerHTML = html || "";
  }

  function fmtRemTs(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) {
      return String(iso).replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 16);
    }
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function fmtRemIc(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(3) : "—";
  }

  function fmtRemHit(v) {
    const n = Number(v);
    return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
  }

  function fmtRemN(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString("en-US") : "—";
  }

  function remStatusMeta(label, value, tip) {
    return (
      `<span class="quant-rem-meta"${tip ? ` title="${escapeHtml(tip)}"` : ""}>` +
      `<span class="quant-rem-meta-k">${escapeHtml(label)}</span>` +
      `<span class="quant-rem-meta-v">${escapeHtml(String(value))}</span>` +
      `</span>`
    );
  }

  /**
   * ŷ_τ 卡头状态：chip + 可选文案 + OOS/n/时间 meta。
   * @param {HTMLElement|null} el
   * @param {{
   *   state?: "idle"|"busy"|"ok"|"warn"|"error",
   *   chip?: string,
   *   message?: string,
   *   oos?: object|null,
   *   sampleCount?: number|null,
   *   promotedAt?: string|null,
   *   busy?: boolean,
   *   error?: boolean,
   * }} opts
   */
  function renderRemStatus(el, opts = {}) {
    if (!el) return;
    const {
      state = "idle",
      chip = "待命",
      message = "",
      oos = null,
      sampleCount = null,
      promotedAt = null,
      busy = false,
      error = false,
    } = opts;
    const chipState = error ? "error" : busy ? "busy" : state;
    const metas = [];
    if (oos && typeof oos === "object") {
      if (oos.ic != null && Number.isFinite(Number(oos.ic))) {
        metas.push(remStatusMeta("OOS IC", fmtRemIc(oos.ic), "时间切分样本外 IC"));
      }
      if (oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))) {
        metas.push(
          remStatusMeta("命中", fmtRemHit(oos.sign_hit), "样本外方向命中率")
        );
      }
      const bt = oos.by_theme || {};
      const th = bt.theme || {};
      const nm = bt.normal || {};
      if (th.ic != null && Number.isFinite(Number(th.ic))) {
        metas.push(
          remStatusMeta(
            "主题IC",
            fmtRemIc(th.ic),
            `主题日 OOS IC · n=${th.n ?? "—"} · 命中 ${fmtRemHit(th.sign_hit)}`
          )
        );
      }
      if (nm.ic != null && Number.isFinite(Number(nm.ic))) {
        metas.push(
          remStatusMeta(
            "普通IC",
            fmtRemIc(nm.ic),
            `普通日 OOS IC · n=${nm.n ?? "—"} · 命中 ${fmtRemHit(nm.sign_hit)}`
          )
        );
      }
    }
    if (sampleCount != null && sampleCount !== "") {
      metas.push(remStatusMeta("n", fmtRemN(sampleCount), "入模观测数"));
    }
    if (promotedAt) {
      metas.push(
        `<span class="quant-rem-meta quant-rem-meta--time" title="${escapeHtml(
          String(promotedAt)
        )}">${escapeHtml(fmtRemTs(promotedAt))}</span>`
      );
    }
    const html =
      `<span class="quant-pro-status-chip quant-rem-status-chip" data-state="${escapeHtml(
        chipState
      )}">${escapeHtml(chip)}</span>` +
      (message
        ? `<span class="quant-rem-status-msg">${escapeHtml(message)}</span>`
        : "") +
      (metas.length
        ? `<span class="quant-rem-status-meta">${metas.join("")}</span>`
        : "");
    setBusyText(el, html, { busy: !!busy && !error, html: true });
    if (error) el.classList.add("is-error");
    else el.classList.remove("is-error");
  }

  function clearRemResultBox() {
    const box = document.getElementById("quant-rem-result");
    if (box) box.innerHTML = "";
  }

  // 轻量预填 ŷ_τ KPI / 状态（不阻塞；复盘加载后会用 tau_shadow 覆盖命中）
  void (async () => {
    try {
      const res = await fetch("/api/quant/rem-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-rem-summary");
      if (!data.exists) {
        renderRemStatus(sum, {
          state: "idle",
          chip: "未启用",
          message: data.note || "点「拟合」开始",
        });
        clearRemResultBox();
        await renderRemCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      renderRemStatus(sum, {
        state: "ok",
        chip: "已启用",
        oos,
        sampleCount: data.sample_count,
        promotedAt: data.promoted_at,
      });
      clearRemResultBox();
      await renderRemCoefTable(data.return_model || {}, { oos });
      // 仅当概览仍空时写入，避免盖住复盘 tau_shadow
      const card = document.querySelector(
        '#quant-pro-overview-kpis .quant-pro-kpi-card[data-kpi="tau"]'
      );
      if (card && card.classList.contains("is-empty")) {
        if (oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))) {
          syncOverviewTau(
            oos.sign_hit,
            `模型 OOS · IC ${fmtRemIc(oos.ic)}`,
            "hit"
          );
        } else if (oos.ic != null && Number.isFinite(Number(oos.ic))) {
          syncOverviewTau(oos.ic, "模型 OOS", "ic");
        } else {
          syncOverviewTau("已启用", data.promoted_at || "rem", "text");
        }
      }
    } catch (_) {
      /* ignore */
    }
  })();

  cluster.wireOosGateTips(els.quantOlsClusters);
  cluster.wireOosGateTips(els.quantFactorList);
  cluster.wireOosGateTips(els.quantWeightSuggest);
  if (els.quantOlsClusters && els.quantOlsClusters.dataset.exportWired !== "1") {
    els.quantOlsClusters.dataset.exportWired = "1";
    els.quantOlsClusters.addEventListener("click", cluster.onClusterExportClick);
  }
  if (els.quantFactorList && els.quantFactorList.dataset.clusterExportWired !== "1") {
    els.quantFactorList.dataset.clusterExportWired = "1";
    els.quantFactorList.addEventListener("click", cluster.onClusterExportClick);
  }

  async function loadPrefsHorizon() {
    try {
      const res = await fetch("/api/prefs");
      const data = await res.json();
      const prefs = (data && data.preferences) || {};
      state.prefsHorizonDays = clampHorizonDays(prefs.horizon_days, 1);
    } catch (_) {
      state.prefsHorizonDays = 1;
    }
    syncHorizonInputs(state.prefsHorizonDays);
    return state.prefsHorizonDays;
  }
  async function saveHorizonAsDefault() {
    const h = readHorizonDays();
    syncHorizonInputs(h);
    const res = await fetch("/api/memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences: { horizon_days: h } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && (data.detail || data.error)) || res.statusText);
    }
    const eff = (data && data.effective) || {};
    state.prefsHorizonDays = clampHorizonDays(eff.horizon_days, h);
    syncHorizonInputs(state.prefsHorizonDays);
    return state.prefsHorizonDays;
  }
  async function openQuantDialog(options = {}) {
    const { autoBacktest = false } = options;
    const page = document.body.dataset.page;
    const useDialog =
      page !== "quant" &&
      page !== "chat" &&
      page !== "strategy" &&
      page !== "replay" &&
      page !== "watching" &&
      page !== "follow" &&
      els.quantDialog &&
      typeof els.quantDialog.showModal === "function";
    const hasStrategy = !!document.getElementById("strategy-list");
    const hasWatching = !!document.getElementById("quant-watching-list") || !!document.getElementById("quant-watching-meta");
    const hasReplay = !!document.getElementById("quant-portfolio-run");
    const hasOps = !!document.getElementById("quant-ops-summary");
    try {
      if (els.quantMeta) els.quantMeta.textContent = "加载面板…";
      // 先拉研究默认 horizon（memory）与 OLS 标的列表，再跑 IC/OLS/回测
      await loadPrefsHorizon().catch(() => {});
      await cluster.populateOlsCodeOptions().catch(() => {});
      const foreground = [];
      if (hasWatching || hasReplay) {
        foreground.push(
          watching.loadWatchingPanel().catch((err) => {
            watching.setPoolMeta(String(err.message || err));
          })
        );
      }
      if (hasStrategy) {
        foreground.push(strategy.loadSignalConfigPanel().catch(() => {}));
        foreground.push(strategy.loadStrategyList().catch(() => {}));
      }
      // 前台只等名单/策略骨架，超时也放行，避免右侧一直「加载观察…」
      if (foreground.length) {
        await Promise.race([
          Promise.all(foreground),
          new Promise((resolve) => setTimeout(resolve, 12000)),
        ]);
      }
      if (els.quantMeta) {
        setQuantMeta("分组为主路径 · 不写 signal_config");
      }
      if (useDialog) els.quantDialog.showModal();
      // 运维/因子/桥接等后台拉取：不阻塞 tab 加载态
      const background = [];
      if (hasOps) background.push(exportDomain.loadOpsPanel().catch(() => {}));
      if (hasStrategy || els.quantFactorList) {
        background.push(suggest.loadFactorPanel().catch(() => {}));
      }
      if (hasReplay) background.push(backtest.loadLastBacktestSnapshot().catch(() => {}));
      // 研究枢纽：进页自动跑分组（refresh_bars 默认 false → 24h 缓存命中秒级返回；未命中用本地日线重算 ~30s）
      if (page === "quant" && (els.quantFactorList || els.quantOlsClusters)) {
        background.push(cluster.bootstrapClusterHub().catch(() => {}));
      }
      Promise.all(background)
        .then(async () => {
          if (autoBacktest && hasReplay) await backtest.runPortfolioBacktest();
        })
        .catch(() => {});
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
      if (useDialog) els.quantDialog.showModal();
    }
  }
  async function gotoPaperTab() {
    const page = document.body.dataset.page;
    if (typeof ctx.showResultsTab === "function" && (page === "chat" || !page)) {
      await ctx.showResultsTab("paper", { openMobile: true, load: true });
    }
  }

  async function onGotoFollow(e) {
    e.preventDefault();
    try {
      await watching.gotoFollowTab();
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  }

  const btnQuant = document.getElementById("btn-quant");
  // 对话工作台顶栏由 results Tab 接管，避免重复加载
  if (btnQuant && btnQuant.tagName === "BUTTON" && !btnQuant.dataset.resultsTab) {
    btnQuant.addEventListener("click", () => openQuantDialog());
  }

  on("quant-goto-paper", "click", onGotoFollow);
  on("quant-goto-paper-from-compare", "click", onGotoFollow);

  on("quant-follow-refresh", "click", async (e) => {
    e.preventDefault();
    await watching.loadFollowCard();
  });

  on("quant-watching-sync-follow", "click", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/watching/refresh?sync_paper=true", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      await watching.loadWatchingPanel();
      await watching.loadFollowCard();
      const el = document.getElementById("quant-follow-summary");
      if (el) el.textContent = `${el.textContent} · 已同步研究池`;
      } catch (err) {
      const el = document.getElementById("quant-follow-summary");
      if (el) el.textContent = String(err.message || err);
      }
  });

  on("quant-watching-init", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-watching-init");
    if (btn) btn.disabled = true;
    watching.setPoolMeta("正在从模板初始化…");
    try {
      const res = await fetch("/api/watching/init", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      await watching.loadWatchingPanel();
      watching.setWatchingRefreshStatus("已创建观察名单，可用搜索加入股票");
    } catch (err) {
      const msg = String(err.message || err);
      watching.setPoolMeta(msg);
      watching.setWatchingRefreshStatus(msg, { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-watching-remove", "click", async (e) => {
    e.preventDefault();
    await watching.removeSelectedWatchingItems();
  });

  on("quant-watching-sync", "click", (e) => {
    e.preventDefault();
    watching.openWatchingBuildLayer(watching.getSelectedWatchingCodes());
  });

  on("watching-build-close", "click", (e) => {
    e.preventDefault();
    watching.closeWatchingBuildLayer();
  });

  on("watching-build-cancel", "click", (e) => {
    e.preventDefault();
    watching.closeWatchingBuildLayer();
  });

  on("watching-build-confirm", "click", async (e) => {
    e.preventDefault();
    await watching.confirmWatchingBuild();
  });

  on("watching-build-shares-input", "input", () => {
    state.watchingBuildSharesByCode = {};
    state.watchingBuildAmountByCode = {};
    watching.scheduleWatchingBuildPreview();
  });

  document.querySelectorAll(".watching-build-mode-btn").forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const mode = String(btn.dataset.mode || "amount");
      if (mode === state.watchingBuildMode) return;
      state.watchingBuildMode = mode;
      state.watchingBuildSharesByCode = {};
      state.watchingBuildAmountByCode = {};
      const input = document.getElementById("watching-build-shares-input");
      if (input) {
        if (mode === "amount") input.value = "20000";
        else if (mode === "pct") input.value = "10";
        else input.value = "200";
      }
      watching.syncWatchingBuildModeUI();
      watching.scheduleWatchingBuildPreview();
    });
  });

  const watchingBuildBodyEl = document.getElementById("watching-build-body");
  if (watchingBuildBodyEl && watchingBuildBodyEl.dataset.sharesWired !== "1") {
    watchingBuildBodyEl.dataset.sharesWired = "1";
    watchingBuildBodyEl.addEventListener("input", (e) => {
      const sharesInput = e.target.closest(".watching-build-row-shares");
      if (sharesInput) {
        const code = String(sharesInput.dataset.code || "").trim();
        if (!code) return;
        const n = Math.floor(Number(sharesInput.value || 0) / 100) * 100;
        if (Number.isFinite(n) && n >= 100) {
          state.watchingBuildSharesByCode[code] = n;
        } else {
          delete state.watchingBuildSharesByCode[code];
        }
        watching.scheduleWatchingBuildPreview();
        return;
      }
      const amtInput = e.target.closest(".watching-build-row-amount");
      if (!amtInput) return;
      const code = String(amtInput.dataset.code || "").trim();
      if (!code) return;
      const n = Number(amtInput.value || 0);
      if (Number.isFinite(n) && n > 0) {
        state.watchingBuildAmountByCode[code] = Math.round(n * 100) / 100;
      } else {
        delete state.watchingBuildAmountByCode[code];
      }
      watching.scheduleWatchingBuildPreview();
    });
  }

  on("watching-build-layer", "click", (e) => {
    if (e.target && e.target.id === "watching-build-layer") watching.closeWatchingBuildLayer();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const layer = document.getElementById("watching-build-layer");
    if (layer && !layer.hidden) watching.closeWatchingBuildLayer();
  });

  const watchTableEl = document.getElementById("watching-watchlist-table");
  if (watchTableEl) {
    watchingScoreTips.bindHost(watchTableEl, {
      scoreSelector: ".watching-score-cell[data-score-detail], .paper-hold-score[data-score-detail]",
    });
  }
  if (watchTableEl && watchTableEl.dataset.removeWired !== "1") {
    watchTableEl.dataset.removeWired = "1";
    watchTableEl.addEventListener("click", (e) => {
      const buildBtn = e.target.closest(".watching-build-btn");
      if (buildBtn) {
        e.preventDefault();
        const tr = buildBtn.closest("[data-code]");
        const nameEl = tr ? tr.querySelector(".watching-name-text") : null;
        watching.openWatchingBuildLayer(
          [buildBtn.dataset.code],
          watching.watchingNameFromEl(nameEl, buildBtn.dataset.code)
        );
        return;
      }
      const heldBtn = e.target.closest(".watching-held-btn");
      if (heldBtn) {
        e.preventDefault();
        watching.gotoFollowPage(heldBtn.dataset.code);
        return;
      }
      const sentBadge = e.target.closest(".watching-sent-badge");
      if (sentBadge) {
        e.preventDefault();
        const code = sentBadge.dataset.code;
        const tr = sentBadge.closest("[data-code]");
        const nameEl = tr ? tr.querySelector(".watching-name-text") : null;
        const name = watching.watchingNameFromEl(nameEl, tr ? tr.dataset.code : code);
        watching.showWatchingChart(code, name);
        watching.openWatchingNewsDetail(code);
        return;
      }
      // 点击股票名称行时同步显示日线图 + 舆情
      const stockCell = e.target.closest(".watching-stock");
      if (stockCell) {
        const tr = stockCell.closest("[data-code]");
        if (tr && tr.dataset.code) {
          e.preventDefault();
          const nameEl = stockCell.querySelector(".watching-name-text");
          const name = watching.watchingNameFromEl(nameEl, tr.dataset.code);
          watching.showWatchingChart(tr.dataset.code, name);
          watching.openWatchingNewsDetail(tr.dataset.code);
        }
      }
    });
    watchTableEl.addEventListener("change", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLInputElement)) return;
      if (t.id === "watching-select-all") {
        if (state.watchingGrid && state.watchingGridReady) {
          (state.watchingGrid.getData() || []).forEach((rowData) => {
            const code = String((rowData && rowData.code) || "");
            const row = state.watchingGrid.getRow(code);
            if (row) row.update({ picked: !!t.checked });
          });
        } else {
          watchTableEl.querySelectorAll(".watching-pick").forEach((box) => {
            box.checked = t.checked;
          });
        }
        t.indeterminate = false;
        watching.updateWatchingPickCount();
        return;
      }
      if (t.classList.contains("watching-pick")) {
        const code = String(t.dataset.code || t.value || "").trim();
        if (state.watchingGrid && state.watchingGridReady && code) {
          const row = state.watchingGrid.getRow(code);
          if (row) row.update({ picked: !!t.checked });
        }
        watching.syncWatchingSelectAllState();
      }
    });
  }

  on("watching-news-detail-close", "click", (e) => {
    e.preventDefault();
    watching.hideWatchingNewsDetail();
  });

  on("watching-chart-close", "click", (e) => {
    e.preventDefault();
    watching.hideWatchingChart();
  });

  on("quant-cross-run", "click", async (e) => {
    e.preventDefault();
    try {
      await cluster.refreshCrossSection();
    } catch (err) {
      if (els.quantCrossSummary) els.quantCrossSummary.textContent = String(err.message || err);
    }
  });










  on("quant-factor-run", "click", async (e) => {
    e.preventDefault();
    try {
      await suggest.runFactorIcSuggest();
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-probe-run", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-probe-run");
    if (btn) btn.disabled = true;
    try {
      await cluster.runProbeStockVsGroup();
    } catch (err) {
      setBusyText(els.quantProbeSummary, String(err.message || err), { busy: false });
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-ols-code", "change", async () => {
    // 换票后自动对照（需已有分组）
    const btn = document.getElementById("quant-probe-run");
    if (btn) btn.disabled = true;
    try {
      await cluster.runProbeStockVsGroup();
    } catch (err) {
      setBusyText(els.quantProbeSummary, String(err.message || err), { busy: false });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  // 兼容隐藏入口 / 旧深链
  on("quant-ols-run", "click", async (e) => {
    e.preventDefault();
    try {
      await cluster.runProbeStockVsGroup();
    } catch (err) {
      setBusyText(els.quantProbeSummary, String(err.message || err), { busy: false });
    }
  });

  on("quant-ols-pool-run", "click", async (e) => {
    e.preventDefault();
    // 研究池 OLS 已移出探针；隐藏按钮若被触发则提示改用对照验证
    setBusyText(
      els.quantProbeSummary,
      "研究池 OLS 已从探针移除 · 请用「对照验证」看单票 vs 所在组",
      { busy: false }
    );
  });

  on("quant-cs-ic-run", "click", async (e) => {
    e.preventDefault();
    try {
      await cluster.runProbeStockVsGroup();
    } catch (err) {
      setBusyText(els.quantProbeSummary, String(err.message || err), { busy: false });
    }
  });

  on("quant-ols-clusters-run", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-ols-clusters-run");
    if (btn) btn.disabled = true;
    setProStatusChip("quant-pro-cluster-status", "busy", "分组计算中…");
    try {
      await suggest.runFactorOlsClustersSuggest();
      // 概览 KPI 已在 renderOlsClusters 内从结果写回；此处兜底
      const last = state.quantLastOlsClusters;
      if (last && last.success) {
        syncOverviewFromClusters(last);
        const k = last.n_clusters ?? (last.clusters || []).length;
        setProStatusChip("quant-pro-cluster-status", "ok", k ? `${k} 组就绪` : "完成");
      } else {
        setProStatusChip("quant-pro-cluster-status", "ok", "完成");
      }
    } catch (err) {
      const raw = String((err && err.message) || err || "分组失败");
      const msg = /failed to fetch|networkerror|load failed/i.test(raw)
        ? "服务断开（可能刚重启），请再点「跑分组」"
        : raw;
      setBusyText(els.quantOlsSummary, msg, { busy: false });
      setQuantMeta(`分组失败 · ${msg}`, { error: true });
      if (els.quantOlsClusters) {
        els.quantOlsClusters.hidden = false;
        els.quantOlsClusters.textContent = msg;
      }
      setProStatusChip(
        "quant-pro-cluster-status",
        "error",
        msg.length > 24 ? "分组失败" : msg
      );
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  // Factor analysis: correlation heatmap + IR
  on("quant-factor-corr-run", "click", async (e) => {
    e.preventDefault();
    await loadAndRenderFactorCorr("quant-factor-corr-heatmap");
  });

    on("quant-ic-series-run", "click", async (e) => {
      e.preventDefault();
      const lookback = document.getElementById("quant-ic-series-lookback")?.value || "60";
      const statusEl = document.getElementById("quant-ic-series-status");
      await loadAndRenderFactorICSeries(
        "quant-ic-series-chart",
        "quant-ic-series-stats",
        statusEl,
        lookback
      );
    });

  // 复盘 / 拟合 KPI 由各自 domain 在渲染时直接写概览
  on("quant-score-review-run", "click", () => setProStatusChip("quant-pro-review-status", "busy", "复盘计算中…"));

  const oosGateTips = createScoreTooltipController();
  cluster.wireOosGateTips(els.quantOlsClusters);
  cluster.wireOosGateTips(els.quantFactorList);
  cluster.wireOosGateTips(els.quantWeightSuggest);

  if (els.quantOlsClusters && els.quantOlsClusters.dataset.exportWired !== "1") {
    els.quantOlsClusters.dataset.exportWired = "1";
    els.quantOlsClusters.addEventListener("click", onClusterExportClick);
  }
  if (els.quantFactorList && els.quantFactorList.dataset.clusterExportWired !== "1") {
    els.quantFactorList.dataset.clusterExportWired = "1";
    els.quantFactorList.addEventListener("click", onClusterExportClick);
  }

  on("quant-horizon-save-default", "click", async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    if (btn) btn.disabled = true;
    try {
      const h = await saveHorizonAsDefault();
      const msg = `已存研究默认 horizon=${h}d（不影响数据中心/交易 live）`;
      if (typeof setQuantMeta === "function") setQuantMeta(msg);
      const replayMeta = document.getElementById("replay-meta");
      if (replayMeta) replayMeta.textContent = msg;
    } catch (err) {
      const detail = String(err.message || err);
      if (typeof setQuantMeta === "function") {
        setQuantMeta(`存默认失败 · ${detail}`, { error: true });
      } else {
        window.alert(detail);
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-cs-ic-run", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-cs-ic-run");
    if (btn) btn.disabled = true;
    try {
      await suggest.runFactorCsIcSuggest();
    } catch (err) {
      setBusyText(els.quantOlsSummary, String(err.message || err), { busy: false });
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-weight-export", "click", (e) => {
    e.preventDefault();
    if (!state.quantLastWeightDiff || !state.quantLastWeightDiff.success) {
      if (els.quantMeta) els.quantMeta.textContent = "建议尚未就绪，稍后再导出权重 diff";
      return;
    }
    if (state.quantLastWeightDiff.promote_ready === false) {
      const note = state.quantLastWeightDiff.apply_note || "OOS 未过或已跳过";
      if (
        !window.confirm(
          `当前建议 promote_ready=否（${note}）。仍导出 diff 仅供对照？`
        )
      ) {
        return;
      }
    }
    downloadJson(state.quantLastWeightDiff, "signal_config_weight_diff.json");
  });

  on("strategy-weight-suggest-run", "click", (e) => {
    e.preventDefault();
    strategy.runStrategyWeightSuggest();
  });
  on("strategy-ic-export", "click", (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-factor-status");
    if (!state.strategyLastIcExport) {
      if (status) status.textContent = "请先「分析 IC / 权重」";
      return;
    }
    downloadJson(state.strategyLastIcExport, "factor_ic_export.json");
  });
  on("strategy-weight-diff-export", "click", (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-factor-status");
    if (!state.strategyLastWeightDiff) {
      if (status) status.textContent = "请先「分析 IC / 权重」";
      return;
    }
    downloadJson(state.strategyLastWeightDiff, "signal_config_weight_diff.json");
  });

  on("strategy-feedback-from-ic", "click", async (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-factor-status");
    if (!state.strategyLastWeightDiff) {
      if (status) status.textContent = "请先「分析 IC / 权重」";
      return;
    }
    if (status) status.textContent = "生成反馈建议中…";
    try {
      const { ok, data, error } = await apiFetch("/api/feedback/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backtest_metrics: {
            win_rate_pct: null,
            max_drawdown_pct: null,
            weight_suggest: state.strategyLastWeightDiff,
          },
          paper_metrics: {},
          monitor_alerts: [
            {
              level: "info",
              code: "ic_weight_suggest",
              message: "来自策略页 IC/权重分析的人审建议入口",
            },
          ],
        }),
      });
      if (!ok) throw new Error(error || "反馈失败");
      const reasons = (data.reasons || []).filter(Boolean);
      if (status) {
        status.textContent =
          reasons.slice(0, 2).join("；") ||
          data.note ||
          "已生成建议（未写盘）· 选股改β请到研究枢纽；限额改策略卡晋升";
      }
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      }
  });


  on("quant-threshold-run", "click", async (e) => {
    e.preventDefault();
    try {
      await suggest.runThresholdSuggest({ useWatching: false });
    } catch (err) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent = String(err.message || err);
      }
    }
  });

  on("quant-threshold-watching", "click", async (e) => {
    e.preventDefault();
    try {
      await suggest.runThresholdSuggest({ useWatching: true });
    } catch (err) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent = String(err.message || err);
      }
    }
  });

  async function runRemRidge({ persist = false } = {}) {
    const sum = document.getElementById("quant-rem-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist ? "写入上次拟合…" : "Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const remLimit = Math.min(40, Math.max(2, Number(readWatchingLimit()) || 36));
      const res = await fetch("/api/quant/rem-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: remLimit,
          ridge_lambda: 1.0,
          persist: !!persist,
          note: persist ? "ui rem promote" : "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        clearRemResultBox();
        renderRemCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      if (persist) {
        renderRemStatus(sum, {
          state: "ok",
          chip: "已启用",
          oos,
          sampleCount: data.sample_count,
          promotedAt: data.promoted_at || new Date().toISOString(),
        });
      } else {
        renderRemStatus(sum, {
          state: "warn",
          chip: "未写盘",
          message: "人审后点「启用」",
          oos,
          sampleCount: data.sample_count,
        });
      }
      if (oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))) {
        syncOverviewTau(
          oos.sign_hit,
          `rem OOS · IC ${fmtRemIc(oos.ic)}${persist ? " · 已启用" : " · 未写盘"}`,
          "hit"
        );
      } else if (oos.ic != null && Number.isFinite(Number(oos.ic))) {
        syncOverviewTau(oos.ic, persist ? "rem OOS · 已启用" : "rem OOS · 未写盘", "ic");
      }
      const rm = data.return_model || {};
      clearRemResultBox();
      await renderRemCoefTable(rm, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      throw err;
    }
  }

  on("quant-rem-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runRemRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-rem-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-rem-ridge-persist", "click", async (e) => {
    e.preventDefault();
    if (!window.confirm("将 rem / ŷ_τ 模型写入 live（不改 ŷ_EOD、不改聚类）？仅影响主题日 soft hold / τ 闸。")) return;
    try {
      await runRemRidge({ persist: true });
    } catch (err) {
      const sum = document.getElementById("quant-rem-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-rem-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-rem-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/rem-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        renderRemStatus(sum, {
          state: "idle",
          chip: "未启用",
          message: data.note || "尚无 live 模型",
        });
        syncOverviewTau("未启用", "rem_ridge_model 缺失", "text");
        clearRemResultBox();
        await renderRemCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      renderRemStatus(sum, {
        state: "ok",
        chip: "已启用",
        oos,
        sampleCount: data.sample_count,
        promotedAt: data.promoted_at,
      });
      if (oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))) {
        syncOverviewTau(
          oos.sign_hit,
          `已启用 · IC ${fmtRemIc(oos.ic)} · ${fmtRemTs(data.promoted_at) || ""}`,
          "hit"
        );
      } else if (oos.ic != null && Number.isFinite(Number(oos.ic))) {
        syncOverviewTau(oos.ic, `已启用 · ${fmtRemTs(data.promoted_at) || ""}`, "ic");
      } else {
        syncOverviewTau("已启用", data.promoted_at || "rem 模型", "text");
      }
      clearRemResultBox();
      await renderRemCoefTable(data.return_model || {}, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-threshold-export", "click", (e) => {
    e.preventDefault();
    if (!state.quantLastThresholdDiff || !state.quantLastThresholdDiff.success) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent = "建议尚未就绪，稍后再导出阈值 diff";
      }
      return;
    }
    if (state.quantLastThresholdDiff.skipped_apply) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent =
          "无门槛改动可导出（已跳过或与当前接近）";
      }
      return;
    }
    downloadJson(state.quantLastThresholdDiff, "signal_config_threshold_diff.json");
  });

  on("quant-threshold-apply", "click", async (e) => {
    e.preventDefault();
    try {
      await suggest.applyThresholdSuggest();
    } catch (err) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent = String(err.message || err);
      }
    }
  });

  on("quant-portfolio-run", "click", async (e) => {
    e.preventDefault();
    try {
      await backtest.runPortfolioBacktest();
    } catch (err) {
      els.quantPortfolioSummary.textContent = String(err.message || err);
      backtest.paintPortfolioChart([], "回测失败");
    }
  });

  on("quant-portfolio-compare", "click", async (e) => {
    e.preventDefault();
    try {
      await backtest.runPortfolioNeutralCompare();
    } catch (err) {
      els.quantPortfolioSummary.textContent = String(err.message || err);
      backtest.paintPortfolioChart([], "对照失败");
    }
  });


  on("quant-return-model-fit", "click", async (e) => {
    e.preventDefault();
    try {
      await backtest.fitReturnScoreModel();
    } catch (err) {
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = String(err.message || err);
    }
  });





  on("quant-param-grid-run", "click", async (e) => {
    e.preventDefault();
    try {
      await backtest.runParamGrid();
    } catch (err) {
      const meta = document.getElementById("param-grid-meta");
      if (meta) meta.textContent = String(err.message || err);
      }
  });

  on("quant-param-grid-apply-best", "click", (e) => {
    e.preventDefault();
    const best = state.lastParamGrid && state.lastParamGrid.best;
    const meta = document.getElementById("param-grid-meta");
    const gate = backtest.paramGridApplyGate();
    if (!best || !gate.ok) {
      if (meta) meta.textContent = gate.reason || "无最优单元可应用";
      backtest.refreshParamGridApplyGate();
      return;
    }
    const dull = state.lastParamGrid.dullness || backtest.paramGridDullness(state.lastParamGrid);
    if (dull && dull.sharp) {
      const ok = window.confirm(
        `过门最优格相对邻格 OOS 差达 ${dull.max_gap_pp}pp，可能过拟合尖峰。仍应用 lookback=${best.lookback} / top_k=${best.top_k}？`
      );
      if (!ok) {
        if (meta) meta.textContent = "已取消应用最优（邻格过尖）";
        return;
      }
    }
    if (gate.warn) {
      const okAlign = window.confirm(
        `${gate.reason}\n\n仍将最优 lookback=${best.lookback} / top_k=${best.top_k} 写入回测表单？`
      );
      if (!okAlign) {
        if (meta) meta.textContent = "已取消应用最优（需确认）";
        return;
      }
    }
    backtest.applyParamGridCellToForm(best, {
      horizon_days: state.lastParamGrid && state.lastParamGrid.horizon_days,
    });
    if (meta) {
      meta.textContent =
        `已写入 lookback=${best.lookback} · top_k=${best.top_k}` +
        (state.lastParamGrid && state.lastParamGrid.horizon_days != null
          ? ` · h=${state.lastParamGrid.horizon_days}`
          : "") +
        `；请再跑 Top-K 看完整报告。仍勿静默 promote` +
        (dull && dull.sharp ? ` · 邻格Δ ${dull.max_gap_pp}pp` : "");
    }
  });



  on("quant-research-export", "click", (e) => {
    e.preventDefault();
    const result = state.lastBacktestPack && state.lastBacktestPack.result;
    const pack = {
      exported_at: new Date().toISOString(),
      backtest: state.lastBacktestPack,
      param_grid: state.lastParamGrid,
      curves: backtest.buildResearchCurves(result),
      promote_meta: backtest.buildResearchPromoteMeta(result),
      note: "研究包 · 含 IC/分层/基准曲线与 promote_meta（硬闸/提示）；不含实盘指令",
    };
    if (!pack.backtest && !pack.param_grid) {
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = "请先跑回测或参数网格";
      return;
    }
    downloadJson(pack, `research_pack_${Date.now()}.json`);
    if (els.quantPortfolioSummary && pack.curves) {
      els.quantPortfolioSummary.textContent =
        (els.quantPortfolioSummary.textContent || "") + " · 已导出研究包(含曲线/promote_meta)";
    }
  });

  on("quant-backtest-report-export", "click", async (e) => {
    e.preventDefault();
    const result = state.lastBacktestPack && state.lastBacktestPack.result;
    if (!result || !result.success) {
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = "请先跑 Top-K 回测";
      return;
    }
    try {
      const { ok, data, error } = await apiFetch("/api/quant/export/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ result, format: "markdown" }),
      });
      if (!ok) throw new Error(error || "导出失败");
      const blob = new Blob([data.content || ""], {
        type: "text/markdown;charset=utf-8",
      });
      downloadBlob(blob, data.filename || `portfolio_backtest_${Date.now()}.md`);
      if (els.quantPortfolioSummary) {
        els.quantPortfolioSummary.textContent =
          (els.quantPortfolioSummary.textContent || "") + " · 已导出回测报告 MD";
      }
    } catch (err) {
      if (els.quantPortfolioSummary) {
        els.quantPortfolioSummary.textContent = String(err.message || err);
      }
    }
  });

  document.getElementById("strategy-factor-dict-list")?.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("[data-factor]");
    if (!btn) return;
    e.preventDefault();
    const name = btn.getAttribute("data-factor") || "";
    const tip = btn.getAttribute("title") || name;
    const q = `请解释因子 ${name}：${tip}`;
    if (typeof window.__investmentOpenAi === "function") {
      window.__investmentOpenAi(q);
      setTimeout(() => {
        document.getElementById("ai-drawer-form")?.requestSubmit();
      }, 40);
    }
  });

  on("quant-export-preview-md", "click", async (e) => {
    e.preventDefault();
    try {
      await exportDomain.previewQuantExport("markdown");
    } catch (err) {
      if (els.quantExportPreviewMeta) els.quantExportPreviewMeta.textContent = String(err.message || err);
    }
  });

  on("quant-export-preview-html", "click", async (e) => {
    e.preventDefault();
    try {
      await exportDomain.previewQuantExport("html");
    } catch (err) {
      if (els.quantExportPreviewMeta) els.quantExportPreviewMeta.textContent = String(err.message || err);
    }
  });

  // 日报：进页拉预览；深链打开折叠并滚到卡片
  exportDomain.ensureDailyPreview();
  if (String(location.hash || "").replace(/^#/, "") === "quant-daily-fold") {
    exportDomain.openDailyFold();
  }

  // 路径轨：点击切换当前步高亮；深链到次要块时展开
  const pathRail = document.querySelector(".quant-page .quant-path-rail");
  if (pathRail) {
    pathRail.addEventListener("click", (e) => {
      const step = e.target.closest?.("a.quant-path-step");
      if (!step) return;
      pathRail.querySelectorAll("a.quant-path-step").forEach((a) => {
        a.classList.toggle("is-active", a === step);
        if (a === step) a.setAttribute("aria-current", "step");
        else a.removeAttribute("aria-current");
      });
    });
  }
  const openSecondaryByHash = () => {
    const id = String(location.hash || "").replace(/^#/, "");
    if (!id) return;
    const sec = document.getElementById(id);
    if (!sec?.classList.contains("quant-section-secondary")) return;
    const fold = sec.querySelector("details.quant-secondary-fold");
    if (fold) fold.open = true;
  };
  openSecondaryByHash();
  window.addEventListener("hashchange", openSecondaryByHash);

  on("quant-export-md", "click", async (e) => {
    e.preventDefault();
    exportDomain.openDailyFold();
    try {
      const res = await fetch("/api/quant/export?format=markdown&use_saved=true");
      const data = await res.json();
      if (!res.ok) {
        if (els.quantMeta) els.quantMeta.textContent = data.detail || data.error || "导出失败";
        return;
      }
      const blob = new Blob([data.content || ""], { type: "text/markdown;charset=utf-8" });
      downloadBlob(blob, data.filename || "quant_daily.md");
      if (els.quantMeta) els.quantMeta.textContent = "Markdown 已下载";
      } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
      }
  });

  on("quant-export-html", "click", async (e) => {
    e.preventDefault();
    exportDomain.openDailyFold();
    try {
      const res = await fetch("/api/quant/export?format=html&use_saved=true");
      const data = await res.json();
      if (!res.ok) {
        if (els.quantMeta) els.quantMeta.textContent = data.detail || data.error || "导出失败";
        return;
      }
      const blob = new Blob([data.content || ""], { type: "text/html;charset=utf-8" });
      downloadBlob(blob, data.filename || "quant_daily.html");
      if (els.quantMeta) els.quantMeta.textContent = "HTML 已下载";
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  });





  on("quant-interpret", "click", async (e) => {
    e.preventDefault();
    const q =
      "请解读上次量化日报（quant_daily / use_saved）：概括因子 IC、权重建议、组合表现与主要风险；" +
      "不要改写 score / stance_label；结论须可核对数据。";
    if (typeof window.__investmentOpenAi === "function") {
      if (els.quantMeta) els.quantMeta.textContent = "已打开 AI 助手…";
      window.__investmentOpenAi(q);
      setTimeout(() => {
        document.getElementById("ai-drawer-form")?.requestSubmit();
      }, 40);
      return;
    }
    /* 抽屉不可用时回退本页解读 */
    try {
      await exportDomain.runQuantInterpret({ forceOffline: false });
    } catch (err) {
      exportDomain.showQuantInterpretPanel();
      if (els.quantInterpretBody) exportDomain.setQuantInterpretContent(String(err.message || err));
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-interpret-offline", "click", async (e) => {
    e.preventDefault();
    try {
      await exportDomain.runQuantInterpret({ forceOffline: true });
    } catch (err) {
      exportDomain.showQuantInterpretPanel();
      if (els.quantInterpretBody) exportDomain.setQuantInterpretContent(String(err.message || err));
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-feedback-suggest", "click", async (e) => {
    e.preventDefault();
    try {
      if (els.quantMeta) els.quantMeta.textContent = "生成配置反馈中…";
      exportDomain.showQuantInterpretPanel();
      if (els.quantInterpretBody) els.quantInterpretBody.textContent = "生成配置反馈中…";
      const m = ctx.lastBacktestMetrics || {};
      const { ok, data, error } = await apiFetch("/api/feedback/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backtest_metrics: {
            win_rate_pct: m.win_rate_pct ?? null,
            max_drawdown_pct: m.max_drawdown_pct ?? null,
            ...(state.quantLastWeightDiff ? { weight_suggest: state.quantLastWeightDiff } : {}),
          },
          paper_metrics: {},
          monitor_alerts: [
            {
              level: "info",
              code: "quant_hub_feedback",
              message: "来自研究枢纽的配置反馈入口",
            },
          ],
        }),
      });
      if (!ok) throw new Error(error || "反馈失败");
      const reasons = (data.reasons || []).filter(Boolean);
      const note =
        reasons.slice(0, 3).join("；") ||
        data.note ||
        "已生成建议（未写盘）· 选股改β请到研究枢纽";
      if (els.quantMeta) els.quantMeta.textContent = note;
      if (els.quantInterpretBody) {
        const patch = data.patch && typeof data.patch === "object" ? data.patch : {};
        const lines = [
          "【配置反馈】未写盘，须人审后合并。",
          note,
          Object.keys(patch).length
            ? `patch keys: ${Object.keys(patch).join(", ")}`
            : "",
        ].filter(Boolean);
        els.quantInterpretBody.textContent = lines.join("\n");
      }
      if (typeof ctx.showResultsTab === "function" && document.body.dataset.page === "chat") {
        await ctx.showResultsTab("platform", { openMobile: true, load: true });
      }
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
      exportDomain.showQuantInterpretPanel();
      if (els.quantInterpretBody) els.quantInterpretBody.textContent = String(err.message || err);
    }
  });

  on("quant-diff-preview", "click", async (e) => {
    e.preventDefault();
    await strategy.loadConfigDiffPreview();
  });

  on("quant-diff-export", "click", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/signal/config/diff-export?use_saved=true");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      downloadJson(data, data.filename || "signal_config_diff_bundle.json");
      if (els.quantDiffSummary) {
        els.quantDiffSummary.textContent = "diff 包已下载 · 请手动合并 merged_patch";
      }
    } catch (err) {
      if (els.quantDiffSummary) els.quantDiffSummary.textContent = String(err.message || err);
      }
  });

  on("quant-daily", "click", async (e) => {
    e.preventDefault();
    try {
      await exportDomain.runDailyWithPreset("quant", "每日量化任务运行中…");
    } catch (err) {
      setBusyText(
        els.quantOpsSummary,
        String(err.message || err),
        { busy: false }
      );
    }
  });

  on("quant-ops-run-daily", "click", async (e) => {
    e.preventDefault();
    try {
      await exportDomain.runDailyWithPreset("quant", "生成日报中…");
    } catch (err) {
      setBusyText(
        els.quantOpsSummary,
        String(err.message || err),
        { busy: false }
      );
    }
  });

  on("quant-ops-refresh", "click", async (e) => {
    e.preventDefault();
    await exportDomain.loadOpsPanel();
  });

  attachReadmeLinkHandler(els.quantOpsPackage, ctx);

  ctx.openQuantDialog = openQuantDialog;
  ctx.openReadmeViewer = exportDomain.openReadmeViewer;
  ctx.reloadWatching = watching.loadWatchingPanel;



  document.getElementById("watching-data-quality-fold")?.addEventListener("toggle", (e) => {
    if (e.target.open) watching.loadWatchingDataQuality();
  });
  document.getElementById("strategy-risk-audit-fold")?.addEventListener("toggle", (e) => {
    if (e.target.open) strategy.loadStrategyRiskAudit();
  });

  document.getElementById("strategy-risk-audit")?.addEventListener("click", async (e) => {
    const btn = e.target.closest && e.target.closest(".strategy-rb-annotate");
    if (!btn) return;
    e.preventDefault();
    const index = Number(btn.getAttribute("data-index"));
    const outcome = btn.getAttribute("data-outcome") || "";
    const meta = document.getElementById("strategy-risk-meta");
    try {
      const { ok, data, error } = await apiFetch("/api/paper/risk-blocks/annotate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index, outcome }),
      });
      if (!ok) throw new Error(error || "标注失败");
      if (meta) {
        meta.textContent = `已标注 #${index} → ${data.outcome || outcome}`;
      }
      await strategy.loadStrategyRiskAudit();
    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      }
  });

  // 进页面时先把“折叠 summary 结论”拉出来；同时支持 hash 深链强制打开对应折叠
  const page = document.body.dataset.page || "";
  const hash = String(location.hash || "").replace(/^#/, "");
  // 仅数据中心注册：避免 /follow 上覆盖 paper 的当前持仓 getter
  if (page === "watching") {
    window.__investmentGetCurrentStock = () => {
      if (state.watchingFocusCode) {
        return {
          code: state.watchingFocusCode,
          name:
            state.watchingFocusName ||
            state.watchingNameByCode[state.watchingFocusCode] ||
            "",
        };
      }
      let picked = [];
      if (state.watchingGrid && state.watchingGridReady && typeof state.watchingGrid.getData === "function") {
        picked = (state.watchingGrid.getData() || [])
          .filter((r) => r && r.picked && r.code)
          .map((r) => ({
            code: String(r.code).trim(),
            name: String(r.name || state.watchingNameByCode[r.code] || "").trim(),
          }));
      } else {
        picked = watching.getSelectedWatchingCodes().map((code) => ({
          code: String(code).trim(),
          name: String(state.watchingNameByCode[code] || "").trim(),
        }));
      }
      if (picked.length === 1) return picked[0];
      return null;
    };
  }
  if (hash === "watching-data-quality-fold") {
    document.getElementById("watching-data-quality-fold").open = true;
    watching.loadWatchingDataQuality().catch(() => {});
  } else if (page === "watching") {
    watching.loadWatchingDataQuality().catch(() => {});
  }
  if (hash === "strategy-risk-audit-fold") {
    document.getElementById("strategy-risk-audit-fold").open = true;
    strategy.loadStrategyRiskAudit().catch(() => {});
  } else if (page === "strategy") {
    strategy.loadStrategyRiskAudit().catch(() => {});
    q.renderPromoteHintsPanel(loadCachedPromoteHints(), "strategy-promote-hints");
  }
  if (hash === "strategy-factor-dict") {
    const fold = document.getElementById("strategy-factor-dict-fold");
    if (fold) fold.open = true;
  }

  // T15/T17：IC硬闸 / TTL / 过期硬拦（默认均关或 24h）
  const hardGateEl = document.getElementById("quant-promote-hard-gate");
  if (hardGateEl) {
    try {
      hardGateEl.checked = sessionStorage.getItem(PROMOTE_HARD_GATE_KEY) === "1";
    } catch (_) {
      hardGateEl.checked = false;
    }
    hardGateEl.addEventListener("change", () => {
      persistPromoteHardGate(!!hardGateEl.checked);
      backtest.refreshParamGridApplyGate();
    });
  }
  const ttlEl = document.getElementById("quant-promote-ttl-hours");
  if (ttlEl) {
    try {
      const saved = Number(sessionStorage.getItem(PROMOTE_HINTS_TTL_HOURS_KEY));
      if (Number.isFinite(saved) && saved > 0) {
        ttlEl.value = String(Math.max(1, Math.min(168, Math.round(saved))));
      }
    } catch (_) {
      /* ignore */
    }
    ttlEl.addEventListener("change", () => {
      const h = readPromoteTtlHours();
      ttlEl.value = String(h);
      persistPromoteTtlHours(h);
      q.renderPromoteHintsPanel(loadCachedPromoteHints(), "quant-promote-hints");
      q.renderPromoteHintsPanel(loadCachedPromoteHints(), "strategy-promote-hints");
    });
  }
  const expireHardEl = document.getElementById("strategy-promote-expire-hard");
  if (expireHardEl) {
    try {
      expireHardEl.checked = sessionStorage.getItem(PROMOTE_EXPIRE_HARD_KEY) === "1";
    } catch (_) {
      expireHardEl.checked = false;
    }
    expireHardEl.addEventListener("change", () => {
      persistPromoteExpireHard(!!expireHardEl.checked);
    });
  }
  if (document.getElementById("quant-promote-hints")) {
    q.renderPromoteHintsPanel(loadCachedPromoteHints(), "quant-promote-hints");
  }

  const floorSaveBtn = document.getElementById("strategy-floor-save");
  if (floorSaveBtn) {
    floorSaveBtn.addEventListener("click", (e) => {
      e.preventDefault();
      strategy.saveStrategyScoringFloors().catch(() => {});
    });
  }

  const dualSaveBtn = document.getElementById("strategy-dual-save");
  if (dualSaveBtn) {
    dualSaveBtn.addEventListener("click", (e) => {
      e.preventDefault();
      strategy.saveStrategyDualScore().catch(() => {});
    });
  }
  strategy.loadDualScoreForm?.().catch(() => {});

  const priorSaveBtn = document.getElementById("strategy-prior-save");
  if (priorSaveBtn) {
    priorSaveBtn.addEventListener("click", (e) => {
      e.preventDefault();
      strategy.saveStrategySentimentPrior().catch(() => {});
    });
  }
  document.querySelectorAll('input[name="strategy-prior-mode"]').forEach((el) => {
    el.addEventListener("change", () => {
      if (typeof strategy.syncPriorGateOptsVisibility === "function") {
        strategy.syncPriorGateOptsVisibility();
      } else {
        const opts = document.getElementById("strategy-prior-gate-opts");
        const checked = document.querySelector(
          'input[name="strategy-prior-mode"]:checked'
        );
        if (opts) opts.hidden = !(checked && checked.value === "gate");
      }
    });
  });
  strategy.loadSentimentPriorForm?.().catch(() => {});

  ctx.applyQuantArtifact = async function applyQuantArtifact(art) {
    const data = (art && art.data) || {};
    const task = String((art.params && art.params.task) || data.task || "").toLowerCase();
    const summary = (art && art.summary) || "Agent 量化结果";

    if (data.metrics && (data.trades_sample || data.equity_curve || data.equity_curve_tail || data.loaded_stocks)) {
      backtest.renderPortfolioBacktestResult(data);
      backtest.paintPortfolioChart(
        data.equity_curve_tail || data.equity_curve,
        "无净值曲线"
      );
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = summary;
      return;
    }
    if (data.t0_pnl_total != null || data.t0_trade_days != null || (data.days && task.includes("t0"))) {
      backtest.renderT0BacktestResult(data);
      if (els.quantT0Summary) els.quantT0Summary.textContent = summary;
      else if (els.quantMeta) els.quantMeta.textContent = `${summary} · 详情见纸面 Tab`;
      return;
    }
    if (task === "cross_section" || data.picks || data.cross_section) {
      cluster.renderCrossSection(data.cross_section || data);
      return;
    }
    if (task === "factor_ols" || data.coefficients || data.rows) {
      cluster.renderFactorOls(data);
      return;
    }
    if (data.portfolio_backtest_summary && data.portfolio_backtest_summary.success) {
      const ps = data.portfolio_backtest_summary;
      if (els.quantPortfolioSummary) {
        els.quantPortfolioSummary.textContent = `${summary} · 累计 ${ps.total_return_pct ?? "—"}%`;
      }
      q.renderMetricCards(els.quantBtMetrics, [
        { label: "累计收益", value: fmtPct(ps.total_return_pct), cls: metricClass(ps.total_return_pct) },
        { label: "胜率", value: fmtPct(ps.win_rate_pct) },
        { label: "交易次数", value: escapeHtml(String(ps.trade_count ?? "—")) },
        { label: "来源", value: "Agent" },
      ]);
      applyReplayOverviewKpis(ps, { source: "Agent" });
      backtest.paintPortfolioChart(ps.equity_curve_tail, "无摘要曲线");
      return;
    }
    await openQuantDialog();
  };

  if (document.body.dataset.page === "quant") {
    const auto = new URLSearchParams(location.search).get("auto");
    openQuantDialog({
      autoBacktest: auto === "backtest",
    });
  }

  // W3：键盘链路自动化（可从全局快捷键触发）
  if (document.body.dataset.page === "replay") {
    const sp = new URLSearchParams(location.search);
    const auto = sp.get("auto_param_grid_chain");
    if (auto === "1") {
      const meta = document.getElementById("param-grid-meta");
      if (meta) meta.textContent = "键盘链路：自动跑网格中…";
      setTimeout(() => {
        backtest.runParamGrid()
          .then(() => {
            if (meta) meta.textContent = (meta.textContent || "") + " · 键盘链路完成（留在本页看热力）";
            try {
              const url = new URL(window.location.href);
              url.searchParams.delete("auto_param_grid_chain");
              history.replaceState({}, "", `${url.pathname}${url.search}#param-grid`);
            } catch (_) {
              /* ignore */
            }
          })
          .catch((err) => {
            if (meta) meta.textContent = String(err.message || err);
      });
      }, 30);
    } else if (typeof backtest.resumeParamGridJobIfAny === "function") {
      backtest.resumeParamGridJobIfAny().catch((err) => {
        const meta = document.getElementById("param-grid-meta");
        if (meta && err) meta.textContent = String(err.message || err);
      });
    }
  }}
