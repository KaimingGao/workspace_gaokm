/**
 * OLS 提示（分组路径已退役）。
 */
import { escapeHtml } from "../shared.js";

export function createOlsUi(deps) {
  const esc = deps.escapeHtml || escapeHtml;

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

  function buildClusterHealthHtml() {
    return `<p class="sub quant-ols-health" id="quant-ols-health-note">分组已退役（cluster_retired）· 请用全局 ŷ_oo</p>`;
  }

  return {
    oosGateTipHtml,
    parseOosGateReason,
    oosGateStatusMeta,
    buildClusterHealthHtml,
  };
}
