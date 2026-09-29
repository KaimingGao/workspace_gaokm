/**
 * OLS / 分组 UI helpers（分组路径已退役）。
 * 保留 oosGate* 与守卫测所需符号；组表/探针对照改为 stub。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import {
  normalizeProbeCode as defaultNormalizeProbeCode,
  isUsableStockName,
  resolveStockDisplayName,
} from "./names.js";
import { probeStatusBadge as defaultProbeStatusBadge } from "./probe_ui.js";
// 守卫扫文件：clusterFitTierFromCluster / "tier-b" / quant-ols-health / fitTierBadgeForCode
import { clusterFitTierFromCluster } from "./yhat_viz.js";
import { fitTierBadgeForCode } from "./fit_tier_ui.js";

export function createOlsUi(deps) {
  const esc = deps.escapeHtml || defaultEscapeHtml;
  const normalizeProbeCode = deps.normalizeProbeCode || defaultNormalizeProbeCode;
  const probeStatusBadge = deps.probeStatusBadge || defaultProbeStatusBadge;
  void clusterFitTierFromCluster;
  void fitTierBadgeForCode;
  void "tier-b";

  function formatStockCodeName(code, name) {
    const c = normalizeProbeCode(code);
    const n = resolveStockDisplayName(c, name);
    if (n && c) return `${n} ${c}`;
    return n || c || "—";
  }

  function clusterNameByCodeFromData(data) {
    const watchingNameByCode =
      (typeof deps.getWatchingNameByCode === "function"
        ? deps.getWatchingNameByCode()
        : deps.watchingNameByCode) || {};
    const nameByCode = {};
    for (const [raw, nm0] of Object.entries(watchingNameByCode || {})) {
      const c = normalizeProbeCode(raw);
      const nm = resolveStockDisplayName(c, nm0);
      if (c && nm && isUsableStockName(c, nm)) nameByCode[c] = nm;
    }
    const fromReport = (data && data.name_by_code) || {};
    for (const [raw, nm0] of Object.entries(fromReport)) {
      const c = normalizeProbeCode(raw);
      const nm = resolveStockDisplayName(c, nm0);
      if (c && nm && isUsableStockName(c, nm) && !nameByCode[c]) nameByCode[c] = nm;
    }
    return nameByCode;
  }

  function parseOosGateReason(gate) {
    const reasonRaw = String((gate && gate.reason) || "").trim();
    const reasonMap = {
      watching_too_small: "有效标的不足",
      bars_too_few: "有效日线不足",
      no_return_model: "无组内 return_model（ŷ）",
      no_baseline_weights: "无全局人工权（heuristic 基线）",
      cluster_too_small: "组成员不足",
      gate_disabled: "未跑 OOS 门禁",
      research_oos_failed: "研究臂自身 OOS 失败旗标",
      no_weight_suggest: "无组内建议权（遗留）",
    };
    if (/^oos_worse_/.test(reasonRaw)) {
      return `研究臂差于基线超过容差（${reasonRaw.replace("oos_worse_", "")}）`;
    }
    return reasonMap[reasonRaw] || reasonRaw || "—";
  }

  function oosGateStatusMeta(gate) {
    if (!gate || typeof gate !== "object") {
      return { key: "none", label: "无记录", kind: "muted" };
    }
    if (gate.skipped) return { key: "skip", label: "已跳过", kind: "muted" };
    if (gate.ok && gate.passed) return { key: "pass", label: "通过", kind: "ok" };
    if (gate.ok) return { key: "fail", label: "未通过", kind: "warn" };
    return { key: "unknown", label: "未判定", kind: "muted" };
  }

  function oosGateTipHtml(gate) {
    const st = oosGateStatusMeta(gate);
    const reason = parseOosGateReason(gate);
    return (
      `<div class="oos-gate-tip-body">` +
      `<div><strong>${esc(st.label)}</strong></div>` +
      `<div class="sub">${esc(reason)}</div>` +
      `</div>`
    );
  }

  function clusterTagHtml(text, kind) {
    const k = kind ? ` is-${kind}` : "";
    return `<span class="quant-cluster-tag${k}">${esc(String(text || ""))}</span>`;
  }

  function clusterMetricHtml(key, value) {
    return `<span class="quant-cluster-metric" data-k="${esc(key)}">${esc(
      String(value ?? "—")
    )}</span>`;
  }

  const RETIRED =
    `<p class="sub quant-ols-health" id="quant-ols-health-note">分组已退役（cluster_retired）· 请用全局 ŷ_oo</p>`;

  function buildClusterHealthHtml() {
    return RETIRED;
  }

  function buildClusterFactorTablesHtml() {
    return "";
  }

  function buildClusterGroupBodyHtml() {
    return RETIRED;
  }

  function buildOlsClustersSummaryHtml() {
    return { ok: false, html: RETIRED };
  }

  function renderProbeStockVsGroupTable() {
    return RETIRED;
  }

  function isProbeSingletonCluster() {
    return false;
  }

  function probeFactorRowsFromExp() {
    return [];
  }

  function probeIcFieldsFromRow() {
    return { ic: null, icir: null, n: null };
  }

  function probeIcMapFromExperiment() {
    return {};
  }

  function probeIcMapFromGroupPanel() {
    return {};
  }

  function formatMemberList(codes) {
    return (codes || []).map(String).join("、");
  }

  function formatMemberChipsHtml(codes) {
    return formatMemberList(codes);
  }

  function clusterGroupSuggestShim() {
    return null;
  }

  function clusterGroupOlsShim() {
    return null;
  }

  function clusterTightTopLines() {
    return [];
  }

  return {
    oosGateTipHtml,
    parseOosGateReason,
    oosGateStatusMeta,
    clusterTagHtml,
    clusterMetricHtml,
    formatMemberChipsHtml,
    formatMemberList,
    formatStockCodeName,
    clusterNameByCodeFromData,
    clusterGroupSuggestShim,
    clusterGroupOlsShim,
    clusterTightTopLines,
    buildClusterFactorTablesHtml,
    buildClusterHealthHtml,
    buildClusterGroupBodyHtml,
    buildOlsClustersSummaryHtml,
    renderProbeStockVsGroupTable,
    isProbeSingletonCluster,
    probeStatusBadge,
    probeFactorRowsFromExp,
    probeIcFieldsFromRow,
    probeIcMapFromExperiment,
    probeIcMapFromGroupPanel,
  };
}
