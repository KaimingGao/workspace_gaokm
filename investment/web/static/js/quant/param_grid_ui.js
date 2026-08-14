/**
 * 参数网格热力矩阵与结果表 HTML。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml, metricCell as defaultMetricCell } from "./research_grid.js";
import { fmtPct as defaultFmtPct, metricClass as defaultMetricClass } from "./bt_result.js";

function _heatTone(t) {
  // t∈[0,1]：格内相对高低；A 股语义：高→红、低→绿，强度随 |t−0.5|
  const clamp = Math.max(0, Math.min(1, t));
  if (clamp >= 0.5) {
    const u = (clamp - 0.5) * 2;
    const pct = Math.round(10 + u * 42);
    return {
      cls: "is-high",
      style: `background: color-mix(in srgb, var(--replay-up, #dc2626) ${pct}%, var(--surface, #fff));`,
    };
  }
  const u = (0.5 - clamp) * 2;
  const pct = Math.round(10 + u * 42);
  return {
    cls: "is-low",
    style: `background: color-mix(in srgb, var(--replay-down, #16a34a) ${pct}%, var(--surface, #fff));`,
  };
}

/** 渲染 lookback × top_k 矩阵（替代旧 canvas 热力）。 */
export function buildParamHeatmapHtml(cells, axes, opts = {}) {
  const escapeHtml = opts.escapeHtml || defaultEscapeHtml;
  const lookbacks = (axes && axes.lookback) || [];
  const topKs = (axes && axes.top_k) || [];
  if (!lookbacks.length || !topKs.length) {
    return `<div class="param-grid-matrix is-empty">暂无网格</div>`;
  }

  const byKey = {};
  (cells || []).forEach((c) => {
    byKey[`${c.lookback}|${c.top_k}`] = c;
  });
  const vals = (cells || [])
    .filter((c) => c.success && c.total_return_pct != null)
    .map((c) => Number(c.total_return_pct));
  const minV = vals.length ? Math.min(...vals) : 0;
  const maxV = vals.length ? Math.max(...vals) : 1;
  const best = opts.best || null;

  const head =
    `<div class="param-grid-matrix-corner" aria-hidden="true">lb\\K</div>` +
    topKs
      .map((tk) => `<div class="param-grid-matrix-colhead">K=${escapeHtml(String(tk))}</div>`)
      .join("");

  const rows = lookbacks
    .map((lb) => {
      const cellsHtml = topKs
        .map((tk) => {
          const c = byKey[`${lb}|${tk}`];
          const isBest =
            best &&
            Number(c && c.lookback) === Number(best.lookback) &&
            Number(c && c.top_k) === Number(best.top_k);
          if (c && c.success && c.total_return_pct != null) {
            const v = Number(c.total_return_pct);
            const t = maxV === minV ? 0.5 : (v - minV) / (maxV - minV);
            const tone = _heatTone(t);
            const title = `lookback=${lb} · top_k=${tk} · 收益 ${v.toFixed(2)}%`;
            return (
              `<div class="param-grid-matrix-cell ${tone.cls}${isBest ? " is-best" : ""}" ` +
              `style="${tone.style}" title="${escapeHtml(title)}">` +
              `<span class="param-grid-matrix-val">${escapeHtml(v.toFixed(1))}%</span>` +
              (isBest ? `<span class="param-grid-matrix-best">最优</span>` : "") +
              `</div>`
            );
          }
          const err = c && c.error ? String(c.error) : "";
          return (
            `<div class="param-grid-matrix-cell is-empty" title="${escapeHtml(err || "无结果")}">` +
            `<span class="param-grid-matrix-val">${err ? "×" : "—"}</span>` +
            `</div>`
          );
        })
        .join("");
      return (
        `<div class="param-grid-matrix-rowhead">${escapeHtml(String(lb))}</div>` + cellsHtml
      );
    })
    .join("");

  const cols = topKs.length + 1;
  return (
    `<div class="param-grid-matrix" style="--pg-cols:${cols}" role="img" aria-label="参数网格收益矩阵">` +
    head +
    rows +
    `</div>` +
    `<div class="param-grid-matrix-legend" aria-hidden="true">` +
    `<span class="param-grid-matrix-leg is-low">相对低</span>` +
    `<span class="param-grid-matrix-leg-bar"></span>` +
    `<span class="param-grid-matrix-leg is-high">相对高</span>` +
    `</div>`
  );
}

export function drawParamHeatmap(cells, axes, opts = {}) {
  const host = document.getElementById("param-grid-heat");
  if (!host) return;
  host.innerHTML = buildParamHeatmapHtml(cells, axes, opts);
}

export function paramGridDullness(data) {
  const best = data && data.best;
  const cells = (data && data.cells) || [];
  if (!best || best.total_return_pct == null) return null;
  const neighbors = cells.filter((c) => {
    if (!c.success || c.total_return_pct == null) return false;
    const dLb = Math.abs(Number(c.lookback) - Number(best.lookback));
    const dK = Math.abs(Number(c.top_k) - Number(best.top_k));
    if (dLb === 0 && dK === 0) return false;
    return dLb + dK === 1;
  });
  if (!neighbors.length) return null;
  const bestRet = Number(best.total_return_pct);
  let maxGap = 0;
  neighbors.forEach((c) => {
    const g = Math.abs(bestRet - Number(c.total_return_pct));
    if (g > maxGap) maxGap = g;
  });
  return {
    max_gap_pp: Math.round(maxGap * 100) / 100,
    sharp: maxGap >= 8,
    neighbor_count: neighbors.length,
  };
}

export function buildParamGridMetaText(data, dull) {
  const best = data && data.best;
  let t = best
    ? `最优(样本内) lookback=${best.lookback} · top_k=${best.top_k} · 收益 ${best.total_return_pct}% · ${data.cell_count} 格 · 网格跳过 WF/OOS`
    : `完成 ${data.cell_count} 格 · 无成功单元 · 网格为样本内扫描`;
  if (data.multiple_testing_note) {
    t += ` · ${data.multiple_testing_note}`;
  } else if (data.trial_count != null) {
    t += ` · trial_count=${data.trial_count}`;
  }
  if (dull && dull.sharp) {
    t += ` · ⚠邻格Δ最大 ${dull.max_gap_pp}pp（参数过尖，应用前请二次确认）`;
  } else if (dull) {
    t += ` · 邻格Δ ${dull.max_gap_pp}pp`;
  }
  return t;
}

/**
 * @param {object} data param grid API payload
 * @param {{ researchGridHtml?: Function, metricCell?: Function, fmtPct?: Function, metricClass?: Function, escapeHtml?: Function }} [deps]
 */
export function buildParamGridTableHtml(data, deps = {}) {
  const researchGridHtml = deps.researchGridHtml || defaultResearchGridHtml;
  const metricCell = deps.metricCell || defaultMetricCell;
  const fmtPct = deps.fmtPct || defaultFmtPct;
  const metricClass = deps.metricClass || defaultMetricClass;
  const escapeHtml = deps.escapeHtml || defaultEscapeHtml;
  const best = data && data.best;
  return researchGridHtml(
    [
      { id: "lookback", label: "lookback", widthPct: 18, num: true },
      { id: "top_k", label: "top_k", widthPct: 14, num: true },
      { id: "ret", label: "收益", widthPct: 22, num: true },
      { id: "dd", label: "回撤", widthPct: 22, num: true },
      { id: "n", label: "笔数", widthPct: 14, num: true },
    ],
    (data.cells || []).map((c) => {
      const ret = c.total_return_pct;
      const isBest = best && c.lookback === best.lookback && c.top_k === best.top_k;
      return {
        lookback: String(c.lookback),
        top_k: String(c.top_k),
        retText: c.success ? fmtPct(ret) : String(c.error || "失败"),
        retCls: c.success ? metricClass(ret) : "down",
        dd: fmtPct(c.max_drawdown_pct),
        n: String(c.trade_count ?? "—"),
        isBest,
      };
    }),
    (col, d) => {
      if (col.id === "ret") return metricCell(escapeHtml(d.retText), d.retCls);
      if (col.id === "dd") return escapeHtml(d.dd);
      return escapeHtml(d[col.id] ?? "—");
    },
    {
      emptyText: "无网格结果",
      rowClass: (d) => (d.isBest ? "is-best" : ""),
    }
  );
}
