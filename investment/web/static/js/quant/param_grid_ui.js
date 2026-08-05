/**
 * 参数网格热力图与结果表 HTML（canvas / 字符串）。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml, metricCell as defaultMetricCell } from "./research_grid.js";
import { fmtPct as defaultFmtPct, metricClass as defaultMetricClass } from "./bt_result.js";

export function drawParamHeatmap(cells, axes) {
  const canvas = document.getElementById("param-grid-heat");
  if (!canvas) return;
  const g = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  g.clearRect(0, 0, w, h);
  const lookbacks = (axes && axes.lookback) || [];
  const topKs = (axes && axes.top_k) || [];
  if (!lookbacks.length || !topKs.length) {
    g.fillStyle = "#9ca3af";
    g.font = "12px Manrope, sans-serif";
    g.fillText("暂无网格", 16, h / 2);
    return;
  }
  const vals = (cells || [])
    .filter((c) => c.success && c.total_return_pct != null)
    .map((c) => Number(c.total_return_pct));
  const minV = vals.length ? Math.min(...vals) : 0;
  const maxV = vals.length ? Math.max(...vals) : 1;
  const padL = 48;
  const padB = 28;
  const padT = 12;
  const padR = 12;
  const cellW = (w - padL - padR) / topKs.length;
  const cellH = (h - padT - padB) / lookbacks.length;
  const byKey = {};
  (cells || []).forEach((c) => {
    byKey[`${c.lookback}|${c.top_k}`] = c;
  });
  lookbacks.forEach((lb, ri) => {
    topKs.forEach((tk, ci) => {
      const c = byKey[`${lb}|${tk}`];
      const x = padL + ci * cellW;
      const y = padT + ri * cellH;
      let fill = "#e5e7eb";
      if (c && c.success && c.total_return_pct != null) {
        const t = maxV === minV ? 0.5 : (Number(c.total_return_pct) - minV) / (maxV - minV);
        const r = Math.round(255 * t);
        const b = Math.round(255 * (1 - t));
        fill = `rgb(${r},80,${b})`;
      }
      g.fillStyle = fill;
      g.fillRect(x + 2, y + 2, cellW - 4, cellH - 4);
      g.fillStyle = "#111827";
      g.font = "11px IBM Plex Mono, monospace";
      g.textAlign = "center";
      g.textBaseline = "middle";
      const label =
        c && c.success && c.total_return_pct != null
          ? `${Number(c.total_return_pct).toFixed(1)}%`
          : c && c.error
            ? "×"
            : "—";
      g.fillText(label, x + cellW / 2, y + cellH / 2);
    });
  });
  g.fillStyle = "#6b7280";
  g.font = "10px Manrope, sans-serif";
  g.textAlign = "right";
  lookbacks.forEach((lb, ri) => {
    g.fillText(String(lb), padL - 6, padT + ri * cellH + cellH / 2);
  });
  g.textAlign = "center";
  topKs.forEach((tk, ci) => {
    g.fillText(`K=${tk}`, padL + ci * cellW + cellW / 2, h - 10);
  });
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
