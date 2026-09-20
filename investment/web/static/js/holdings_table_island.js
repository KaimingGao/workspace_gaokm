/**
 * 交易执行 · 持仓主表（共享 virtual_table 内核，与数据中心同方案）。
 */

import { fmtPriceUnit, fmtPct, metricCls, fmtTableScore, scoreCls, resolveRankingScore, resolveEodScore, resolveTauScore, resolveOnScore, isHeuristicScoreScale, Y_EOD_TITLE, Y_OC_REBALANCE_TITLE, Y_ON_TITLE, RANKING_REBALANCE_TITLE } from "./paper/fmt.js?v=p2544";
import { sentimentBadgeHtml, watchingScoreDetail } from "./quant/watching_render.js?v=p2544";
import {
  isSingleHeadItem,
  singleHeadBadgeHtml,
  yCheckBadgeHtml,
} from "./quant/watching_insights_ui.js?v=p2544";
import { formatPrevCloseDisplay, formatOpenDisplay, resolveOpenPx } from "./quant/watching_quotes_ui.js?v=p2389";
import { buildHoldingSharesTip } from "./paper/holding_lots_tip.js?v=p1227";
import { holdingT0BadgeHtml } from "./paper/holding_t0_badge.js?v=p1526";
import { fitTierBadgeForCode, ensureFitTierMap } from "./quant/fit_tier_ui.js?v=p2261";

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
  const score = resolveRankingScore(h);
  const scoreEod = resolveEodScore(h);
  const scoreTau = resolveTauScore(h);
  const scoreOn = resolveOnScore(h);
  const belowMin = !!h.below_min_score;
  const minScore = h.min_score;
  const hardReject = !!h.hard_reject;
  const scoreBase = fmtTableScore(h, score);
  let scoreText =
    scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
  if (scoreText === "—" && hardReject) scoreText = "拒";
  const scoreEodText = fmtTableScore(h, scoreEod);
  const scoreTauText = fmtTableScore(h, scoreTau);
  const scoreOnText = fmtTableScore(h, scoreOn);
  const origin = String(h.origin || "");
  const mv = Number(h.market_value ?? h.market_value_approx);
  const oosFailed =
    !!h.oos_failed ||
    isHeuristicScoreScale(h) ||
    String(h.return_model_source || "").startsWith("oos_failed");
  const inBook = !!(h.in_book || h.inBook);
  const singleHead = isSingleHeadItem(h);
  const scoreTitle = hardReject
    ? String(h.reject_reason || "硬拒绝 · 无收益分")
    : isHeuristicScoreScale(h)
      ? score != null
        ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
        : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
      : singleHead
        ? `ranking 单头降级（${String(h.dual_score_head || "single")}）· 悬停看详情`
      : belowMin
        ? `低于ŷ_oo门槛 ${minScore ?? "—"}（表列为 ranking）· 悬停看详情`
        : RANKING_REBALANCE_TITLE;
  const scoreEodTitle = scoreEod == null ? "暂无 ŷ_oo" : Y_EOD_TITLE;
  const scoreTauTitle = scoreTau == null ? "暂无 ŷ_oc" : Y_OC_REBALANCE_TITLE;
  const scoreOnTitle = scoreOn == null ? "暂无 ŷ_co" : Y_ON_TITLE;
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
    prevCloseText: formatPrevCloseDisplay(h, { unit: h.unit, currency: h.currency }),
    openText: formatOpenDisplay(h, { unit: h.unit, currency: h.currency }),
    openNum: (() => {
      const n = resolveOpenPx(h);
      return n != null && Number.isFinite(n) ? n : null;
    })(),
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
    predictedScoreOn: h.predicted_score_on ?? null,
    yCheck: h.y_check || null,
    scoreEodText,
    scoreEodNum:
      scoreEod != null && Number.isFinite(Number(scoreEod)) ? Number(scoreEod) : null,
    scoreEodCls: scoreCls(scoreEod),
    scoreEodTitle,
    scoreTauText,
    scoreTauNum:
      scoreTau != null && Number.isFinite(Number(scoreTau)) ? Number(scoreTau) : null,
    scoreTauCls: scoreCls(scoreTau),
    scoreTauTitle,
    scoreOnText,
    scoreOnNum:
      scoreOn != null && Number.isFinite(Number(scoreOn)) ? Number(scoreOn) : null,
    scoreOnCls: scoreCls(scoreOn),
    scoreOnTitle,
    scoreDetail: watchingScoreDetail(h),
    chgText,
    chgCls: metricCls(chg),
    chgNum: chg != null && Number.isFinite(Number(chg)) ? Number(chg) : null,
    changeAsof: h.change_asof || h.quote_as_of || h.as_of || null,
    pnlText,
    pnlCls: metricCls(pnl),
    pnlNum: pnl != null && Number.isFinite(Number(pnl)) ? Number(pnl) : null,
    boughtDate: h.bought_date || "—",
    sharesTip: buildHoldingSharesTip(h),
    t0Intraday: h.t0_intraday || null,
    t0BadgeHtml: holdingT0BadgeHtml(h.t0_intraday, escapeHtml),
    origin,
    originLabel: h.origin_label || "—",
    inBook,
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

/** 持仓主表列轨：对齐数据中心固定 px + Y 组间距。 */
const COLS = [
  {
    id: "name",
    label: "股票",
    flex: true,
    flexMin: "10.5rem",
    flexFr: 1,
    title: "股票名称与代码",
  },
  {
    id: "sent",
    label: "情绪",
    width: 36,
    headClass: "watching-col-center",
    cellClass: "watching-col-center paper-hold-sent",
    title: "标题情绪摘要",
  },
  { id: "shares", label: "股数", width: 56, num: true, title: "持仓股数；悬停查看买入批次与 T+1 可卖" },
  {
    id: "t0",
    label: "做T",
    width: 52,
    headClass: "watching-col-center paper-hold-t0-head",
    cellClass: "watching-col-center paper-hold-t0",
    title: "当日实时做 T：正T/反T · 盯盘/一腿/完成",
  },
  { id: "prev_close", label: "昨收", width: 78, num: true, title: "上一交易日收盘价" },
  { id: "open", label: "今开", width: 78, num: true, title: "今日开盘价" },
  { id: "price", label: "现价", width: 78, num: true, title: "最新成交价" },
  {
    id: "chg",
    label: "涨跌",
    width: 68,
    num: true,
    sortable: true,
    title: "相对昨收的涨跌幅 %",
  },
  {
    id: "score_eod",
    label: "y_oo",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: Y_EOD_TITLE,
  },
  {
    id: "score_tau",
    label: "y_oc",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: Y_OC_REBALANCE_TITLE,
  },
  {
    id: "score_on",
    label: "y_co",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: Y_ON_TITLE,
  },
  {
    id: "score",
    label: "ranking",
    width: 94,
    num: true,
    sortable: true,
    headClass: "watching-col-y watching-col-y-ranking",
    cellClass: "watching-col-y watching-col-y-ranking",
    title: RANKING_REBALANCE_TITLE,
  },
  { id: "cost", label: "成本", width: 78, num: true, title: "持仓加权平均成本" },
  {
    id: "market_value",
    label: "市值",
    width: 78,
    num: true,
    sortable: true,
    title: "现价 × 股数",
  },
  {
    id: "pnl",
    label: "浮盈亏",
    width: 68,
    num: true,
    sortable: true,
    title: "浮盈亏 = 现价 − 成本价（表内为相对成本的浮动盈亏 %）；加仓为加权成本，不是当日涨跌",
  },
  { id: "since", label: "开始", width: 100, cellClass: "watching-col-since", title: "建仓日期" },
  {
    id: "origin",
    label: "出处",
    width: 56,
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
  if (id === "score_eod") return numCmp(Number(a.scoreEodNum), Number(b.scoreEodNum));
  if (id === "score_tau") return numCmp(Number(a.scoreTauNum), Number(b.scoreTauNum));
  if (id === "score_on") return numCmp(Number(a.scoreOnNum), Number(b.scoreOnNum));
  if (id === "pnl") return numCmp(Number(a.pnlNum), Number(b.pnlNum));
  if (id === "chg") return numCmp(Number(a.chgNum), Number(b.chgNum));
  if (id === "open") return numCmp(Number(a.openNum), Number(b.openNum));
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
  await ensureFitTierMap();
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
        d.t0Intraday?.phase === "after_leg1" ? "is-t0-leg1" : "",
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
          fitTierBadgeForCode(d.code, { escapeHtml }) +
          oosBadge +
          `</span>` +
          `<span class="paper-wl-code">${escapeHtml(d.code || "")}</span></div>`
        );
      }
      if (col.id === "sent") return d.sentHtml || holdingSentPlaceholder(d.code);
      if (col.id === "shares") {
        const text = d.shares != null ? String(d.shares) : "—";
        const tip = d.sharesTip || (d.shares != null ? `持仓 ${text} 股` : "");
        return `<span class="paper-hold-shares-qty has-tip" title="${escapeHtml(tip)}">${escapeHtml(
          text
        )}</span>`;
      }
      if (col.id === "t0") return d.t0BadgeHtml || holdingT0BadgeHtml(null, escapeHtml);
      if (col.id === "price") return escapeHtml(d.priceText || "—");
      if (col.id === "prev_close") {
        return `<span class="paper-hold-prev-close" title="上一交易日收盘价">${escapeHtml(
          d.prevCloseText || "—"
        )}</span>`;
      }
      if (col.id === "open") {
        const full = d.openText || "—";
        const text = String(full).replace(/元$/u, "");
        return `<span class="paper-hold-open" title="${escapeHtml(full)}">${escapeHtml(text)}</span>`;
      }
      if (col.id === "chg") {
        const code = escapeHtml(d.code || "");
        const asof = escapeHtml(String(d.changeAsof || "").slice(0, 10));
        const name = escapeHtml(d.name || "");
        const chgNum =
          d.chgNum != null && Number.isFinite(Number(d.chgNum))
            ? String(Number(d.chgNum))
            : "";
        return (
          `<span class="paper-hold-chg has-tip ${escapeHtml(d.chgCls || "")}" ` +
          `data-hold-chg-code="${code}" data-hold-chg-name="${name}" ` +
          `data-hold-chg-asof="${asof}" data-hold-chg-num="${escapeHtml(chgNum)}" ` +
          `data-hold-chg-tip="${code}" title="相对昨收 · 悬停看涨跌日5m K">${escapeHtml(
            d.chgText || "—"
          )}</span>`
        );
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
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="ranking" title="${escapeHtml(title)}">` +
          `${escapeHtml(d.scoreText || "—")}${badge}</span>`
        );
      }
      if (col.id === "score_eod" || col.id === "score_tau" || col.id === "score_on") {
        const tipMap = {
          score_eod: "eod",
          score_tau: "tau",
          score_on: "on",
        };
        const skinMap = {
          score_eod: "eod",
          score_tau: "tau",
          score_on: "on",
        };
        const textKey =
          col.id === "score_eod"
            ? "scoreEodText"
            : col.id === "score_tau"
              ? "scoreTauText"
              : "scoreOnText";
        const clsKey =
          col.id === "score_eod"
            ? "scoreEodCls"
            : col.id === "score_tau"
              ? "scoreTauCls"
              : "scoreOnCls";
        const titleKey =
          col.id === "score_eod"
            ? "scoreEodTitle"
            : col.id === "score_tau"
              ? "scoreTauTitle"
              : "scoreOnTitle";
        const detail = d.scoreDetail || "";
        const title = d[titleKey] || "";
        const text = d[textKey] || "—";
        if (!detail) {
          return `<span class="paper-hold-score watching-score-${skinMap[col.id]} ${escapeHtml(
            d[clsKey] || ""
          )}">${escapeHtml(text)}</span>`;
        }
        return (
          `<span class="paper-hold-score watching-score-${skinMap[col.id]} has-tip ${escapeHtml(
            d[clsKey] || ""
          )}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="${tipMap[col.id]}" title="${escapeHtml(title)}">` +
          `${escapeHtml(text)}</span>`
        );
      }
      if (col.id === "pnl") {
        return `<span class="paper-hold-pnl ${escapeHtml(d.pnlCls || "")}">${escapeHtml(
          d.pnlText || "—"
        )}</span>`;
      }
      if (col.id === "since") {
        const text = d.boughtDate || "—";
        return `<span class="paper-hold-since" title="建仓日期 ${escapeHtml(text)}">${escapeHtml(text)}</span>`;
      }
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
