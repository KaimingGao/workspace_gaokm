/**
 * 中性化对照表渲染。
 */
import { escapeHtml } from "../shared.js";
import { metricClass } from "./bt_result.js";
import { metricCell, researchGridHtml } from "./research_grid.js";

/**
 * @param {object|null} source
 * @param {HTMLElement|null} host
 * @param {{ frozen?: boolean, frozenAt?: string }} [opts]
 */
export function renderNeutralCompareTable(source, host, opts = {}) {
  if (!host) return;
  if (!source) {
    host.innerHTML = "";
    return;
  }

  let winner;
  let nRet;
  let aRet;
  let nWin;
  let aWin;
  let dRet;
  let dWin;
  let interp;
  let nEx;
  let aEx;
  let dEx;
  let benchLabel;

  if (source.neutralized && source.absolute) {
    const nm = source.neutralized.metrics || {};
    const am = source.absolute.metrics || {};
    const delta = source.delta || {};
    winner = source.winner;
    nRet = nm.total_return_pct;
    aRet = am.total_return_pct;
    nWin = nm.win_rate_pct;
    aWin = am.win_rate_pct;
    dRet = delta.total_return_pct;
    dWin = delta.win_rate_pct;
    interp = source.note;
    const bc = source.benchmark_compare || {};
    if (bc.ok) {
      nEx = bc.neutralized_excess_pct;
      aEx = bc.absolute_excess_pct;
      dEx = bc.delta_excess_pct;
      benchLabel = bc.benchmark_label || "基准";
    }
  } else if (source.success) {
    winner = source.winner;
    nRet = source.neutralized_total_return_pct;
    aRet = source.absolute_total_return_pct;
    nWin = source.neutralized_win_rate_pct;
    aWin = source.absolute_win_rate_pct;
    dRet = (source.delta || {}).total_return_pct;
    dWin = (source.delta || {}).win_rate_pct;
    interp = source.interpretation;
  } else {
    host.innerHTML = "";
    return;
  }

  const winnerLabel =
    winner === "neutralized" ? "中性化" : winner === "absolute" ? "未中性化ŷ" : "接近";
  const fmtDelta = (v) => (v == null || v === "" ? "—" : `${v}%`);
  const frozen = !!opts.frozen;
  const srcLabel = frozen
    ? `日报冻结${opts.frozenAt ? ` · ${opts.frozenAt}` : ""}`
    : "当次对照";
  const caption =
    `<p class="quant-trades-caption${frozen ? " down" : ""}">中性化对照 · ${escapeHtml(
      srcLabel
    )} · 更优：${escapeHtml(winnerLabel)}</p>`;
  const table =
    researchGridHtml(
      [
        { id: "dim", label: "维度", flex: true },
        { id: "n", label: "中性化", widthPct: 22, num: true },
        { id: "a", label: "未中性化ŷ", widthPct: 22, num: true },
        { id: "d", label: "Δ", widthPct: 18, num: true },
      ],
      [
        {
          dim: "累计收益",
          n: nRet != null ? `${nRet}%` : "—",
          a: aRet != null ? `${aRet}%` : "—",
          d: fmtDelta(dRet),
          dCls: metricClass(dRet),
        },
        {
          dim: "胜率",
          n: nWin != null ? `${nWin}%` : "—",
          a: aWin != null ? `${aWin}%` : "—",
          d: fmtDelta(dWin),
          dCls: metricClass(dWin),
        },
        ...(benchLabel
          ? [
              {
                dim: `超额·${benchLabel}`,
                n: nEx != null ? `${nEx}%` : "—",
                a: aEx != null ? `${aEx}%` : "—",
                d: fmtDelta(dEx),
                dCls: metricClass(dEx),
              },
            ]
          : []),
      ],
      (col, d) => {
        if (col.id === "d") return metricCell(escapeHtml(d.d), d.dCls);
        return escapeHtml(d[col.id] ?? "—");
      }
    ) +
    (interp ? `<p class="sub">${escapeHtml(interp)}</p>` : "") +
    (frozen
      ? `<p class="sub down">非当次回测结果；点「中性化对照」按当前 lookback/top_k 重算，或点「Top-K 回测」清掉本块。</p>`
      : "");
  if (frozen) {
    host.innerHTML =
      `<details class="quant-fold quant-neutral-frozen">` +
      `<summary>日报冻结 · 中性化对照（${escapeHtml(winnerLabel)}）· 展开查看</summary>` +
      `<div class="quant-section">${caption}${table}</div>` +
      `</details>`;
    return;
  }
  host.innerHTML = caption + table;
}
