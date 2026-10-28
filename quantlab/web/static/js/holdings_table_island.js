/**
 * 交易执行 · 持仓主表（共享 virtual_table 内核，与数据中心同方案）。
 */

import { fmtPriceUnit, fmtPct, metricCls, fmtTableScore, scoreCls, resolveFuseScore, resolveEodScore, resolveTauScore, resolveOnScore, isHeuristicScoreScale, Y_EOD_TITLE, Y_OC_REBALANCE_TITLE, Y_ON_TITLE, FUSE_REBALANCE_TITLE } from "./paper/fmt.js?v=p3360";
import { sentimentBadgeHtml, watchingScoreDetail } from "./quant/watching_render.js?v=p2746";
import {
  isSingleHeadItem,
  singleHeadBadgeHtml,
  yCheckBadgeHtml,
} from "./quant/watching_insights_ui.js?v=p2544";
import { formatPrevCloseDisplay, formatOpenDisplay, resolveOpenPx } from "./quant/watching_quotes_ui.js?v=p2389";
import { buildHoldingSharesTip } from "./paper/holding_lots_tip.js?v=p1227";
import {
  holdingFuseBelowEnter,
  holdingFuseBelowTitle,
} from "./paper/holdings_ui.js?v=p3358";
import { holdingT0BadgeHtml } from "./paper/holding_t0_badge.js?v=p1526";
import { fitTierBadgeForCode, ensureFitTierMap } from "./quant/fit_tier_ui.js?v=p2261";

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 只认带池大小的 1..n 名次。旧相对分或单侧缺 n 显示 —。 */
function rankOrdinal(raw, nRaw) {
  const y = Number(raw);
  const n = Number(nRaw);
  if (!Number.isFinite(y)) {
    return { text: "—", num: null, title: "" };
  }
  const ord = Math.round(y);
  const isInt = Math.abs(y - ord) < 1e-9;
  const pool = Number.isFinite(n) && n >= 1 ? Math.round(n) : null;
  if (pool != null && isInt && ord >= 1 && ord <= pool) {
    return { text: String(ord), num: ord, title: "" };
  }
  return { text: "—", num: null, title: "" };
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
  const score = resolveFuseScore(h);
  const scoreEod = resolveEodScore(h);
  const scoreTau = resolveTauScore(h);
  const scoreOn = resolveOnScore(h);
  const belowMin = holdingFuseBelowEnter(h, score);
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
        ? `fuse 单头降级（${String(h.dual_score_head || "single")}）· 悬停看详情`
      : belowMin
        ? holdingFuseBelowTitle(h)
        : FUSE_REBALANCE_TITLE;
  const scoreEodTitle = scoreEod == null ? "暂无 ŷ_oo" : Y_EOD_TITLE;
  const scoreTauTitle = scoreTau == null ? "暂无 ŷ_τc" : Y_OC_REBALANCE_TITLE;
  const scoreOnTitle = scoreOn == null ? "暂无 ŷ_co" : Y_ON_TITLE;
  const ooRank = rankOrdinal(
    h.y_oo_rank != null ? h.y_oo_rank : h.predicted_score_oo_rank,
    h.y_oo_rank_n
  );
  const tcRank = rankOrdinal(
    h.y_tc_rank != null ? h.y_tc_rank : h.predicted_score_tc_rank,
    h.y_tc_rank_n
  );
  const ooRankTitle =
    ooRank.text === "—"
      ? "oo_rank — · 无已启用模型或未写入；不进 fuse；过入场后作买序；与 τc_rank 共用 rank 入场"
      : `当日截面第 ${ooRank.text} 名 / ${Math.round(Number(h.y_oo_rank_n))} · 1=相对分最高 · 不进 fuse；过入场后作买序；与 τc_rank 共用 rank 入场`;
  const tcRankTitle =
    tcRank.text === "—"
      ? "τc_rank — · 无已启用模型或未写入 · 与 oo_rank 共用 rank 入场；不进 fuse / 不改买序"
      : `当日截面第 ${tcRank.text} 名 / ${Math.round(Number(h.y_tc_rank_n))} · 1=相对分最高 · 与 oo_rank 共用 rank 入场；不进 fuse / 不改买序`;
  const fmtSignedPct = (v) => {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}%`;
  };
  const pnlText = fmtSignedPct(pnl);
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
    costText: fmtPriceUnit(h.cost, h.unit, h.currency, 2),
    costExact: fmtPriceUnit(h.cost, h.unit, h.currency),
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
    predictedScoreTau: h.y_tau ?? h["y_τc"] ?? h.score_rem ?? h.predicted_score_tau ?? null,
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
    ooRankText: ooRank.text,
    ooRankNum: ooRank.num,
    ooRankTitle,
    tcRankText: tcRank.text,
    tcRankNum: tcRank.num,
    tcRankTitle,
    scoreDetail: watchingScoreDetail(h),
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
    id: "tier",
    label: "分档",
    width: 52,
    sortable: true,
    defaultDir: "asc",
    headClass: "watching-col-center",
    cellClass: "watching-col-center",
    title: "可预测性分档（ŷ_oo Holdout 前半）：A 强 / B 中 / C 弱。同档内按 fuse 从高到低",
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
    label: "y_τc",
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
    label: "fuse",
    width: 94,
    num: true,
    sortable: true,
    headClass: "watching-col-y watching-col-y-fuse",
    cellClass: "watching-col-y watching-col-y-fuse",
    title: FUSE_REBALANCE_TITLE,
  },
  {
    id: "oo_rank",
    label: "oo_rank",
    width: 68,
    num: true,
    sortable: true,
    defaultDir: "asc",
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title:
      "ŷ_oo_rank 当日观察池截面名次 · 1=相对分最高 · 悬停看因子组成 · 过入场后作买序；rank 入场与 τc_rank 共用门槛",
  },
  {
    id: "tc_rank",
    label: "τc_rank",
    width: 68,
    num: true,
    sortable: true,
    defaultDir: "asc",
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title:
      "ŷ_τc_rank 当日截面名次 · 1=相对分最高 · 悬停看因子组成 · 与 oo_rank 共用 rank 入场；不进 fuse / 不改买序",
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

function rankCmp(av, bv) {
  const aOk = Number.isFinite(Number(av));
  const bOk = Number.isFinite(Number(bv));
  if (aOk && bOk) return Number(av) - Number(bv);
  if (aOk) return -1;
  if (bOk) return 1;
  return 0;
}

let _predTier = {
  rank() {
    return 9;
  },
  get() {
    return null;
  },
  badge() {
    return "—";
  },
};

function compare(id, a, b) {
  if (id === "tier") {
    const ta = _predTier.get(a && a.code);
    const tb = _predTier.get(b && b.code);
    return _predTier.rank(ta && ta.tier) - _predTier.rank(tb && tb.tier);
  }
  if (id === "code") {
    return String(a.code || "").localeCompare(String(b.code || ""), "zh-CN", { numeric: true });
  }
  if (id === "score") return numCmp(Number(a.scoreNum), Number(b.scoreNum));
  if (id === "score_eod") return numCmp(Number(a.scoreEodNum), Number(b.scoreEodNum));
  if (id === "score_tau") return numCmp(Number(a.scoreTauNum), Number(b.scoreTauNum));
  if (id === "score_on") return numCmp(Number(a.scoreOnNum), Number(b.scoreOnNum));
  if (id === "oo_rank") return rankCmp(a.ooRankNum, b.ooRankNum);
  if (id === "tc_rank") return rankCmp(a.tcRankNum, b.tcRankNum);
  if (id === "pnl") return numCmp(Number(a.pnlNum), Number(b.pnlNum));
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
  const predTier = await import(`./quant/pred_tier_ui.js?v=${encodeURIComponent(V)}`);
  _predTier = {
    rank: predTier.predTierRank,
    get: predTier.getPredTier,
    badge: predTier.predTierBadgeHtml,
  };
  await Promise.all([ensureFitTierMap(), predTier.ensurePredTierMap()]);
  return mountVirtualTable(host, {
    columns: COLS,
    emptyText: "暂无持仓",
    rowHeight: 50,
    rootClass: "watching-react-grid paper-holdings-react-grid",
    bodyClass: "paper-holdings-react-body",
    initialSort: options.initialSort,
    compare,
    tiebreak: (id, a, b) => {
      if (id !== "tier") return 0;
      const as = Number(a && a.scoreNum);
      const bs = Number(b && b.scoreNum);
      const aOk = Number.isFinite(as);
      const bOk = Number.isFinite(bs);
      if (aOk && bOk) return bs - as;
      if (aOk) return -1;
      if (bOk) return 1;
      return 0;
    },
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
      if (col.id === "tier") return _predTier.badge(d.code, escapeHtml);
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
      if (col.id === "cost") {
        const exact = d.costExact || d.costText || "—";
        return `<span class="paper-hold-cost" title="持仓加权平均成本 ${escapeHtml(exact)}">${escapeHtml(
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
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="fuse" title="${escapeHtml(title)}">` +
          `${escapeHtml(d.scoreText || "—")}${badge}</span>`
        );
      }
      if (col.id === "oo_rank" || col.id === "tc_rank") {
        const tip = col.id === "oo_rank" ? "oo_rank" : "tc_rank";
        const text = col.id === "oo_rank" ? d.ooRankText : d.tcRankText;
        const title = col.id === "oo_rank" ? d.ooRankTitle : d.tcRankTitle;
        const detail = d.scoreDetail || "";
        if (!detail) {
          return `<span class="paper-hold-score">${escapeHtml(text || "—")}</span>`;
        }
        return (
          `<span class="paper-hold-score has-tip" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="${tip}" title="${escapeHtml(
            title || ""
          )}">${escapeHtml(text || "—")}</span>`
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
