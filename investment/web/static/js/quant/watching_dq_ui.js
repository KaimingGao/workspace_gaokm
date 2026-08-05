/**
 * 数据中心 · 数据质量折叠 HTML。
 */
import { escapeHtml } from "../shared.js";

/**
 * @param {{
 *   wlLength: number,
 *   paperOk: boolean,
 *   warnings: Array<unknown>,
 * }} ctx
 */
export function buildWatchingDqMetaText({ wlLength, paperOk, warnings }) {
  return (
    `观察 ${wlLength} 只` +
    (paperOk ? "" : " · 纸面缓存不可用") +
    (warnings.length ? ` · 告警 ${warnings.length}` : "")
  );
}

/**
 * @param {{ dq: object, warnings: Array<unknown> }} ctx
 */
export function buildWatchingDqFoldSummary({ dq, warnings }) {
  const fallback = dq && dq.fallback_count != null ? Number(dq.fallback_count) : null;
  const tail = warnings.length
    ? `告警 ${warnings.length} 条`
    : fallback && fallback > 0
      ? `fallback ${fallback}`
      : "良好";
  return `数据质量 · ${tail}`;
}

/**
 * @param {{ dq: object, warnings: Array<unknown> }} ctx
 */
export function buildWatchingDqTableHtml({ dq, warnings }) {
  const warnRows = (warnings || [])
    .slice(0, 8)
    .map((w) => `<tr><td colspan="2">${escapeHtml(String(w))}</td></tr>`)
    .join("");
  return (
    `<table class="quant-weight-table"><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>` +
    `<tr><td>样本数</td><td class="num">${escapeHtml(String(dq.count ?? "—"))}</td></tr>` +
    `<tr><td>fallback</td><td class="num">${escapeHtml(String(dq.fallback_count ?? "—"))}</td></tr>` +
    `<tr><td>gated</td><td class="num">${escapeHtml(String(dq.gated_count ?? "—"))}</td></tr>` +
    `<tr><td>复权策略</td><td>${escapeHtml(String(dq.adjust_policy ?? "—"))}</td></tr>` +
    (warnRows
      ? `<tr><td colspan="2"><strong>健康警告</strong></td></tr>${warnRows}`
      : "") +
    `</tbody></table>` +
    `<p class="quant-attr-note">质量摘要来自最近纸面调仓五问；专用逐票质量 API 后续可接。</p>`
  );
}
