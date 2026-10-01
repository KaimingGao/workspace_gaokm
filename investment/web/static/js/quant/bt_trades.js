/**
 * Top-K 回测模拟成交表：列定义与纯变换。
 */

import { resolveStockDisplayName } from "./names.js";

const _V =
  (typeof window !== "undefined" && window.__ASSET_V__) || "dev";
const { watchingScoreDetail } = await import(
  `./watching_render.js?v=${encodeURIComponent(_V)}`
);
const { fitTierBadgeForCode } = await import(
  `./fit_tier_ui.js?v=${encodeURIComponent(_V)}`
);
const {
  resolveRankingScore,
  remainingRankingPct,
  compoundPct,
  Y_EOD_TITLE,
  Y_OC_REBALANCE_TITLE,
  Y_ON_TITLE,
} = await import(`../paper/fmt.js?v=${encodeURIComponent(_V)}`);

export const BT_SIM_TRADE_COLS_BASE = [
  { id: "signal", label: "信号日", widthPct: 9 },
  { id: "entry", label: "买入日", widthPct: 9 },
  { id: "exit", label: "卖出日", widthPct: 9 },
  { id: "name", label: "股票", flex: true, title: "点击名称看日线" },
  {
    id: "score",
    label: "ŷ_oo",
    widthPct: 6.5,
    num: true,
    sortable: true,
    title: "历史 Top-K 表列 = ŷ_oo（选股键）· 关 τ 闸 · 悬停可看字段；日线通常无可靠 ŷ_oc",
  },
  {
    id: "intent",
    label: "意图价",
    widthPct: 8,
    num: true,
    sortable: true,
    title: "信号日收盘（决策参照）；与买入价不同才显示本列",
  },
  {
    id: "buy",
    label: "买入价",
    widthPct: 8,
    num: true,
    sortable: true,
    title: "实际入场价（next_open=次日开盘）",
  },
  { id: "sell", label: "卖出价", widthPct: 8, num: true, sortable: true },
  { id: "ret", label: "收益", widthPct: 8, num: true, sortable: true },
  { id: "status", label: "状态", widthPct: 10 },
];

export function simTradesIntentDiffers(legs) {
  for (const r of legs || []) {
    const intent = r.intent_price;
    const buy = r.entry_price;
    if (intent == null || buy == null) continue;
    if (Number(intent) !== Number(buy)) return true;
  }
  return false;
}

export function btSimTradeColumns(showIntent) {
  if (showIntent) return BT_SIM_TRADE_COLS_BASE;
  return BT_SIM_TRADE_COLS_BASE.filter((c) => c.id !== "intent");
}

export function flattenTradesToSimLegs(trades) {
  const out = [];
  for (const t of trades || []) {
    const legs = Array.isArray(t.legs) ? t.legs : [];
    if (!legs.length) continue;
    for (const l of legs) {
      out.push({
        stock_code: l.stock_code || l.code || "",
        signal_date: t.signal_date || l.signal_date,
        entry_date: l.entry_date || t.entry_date,
        exit_date: l.exit_date || t.exit_date,
        intent_price: l.intent_price,
        entry_price: l.fill_price ?? l.entry_price,
        exit_price: l.exit_price,
        return_pct: l.return_pct,
        score: l.score,
        status: "filled",
        port_return_pct: t.return_pct,
        cluster_label: l.cluster_label,
        cluster_mode: l.cluster_mode,
        cluster_version: l.cluster_version,
        score_weight_source: l.score_weight_source,
        factor_weights: l.factor_weights,
        factor_weights_note: l.factor_weights_note,
        factor_coefficients: l.factor_coefficients,
        return_model_source: l.return_model_source,
        score_formula: l.score_formula,
        score_formula_terms: l.score_formula_terms,
        score_reasons: l.score_reasons,
        score_raw: l.score_raw,
        predicted_score: l.predicted_score != null ? l.predicted_score : l.score,
        predicted_score_tau: l.predicted_score_tau,
        predicted_score_blend: l.predicted_score_blend,
        predicted_score_eod_rem: l.predicted_score_eod_rem,
        realized_t1_to_tau: l.realized_t1_to_tau,
        score_rem: l.score_rem != null ? l.score_rem : l.predicted_score_rem,
        formula_terms_tau: l.formula_terms_tau || l.score_formula_terms_tau,
        score_formula_tau: l.score_formula_tau,
        factor_coefficients_tau: l.factor_coefficients_tau,
        dual_score_fusion: l.dual_score_fusion,
        dual_score_weights: l.dual_score_weights,
        dual_score_head: l.dual_score_head || null,
        dual_score_single_head: !!l.dual_score_single_head,
        gap_pct: l.gap_pct,
        event_prior: l.event_prior,
        as_of_tau: l.as_of_tau || l.rem_tau,
        y_spec_tau: l.y_spec_tau,
        features_tau: l.features_tau,
        sector: l.sector,
      });
    }
  }
  return out;
}

export function formatSimStatus(st) {
  const s = String(st || "filled");
  if (s === "filled") return "成交";
  if (s === "skipped_limit_entry") return "买跳过";
  if (s === "skipped_limit_exit") return "卖跳过";
  return s;
}

export function formatFactorWeightsNote(r) {
  if (r && r.factor_weights_note) return String(r.factor_weights_note);
  const fw = (r && r.factor_weights) || {};
  const parts = Object.keys(fw)
    .sort((a, b) => Number(fw[b] || 0) - Number(fw[a] || 0) || a.localeCompare(b))
    .slice(0, 8)
    .map((k) => `${k} ${Number(fw[k]).toFixed(2)}`);
  const body = parts.join(" / ");
  const label = r && r.cluster_label;
  if (label) {
    return `分组 ${label} 因子权重` + (body ? `：${body}` : "");
  }
  return "未入组 · 全局因子权重" + (body ? `：${body}` : "");
}

/**
 * @param {Array<object>} rows
 * @param {Record<string, string>} nameByCode
 * @returns {string} CSV text with BOM
 */
export function buildSimTradesCsv(rows, nameByCode = {}) {
  const showIntent = simTradesIntentDiffers(rows);
  const header = [
    "stock_code",
    "stock_name",
    "signal_date",
    "entry_date",
    ...(showIntent ? ["intent_price"] : []),
    "entry_price",
    "exit_date",
    "exit_price",
    "return_pct",
    "score",
    "cluster_label",
    "factor_weights_note",
    "score_formula",
    "status",
    "port_return_pct",
    "sector",
  ];
  const lines = [header.join(",")];
  for (const r of rows || []) {
    const code = String(r.stock_code || "").trim();
    const name = resolveStockDisplayName(code, nameByCode[code], r.stock_name) || "";
    const cells = [
      code,
      name,
      r.signal_date || "",
      r.entry_date || "",
      ...(showIntent ? [r.intent_price ?? ""] : []),
      r.entry_price ?? "",
      r.exit_date || "",
      r.exit_price ?? "",
      r.return_pct ?? "",
      r.score ?? "",
      r.cluster_label || "",
      formatFactorWeightsNote(r),
      r.score_formula || "",
      r.status || "",
      r.port_return_pct ?? "",
      r.sector || "",
    ].map((v) => {
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    });
    lines.push(cells.join(","));
  }
  return "\ufeff" + lines.join("\n");
}

/** @returns {Array<object>} */
export function resolveSimTradeLegs(dataOrTrades) {
  if (Array.isArray(dataOrTrades)) return dataOrTrades;
  if (dataOrTrades && typeof dataOrTrades === "object") {
    if (Array.isArray(dataOrTrades.sim_trades) && dataOrTrades.sim_trades.length) {
      return dataOrTrades.sim_trades;
    }
    if (Array.isArray(dataOrTrades.trades) && dataOrTrades.trades.length) {
      return dataOrTrades.trades;
    }
    const paperTrades = dataOrTrades.paper && dataOrTrades.paper.trades;
    if (Array.isArray(paperTrades) && paperTrades.length) {
      return paperTrades;
    }
    if (Array.isArray(dataOrTrades.trades_sample)) {
      return flattenTradesToSimLegs(dataOrTrades.trades_sample);
    }
    if (Array.isArray(dataOrTrades.signal_fill_sample)) {
      return dataOrTrades.signal_fill_sample;
    }
  }
  return [];
}

/** rank_lots 账本腿（有 buy/sell），不是研究独立腿 round-trip。 */
export function isRankLotsLedger(legs, dataOrTrades) {
  const eng =
    (dataOrTrades && dataOrTrades.params && dataOrTrades.params.engine) ||
    (dataOrTrades && dataOrTrades.request && dataOrTrades.request.engine) ||
    (dataOrTrades && dataOrTrades.strategy) ||
    "";
  if (String(eng).trim().toLowerCase() === "paper_replay") return true;
  const rows = legs || [];
  if (!rows.length) return false;
  let n = 0;
  for (const r of rows) {
    const s = String(r.side || "").toLowerCase();
    const act = String(r.action || r.matrix_action || "").toLowerCase();
    const st = String(r.status || "").toLowerCase();
    if (
      s === "buy" ||
      s === "sell" ||
      s === "hold" ||
      act === "hold" ||
      act === "skip" ||
      st === "skipped" ||
      st === "held"
    ) {
      n += 1;
    }
  }
  return n >= Math.max(1, Math.ceil(rows.length * 0.6));
}

export const BT_LEDGER_TRADE_COLS = [
  { id: "action", label: "动作", widthPct: 8, widthMin: "6.2rem", title: "开/加/清/跳过；跳过行下方为原因" },
  { id: "name", label: "股票", flex: true, flexMin: "8.4rem", title: "点击名称看日线" },
  {
    id: "open_date",
    label: "开日日期",
    widthPct: 8,
    widthMin: "7.6rem",
    sortable: true,
    title: "该持仓最早买入日；开仓=当日，加仓/减仓/清仓/持=原开日",
  },
  { id: "shares", label: "股数", widthPct: 5, widthMin: "4.2rem", num: true, sortable: true },
  {
    id: "day_open",
    label: "开盘价",
    widthPct: 6,
    widthMin: "5rem",
    num: true,
    sortable: true,
    title: "当日日 K 开盘",
  },
  {
    id: "day_close",
    label: "收盘价",
    widthPct: 6,
    widthMin: "5rem",
    num: true,
    sortable: true,
    title: "当日日 K 收盘",
  },
  {
    id: "price",
    label: "成交价",
    widthPct: 6,
    widthMin: "5rem",
    num: true,
    sortable: true,
    title: "开/加/清=调仓钟成交价；持/跳过无成交为 —",
  },
  {
    id: "stock_ret",
    label: "收益率",
    widthPct: 8,
    widthMin: "6.4rem",
    num: true,
    sortable: true,
    title: "卖出总额/买入总额−1 · 本票当前这手；未卖完时剩余按收盘市值计入卖出总额",
  },
  {
    id: "ranking",
    label: "ranking",
    widthPct: 11,
    widthMin: "10rem",
    num: true,
    sortable: true,
    title: "rank=w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) · τ→open[T+1] 基准",
  },
  {
    id: "y_fuse",
    label: "y_oo",
    widthPct: 11,
    widthMin: "10rem",
    num: true,
    sortable: true,
    title: `${Y_EOD_TITLE} · 预估(真实)：次日开/今日开`,
  },
  {
    id: "y_tau",
    label: "y_τc",
    widthPct: 11,
    widthMin: "10rem",
    num: true,
    sortable: true,
    title: `${Y_OC_REBALANCE_TITLE} · 预估(真实)：收盘/开盘 · 对照，不进决策`,
  },
  {
    id: "y_on",
    label: "y_co",
    widthPct: 11,
    widthMin: "10rem",
    num: true,
    sortable: true,
    title: `${Y_ON_TITLE} · 预估(真实)：次日开/今日收 · 对照，不进决策`,
  },
];

export function sortLedgerTradeLegs(legs) {
  const sideRank = (r) => {
    const s = String(r.side || "").toLowerCase();
    const skipped = String(r.status || "") === "skipped";
    const held = String(r.status || "") === "held" || String(r.action || "").toLowerCase() === "hold";
    if (s === "sell") return skipped ? 1 : 0;
    if (s === "buy") return skipped ? 3 : 2;
    if (held) return 4;
    return 5;
  };
  return (legs || []).slice().sort((a, b) => {
    const ad = String(a.as_of || a.signal_date || a.ts || "").slice(0, 10);
    const bd = String(b.as_of || b.signal_date || b.ts || "").slice(0, 10);
    if (ad !== bd) return ad.localeCompare(bd);
    const sr = sideRank(a) - sideRank(b);
    if (sr) return sr;
    return String(a.stock_code || "").localeCompare(String(b.stock_code || ""), "zh-CN");
  });
}

export function attachLedgerOpenCost(legs) {
  const pos = Object.create(null);
  return (legs || []).map((raw) => {
    const r = { ...raw };
    const code = String(r.stock_code || "").trim();
    const day = String(r.as_of || r.signal_date || r.ts || "").slice(0, 10);
    const skipped = String(r.status || "") === "skipped";
    const held =
      String(r.status || "") === "held" ||
      String(r.action || r.matrix_action || "").toLowerCase() === "hold";
    const side = String(r.side || "").toLowerCase();
    const px = _numOrNull(
      r.price != null ? r.price : side === "buy" ? r.entry_price : r.exit_price
    );
    const sh = _numOrNull(r.shares);
    const closePx = _numOrNull(r.day_close != null ? r.day_close : held ? px : null);
    const book = pos[code];
    let openDate = String(r.open_date || "").slice(0, 10);
    let stockRet = null;
    const markRet = (slot) => {
      if (!slot || !(slot.buy_amount > 0)) return null;
      if (slot.shares > 1e-6 && closePx == null) return null;
      const remain = slot.shares > 0 && closePx != null ? slot.shares * closePx : 0;
      return (slot.sell_amount + remain) / slot.buy_amount - 1;
    };
    if (side === "buy") {
      if (!openDate) openDate = (book && book.open_date) || day;
      if (!skipped && px != null && sh != null && sh > 0) {
        if (!book) {
          pos[code] = {
            open_date: openDate || day,
            shares: sh,
            buy_amount: px * sh,
            sell_amount: 0,
          };
        } else {
          book.shares += sh;
          book.buy_amount += px * sh;
        }
        stockRet = markRet(pos[code]);
      }
    } else if (side === "sell") {
      if (!openDate && book) openDate = book.open_date;
      if (!skipped && book && sh != null && sh > 0 && px != null) {
        book.sell_amount += px * sh;
        book.shares -= sh;
        stockRet = markRet(book);
        if (book.shares <= 1e-6) delete pos[code];
      }
    } else if (held) {
      if (!openDate && book) openDate = book.open_date;
      if (!skipped && book) stockRet = markRet(book);
    }
    r.open_date = openDate || "";
    if (stockRet != null) r.stock_ret = stockRet * 100;
    else delete r.stock_ret;
    return r;
  });
}

function _numOrNull(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function _scoreClsOf(scoreCls, v) {
  return v == null ? "score-na" : scoreCls(v);
}

function _fmtNum(v, digits = 2) {
  const n = _numOrNull(v);
  return n == null ? "—" : n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function _fmtSignedPct(v) {
  const n = _numOrNull(v);
  if (n == null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

/** 日头现金/净值：本金 20 万时 万两位会把盯市抹成「20万」。百万以下用元。 */
function _fmtBookYuan(v) {
  const n = _numOrNull(v);
  if (n == null) return "—";
  if (Math.abs(n) >= 1e6) {
    return `${(n / 10000).toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}万`;
  }
  const intish = Math.abs(n - Math.round(n)) < 1e-9;
  return n.toLocaleString("zh-CN", {
    maximumFractionDigits: intish ? 0 : 2,
    minimumFractionDigits: intish ? 0 : 2,
  });
}

function _actionMeta(r) {
  const skipped = String(r.status || "") === "skipped";
  const raw = String(r.action || r.matrix_action || "").toLowerCase();
  const side = String(r.side || "").toLowerCase();
  if (skipped || raw === "skip") return { key: "skip", text: "跳过" };
  if (raw === "open") return { key: "open", text: "开" };
  if (raw === "add") return { key: "add", text: "加" };
  if (raw === "reduce") return { key: "reduce", text: "减" };
  if (raw === "exit") return { key: "exit", text: "清" };
  if (raw === "hold" || side === "hold") return { key: "hold", text: "持" };
  if (side === "buy") return { key: "open", text: "买" };
  if (side === "sell") return { key: "exit", text: "卖" };
  return { key: "", text: "—" };
}

function _legReason(r) {
  const s = String((r && (r.reason || r.note)) || "").trim();
  return s;
}

function _fusionFromReplayParams(params) {
  const p = params && typeof params === "object" ? params : {};
  const wOo = p.fusion_w_oo;
  const wOc = p.fusion_w_oc;
  const wCo = p.fusion_w_co;
  return { fusion_w_oo: wOo, fusion_w_oc: wOc, fusion_w_co: wCo };
}

export function buildLedgerTradeRow(r, i, deps) {
  const fw = _fusionFromReplayParams((deps && deps.params) || (deps && deps.request));
  r = {
    ...r,
    fusion_w_oo: r.fusion_w_oo != null ? r.fusion_w_oo : fw.fusion_w_oo,
    fusion_w_oc: r.fusion_w_oc != null ? r.fusion_w_oc : fw.fusion_w_oc,
    fusion_w_co: r.fusion_w_co != null ? r.fusion_w_co : fw.fusion_w_co,
    fusion_w_co: r.fusion_w_co != null ? r.fusion_w_co : fw.fusion_w_co,
  };
  const nameByCode = deps.nameByCode || {};
  const { fmtScore, scoreCls } = deps;
  const code = String(r.stock_code || "").trim();
  const fullName = resolveStockDisplayName(code, nameByCode[code], r.stock_name) || code;
  const side = String(r.side || "").toLowerCase();
  const buy = side === "buy";
  const skipped = String(r.status || "") === "skipped";
  const yf = _numOrNull(r.y_oo != null ? r.y_oo : r.predicted_score_eod);
  const yo = _numOrNull(r.y_co != null ? r.y_co : r.y_on);
  const ytau = _numOrNull(
    r.y_oc != null ? r.y_oc : r.y_tau != null ? r.y_tau : r.predicted_score_tau
  );
  const rk = _numOrNull(r.ranking_score);
  const rankingPct =
    rk != null ? rk * 100 : _numOrNull(r.ranking != null ? r.ranking : r.y_fuse);
  const rCc = _numOrNull(r.realized_cc);
  const rOn = _numOrNull(r.realized_on);
  const rTau = _numOrNull(r.realized_tau);
  let rOo = _numOrNull(r.realized_oo);
  if (rOo == null && rTau != null && rOn != null) {
    rOo = compoundPct(rTau, rOn);
  }
  const act = _actionMeta(r);
  const actionTip = _legReason(r);
  const fuseTip = [
    yf != null ? `ŷ_oo ${fmtScore(yf, { signed: true })}` : "ŷ_oo —",
    rOo != null ? `真实 次日开/今日开 ${fmtScore(rOo, { signed: true })}` : "真实 —（待次日开）",
  ]
    .filter(Boolean)
    .join(" · ");
  const onTip = [
    yo != null ? `ŷ_co ${fmtScore(yo, { signed: true })}` : "ŷ_co —",
    rOn != null ? `真实 次日开/今日收 ${fmtScore(rOn, { signed: true })}` : "真实 —（待次日开）",
  ]
    .filter(Boolean)
    .join(" · ");
  const tauTip = [
    ytau != null ? `ŷ_oc ${fmtScore(ytau, { signed: true })}` : "ŷ_oc —",
    rTau != null ? `真实 收盘/开盘 ${fmtScore(rTau, { signed: true })}` : "真实 —",
  ]
    .filter(Boolean)
    .join(" · ");
  const held =
    String(r.status || "") === "held" ||
    String(r.action || r.matrix_action || "").toLowerCase() === "hold" ||
    side === "hold";
  const sharesNum = skipped ? null : _numOrNull(r.shares);
  const priceNum =
    skipped || held
      ? null
      : _numOrNull(r.price != null ? r.price : buy ? r.entry_price : r.exit_price);
  const dayOpenNum = _numOrNull(r.day_open);
  const dayCloseNum = _numOrNull(r.day_close);
  const stockRetNum = skipped ? null : _numOrNull(r.stock_ret);
  const tauPx =
    _numOrNull(r.rebalance_px) ??
    (held || skipped ? null : priceNum);
  let rRank = _numOrNull(r.realized_ranking);
  if (rRank == null && rOo != null) {
    rRank =
      dayOpenNum != null && tauPx != null
        ? remainingRankingPct(rOo, dayOpenNum, tauPx)
        : rOo;
  }
  const rankingTip = [
    rankingPct != null ? `ranking ${fmtScore(rankingPct, { signed: true })}` : "ranking —",
    rRank != null
      ? `真实 (open[T+1]−price(τ))/open[T] ${fmtScore(rRank, { signed: true })}`
      : "真实 —（待次日开）",
  ]
    .filter(Boolean)
    .join(" · ");
  const scoreDetail = watchingScoreDetail({
    ...r,
    y_oo: yf,
    predicted_score_eod: yf != null ? yf : r.predicted_score_eod,
    predicted_score: yf != null ? yf : r.predicted_score,
    y_oc: ytau,
    y_tau: ytau,
    predicted_score_tau: ytau != null ? ytau : r.predicted_score_tau,
    y_co: yo,
    predicted_score_on: yo != null ? yo : r.predicted_score_on,
    ranking: rankingPct != null ? rankingPct : r.ranking,
    realized_ranking: rRank,
    realized_oo: rOo,
    day_open: dayOpenNum != null ? dayOpenNum : r.day_open,
    rebalance_px: tauPx != null ? tauPx : r.rebalance_px,
  });
  const openDate = String(r.open_date || "").slice(0, 10);
  return {
    code: `led-${i}-${code}-${r.as_of || ""}-${side}-${skipped ? "skip" : "fill"}`,
    date: String(r.as_of || r.signal_date || r.ts || "").slice(0, 10) || "—",
    openDate: openDate || "—",
    open_date: openDate || "—",
    side,
    skipped,
    actionKey: act.key,
    actionText: act.text,
    actionTip,
    stock_code: code,
    name: fullName,
    sharesNum,
    sharesText: skipped ? "—" : _fmtNum(r.shares, 0),
    dayOpenNum,
    dayOpenText: dayOpenNum == null ? "—" : _fmtNum(dayOpenNum, 2),
    dayCloseNum,
    dayCloseText: dayCloseNum == null ? "—" : _fmtNum(dayCloseNum, 2),
    priceNum,
    priceText: priceNum == null ? "—" : _fmtNum(priceNum, 2),
    stockRetNum,
    stockRetText: _fmtSignedPct(stockRetNum),
    stockRetCls: _scoreClsOf(scoreCls, stockRetNum),
    y_fuseNum: yf,
    y_fuseText: fmtScore(yf, { signed: true }),
    y_fuseCls: _scoreClsOf(scoreCls, yf),
    y_fuseTip: fuseTip,
    y_tauNum: ytau,
    y_tauText: fmtScore(ytau, { signed: true }),
    y_tauCls: _scoreClsOf(scoreCls, ytau),
    y_tauTip: tauTip,
    y_onNum: yo,
    y_onText: fmtScore(yo, { signed: true }),
    y_onCls: _scoreClsOf(scoreCls, yo),
    rankingNum: rankingPct,
    rankingText: rankingPct == null ? "—" : fmtScore(rankingPct, { signed: true }),
    rankingCls: _scoreClsOf(scoreCls, rankingPct),
    rankingTip,
    y_onTip: onTip,
    scoreDetail,
    realizedOoNum: rOo,
    realizedOoText: rOo == null ? "" : fmtScore(rOo, { signed: true }),
    realizedOoCls: _scoreClsOf(scoreCls, rOo),
    realizedRankingNum: rRank,
    realizedRankingText: rRank == null ? "" : fmtScore(rRank, { signed: true }),
    realizedRankingCls: _scoreClsOf(scoreCls, rRank),
    realizedCcNum: rCc,
    realizedCcText: rCc == null ? "" : fmtScore(rCc, { signed: true }),
    realizedCcCls: _scoreClsOf(scoreCls, rCc),
    realizedOnNum: rOn,
    realizedOnText: rOn == null ? "" : fmtScore(rOn, { signed: true }),
    realizedOnCls: _scoreClsOf(scoreCls, rOn),
    realizedTauNum: rTau,
    realizedTauText: rTau == null ? "" : fmtScore(rTau, { signed: true }),
    realizedTauCls: _scoreClsOf(scoreCls, rTau),
  };
}

function _legDay(r) {
  return String((r && (r.as_of || r.signal_date || r.ts)) || "").slice(0, 10);
}

function _dayMeta(legs, day) {
  let nOpen = 0;
  let nAdd = 0;
  let nExit = 0;
  let nKeep = 0;
  let nSkip = 0;
  let cash = null;
  let nHold = null;
  let equity = null;
  for (const r of legs || []) {
    const d = _legDay(r);
    if (d !== day) continue;
    const act = _actionMeta(r);
    if (act.key === "open") nOpen += 1;
    else if (act.key === "add") nAdd += 1;
    else if (act.key === "exit") nExit += 1;
    else if (act.key === "reduce") nExit += 1;
    else if (act.key === "hold") nKeep += 1;
    else if (act.key === "skip") nSkip += 1;
    if (cash == null) cash = _numOrNull(r.cash_after);
    if (nHold == null) nHold = _numOrNull(r.n_holdings);
    if (equity == null) equity = _numOrNull(r.equity_after);
  }
  return { nOpen, nAdd, nExit, nKeep, nSkip, cash, nHold, equity };
}

/** 净值曲线上的调仓日（跳过起始垫点：无 cash / n_holdings）。 */
export function curveLedgerDays(curve) {
  const out = [];
  const seen = new Set();
  for (const p of curve || []) {
    if (!p || typeof p !== "object") continue;
    const d = String(p.date || p.ts || p.time || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || seen.has(d)) continue;
    if (p.n_holdings == null && p.cash == null) continue;
    seen.add(d);
    out.push({
      date: d,
      cash: _numOrNull(p.cash),
      nHold: _numOrNull(p.n_holdings),
      equity: _numOrNull(p.equity),
    });
  }
  return out;
}

export function buildLedgerDayRow(day, meta) {
  const cash = meta && meta.cash != null ? meta.cash : null;
  const nHold = meta && meta.nHold != null ? meta.nHold : null;
  const equity = meta && meta.equity != null ? meta.equity : null;
  return {
    kind: "day",
    code: `led-day-${day}`,
    date: day,
    stock_code: "",
    name: "",
    nOpen: meta ? meta.nOpen : 0,
    nAdd: meta ? meta.nAdd : 0,
    nExit: meta ? meta.nExit : 0,
    nKeep: meta ? meta.nKeep : 0,
    nSkip: meta ? meta.nSkip : 0,
    cashNum: cash,
    cashText: _fmtBookYuan(cash),
    nHold,
    equityNum: equity,
    equityText: _fmtBookYuan(equity),
  };
}

export function buildLedgerTradeRows(legs, deps) {
  const sorted = attachLedgerOpenCost(sortLedgerTradeLegs(legs));
  const metaLegs = Array.isArray(deps && deps.metaLegs) ? deps.metaLegs : sorted;
  const curveDays = curveLedgerDays(deps && deps.curve);
  const curveByDay = new Map(curveDays.map((c) => [c.date, c]));
  const days = [];
  const seen = new Set();
  for (const c of curveDays) {
    seen.add(c.date);
    days.push(c.date);
  }
  for (const r of sorted) {
    const day = _legDay(r);
    if (!day || seen.has(day)) continue;
    seen.add(day);
    days.push(day);
  }
  days.sort();
  const legsByDay = new Map();
  sorted.forEach((r, i) => {
    const day = _legDay(r);
    if (!legsByDay.has(day)) legsByDay.set(day, []);
    legsByDay.get(day).push({ r, i });
  });
  const out = [];
  for (const day of days) {
    const meta = _dayMeta(metaLegs, day);
    const fromCurve = curveByDay.get(day);
    if (fromCurve) {
      if (meta.cash == null) meta.cash = fromCurve.cash;
      if (meta.nHold == null) meta.nHold = fromCurve.nHold;
      if (meta.equity == null) meta.equity = fromCurve.equity;
    }
    out.push(buildLedgerDayRow(day, meta));
    for (const { r, i } of legsByDay.get(day) || []) {
      out.push(buildLedgerTradeRow(r, i, deps));
    }
  }
  return out;
}

export function ledgerTradesCaptionHtml() {
  return `<div class="quant-bt-trades-host"></div>`;
}

export function buildLedgerTradesCsv(rows, nameByCode = {}) {
  const header = [
    "date",
    "open_date",
    "status",
    "action",
    "reason",
    "side",
    "stock_code",
    "stock_name",
    "shares",
    "day_open",
    "day_close",
    "price",
    "stock_ret",
    "ranking",
    "y_oo",
    "y_oc",
    "y_co",
    "realized_cc",
    "realized_tau",
    "realized_on",
    "realized_oo",
    "realized_ranking",
    "cash_after",
    "n_holdings",
    "equity_after",
  ];
  const lines = [header.join(",")];
  for (const r of attachLedgerOpenCost(sortLedgerTradeLegs(rows))) {
    const code = String(r.stock_code || "").trim();
    const name = resolveStockDisplayName(code, nameByCode[code], r.stock_name) || "";
    const held =
      String(r.status || "") === "held" ||
      String(r.action || r.matrix_action || "").toLowerCase() === "hold" ||
      String(r.side || "").toLowerCase() === "hold";
    const skipped = String(r.status || "") === "skipped";
    const fillPx = skipped || held ? "" : r.price ?? "";
    const cells = [
      String(r.as_of || r.signal_date || r.ts || "").slice(0, 10),
      String(r.open_date || "").slice(0, 10),
      r.status || "",
      r.action || r.matrix_action || "",
      r.reason || r.note || "",
      r.side || "",
      code,
      name,
      r.shares ?? "",
      r.day_open ?? "",
      r.day_close ?? "",
      fillPx,
      r.stock_ret ?? "",
      r.ranking != null
        ? r.ranking
        : r.ranking_score != null && Number.isFinite(Number(r.ranking_score))
          ? (Number(r.ranking_score) * 100).toFixed(4)
          : r.y_fuse ?? "",
      r.y_oo ?? r.y_fuse ?? r.predicted_score ?? "",
      r.y_oc ?? r.y_tau ?? r.predicted_score_tau ?? "",
      r.y_co ?? r.y_on ?? "",
      r.realized_cc ?? "",
      r.realized_tau ?? "",
      r.realized_on ?? "",
      r.realized_oo ?? "",
      r.realized_ranking ?? "",
      r.cash_after ?? "",
      r.n_holdings ?? "",
      r.equity_after ?? "",
    ].map((v) => {
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    });
    lines.push(cells.join(","));
  }
  return "\ufeff" + lines.join("\n");
}

/** @param {Array<object>} legs */
export function sortSimTradeLegs(legs) {
  return (legs || []).slice().sort((a, b) => {
    const ae = String(a.entry_date || a.signal_date || "");
    const be = String(b.entry_date || b.signal_date || "");
    if (ae !== be) return be.localeCompare(ae);
    const as = String(a.signal_date || "");
    const bs = String(b.signal_date || "");
    if (as !== bs) return bs.localeCompare(as);
    return String(b.stock_code || "").localeCompare(String(a.stock_code || ""), "zh-CN");
  });
}

/**
 * @param {object} r
 * @param {number} i
 * @param {{
 *   nameByCode?: Record<string, string>,
 *   fmtPct: (v: unknown) => string,
 *   metricClass: (v: unknown) => string,
 *   fmtScore: (v: unknown) => string,
 *   scoreCls: (v: unknown) => string,
 * }} deps
 */
export function buildSimTradeRow(r, i, deps) {
  const nameByCode = deps.nameByCode || {};
  const { fmtPct, metricClass, fmtScore, scoreCls } = deps;
  const code = String(r.stock_code || "").trim();
  const fullName = resolveStockDisplayName(code, nameByCode[code], r.stock_name) || code;
  const ret = r.return_pct;
  const st = r.status || "filled";
  // 与观察池同源：slim 分项、优先 τ 字段，避免 data-score-detail 过长截断坏 JSON
  const reasons = Array.isArray(r.score_reasons) ? r.score_reasons.slice(0, 5) : [];
  if (r.return_model_source === "cluster_group_beta") {
    reasons.push("收益分：分组因子系数 β → ŷ%");
  } else if (r.return_model_source === "walk_forward") {
    reasons.push("收益分：walk-forward 拟合 → ŷ%");
  } else if (r.return_model_source) {
    reasons.push(`收益分来源 · ${r.return_model_source}`);
  } else {
    reasons.push("收益分 ŷ%（线性回归预测前瞻收益）");
  }
  if (r.sector) reasons.push(`行业 ${r.sector}`);
  if (r.port_return_pct != null) reasons.push(`本期组合收益 ${r.port_return_pct}%`);
  const eodScore =
    r.predicted_score != null && Number.isFinite(Number(r.predicted_score))
      ? Number(r.predicted_score)
      : null;
  // 与持仓/观察池同源：修历史成交里塌成 EOD 的旧 blend
  const blendRaw = resolveRankingScore(r);
  // 表列：历史日线路径无可靠 τ → 展示/选股键均为 ŷ_oo；有 τ 时才用 ranking
  const hasTau =
    (r.predicted_score_tau != null && Number.isFinite(Number(r.predicted_score_tau))) ||
    (r.score_rem != null && Number.isFinite(Number(r.score_rem)));
  const blendScore = hasTau
    ? blendRaw != null
      ? blendRaw
      : eodScore
    : eodScore != null
      ? eodScore
      : blendRaw;
  const scoreColTitleBase = hasTau
    ? "ranking = w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) · 悬停看组成"
    : "ŷ_oo（历史 Top-K 选股键）· 日线无可靠 ŷ_oc · 关 τ 闸";
  const singleHead =
    r.dual_score_single_head === true ||
    String(r.dual_score_head || "") === "single_oo" ||
    String(r.dual_score_head || "") === "single_tau";
  const scoreColTitle = singleHead
    ? `ranking 单头降级（${String(r.dual_score_head || "single")}）· 悬停看详情`
    : scoreColTitleBase;
  let gapPct =
    r.gap_pct != null && Number.isFinite(Number(r.gap_pct)) ? Number(r.gap_pct) : null;
  let realized =
    r.realized_t1_to_tau != null && Number.isFinite(Number(r.realized_t1_to_tau))
      ? Number(r.realized_t1_to_tau)
      : null;
  if (gapPct == null && realized == null) {
    const intent = Number(r.intent_price);
    const fill = Number(r.entry_price ?? r.fill_price);
    if (Number.isFinite(intent) && intent > 0 && Number.isFinite(fill)) {
      gapPct = (fill / intent - 1) * 100;
      realized = gapPct;
    }
  } else if (realized == null) {
    realized = gapPct;
  }
  let eodRem = null;
  if (eodScore != null && realized != null) {
    const denom = 1 + realized / 100;
    eodRem =
      Math.abs(denom) < 1e-12 ? null : ((1 + eodScore / 100) / denom - 1) * 100;
  } else if (
    r.predicted_score_eod_rem != null &&
    Number.isFinite(Number(r.predicted_score_eod_rem))
  ) {
    eodRem = Number(r.predicted_score_eod_rem);
  } else if (eodScore != null) {
    eodRem = eodScore;
  }
  const scoreDetail = watchingScoreDetail({
    // ranking 头/权进 tip，与表列同式
    ranking: r.ranking,
    y_oo: r.y_oo != null ? r.y_oo : eodScore,
    y_oc: r.y_oc != null ? r.y_oc : r.y_tau_oc,
    y_co: r.y_co != null ? r.y_co : r.y_on,
    fusion_w_oo: r.fusion_w_oo,
    fusion_w_oc: r.fusion_w_oc,
    fusion_w_co: r.fusion_w_co,
    // tip ① 优先 predicted_score=ŷ_oo；表列展示用 blend
    score: eodScore != null ? eodScore : blendScore,
    predicted_score: eodScore != null ? eodScore : blendScore,
    y_tau: r.y_tau != null ? r.y_tau : r["y_τc"],
    "y_τc": r["y_τc"] != null ? r["y_τc"] : r.y_oc,
    predicted_score_blend: blendRaw != null ? blendRaw : blendScore,
    predicted_score_eod_rem: eodRem,
    predicted_score_tau_delta: r.predicted_score_tau_delta,
    dual_score_window: r.dual_score_window || null,
    predicted_score_eod: eodScore,
    realized_t1_to_tau: realized,
    score_rem: r.score_rem != null ? r.score_rem : r.predicted_score_rem,
    gap_pct: gapPct,
    event_prior: r.event_prior,
    as_of_tau: r.as_of_tau || r.rem_tau,
    rem_tau: r.rem_tau,
    y_spec_tau: r.y_spec_tau,
    features_tau: r.features_tau,
    formula_terms_tau: r.formula_terms_tau || r.score_formula_terms_tau,
    score_formula_tau: r.score_formula_tau,
    factor_coefficients_tau: r.factor_coefficients_tau,
    dual_score_fusion: r.dual_score_fusion,
    dual_score_weights: r.dual_score_weights,
    dual_score_head: r.dual_score_head || null,
    dual_score_single_head: !!r.dual_score_single_head || singleHead,
    y_check: r.y_check || null,
    y_disagree: r.y_disagree,
    y_sigma: r.y_sigma,
    eod_trust: r.eod_trust,
    y_tau_to_close: r.y_tau_to_close,
    y_tau_to_close_src: r.y_tau_to_close_src,
    y_state: r.y_state,
    score_formula: r.score_formula,
    score_reasons: reasons,
    hard_reject: !!r.hard_reject,
    reject_reason: r.reject_reason || "",
    weight_source: r.score_weight_source || r.weight_source || "",
    cluster_label: r.cluster_label || "",
    cluster_mode: r.cluster_mode || "",
    cluster_version: r.cluster_version,
    score_global: r.score_global,
    score_cluster: r.score_cluster,
    min_score: r.min_score,
    below_min_score: !!r.below_min_score,
    return_model_source: r.return_model_source || "",
    score_formula_terms: r.score_formula_terms,
    factor_coefficients: r.factor_coefficients || {},
  });
  return {
    code: `sim-${i}-${code}-${r.entry_date || ""}-${r.exit_date || ""}-${st}`,
    stock_code: code,
    name: fullName,
    signal: r.signal_date || "—",
    entry: r.entry_date || "—",
    exit: r.exit_date || "—",
    intentNum: Number.isFinite(Number(r.intent_price)) ? Number(r.intent_price) : null,
    intentText: r.intent_price != null ? String(r.intent_price) : "—",
    buyNum: Number.isFinite(Number(r.entry_price)) ? Number(r.entry_price) : null,
    buyText: r.entry_price != null ? String(r.entry_price) : "—",
    sellNum: Number.isFinite(Number(r.exit_price)) ? Number(r.exit_price) : null,
    sellText: r.exit_price != null ? String(r.exit_price) : "—",
    retNum: Number.isFinite(Number(ret)) ? Number(ret) : null,
    retText: ret != null ? fmtPct(ret) : "—",
    retCls: st !== "filled" ? "down" : metricClass(ret),
    scoreNum: blendScore,
    scoreText: fmtScore(blendScore),
    scoreCls: `${scoreCls(blendScore)}${singleHead ? " score-single-head" : ""}`.trim(),
    scoreDetail,
    scoreTitle: scoreColTitle,
    scoreSingleHead: singleHead,
    dualScoreHead: r.dual_score_head || null,
    status: formatSimStatus(st),
  };
}

/** @param {Array<object>} legs @param {Parameters<typeof buildSimTradeRow>[2]} deps */
export function buildSimTradeRows(legs, deps) {
  return sortSimTradeLegs(legs).map((r, i) => buildSimTradeRow(r, i, deps));
}

/** @param {{ rowsLen: number, filled: number, skipped: number, showIntent: boolean }} opts */
export function simTradesCaptionHtml({ rowsLen, filled, skipped, showIntent }) {
  return (
    `<p class="quant-trades-caption">` +
    `模拟成交账（含涨跌停跳过）· ${rowsLen} 笔` +
    `（成交 ${filled}` +
    (skipped ? ` · 跳过 ${skipped}` : "") +
    `）· 按买入日新→旧 · 信号日→次日买入 · 表列 ŷ_oo（历史选股键；关 τ 闸，日线无可靠 ŷ_oc）` +
    (showIntent ? "" : " · 意图价=买入价已省略") +
    `</p>` +
    `<div class="quant-bt-trades-host"></div>`
  );
}

const BT_TRADES_NUM_KEYS = {
  ret: "retNum",
  buy: "buyNum",
  sell: "sellNum",
  intent: "intentNum",
  score: "scoreNum",
  shares: "sharesNum",
  day_open: "dayOpenNum",
  day_close: "dayCloseNum",
  price: "priceNum",
  stock_ret: "stockRetNum",
  y_fuse: "y_fuseNum",
  y_tau: "y_tauNum",
  y_on: "y_onNum",
  ranking: "rankingNum",
};

/** Virtual-table compare callback for sim trades. */
export function btTradesNumCompare(id, a, b) {
  const ad = a && a.date;
  const bd = b && b.date;
  if (ad && bd && ad !== bd) return String(ad).localeCompare(String(bd));
  if (a && a.kind === "day" && !(b && b.kind === "day")) return -1;
  if (b && b.kind === "day" && !(a && a.kind === "day")) return 1;
  const nk = BT_TRADES_NUM_KEYS[id];
  if (nk) {
    const av = a[nk];
    const bv = b[nk];
    if (av == null && bv == null) return 0;
    if (av == null) return -1;
    if (bv == null) return 1;
    return av - bv;
  }
  return String(a[id] ?? "").localeCompare(String(b[id] ?? ""), "zh-CN", {
    numeric: true,
  });
}

function _dayBandHtml(d, escapeHtml) {
  const chips = [];
  if (d.nExit) chips.push(`<span class="bt-ledger-flow is-exit">清 ${d.nExit}</span>`);
  if (d.nOpen) chips.push(`<span class="bt-ledger-flow is-open">开 ${d.nOpen}</span>`);
  if (d.nAdd) chips.push(`<span class="bt-ledger-flow is-add">加 ${d.nAdd}</span>`);
  if (d.nKeep) chips.push(`<span class="bt-ledger-flow is-hold">持 ${d.nKeep}</span>`);
  if (d.nSkip) chips.push(`<span class="bt-ledger-flow is-skip">跳 ${d.nSkip}</span>`);
  if (!chips.length) {
    chips.push(`<span class="bt-ledger-flow is-hold">未调仓</span>`);
  }
  const cashBits = [
    d.cashText && d.cashText !== "—" ? `现金 ${d.cashText}` : "",
    d.nHold != null ? `持仓 ${d.nHold}` : "",
    d.equityText && d.equityText !== "—" ? `净值 ${d.equityText}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    `<div class="bt-ledger-day-inner">` +
    `<time class="bt-ledger-day-date">${escapeHtml(d.date || "")}</time>` +
    (chips.length ? `<span class="bt-ledger-day-flows">${chips.join("")}</span>` : "") +
    (cashBits
      ? `<span class="bt-ledger-day-cash" title="净值=现金+收盘市值">${escapeHtml(cashBits)}</span>`
      : "") +
    `</div>`
  );
}

function _predRealHtml(predText, predCls, realText, realCls, escapeHtml) {
  const hasPred = predText && predText !== "—";
  const hasReal = realText && realText !== "—";
  if (!hasPred && !hasReal) {
    return `<span class="bt-trade-score bt-stack-main paper-hold-score score-na">—</span>`;
  }
  const pred = hasPred ? predText : "—";
  const real = hasReal ? realText : "—";
  return (
    `<span class="bt-pair">` +
    `<span class="bt-trade-score bt-stack-main paper-hold-score ${escapeHtml(
      predCls || ""
    )}">${escapeHtml(pred)}</span>` +
    `<span class="bt-pair-real paper-hold-score ${escapeHtml(
      hasReal ? realCls || "" : "score-na"
    )}">(${escapeHtml(real)})</span>` +
    `</span>`
  );
}

function _optPredRealHtml(predText, predCls, realText, realCls, escapeHtml) {
  const hasReal = realText && realText !== "—";
  if (!hasReal) {
    return `<span class="bt-trade-score bt-stack-main paper-hold-score ${escapeHtml(
      predCls || ""
    )}">${escapeHtml(predText || "—")}</span>`;
  }
  return _predRealHtml(predText, predCls, realText, realCls, escapeHtml);
}

function _stackScoreHtml(topHtml, tip, escapeHtml, scoreTip, scoreDetail) {
  const t = tip ? ` title="${escapeHtml(tip)}"` : "";
  if (!scoreDetail || !scoreTip) {
    return `<span class="bt-stack-score"${t}>${topHtml}</span>`;
  }
  return (
    `<span class="bt-stack-score bt-trade-score paper-hold-score has-tip" ` +
    `data-score-detail="${escapeHtml(scoreDetail)}" data-score-tip="${escapeHtml(
      scoreTip
    )}"${t}>${topHtml}</span>`
  );
}

/**
 * Virtual-table cellHtml for sim trades.
 * @param {{ escapeHtml: (s: string) => string, watchingNameSpanHtml: (name: string) => string }} deps
 */
export function btTradesCellHtml(col, d, deps) {
  const { escapeHtml, watchingNameSpanHtml } = deps;
  if (d && d.kind === "day") {
    if (col.id === "action") return _dayBandHtml(d, escapeHtml);
    return "";
  }
  if (col.id === "name") {
    return (
      `<div class="watching-stock" title="${escapeHtml(
        `${d.name} ${d.stock_code} · 点击看日线`
      )}">` +
      `<span class="watching-name-row">` +
      watchingNameSpanHtml(d.name) +
      fitTierBadgeForCode(d.stock_code, { escapeHtml }) +
      `</span>` +
      `<span class="watching-code-sub">${escapeHtml(d.stock_code)}</span></div>`
    );
  }
  if (col.id === "open_date") return escapeHtml(d.openDate || "—");
  if (col.id === "action") {
    const cls = d.actionKey ? `bt-trade-action is-${d.actionKey}` : "bt-trade-action";
    const tip = d.actionTip ? ` title="${escapeHtml(d.actionTip)}"` : "";
    const badge = `<span class="${cls}"${tip}>${escapeHtml(d.actionText || "—")}</span>`;
    const showWhy =
      !!d.actionTip &&
      (d.skipped ||
        (d.actionKey === "hold" && !/持有\s*$/.test(String(d.actionTip))));
    if (showWhy) {
      return (
        `<span class="bt-trade-action-wrap">` +
        badge +
        `<span class="bt-trade-skip-why">${escapeHtml(d.actionTip)}</span>` +
        `</span>`
      );
    }
    return badge;
  }
  if (col.id === "shares") return escapeHtml(d.sharesText || "—");
  if (col.id === "day_open") return escapeHtml(d.dayOpenText || "—");
  if (col.id === "day_close") return escapeHtml(d.dayCloseText || "—");
  if (col.id === "price") return escapeHtml(d.priceText || "—");
  if (col.id === "stock_ret") {
    return `<span class="bt-trade-score paper-hold-score ${escapeHtml(
      d.stockRetCls || ""
    )}">${escapeHtml(d.stockRetText || "—")}</span>`;
  }
  if (col.id === "y_fuse") {
    return _stackScoreHtml(
      _optPredRealHtml(d.y_fuseText, d.y_fuseCls, d.realizedOoText, d.realizedOoCls, escapeHtml),
      d.y_fuseTip || Y_EOD_TITLE,
      escapeHtml,
      "eod",
      d.scoreDetail
    );
  }
  if (col.id === "y_tau") {
    return _stackScoreHtml(
      _predRealHtml(d.y_tauText, d.y_tauCls, d.realizedTauText, d.realizedTauCls, escapeHtml),
      d.y_tauTip || Y_OC_REBALANCE_TITLE,
      escapeHtml,
      "tau",
      d.scoreDetail
    );
  }
  if (col.id === "y_on") {
    return _stackScoreHtml(
      _optPredRealHtml(d.y_onText, d.y_onCls, d.realizedOnText, d.realizedOnCls, escapeHtml),
      d.y_onTip || Y_ON_TITLE,
      escapeHtml,
      "on",
      d.scoreDetail
    );
  }
  if (col.id === "ranking") {
    return _stackScoreHtml(
      _optPredRealHtml(
        d.rankingText,
        d.rankingCls,
        d.realizedRankingText,
        d.realizedRankingCls,
        escapeHtml
      ),
      d.rankingTip || "ranking = w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) · 真实 (open[T+1]−price(τ))/open[T]",
      escapeHtml,
      "ranking",
      d.scoreDetail
    );
  }
  if (col.id === "ret") {
    return `<span class="bt-trade-ret ${d.retCls || ""}">${escapeHtml(d.retText)}</span>`;
  }
  if (col.id === "score") {
    const title = d.scoreTitle || "悬停查看收益分与因子系数";
    const singleHead = !!d.scoreSingleHead;
    const head = d.dualScoreHead || "";
    const headTitle =
      head === "single_tau"
        ? "ranking 单头降级：仅 ŷ_oc（缺 ŷ_oo）· 与双头票不同量纲"
        : head === "single_oo"
          ? "ranking 单头降级：仅 ŷ_oo（缺 ŷ_oc）· 与双头票不同量纲"
          : "ranking 单头降级 · 与双头票不同量纲";
    const badge = singleHead
      ? `<span class="watching-single-head-badge" title="${escapeHtml(
          headTitle
        )}">单</span>`
      : "";
    if (!d.scoreDetail) {
      return `<span class="bt-trade-score paper-hold-score ${escapeHtml(
        d.scoreCls || ""
      )}">${escapeHtml(d.scoreText)}${badge}</span>`;
    }
    return (
      `<span class="bt-trade-score paper-hold-score has-tip ${escapeHtml(
        d.scoreCls || ""
      )}" ` +
      `data-score-detail="${escapeHtml(d.scoreDetail)}" data-score-tip="ranking" ` +
      `title="${escapeHtml(title)}">${escapeHtml(d.scoreText)}${badge}</span>`
    );
  }
  if (col.id === "intent") return escapeHtml(d.intentText);
  if (col.id === "buy") return escapeHtml(d.buyText);
  if (col.id === "sell") return escapeHtml(d.sellText);
  return escapeHtml(d[col.id] ?? "—");
}
