/**
 * 数据中心主表 · 纯 DOM 虚拟滚动（共享 virtual_table 内核）。
 */

import {
  mountVirtualTable,
  escapeHtml,
  truncateName,
} from "./virtual_table.js";

function numSortKey(row, key) {
  const n = Number(row?.[key]);
  return Number.isFinite(n) ? n : -Infinity;
}

function compare(id, a, b) {
  if (id === "score") return numSortKey(a, "scoreNum") - numSortKey(b, "scoreNum");
  if (id === "vol") return numSortKey(a, "volNum") - numSortKey(b, "volNum");
  if (id === "excess") return numSortKey(a, "excessNum") - numSortKey(b, "excessNum");
  if (id === "chg") return numSortKey(a, "chgNum") - numSortKey(b, "chgNum");
  if (id === "name") {
    return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
  }
  return String(a[id] ?? "").localeCompare(String(b[id] ?? ""), "zh-CN", { numeric: true });
}

/** 短文案列居中，避免夹在数值列之间时一侧挤、一侧空 */
const COLS = [
  { id: "picked", label: "", widthPct: 3.5, headClass: "watching-pick-cell", cellClass: "watching-pick-cell" },
  { id: "name", label: "股票", flex: true, sortable: true, cellClass: "watching-stock" },
  { id: "paper", label: "仓位", widthPct: 6.5, headClass: "watching-col-center", cellClass: "watching-col-center" },
  { id: "sent", label: "情绪", widthPct: 5, headClass: "watching-col-center", cellClass: "watching-col-center" },
  { id: "price", label: "现价", widthPct: 7, num: true },
  { id: "chg", label: "涨跌", widthPct: 6.5, num: true, sortable: true },
  { id: "score", label: "评分", widthPct: 6, num: true, sortable: true },
  { id: "stance", label: "倾向", widthPct: 6, headClass: "watching-col-center", cellClass: "watching-col-center" },
  { id: "excess", label: "超额", widthPct: 7.5, num: true, sortable: true },
  { id: "vol", label: "量", widthPct: 7, num: true, sortable: true },
  { id: "volr", label: "量比", widthPct: 5, num: true },
  { id: "pe", label: "PE", widthPct: 5, num: true },
  { id: "pb", label: "PB", widthPct: 5, num: true },
];

/**
 * @param {HTMLElement} host
 * @param {{ initialSort?: Array<{column:string, dir:string}> }} [options]
 */
export async function mountWatchingTableIsland(host, options = {}) {
  const api = mountVirtualTable(host, {
    columns: COLS,
    emptyText: "暂无观察",
    rowHeight: 38,
    initialSort: options.initialSort,
    compare,
    rowClass: (d) =>
      [
        "watching-watch-row",
        d.isSentimentAlert ? "is-sentiment-alert" : "",
        d.isHardReject ? "is-hard-reject" : "",
        d.yhatHistHit ? "is-yhat-hist-hit" : "",
        d.yhatHistDim ? "is-yhat-hist-dim" : "",
      ]
        .filter(Boolean)
        .join(" "),
    rowAttrs: (d) => ({ "data-code": d.code || "" }),
    headHtml: (col, ctx) => {
      if (col.id !== "picked") {
        const s = ctx.sortState[0];
        const sorted = s && s.id === col.id;
        const arrow = sorted ? (s.desc ? " ↓" : " ↑") : "";
        return `${escapeHtml(col.label || "")}${arrow}`;
      }
      const rows = ctx.rows || [];
      const allPicked = rows.length > 0 && rows.every((r) => !!r.picked);
      return (
        `<input type="checkbox" id="watching-select-all" title="全选" aria-label="全选"` +
        `${allPicked ? " checked" : ""} />`
      );
    },
    cellHtml: (col, d) => {
      if (col.id === "picked") {
        return (
          `<input type="checkbox" class="watching-pick" value="${escapeHtml(d.code || "")}" ` +
          `data-code="${escapeHtml(d.code || "")}" aria-label="选择 ${escapeHtml(
            d.name || d.code || ""
          )}"${d.picked ? " checked" : ""} />`
        );
      }
      if (col.id === "name") {
        return (
          `<div class="watching-stock" title="${escapeHtml((d.name || "") + " " + (d.code || ""))}">` +
          `<span class="watching-name-text" title="${escapeHtml(d.name || "")}" data-full-name="${escapeHtml(
            d.name || ""
          )}">${escapeHtml(truncateName(d.name || d.code))}</span>` +
          `<span class="watching-code-sub">${escapeHtml(d.code || "")}` +
          `<span class="watching-mkt">${escapeHtml(d.market || "")}</span></span></div>`
        );
      }
      if (col.id === "paper") {
        return d.onPaper
          ? `<button type="button" class="watching-held-btn" data-code="${escapeHtml(
              d.code || ""
            )}" title="已在模拟持仓，点击到模拟页管理">${escapeHtml(d.paper || "已持")}</button>`
          : `<button type="button" class="watching-build-btn" data-code="${escapeHtml(
              d.code || ""
            )}" title="按金额确认后，现价假买进模拟账户">建仓</button>`;
      }
      if (col.id === "sent") return d.sentHtml || "—";
      if (col.id === "chg") {
        const text = d.chg != null && d.chg !== "" ? String(d.chg) : "—";
        const cls = d.chgCls ? ` ${escapeHtml(d.chgCls)}` : "";
        return `<span class="watching-chg${cls}">${escapeHtml(text)}</span>`;
      }
      if (col.id === "score") {
        const text = d.score != null && d.score !== "" ? String(d.score) : "—";
        const detail = d.scoreDetail || "";
        const below = !!d.scoreBelowMin;
        const title = d.scoreTitle || "悬停查看收益分与因子系数";
        const signCls = d.scoreCls ? ` ${escapeHtml(String(d.scoreCls))}` : "";
        if (!detail) {
          return `<span class="watching-score-cell paper-hold-score${signCls}">${escapeHtml(
            text
          )}</span>`;
        }
        return (
          `<span class="watching-score-cell paper-hold-score has-tip${signCls}${
            below ? " score-below-min" : ""
          }" ` +
          `data-score-detail="${escapeHtml(detail)}" title="${escapeHtml(title)}">` +
          `${escapeHtml(text)}</span>`
        );
      }
      const v = d[col.id];
      return v != null && v !== "" ? escapeHtml(String(v)) : "—";
    },
  });

  // 勾选变更：同步到行数据（事件由外层 #watching-watchlist-table 委托也会处理）
  host.addEventListener("change", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement)) return;
    if (t.id === "watching-select-all") {
      const checked = !!t.checked;
      (api.getData() || []).forEach((row) => {
        const r = api.getRow(row.code);
        if (r) r.update({ picked: checked });
      });
      return;
    }
    if (t.classList.contains("watching-pick")) {
      const code = String(t.dataset.code || t.value || "").trim();
      const r = code ? api.getRow(code) : null;
      if (r) r.update({ picked: !!t.checked });
    }
  });

  return api;
}
