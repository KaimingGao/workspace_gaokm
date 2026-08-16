/**
 * 交易执行 · 持仓主表（共享 virtual_table 内核，与数据中心同方案）。
 */

import { fmtPriceUnit, fmtPct, metricCls, fmtTableScore, scoreCls, resolveTradeScore, resolveCalTradeScore, isHeuristicScoreScale } from "./paper/fmt.js?v=p1128";
import { sentimentBadgeHtml } from "./quant/watching_render.js?v=p1108";

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
  const score = resolveTradeScore(h);
  const scoreCal = resolveCalTradeScore(h);
  const belowMin = !!h.below_min_score;
  const minScore = h.min_score;
  const hardReject = !!h.hard_reject;
  const scoreBase = fmtTableScore(h, score);
  let scoreText =
    scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
  if (scoreText === "—" && hardReject) scoreText = "拒";
  const scoreCalText = fmtTableScore(h, scoreCal);
  const origin = String(h.origin || "");
  const mv = Number(h.market_value ?? h.market_value_approx);
  const scoreTitle = hardReject
    ? String(h.reject_reason || "硬拒绝 · 无收益分")
    : isHeuristicScoreScale(h)
      ? score != null
        ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
        : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
      : belowMin
        ? `低于ŷ_EOD门槛 ${minScore ?? "—"}（表列为 ŷ_trade）· 悬停看详情`
        : "ŷ_trade · 悬停看 ŷ_EOD_rem / ŷ_τ";
  const scoreCalOor = !!(
    h.score_calibration_eod_oor ||
    h.score_calibration_eod_rem_oor ||
    h.score_calibration_tau_oor
  );
  const scoreCalTitle =
    scoreCal == null
      ? "暂无 g(ŷ) 映射（拟合并写入 live 后可见）"
      : scoreCalOor
        ? "g(ŷ_trade) 对照 · 部分落在拟合域外（端点钳制）· 悬停看校准 tip"
        : "g(ŷ_trade) 对照 · 不进决策 · 悬停看校准 tip";
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
    openText: fmtPriceUnit(h.open, h.unit, h.currency),
    costText: fmtPriceUnit(h.cost, h.unit, h.currency),
    mvText: fmtPriceUnit(h.market_value, h.unit, h.currency),
    marketValueNum: Number.isFinite(mv) ? mv : null,
    scoreText,
    scoreNum: score != null && Number.isFinite(Number(score)) ? Number(score) : null,
    scoreCls: `${scoreCls(score)}${belowMin ? " score-below-min" : ""}${
      hardReject ? " score-reject" : ""
    }`,
    scoreTitle,
    scoreCalText,
    scoreCalNum:
      scoreCal != null && Number.isFinite(Number(scoreCal)) ? Number(scoreCal) : null,
    scoreCalCls: `${scoreCls(scoreCal)}${scoreCalOor ? " is-cal-oor" : ""}`.trim(),
    scoreCalTitle,
    scoreDetail: JSON.stringify((() => {
      const terms = h.score_formula_terms || null;
      const hasTerms =
        terms && Array.isArray(terms.terms) && terms.terms.length > 0;
      return {
        predicted_score: h.predicted_score != null ? h.predicted_score : h.score,
        score: h.score != null ? h.score : h.predicted_score,
        predicted_score_tau:
          h.predicted_score_tau != null
            ? h.predicted_score_tau
            : h.score_rem != null
              ? h.score_rem
              : h.predicted_score_rem,
        predicted_score_blend: h.predicted_score_blend,
        predicted_score_eod: h.predicted_score_eod,
        predicted_score_eod_rem: h.predicted_score_eod_rem,
        predicted_score_tau_delta: h.predicted_score_tau_delta,
        predicted_score_nowcast: h.predicted_score_nowcast,
        dual_score_window: h.dual_score_window || null,
        nowcast_as_of: h.nowcast_as_of || null,
        nowcast_K: h.nowcast_K,
        nowcast_q: h.nowcast_q,
        predicted_score_cal: h.predicted_score_cal,
        predicted_score_eod_rem_cal: h.predicted_score_eod_rem_cal,
        predicted_score_tau_cal: h.predicted_score_tau_cal,
        predicted_score_blend_cal: h.predicted_score_blend_cal,
        score_calibration_applied: !!h.score_calibration_applied,
        score_calibration_enabled: !!h.score_calibration_enabled,
        score_calibration_eod_oor: !!h.score_calibration_eod_oor,
        score_calibration_eod_rem_oor: !!h.score_calibration_eod_rem_oor,
        score_calibration_tau_oor: !!h.score_calibration_tau_oor,
        score_calibration_note: h.score_calibration_note || null,
        score_calibration_partial: h.score_calibration_partial || null,
        realized_t1_to_tau: h.realized_t1_to_tau,
        score_rem: h.score_rem != null ? h.score_rem : h.predicted_score_rem,
        gap_pct: h.gap_pct,
        event_prior: h.event_prior || null,
        as_of_tau: h.as_of_tau || h.rem_tau || null,
        y_spec_tau: h.y_spec_tau || null,
        features_tau: h.features_tau || null,
        formula_terms_tau: h.formula_terms_tau || h.score_formula_terms_tau || null,
        score_formula_tau: h.score_formula_tau || null,
        factor_coefficients_tau: h.factor_coefficients_tau || null,
        dual_score_fusion: h.dual_score_fusion || null,
        dual_score_weights: h.dual_score_weights || null,
        formula: hasTerms ? "" : h.score_formula || "",
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
        score_scale: h.score_scale || "",
        heuristic_score: h.heuristic_score,
        formula_terms: terms,
        factor_coefficients: hasTerms ? {} : h.factor_coefficients || {},
      };
    })()),
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
  { id: "open", label: "开盘价", widthPct: 7, num: true, title: "当日开盘价" },
  {
    id: "chg",
    label: "涨跌",
    widthPct: 7,
    num: true,
    sortable: true,
    title: "相对昨收的当日涨跌幅（行情）；与「浮盈亏」不同",
  },
  { id: "cost", label: "成本", widthPct: 7, num: true, title: "持仓加权平均成本，对账用" },
  { id: "market_value", label: "市值", widthPct: 7, num: true, sortable: true },
  { id: "score", label: "评分", widthPct: 6.5, num: true, sortable: true, title: "ŷ_trade · 悬停看 ŷ_EOD_rem / ŷ_τ" },
  {
    id: "score_cal",
    label: "校准",
    widthPct: 6.5,
    num: true,
    sortable: true,
    title: "g(ŷ_trade) 对照 · 不进决策 · 悬停看 tip",
  },
  {
    id: "pnl",
    label: "浮盈亏",
    widthPct: 7,
    num: true,
    sortable: true,
    title: "相对持仓成本：(现价÷成本−1)×100%；加仓则为加权成本，非当日涨跌",
  },
  { id: "since", label: "开始", widthPct: 11 },
  { id: "origin", label: "出处", widthPct: 7, cellClass: "paper-hold-origin", headClass: "paper-hold-origin" },
];

function numCmp(av, bv) {
  return (Number.isFinite(av) ? av : -Infinity) - (Number.isFinite(bv) ? bv : -Infinity);
}

function compare(id, a, b) {
  if (id === "code") {
    return String(a.code || "").localeCompare(String(b.code || ""), "zh-CN", { numeric: true });
  }
  if (id === "score") return numCmp(Number(a.scoreNum), Number(b.scoreNum));
  if (id === "score_cal") return numCmp(Number(a.scoreCalNum), Number(b.scoreCalNum));
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
      if (col.id === "open") {
        return `<span class="paper-hold-open" title="当日开盘价">${escapeHtml(
          d.openText || "—"
        )}</span>`;
      }
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
        const detail = d.scoreDetail || "";
        const title = d.scoreTitle || "悬停查看收益分与因子系数";
        if (!detail) {
          return `<span class="paper-hold-score ${escapeHtml(
            d.scoreCls || ""
          )}">${escapeHtml(d.scoreText || "—")}</span>`;
        }
        return (
          `<span class="paper-hold-score has-tip ${escapeHtml(d.scoreCls || "")}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="trade" title="${escapeHtml(title)}">` +
          `${escapeHtml(d.scoreText || "—")}</span>`
        );
      }
      if (col.id === "score_cal") {
        const detail = d.scoreDetail || "";
        const title =
          d.scoreCalTitle || "g(ŷ_trade) 对照 · 不进决策 · 悬停看 tip";
        const text = d.scoreCalText || "—";
        if (!detail) {
          return `<span class="paper-hold-score watching-score-cal ${escapeHtml(
            d.scoreCalCls || ""
          )}">${escapeHtml(text)}</span>`;
        }
        return (
          `<span class="paper-hold-score watching-score-cal has-tip ${escapeHtml(
            d.scoreCalCls || ""
          )}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="cal" title="${escapeHtml(title)}">` +
          `${escapeHtml(text)}</span>`
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
