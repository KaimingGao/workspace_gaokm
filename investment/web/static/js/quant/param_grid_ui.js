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

function _oosVal(c) {
  if (!c || !c.success || c.oos_return_pct == null) return null;
  const n = Number(c.oos_return_pct);
  return Number.isFinite(n) ? n : null;
}

function _gateLabel(c) {
  if (!c || !c.success) return "失败";
  if (c.eligible) return c.oos_failed ? "过门·缺口" : "过门";
  const fail = String(c.gate_fail || "");
  if (fail === "oos_negative") return "OOS<0";
  if (fail === "dd_over") return "回撤超";
  if (fail === "oos_short" || fail === "oos_missing") return "OOS缺";
  return "未过门";
}

/** 渲染 lookback × top_k 矩阵（色=OOS，最优=过门格）。 */
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
  const vals = (cells || []).map(_oosVal).filter((v) => v != null);
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
          const oos = _oosVal(c);
          if (c && c.success && oos != null) {
            const t = maxV === minV ? 0.5 : (oos - minV) / (maxV - minV);
            const tone = _heatTone(t);
            const isRet =
              c.total_return_pct != null ? Number(c.total_return_pct).toFixed(2) : "—";
            const dd =
              c.max_drawdown_pct != null ? Number(c.max_drawdown_pct).toFixed(2) : "—";
            const title =
              `lookback=${lb} · top_k=${tk} · OOS ${oos.toFixed(2)}% · 样本内 ${isRet}% · 回撤 ${dd}%` +
              (c.eligible ? " · 过门" : ` · ${_gateLabel(c)}`);
            const extra = [
              tone.cls,
              isBest ? "is-best" : "",
              c.eligible ? "" : "is-ineligible",
              c.oos_failed ? "is-oos-fail" : "",
              "is-pick",
            ]
              .filter(Boolean)
              .join(" ");
            const equiv = c.equivalent_note ? ` · ${c.equivalent_note}` : "";
            return (
              `<div class="param-grid-matrix-cell ${extra}" ` +
              `style="${tone.style}" ` +
              `data-pg-lookback="${escapeHtml(String(lb))}" ` +
              `data-pg-top-k="${escapeHtml(String(tk))}" ` +
              `data-pg-eligible="${c.eligible ? "1" : "0"}" ` +
              `role="button" tabindex="0" ` +
              `title="${escapeHtml(title + equiv)}">` +
              `<span class="param-grid-matrix-val">${escapeHtml(oos.toFixed(1))}%</span>` +
              (isBest
                ? `<span class="param-grid-matrix-best">最优</span>`
                : `<span class="param-grid-matrix-sub">${escapeHtml(_gateLabel(c))}</span>`) +
              `</div>`
            );
          }
          const err = c && c.error ? String(c.error) : "";
          return (
            `<div class="param-grid-matrix-cell is-empty" title="${escapeHtml(err || "无 OOS")}">` +
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
    `<div class="param-grid-matrix" style="--pg-cols:${cols}" role="img" aria-label="参数网格 OOS 矩阵">` +
    head +
    rows +
    `</div>` +
    `<div class="param-grid-matrix-legend" aria-hidden="true">` +
    `<span class="param-grid-matrix-leg is-low">OOS 相对低</span>` +
    `<span class="param-grid-matrix-leg-bar"></span>` +
    `<span class="param-grid-matrix-leg is-high">OOS 相对高</span>` +
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
  if (!best || best.oos_return_pct == null) return null;
  const neighbors = cells.filter((c) => {
    if (!c.success || c.oos_return_pct == null) return false;
    const dLb = Math.abs(Number(c.lookback) - Number(best.lookback));
    const dK = Math.abs(Number(c.top_k) - Number(best.top_k));
    if (dLb === 0 && dK === 0) return false;
    return dLb + dK === 1;
  });
  if (!neighbors.length) return null;
  const bestRet = Number(best.oos_return_pct);
  let maxGap = 0;
  neighbors.forEach((c) => {
    const g = Math.abs(bestRet - Number(c.oos_return_pct));
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
  const n = data && data.cell_count != null ? data.cell_count : 0;
  const elig = data && data.eligible_count != null ? data.eligible_count : 0;
  let t;
  if (best) {
    const oos = best.oos_return_pct != null ? `${best.oos_return_pct}%` : "—";
    const dd = best.max_drawdown_pct != null ? `${best.max_drawdown_pct}%` : "—";
    t =
      `过门最优 lookback=${best.lookback} · top_k=${best.top_k} · OOS ${oos} · 回撤 ${dd}` +
      ` · ${n} 格 / 过门 ${elig}`;
  } else {
    t = `完成 ${n} 格 · 过门 ${elig} · 无格满足 OOS≥0 且回撤≤15%`;
  }
  if (data && data.align_paper) {
    const h = data.horizon_days != null ? data.horizon_days : "—";
    const ymin = data.min_predicted_score != null ? data.min_predicted_score : "—";
    t += ` · 纸面口径 h=${h} ŷ≥${ymin}`;
  }
  if (data && data.multiple_testing_note) {
    t += ` · ${data.multiple_testing_note}`;
  } else if (data && data.trial_count != null) {
    t += ` · trial_count=${data.trial_count}`;
  }
  if (dull && dull.sharp) {
    t += ` · ⚠邻格 OOS Δ最大 ${dull.max_gap_pp}pp（参数过尖，应用前请二次确认）`;
  } else if (dull) {
    t += ` · 邻格 OOS Δ ${dull.max_gap_pp}pp`;
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
      { id: "lookback", label: "lookback", widthPct: 12, num: true },
      { id: "top_k", label: "top_k", widthPct: 10, num: true },
      { id: "ret", label: "样本内", widthPct: 16, num: true },
      { id: "oos", label: "OOS", widthPct: 16, num: true },
      { id: "dd", label: "回撤", widthPct: 12, num: true },
      { id: "oosDd", label: "OOS回撤", widthPct: 12, num: true },
      { id: "n", label: "笔数", widthPct: 8, num: true },
      { id: "gate", label: "过门", widthPct: 12, num: false },
    ],
    (data.cells || []).map((c) => {
      const ret = c.total_return_pct;
      const oos = c.oos_return_pct;
      const isBest = best && c.lookback === best.lookback && c.top_k === best.top_k;
      return {
        lookback: String(c.lookback),
        top_k: String(c.top_k),
        retText: c.success ? fmtPct(ret) : String(c.error || "失败"),
        retCls: c.success ? metricClass(ret) : "down",
        oosText: c.success ? fmtPct(oos) : "—",
        oosCls: c.success ? metricClass(oos) : "down",
        dd: fmtPct(c.max_drawdown_pct),
        oosDd: fmtPct(c.oos_max_drawdown_pct),
        n: String(c.trade_count ?? "—"),
        gate: c.equivalent_note ? `${_gateLabel(c)} · 同指标` : _gateLabel(c),
        isBest,
        eligible: !!c.eligible,
      };
    }),
    (col, d) => {
      if (col.id === "ret") return metricCell(escapeHtml(d.retText), d.retCls);
      if (col.id === "oos") return metricCell(escapeHtml(d.oosText), d.oosCls);
      if (col.id === "dd") return escapeHtml(d.dd);
      if (col.id === "oosDd") return escapeHtml(d.oosDd);
      return escapeHtml(d[col.id] ?? "—");
    },
    {
      emptyText: "无网格结果",
      rowClass: (d) => {
        const bits = [];
        if (d.isBest) bits.push("is-best");
        if (!d.eligible) bits.push("is-ineligible");
        return bits.join(" ");
      },
    }
  );
}
