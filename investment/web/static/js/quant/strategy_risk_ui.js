/**
 * 策略中心 · 风控与敞口审计 HTML。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml } from "./research_grid.js";

const SECTOR_COLS = [
  { id: "name", label: "行业", flex: true },
  { id: "weight_pct", label: "权重", widthPct: 22, num: true },
  { id: "count", label: "只数", widthPct: 16, num: true },
  { id: "status", label: "状态", widthPct: 18, center: true },
];

const TRI_COLS = [
  { id: "name", label: "名称", flex: true },
  { id: "weight_pct", label: "权重", widthPct: 28, num: true },
  { id: "count", label: "只数", widthPct: 22, num: true },
];

function sectorCell(col, r, esc) {
  if (col.id === "name") return esc(r.name || "—");
  if (col.id === "weight_pct") return `${esc(String(r.weight_pct ?? "—"))}%`;
  if (col.id === "count") return esc(String(r.count ?? "—"));
  if (col.id === "status") {
    return r.over_limit ? `<span class="down">超限</span>` : "—";
  }
  return "—";
}

function triCell(col, r, esc) {
  if (col.id === "name")
    return esc(r.name || r.origin_label || r.origin || r.label || "—");
  if (col.id === "weight_pct")
    return `${esc(String(r.weight_pct ?? r.pct ?? "—"))}%`;
  if (col.id === "count") return esc(String(r.count ?? "—"));
  return "—";
}

/** @param {{ ops: object, data: object, exposure: object, lim: object }} ctx */
export function buildRiskAuditMetaText({ ops, data, exposure, lim }) {
  const overN = (exposure.over_limit_sectors || []).length;
  return (
    `策略 ${ops.strategy_id || data.strategy_id || "—"} · 成本 ${ops.cost_model || data.cost_model || "—"}` +
    (lim.max_sector_pct != null ? ` · 行业≤${lim.max_sector_pct}%` : "") +
    (overN ? ` · 超限行业 ${overN}` : "")
  );
}

/** @param {{ blocks: Array<unknown>, rbSum: object, exposure: object }} ctx */
export function buildRiskAuditFoldSummary({ blocks, rbSum, exposure }) {
  const bN = Array.isArray(blocks) ? blocks.length : Number(rbSum.block_count || 0);
  const over = (exposure.over_limit_sectors || []).length;
  if (over) {
    return `风控与敞口 · 行业超限 ${over}` + (bN ? ` · 拦截 ${bN}` : "");
  }
  if (bN) return `风控与敞口 · 拦截 ${bN}`;
  return "风控与敞口 · 无超限";
}

/**
 * @param {{
 *   exposure: object,
 *   budget: object,
 *   lim: object,
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 * }} ctx
 */
export function buildSectorExposureHtml({
  exposure,
  budget,
  lim,
  escapeHtml = defaultEscapeHtml,
  researchGridHtml = defaultResearchGridHtml,
}) {
  const sectors = exposure.sectors || [];
  const volNote =
    budget.scale != null
      ? ` · vol_scale ${escapeHtml(String(budget.scale))}`
      : budget.market_vol_scale != null
        ? ` · vol_scale ${escapeHtml(String(budget.market_vol_scale))}`
        : "";
  return (
    `<p class="quant-trades-caption">行业暴露` +
    volNote +
    (lim.max_sector_pct != null
      ? ` · 上限 ${escapeHtml(String(lim.max_sector_pct))}%`
      : "") +
    `</p>` +
    researchGridHtml(
      SECTOR_COLS,
      sectors,
      (col, r) => sectorCell(col, r, escapeHtml),
      {
        emptyText: "暂无行业敞口（空仓或未计价）",
        rowClass: (r) => (r.over_limit ? "is-over-limit" : ""),
      }
    )
  );
}

/**
 * @param {{
 *   exposure: object,
 *   origin: Array<object>,
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 * }} ctx
 */
export function buildStyleExposureHtml({
  exposure,
  origin,
  escapeHtml = defaultEscapeHtml,
  researchGridHtml = defaultResearchGridHtml,
}) {
  const styles = exposure.styles || [];
  const sizeBuckets = exposure.size_buckets || [];
  const triCellFn = (col, r) => triCell(col, r, escapeHtml);
  let html =
    `<p class="quant-trades-caption">板块风格</p>` +
    researchGridHtml(
      TRI_COLS.map((c) => (c.id === "name" ? { ...c, label: "风格" } : c)),
      styles,
      triCellFn,
      { emptyText: "暂无板块风格" }
    );
  if (sizeBuckets.length) {
    html +=
      `<p class="quant-trades-caption">仓位档</p>` +
      researchGridHtml(
        TRI_COLS.map((c) => (c.id === "name" ? { ...c, label: "档位" } : c)),
        sizeBuckets,
        triCellFn
      );
  }
  if (origin.length) {
    html +=
      `<p class="quant-trades-caption">来源暴露</p>` +
      researchGridHtml(
        TRI_COLS.map((c) => (c.id === "name" ? { ...c, label: "来源" } : c)),
        origin,
        triCellFn
      );
  }
  return html;
}

/**
 * @param {{
 *   rbSum: object,
 *   ops: object,
 *   blocksAnnotate: Array<object>,
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 * }} ctx
 */
export function buildRiskEffHtml({
  rbSum,
  ops,
  blocksAnnotate,
  escapeHtml = defaultEscapeHtml,
  researchGridHtml = defaultResearchGridHtml,
}) {
  const byReason = rbSum.by_reason || (ops.north_star && ops.north_star.risk_by_reason) || {};
  const reasonRows = Object.keys(byReason)
    .sort((a, b) => Number(byReason[b]) - Number(byReason[a]))
    .map((k) => ({ code: k, count: byReason[k] }));
  const dayRows = (rbSum.by_day || []).slice(0, 7);
  const eff =
    rbSum.effectiveness_rate != null
      ? `${(Number(rbSum.effectiveness_rate) * 100).toFixed(0)}%`
      : "—";
  const fp =
    rbSum.false_block_rate != null
      ? `${(Number(rbSum.false_block_rate) * 100).toFixed(0)}%`
      : "—";

  let annotateHtml = "";
  if (blocksAnnotate && blocksAnnotate.length) {
    annotateHtml =
      `<p class="quant-trades-caption">标注 outcome（抬有效率）</p>` +
      researchGridHtml(
        [
          { id: "index", label: "#", widthPct: 10, num: true },
          { id: "detail", label: "说明", flex: true },
          { id: "outcome", label: "outcome", widthPct: 16, center: true },
          { id: "act", label: "标注", widthPct: 28, center: true },
        ],
        blocksAnnotate,
        (col, b) => {
          const idx = b.index;
          if (col.id === "index") return escapeHtml(String(idx));
          if (col.id === "detail")
            return escapeHtml(String(b.detail || "").slice(0, 48));
          if (col.id === "outcome")
            return `<code>${escapeHtml(String(b.outcome || "—"))}</code>`;
          if (col.id === "act") {
            return (
              `<button type="button" class="dialog-btn secondary strategy-rb-annotate" data-index="${escapeHtml(
                String(idx)
              )}" data-outcome="true_positive">真拦</button> ` +
              `<button type="button" class="dialog-btn secondary strategy-rb-annotate" data-index="${escapeHtml(
                String(idx)
              )}" data-outcome="false_positive">误拦</button>`
            );
          }
          return "—";
        }
      );
  }

  return (
    `<p class="quant-trades-caption">拦截有效率 · 共 ${escapeHtml(
      String(rbSum.block_count ?? 0)
    )} 条 · 有效 ${escapeHtml(eff)} · 误拦 ${escapeHtml(fp)}` +
    (rbSum.labeled_count != null
      ? ` · 已标注 ${escapeHtml(String(rbSum.labeled_count))}`
      : "") +
    `</p>` +
    (reasonRows.length
      ? researchGridHtml(
          [
            { id: "code", label: "原因码", flex: true },
            { id: "count", label: "次数", widthPct: 28, num: true },
          ],
          reasonRows,
          (col, r) =>
            col.id === "code"
              ? `<code>${escapeHtml(r.code)}</code>`
              : escapeHtml(String(r.count))
        )
      : `<p class="watching-table-empty">暂无按码汇总</p>`) +
    (dayRows.length
      ? `<p class="quant-trades-caption">近七日</p>` +
        researchGridHtml(
          [
            { id: "date", label: "日", flex: true },
            { id: "count", label: "拦截", widthPct: 28, num: true },
          ],
          dayRows,
          (col, d) =>
            col.id === "date"
              ? escapeHtml(d.date || "—")
              : escapeHtml(String(d.count ?? 0))
        )
      : "") +
    annotateHtml +
    (rbSum.note
      ? `<p class="quant-fingerprint">${escapeHtml(String(rbSum.note))}</p>`
      : "")
  );
}

/** @param {{ blockItems: Array<object>, blocks: Array<unknown> }} ctx */
export function normalizeRiskBlockRows({ blockItems, blocks }) {
  return (
    blockItems.length
      ? blockItems.map((b) => ({
          code: b.code || "—",
          reason: b.message || b.reason || "—",
          ts: b.sector || b.stock_code || "—",
        }))
      : (Array.isArray(blocks) ? blocks : []).map((b) =>
          typeof b === "string"
            ? { code: "—", reason: b, ts: "—" }
            : {
                code: b.code || b.stock_code || "—",
                reason: b.reason || b.message || "—",
                ts: b.ts || b.time || "—",
              }
        )
  ).slice(0, 20);
}

/**
 * @param {Array<object>} blkData
 * @param {{
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 * }} [opts]
 */
export function buildRiskBlocksHtml(blkData, opts = {}) {
  const escapeHtml = opts.escapeHtml || defaultEscapeHtml;
  const researchGridHtml = opts.researchGridHtml || defaultResearchGridHtml;
  return (
    `<p class="quant-trades-caption">本次/最近拦截明细</p>` +
    researchGridHtml(
      [
        { id: "code", label: "原因码", widthPct: 22 },
        { id: "reason", label: "说明", flex: true },
        { id: "ts", label: "标的/行业", widthPct: 22 },
      ],
      blkData,
      (col, b) => {
        if (col.id === "code") return `<code>${escapeHtml(b.code)}</code>`;
        if (col.id === "reason") return escapeHtml(b.reason);
        return escapeHtml(b.ts);
      },
      { emptyText: "暂无拦截记录" }
    )
  );
}
