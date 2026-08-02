/**
 * 交易执行 · 持仓主表（共享 virtual_table 内核，与数据中心同方案）。
 */

import { fmtPriceUnit, fmtPct, metricCls } from "./paper/fmt.js";
import { mountVirtualTable, escapeHtml, truncateName } from "./virtual_table.js";

function scoreCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "score-na";
  if (n >= 60) return "score-high";
  if (n >= 50) return "score-mid";
  return "score-low";
}

function fmtScore(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(1);
}

export function holdingToRow(h, { chartMode, chartStockCode, selectedHoldCode, pendingFocusCode } = {}) {
  const code = String(h.stock_code || "").trim();
  const name = h.stock_name || code || "";
  const pnl = h.pnl_pct;
  const score = h.score;
  const origin = String(h.origin || "");
  const mv = Number(h.market_value ?? h.market_value_approx);
  return {
    code,
    name,
    shares: h.shares,
    priceText: fmtPriceUnit(h.price, h.unit, h.currency),
    costText: fmtPriceUnit(h.cost, h.unit, h.currency),
    mvText: fmtPriceUnit(h.market_value, h.unit, h.currency),
    marketValueNum: Number.isFinite(mv) ? mv : null,
    scoreText: fmtScore(score),
    scoreNum: score != null && Number.isFinite(Number(score)) ? Number(score) : null,
    scoreCls: scoreCls(score),
    scoreDetail: JSON.stringify({
      formula: h.score_formula || "",
      reasons: h.score_reasons || [],
      hard_reject: h.hard_reject,
      reject_reason: h.reject_reason || "",
      weight_source: h.weight_source || "",
      cluster_label: h.cluster_label || "",
      cluster_mode: h.cluster_mode || "",
      cluster_version: h.cluster_version,
      score_global: h.score_global,
      score_cluster: h.score_cluster,
    }),
    pnlText: fmtPct(pnl, { signed: true }),
    pnlCls: metricCls(pnl),
    boughtDate: h.bought_date || "—",
    origin,
    originLabel: h.origin_label || "—",
    isChartActive: chartMode === "stock" && chartStockCode && String(chartStockCode) === code,
    isAdjustActive: selectedHoldCode && String(selectedHoldCode) === code,
    isFocusHolding: pendingFocusCode && String(pendingFocusCode) === code,
  };
}

const ORIGIN_HINT = {
  manual: "你手动建仓或加仓",
  strategy: "由「按策略调仓」生成",
  mixed: "手动建仓后被策略加过仓",
};

const COLS = [
  { id: "name", label: "股票", flex: true },
  { id: "shares", label: "股数", widthPct: 7, num: true },
  { id: "price", label: "现价", widthPct: 9, num: true },
  { id: "cost", label: "成本", widthPct: 9, num: true, title: "持仓加权平均成本，对账用" },
  { id: "market_value", label: "市值", widthPct: 10, num: true, sortable: true },
  { id: "score", label: "评分", widthPct: 7, num: true, sortable: true },
  {
    id: "pnl",
    label: "浮盈亏",
    widthPct: 8,
    num: true,
    title: "相对持仓成本：(现价÷成本−1)×100%；加仓则为加权成本，非当日涨跌",
  },
  { id: "since", label: "开始", widthPct: 9 },
  { id: "origin", label: "出处", widthPct: 8, cellClass: "paper-hold-origin", headClass: "paper-hold-origin" },
];

function compare(id, a, b) {
  if (id === "code") {
    return String(a.code || "").localeCompare(String(b.code || ""), "zh-CN", { numeric: true });
  }
  if (id === "score") {
    const av = Number(a.scoreNum);
    const bv = Number(b.scoreNum);
    return (Number.isFinite(av) ? av : -Infinity) - (Number.isFinite(bv) ? bv : -Infinity);
  }
  const av = Number(a.marketValueNum);
  const bv = Number(b.marketValueNum);
  return (Number.isFinite(av) ? av : -Infinity) - (Number.isFinite(bv) ? bv : -Infinity);
}

/**
 * @param {HTMLElement} host
 * @param {{ initialSort?: Array<{column:string, dir:string}> }} [options]
 */
export async function mountHoldingsTableIsland(host, options = {}) {
  return mountVirtualTable(host, {
    columns: COLS,
    emptyText: "暂无持仓",
    rowHeight: 40,
    rootClass: "watching-react-grid paper-holdings-react-grid",
    bodyClass: "paper-holdings-react-body",
    initialSort: options.initialSort,
    compare,
    rowClass: (d) =>
      [
        "paper-hold-row",
        d.isChartActive ? "is-chart-active" : "",
        d.isAdjustActive ? "is-adjust-active" : "",
        d.isFocusHolding ? "is-focus-holding" : "",
      ]
        .filter(Boolean)
        .join(" "),
    rowAttrs: (d) => ({
      "data-code": d.code || "",
      "data-shares": d.shares != null ? String(d.shares) : "",
      title: "点击选中并查看曲线",
    }),
    headHtml: (col, ctx) => {
      const s = ctx.sortState[0];
      const sorted = s && s.id === col.id;
      const arrow = sorted ? (s.desc ? " ↓" : " ↑") : "";
      return `${escapeHtml(col.label || "")}${arrow}`;
    },
    cellHtml: (col, d) => {
      if (col.id === "name") {
        return (
          `<div class="paper-wl-name" title="${escapeHtml((d.name || "") + " " + (d.code || ""))}">` +
          `<span class="paper-wl-name-text" title="${escapeHtml(d.name || "")}" data-full-name="${escapeHtml(
            d.name || ""
          )}">${escapeHtml(truncateName(d.name || d.code))}</span>` +
          `<span class="paper-wl-code">${escapeHtml(d.code || "")}</span></div>`
        );
      }
      if (col.id === "shares") return escapeHtml(d.shares != null ? String(d.shares) : "—");
      if (col.id === "price") return escapeHtml(d.priceText || "—");
      if (col.id === "cost") {
        return `<span class="paper-hold-cost" title="持仓加权平均成本">${escapeHtml(
          d.costText || "—"
        )}</span>`;
      }
      if (col.id === "market_value") return escapeHtml(d.mvText || "—");
      if (col.id === "score") {
        return (
          `<span class="paper-hold-score ${escapeHtml(d.scoreCls || "")}" ` +
          `data-score-detail="${escapeHtml(d.scoreDetail || "")}" title="悬停查看评分与权重来源">` +
          `${escapeHtml(d.scoreText || "—")}</span>`
        );
      }
      if (col.id === "pnl") {
        return `<span class="paper-hold-pnl ${escapeHtml(d.pnlCls || "")}">${escapeHtml(
          d.pnlText || "—"
        )}</span>`;
      }
      if (col.id === "since") return escapeHtml(d.boughtDate || "—");
      if (col.id === "origin") {
        const title = ORIGIN_HINT[d.origin] || "早期记录未标出处";
        return (
          `<span class="paper-origin-chip is-${escapeHtml(d.origin || "unknown")}" title="${escapeHtml(
            title
          )}">${escapeHtml(d.originLabel || "—")}</span>`
        );
      }
      return "—";
    },
  });
}
