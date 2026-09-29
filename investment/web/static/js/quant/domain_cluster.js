import { downloadJson } from "../shared.js";
import { normalizeProbeCode, isUsableStockName, resolveStockDisplayName } from "./names.js";
import { formatClusterApiError } from "./cluster_api.js";
import { clusterLandingHtml } from "./cluster_landing.js?v=p2253";
import { createScoreTooltipController } from "../score_tooltip.js?v=p2531";
import { syncOverviewLanding } from "./factor_corr_ui.js";
import {
  probePickerTriggerHtml,
  buildProbePickerMenuHtml,
  openProbeFold,
} from "./probe_ui.js";

const RETIRED_MSG = "分组已退役（cluster_retired）· 请用全局 ŷ_oo / factor-ols";

/** Quant domain: cluster（分组 OLS / live 已退役；保留横截面 + 探针外壳） */
export function installClusterProbe(q) {
  const {
    els,
    state,
    escapeHtml,
    setQuantMeta,
    setBusyText,
    readHorizonDays,
    buildCrossSectionResult,
    oosGateTipHtml,
    oosGateStatusMeta,
    probeFactorRowsFromExp,
    probeIcFieldsFromRow,
    probeIcMapFromExperiment,
    probeIcMapFromGroupPanel,
    probeStatusBadge,
  } = q;
  const oosGateTips = createScoreTooltipController();

  function paintClusterHealth(html) {
    const host = els.quantOlsHealth;
    if (!host) return;
    const body = String(html || "").trim();
    host.innerHTML = body;
    host.hidden = !body;
  }

  function buildClusterHealthHtml() {
    return `<p class="sub">${escapeHtml(RETIRED_MSG)}</p>`;
  }

  async function bootstrapClusterHub() {
    if (bootstrapClusterHub._running) return;
    bootstrapClusterHub._running = true;
    try {
      setBusyText(els.quantOlsSummary, RETIRED_MSG, { busy: false });
      if (els.quantOlsClusters) {
        els.quantOlsClusters.hidden = false;
        els.quantOlsClusters.innerHTML = `<p class="sub">${escapeHtml(RETIRED_MSG)}</p>`;
      }
      paintClusterHealth(buildClusterHealthHtml());
      if (els.quantProbeSummary) {
        setBusyText(els.quantProbeSummary, RETIRED_MSG, { busy: false });
      }
      try {
        syncOverviewLanding({ cluster_retired: true });
      } catch (_) {
        /* ignore */
      }
    } finally {
      bootstrapClusterHub._running = false;
    }
  }

  function applyProbePickerSelection(code, { silent } = {}) {
    const hidden = document.getElementById("quant-ols-code");
    const trigger = document.getElementById("quant-ols-code-trigger");
    if (!hidden || !trigger) return;
    const c = normalizeProbeCode(code);
    const row =
      (state.probePickerRows || []).find((r) => r.code === c) ||
      (c ? { code: c, name: "", group: "" } : null);
    hidden.value = row && row.code ? row.code : "";
    trigger.innerHTML = probePickerTriggerHtml(row);
    setProbePickerOpen(false);
    if (!silent && hidden.value) {
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function setProbePickerOpen(open) {
    const root = document.getElementById("quant-probe-picker");
    const trigger = document.getElementById("quant-ols-code-trigger");
    const menu = document.getElementById("quant-ols-code-menu");
    if (!root || !trigger || !menu) return;
    root.classList.toggle("is-open", !!open);
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    menu.hidden = !open;
  }

  function renderProbePickerMenu() {
    const menu = document.getElementById("quant-ols-code-menu");
    if (!menu) return;
    menu.innerHTML = buildProbePickerMenuHtml(
      state.probePickerRows || [],
      document.getElementById("quant-ols-code")?.value || ""
    );
  }

  function bindProbePicker() {
    const root = document.getElementById("quant-probe-picker");
    const trigger = document.getElementById("quant-ols-code-trigger");
    const menu = document.getElementById("quant-ols-code-menu");
    if (!root || !trigger || !menu || root._probePickerBound) return;
    root._probePickerBound = true;
    trigger.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setProbePickerOpen(!root.classList.contains("is-open"));
    });
    menu.addEventListener("click", (e) => {
      const opt = e.target && e.target.closest("[data-code]");
      if (!opt) return;
      e.preventDefault();
      applyProbePickerSelection(opt.getAttribute("data-code"));
    });
    document.addEventListener("click", (e) => {
      if (!root.contains(e.target)) setProbePickerOpen(false);
    });
  }

  function fillProbeCodeSelect(rows, preferredCode) {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return;
    bindProbePicker();
    state.probePickerRows = (rows || []).map((r) => {
      const code = normalizeProbeCode(r.code) || String(r.code || "").trim();
      const name = resolveStockDisplayName(
        code,
        r.name,
        state.watchingNameByCode?.[code]
      );
      return { code, name, group: String(r.group || "").trim() };
    });
    renderProbePickerMenu();
    const pref = normalizeProbeCode(preferredCode || hidden.value || "");
    const hit = state.probePickerRows.find((r) => r.code === pref);
    applyProbePickerSelection(hit ? hit.code : state.probePickerRows[0]?.code || "", {
      silent: true,
    });
  }

  async function hydrateWatchingNamesFromApi() {
    try {
      const res = await fetch("/api/watching");
      const data = await res.json();
      const uni = (data && data.watching) || data || {};
      const wl = uni.watchlist || data.watchlist || [];
      const names = Array.isArray(uni.watchlist_names)
        ? uni.watchlist_names
        : Array.isArray(data.watchlist_names)
          ? data.watchlist_names
          : [];
      state.watchingNameByCode = state.watchingNameByCode || {};
      for (let i = 0; i < wl.length; i++) {
        const item = wl[i];
        if (typeof item === "string") {
          const nm = names[i] || "";
          if (isUsableStockName(item, nm)) state.watchingNameByCode[item] = nm;
        } else if (item && typeof item === "object") {
          const code = String(item.code || item.stock_code || "").trim();
          const nm = item.name || item.stock_name || names[i] || "";
          if (code && isUsableStockName(code, nm)) state.watchingNameByCode[code] = nm;
        }
      }
      return wl;
    } catch (_) {
      return [];
    }
  }

  async function populateOlsCodeOptions() {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return [];
    const wl = await hydrateWatchingNamesFromApi();
    const seen = new Set();
    const rows = [];
    const push = (code, name) => {
      const c = normalizeProbeCode(code);
      if (!c || seen.has(c)) return;
      seen.add(c);
      rows.push({ code: c, name: name || state.watchingNameByCode?.[c] || "" });
    };
    for (const item of wl || []) {
      if (typeof item === "string") push(item, "");
      else if (item && typeof item === "object") {
        push(item.code || item.stock_code, item.name || item.stock_name || "");
      }
    }
    fillProbeCodeSelect(rows, hidden.value);
    return rows;
  }

  function resolveProbeInputToCode(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    const bare = normalizeProbeCode(s);
    if (/^\d{6}$/.test(bare)) return bare;
    const map = state.probeSelectValueToCode || {};
    return map[s] || map[bare] || "";
  }

  function readOlsCode() {
    const el = document.getElementById("quant-ols-code");
    const raw = el && el.value != null ? String(el.value).trim() : "";
    if (!raw) return "茅台";
    if (/^\d{6}$/.test(normalizeProbeCode(raw))) return normalizeProbeCode(raw);
    return resolveProbeInputToCode(raw) || "茅台";
  }

  async function refreshCrossSection() {
    if (!els.quantCrossSummary && !els.quantCrossList) return null;
    setBusyText(els.quantCrossSummary, "排序中…", { busy: true });
    if (els.quantCrossList) els.quantCrossList.innerHTML = "";
    const res = await fetch("/api/quant/cross-section", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 10, horizon_days: readHorizonDays() }),
    });
    const data = await res.json();
    renderCrossSection(data);
    return data;
  }

  function renderCrossSection(data) {
    if (!els.quantCrossSummary && !els.quantCrossList) return;
    const result = buildCrossSectionResult(data);
    setBusyText(els.quantCrossSummary, result.summary, { busy: false });
    if (els.quantCrossList) els.quantCrossList.innerHTML = result.listHtml;
  }

  function renderFactorOls(data) {
    if (!els.quantOlsSummary) return;
    if (!data || !data.success) {
      setBusyText(els.quantOlsSummary, (data && data.error) || RETIRED_MSG, {
        busy: false,
      });
      return;
    }
    const n = data.sample_count != null ? data.sample_count : "—";
    setBusyText(
      els.quantOlsSummary,
      `全局 OLS · n=${escapeHtml(String(n))} · 分组路径已退役`,
      { busy: false }
    );
  }

  function renderOlsClusters() {
    paintClusterHealth(buildClusterHealthHtml());
    if (els.quantOlsClusters) {
      els.quantOlsClusters.innerHTML = `<p class="sub">${escapeHtml(RETIRED_MSG)}</p>`;
    }
  }

  async function runProbeStockVsGroup() {
    openProbeFold();
    setBusyText(els.quantProbeSummary, RETIRED_MSG, { busy: false });
    if (els.quantProbeResult) {
      els.quantProbeResult.innerHTML = `<p class="watching-table-empty">${escapeHtml(
        RETIRED_MSG
      )}</p>`;
    }
    setQuantMeta(RETIRED_MSG, { error: true });
  }

  async function postClusterLive(path, body) {
    void path;
    void body;
    setQuantMeta(RETIRED_MSG, { error: true });
    return {
      ok: false,
      error: RETIRED_MSG,
      data: { success: false, error: "cluster_retired", cluster_retired: true },
    };
  }

  async function saveUniverseFitTiers() {
    // 保留符号供守卫测；universe-fit-tiers 已随分组退役
    setQuantMeta(RETIRED_MSG, { error: true });
    await postClusterLive("/api/quant/cluster-live/universe-fit-tiers", {
      universe_fit_tiers: ["A", "B", "C"],
    });
  }

  async function refreshClusterLiveStatus() {
    const host = document.getElementById("quant-cluster-landing");
    if (host) {
      host.innerHTML = clusterLandingHtml({
        cluster_retired: true,
        error: "cluster_retired",
      });
    }
  }

  function onClusterExportClick(e) {
    const btn = e.target?.closest?.("[data-cluster-export]");
    if (!btn) return;
    const key = btn.getAttribute("data-cluster-export");
    e.preventDefault();
    e.stopPropagation();
    if (key === "live-refit") {
      setQuantMeta("分组已退役 · 请用 ŷ_oo「拟合」", { error: true });
      document.getElementById("quant-return-model-fit")?.scrollIntoView({
        block: "nearest",
        behavior: "smooth",
      });
      return;
    }
    if (key === "universe-fit-tiers") {
      saveUniverseFitTiers();
      return;
    }
    setQuantMeta(RETIRED_MSG, { error: true });
  }

  function wireOosGateTips(host) {
    if (!host) return;
    oosGateTips.bindAttrTip(host, {
      selector: "[data-oos-gate]",
      className: "score-tooltip oos-gate-tip",
      buildHtml: (el) => {
        try {
          return oosGateTipHtml(JSON.parse(el.getAttribute("data-oos-gate") || "{}"));
        } catch (_) {
          return "";
        }
      },
    });
  }

  const retiredAsync = async () => {
    setQuantMeta(RETIRED_MSG, { error: true });
  };

  // 打开面板时刷一次退役提示（不阻塞）
  bootstrapClusterHub().catch(() => {});

  return {
    applyProbePickerSelection,
    bindProbePicker,
    bootstrapClusterHub,
    clusterPoolBookAndArtifact: () => ({ book: [], artifact: null }),
    exportClusterWeightDiff: () => setQuantMeta(RETIRED_MSG, { error: true }),
    exportMultiScore: () => setQuantMeta(RETIRED_MSG, { error: true }),
    exportPoolArtifact: () => setQuantMeta(RETIRED_MSG, { error: true }),
    fillProbeCodeSelect,
    findClusterForProbeCode: () => null,
    formatClusterApiError,
    markProbeReadyFromClusters: () => {
      if (els.quantProbeSummary) setBusyText(els.quantProbeSummary, RETIRED_MSG, { busy: false });
    },
    onClusterExportClick,
    oosGateStatusMeta,
    populateOlsCodeOptions,
    postClusterLive,
    postClusterPaperRebalance: async () => ({ ok: false, error: RETIRED_MSG }),
    probeFactorRowsFromExp,
    probeIcFieldsFromRow,
    probeIcMapFromExperiment,
    probeIcMapFromGroupPanel,
    probeStatusBadge,
    readOlsCode,
    refreshClusterLiveStatus,
    refreshCrossSection,
    renderClusterFactorTables: () => false,
    renderCrossSection,
    renderFactorOls,
    renderMergedFactorTable: () => {},
    renderOlsClusters,
    renderProbeFactorTable: () => {},
    renderProbePickerMenu,
    renderProbeStockVsGroupTable: () => {},
    resolveProbeInputToCode,
    runClusterLiveApply: retiredAsync,
    runClusterLiveMode: retiredAsync,
    runClusterLivePromote: retiredAsync,
    runClusterLiveRank: retiredAsync,
    runClusterLiveRollback: retiredAsync,
    runClusterMultiRescore: retiredAsync,
    runClusterPaperApply: retiredAsync,
    runClusterPaperPreview: retiredAsync,
    runProbeStockVsGroup,
    setProbePickerOpen,
    syncProbeCodeOptionsFromClusters: () => {},
    syncProbeCodeOptionsFromClustersAsync: async () => {},
    waitForClusterHubReady: async () => false,
    wireOosGateTips,
    saveUniverseFitTiers,
    paintClusterHealth,
    buildClusterHealthHtml,
    downloadJson,
  };
}
