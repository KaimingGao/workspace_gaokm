/**
 * 探针 picker / 摘要行 HTML 与异质汇总（纯字符串）。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

export const PROBE_EMPTY_CLUSTER_FAILED =
  `<p class="watching-table-empty">分组未完成或失败，无法对照</p>`;
export const PROBE_EMPTY_NO_CLUSTER =
  `<p class="watching-table-empty">尚无分组结果 · 请先点「跑分组」</p>`;
export const PROBE_EMPTY_COMPARE_FAILED =
  `<p class="watching-table-empty">对照失败</p>`;
export const PROBE_PICKER_EMPTY =
  `<span class="quant-probe-picker-empty">分组后可选</span>`;

export function probeStatusBadge(kind, text, escapeHtml = defaultEscapeHtml) {
  const esc = escapeHtml;
  const k = kind ? ` is-${kind}` : "";
  return `<span class="quant-probe-badge${k}">${esc(String(text))}</span>`;
}

export function probePickerIdentityHtml(row, escapeHtml = defaultEscapeHtml) {
  const esc = escapeHtml;
  const code = (row && row.code) || "";
  const rawName = String((row && row.name) || "").trim();
  // 名=代码时不当作股票名展示，避免两行都是 300750
  const name =
    rawName && rawName !== code && !/^\d{6}$/.test(rawName) ? rawName : "";
  const title = name || code || "";
  return (
    `<span class="quant-probe-picker-identity">` +
    `<span class="quant-probe-picker-name">${esc(title)}</span>` +
    (name
      ? `<span class="quant-probe-picker-code">${esc(code)}</span>`
      : "") +
    `</span>`
  );
}

export function probePickerTriggerHtml(row, escapeHtml = defaultEscapeHtml) {
  if (!row || !row.code) return PROBE_PICKER_EMPTY;
  const esc = escapeHtml;
  const group = row.group || "—";
  return (
    probePickerIdentityHtml(row, esc) +
    `<span class="quant-probe-picker-group">${esc(group)}</span>` +
    `<span class="quant-probe-picker-caret" aria-hidden="true"></span>`
  );
}

/**
 * @param {Array<{flag?: boolean, dB?: number|null}>|null|undefined} probeRows
 */
export function summarizeProbeHeterogeneity(probeRows) {
  const rows = Array.isArray(probeRows) ? probeRows : [];
  const heteroN = rows.filter((r) => r && r.flag).length;
  const maxAbsDb = rows.reduce((acc, r) => {
    if (r && r.dB != null && Number.isFinite(Number(r.dB))) {
      return Math.max(acc, Math.abs(Number(r.dB)));
    }
    return acc;
  }, 0);
  const unfit = heteroN >= 2 || maxAbsDb >= 0.4;
  const detail = unfit
    ? `异质${heteroN} · max|Δβ|=${maxAbsDb.toFixed(2)}`
    : `异质${heteroN}`;
  return { heteroN, maxAbsDb, unfit, detail };
}

export const PROBE_PICKER_EMPTY_ROW =
  `<div class="quant-probe-picker-empty-row">暂无分组成员</div>`;

export function buildProbePickerMenuHtml(rows, currentCode, escapeHtml = defaultEscapeHtml) {
  const esc = escapeHtml;
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return PROBE_PICKER_EMPTY_ROW;
  const head =
    `<div class="quant-probe-picker-head" aria-hidden="true">` +
    `<span>标的</span><span>分组</span>` +
    `</div>`;
  const cur = String(currentCode || "");
  const body = list
    .map((r) => {
      const selected = r.code === cur;
      return (
        `<button type="button" role="option" class="quant-probe-picker-option` +
        `${selected ? " is-selected" : ""}"` +
        ` data-code="${esc(r.code)}"` +
        ` aria-selected="${selected ? "true" : "false"}">` +
        probePickerIdentityHtml(r, esc) +
        `<span class="quant-probe-picker-group">${esc(r.group || "—")}</span>` +
        `</button>`
      );
    })
    .join("");
  return head + body;
}

export function buildProbeReadySummaryHtml(nCl, nIn, escapeHtml = defaultEscapeHtml) {
  const esc = escapeHtml;
  return (
    `${probeStatusBadge("ok", "就绪", esc)} ${esc(String(nCl))} 组 · 入组 ${esc(
      String(nIn)
    )} · 选票后对照`
  );
}

export function buildProbeNotReadySummaryHtml(err, escapeHtml = defaultEscapeHtml) {
  const esc = escapeHtml;
  const msg = err || "上方分组未成功";
  return `${probeStatusBadge("warn", "未就绪", esc)} ${esc(msg)} · 请先跑分组`;
}

export function buildProbeSingletonSummaryHtml(resolved, cl, escapeHtml = defaultEscapeHtml) {
  const esc = escapeHtml;
  const r2g = cl.ols && cl.ols.r_squared != null ? cl.ols.r_squared : "—";
  const badge = cl.outlier_singleton
    ? probeStatusBadge("warn", "离群单票组", esc)
    : probeStatusBadge("muted", "单票组", esc);
  return (
    `${badge} ${esc(resolved)} ∈ ${esc(cl.label || "?")} · 组模型=自身 · R²=${esc(String(r2g))}`
  );
}

export function buildProbeNotInClusterPlainText(resolved) {
  return `已算单票 ${resolved} · 未落入当前任一组（可能不在观察池宇宙/数据不足）`;
}

export function buildProbeHeteroSummaryHtml(
  resolved,
  cl,
  r2s,
  r2g,
  hetero,
  escapeHtml = defaultEscapeHtml
) {
  const esc = escapeHtml;
  const nMem = Number(cl.member_count);
  const memLabel = Number.isFinite(nMem) ? `${nMem}只` : "?只";
  const badge = hetero.unfit
    ? probeStatusBadge("warn", "异质", esc)
    : probeStatusBadge("ok", "可共用", esc);
  return (
    `${badge} ${esc(resolved)} ∈ ${esc(cl.label || "?")} · ${esc(memLabel)} · R² ${esc(
      String(r2s)
    )}/${esc(String(r2g))} · ${esc(hetero.detail)}`
  );
}

export function buildProbeMetaSingleton(resolved) {
  return `探针 · ${resolved} 单票组 · 同源β · 不冲分组表`;
}

export function buildProbeMetaNotInCluster(resolved) {
  return `探针 · ${resolved} 未入组`;
}

export function buildProbeMetaHetero(resolved, cl, unfit) {
  return unfit
    ? `探针 · ${resolved} 相对 ${cl.label || "组"} 异质偏大 · 建议视为离群`
    : `探针对照 · ${resolved} vs ${cl.label || "组"} · 不冲分组表`;
}
