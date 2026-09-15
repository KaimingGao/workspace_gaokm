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
import { loadAndPaintMacroStrip } from "./macro_context_ui.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart } from "./lw_charts.js";
import { mountVirtualTable, colStyle } from "./virtual_table.js";
import { createScoreTooltipController } from "./score_tooltip.js?v=p2404";
import { fmtScore, scoreCls } from "./paper/fmt.js?v=p2389";
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
import { createFactorMetaCache } from "./quant/factor_meta.js";
import { buildUniversePanelHtml } from "./quant/universe_ui.js";
import { researchGridHtml, metricCell } from "./quant/research_grid.js";
import { createBtTablesUi } from "./quant/bt_tables.js?v=p2261";
import { installClusterProbe } from "./quant/domain_cluster.js";
import { installSuggest } from "./quant/domain_suggest.js";
import { installExportInterpret } from "./quant/domain_export.js";
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
const { installStrategy } = await import(
  `./quant/domain_strategy.js?v=${encodeURIComponent(_QV)}`
);
const { installScoreReview } = await import(
  `./quant/domain_score_review.js?v=${encodeURIComponent(_QV)}`
);
const { installClusterBarsUi } = await import(
  `./quant/cluster_bars_ui.js?v=${encodeURIComponent(_QV)}`
);
const { installClusterMinuteUi } = await import(
  `./quant/cluster_minute_ui.js?v=${encodeURIComponent(_QV)}`
);
const { createFactorIcUi } = await import(
  `./quant/factor_ic_ui.js?v=${encodeURIComponent(_QV)}`
);
const { treeReportHtml } = await import(
  `./quant/tree_report.js?v=${encodeURIComponent(_QV)}`
);

/** Quant research panel — shell + domain installs.
 * A4: 禁止再往根文件堆域逻辑；新能力进 quant/domain_* 或子模块，按页懒加载。
 */
export function initQuant(ctx) {
  void import(`./quant/fit_tier_ui.js?v=${encodeURIComponent(_QV)}`).then((m) =>
    m.ensureFitTierMap()
  );
  const researchParams = createResearchParams();
  const {
    clampHorizonDays,
    syncHorizonInputs,
    readHorizonDays,
    readRidgeLambda,
    readClusterK,
    readWatchingLimit,
    readHoldoutTradingDays,
    hydrateHoldoutTradingDays,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  } = researchParams;
  hydrateHoldoutTradingDays();
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
            "口径：每个交易日 09:30 rank_lots（本金默认 20 万 · rank=w_oo·ŷ_oo+w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1) · 100/200 股）。" +
    "净值起点 100；成本按纸面成本模型。有效≠正确：先看超额/回撤，再解读累计收益。";
  const BT_SCOPE_FROZEN =
    "以下为 quant_daily 冻结摘要，不是刚才点的回测；点「跑回测」刷新当次结果。";
  const quantBtBusyIds = [
    "quant-portfolio-run",
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
    paper_cross_section_rebalance: "纸面调仓",
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
    quantCrossSummary: document.getElementById("quant-cross-summary"),
    quantCrossList: document.getElementById("quant-cross-list"),
    quantFactorList: document.getElementById("quant-factor-list"),
    quantOlsHealth: document.getElementById("quant-ols-health"),
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
    quantT0Metrics: document.getElementById("paper-t0-metrics"),
    quantT0Viz: document.getElementById("paper-t0-viz"),
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
    readWatchingLimit, readHoldoutTradingDays, setPrefsHorizonDays, getPrefsHorizonDays,
    factorMetaByName, factorMetaByLabel, rememberFactorMeta, ensureFactorMeta,
    factorDescription, factorNameCellHtml, factorTaxonomyCellHtml,
    BT_SCOPE_LIVE, BT_SCOPE_FROZEN, quantBtBusyIds, PRESET_FLAG_LABELS, QUANT_EXPORT_PRESETS,
    setQuantMeta, setBusyText, watchingScoreTips, btSimScoreTips,
    buildUniversePanelHtml, buildResearchCurves,
    truncateStockName, watchingNameSpanHtml, watchingNameFromEl, applyWatchingNameEl, normalizeProbeCode,
    fmtScore, scoreCls, mountVirtualTable, colStyle,
    renderLineChart, renderDualLineChart, renderMultiLineChart,
    buildPortfolioBacktestSummaryText, buildPortfolioBacktestFailText,
    buildBtScoreFloorPayload, defaultScoringFloors, mergeScoringFloors,
    attachReadmeLinkHandler, renderReadmeLinksHtml,
  };

  Object.assign(q, createBtResultRenderers({ escapeHtml, fmtPct, metricClass }));
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
  const clusterBars = installClusterBarsUi(q);
  const clusterMinute = installClusterMinuteUi(q);
  const strategy = installStrategy(q);
  const exportDomain = installExportInterpret(q);
  const scoreReviewDomain = installScoreReview(q);
  q.watching = watching;
  q.backtest = backtest;
  q.cluster = cluster;
  q.suggest = suggest;
  q.clusterBars = clusterBars;
  q.clusterMinute = clusterMinute;
  q.strategy = strategy;
  q.exportDomain = exportDomain;
  q.scoreReviewDomain = scoreReviewDomain;

  async function renderRemCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-tau-coef-table");
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

  function tauOpenBucket(oos) {
    if (!oos || typeof oos !== "object") return null;
    const b = oos.by_tau && oos.by_tau["09:30"];
    return b && typeof b === "object" ? b : null;
  }

  function syncOverviewTauFromOos(oos, extraSub) {
    const extra = extraSub != null && extraSub !== "" ? String(extraSub) : "";
    const open = tauOpenBucket(oos);
    if (open && open.sign_hit != null && Number.isFinite(Number(open.sign_hit))) {
      const mix =
        oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))
          ? ` · 混合 ${fmtRemHit(oos.sign_hit)}`
          : "";
      syncOverviewTau(
        open.sign_hit,
        [`开盘 09:30${mix}`, extra].filter(Boolean).join(" · "),
        "hit"
      );
      return;
    }
    if (oos && oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))) {
      syncOverviewTau(oos.sign_hit, extra || "ŷ_oc 命中 · open→收", "hit");
      return;
    }
    if (oos && oos.ic != null && Number.isFinite(Number(oos.ic))) {
      syncOverviewTau(oos.ic, extra || "ŷ_oc IC", "ic");
    }
  }

  /**
   * ŷ_oc 卡头状态：chip + 可选文案 + OOS/n/时间 meta。
   * @param {HTMLElement|null} el
   * @param {{
   *   state?: "idle"|"busy"|"ok"|"warn"|"error",
   *   chip?: string,
   *   message?: string,
   *   oos?: object|null,
   *   sampleCount?: number|null,
   *   fittedAt?: string|null,
   *   promotedAt?: string|null,
   *   liveOn?: boolean|null,
   *   researchOn?: boolean|null,
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
      fittedAt = null,
      promotedAt = null,
      liveOn = null,
      researchOn = null,
      busy = false,
      error = false,
    } = opts;
    const chipState = error ? "error" : busy ? "busy" : state;
    const metas = [];
    if (oos && typeof oos === "object") {
      if (oos.ic != null && Number.isFinite(Number(oos.ic))) {
        metas.push(remStatusMeta("OOS IC", fmtRemIc(oos.ic), "时间切分样本外 IC"));
      }
      const openTau = tauOpenBucket(oos);
      if (openTau && openTau.sign_hit != null && Number.isFinite(Number(openTau.sign_hit))) {
        metas.push(
          remStatusMeta(
            "开盘",
            fmtRemHit(openTau.sign_hit),
            "09:30 无分钟前缀 · 与旧 open 口径可比；后面的钟会被开→τ 已实现垫高"
          )
        );
      }
      const pooledHit =
        oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))
          ? oos.sign_hit
          : oos.sign_hit_rate != null && Number.isFinite(Number(oos.sign_hit_rate))
            ? oos.sign_hit_rate
            : null;
      if (pooledHit != null) {
        const hasGrid = !!(openTau && openTau.sign_hit != null);
        metas.push(
          remStatusMeta(
            hasGrid ? "混合" : "命中",
            fmtRemHit(pooledHit),
            hasGrid
              ? "09:30–11:00 全网格混合方向命中（含已实现开→τ，偏高）"
              : "样本外方向命中率"
          )
        );
      } else if (oos.median_hit != null && Number.isFinite(Number(oos.median_hit))) {
        metas.push(
          remStatusMeta("中位命中", fmtRemHit(oos.median_hit), "ŷ 与 y 是否同在中位以上；≈50% 即无排序信息")
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
      const buckets = oos.buckets || {};
      const b04 = buckets.abs_ge_0_4 || {};
      const b06 = buckets.abs_ge_0_6 || {};
      if (b04.sign_hit != null && Number.isFinite(Number(b04.sign_hit))) {
        metas.push(
          remStatusMeta(
            "|ŷ|≥0.4",
            fmtRemHit(b04.sign_hit),
            `强于 enter 桶同号 · n=${b04.n ?? "—"}`
          )
        );
      }
      if (b06.sign_hit != null && Number.isFinite(Number(b06.sign_hit))) {
        metas.push(
          remStatusMeta(
            "|ŷ|≥0.6",
            fmtRemHit(b06.sign_hit),
            `强信号桶同号 · n=${b06.n ?? "—"}`
          )
        );
      }
      const tc = oos.theme_counts || {};
      const tcAll = tc.all || tc.oos || {};
      if (tcAll.n_theme != null) {
        metas.push(
          remStatusMeta(
            "主题n",
            String(tcAll.n_theme),
            `theme_day=1 样本 · 率 ${fmtRemHit(tcAll.theme_rate)}`
          )
        );
      }
    }
    if (sampleCount != null && sampleCount !== "") {
      metas.push(remStatusMeta("n", fmtRemN(sampleCount), "面板观测数（Holdout 不改此数）"));
    }
    if (oos && typeof oos === "object") {
      if (oos.holdout_trading_days != null && Number.isFinite(Number(oos.holdout_trading_days))) {
        metas.push(
          remStatusMeta(
            "Holdout",
            `${Number(oos.holdout_trading_days)}日`,
            "近 N 个交易日不进研究套训练"
          )
        );
      }
      if (oos.n_train != null && Number.isFinite(Number(oos.n_train))) {
        metas.push(remStatusMeta("训", fmtRemN(oos.n_train), "研究套训练集行数"));
      }
      if (oos.n_test != null && Number.isFinite(Number(oos.n_test))) {
        metas.push(
          remStatusMeta(
            "测",
            fmtRemN(oos.n_test),
            "Holdout 样本外行数（近 N 日，ŷ_oc 含多 τ）"
          )
        );
      }
    }
    const fitTs = fittedAt || promotedAt;
    if (fitTs) {
      metas.push(
        remStatusMeta(
          "拟合",
          fmtRemTs(fitTs),
          `拟合时间 ${String(fitTs)}`
        )
      );
    }
    if (liveOn != null || researchOn != null) {
      metas.push(
        remStatusMeta(
          "研究",
          researchOn ? "已启用" : "未写盘",
          "研究套 sidecar（*_research.json），供历史回测"
        )
      );
      metas.push(
        remStatusMeta(
          "执行",
          liveOn ? "已启用" : "未写盘",
          "执行套 live 模型，供交易执行"
        )
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

  function ridgeDiskFlags(data, opts = {}) {
    const src = data && typeof data === "object" ? data : {};
    let liveOn = src.live_model_present;
    let researchOn = src.research_exists;
    if (opts.persistOk) {
      if (liveOn == null && opts.persistRole === "live") liveOn = true;
      if (researchOn == null && opts.persistRole === "research") researchOn = true;
    }
    if (liveOn == null) liveOn = !!(src.exists && !src.shadow);
    return { liveOn: !!liveOn, researchOn: !!researchOn };
  }

  function ridgeEnableChip({
    liveOn,
    researchOn,
    fitted,
    justFitted,
    shadow,
    persistOk,
    persistRole,
  }) {
    if (justFitted) return { chip: "已拟合", state: "ok" };
    if (persistOk && persistRole === "research") {
      return { chip: "仅研究", state: "ok" };
    }
    if (persistOk && persistRole === "live") {
      return researchOn
        ? { chip: "研究+执行", state: "ok" }
        : { chip: "仅执行", state: "warn" };
    }
    if (liveOn && researchOn) return { chip: "研究+执行", state: "ok" };
    if (researchOn) return { chip: "仅研究", state: "ok" };
    if (liveOn) return { chip: "仅执行", state: "warn" };
    if (shadow || fitted) return { chip: "已拟合", state: "ok" };
    return { chip: "未启用", state: "idle" };
  }

  function paintRidgeEnableStatus(el, data, extra = {}) {
    const src = data && typeof data === "object" ? data : {};
    const flags = ridgeDiskFlags(src, extra);
    const fitted = !!(
      extra.justFitted ||
      extra.fitted ||
      src.last_report_exists ||
      (src.exists && src.shadow) ||
      (src.exists && !flags.liveOn && !flags.researchOn)
    );
    const painted = ridgeEnableChip({
      ...flags,
      fitted,
      justFitted: !!extra.justFitted,
      shadow: !!src.shadow,
      persistOk: !!extra.persistOk,
      persistRole: extra.persistRole || "",
    });
    const idleFallback =
      extra.idleMessage ||
      (src.last_report_exists
        ? "有 last report · 点「拟合」或「启用研究 / 启用执行」"
        : src.note || "");
    let message = extra.message;
    if (message === undefined) {
      if (src.shadow) {
        const gate = extra.gate || src.promote_gate;
        const liveNote =
          src.live_tau != null && src.live_tau !== ""
            ? ` · live 仍为 ${src.live_tau}`
            : "";
        message =
          gate && !gate.ok
            ? `last report · 闸：${(gate.blockers || []).join("；")}（可强制启用）${liveNote}`
            : `last report · 过门后点「启用研究 / 启用执行」${liveNote}`;
      } else if (painted.chip === "未启用" || painted.chip === "已拟合") {
        message = idleFallback;
      } else {
        message = "";
      }
    }
    renderRemStatus(el, {
      state: extra.state || painted.state,
      chip: extra.chip || painted.chip,
      message,
      oos: extra.oos !== undefined ? extra.oos : src.oos,
      sampleCount:
        extra.sampleCount !== undefined ? extra.sampleCount : src.sample_count,
      fittedAt:
        extra.fittedAt !== undefined
          ? extra.fittedAt
          : src.fitted_at || src.promoted_at || src.research_promoted_at,
      promotedAt:
        extra.promotedAt !== undefined ? extra.promotedAt : src.promoted_at,
      liveOn: flags.liveOn,
      researchOn: flags.researchOn,
      busy: extra.busy,
      error: extra.error,
    });
    return painted;
  }

  function clearRemResultBox() {
    const box = document.getElementById("quant-tau-result");
    if (box) box.innerHTML = "";
  }

  async function renderOnCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-on-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "on" })
        : "";
    host.innerHTML = html || "";
  }

  function clearOnResultBox() {
    const box = document.getElementById("quant-on-result");
    if (box) box.innerHTML = "";
  }

  async function renderPathCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-path-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "path" })
        : "";
    host.innerHTML = html || "";
  }


  async function renderCxCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-cx-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "cx" })
        : "";
    host.innerHTML = html || "";
  }

  async function renderTpdCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-tpd-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "tpd" })
        : "";
    host.innerHTML = html || "";
  }

  async function renderRCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-r-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "r" })
        : "";
    host.innerHTML = html || "";
  }

  async function renderT30CoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-t30-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "t30" })
        : "";
    host.innerHTML = html || "";
  }

  async function renderT60CoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-t60-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "t60" })
        : "";
    host.innerHTML = html || "";
  }
  async function renderT90CoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-t90-coef-table");
    if (!host) return;
    try {
      if (typeof q.ensureFactorMeta === "function") {
        await q.ensureFactorMeta();
      }
    } catch (_) {
      /* ignore */
    }
    const html =
      typeof q.remCoefTableHtml === "function"
        ? q.remCoefTableHtml(rm, { ...opts, head: "t90" })
        : "";
    host.innerHTML = html || "";
  }

  function clearPathResultBox() {
    const box = document.getElementById("quant-path-result");
    if (box) box.innerHTML = "";
  }

  function ridgeFitNBits(data) {
    const oos = (data && data.oos) || {};
    const rm = (data && data.return_model) || {};
    const bits = [];
    if (data && data.sample_count != null) bits.push(`面板 n=${data.sample_count}`);
    if (rm.n_obs != null) bits.push(`入模 n=${rm.n_obs}`);
    const holdN = oos.holdout_trading_days;
    if (holdN != null && Number.isFinite(Number(holdN))) {
      bits.push(`Holdout ${Number(holdN)}日`);
    }
    if (oos.n_train != null) bits.push(`训 ${fmtRemN(oos.n_train)}`);
    if (oos.n_test != null) bits.push(`测 ${fmtRemN(oos.n_test)}`);
    const byTau = oos.by_tau || {};
    const nd =
      (byTau["09:30"] && byTau["09:30"].n) ||
      (byTau["10:30"] && byTau["10:30"].n) ||
      null;
    if (oos.ic != null) {
      const nRow = oos.n_valid ?? oos.n_test ?? "—";
      bits.push(
        nd != null
          ? `OOS IC ${Number(oos.ic).toFixed(3)}（${nRow} 行 / ${nd} 票×日）`
          : `OOS IC ${Number(oos.ic).toFixed(3)}（n=${nRow}）`
      );
    }
    return bits;
  }

  let tauTreeBusyTimer = null;

  function fmtTauTreeSec(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) return "—";
    if (n < 60) return `${n >= 10 ? Math.round(n) : n.toFixed(1)}s`;
    const m = Math.floor(n / 60);
    const s = Math.round(n % 60);
    return `${m}分${String(s).padStart(2, "0")}秒`;
  }

  function tauTreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 15) return "拉观察池行情";
    if (s < 90) return "组 open→close 面板（满池分钟特征）";
    return "仍在组面板（XGBoost 约数秒；慢的是组样本）";
  }

  function fmtTauTreeTiming(data) {
    const t = data && data.timing;
    if (!t || typeof t !== "object") return "";
    const total = t.total_s != null ? t.total_s : t.fit_s;
    const parts = [];
    if (total != null && Number.isFinite(Number(total))) {
      parts.push(`用时 ${fmtTauTreeSec(total)}`);
    }
    const segs = [];
    if (t.bars_s != null) segs.push(`行情 ${fmtTauTreeSec(t.bars_s)}`);
    if (t.panel_s != null) segs.push(`面板 ${fmtTauTreeSec(t.panel_s)}`);
    if (t.tree_s != null) segs.push(`树 ${fmtTauTreeSec(t.tree_s)}`);
    if (t.ridge_s != null) segs.push(`Ridge ${fmtTauTreeSec(t.ridge_s)}`);
    if (segs.length) parts.push(segs.join(" / "));
    return parts.join(" · ");
  }

  function tauTreeHyperBits(data) {
    const hyper = (data && data.hyperparams) || {};
    const n =
      hyper.n_estimators != null && Number.isFinite(Number(hyper.n_estimators))
        ? Number(hyper.n_estimators)
        : 80;
    const depth =
      hyper.max_depth != null && Number.isFinite(Number(hyper.max_depth))
        ? Number(hyper.max_depth)
        : 3;
    const engine =
      String((data && data.backend) || "").toLowerCase() === "xgboost"
        ? "XGBoost"
        : String((data && data.backend) || "").toLowerCase() === "numpy_gbm"
          ? "numpy GBM"
          : "";
    return [engine, `${n} 棵 · 深度 ${depth}`].filter(Boolean).join(" · ");
  }

  function stopTauTreeBusy() {
    if (tauTreeBusyTimer) {
      clearInterval(tauTreeBusyTimer);
      tauTreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-tau-tree-run");
    if (btn) btn.disabled = false;
  }

  function startTauTreeBusy() {
    stopTauTreeBusy();
    const sum = document.getElementById("quant-tau-tree-summary");
    const btn = document.getElementById("quant-tau-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${tauTreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderTauTreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    tauTreeBusyTimer = setInterval(tick, 1000);
  }

  function renderTauTreeCompare(data) {
    const box = document.getElementById("quant-tau-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_oc_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_oc_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "tau" });
  }

  async function runTauTree() {
    const sum = document.getElementById("quant-tau-tree-summary");
    startTauTreeBusy();
    try {
      const tauLimit = 200;
      const res = await fetch("/api/quant/tau-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: tauLimit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopTauTreeBusy();
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        renderTauTreeCompare({ success: false, error: String(err) });
        return;
      }
      const timingMsg = fmtTauTreeTiming(data);
      renderRemStatus(sum, {
        state: "ok",
        chip: "已拟合",
        message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
          .filter(Boolean)
          .join(" · "),
        oos: data.oos || {},
        sampleCount: data.sample_count,
      });
      renderTauTreeCompare(data);
    } finally {
      stopTauTreeBusy();
    }
  }

  async function loadTauTreeLast() {
    const sum = document.getElementById("quant-tau-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次影子对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/tau-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_oc_tree",
      });
      renderTauTreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    const timingMsg = fmtTauTreeTiming(data);
    renderRemStatus(sum, {
      state: "ok",
      chip: "上次",
      message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
        .filter(Boolean)
        .join(" · "),
      oos: data.oos || {},
      sampleCount: data.sample_count,
    });
    renderTauTreeCompare(data);
  }

  let rTreeBusyTimer = null;

  function rTreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 close/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（XGBoost 约数秒；慢的是组样本）";
  }

  function stopRTreeBusy() {
    if (rTreeBusyTimer) {
      clearInterval(rTreeBusyTimer);
      rTreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-r-tree-run");
    if (btn) btn.disabled = false;
  }

  function startRTreeBusy() {
    stopRTreeBusy();
    const sum = document.getElementById("quant-r-tree-summary");
    const btn = document.getElementById("quant-r-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${rTreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderRTreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    rTreeBusyTimer = setInterval(tick, 1000);
  }

  function renderRTreeCompare(data) {
    const box = document.getElementById("quant-r-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_τc_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_τc_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "r" });
  }

  async function runRTree() {
    const sum = document.getElementById("quant-r-tree-summary");
    startRTreeBusy();
    try {
      const rLimit = 200;
      const res = await fetch("/api/quant/r-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: rLimit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopRTreeBusy();
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        renderRTreeCompare({ success: false, error: String(err) });
        return;
      }
      const timingMsg = fmtTauTreeTiming(data);
      renderRemStatus(sum, {
        state: "ok",
        chip: "已拟合",
        message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
          .filter(Boolean)
          .join(" · "),
        oos: data.oos || {},
        sampleCount: data.sample_count,
      });
      renderRTreeCompare(data);
    } finally {
      stopRTreeBusy();
    }
  }

  async function loadRTreeLast() {
    const sum = document.getElementById("quant-r-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次影子对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/r-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_τc_tree",
      });
      renderRTreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    const timingMsg = fmtTauTreeTiming(data);
    renderRemStatus(sum, {
      state: "ok",
      chip: "上次",
      message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
        .filter(Boolean)
        .join(" · "),
      oos: data.oos || {},
      sampleCount: data.sample_count,
    });
    renderRTreeCompare(data);
  }

  let t30TreeBusyTimer = null;

  function t30TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 price(τ⊕30m)/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（XGBoost 约数秒；慢的是组样本）";
  }

  function stopT30TreeBusy() {
    if (t30TreeBusyTimer) {
      clearInterval(t30TreeBusyTimer);
      t30TreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-t30-tree-run");
    if (btn) btn.disabled = false;
  }

  function startT30TreeBusy() {
    stopT30TreeBusy();
    const sum = document.getElementById("quant-t30-tree-summary");
    const btn = document.getElementById("quant-t30-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${t30TreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderT30TreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    t30TreeBusyTimer = setInterval(tick, 1000);
  }

  function renderT30TreeCompare(data) {
    const box = document.getElementById("quant-t30-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_τ30_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_τ30_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "t30" });
  }

  async function runT30Tree() {
    const sum = document.getElementById("quant-t30-tree-summary");
    startT30TreeBusy();
    try {
      const t30Limit = 200;
      const res = await fetch("/api/quant/t30-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: t30Limit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopT30TreeBusy();
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        renderT30TreeCompare({ success: false, error: String(err) });
        return;
      }
      const timingMsg = fmtTauTreeTiming(data);
      renderRemStatus(sum, {
        state: "ok",
        chip: "已拟合",
        message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
          .filter(Boolean)
          .join(" · "),
        oos: data.oos || {},
        sampleCount: data.sample_count,
      });
      renderT30TreeCompare(data);
    } finally {
      stopT30TreeBusy();
    }
  }

  async function loadT30TreeLast() {
    const sum = document.getElementById("quant-t30-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次影子对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/t30-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_τ30_tree",
      });
      renderT30TreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    const timingMsg = fmtTauTreeTiming(data);
    renderRemStatus(sum, {
      state: "ok",
      chip: "上次",
      message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
        .filter(Boolean)
        .join(" · "),
      oos: data.oos || {},
      sampleCount: data.sample_count,
    });
    renderT30TreeCompare(data);
  }

  let t60TreeBusyTimer = null;

  function t60TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 price(τ⊕60m)/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（XGBoost 约数秒；慢的是组样本）";
  }

  function stopT60TreeBusy() {
    if (t60TreeBusyTimer) {
      clearInterval(t60TreeBusyTimer);
      t60TreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-t60-tree-run");
    if (btn) btn.disabled = false;
  }

  function startT60TreeBusy() {
    stopT60TreeBusy();
    const sum = document.getElementById("quant-t60-tree-summary");
    const btn = document.getElementById("quant-t60-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${t60TreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderT60TreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    t60TreeBusyTimer = setInterval(tick, 1000);
  }

  function renderT60TreeCompare(data) {
    const box = document.getElementById("quant-t60-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_τ60_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_τ60_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "t60" });
  }

  async function runT60Tree() {
    const sum = document.getElementById("quant-t60-tree-summary");
    startT60TreeBusy();
    try {
      const t60Limit = 200;
      const res = await fetch("/api/quant/t60-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: t60Limit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopT60TreeBusy();
      if (!res.ok || !data.success) {
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(data.error || data.note || data.detail || `HTTP ${res.status}`),
          error: true,
        });
        renderT60TreeCompare({
          success: false,
          error: data.error || data.note || `HTTP ${res.status}`,
        });
        return;
      }
      const timingMsg = fmtTauTreeTiming(data);
      renderRemStatus(sum, {
        state: "ok",
        chip: "已拟合",
        message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
          .filter(Boolean)
          .join(" · "),
        oos: data.oos || {},
        sampleCount: data.sample_count,
      });
      renderT60TreeCompare(data);
    } finally {
      stopT60TreeBusy();
    }
  }

  async function loadT60TreeLast() {
    const sum = document.getElementById("quant-t60-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次影子对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/t60-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_τ60_tree",
      });
      renderT60TreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    const timingMsg = fmtTauTreeTiming(data);
    renderRemStatus(sum, {
      state: "ok",
      chip: "上次",
      message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
        .filter(Boolean)
        .join(" · "),
      oos: data.oos || {},
      sampleCount: data.sample_count,
    });
    renderT60TreeCompare(data);
  }
  let t90TreeBusyTimer = null;

  function t90TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 price(τ⊕90m)/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（XGBoost 约数秒；慢的是组样本）";
  }

  function stopT90TreeBusy() {
    if (t90TreeBusyTimer) {
      clearInterval(t90TreeBusyTimer);
      t90TreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-t90-tree-run");
    if (btn) btn.disabled = false;
  }

  function startT90TreeBusy() {
    stopT90TreeBusy();
    const sum = document.getElementById("quant-t90-tree-summary");
    const btn = document.getElementById("quant-t90-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${t90TreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderT90TreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    t90TreeBusyTimer = setInterval(tick, 1000);
  }

  function renderT90TreeCompare(data) {
    const box = document.getElementById("quant-t90-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_τ90_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_τ90_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "t90" });
  }

  async function runT90Tree() {
    const sum = document.getElementById("quant-t90-tree-summary");
    startT90TreeBusy();
    try {
      const t90Limit = 200;
      const res = await fetch("/api/quant/t90-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: t90Limit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopT90TreeBusy();
      if (!res.ok || !data.success) {
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(data.error || data.note || data.detail || `HTTP ${res.status}`),
          error: true,
        });
        renderT90TreeCompare({
          success: false,
          error: data.error || data.note || `HTTP ${res.status}`,
        });
        return;
      }
      const timingMsg = fmtTauTreeTiming(data);
      renderRemStatus(sum, {
        state: "ok",
        chip: "已拟合",
        message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
          .filter(Boolean)
          .join(" · "),
        oos: data.oos || {},
        sampleCount: data.sample_count,
      });
      renderT90TreeCompare(data);
    } finally {
      stopT90TreeBusy();
    }
  }

  async function loadT90TreeLast() {
    const sum = document.getElementById("quant-t90-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次影子对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/t90-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_τ90_tree",
      });
      renderT90TreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    const timingMsg = fmtTauTreeTiming(data);
    renderRemStatus(sum, {
      state: "ok",
      chip: "上次",
      message: [tauTreeHyperBits(data), timingMsg, "影子头 · 未写盘"]
        .filter(Boolean)
        .join(" · "),
      oos: data.oos || {},
      sampleCount: data.sample_count,
    });
    renderT90TreeCompare(data);
  }

  function renderPathFitSummary(data) {
    const box = document.getElementById("quant-path-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const buckets = oos.buckets || {};
    const b15 = buckets.abs_ge_15 || {};
    const b30 = buckets.abs_ge_30 || {};
    const gate = data.promote_gate || {};
    const fill = oos.feature_fill || {};
    const fillKeys = fill.keys || {};
    const ycloseFill = fillKeys.yclose_loc || {};
    const touch = oos.label_touch || {};
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      data.minute_days_total != null ? `分钟日 ${data.minute_days_total}` : null,
      data.minute_lookback_days != null
        ? `回看 ${data.minute_lookback_days}d`
        : null,
      data.sell_trig_pct != null && data.buy_trig_pct != null
        ? `触发 卖${data.sell_trig_pct}% / 买${data.buy_trig_pct}%`
        : null,
      ...ridgeFitNBits(data),
      touch.n_labeled != null
        ? `触达标签 ${touch.n_labeled}（+${touch.n_pos ?? 0}/−${touch.n_neg ?? 0}）`
        : null,
      ycloseFill.rate != null
        ? `yclose非空 ${(Number(ycloseFill.rate) * 100).toFixed(0)}%`
        : null,
      oos.sign_hit != null
        ? `OOS 命中 ${(Number(oos.sign_hit) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
        : null,
      b15.sign_hit != null
        ? `|ŷ|≥15 ${(Number(b15.sign_hit) * 100).toFixed(0)}%（n=${b15.n ?? 0}）`
        : null,
      b30.sign_hit != null
        ? `|ŷ|≥30 ${(Number(b30.sign_hit) * 100).toFixed(0)}%（n=${b30.n ?? 0}）`
        : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过（强桶）"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }


  function renderCxFitSummary(data) {
    const box = document.getElementById("quant-cx-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const gate = data.promote_gate || {};
    const dist = oos.label_dist || {};
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      dist.y_mean != null ? `标签均 ${Number(dist.y_mean).toFixed(3)}` : null,
      oos.median_hit != null
        ? `中位命中 ${(Number(oos.median_hit) * 100).toFixed(1)}%`
        : null,
      oos.mae != null ? `MAE ${Number(oos.mae).toFixed(2)}` : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  function renderTpdFitSummary(data) {
    const box = document.getElementById("quant-tpd-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const gate = data.promote_gate || {};
    const dist = oos.label_dist || {};
    const rho = oos.spearman_y_complexity_y_tpd;
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      dist.y_mean != null ? `标签均 ${Number(dist.y_mean).toFixed(3)}` : null,
      oos.median_hit != null
        ? `中位命中 ${(Number(oos.median_hit) * 100).toFixed(1)}%`
        : null,
      oos.mae != null ? `MAE ${Number(oos.mae).toFixed(3)}` : null,
      rho != null && Number.isFinite(Number(rho))
        ? `ρ(y_cx,y_tpd) ${Number(rho).toFixed(3)}`
        : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  function renderRFitSummary(data) {
    const box = document.getElementById("quant-r-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const gate = data.promote_gate || {};
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      oos.sign_hit != null
        ? `OOS 命中 ${(Number(oos.sign_hit) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
        : null,
      oos.ic != null ? `OOS IC ${Number(oos.ic).toFixed(3)}` : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过（强桶）"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  function renderT30FitSummary(data) {
    const box = document.getElementById("quant-t30-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const gate = data.promote_gate || {};
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      oos.sign_hit != null
        ? `OOS 命中 ${(Number(oos.sign_hit) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
        : null,
      oos.ic != null ? `OOS IC ${Number(oos.ic).toFixed(3)}` : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过（强桶）"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  function renderT60FitSummary(data) {
    const box = document.getElementById("quant-t60-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const gate = data.promote_gate || {};
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      oos.sign_hit != null
        ? `OOS 命中 ${(Number(oos.sign_hit) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
        : null,
      oos.ic != null ? `OOS IC ${Number(oos.ic).toFixed(3)}` : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过（强桶）"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }
  function renderT90FitSummary(data) {
    const box = document.getElementById("quant-t90-result");
    if (!box || !data || typeof data !== "object") return;
    const oos = data.oos || {};
    const gate = data.promote_gate || {};
    const bits = [
      data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      oos.sign_hit != null
        ? `OOS 命中 ${(Number(oos.sign_hit) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
        : null,
      oos.ic != null ? `OOS IC ${Number(oos.ic).toFixed(3)}` : null,
      gate.ok === false
        ? `promote 闸：${(gate.blockers || []).join("；")}`
        : gate.ok === true
          ? "promote 闸：通过（强桶）"
          : null,
      Array.isArray(gate.warnings) && gate.warnings.length
        ? `软提示：${gate.warnings.join("；")}`
        : null,
    ].filter(Boolean);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  /** ŷ_oc 启用：不过闸也可点，确认后 force_promote。 */
  let _tauPromoteGate = null;
  function syncRidgePersistPair(liveId, researchId, gate, { hasReport = true } = {}) {
    const buttons = [
      document.getElementById(liveId),
      document.getElementById(researchId),
    ].filter(Boolean);
    buttons.forEach((persistBtn) => {
      const role = persistBtn.dataset.persistRole || "live";
      if (!hasReport) {
        persistBtn.disabled = true;
        persistBtn.title = "先拟合，再启用";
        persistBtn.classList.remove("is-gate-warn");
        return;
      }
      persistBtn.disabled = false;
      if (gate && gate.ok === false) {
        const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
        persistBtn.title = `未过闸：${blockers} · 点击可确认后强制启用${role === "research" ? "研究套" : "执行套"}`;
        persistBtn.classList.add("is-gate-warn");
      } else {
        persistBtn.title =
          role === "research"
            ? "写入 *_research.json，供历史回测"
            : `写入 live 模型，供交易执行`;
        persistBtn.classList.remove("is-gate-warn");
      }
    });
  }
  function syncTauPersistBtn(gate, { hasReport = true } = {}) {
    _tauPromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-tau-ridge-persist",
      "quant-tau-ridge-persist-research",
      _tauPromoteGate,
      { hasReport }
    );
  }

  /** path 启用：不过闸也可点，确认后 force_promote；无 report 才禁用。 */
  let _pathPromoteGate = null;
  function syncPathPersistBtn(gate, { hasReport = true } = {}) {
    _pathPromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-path-ridge-persist",
      "quant-path-ridge-persist-research",
      _pathPromoteGate,
      { hasReport }
    );
  }

  let _cxPromoteGate = null;
  function syncCxPersistBtn(gate, { hasReport = true } = {}) {
    _cxPromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-cx-ridge-persist",
      "quant-cx-ridge-persist-research",
      _cxPromoteGate,
      { hasReport }
    );
  }

  function clearCxResultBox() {
    const box = document.getElementById("quant-cx-result");
    if (box) box.innerHTML = "";
  }

  let _tpdPromoteGate = null;
  function syncTpdPersistBtn(gate, { hasReport = true } = {}) {
    _tpdPromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-tpd-ridge-persist",
      "quant-tpd-ridge-persist-research",
      _tpdPromoteGate,
      { hasReport }
    );
  }

  function clearTpdResultBox() {
    const box = document.getElementById("quant-tpd-result");
    if (box) box.innerHTML = "";
  }

  let _rPromoteGate = null;
  function syncRPersistBtn(gate, { hasReport = true } = {}) {
    _rPromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-r-ridge-persist",
      "quant-r-ridge-persist-research",
      _rPromoteGate,
      { hasReport }
    );
  }

  function clearRResultBox() {
    const box = document.getElementById("quant-r-result");
    if (box) box.innerHTML = "";
  }

  let _t30PromoteGate = null;
  function syncT30PersistBtn(gate, { hasReport = true } = {}) {
    _t30PromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-t30-ridge-persist",
      "quant-t30-ridge-persist-research",
      _t30PromoteGate,
      { hasReport }
    );
  }

  function clearT30ResultBox() {
    const box = document.getElementById("quant-t30-result");
    if (box) box.innerHTML = "";
  }

  let _t60PromoteGate = null;
  function syncT60PersistBtn(gate, { hasReport = true } = {}) {
    _t60PromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-t60-ridge-persist",
      "quant-t60-ridge-persist-research",
      _t60PromoteGate,
      { hasReport }
    );
  }

  function clearT60ResultBox() {
    const box = document.getElementById("quant-t60-result");
    if (box) box.innerHTML = "";
  }
  let _t90PromoteGate = null;
  function syncT90PersistBtn(gate, { hasReport = true } = {}) {
    _t90PromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-t90-ridge-persist",
      "quant-t90-ridge-persist-research",
      _t90PromoteGate,
      { hasReport }
    );
  }

  function clearT90ResultBox() {
    const box = document.getElementById("quant-t90-result");
    if (box) box.innerHTML = "";
  }

  // 轻量预填 ŷ_hl 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/path-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-path-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncPathPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearPathResultBox();
        await renderPathCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncPathPersistBtn(gate, { hasReport: true });
      clearPathResultBox();
      await renderPathCoefTable(data.return_model || {}, { oos });
    } catch (_) {
      /* ignore */
    }
  })();


  // 轻量预填 ŷ_cx 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/cx-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-cx-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncCxPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearCxResultBox();
        await renderCxCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncCxPersistBtn(gate, { hasReport: true });
      clearCxResultBox();
      await renderCxCoefTable(data.return_model || {}, { oos });
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_tpd 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/tpd-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-tpd-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncTpdPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearTpdResultBox();
        await renderTpdCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncTpdPersistBtn(gate, { hasReport: true });
      clearTpdResultBox();
      await renderTpdCoefTable(data.return_model || {}, { oos });
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_r 状态（枢纽 UI 已下线时跳过）
  void (async () => {
    try {
      if (!document.getElementById("quant-r-summary")) return;
      const res = await fetch("/api/quant/r-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-r-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncRPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearRResultBox();
        await renderRCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncRPersistBtn(gate, { hasReport: true });
      clearRResultBox();
      await renderRCoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_τ30 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/t30-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-t30-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncT30PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT30ResultBox();
        await renderT30CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT30PersistBtn(gate, { hasReport: true });
      clearT30ResultBox();
      await renderT30CoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_τ60 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/t60-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-t60-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncT60PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT60ResultBox();
        await renderT60CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT60PersistBtn(gate, { hasReport: true });
      clearT60ResultBox();
      await renderT60CoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (_) {
      /* ignore */
    }
  })();
  // 轻量预填 ŷ_τ90 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/t90-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-t90-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncT90PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT90ResultBox();
        await renderT90CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT90PersistBtn(gate, { hasReport: true });
      clearT90ResultBox();
      await renderT90CoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_oc KPI / 状态（不阻塞；概览用模型 OOS，不再被单日验收覆盖）
  void (async () => {
    try {
      const res = await fetch("/api/quant/tau-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-tau-summary");
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncTauPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearRemResultBox();
        await renderRemCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      const painted = paintRidgeEnableStatus(sum, data, { gate });
      const chip = painted.chip;
      syncTauPersistBtn(gate, { hasReport: true });
      clearRemResultBox();
      await renderRemCoefTable(data.return_model || {}, { oos });
      // 仅当概览仍空时写入（拟合/启用后再刷）
      const card = document.querySelector(
        '#quant-pro-overview-kpis .quant-pro-kpi-card[data-kpi="tau"]'
      );
      if (card && card.classList.contains("is-empty")) {
        syncOverviewTauFromOos(
          oos,
          data.shadow ? "已拟合" : painted.chip
        );
        if (
          !(tauOpenBucket(oos) && tauOpenBucket(oos).sign_hit != null) &&
          !(oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))) &&
          !(oos.ic != null && Number.isFinite(Number(oos.ic)))
        ) {
          syncOverviewTau(chip, data.promoted_at || "ŷ_oc", "text");
        }
      }
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_co 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/on-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-on-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        clearOnResultBox();
        await renderOnCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data);
      clearOnResultBox();
      await renderOnCoefTable(data.return_model || {}, { oos });
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
    const hasStrategy = !!document.getElementById("strategy-market-context");
    const hasWatching = !!document.getElementById("quant-watching-list") || !!document.getElementById("quant-watching-meta");
    const hasReplay = !!document.getElementById("quant-portfolio-run");
    const hasOps = !!document.getElementById("quant-ops-summary");
    try {
      if (els.quantMeta) els.quantMeta.textContent = "加载面板…";
      // 先拉研究默认 horizon（memory）与 OLS 标的列表，再跑 IC/OLS/回测
      await loadPrefsHorizon().catch(() => {});
      await cluster.populateOlsCodeOptions().catch(() => {});
      const foreground = [];
      const watchingP =
        hasWatching || hasReplay
          ? watching.loadWatchingPanel().catch((err) => {
              watching.setPoolMeta(String(err.message || err));
            })
          : Promise.resolve();
      if (hasWatching || hasReplay) {
        foreground.push(watchingP);
      }
      if (page === "replay") {
        foreground.push(
          watchingP
            .catch(() => {})
            .then(() => backtest.restoreLastPortfolioBacktest())
            .then(() => backtest.restoreLastT0Backtest?.())
            .catch(() => {})
        );
      }
      if (hasStrategy) {
        foreground.push(strategy.loadSignalConfigPanel().catch(() => {}));
        foreground.push(strategy.loadStrategyList().catch(() => {}));
      }
      const macroStrip = document.getElementById("quant-macro-context-strip");
      if (macroStrip) {
        foreground.push(
          loadAndPaintMacroStrip(macroStrip, apiFetch, escapeHtml).catch(() => {})
        );
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
      if (hasReplay && page !== "replay") {
        background.push(backtest.loadLastBacktestSnapshot().catch(() => {}));
      }
      // 研究枢纽：进页恢复上次分组；点「跑分组」才重算（当日首次自动强制日线，其后纯缓存）
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
  async function onGotoFollow(e) {
    e.preventDefault();
    try {
      await watching.gotoFollowTab();
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  }

  const btnQuant = document.getElementById("btn-quant");
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

  async function runTauRidge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-tau-summary");
    const persistBtn = document.getElementById("quant-tau-ridge-persist");
    const roleLabel = persistRole === "research" ? "研究套" : "执行套";
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? `强制写入上次拟合（${roleLabel}）…`
          : `写入上次拟合（${roleLabel}）…`
        : "Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      // 满观察池（与 ŷ_hl 一致）
      const tauLimit = 200;
      const res = await fetch("/api/quant/tau-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: tauLimit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui tau force promote ${persistRole}`
              : `ui tau promote ${persistRole}`
            : "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!res.ok || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearRemResultBox();
          renderRemCoefTable(null);
        }
        if (persistBtn && gate) {
          syncTauPersistBtn(gate, { hasReport: true });
        }
        _tauPromoteGate = gate;
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      const painted = paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      _tauPromoteGate = gate;
      syncTauPersistBtn(gate, { hasReport: true });
      syncOverviewTauFromOos(oos, painted.chip);
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

  async function runOnRidge({ persist = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-on-summary");
    const roleLabel = persistRole === "research" ? "研究套" : "执行套";
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist ? `写入上次拟合（${roleLabel}）…` : "ON Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      // 满观察池（与 ŷ_oc / ŷ_hl 一致）
      const onLimit = 200;
      const res = await fetch("/api/quant/on-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: onLimit,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          note: persist ? `ui on promote ${persistRole}` : "",
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
        clearOnResultBox();
        renderOnCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : "未写盘 · 过门后点「启用研究 / 启用执行」",
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      const rm = data.return_model || {};
      clearOnResultBox();
      await renderOnCoefTable(rm, { oos });
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

  async function runPathRidge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-path-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "path Ridge + 分钟标签…",
      busy: true,
    });
    try {
      // 满观察池；只读本地 5m 缓存（服务端不再拉远端）
      const pathLimit = 200;
      const res = await fetch("/api/quant/path-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: pathLimit,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui path force promote ${persistRole}`
              : `ui path promote ${persistRole}`
            : "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!res.ok || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearPathResultBox();
          renderPathCoefTable(null);
        }
        syncPathPersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncPathPersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderPathFitSummary(data);
      await renderPathCoefTable(rm, { oos });
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

  on("quant-path-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runPathRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-path-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-path-ridge-persist", "click", onPathPersistClick);
  on("quant-path-ridge-persist-research", "click", onPathPersistClick);
  async function onPathPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _pathPromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_hl 研究套（path_ridge_model_research.json，供历史回测）"
        : "ŷ_hl 执行套（path_ridge_model.json，供交易执行）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_hl 写入 ${target}？不改 ŷ_oo / ŷ_oc。`)) {
      return;
    }
    try {
      await runPathRidge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-path-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  }

  on("quant-path-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-path-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/path-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncPathPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearPathResultBox();
        await renderPathCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncPathPersistBtn(gate, { hasReport: true });
      clearPathResultBox();
      await renderPathCoefTable(data.return_model || {}, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });


  async function runCxRidge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-cx-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "cx Ridge + (1−D/L)…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/cx-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: 200,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui cx force promote ${persistRole}`
              : `ui cx promote ${persistRole}`
            : "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!res.ok || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearCxResultBox();
          renderCxCoefTable(null);
        }
        syncCxPersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncCxPersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderCxFitSummary(data);
      await renderCxCoefTable(rm, { oos });
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

  on("quant-cx-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runCxRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-cx-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-cx-ridge-persist", "click", onCxPersistClick);
  on("quant-cx-ridge-persist-research", "click", onCxPersistClick);
  async function onCxPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _cxPromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_cx 研究套（cx_ridge_model_research.json，供历史回测）"
        : "ŷ_cx 执行套（cx_ridge_model.json，供交易执行）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_cx 写入 ${target}？`)) {
      return;
    }
    try {
      await runCxRidge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-cx-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  }

  on("quant-cx-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-cx-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/cx-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncCxPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearCxResultBox();
        await renderCxCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncCxPersistBtn(gate, { hasReport: true });
      clearCxResultBox();
      await renderCxCoefTable(data.return_model || {}, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  async function runTpdRidge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-tpd-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "tpd Ridge + TPD…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/tpd-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: 200,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui tpd force promote ${persistRole}`
              : `ui tpd promote ${persistRole}`
            : "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!res.ok || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearTpdResultBox();
          renderTpdCoefTable(null);
        }
        syncTpdPersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncTpdPersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderTpdFitSummary(data);
      await renderTpdCoefTable(rm, { oos });
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

  on("quant-tpd-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runTpdRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-tpd-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-tpd-ridge-persist", "click", onTpdPersistClick);
  on("quant-tpd-ridge-persist-research", "click", onTpdPersistClick);
  async function onTpdPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _tpdPromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_tpd 研究套（tpd_ridge_model_research.json，供历史回测）"
        : "ŷ_tpd 执行套（tpd_ridge_model.json，供交易执行）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_tpd 写入 ${target}？`)) {
      return;
    }
    try {
      await runTpdRidge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-tpd-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  }

  on("quant-tpd-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-tpd-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/tpd-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncTpdPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearTpdResultBox();
        await renderTpdCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncTpdPersistBtn(gate, { hasReport: true });
      clearTpdResultBox();
      await renderTpdCoefTable(data.return_model || {}, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  function describeNetworkFetchError(err) {
    const msg = String((err && (err.message || err.error)) || err || "");
    if (/failed to fetch|networkerror|load failed|network request failed|fetch failed/i.test(msg)) {
      return "连接中断（拟合过长或服务重启）· 请再点拟合";
    }
    return msg;
  }

  function fmtRidgeJobSec(ms) {
    const s = Math.max(1, Math.round(Number(ms) / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}分${String(r).padStart(2, "0")}秒`;
  }

  function ridgeFitBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 8) return "排队 / 读分钟缓存";
    if (s < 40) return "组 τ 面板";
    return "Ridge + 时间 OOS（满池可能数分钟）";
  }

  async function pollRidgeJobUntilDone(jobName, { jobId, sumEl, label } = {}) {
    const pollStarted = Date.now();
    const absoluteCapMs = 40 * 60 * 1000;
    let netFailStreak = 0;
    let sawOwnJob = false;
    while (true) {
      if (Date.now() - pollStarted > absoluteCapMs) {
        throw new Error(
          `${label || "拟合"} 超时（>${Math.round(absoluteCapMs / 60000)} 分钟），请再点拟合`
        );
      }
      const { ok, data, status } = await apiFetch(`/api/jobs/${jobName}?progress=1`);
      if (!ok && (!status || status === 0 || status >= 500)) {
        netFailStreak += 1;
        renderRemStatus(sumEl, {
          state: "busy",
          chip: "拟合中",
          message: `${label || "拟合"}… ${fmtRidgeJobSec(Date.now() - pollStarted)} · 服务短暂断开，重连中（${netFailStreak}）…`,
          busy: true,
        });
        await new Promise((r) => setTimeout(r, Math.min(4000, 500 * netFailStreak)));
        continue;
      }
      netFailStreak = 0;
      const job = (data && data.job) || {};
      const sameJob = !jobId || !job.id || job.id === jobId;
      if (sameJob && job.id) sawOwnJob = true;
      if (job.status === "idle" || !job.id) {
        if (sawOwnJob || Date.now() - pollStarted > 2500) {
          throw new Error(`${label || "拟合"} 已中断（可能服务重启），请再点拟合`);
        }
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      if (!sameJob) {
        throw new Error(`${label || "拟合"} 已被其它任务覆盖，请重试`);
      }
      if (job.status === "failed") {
        throw new Error(job.error || `${label || "拟合"} 失败`);
      }
      if (job.status === "done") {
        const full = await apiFetch(`/api/jobs/${jobName}`);
        return ((full.data && full.data.job) || job);
      }
      const sec = Math.max(1, Math.round((Date.now() - pollStarted) / 1000));
      const hint = job.message || ridgeFitBusyHint(sec);
      renderRemStatus(sumEl, {
        state: "busy",
        chip: "拟合中",
        message: `${label || "拟合"}… ${fmtRidgeJobSec(Date.now() - pollStarted)} · ${hint}`,
        busy: true,
      });
      await new Promise((r) => setTimeout(r, 1200));
    }
  }

  async function awaitRidgeFitIfBackground(res, data, { persist, jobName, modelPath, sumEl, label }) {
    if (persist || !data || !data.background) {
      return { data, httpOk: res.ok };
    }
    const job = await pollRidgeJobUntilDone(jobName, {
      jobId: data.job && data.job.id,
      sumEl,
      label,
    });
    let next = job && job.result && typeof job.result === "object" ? { ...job.result } : {};
    if (!next.return_model) {
      const modelRes = await fetch(modelPath);
      const modelData = await modelRes.json().catch(() => ({}));
      if (modelData && typeof modelData === "object") {
        next = { ...modelData, ...next };
      }
    }
    return { data: next, httpOk: true };
  }

  async function runRRidge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-r-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τc Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/r-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: 200,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui r force promote ${persistRole}`
              : `ui r promote ${persistRole}`
            : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "r-ridge",
        modelPath: "/api/quant/r-ridge/model",
        sumEl: sum,
        label: "ŷ_τc",
      });
      const data = awaited.data || {};
      const httpOk = awaited.httpOk;
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!httpOk || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearRResultBox();
          renderRCoefTable(null);
        }
        syncRPersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncRPersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderRFitSummary(data);
      await renderRCoefTable(rm, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
      throw err;
    }
  }

  on("quant-r-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runRRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-r-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-r-ridge-persist", "click", onRPersistClick);
  on("quant-r-ridge-persist-research", "click", onRPersistClick);
  async function onRPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _rPromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_τc 研究套（r_ridge_model_research.json，供历史回测）"
        : "ŷ_τc 执行套（r_ridge_model.json；不改 ŷ_oo / ŷ_oc、不进调仓）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_τc 写入 ${target}？不改 ŷ_oo / ŷ_oc、不进调仓。`)) {
      return;
    }
    try {
      await runRRidge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-r-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  }

  on("quant-r-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-r-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/r-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncRPersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearRResultBox();
        await renderRCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncRPersistBtn(gate, { hasReport: true });
      clearRResultBox();
      await renderRCoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  async function runT30Ridge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-t30-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τ30 Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t30-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: 200,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui t30 force promote ${persistRole}`
              : `ui t30 promote ${persistRole}`
            : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "t30-ridge",
        modelPath: "/api/quant/t30-ridge/model",
        sumEl: sum,
        label: "ŷ_τ30",
      });
      const data = awaited.data || {};
      const httpOk = awaited.httpOk;
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!httpOk || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearT30ResultBox();
          renderT30CoefTable(null);
        }
        syncT30PersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncT30PersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderT30FitSummary(data);
      await renderT30CoefTable(rm, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
      throw err;
    }
  }

  on("quant-t30-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT30Ridge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-t30-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-t30-ridge-persist", "click", onT30PersistClick);
  on("quant-t30-ridge-persist-research", "click", onT30PersistClick);
  async function onT30PersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _t30PromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_τ30 研究套（t30_ridge_model_research.json）"
        : "ŷ_τ30 执行套（t30_ridge_model.json；做 T 旁路，不改 C_τ）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_τ30 写入 ${target}？不进 ranking、不改 C_τ。`)) {
      return;
    }
    try {
      await runT30Ridge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-t30-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  }

  on("quant-t30-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-t30-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t30-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncT30PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT30ResultBox();
        await renderT30CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncT30PersistBtn(gate, { hasReport: true });
      clearT30ResultBox();
      await renderT30CoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  async function runT60Ridge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-t60-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τ60 Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t60-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: 200,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui t60 force promote ${persistRole}`
              : `ui t60 promote ${persistRole}`
            : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "t60-ridge",
        modelPath: "/api/quant/t60-ridge/model",
        sumEl: sum,
        label: "ŷ_τ60",
      });
      const data = awaited.data || {};
      const httpOk = awaited.httpOk;
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!httpOk || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearT60ResultBox();
          renderT60CoefTable(null);
        }
        syncT60PersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncT60PersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderT60FitSummary(data);
      await renderT60CoefTable(rm, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
      throw err;
    }
  }

  on("quant-t60-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT60Ridge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-t60-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-t60-ridge-persist", "click", onT60PersistClick);
  on("quant-t60-ridge-persist-research", "click", onT60PersistClick);
  async function onT60PersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _t60PromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_τ60 研究套（t60_ridge_model_research.json）"
        : "ŷ_τ60 执行套（t60_ridge_model.json；做 T 旁路，不改 C_τ）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_τ60 写入 ${target}？不进 ranking、不改 C_τ。`)) {
      return;
    }
    try {
      await runT60Ridge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-t60-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  }

  on("quant-t60-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-t60-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t60-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncT60PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT60ResultBox();
        await renderT60CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncT60PersistBtn(gate, { hasReport: true });
      clearT60ResultBox();
      await renderT60CoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });
  async function runT90Ridge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-t90-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τ90 Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t90-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 120,
          watching_limit: 200,
          ridge_lambda: 1.0,
          minute_period: "5",
          minute_lookback_days: 150,
          holdout_trading_days: readHoldoutTradingDays(),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui t90 force promote ${persistRole}`
              : `ui t90 promote ${persistRole}`
            : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "t90-ridge",
        modelPath: "/api/quant/t90-ridge/model",
        sumEl: sum,
        label: "ŷ_τ90",
      });
      const data = awaited.data || {};
      const httpOk = awaited.httpOk;
      const gate = data.promote_gate || (data.persisted && data.persisted.promote_gate) || null;
      const persistFailed =
        persist &&
        data.persisted &&
        data.persisted.success === false &&
        !data.persisted.skipped;
      if (!httpOk || (!data.success && !persistFailed) || persistFailed) {
        const err =
          (data.persisted && data.persisted.error) ||
          (data && (data.detail || data.error)) ||
          `HTTP ${res.status}`;
        const gateNote =
          gate && Array.isArray(gate.blockers) && gate.blockers.length
            ? ` · 闸：${gate.blockers.join("；")}`
            : "";
        renderRemStatus(sum, {
          state: "error",
          chip: persistFailed ? "未过闸" : "失败",
          message: String(err) + gateNote,
          error: true,
        });
        if (!persistFailed) {
          clearT90ResultBox();
          renderT90CoefTable(null);
        }
        syncT90PersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      const gateMsg =
        gate && !gate.ok
          ? ` · 未过 promote 闸（${(gate.blockers || []).join("；")}；可强制启用）`
          : gate && gate.ok
            ? " · 可启用研究 / 启用执行"
            : "";
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist
          ? forcePromote && gate && !gate.ok
            ? `已跳过闸：${(gate.blockers || []).join("；")}`
            : ""
          : `未写盘${gateMsg}`,
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncT90PersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderT90FitSummary(data);
      await renderT90CoefTable(rm, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
      throw err;
    }
  }

  on("quant-t90-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT90Ridge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-t90-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-t90-ridge-persist", "click", onT90PersistClick);
  on("quant-t90-ridge-persist-research", "click", onT90PersistClick);
  async function onT90PersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _t90PromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_τ90 研究套（t90_ridge_model_research.json）"
        : "ŷ_τ90 执行套（t90_ridge_model.json；做 T 旁路，不改 C_τ）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_τ90 写入 ${target}？不进 ranking、不改 C_τ。`)) {
      return;
    }
    try {
      await runT90Ridge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-t90-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  }

  on("quant-t90-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-t90-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t90-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncT90PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT90ResultBox();
        await renderT90CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, {
        gate,
        message:
          gate && !gate.ok
            ? `promote 闸：${(gate.blockers || []).join("；")}（可强制启用）`
            : undefined,
      });
      syncT90PersistBtn(gate, { hasReport: true });
      clearT90ResultBox();
      await renderT90CoefTable(data.return_model || {}, {
        oos,
        researchModel: data.return_model_research || null,
      });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-tau-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runTauRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-tau-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-tau-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runTauTree();
    } catch (err) {
      const sum = document.getElementById("quant-tau-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderTauTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-tau-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadTauTreeLast();
    } catch (err) {
      renderTauTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-r-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runRTree();
    } catch (err) {
      const sum = document.getElementById("quant-r-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderRTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-r-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadRTreeLast();
    } catch (err) {
      renderRTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t30-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT30Tree();
    } catch (err) {
      const sum = document.getElementById("quant-t30-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderT30TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t30-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadT30TreeLast();
    } catch (err) {
      renderT30TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t60-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT60Tree();
    } catch (err) {
      const sum = document.getElementById("quant-t60-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderT60TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t60-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadT60TreeLast();
    } catch (err) {
      renderT60TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });
  on("quant-t90-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT90Tree();
    } catch (err) {
      const sum = document.getElementById("quant-t90-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderT90TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t90-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadT90TreeLast();
    } catch (err) {
      renderT90TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-tau-ridge-persist", "click", onTauPersistClick);
  on("quant-tau-ridge-persist-research", "click", onTauPersistClick);
  async function onTauPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _tauPromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_oc 研究套（tau_ridge_model_research.json，供历史回测）"
        : "ŷ_oc 执行套（tau_ridge_model.json，供交易执行）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (
        !window.confirm(
          `promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`
        )
      ) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_oc 写入 ${target}？不改 ŷ_oo、不改聚类。`)) {
      return;
    }
    try {
      await runTauRidge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-tau-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  }

  on("quant-on-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runOnRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-on-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-on-ridge-persist", "click", onOnPersistClick);
  on("quant-on-ridge-persist-research", "click", onOnPersistClick);
  async function onOnPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const target =
      persistRole === "research"
        ? "ŷ_co 研究套（on_ridge_model_research.json，供历史回测）"
        : "ŷ_co 执行套（on_ridge_model.json，供交易执行）";
    if (!window.confirm(`将 on / ŷ_co 写入 ${target}？不改 ŷ_oo / ŷ_oc、不进主排序。`)) {
      return;
    }
    try {
      await runOnRidge({ persist: true, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-on-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  }

  on("quant-on-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-on-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/on-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        clearOnResultBox();
        await renderOnCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data);
      clearOnResultBox();
      await renderOnCoefTable(data.return_model || {}, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-tau-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-tau-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/tau-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        const painted = paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        if (!data.research_exists && !data.live_model_present) {
          syncOverviewTau("未启用", "ŷ_oc 模型缺失", "text");
        } else {
          syncOverviewTau(painted.chip, data.promoted_at || "ŷ_oc 模型", "text");
        }
        clearRemResultBox();
        await renderRemCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      const painted = paintRidgeEnableStatus(sum, data, { gate });
      const chip = painted.chip;
      syncTauPersistBtn(gate, { hasReport: true });
      if (oos.sign_hit != null || (oos.ic != null && Number.isFinite(Number(oos.ic))) || tauOpenBucket(oos)) {
        syncOverviewTauFromOos(oos, chip);
      } else {
        syncOverviewTau(chip, data.promoted_at || "ŷ_oc 模型", "text");
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

  on("quant-return-model-fit", "click", async (e) => {
    e.preventDefault();
    try {
      await backtest.fitReturnScoreModel();
    } catch (err) {
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = String(err.message || err);
    }
  });


  on("quant-research-export", "click", (e) => {
    e.preventDefault();
    const result = state.lastBacktestPack && state.lastBacktestPack.result;
    const pack = {
      exported_at: new Date().toISOString(),
      backtest: state.lastBacktestPack,
      curves: backtest.buildResearchCurves(result),
      note: "研究包 · 含 IC/分层/基准曲线；不含实盘指令",
    };
    if (!pack.backtest) {
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = "请先跑回测";
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
      if (els.quantPortfolioSummary) els.quantPortfolioSummary.textContent = "请先跑回测";
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
      if (els.quantMeta) els.quantMeta.textContent = "已打开 AI…";
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
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
      exportDomain.showQuantInterpretPanel();
      if (els.quantInterpretBody) els.quantInterpretBody.textContent = String(err.message || err);
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
  if (hash === "strategy-factor-dict") {
    const fold = document.getElementById("strategy-factor-dict-fold");
    if (fold) fold.open = true;
  }

  if (document.getElementById("strategy-market-context")) {
    strategy.loadStrategyList().catch(() => {});
  }

  const mctxRefreshBtn = document.getElementById("strategy-mctx-refresh");
  if (mctxRefreshBtn) {
    mctxRefreshBtn.addEventListener("click", (e) => {
      e.preventDefault();
      strategy.refreshMarketContextPanel?.().catch(() => {});
    });
  }
  const mctxSaveBtn = document.getElementById("strategy-mctx-save");
  if (mctxSaveBtn) {
    mctxSaveBtn.addEventListener("click", (e) => {
      e.preventDefault();
      strategy.saveStrategyMarketPrior?.().catch(() => {});
    });
  }
  const mctxModeSync = [
    ["strategy-mctx-mode", "syncMctxGateOptsVisibility"],
    ["strategy-msp-mode", "syncMspGateOptsVisibility"],
    ["strategy-reg-mode", "syncRegGateOptsVisibility"],
    ["strategy-ipo-mode", "syncIpoGateOptsVisibility"],
  ];
  mctxModeSync.forEach(([id, fn]) => {
    document.getElementById(id)?.addEventListener("change", () => {
      strategy[fn]?.();
    });
  });
  strategy.loadMarketPriorForm?.().catch(() => {});
  if (page === "strategy") {
    strategy.loadMarketContextPanel?.().catch(() => {});
  }

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
      if (document.querySelector(".replay-t0-section")) return;
      const t0Status = document.getElementById("paper-t0-action-status");
      if (t0Status) t0Status.textContent = summary;
      else if (els.quantMeta) els.quantMeta.textContent = `${summary} · 详情见历史回测`;
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
}
