/**
 * 交易执行 · 持仓主表（共享 virtual_table 内核，与数据中心同方案）。
 */

import { fmtPriceUnit, fmtPct, metricCls, fmtTableScore, scoreCls, resolveTradeScore, resolveCalTradeScore, resolveNowcastScore, isHeuristicScoreScale } from "./paper/fmt.js?v=p1227";
import { sentimentBadgeHtml, watchingScoreDetail } from "./quant/watching_render.js?v=p1227";
import {
  isSingleHeadItem,
  singleHeadBadgeHtml,
  yCheckBadgeHtml,
} from "./quant/watching_insights_ui.js?v=p1227";
import { formatWatchingResidual, RESIDUAL_TITLE, NOWCAST_TITLE, TRADE_TITLE, EOD_CAL_TITLE } from "./quant/watching_quotes_ui.js?v=p1227";

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
  const scoreNowcast = resolveNowcastScore(h);
  const belowMin = !!h.below_min_score;
  const minScore = h.min_score;
  const hardReject = !!h.hard_reject;
  const scoreBase = fmtTableScore(h, score);
  let scoreText =
    scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
  if (scoreText === "—" && hardReject) scoreText = "拒";
  const scoreCalText = fmtTableScore(h, scoreCal);
  const scoreNowcastText = fmtTableScore(h, scoreNowcast);
  const origin = String(h.origin || "");
  const mv = Number(h.market_value ?? h.market_value_approx);
  const oosFailed =
    !!h.oos_failed ||
    isHeuristicScoreScale(h) ||
    String(h.return_model_source || "").startsWith("oos_failed");
  const singleHead = isSingleHeadItem(h);
  const scoreTitle = hardReject
    ? String(h.reject_reason || "硬拒绝 · 无收益分")
    : isHeuristicScoreScale(h)
      ? score != null
        ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
        : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
      : singleHead
        ? `ŷ_trade 单头降级（${String(h.dual_score_head || "single")}）· 悬停看详情`
      : belowMin
        ? `低于ŷ_EOD门槛 ${minScore ?? "—"}（表列为 ŷ_trade）· 悬停看详情`
        : TRADE_TITLE;
  const scoreCalOor = !!h.score_calibration_eod_oor;
  const scoreCalTitle =
    scoreCal == null
      ? "暂无 g(ŷ_EOD) 映射（拟合并写入 live 后可见）"
      : scoreCalOor
        ? "g(ŷ_EOD) 域外钳制 · 悬停看 eod tip"
        : EOD_CAL_TITLE;
  const scoreNowcastTitle =
    scoreNowcast == null ? "暂无 nowcast · 有 ŷ_EOD 与 ŷ_τ 后可见" : NOWCAST_TITLE;
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
  let residualNum = null;
  let residualText = "—";
  if (
    score != null &&
    chg != null &&
    chg !== "" &&
    Number.isFinite(Number(chg))
  ) {
    const fmt = formatWatchingResidual(score, chg);
    residualNum = fmt.residualNum;
    residualText = fmt.residual;
  }
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
    }${singleHead ? " score-single-head" : ""}`,
    scoreTitle,
    scoreSingleHead: singleHead,
    dualScoreHead: h.dual_score_head || null,
    dualScoreWindow: h.dual_score_window || null,
    dualScoreWeights: h.dual_score_weights || null,
    predictedScoreTau: h.predicted_score_tau ?? h.score_rem ?? null,
    yCheck: h.y_check || null,
    scoreCalText,
    scoreCalNum:
      scoreCal != null && Number.isFinite(Number(scoreCal)) ? Number(scoreCal) : null,
    scoreCalCls: `${scoreCls(scoreCal)}${scoreCalOor ? " is-cal-oor" : ""}`.trim(),
    scoreCalTitle,
    scoreNowcastText,
    scoreNowcastNum:
      scoreNowcast != null && Number.isFinite(Number(scoreNowcast))
        ? Number(scoreNowcast)
        : null,
    scoreNowcastCls: scoreCls(scoreNowcast),
    scoreNowcastTitle,
    residualText,
    residualNum,
    residualCls: metricCls(residualNum),
    residualTitle: RESIDUAL_TITLE,
    scoreDetail: watchingScoreDetail(h),
    chgText,
    chgCls: metricCls(chg),
    chgNum: chg != null && Number.isFinite(Number(chg)) ? Number(chg) : null,
    pnlText,
    pnlCls: metricCls(pnl),
    pnlNum: pnl != null && Number.isFinite(Number(pnl)) ? Number(pnl) : null,
    boughtDate: h.bought_date || "—",
    origin,
    originLabel: h.origin_label || "—",
    oosFailed,
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
  { id: "name", label: "股票", flex: true, title: "股票名称与代码" },
  {
    id: "sent",
    label: "情绪",
    widthPct: 5,
    headClass: "watching-col-center",
    cellClass: "watching-col-center paper-hold-sent",
    title: "标题情绪摘要",
  },
  { id: "shares", label: "股数", widthPct: 6, num: true, title: "持仓股数" },
  { id: "price", label: "现价", widthPct: 7, num: true, title: "最新成交价" },
  { id: "open", label: "开盘价", widthPct: 7, num: true, title: "当日开盘价" },
  {
    id: "chg",
    label: "涨跌",
    widthPct: 7,
    num: true,
    sortable: true,
    title: "相对昨收的涨跌幅 %。与 EOD / ŷ_trade 同一口径",
  },
  {
    id: "score_cal",
    label: "EOD",
    widthPct: 6,
    num: true,
    sortable: true,
    title: "eod = g(ŷ_EOD)，对涨跌的回归预估（现价对昨收）；不含缺口；不进决策",
  },
  {
    id: "score",
    label: "TRADE",
    widthPct: 6.5,
    num: true,
    sortable: true,
    title: "ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ) · 现价对昨收（与涨跌同一口径）· 排序/卖门槛",
  },
  {
    id: "score_nowcast",
    label: "NOWCAST",
    widthPct: 6,
    num: true,
    sortable: true,
    title: "ŷ_nowcast · Kalman 权昨收口径对照（与 ŷ_trade / 涨跌同一目标），不进决策",
  },
  {
    id: "residual",
    label: "残差",
    widthPct: 6,
    num: true,
    sortable: true,
    title: "残差 = trade − 涨跌。trade 是 ŷ_trade，与涨跌同一目标",
  },
  { id: "cost", label: "成本", widthPct: 7, num: true, title: "持仓加权平均成本" },
  { id: "market_value", label: "市值", widthPct: 7, num: true, sortable: true, title: "现价 × 股数" },
  {
    id: "pnl",
    label: "浮盈亏",
    widthPct: 6.5,
    num: true,
    sortable: true,
    title: "浮盈亏 = 现价 − 成本价（表内为相对成本的浮动盈亏 %）；加仓为加权成本，不是当日涨跌",
  },
  { id: "since", label: "开始", widthPct: 11, title: "建仓日期" },
  {
    id: "origin",
    label: "出处",
    widthPct: 7,
    cellClass: "paper-hold-origin",
    headClass: "paper-hold-origin",
    title: "仓位来源：手动 / 策略 / 混合",
  },
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
  if (id === "score_nowcast") return numCmp(Number(a.scoreNowcastNum), Number(b.scoreNowcastNum));
  if (id === "residual") return numCmp(Number(a.residualNum), Number(b.residualNum));
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
    rowHeight: 50,
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
        d.oosFailed ? "is-oos-failed" : "",
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
        const oosBadge = d.oosFailed
          ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
          : "";
        return (
          `<div class="paper-wl-name" title="${escapeHtml((d.name || "") + " " + (d.code || ""))}">` +
          `<span class="watching-name-row">` +
          `<span class="paper-wl-name-text" title="${escapeHtml(d.name || "")}" data-full-name="${escapeHtml(
            d.name || ""
          )}">${escapeHtml(truncateName(d.name || d.code))}</span>` +
          oosBadge +
          `</span>` +
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
        const singleHead = !!d.scoreSingleHead;
        const badges = [];
        if (singleHead) {
          badges.push(
            singleHeadBadgeHtml(
              {
                dual_score_head: d.dualScoreHead,
                dual_score_single_head: true,
                dual_score_window: d.dualScoreWindow,
                dual_score_weights: d.dualScoreWeights,
                predicted_score_tau: d.predictedScoreTau,
              },
              escapeHtml
            )
          );
        }
        const yBadge = yCheckBadgeHtml({ y_check: d.yCheck }, escapeHtml);
        if (yBadge) badges.push(yBadge);
        const badge = badges.join("");
        if (!detail) {
          return `<span class="paper-hold-score ${escapeHtml(
            d.scoreCls || ""
          )}">${escapeHtml(d.scoreText || "—")}${badge}</span>`;
        }
        return (
          `<span class="paper-hold-score has-tip ${escapeHtml(d.scoreCls || "")}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="trade" title="${escapeHtml(title)}">` +
          `${escapeHtml(d.scoreText || "—")}${badge}</span>`
        );
      }
      if (col.id === "score_cal") {
        const detail = d.scoreDetail || "";
        const title =
          d.scoreCalTitle || "g(ŷ_EOD) · 对涨跌";
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
      if (col.id === "score_nowcast") {
        const detail = d.scoreDetail || "";
        const title = d.scoreNowcastTitle || NOWCAST_TITLE;
        const text = d.scoreNowcastText || "—";
        if (!detail) {
          return `<span class="paper-hold-score watching-score-nowcast ${escapeHtml(
            d.scoreNowcastCls || ""
          )}">${escapeHtml(text)}</span>`;
        }
        return (
          `<span class="paper-hold-score watching-score-nowcast has-tip ${escapeHtml(
            d.scoreNowcastCls || ""
          )}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="nowcast" title="${escapeHtml(title)}">` +
          `${escapeHtml(text)}</span>`
        );
      }
      if (col.id === "residual") {
        return `<span class="paper-hold-residual ${escapeHtml(
          d.residualCls || ""
        )}" title="${escapeHtml(
          d.residualTitle || RESIDUAL_TITLE
        )}">${escapeHtml(d.residualText || "—")}</span>`;
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
