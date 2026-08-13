/**
 * 交易执行 · 持仓主表（共享 virtual_table 内核，与数据中心同方案）。
 */

import { fmtPriceUnit, fmtPct, metricCls, fmtScore, scoreCls } from "./paper/fmt.js";
import { sentimentBadgeHtml } from "./quant/watching_render.js";

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function truncateName(name, max = 6) {
  const full = String(name || "").trim();
  const chars = Array.from(full);
  if (chars.length <= max) return full;
  return `${chars.slice(0, max).join("")}…`;
}

export function holdingSentPlaceholder(code) {
  const c = String(code || "").trim();
  return (
    `<span class="watching-sent-badge is-neutral" data-code="${escapeHtml(c)}" title="加载中">…</span>`
  );
}

export function holdingToRow(
  h,
  { chartMode, chartStockCode, selectedHoldCode, pendingFocusCode, sentHtml } = {}
) {
  const code = String(h.stock_code || "").trim();
  const name = h.stock_name || code || "";
  const pnl = h.pnl_pct;
  const score = h.score;
  const belowMin = !!h.below_min_score;
  const minScore = h.min_score;
  const hardReject = !!h.hard_reject;
  const scoreBase = fmtScore(score);
  let scoreText =
    scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
  if (scoreText === "—" && hardReject) scoreText = "拒";
  const origin = String(h.origin || "");
  const mv = Number(h.market_value ?? h.market_value_approx);
  const scoreTitle = hardReject
    ? String(h.reject_reason || "硬拒绝 · 无收益分")
    : belowMin
      ? `低于ŷ门槛 ${minScore ?? "—"}（仍显示分数）· 悬停看详情`
      : "悬停查看收益分与因子系数";
  const fmtSignedPct = (v) => {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}%`;
  };
  const pnlText = fmtSignedPct(pnl);
  const chg = h.change_pct;
  const chgText = fmtSignedPct(chg);
  return {
    code,
    name,
    shares: h.shares,
    sentHtml:
      sentHtml != null && sentHtml !== ""
        ? sentHtml
        : holdingSentPlaceholder(code),
    priceText: fmtPriceUnit(h.price, h.unit, h.currency),
    costText: fmtPriceUnit(h.cost, h.unit, h.currency),
    mvText: fmtPriceUnit(h.market_value, h.unit, h.currency),
    marketValueNum: Number.isFinite(mv) ? mv : null,
    scoreText,
    scoreNum: score != null && Number.isFinite(Number(score)) ? Number(score) : null,
    scoreCls: `${scoreCls(score)}${belowMin ? " score-below-min" : ""}${
      hardReject ? " score-reject" : ""
    }`,
    scoreTitle,
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
      min_score: minScore,
      below_min_score: belowMin,
      return_model_source: h.return_model_source || "",
      factor_coefficients: h.factor_coefficients || {},
      formula_terms: h.score_formula_terms || null,
      predicted_score: h.predicted_score != null ? h.predicted_score : score,
    }),
    chgText,
    chgCls: metricCls(chg),
    chgNum: chg != null && Number.isFinite(Number(chg)) ? Number(chg) : null,
    pnlText,
    pnlCls: metricCls(pnl),
    pnlNum: pnl != null && Number.isFinite(Number(pnl)) ? Number(pnl) : null,
    boughtDate: h.bought_date || "—",
    origin,
    originLabel: h.origin_label || "—",
    isChartActive: chartMode === "stock" && chartStockCode && String(chartStockCode) === code,
    isAdjustActive: selectedHoldCode && String(selectedHoldCode) === code,
    isFocusHolding: pendingFocusCode && String(pendingFocusCode) === code,
  };
}

/** 供外部把接口 sentiment 转成徽章 HTML（与数据中心同源）。 */
export function holdingSentimentHtml(sent, code) {
  return sentimentBadgeHtml(sent || {}, code);
}

const ORIGIN_HINT = {
  manual: "你手动建仓或加仓",
  strategy: "由「按策略调仓」生成",
  mixed: "手动建仓后被策略加过仓",
};

const COLS = [
  { id: "name", label: "股票", flex: true },
  {
    id: "sent",
    label: "情绪",
    widthPct: 5,
    headClass: "watching-col-center",
    cellClass: "watching-col-center paper-hold-sent",
  },
  { id: "shares", label: "股数", widthPct: 6, num: true },
  { id: "price", label: "现价", widthPct: 7, num: true },
  {
    id: "chg",
    label: "涨跌",
    widthPct: 7,
    num: true,
    sortable: true,
    title: "相对昨收的当日涨跌幅（行情）；与「浮盈亏」不同",
  },
  { id: "cost", label: "成本", widthPct: 7, num: true, title: "持仓加权平均成本，对账用" },
  { id: "market_value", label: "市值", widthPct: 8, num: true, sortable: true },
  { id: "score", label: "评分", widthPct: 7, num: true, sortable: true },
  {
    id: "pnl",
    label: "浮盈亏",
    widthPct: 8,
    num: true,
    sortable: true,
    title: "相对持仓成本：(现价÷成本−1)×100%；加仓则为加权成本，非当日涨跌",
  },
  { id: "since", label: "开始", widthPct: 12 },
  { id: "origin", label: "出处", widthPct: 8, cellClass: "paper-hold-origin", headClass: "paper-hold-origin" },
];

function numCmp(av, bv) {
  return (Number.isFinite(av) ? av : -Infinity) - (Number.isFinite(bv) ? bv : -Infinity);
}

function compare(id, a, b) {
  if (id === "code") {
    return String(a.code || "").localeCompare(String(b.code || ""), "zh-CN", { numeric: true });
  }
  if (id === "score") return numCmp(Number(a.scoreNum), Number(b.scoreNum));
  if (id === "pnl") return numCmp(Number(a.pnlNum), Number(b.pnlNum));
  if (id === "chg") return numCmp(Number(a.chgNum), Number(b.chgNum));
  return numCmp(Number(a.marketValueNum), Number(b.marketValueNum));
}

/**
 * @param {HTMLElement} host
 * @param {{ initialSort?: Array<{column:string, dir:string}> }} [options]
 */
export async function mountHoldingsTableIsland(host, options = {}) {
  const V = (typeof window !== "undefined" && window.__ASSET_V__) || "dev";
  const { mountVirtualTable } = await import(
    `./virtual_table.js?v=${encodeURIComponent(V)}`
  );
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
      if (col.id === "sent") return d.sentHtml || holdingSentPlaceholder(d.code);
      if (col.id === "shares") return escapeHtml(d.shares != null ? String(d.shares) : "—");
      if (col.id === "price") return escapeHtml(d.priceText || "—");
      if (col.id === "chg") {
        return `<span class="paper-hold-chg ${escapeHtml(d.chgCls || "")}" title="相对昨收">${escapeHtml(
          d.chgText || "—"
        )}</span>`;
      }
      if (col.id === "cost") {
        return `<span class="paper-hold-cost" title="持仓加权平均成本">${escapeHtml(
          d.costText || "—"
        )}</span>`;
      }
      if (col.id === "market_value") return escapeHtml(d.mvText || "—");
      if (col.id === "score") {
        return (
          `<span class="paper-hold-score ${escapeHtml(d.scoreCls || "")}" ` +
          `data-score-detail="${escapeHtml(d.scoreDetail || "")}" title="${escapeHtml(
            d.scoreTitle || "悬停查看收益分与因子系数"
          )}">` +
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
