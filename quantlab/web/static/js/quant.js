import {
  escapeHtml,
  formatApiDetail,
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
import { createScoreTooltipController } from "./score_tooltip.js";
import { fmtScore, scoreCls } from "./paper/fmt.js";
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
} from "./quant/names.js";
import { createResearchParams } from "./quant/params.js";
import { createFactorMetaCache } from "./quant/factor_meta.js";
import { researchGridHtml, metricCell } from "./quant/research_grid.js";
import { createBtTablesUi } from "./quant/bt_tables.js";
import { installClusterProbe } from "./quant/domain_cluster.js";
import { installSuggest } from "./quant/domain_suggest.js";
import { installExportInterpret } from "./quant/domain_export.js";
import { loadAndRenderFactorIR, setProStatusChip, syncOverviewFromClusters, syncOverviewTau, renderFactorSummaryCards } from "./quant/factor_corr_ui.js";

const _QV =
  (typeof window !== "undefined" && window.__ASSET_V__) || "dev";

/**
 * 统一格式化树后端名：lightgbm；oo_rank 为 lambdarank（旧包名仅展示）
 */
function formatTreeBackend(backend) {
  const s = String(backend || "").toLowerCase().trim();
  if (s === "lightgbm" || s === "lgb" || s === "") return "LightGBM";
  if (s === "lambdarank" || s === "lightgbm_lambda") return "LambdaRank";
  return s;
}

window.formatTreeBackend = formatTreeBackend;
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
const { installBarsUi } = await import(
  `./quant/bars_ui.js?v=${encodeURIComponent(_QV)}`
);
const { installMinuteUi } = await import(
  `./quant/minute_ui.js?v=${encodeURIComponent(_QV)}`
);
const { installBarsIntegrityUi } = await import(
  `./quant/bars_integrity_ui.js?v=${encodeURIComponent(_QV)}`
);
const { installResearchUniverseUi } = await import(
  `./quant/research_universe_ui.js?v=${encodeURIComponent(_QV)}`
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
    readFitLookbackDays,
    hydrateFitLookbackDays,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  } = researchParams;
  hydrateHoldoutTradingDays();
  hydrateFitLookbackDays();
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
            "口径：每个交易日 09:30 rank_lots（本金默认 20 万 · rank=w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) · 入场/强档金额）。" +
    "净值起点 100；成本按纸面成本模型。有效≠正确：先看超额/回撤，再解读累计收益。";
  const BT_SCOPE_FROZEN =
    "以下为 quant_daily 冻结摘要，不是刚才点的回测；点「跑回测」刷新当次结果。";
  const quantBtBusyIds = [
    "quant-portfolio-run",
    "quant-return-model-fit",
  ];
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
    quantFactorList: document.getElementById("quant-factor-list"),
    quantOlsHealth: document.getElementById("quant-ols-health"),
    quantWeightSuggest: document.getElementById("quant-weight-suggest"),
    quantWeightTable: document.getElementById("quant-weight-table"),
    quantOlsSummary: document.getElementById("quant-ols-summary"),
    quantOlsClusters: document.getElementById("quant-ols-clusters"),
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
    watchingFocusCode: null,
    watchingFocusName: "",
    quantLastWeightDiff: null,
    quantLastOlsClusters: null,
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
  };

  const watchingScoreTips = createScoreTooltipController();
  const btSimScoreTips = createScoreTooltipController();

  const q = {
    ctx, on, els, state, escapeHtml, apiFetch, downloadBlob, downloadJson,
    fmtPct, metricClass, researchGridHtml, metricCell,
    clampHorizonDays, syncHorizonInputs, readHorizonDays, readRidgeLambda, readClusterK,
    readWatchingLimit, readHoldoutTradingDays, readFitLookbackDays,
    setPrefsHorizonDays, getPrefsHorizonDays,
    factorMetaByName, factorMetaByLabel, rememberFactorMeta, ensureFactorMeta,
    factorDescription, factorNameCellHtml, factorTaxonomyCellHtml,
    BT_SCOPE_LIVE, BT_SCOPE_FROZEN, quantBtBusyIds,
    setQuantMeta, setBusyText, watchingScoreTips, btSimScoreTips,
    buildResearchCurves,
    truncateStockName, watchingNameSpanHtml, watchingNameFromEl, applyWatchingNameEl,
    fmtScore, scoreCls, mountVirtualTable, colStyle,
    renderLineChart, renderDualLineChart, renderMultiLineChart,
    buildPortfolioBacktestSummaryText, buildPortfolioBacktestFailText,
    buildBtScoreFloorPayload, defaultScoringFloors, mergeScoringFloors,
    attachReadmeLinkHandler, renderReadmeLinksHtml,
  };

  Object.assign(q, createBtResultRenderers({ escapeHtml, fmtPct, metricClass }));
  Object.assign(q, createFactorIcUi({ escapeHtml, researchGridHtml, metricCell, metricClass, factorMetaByName, factorMetaByLabel, factorNameCellHtml, factorTaxonomyCellHtml }));
  Object.assign(q, createOlsUi({ escapeHtml }));
  Object.assign(q, createBtTablesUi({
    escapeHtml, researchGridHtml, metricCell, fmtPct, metricClass,
    getWatchingNameByCode: () => state.watchingNameByCode,
  }));

  const watching = installWatching(q);
  const backtest = installBacktest(q);
  const cluster = installClusterProbe(q);
  const suggest = installSuggest(q);
  const barsUi = installBarsUi(q);
  const minuteUi = installMinuteUi(q);
  installBarsIntegrityUi(q);
  const researchUniverse = installResearchUniverseUi(q);
  const strategy = installStrategy(q);
  const exportDomain = installExportInterpret(q);
  q.watching = watching;
  q.backtest = backtest;
  q.cluster = cluster;
  q.suggest = suggest;
  q.barsUi = barsUi;
  q.minuteUi = minuteUi;
  q.researchUniverse = researchUniverse;
  q.strategy = strategy;
  q.exportDomain = exportDomain;

  async function renderOoCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-oo-coef-table");
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
        ? q.remCoefTableHtml(rm, { ...opts, head: "oo" })
        : "";
    host.innerHTML = html || "";
  }

  function clearOoResultBox() {
    const box = document.getElementById("quant-oo-result");
    if (box) box.innerHTML = "";
  }

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
        ? q.remCoefTableHtml(rm, { ...opts, head: "τc" })
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

  function remIcTone(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    // A 股：正红、负绿
    return n > 0 ? "up" : "down";
  }

  function remStatusMeta(label, value, tip, opts = {}) {
    const tone =
      opts.signed === true
        ? remIcTone(opts.raw != null ? opts.raw : value)
        : opts.tone
          ? String(opts.tone)
          : "";
    return (
      `<span class="quant-rem-meta"${tip ? ` title="${escapeHtml(tip)}"` : ""}>` +
      `<span class="quant-rem-meta-k">${escapeHtml(label)}</span>` +
      `<span class="quant-rem-meta-v${tone ? ` ${tone}` : ""}">${escapeHtml(String(value))}</span>` +
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
      syncOverviewTau(oos.sign_hit, extra || "ŷ_τc 命中 · τ→收", "hit");
      return;
    }
    if (oos && oos.ic != null && Number.isFinite(Number(oos.ic))) {
      syncOverviewTau(oos.ic, extra || "ŷ_τc IC", "ic");
    }
  }

  /**
   * ŷ_* 卡头状态：拟合时间 / 研究·执行启用时间（或未写盘）；
   * 有启用态 meta 时不再叠「研究+执行」等 chip（与 meta 重复）。
   * busy / error / 尚无 meta 时仍显示 chip。
   * OOS 指标只在系数表 KPI 条展示。
   */
  function renderRemStatus(el, opts = {}) {
    if (!el) return;
    const {
      state = "idle",
      chip = "待命",
      message = "",
      fittedAt = null,
      promotedAt = null,
      researchAt = null,
      liveAt = null,
      liveOn = null,
      researchOn = null,
      busy = false,
      error = false,
    } = opts;
    const chipState = error ? "error" : busy ? "busy" : state;
    const msgText = formatApiDetail(message, "");
    const metas = [];
    // 拟合时间只用 fittedAt；勿回退 promotedAt（易与启用时间混淆）
    const fitTs = fittedAt || null;
    if (fitTs) {
      metas.push(
        remStatusMeta(
          "拟合",
          fmtRemTs(fitTs),
          `最近一次拟合的模型标识 ${String(fitTs)}（last report / 草稿）`
        )
      );
    }
    if (liveOn != null || researchOn != null) {
      const resTs = researchAt || null;
      const execTs = liveAt || null;
      metas.push(
        remStatusMeta(
          "研究",
          researchOn
            ? resTs
              ? fmtRemTs(resTs)
              : "已启用"
            : "未写盘",
          researchOn
            ? `研究套模型拟合标识${resTs ? ` ${String(resTs)}` : ""}（*_research.json）`
            : "研究套尚未写入 *_research.json"
        )
      );
      metas.push(
        remStatusMeta(
          "执行",
          liveOn
            ? execTs
              ? fmtRemTs(execTs)
              : "已启用"
            : "未写盘",
          liveOn
            ? `执行套模型拟合标识${execTs ? ` ${String(execTs)}` : ""}（live *_model.json）`
            : "执行套尚未写入 live 模型"
        )
      );
    }
    // 拟合/研究/执行 meta 已表达落盘态；chip 仅保留忙碌、失败、或尚未有 meta 的空闲提示
    // 顺序：meta 靠左 → 文案 → chip（忙碌/失败时）
    const showChip = !!busy || !!error || metas.length === 0;
    const html =
      (metas.length
        ? `<span class="quant-rem-status-meta">${metas.join("")}</span>`
        : "") +
      (msgText
        ? `<span class="quant-rem-status-msg">${escapeHtml(msgText)}</span>`
        : "") +
      (showChip
        ? `<span class="quant-pro-status-chip quant-rem-status-chip" data-state="${escapeHtml(
            chipState
          )}">${escapeHtml(chip)}</span>`
        : "");
    setBusyText(el, html, { busy: !!busy && !error, html: true });
    if (error) el.classList.add("is-error");
    else el.classList.remove("is-error");
  }
  q.renderRemStatus = renderRemStatus;
  q.renderOoCoefTable = renderOoCoefTable;
  q.clearOoResultBox = clearOoResultBox;

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
        ? "有上次拟合 · 可启用研究 / 执行"
        : src.note || "");
    let message = extra.message;
    if (message === undefined) {
      // promote 闸明细只挂在「启用」按钮 title / 确认框，不占卡头
      if (painted.chip === "未启用" || painted.chip === "已拟合") {
        message = idleFallback;
      } else {
        message = "";
      }
    }
    const researchAt =
      extra.researchAt !== undefined
        ? extra.researchAt
        : src.research_fitted_at ||
          src.research_promoted_at ||
          (extra.persistOk && extra.persistRole === "research"
            ? src.fitted_at ||
              src.promoted_at ||
              (src.persisted && src.persisted.promoted_at) ||
              null
            : null);
    const liveAt =
      extra.liveAt !== undefined
        ? extra.liveAt
        : src.live_fitted_at ||
          src.live_promoted_at ||
          (extra.persistOk && extra.persistRole === "live"
            ? src.fitted_at ||
              src.promoted_at ||
              (src.persisted && src.persisted.promoted_at) ||
              null
            : null);
    renderRemStatus(el, {
      state: extra.state || painted.state,
      chip: extra.chip || painted.chip,
      message,
      oos: extra.oos !== undefined ? extra.oos : src.oos,
      sampleCount:
        extra.sampleCount !== undefined ? extra.sampleCount : src.sample_count,
      sampleCountDay:
        extra.sampleCountDay !== undefined
          ? extra.sampleCountDay
          : src.sample_count_day,
      fittedAt:
        extra.fittedAt !== undefined ? extra.fittedAt : src.fitted_at || null,
      researchAt,
      liveAt,
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

  async function renderCoCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-co-coef-table");
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
        ? q.remCoefTableHtml(rm, { ...opts, head: "co" })
        : "";
    host.innerHTML = html || "";
  }

  function clearCoResultBox() {
    const box = document.getElementById("quant-co-result");
    if (box) box.innerHTML = "";
  }

  async function renderOoRankCoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-oo-rank-coef-table");
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
        ? q.remCoefTableHtml(rm, { ...opts, head: "oo_rank" })
        : "";
    host.innerHTML = html || "";
  }

  function clearOoRankResultBox() {
    const box = document.getElementById("quant-oo-rank-result");
    if (box) box.innerHTML = "";
  }

  async function paintOoRankDesk(sum, data, extra = {}) {
    const src = data && typeof data === "object" ? data : {};
    const rm =
      src.return_model && typeof src.return_model === "object"
        ? src.return_model
        : src.coefficients && typeof src.coefficients === "object"
          ? src
          : null;
    const oos = src.oos && typeof src.oos === "object" ? src.oos : {};
    const hasBody = !!(
      (rm && typeof rm === "object") ||
      oos.oo_rank ||
      oos.ridge_oo_baseline
    );
    const persisted = !!(src.exists && !src.shadow);
    let message = extra.message;
    if (message === undefined) {
      if (!hasBody) {
        message = src.note || "尚无影子对照";
      } else if (persisted) {
        message = "已落盘影子 · 成交 rank=1..n · 不进买序";
      } else {
        message = "刷新仍保留上次拟合";
      }
    }
    renderRemStatus(sum, {
      state: extra.state || (hasBody ? "ok" : "idle"),
      chip:
        extra.chip ||
        (persisted ? "已落盘" : hasBody ? "草稿" : "待命"),
      message,
      fittedAt:
        extra.fittedAt !== undefined
          ? extra.fittedAt
          : src.fitted_at || (rm && rm.fitted_at) || null,
      busy: extra.busy,
      error: extra.error,
    });
    if (!hasBody) {
      clearOoRankResultBox();
      await renderOoRankCoefTable(null);
      return;
    }
    renderOoRankOosCompare(src);
    await renderOoRankCoefTable(rm && typeof rm === "object" ? rm : {}, {
      oos,
    });
  }

  function _fmtOoRankNum(v, digits = 4) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(digits);
  }

  function _ooRankBarPair(a, b) {
    const x = Number(a);
    const y = Number(b);
    const ax = Number.isFinite(x) ? x : null;
    const by = Number.isFinite(y) ? y : null;
    const m = Math.max(Math.abs(ax || 0), Math.abs(by || 0), 1e-12);
    const d =
      ax != null && by != null ? ax - by : null;
    function bar(v, kind, lead) {
      const w =
        v == null ? 0 : Math.max(2, Math.round((100 * Math.abs(v)) / m));
      const dir = v == null ? "" : v < 0 ? " is-neg" : " is-pos";
      const leadCls = lead ? " is-lead" : "";
      return (
        `<span class="quant-oos-bar ${kind}${dir}${leadCls}">` +
        `<i style="width:${v == null ? 0 : w}%"></i></span>`
      );
    }
    return (
      `<div class="quant-oos-bars" aria-hidden="true">` +
      bar(ax, "is-lr", d != null && d > 1e-12) +
      bar(by, "is-rg", d != null && d < -1e-12) +
      `</div>`
    );
  }

  function renderOoRankOosCompare(data) {
    const box = document.getElementById("quant-oo-rank-result");
    if (!box) return;
    if (!data || data.success === false) {
      box.innerHTML = "";
      return;
    }
    const oos = data.oos || {};
    const rank = oos.oo_rank || {};
    const ridge = oos.ridge_oo_baseline || {};
    if (!rank.spearman && !ridge.spearman && rank.topk_mean_y_oo == null) {
      box.innerHTML = "";
      return;
    }
    const track = data.shadow_track || {};
    function delta(a, b, fallback) {
      if (fallback != null && fallback !== "") return Number(fallback);
      if (a == null || b == null) return null;
      const x = Number(a);
      const y = Number(b);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      return x - y;
    }
    const rows = [
      [
        "Spearman",
        rank.spearman,
        ridge.spearman,
        delta(rank.spearman, ridge.spearman, track.delta_spearman),
      ],
      [
        "NDCG@K",
        rank.ndcg_at_k,
        ridge.ndcg_at_k,
        delta(rank.ndcg_at_k, ridge.ndcg_at_k, track.delta_ndcg_at_k),
      ],
      [
        "TopK overlap",
        rank.topk_overlap,
        ridge.topk_overlap,
        delta(rank.topk_overlap, ridge.topk_overlap, track.delta_topk_overlap),
      ],
      [
        "TopK y_oo%",
        rank.topk_mean_y_oo,
        ridge.topk_mean_y_oo,
        delta(
          rank.topk_mean_y_oo,
          ridge.topk_mean_y_oo,
          track.delta_topk_mean_y_oo
        ),
      ],
    ];
    let lrWins = 0;
    let rgWins = 0;
    const body = rows
      .map(([lab, a, b, d]) => {
        const dn = d == null || !Number.isFinite(Number(d)) ? null : Number(d);
        if (dn != null && dn > 1e-12) lrWins += 1;
        if (dn != null && dn < -1e-12) rgWins += 1;
        const lrCls = dn != null && dn > 1e-12 ? "num is-best" : "num";
        const rgCls = dn != null && dn < -1e-12 ? "num is-best" : "num";
        const dCls =
          dn == null ? "num" : dn > 1e-12 ? "num up" : dn < -1e-12 ? "num down" : "num";
        const dTxt =
          dn == null ? "—" : `${dn > 0 ? "+" : ""}${_fmtOoRankNum(dn)}`;
        return (
          `<tr><th scope="row">${lab}${_ooRankBarPair(a, b)}</th>` +
          `<td class="${lrCls}">${_fmtOoRankNum(a)}</td>` +
          `<td class="${rgCls}">${_fmtOoRankNum(b)}</td>` +
          `<td class="${dCls}">${dTxt}</td></tr>`
        );
      })
      .join("");
    const scored = lrWins + rgWins;
    const pool = data.watching_tier_a_only ? "观察池 A 档" : "整观察池";
    const hold =
      oos.holdout_trading_days != null
        ? `Holdout ${Number(oos.holdout_trading_days)} 日`
        : "同窗 Holdout";
    const nTe =
      data.n_test_days != null ? `测 ${Number(data.n_test_days)} 日` : "";
    const lrLead = scored > 0 && lrWins > rgWins;
    const rgLead = scored > 0 && rgWins > lrWins;
    box.innerHTML =
      `<div class="quant-oos-compare" aria-label="LambdaRank vs Ridge">` +
      `<div class="quant-oos-board">` +
      `<div class="quant-oos-board-card${lrLead ? " is-lead" : ""}">` +
      `<span class="quant-oos-board-kicker">排序头</span>` +
      `<span class="quant-oos-board-name">LambdaRank</span>` +
      `<span class="quant-oos-board-wins">${lrWins}<small>/${scored || "—"}</small></span>` +
      `</div>` +
      `<div class="quant-oos-board-card${rgLead ? " is-lead" : ""}">` +
      `<span class="quant-oos-board-kicker">回归头</span>` +
      `<span class="quant-oos-board-name">Ridge</span>` +
      `<span class="quant-oos-board-wins">${rgWins}<small>/${scored || "—"}</small></span>` +
      `</div>` +
      `</div>` +
      `<table class="quant-weight-table quant-oos-compare-table">` +
      `<thead><tr><th>指标</th><th>LambdaRank</th><th>Ridge</th><th>Δ</th></tr></thead>` +
      `<tbody>${body}</tbody></table>` +
      `<p class="quant-oos-compare-note">${hold}${nTe ? " · " + nTe : ""} · ${pool} · 成交 rank=1..n · 入场闸在回测 · 条长为该项相对幅度，加粗为胜出</p>` +
      `</div>`;
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

  async function renderT45CoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-t45-coef-table");
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
        ? q.remCoefTableHtml(rm, { ...opts, head: "t45" })
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
  async function renderT75CoefTable(rm, opts = {}) {
    const host = document.getElementById("quant-t75-coef-table");
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
        ? q.remCoefTableHtml(rm, { ...opts, head: "t75" })
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
      (byTau["10:00"] && byTau["10:00"].n) ||
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

  let ooTreeBusyTimer = null;

  function stopOoTreeBusy() {
    if (ooTreeBusyTimer) {
      clearInterval(ooTreeBusyTimer);
      ooTreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-oo-tree-run");
    if (btn) btn.disabled = false;
  }

  function startOoTreeBusy() {
    stopOoTreeBusy();
    const sum = document.getElementById("quant-oo-tree-summary");
    const btn = document.getElementById("quant-oo-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg =
        s < 30
          ? `拉观察池日线 · 已 ${fmtTauTreeSec(s)}`
          : s < 90
            ? `堆叠日线因子面板 · 已 ${fmtTauTreeSec(s)}`
            : `LightGBM + Ridge Holdout · 已 ${fmtTauTreeSec(s)}`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderOoTreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    ooTreeBusyTimer = setInterval(tick, 1000);
  }

  function renderOoTreeCompare(data) {
    const box = document.getElementById("quant-oo-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_oo_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_oo_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "oo" });
  }

  async function runOoTree() {
    const sum = document.getElementById("quant-oo-tree-summary");
    startOoTreeBusy();
    try {
      const res = await fetch("/api/quant/oo-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("oo_tree"),
          watching_limit: readWatchingLimit(),
          horizon_days: 1,
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("oo_tree"),
          backend: "lightgbm",
          include_alpha158: true,
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopOoTreeBusy();
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        renderOoTreeCompare({ success: false, error: String(err) });
        return;
      }
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
      renderOoTreeCompare(data);
    } finally {
      stopOoTreeBusy();
    }
  }

  async function loadOoTreeLast() {
    const sum = document.getElementById("quant-oo-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/oo-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_oo_tree",
      });
      renderOoTreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    paintTreeFitStatus(sum, data, { chip: "上次" });
    renderOoTreeCompare(data);
  }

  let coTreeBusyTimer = null;

  function stopCoTreeBusy() {
    if (coTreeBusyTimer) {
      clearInterval(coTreeBusyTimer);
      coTreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-co-tree-run");
    if (btn) btn.disabled = false;
  }

  function startCoTreeBusy() {
    stopCoTreeBusy();
    const sum = document.getElementById("quant-co-tree-summary");
    const btn = document.getElementById("quant-co-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg =
        s < 30
          ? `拉观察池日线 · 已 ${fmtTauTreeSec(s)}`
          : s < 90
            ? `堆叠隔夜缺口 Z 面板 · 已 ${fmtTauTreeSec(s)}`
            : `LightGBM + Ridge Holdout · 已 ${fmtTauTreeSec(s)}`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderCoTreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    coTreeBusyTimer = setInterval(tick, 1000);
  }

  function renderCoTreeCompare(data) {
    const box = document.getElementById("quant-co-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_co_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_co_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "co" });
  }

  async function runCoTree() {
    const sum = document.getElementById("quant-co-tree-summary");
    startCoTreeBusy();
    try {
      const res = await fetch("/api/quant/co-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("co_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("co_tree"),
          backend: "lightgbm",
          include_alpha158: true,
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopCoTreeBusy();
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        renderCoTreeCompare({ success: false, error: String(err) });
        return;
      }
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
      renderCoTreeCompare(data);
    } finally {
      stopCoTreeBusy();
    }
  }

  async function loadCoTreeLast() {
    const sum = document.getElementById("quant-co-tree-summary");
    const statusBtn = document.getElementById("quant-co-tree-status");
    if (coTreeBusyTimer) {
      clearInterval(coTreeBusyTimer);
      coTreeBusyTimer = null;
    }
    if (statusBtn) statusBtn.disabled = true;
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次对照…",
      busy: true,
    });
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 20000);
    try {
      const res = await fetch("/api/quant/co-tree/last", { signal: ctrl.signal });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        renderCoTreeCompare({ success: false, error: String(err) });
        return;
      }
      if (!data.exists || !data.success) {
        renderRemStatus(sum, {
          state: "idle",
          chip: "待命",
          message: data.note || "尚无 ŷ_co_tree",
        });
        renderCoTreeCompare({ success: false, error: data.note || "尚无上次对照" });
        return;
      }
      paintTreeFitStatus(sum, data, { chip: "上次" });
      renderCoTreeCompare(data);
    } catch (err) {
      const aborted = err && (err.name === "AbortError" || err.code === 20);
      const msg = aborted
        ? "读取超时（若正在拟合，请等拟合完成后再点「上次」）"
        : String((err && err.message) || err || "读取失败");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: msg,
        error: true,
      });
      renderCoTreeCompare({ success: false, error: msg });
    } finally {
      clearTimeout(to);
      if (statusBtn) statusBtn.disabled = false;
    }
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
    if (s < 90) return "组 τ→close 面板（满池分钟特征）";
    return "仍在组面板（LightGBM 约数秒；慢的是组样本）";
  }

  function paintTreeFitStatus(el, data, extra = {}) {
    const src = data && typeof data === "object" ? data : {};
    const rm =
      (src.tree_return_model && typeof src.tree_return_model === "object"
        ? src.tree_return_model
        : null) ||
      (src.return_model && typeof src.return_model === "object"
        ? src.return_model
        : null) ||
      {};
    // 引擎 / Alpha158 / 用时已在对照报告里，卡头只留拟合时间 + 芯片
    renderRemStatus(el, {
      state: extra.state || "ok",
      chip: extra.chip || "已拟合",
      message: extra.message !== undefined ? extra.message : "",
      oos: extra.oos !== undefined ? extra.oos : src.oos || {},
      sampleCount:
        extra.sampleCount !== undefined ? extra.sampleCount : src.sample_count,
      fittedAt:
        extra.fittedAt !== undefined
          ? extra.fittedAt
          : src.fitted_at || rm.fitted_at || null,
    });
  }

  function stopTauTreeBusy() {
    if (tauTreeBusyTimer) {
      clearInterval(tauTreeBusyTimer);
      tauTreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-tau-tree-run");
    if (btn) btn.disabled = false;
  }

  /** 停掉「拟合中」轮询重绘，避免盖住「上次」结果；不改 run 按钮（拟合仍在飞时保持禁用）。 */
  function pauseTauTreeBusyOverlay() {
    if (tauTreeBusyTimer) {
      clearInterval(tauTreeBusyTimer);
      tauTreeBusyTimer = null;
    }
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
    box.innerHTML = treeReportHtml(data, { head: "tau" });
  }

  async function runTauTree() {
    const sum = document.getElementById("quant-tau-tree-summary");
    startTauTreeBusy();
    try {
            const res = await fetch("/api/quant/tau-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("tc_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("tc_tree"),
          backend: "lightgbm",
          include_alpha158: true,
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
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
      renderTauTreeCompare(data);
    } finally {
      stopTauTreeBusy();
    }
  }

  async function loadTauTreeLast() {
    const sum = document.getElementById("quant-tau-tree-summary");
    const statusBtn = document.getElementById("quant-tau-tree-status");
    // 拟合中的 1s 轮询会把「上次」结果盖回「拟合中」；先停 overlay
    pauseTauTreeBusyOverlay();
    if (statusBtn) statusBtn.disabled = true;
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次对照…",
      busy: true,
    });
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 20000);
    try {
      const res = await fetch("/api/quant/tau-tree/last", { signal: ctrl.signal });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
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
      if (!data.exists || !data.success) {
        renderRemStatus(sum, {
          state: "idle",
          chip: "待命",
          message: data.note || "尚无 ŷ_τc_tree",
        });
        renderTauTreeCompare({ success: false, error: data.note || "尚无上次对照" });
        return;
      }
      paintTreeFitStatus(sum, data, { chip: "上次" });
      renderTauTreeCompare(data);
    } catch (err) {
      const aborted = err && (err.name === "AbortError" || err.code === 20);
      const msg = aborted
        ? "读取超时（若正在拟合，请等拟合完成后再点「上次」）"
        : String((err && err.message) || err || "读取失败");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: msg,
        error: true,
      });
      renderTauTreeCompare({ success: false, error: msg });
    } finally {
      clearTimeout(to);
      if (statusBtn) statusBtn.disabled = false;
    }
  }

  let t30TreeBusyTimer = null;

  function t30TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 mean(price(τ⊕25/30/35))/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（LightGBM 约数秒；慢的是组样本）";
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
            const res = await fetch("/api/quant/t30-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t30_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("t30_tree"),
          backend: "lightgbm",
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
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
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
      message: "上次对照…",
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
    paintTreeFitStatus(sum, data, { chip: "上次" });
    renderT30TreeCompare(data);
  }

  let t45TreeBusyTimer = null;

  function t45TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 mean(price(τ⊕40/45/50))/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（LightGBM 约数秒；慢的是组样本）";
  }

  function stopT45TreeBusy() {
    if (t45TreeBusyTimer) {
      clearInterval(t45TreeBusyTimer);
      t45TreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-t45-tree-run");
    if (btn) btn.disabled = false;
  }

  function startT45TreeBusy() {
    stopT45TreeBusy();
    const sum = document.getElementById("quant-t45-tree-summary");
    const btn = document.getElementById("quant-t45-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${t45TreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderT45TreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    t45TreeBusyTimer = setInterval(tick, 1000);
  }

  function renderT45TreeCompare(data) {
    const box = document.getElementById("quant-t45-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_τ45_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_τ45_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "t45" });
  }

  async function runT45Tree() {
    const sum = document.getElementById("quant-t45-tree-summary");
    startT45TreeBusy();
    try {
            const res = await fetch("/api/quant/t45-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t45_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("t45_tree"),
          backend: "lightgbm",
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopT45TreeBusy();
      if (!res.ok || !data.success) {
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(data.error || data.note || data.detail || `HTTP ${res.status}`),
          error: true,
        });
        renderT45TreeCompare({
          success: false,
          error: data.error || data.note || `HTTP ${res.status}`,
        });
        return;
      }
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
      renderT45TreeCompare(data);
    } finally {
      stopT45TreeBusy();
    }
  }

  async function loadT45TreeLast() {
    const sum = document.getElementById("quant-t45-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/t45-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_τ45_tree",
      });
      renderT45TreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    paintTreeFitStatus(sum, data, { chip: "上次" });
    renderT45TreeCompare(data);
  }
  let t60TreeBusyTimer = null;

  function t60TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 mean(price(τ⊕55/60/65))/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（LightGBM 约数秒；慢的是组样本）";
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
            const res = await fetch("/api/quant/t60-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t60_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("t60_tree"),
          backend: "lightgbm",
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
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
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
      message: "上次对照…",
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
    paintTreeFitStatus(sum, data, { chip: "上次" });
    renderT60TreeCompare(data);
  }
  let t75TreeBusyTimer = null;

  function t75TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 mean(price(τ⊕70/75/80))/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（LightGBM 约数秒；慢的是组样本）";
  }

  function stopT75TreeBusy() {
    if (t75TreeBusyTimer) {
      clearInterval(t75TreeBusyTimer);
      t75TreeBusyTimer = null;
    }
    const btn = document.getElementById("quant-t75-tree-run");
    if (btn) btn.disabled = false;
  }

  function startT75TreeBusy() {
    stopT75TreeBusy();
    const sum = document.getElementById("quant-t75-tree-summary");
    const btn = document.getElementById("quant-t75-tree-run");
    if (btn) btn.disabled = true;
    const t0 = Date.now();
    const tick = () => {
      const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
      const msg = `${t75TreeBusyHint(s)} · 已 ${fmtTauTreeSec(s)} · 满池瓶颈在组面板，约数分钟`;
      renderRemStatus(sum, {
        state: "busy",
        chip: "拟合中",
        message: msg,
        busy: true,
      });
      renderT75TreeCompare({
        busy: true,
        success: false,
        message: msg,
      });
    };
    tick();
    t75TreeBusyTimer = setInterval(tick, 1000);
  }

  function renderT75TreeCompare(data) {
    const box = document.getElementById("quant-t75-tree-result");
    if (!box) return;
    if (!data || typeof data !== "object") {
      box.innerHTML = "";
      return;
    }
    if (data.busy) {
      box.innerHTML = `<div class="quant-tree-report is-busy"><p class="quant-attr-note">${escapeHtml(
        data.message || "ŷ_τ75_tree 拟合中…"
      )}</p></div>`;
      return;
    }
    if (!data.success) {
      box.innerHTML = `<p class="quant-attr-note">${escapeHtml(
        String(data.error || data.note || "ŷ_τ75_tree 拟合失败")
      )}</p>`;
      return;
    }
    box.innerHTML = treeReportHtml(data, { head: "t75" });
  }

  async function runT75Tree() {
    const sum = document.getElementById("quant-t75-tree-summary");
    startT75TreeBusy();
    try {
            const res = await fetch("/api/quant/t75-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t75_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("t75_tree"),
          backend: "lightgbm",
        }),
      });
      const data = await res.json().catch(() => ({}));
      stopT75TreeBusy();
      if (!res.ok || !data.success) {
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(data.error || data.note || data.detail || `HTTP ${res.status}`),
          error: true,
        });
        renderT75TreeCompare({
          success: false,
          error: data.error || data.note || `HTTP ${res.status}`,
        });
        return;
      }
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
      renderT75TreeCompare(data);
    } finally {
      stopT75TreeBusy();
    }
  }

  async function loadT75TreeLast() {
    const sum = document.getElementById("quant-t75-tree-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "上次对照…",
      busy: true,
    });
    const res = await fetch("/api/quant/t75-tree/last");
    const data = await res.json().catch(() => ({}));
    if (!data.exists || !data.success) {
      renderRemStatus(sum, {
        state: "idle",
        chip: "待命",
        message: data.note || "尚无 ŷ_τ75_tree",
      });
      renderT75TreeCompare({ success: false, error: data.note || "尚无上次对照" });
      return;
    }
    paintTreeFitStatus(sum, data, { chip: "上次" });
    renderT75TreeCompare(data);
  }
  let t90TreeBusyTimer = null;

  function t90TreeBusyHint(elapsedSec) {
    const s = Number(elapsedSec) || 0;
    if (s < 45) return "拉观察池行情 / 5m 缓存";
    if (s < 90) return "组 mean(price(τ⊕85/90/95))/price(τ) 面板（满池分钟特征）";
    return "仍在组面板（LightGBM 约数秒；慢的是组样本）";
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
            const res = await fetch("/api/quant/t90-tree", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t90_tree"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("t90_tree"),
          backend: "lightgbm",
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
      paintTreeFitStatus(sum, data, { chip: "已拟合" });
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
      message: "上次对照…",
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
    paintTreeFitStatus(sum, data, { chip: "上次" });
    renderT90TreeCompare(data);
  }

  function horizonProbFitBits(data) {
    const oos = (data && data.oos) || {};
    return [
      data && data.minute_codes_hit != null
        ? `分钟覆盖 ${data.minute_codes_hit}/${data.minute_codes_universe ?? "—"} 票`
        : null,
      ...ridgeFitNBits(data),
      oos.auc != null ? `OOS AUC ${Number(oos.auc).toFixed(3)}` : null,
      oos.acc_at_50 != null
        ? `acc@0.5 ${(Number(oos.acc_at_50) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
        : oos.sign_hit != null
          ? `OOS 命中 ${(Number(oos.sign_hit) * 100).toFixed(1)}%（n=${oos.n_valid ?? oos.n_test ?? "—"}）`
          : null,
      oos.brier != null ? `Brier ${Number(oos.brier).toFixed(3)}` : null,
    ].filter(Boolean);
  }

  function renderT30FitSummary(data) {
    const box = document.getElementById("quant-t30-result");
    if (!box || !data || typeof data !== "object") return;
    const bits = horizonProbFitBits(data);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  function renderT45FitSummary(data) {
    const box = document.getElementById("quant-t45-result");
    if (!box || !data || typeof data !== "object") return;
    const bits = horizonProbFitBits(data);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }
  function renderT60FitSummary(data) {
    const box = document.getElementById("quant-t60-result");
    if (!box || !data || typeof data !== "object") return;
    const bits = horizonProbFitBits(data);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }
  function renderT75FitSummary(data) {
    const box = document.getElementById("quant-t75-result");
    if (!box || !data || typeof data !== "object") return;
    const bits = horizonProbFitBits(data);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }
  function renderT90FitSummary(data) {
    const box = document.getElementById("quant-t90-result");
    if (!box || !data || typeof data !== "object") return;
    const bits = horizonProbFitBits(data);
    if (!bits.length) return;
    box.innerHTML =
      `<p class="quant-attr-note">${bits.map((b) => escapeHtml(String(b))).join(" · ")}</p>`;
  }

  /** ŷ_τc 启用：不过闸也可点，确认后 force_promote。 */
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

  let _t45PromoteGate = null;
  function syncT45PersistBtn(gate, { hasReport = true } = {}) {
    _t45PromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-t45-ridge-persist",
      "quant-t45-ridge-persist-research",
      _t45PromoteGate,
      { hasReport }
    );
  }

  function clearT45ResultBox() {
    const box = document.getElementById("quant-t45-result");
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
  let _t75PromoteGate = null;
  function syncT75PersistBtn(gate, { hasReport = true } = {}) {
    _t75PromoteGate = gate && typeof gate === "object" ? gate : null;
    syncRidgePersistPair(
      "quant-t75-ridge-persist",
      "quant-t75-ridge-persist-research",
      _t75PromoteGate,
      { hasReport }
    );
  }

  function clearT75ResultBox() {
    const box = document.getElementById("quant-t75-result");
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

  // ŷ_hl / path-ridge 已下线（模块与拟合入口已删）

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

  // 轻量预填 ŷ_τ45 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/t45-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-t45-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncT45PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT45ResultBox();
        await renderT45CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT45PersistBtn(gate, { hasReport: true });
      clearT45ResultBox();
      await renderT45CoefTable(data.return_model || {}, {
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
  // 轻量预填 ŷ_τ75 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/t75-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-t75-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data);
        syncT75PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT75ResultBox();
        await renderT75CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT75PersistBtn(gate, { hasReport: true });
      clearT75ResultBox();
      await renderT75CoefTable(data.return_model || {}, {
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

  // 轻量预填 ŷ_τc KPI / 状态（不阻塞；概览用模型 OOS，不再被单日验收覆盖）
  void (async () => {
    try {
      const res = await fetch("/api/quant/tc-ridge/model");
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
          syncOverviewTau(chip, data.promoted_at || "ŷ_τc", "text");
        }
      }
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_co 状态
  void (async () => {
    try {
      const res = await fetch("/api/quant/co-ridge/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-co-summary");
      if (!sum) return;
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "拟合后看系数 · 再启用研究/执行",
        });
        clearCoResultBox();
        await renderCoCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data);
      clearCoResultBox();
      await renderCoCoefTable(data.return_model || {}, { oos });
    } catch (_) {
      /* ignore */
    }
  })();

  // 轻量预填 ŷ_oo_rank 影子状态
  const OO_RANK_TIER_A_KEY = "quant.oo_rank.watching_tier_a_only";
  function readOoRankTierAOnly() {
    const el = document.getElementById("quant-oo-rank-tier-a");
    return !!(el && el.checked);
  }
  function hydrateOoRankTierA() {
    const el = document.getElementById("quant-oo-rank-tier-a");
    if (!el) return;
    try {
      el.checked = localStorage.getItem(OO_RANK_TIER_A_KEY) === "1";
    } catch (_) {
      /* ignore */
    }
    if (el.dataset.wired === "1") return;
    el.dataset.wired = "1";
    el.addEventListener("change", () => {
      try {
        localStorage.setItem(OO_RANK_TIER_A_KEY, el.checked ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
    });
  }
  hydrateOoRankTierA();
  void (async () => {
    try {
      const res = await fetch("/api/quant/oo-rank/model");
      const data = await res.json().catch(() => ({}));
      const sum = document.getElementById("quant-oo-rank-summary");
      if (!sum) return;
      await paintOoRankDesk(sum, data);
    } catch (_) {
      /* ignore */
    }
  })();

  cluster.wireOosGateTips(els.quantOlsClusters);
  cluster.wireOosGateTips(els.quantFactorList);
  cluster.wireOosGateTips(els.quantWeightSuggest);

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
  // ---- DSL 表达式因子 ----
  function exprTone(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    // A 股：正红、负绿（is-good / is-bad 走 --color-up / --color-down）
    return n > 0 ? "is-good" : "is-bad";
  }

  function exprNum(v) {
    return v == null || v === "" ? "—" : String(v);
  }

  function exprKpi(label, value, sub, raw) {
    return `<div class="dashboard-kpi-card ${exprTone(raw)}"><span class="dashboard-kpi-label">${escapeHtml(label)}</span><span class="dashboard-kpi-value">${escapeHtml(exprNum(value))}</span><span class="dashboard-kpi-sub">${escapeHtml(sub || "")}</span></div>`;
  }

  function exprCssColor(name, fallback) {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return raw || fallback;
  }

  function exprChartPoints(rows) {
    return (rows || [])
      .filter((p) => p && p.date && p.value != null && Number.isFinite(Number(p.value)))
      .map((p) => ({ time: String(p.date).slice(0, 10), value: Number(p.value) }));
  }

  async function paintExprCharts(data) {
    const seriesHost = document.getElementById("quant-expr-series-chart");
    const icHost = document.getElementById("quant-expr-ic-chart");
    const ink = exprCssColor("--ink", "#334155");
    const accent = exprCssColor("--accent", "#2563eb");
    await renderLineChart(seriesHost, exprChartPoints(data.series || data.recent), {
      color: accent,
      emptyText: "因子序列不足",
      disableZoom: true,
    });
    const icNote = data.cs_note || "截面样本不足";
    await renderLineChart(icHost, exprChartPoints(data.cs_ic_path), {
      color: ink,
      zeroLine: true,
      emptyText: icNote,
      disableZoom: true,
    });
  }

  function paintExprKpis(data) {
    const wrap = document.getElementById("quant-expr-kpis");
    const body = document.getElementById("quant-expr-kpi-body");
    if (!wrap || !body) return;
    const csSub = data.cs_rank_ic != null
      ? `${data.cs_days || 0}日 · ${data.cs_names || 0}只`
      : (data.cs_note || "样本不足");
    const irSub = data.cs_positive_rate != null ? `正日 ${data.cs_positive_rate}%` : "日度 Rank IC";
    body.innerHTML = [
      exprKpi("最新", data.last_value, data.last_date || data.code || "", null),
      exprKpi("本票 IC", data.ic, `n=${data.valid_count ?? "—"}`, data.ic),
      exprKpi("本票 Rank IC", data.rank_ic, "时序", data.rank_ic),
      exprKpi("截面 Rank IC", data.cs_rank_ic, csSub, data.cs_rank_ic),
      exprKpi("IR", data.cs_ir, irSub, data.cs_ir),
    ].join("");
    wrap.hidden = false;
  }

  async function runExprEval() {
    const sum = document.getElementById("quant-expr-summary");
    const code = (document.getElementById("quant-expr-code") || {}).value || "茅台";
    const expr = (document.getElementById("quant-expr-text") || {}).value || "";
    const horizon = parseInt((document.getElementById("quant-expr-horizon") || {}).value || "5", 10);
    if (!expr.trim()) {
      if (sum) sum.innerHTML = '<span class="quant-pro-status-chip" data-state="error">错误</span> <span class="quant-rem-status-msg">表达式不能为空</span>';
      return;
    }
    if (sum) sum.innerHTML = '<span class="quant-pro-status-chip" data-state="busy">求值中</span>';
    try {
      const res = await fetch("/api/quant/expr-eval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, expr, lookback: 120, horizon_days: horizon }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        if (sum) sum.innerHTML = `<span class="quant-pro-status-chip" data-state="error">失败</span> <span class="quant-rem-status-msg">${escapeHtml(String(err))}</span>`;
        const kpis = document.getElementById("quant-expr-kpis");
        if (kpis) kpis.hidden = true;
        await paintExprCharts({ series: [], cs_ic_path: [], cs_note: "求值失败" });
        return;
      }
      const msg = `${data.code || code} · 前瞻 ${data.horizon_days || horizon} 日`;
      if (sum) sum.innerHTML = `<span class="quant-pro-status-chip" data-state="ok">完成</span> <span class="quant-rem-status-msg">${escapeHtml(msg)}</span>`;
      paintExprKpis(data);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await paintExprCharts(data);
    } catch (e) {
      if (sum) sum.innerHTML = `<span class="quant-pro-status-chip" data-state="error">异常</span> <span class="quant-rem-status-msg">${escapeHtml(String(e.message || e))}</span>`;
    }
  }

  // ---- 实验记录 ----
  async function loadExperiments() {
    const status = document.getElementById("quant-experiments-status");
    const table = document.getElementById("quant-experiments-table");
    const btn = document.getElementById("quant-experiments-refresh");
    const mt = (document.getElementById("quant-experiments-model-type") || {}).value || "";
    const started = Date.now();
    if (btn) {
      btn.disabled = true;
      btn.textContent = "刷新中";
    }
    if (status) status.textContent = "加载中…";
    const releaseBtn = async () => {
      const wait = 400 - (Date.now() - started);
      if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
      if (btn) {
        btn.disabled = false;
        btn.textContent = "刷新";
      }
    };
    try {
      const res = await fetch(`/api/quant/experiments?model_type=${encodeURIComponent(mt)}&limit=50`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        if (status) status.textContent = "加载失败";
        return;
      }
      const exps = data.experiments || [];
      const now = new Date();
      const stamp = [now.getHours(), now.getMinutes(), now.getSeconds()]
        .map((n) => String(n).padStart(2, "0"))
        .join(":");
      if (status) status.textContent = `共 ${data.count ?? exps.length} 条 · ${stamp}`;
      if (table) {
        if (!exps.length) {
          table.innerHTML = '<p class="quant-experiments-empty">暂无实验记录</p>';
        } else {
          const rows = exps.map(e => {
            const m = e.metrics || {};
            const ic =
              m.oos_cs_ic != null
                ? m.oos_cs_ic
                : m.oos_ic != null
                  ? m.oos_ic
                  : m.ic != null
                    ? m.ic
                    : "—";
            const fitEnd = m.fit_end || (e.config || {}).fit_end || "—";
            const win = [m.research_window, m.live_window].filter(Boolean).join("/") || "—";
            return `<tr><td style="max-width:180px;word-break:break-all;">${escapeHtml(String(e.experiment_id || "").slice(0, 24))}…</td><td>${escapeHtml(e.model_type)}</td><td>${escapeHtml(String(e.status))}</td><td>${escapeHtml(String(fitEnd))}</td><td>${escapeHtml(String(ic))}</td><td>${escapeHtml(String(win))}</td><td style="font-size:11px;">${escapeHtml(String(e.created_at || "").slice(5, 16))}</td></tr>`;
          }).join("");
          table.innerHTML = `<table class="quant-weight-table" style="font-size:12px;"><thead><tr><th>ID</th><th>模型</th><th>状态</th><th>fit_end</th><th>IC</th><th>窗</th><th>时间</th></tr></thead><tbody>${rows}</tbody></table>`;
        }
      }
    } catch (e) {
      if (status) status.textContent = `异常: ${String(e.message || e)}`;
    } finally {
      await releaseBtn();
    }
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
    try {
      if (els.quantMeta) els.quantMeta.textContent = "加载面板…";
      // 先拉研究默认 horizon（memory）与 OLS 标的列表，再跑 IC/OLS/回测
      await loadPrefsHorizon().catch(() => {});
      await cluster.fillExprCodeSelect().catch(() => {});
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
            .catch(() => {})
        );
        // 做 T 快照可能很大：后台恢复，不挡「跑回测」
        watchingP
          .catch(() => {})
          .then(() => backtest.restoreLastT0Backtest?.())
          .catch(() => {});
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
        setQuantMeta("全局 ŷ_oo · Ridge / OLS");
      }
      if (useDialog) els.quantDialog.showModal();
      // 运维/因子/桥接等后台拉取：不阻塞 tab 加载态
      const background = [];
      if (hasStrategy || els.quantFactorList) {
        background.push(suggest.loadFactorPanel().catch(() => {}));
      }
      if (hasReplay && page !== "replay") {
        background.push(backtest.loadLastBacktestSnapshot().catch(() => {}));
      }
      // 研究枢纽：拉取全局 ŷ live/草稿状态；日线/分钟条仍由各自 UI 拉
      if (page === "quant" && (els.quantFactorList || els.quantOlsSummary)) {
        background.push(suggest.refreshReturnModelStatus().catch(() => {}));
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










  on("quant-factor-run", "click", async (e) => {
    e.preventDefault();
    try {
      await suggest.runFactorIcSuggest();
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-return-model-fit", "click", async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    if (btn) btn.disabled = true;
    try {
      await suggest.runReturnModelFit();
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-return-model-promote", "click", async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    const role = (btn && btn.getAttribute("data-persist-role")) || "live";
    if (btn) btn.disabled = true;
    try {
      await suggest.runReturnModelPromote(role);
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
      try {
        await suggest.refreshReturnModelStatus();
      } catch (_) {
        /* ignore */
      }
    }
  });

  on("quant-return-model-persist-research", "click", async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    if (btn) btn.disabled = true;
    try {
      await suggest.runReturnModelPromote("research");
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
      try {
        await suggest.refreshReturnModelStatus();
      } catch (_) {
        /* ignore */
      }
    }
  });

  on("quant-return-model-status", "click", async (e) => {
    e.preventDefault();
    try {
      await suggest.refreshReturnModelStatus();
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
    }
  });

  const oosGateTips = createScoreTooltipController();

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

  async function runTauRidge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-tau-summary");
    const persistBtn = document.getElementById("quant-tau-ridge-persist");
    const runBtn = document.getElementById("quant-tau-ridge-run");
    const roleLabel = persistRole === "research" ? "研究套" : "执行套";
    if (runBtn) runBtn.disabled = true;
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? `强制写入上次拟合（${roleLabel}）…`
          : `写入上次拟合（${roleLabel}）…`
        : "ŷ_τc Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/tc-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("tc"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("tc"),
          include_alpha158: true,
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
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "tau-ridge",
        modelPath: "/api/quant/tc-ridge/model",
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
      const a158Bit = data.include_alpha158
        ? `Alpha158×${data.n_alpha158_features != null ? data.n_alpha158_features : "?"}`
        : "无 Alpha158";
      const painted = paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : [a158Bit, "未写盘"].filter(Boolean).join(" · "),
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
        message: describeNetworkFetchError(err),
        error: true,
      });
      throw err;
    } finally {
      if (runBtn) runBtn.disabled = false;
    }
  }

  async function runCoRidge({ persist = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-co-summary");
    const runBtn = document.getElementById("quant-co-ridge-run");
    const roleLabel = persistRole === "research" ? "研究套" : "执行套";
    if (runBtn) runBtn.disabled = true;
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? `写入上次拟合（${roleLabel}）…`
        : "ŷ_co Ridge + 隔夜缺口面板…",
      busy: true,
    });
    try {
            const res = await fetch("/api/quant/co-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("co"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          holdout_trading_days: readHoldoutTradingDays("co"),
          persist: !!persist,
          persist_role: persistRole || "live",
          note: persist ? `ui co promote ${persistRole}` : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "co-ridge",
        modelPath: "/api/quant/co-ridge/model",
        sumEl: sum,
        label: "ŷ_co",
      });
      const data = awaited.data || {};
      const httpOk = awaited.httpOk;
      if (!httpOk || !data.success) {
        const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
        renderRemStatus(sum, {
          state: "error",
          chip: "失败",
          message: String(err),
          error: true,
        });
        setQuantMeta(`ŷ_co 失败 · ${err}`, { error: true });
        clearCoResultBox();
        renderCoCoefTable(null);
        return;
      }
      let status = data;
      try {
        const stRes = await fetch("/api/quant/co-ridge/model");
        const stData = await stRes.json().catch(() => ({}));
        if (stData && typeof stData === "object") {
          status = { ...stData, ...data };
        }
      } catch (_) {}
      const flags = ridgeDiskFlags(status, {
        persistOk: !!persist,
        persistRole,
      });
      let message;
      if (persist) {
        message = flags.liveOn
          ? "已落盘"
          : flags.researchOn
            ? "研究套已落盘 · 执行未写"
            : "";
      } else {
        message =
          flags.liveOn || flags.researchOn
            ? "草稿已更新 · 可再启用研究/执行"
            : "草稿已存 · 可启用研究/执行";
      }
      const oos = data.oos || status.oos || {};
      paintRidgeEnableStatus(sum, status, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message,
        oos,
        sampleCount: data.sample_count,
        fittedAt: data.fitted_at || status.fitted_at || null,
      });
      const rm = data.return_model || status.return_model || {};
      clearCoResultBox();
      await renderCoCoefTable(rm, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      setQuantMeta(`ŷ_co 失败 · ${err.message || err}`, { error: true });
      throw err;
    } finally {
      if (runBtn) runBtn.disabled = false;
    }
  }

  async function runOoRank({ persist = false } = {}) {
    const sum = document.getElementById("quant-oo-rank-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist ? "写入影子模型…" : "LambdaRank + Holdout OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/oo-rank", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("oo_rank"),
          watching_limit: 1000,
          holdout_trading_days: Math.max(20, readHoldoutTradingDays("oo_rank")),
          topk_track: 10,
          ndcg_k: 10,
          l2: 1.0,
          persist: !!persist,
          backend: "lambdarank",
          watching_tier_a_only: readOoRankTierAOnly(),
          note: persist ? "ui oo_rank shadow persist" : "",
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
        clearOoRankResultBox();
        renderOoRankCoefTable(null);
        return;
      }
      await paintOoRankDesk(sum, data, {
        persistOk: !!persist,
        justFitted: !persist,
        message: persist ? "已落盘影子 · 成交 rank=1..n · 不进买序" : "刷新仍保留上次拟合",
      });
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

  async function runT30Ridge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-t30-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τ30 logistic Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t30-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t30"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          minute_period: "5",
          holdout_trading_days: readHoldoutTradingDays("t30"),
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
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : "未写盘",
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
      paintRidgeEnableStatus(sum, data, { gate });
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

  async function runT45Ridge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-t45-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τ45 logistic Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t45-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t45"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          minute_period: "5",
          holdout_trading_days: readHoldoutTradingDays("t45"),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui t45 force promote ${persistRole}`
              : `ui t45 promote ${persistRole}`
            : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "t45-ridge",
        modelPath: "/api/quant/t45-ridge/model",
        sumEl: sum,
        label: "ŷ_τ45",
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
          clearT45ResultBox();
          renderT45CoefTable(null);
        }
        syncT45PersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : "未写盘",
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncT45PersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderT45FitSummary(data);
      await renderT45CoefTable(rm, {
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

  on("quant-t45-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT45Ridge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-t45-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-t45-ridge-persist", "click", onT45PersistClick);
  on("quant-t45-ridge-persist-research", "click", onT45PersistClick);
  async function onT45PersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _t45PromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_τ45 研究套（t45_ridge_model_research.json）"
        : "ŷ_τ45 执行套（t45_ridge_model.json；做 T 旁路，不改 C_τ）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_τ45 写入 ${target}？不进 ranking、不改 C_τ。`)) {
      return;
    }
    try {
      await runT45Ridge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-t45-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  }

  on("quant-t45-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-t45-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t45-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncT45PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT45ResultBox();
        await renderT45CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT45PersistBtn(gate, { hasReport: true });
      clearT45ResultBox();
      await renderT45CoefTable(data.return_model || {}, {
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
        : "ŷ_τ60 logistic Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t60-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t60"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          minute_period: "5",
          holdout_trading_days: readHoldoutTradingDays("t60"),
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
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : "未写盘",
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
      paintRidgeEnableStatus(sum, data, { gate });
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
  async function runT75Ridge({ persist = false, forcePromote = false, persistRole = "live" } = {}) {
    const sum = document.getElementById("quant-t75-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: persist ? "写入中" : "拟合中",
      message: persist
        ? forcePromote
          ? "强制写入上次拟合…"
          : "写入上次拟合…"
        : "ŷ_τ75 logistic Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t75-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t75"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          minute_period: "5",
          holdout_trading_days: readHoldoutTradingDays("t75"),
          persist: !!persist,
          persist_role: persistRole || "live",
          force_promote: !!forcePromote,
          note: persist
            ? forcePromote
              ? `ui t75 force promote ${persistRole}`
              : `ui t75 promote ${persistRole}`
            : "",
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const awaited = await awaitRidgeFitIfBackground(res, posted, {
        persist: !!persist,
        jobName: "t75-ridge",
        modelPath: "/api/quant/t75-ridge/model",
        sumEl: sum,
        label: "ŷ_τ75",
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
          clearT75ResultBox();
          renderT75CoefTable(null);
        }
        syncT75PersistBtn(gate, { hasReport: true });
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : "未写盘",
        oos,
        sampleCount: data.sample_count,
        promotedAt: persist
          ? data.promoted_at || (data.persisted && data.persisted.promoted_at)
          : data.promoted_at,
      });
      syncT75PersistBtn(gate, { hasReport: true });
      const rm = data.return_model || {};
      renderT75FitSummary(data);
      await renderT75CoefTable(rm, {
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

  on("quant-t75-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT75Ridge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-t75-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-t75-ridge-persist", "click", onT75PersistClick);
  on("quant-t75-ridge-persist-research", "click", onT75PersistClick);
  async function onT75PersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const gate = _t75PromoteGate;
    const target =
      persistRole === "research"
        ? "ŷ_τ75 研究套（t75_ridge_model_research.json）"
        : "ŷ_τ75 执行套（t75_ridge_model.json；做 T 旁路，不改 C_τ）";
    let forcePromote = false;
    if (gate && gate.ok === false) {
      const blockers = (gate.blockers || []).join("；") || "未过 OOS 闸";
      if (!window.confirm(`promote 未过闸：${blockers}\n\n仍强制写入 ${target} 吗？`)) {
        return;
      }
      forcePromote = true;
    } else if (!window.confirm(`将 ŷ_τ75 写入 ${target}？不进 ranking、不改 C_τ。`)) {
      return;
    }
    try {
      await runT75Ridge({ persist: true, forcePromote, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-t75-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  }

  on("quant-t75-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-t75-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "live 模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t75-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        syncT75PersistBtn(data.promote_gate, {
          hasReport: !!data.last_report_exists,
        });
        clearT75ResultBox();
        await renderT75CoefTable(null);
        return;
      }
      const oos = data.oos || {};
      const gate = data.promote_gate || null;
      paintRidgeEnableStatus(sum, data, { gate });
      syncT75PersistBtn(gate, { hasReport: true });
      clearT75ResultBox();
      await renderT75CoefTable(data.return_model || {}, {
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
        : "ŷ_τ90 logistic Ridge + 时间 OOS…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/t90-ridge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: readFitLookbackDays("t90"),
          watching_limit: readWatchingLimit(),
          ridge_lambda: 1.0,
          minute_period: "5",
          holdout_trading_days: readHoldoutTradingDays("t90"),
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
      paintRidgeEnableStatus(sum, data, {
        persistOk: !!persist,
        persistRole,
        justFitted: !persist,
        message: persist ? "" : "未写盘",
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
      paintRidgeEnableStatus(sum, data, { gate });
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
        message: describeNetworkFetchError(err),
        error: true,
      });
    }
  });

  on("quant-oo-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runOoTree();
    } catch (err) {
      const sum = document.getElementById("quant-oo-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderOoTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-oo-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadOoTreeLast();
    } catch (err) {
      renderOoTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-co-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runCoTree();
    } catch (err) {
      const sum = document.getElementById("quant-co-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderCoTreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-co-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadCoTreeLast();
    } catch (err) {
      const sum = document.getElementById("quant-co-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderCoTreeCompare({
        success: false,
        error: String(err.message || err),
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

  on("quant-t45-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT45Tree();
    } catch (err) {
      const sum = document.getElementById("quant-t45-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderT45TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t45-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadT45TreeLast();
    } catch (err) {
      renderT45TreeCompare({
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
  on("quant-t75-tree-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runT75Tree();
    } catch (err) {
      const sum = document.getElementById("quant-t75-tree-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
      renderT75TreeCompare({
        success: false,
        error: String(err.message || err),
      });
    }
  });

  on("quant-t75-tree-status", "click", async (e) => {
    e.preventDefault();
    try {
      await loadT75TreeLast();
    } catch (err) {
      renderT75TreeCompare({
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
        ? "ŷ_τc 研究套（tau_ridge_model_research.json，供历史回测）"
        : "ŷ_τc 执行套（tau_ridge_model.json，供交易执行）";
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
    } else if (!window.confirm(`将 ŷ_τc 写入 ${target}？不改 ŷ_oo、不改聚类。`)) {
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

  on("quant-co-ridge-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runCoRidge({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-co-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-co-ridge-persist", "click", onCoPersistClick);
  on("quant-co-ridge-persist-research", "click", onCoPersistClick);
  async function onCoPersistClick(e) {
    e.preventDefault();
    const persistRole = (e.currentTarget && e.currentTarget.dataset.persistRole) || "live";
    const target =
      persistRole === "research"
        ? "ŷ_co 研究套（co_ridge_model_research.json，供历史回测）"
        : "ŷ_co 执行套（co_ridge_model.json，供交易执行）";
    if (!window.confirm(`将 on / ŷ_co 写入 ${target}？不改 ŷ_oo / ŷ_τc、不进主排序。`)) {
      return;
    }
    try {
      await runCoRidge({ persist: true, persistRole });
    } catch (err) {
      const sum = document.getElementById("quant-co-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  }

  on("quant-co-ridge-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-co-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "读取研究 / 执行 / 上次拟合…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/co-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "拟合后看系数 · 再启用研究/执行",
        });
        clearCoResultBox();
        await renderCoCoefTable(null);
        return;
      }
      const oos = data.oos || {};
      paintRidgeEnableStatus(sum, data);
      clearCoResultBox();
      await renderCoCoefTable(data.return_model || {}, { oos });
    } catch (err) {
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-oo-rank-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runOoRank({ persist: false });
    } catch (err) {
      const sum = document.getElementById("quant-oo-rank-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-oo-rank-persist", "click", async (e) => {
    e.preventDefault();
    if (
      !window.confirm(
        "将 ŷ_oo_rank 写入模型文件？成交明细 rank 列为当日截面 1..n 名次（1=最高）；不进 ranking/买序；入场闸在回测「其他」勾选 rank < 30。"
      )
    ) {
      return;
    }
    try {
      await runOoRank({ persist: true });
    } catch (err) {
      const sum = document.getElementById("quant-oo-rank-summary");
      renderRemStatus(sum, {
        state: "error",
        chip: "失败",
        message: String(err.message || err),
        error: true,
      });
    }
  });

  on("quant-oo-rank-status", "click", async (e) => {
    e.preventDefault();
    const sum = document.getElementById("quant-oo-rank-summary");
    renderRemStatus(sum, {
      state: "busy",
      chip: "读取中",
      message: "影子模型…",
      busy: true,
    });
    try {
      const res = await fetch("/api/quant/oo-rank/model");
      const data = await res.json().catch(() => ({}));
      await paintOoRankDesk(sum, data);
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
      const res = await fetch("/api/quant/tc-ridge/model");
      const data = await res.json().catch(() => ({}));
      if (!data.exists) {
        const painted = paintRidgeEnableStatus(sum, data, {
          idleMessage: data.note || "尚无 live 模型",
        });
        if (!data.research_exists && !data.live_model_present) {
          syncOverviewTau("未启用", "ŷ_τc 模型缺失", "text");
        } else {
          syncOverviewTau(painted.chip, data.promoted_at || "ŷ_τc 模型", "text");
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
        syncOverviewTau(chip, data.promoted_at || "ŷ_τc 模型", "text");
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

  on("quant-portfolio-run", "click", async (e) => {
    e.preventDefault();
    try {
      await backtest.runPortfolioBacktest();
    } catch (err) {
      els.quantPortfolioSummary.textContent = String(err.message || err);
      backtest.paintPortfolioChart([], "回测失败");
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
    if (typeof window.__quantlabOpenAi === "function") {
      window.__quantlabOpenAi(q);
      setTimeout(() => {
        document.getElementById("ai-drawer-form")?.requestSubmit();
      }, 40);
    }
  });

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
    if (!sec) return;
    const fold = sec.querySelector(
      "details.quant-secondary-fold, details.quant-primary-fold"
    );
    if (fold) fold.open = true;
  };
  openSecondaryByHash();
  window.addEventListener("hashchange", openSecondaryByHash);

  on("quant-feedback-suggest", "click", async (e) => {
    e.preventDefault();
    try {
      if (els.quantMeta) els.quantMeta.textContent = "生成配置反馈中…";
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
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  });

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
    window.__quantlabGetCurrentStock = () => {
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

  // DSL 表达式因子 & 实验记录 事件绑定（页面加载即绑定，不依赖 openQuantDialog）
  const _exprBtn = document.getElementById("quant-expr-run");
  if (_exprBtn) _exprBtn.addEventListener("click", runExprEval);
  const syncExprExampleActive = () => {
    const input = document.getElementById("quant-expr-text");
    const current = String((input && input.value) || "").replace(/\s+/g, "");
    document.querySelectorAll(".quant-expr-example").forEach((btn) => {
      const expr = String(btn.getAttribute("data-expr") || "").replace(/\s+/g, "");
      const on = !!expr && expr === current;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  };
  document.querySelectorAll(".quant-expr-example").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById("quant-expr-text");
      const expr = btn.getAttribute("data-expr") || "";
      if (input && expr) input.value = expr;
      syncExprExampleActive();
      runExprEval();
    });
  });
  const _exprText = document.getElementById("quant-expr-text");
  if (_exprText) _exprText.addEventListener("input", syncExprExampleActive);
  syncExprExampleActive();
  const _expRefresh = document.getElementById("quant-experiments-refresh");
  if (_expRefresh) _expRefresh.addEventListener("click", loadExperiments);
  const _expMt = document.getElementById("quant-experiments-model-type");
  if (_expMt) _expMt.addEventListener("change", loadExperiments);
  // quant 页面自动加载实验列表
  if (document.body.dataset.page === "quant") loadExperiments().catch(() => {});

  if (document.body.dataset.page === "quant") {
    const auto = new URLSearchParams(location.search).get("auto");
    openQuantDialog({
      autoBacktest: auto === "backtest",
    });
  }
}
