/**
 * 权重建议状态条 / 逻辑 tip / 单元格 tip 文案。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

/**
 * @param {object} suggest
 * @param {boolean} [isGroupTsIc]
 */
export function weightSuggestLogicTip(suggest, isGroupTsIc) {
  const p = (suggest && suggest.params) || {};
  const maxD = p.max_delta != null ? p.max_delta : 0.03;
  const olsD = p.ols_delta != null ? p.ols_delta : 0.02;
  const minIc = p.min_ic != null ? p.min_ic : 0.03;
  const minIcir = p.min_icir != null ? p.min_icir : 0.25;
  const minN = p.min_samples != null ? p.min_samples : 8;
  const minBeta = p.min_ols_beta != null ? p.min_ols_beta : 0.05;
  const decay = p.weak_ic_decay != null ? p.weak_ic_decay : 0.015;
  const scaleBeta = !!p.ols_scale_by_beta;
  const scaleCap = p.ols_scale_cap != null ? p.ols_scale_cap : 2;
  const preferOls = !!p.prefer_ols;
  const requireIcir = isGroupTsIc ? false : p.require_icir !== false;
  const olsLine =
    `|β|≥${minBeta}→±${olsD}` +
    (scaleBeta ? `（|β|放大≤${scaleCap}×）` : "") +
    (preferOls ? "；同向强 IC 可加确认步" : "");
  const icLine =
    `n≥${minN}且|IC|≥${minIc}` +
    (requireIcir
      ? `且|ICIR|≥${minIcir}`
      : isGroupTsIc
        ? "（单票组时序 IC，无 ICIR）"
        : "") +
    `→sign(IC)×≤${maxD}` +
    (requireIcir ? "（|ICIR|/0.5 缩放到 0.5～1.5×）" : "");
  const head = "建议权=当前+Δ→组内压缩归一（只读，不写 config）";
  const foot = "零权冻结；非 raw β→权重占比";
  const mid = preferOls
    ? [`① OLS 优先：${olsLine}`, `② 无 β 时 IC：${icLine}`, `③ 弱/未过门槛 IC→−${decay}`]
    : [`① 强 IC：${icLine}`, `② 否则 OLS：${olsLine}`, `③ 弱/未过门槛 IC→−${decay}`];
  return [head, ...mid, foot].join("\n");
}

/**
 * 因子行悬停：rationale / Δ 来源 / 当前→建议 / OLS β。
 * @param {string} name
 * @param {{ cur?: number, sug?: number, delta?: number, ols?: number, deltaSource?: string }|null} row
 * @param {object|null} suggest
 * @param {Record<string, string>|null} [sourceTipMap]
 */
export function factorWeightSuggestCellTip(name, row, suggest, sourceTipMap) {
  const parts = [];
  const rationale = Array.isArray(suggest && suggest.rationale) ? suggest.rationale : [];
  const hit = rationale.find(
    (line) =>
      String(line).startsWith(`${name} `) || String(line).startsWith(`${name}\t`)
  );
  if (hit) parts.push(String(hit));
  else if (row && row.deltaSource) {
    parts.push((sourceTipMap && sourceTipMap[row.deltaSource]) || row.deltaSource);
  } else {
    parts.push("本因子无 Δ（未过门槛、零权冻结，或建议与当前相同）");
  }
  if (row && row.cur != null && row.sug != null) {
    parts.push(`当前 ${Number(row.cur).toFixed(3)} → 建议 ${Number(row.sug).toFixed(3)}`);
  }
  if (row && row.delta != null && Number.isFinite(Number(row.delta))) {
    const d = Number(row.delta);
    parts.push(`Δ=${d > 0 ? "+" : ""}${d.toFixed(3)}`);
  }
  if (row && row.ols != null && Number.isFinite(Number(row.ols))) {
    parts.push(`OLS β=${Number(row.ols).toFixed(4)}`);
  }
  return parts.join("\n");
}

/**
 * @param {object} suggest
 * @param {{
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   parseOosGateReason: (gate: object) => string,
 * }} deps
 */
export function weightSuggestStatusHtml(suggest, deps) {
  const escapeHtml = (deps && deps.escapeHtml) || defaultEscapeHtml;
  const parseOosGateReason = deps && deps.parseOosGateReason;
  if (!suggest || !suggest.success) return "";
  const lines = (suggest.rationale || []).slice(0, 4);
  const gate = suggest.oos_gate || {};
  const gateAttr =
    gate && typeof gate === "object" && Object.keys(gate).length
      ? ` data-oos-gate="${escapeHtml(JSON.stringify(gate))}"`
      : "";
  let gateLine = "";
  if (gate.skipped) {
    gateLine = `<span class="sub has-oos-tip"${gateAttr}>OOS 门禁：已跳过（${escapeHtml(
      parseOosGateReason(gate)
    )}）· promote_ready=否</span>`;
  } else if (gate.ok && gate.passed) {
    const d = gate.delta_oos_pp != null ? ` · ΔOOS ${gate.delta_oos_pp}pp` : "";
    const ex =
      gate.delta_excess_pp != null && gate.delta_excess_pp !== ""
        ? ` · Δ超额 ${gate.delta_excess_pp}pp`
        : "";
    gateLine = `<span class="sub up has-oos-tip"${gateAttr}>OOS 门禁：通过${escapeHtml(
      d + ex
    )} · 仍须人审</span>`;
  } else if (gate.ok) {
    gateLine = `<span class="sub down has-oos-tip"${gateAttr}>OOS 门禁：未过（${escapeHtml(
      parseOosGateReason(gate)
    )}）· 不建议 promote</span>`;
  }
  const warns = [
    ...((suggest.constraint_warnings || []).slice(0, 2)),
    ...((suggest.redundancy_warnings || []).slice(0, 1)),
  ];
  const warnHtml = warns.length
    ? `<span class="sub">${warns.map((w) => escapeHtml(String(w))).join(" · ")}</span>`
    : "";
  return (
    `<strong>权重建议</strong>（${escapeHtml(String(suggest.ic_mode || "—"))}` +
    `${suggest.promote_ready ? " · promote_ready" : ""}）<br/>` +
    (lines.length ? lines.map((l) => `${escapeHtml(String(l))}<br/>`).join("") : "") +
    (gateLine ? `${gateLine}<br/>` : "") +
    (warnHtml ? `${warnHtml}<br/>` : "") +
    `<span class="sub">${escapeHtml(
      (suggest.config_diff && suggest.config_diff.apply_note) ||
        "导出 diff 可手动合并；不自动写盘"
    )}</span>`
  );
}
