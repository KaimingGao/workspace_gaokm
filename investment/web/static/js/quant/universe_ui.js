/**
 * Top-K 回测验证宇宙面板 HTML。
 */
import { escapeHtml } from "../shared.js";
import { researchGridHtml } from "./research_grid.js";

function universeSourceLabel(source) {
  if (source === "include_only") return "include_only";
  if (source === "watching_minus_exclude") return "watching−exclude";
  if (source === "explicit") return "显式 codes";
  return String(source || "—");
}

function dropCols() {
  return [
    { id: "code", label: "代码", widthPct: 22 },
    { id: "reason", label: "原因", widthPct: 22 },
    { id: "detail", label: "说明", flex: true },
  ];
}

/**
 * @param {object|null|undefined} uni
 * @returns {string}
 */
export function buildUniversePanelHtml(uni) {
  if (!uni) return "";
  const src = universeSourceLabel(uni.source);
  const excl = (uni.excluded || []).slice(0, 8).join("、") || "—";
  const dropped = uni.dropped_thin || [];
  const fail = uni.load_failures || [];
  const dropRows = [];
  dropped.slice(0, 12).forEach((d) => {
    dropRows.push({
      code: String(d.stock_code || "—"),
      reason: "短序列",
      detail: String(d.reason || d.bars || "—"),
    });
  });
  fail.slice(0, 8).forEach((f) => {
    dropRows.push({
      code: String(f),
      reason: "拉日线失败",
      detail: "过短/失败",
    });
  });
  let html =
    `<p class="quant-trades-caption">验证宇宙 · ${escapeHtml(src)} · 候选 ${
      uni.candidate_count ?? "—"
    } · 载入 ${uni.loaded_count ?? "—"}` +
    (uni.watching_count != null ? ` · 观察 ${uni.watching_count}` : "") +
    (uni.dropped_thin_count ? ` · 排除短序列 ${uni.dropped_thin_count}` : "") +
    `</p>` +
    `<p class="sub">exclude：${escapeHtml(excl)}</p>` +
    (dropRows.length
      ? researchGridHtml(
          dropCols(),
          dropRows,
          (col, d) => escapeHtml(d[col.id] ?? "—"),
          { emptyText: "无剔除项" }
        )
      : `<p class="sub">${escapeHtml(uni.note || "")}</p>`);

  const filt = uni.filters || {};
  const fd = uni.filter_dropped || [];
  if (filt.exclude_st || filt.min_avg_amount_pctile != null || fd.length) {
    const frows = fd.slice(0, 12).map((d) => ({
      code: String(d.stock_code || "—"),
      reason: "过滤",
      detail: String(d.reason || "—"),
    }));
    html +=
      `<p class="sub">过滤 · 剔ST=${filt.exclude_st ? "是" : "否"}` +
      (filt.min_avg_amount_pctile != null
        ? ` · 成交额≥${filt.min_avg_amount_pctile}%分位`
        : "") +
      ` · 保留 ${filt.kept ?? "—"} · 剔除 ${filt.dropped_count ?? fd.length}</p>` +
      (frows.length
        ? researchGridHtml(
            [
              { id: "code", label: "代码", widthPct: 22 },
              { id: "reason", label: "原因", widthPct: 18 },
              { id: "detail", label: "说明", flex: true },
            ],
            frows,
            (col, d) => escapeHtml(d[col.id] ?? "—")
          )
        : "");
  }
  return html;
}
