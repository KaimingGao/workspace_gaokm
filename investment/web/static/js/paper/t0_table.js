/** 做 T 回测表格：统一列定义，避免表头/数据错位。 */

import {
  escapeText,
  paperMetricClass,
  Y_EOD_TITLE,
  Y_TAU_TITLE,
  Y_ON_TITLE,
  Y_NOWCAST_TITLE,
} from "./fmt.js";
import {
  adaptiveSizingDayTip,
  normalizeYTauMap,
  yTauMapScoreTip,
} from "./execution_ui.js";
import { watchingScoreDetail } from "../quant/watching_render.js";
import { TRADE_TITLE } from "../quant/watching_quotes_ui.js";

export const SKIP_CAT_LABEL = {
  missing_scores: "缺ŷ快照",
  missing_minute: "缺分钟线",
  y_tau_flat: "y_τ横盘",
  y_trade_weak: "y_trade幅度不足",
  trade_tau_sign: "异号跳过",
  conflict: "旧冲突",
  amplitude: "振幅不足",
  directional_amplitude: "方向振幅",
  lot_size: "手数不足",
  tplus1: "T+1无可卖",
  path: "路径否决",
  trigger_miss: "未触达",
  other: "其它",
};

const TABLE_CLASS = "quant-weight-table paper-t0-table paper-t0-trades-table";

/** 列轨宽度：colgroup inline width + CSS 同源，避免 fixed 表头/体错位 */
const T0_TRADE_COL_W = {
  stock: "10.5rem",
  date: "78px",
  eod: "82px",
  tau: "82px",
  trade: "94px",
  on: "82px",
  nc: "82px",
  dir: "48px",
  process: "272px",
  retPct: "76px",
  pnl: "72px",
  exp: "72px",
  reason: "12rem",
  act: "52px",
};

/** 与数据中心主表一致：名称最多 6 字 + … */
function truncateName(name, max = 6) {
  const full = String(name || "").trim();
  const chars = Array.from(full);
  if (chars.length <= max) return full;
  return `${chars.slice(0, max).join("")}…`;
}

function t0Col(cls, key) {
  const w = T0_TRADE_COL_W[key];
  return `<col class="${cls}" style="width:${w}" />`;
}

/** 股票名 + 代码（对齐数据中心 watching-stock：名上行、码下行） */
export function stockCellHtml(row, fallback = {}) {
  const code = String((row && row.stock_code) || fallback.stock_code || "").trim();
  const name = String((row && row.stock_name) || fallback.stock_name || "").trim();
  const fullName = name && name !== code ? name : code || "—";
  const title = name && code && name !== code ? `${name} ${code}` : name || code;
  if (!name && !code) {
    return `<td class="rebalance-stock paper-t0-col-stock watching-stock">—</td>`;
  }
  const display = truncateName(fullName);
  return (
    `<td class="rebalance-stock paper-t0-col-stock watching-stock" title="${escapeText(title || fullName)}">` +
    `<span class="watching-name-row">` +
    `<span class="watching-name-text" title="${escapeText(fullName)}" data-full-name="${escapeText(
      fullName
    )}">${escapeText(display)}</span>` +
    `</span>` +
    (code
      ? `<span class="watching-code-sub">${escapeText(code)}</span>`
      : "") +
    `</td>`
  );
}

export function pickTradeDays(data) {
  const sample = (data && (data.trade_days_sample || data.days)) || [];
  return sample.filter(
    (d) =>
      Number(d.sold_qty) > 0 ||
      Number(d.bought_qty) > 0 ||
      Number(d.covered_qty) > 0 ||
      Number(d.sold_back_qty) > 0 ||
      Number(d.pnl) !== 0 ||
      Number(d.exposure_pnl) !== 0
  );
}

/** 多票 / 多代码时固定显示股票列，避免表头时有时无。 */
export function shouldShowStockColumn(data, days) {
  if (Number(data?.holding_count) > 1) return true;
  if (Number(data?.ok_count) > 1) return true;
  const codes = new Set();
  for (const d of days || []) {
    const c = String(d?.stock_code || "").trim();
    if (c) codes.add(c);
  }
  return codes.size > 1;
}

function yTauEnter(data) {
  const enter = data?.rules?.y_tau_enter;
  return enter != null && Number.isFinite(Number(enter)) ? Number(enter) : 0.25;
}

/** 敞口列：仅「允许隔夜 + 第二腿未 intraday 完成」时有值；强制回补时损益全在 PnL。 */
function exposureColTitle(data) {
  const forced =
    data?.rules?.must_cover_same_day === true ||
    data?.execution?.t0?.must_cover_same_day === true;
  const dualY = String(data?.rules?.direction || data?.execution?.t0?.direction || "dual_y") === "dual_y";
  if (forced) {
    return "隔夜敞口损益；已强制当日回补 → 全 0（损益在 PnL）";
  }
  if (dualY) {
    return "隔夜敞口损益；dual_y 默认 y_on 策略多强制收盘回补 → 常为 0（损益在 PnL）";
  }
  return "未 intraday 完成第二腿、且允许隔夜时的 mark-to-market；强制回补时为 0";
}

function fmtExposureCell(d) {
  const exp = Number(d.exposure_pnl);
  if (!Number.isFinite(exp) || Math.abs(exp) < 1e-9) {
    const cp = d.cover_policy;
    const forced =
      d.must_cover_same_day === true || (cp && cp.must_cover === true);
    const tip = forced
      ? cp?.reason
        ? `已强制回补：${cp.reason} · 损益计入 PnL`
        : "已强制收盘回补 · 损益计入 PnL"
      : "无隔夜敞口或未 mark 敞口";
    return { text: "0", tip };
  }
  return { text: String(exp), tip: "允许隔夜且第二腿未 intraday 完成的敞口损益" };
}

function pickScore(d, key) {
  const feats = d.direction_features || d.features || {};
  const scores = d.scores || {};
  const v = feats[key] ?? scores[key];
  return v != null && v !== "" && Number.isFinite(Number(v))
    ? `${Number(v).toFixed(2)}%`
    : "—";
}

function pickScoreNum(d, key) {
  const feats = d.direction_features || d.features || {};
  const scores = d.scores || {};
  const v = feats[key] ?? scores[key];
  const n = Number(v);
  return v != null && v !== "" && Number.isFinite(n) ? n : null;
}

/** 成交日 → 与观察/持仓同源的 tip 载荷（ŷ 字段映射到 predicted_score_*）。 */
function t0DayScoreItem(d, fallback = {}) {
  const scores = d.scores && typeof d.scores === "object" ? d.scores : {};
  const feats =
    (d.direction_features && typeof d.direction_features === "object"
      ? d.direction_features
      : null) ||
    (d.features && typeof d.features === "object" ? d.features : {}) ||
    {};
  const yEod = pickScoreNum(d, "y_eod");
  const yTau = pickScoreNum(d, "y_tau");
  const yTrade = pickScoreNum(d, "y_trade");
  const yOn = pickScoreNum(d, "y_on");
  const yNc = pickScoreNum(d, "y_nowcast");
  return {
    stock_code: d.stock_code || fallback.stock_code || null,
    stock_name: d.stock_name || fallback.stock_name || null,
    predicted_score_eod: yEod,
    predicted_score: yEod,
    predicted_score_tau: yTau,
    score_rem: yTau,
    predicted_score_blend: yTrade,
    decision_score: yTrade,
    predicted_score_on: yOn,
    predicted_score_nowcast: yNc,
    y_check: scores.y_check ?? feats.y_check ?? null,
    eod_trust: scores.eod_trust ?? feats.eod_trust ?? null,
    dual_score_window: scores.dual_score_window || feats.dual_score_window || "intraday",
    dual_score_fusion: scores.dual_score_fusion || feats.dual_score_fusion || null,
    dual_score_weights: scores.dual_score_weights || null,
    dual_score_head: scores.dual_score_head || null,
    as_of_tau: scores.as_of_tau || feats.as_of_tau || null,
    rem_tau: scores.rem_tau || null,
    gap_pct: scores.gap_pct ?? feats.gap_pct ?? null,
    predicted_score_eod_rem: scores.predicted_score_eod_rem ?? null,
    y_spec_tau: scores.y_spec_tau || null,
    features_tau: scores.features_tau || null,
    score_formula_terms: scores.score_formula_terms || scores.formula_terms || null,
    formula_terms: scores.score_formula_terms || scores.formula_terms || null,
    factor_coefficients: scores.factor_coefficients || null,
    formula_terms_tau: scores.formula_terms_tau || scores.score_formula_terms_tau || null,
    score_formula_terms_tau: scores.formula_terms_tau || scores.score_formula_terms_tau || null,
    factor_coefficients_tau: scores.factor_coefficients_tau || null,
    formula_terms_on: scores.formula_terms_on || scores.score_formula_terms_on || null,
    score_formula_terms_on: scores.formula_terms_on || scores.score_formula_terms_on || null,
    cluster_label: scores.cluster_label || "",
    weight_source: scores.weight_source || "",
    direction_reason: d.direction_reason || "",
    direction_score: d.direction_score,
    score_reasons: d.direction_reason ? [String(d.direction_reason)] : [],
    return_model_source:
      scores.return_model_source || scores._score_source || "t0_backtest_compute",
  };
}

function t0YScoreCell(tip, text, detailJson, title) {
  const colKey = tip === "nowcast" ? "nc" : tip;
  return (
    `<td class="num paper-t0-col-${escapeText(colKey)} paper-t0-col-y paper-t0-col-y-${escapeText(
      colKey
    )} paper-t0-y-score has-tip" data-score-tip="${escapeText(
      tip
    )}" data-score-detail="${detailJson}" title="${escapeText(title)}">${escapeText(
      text
    )}</td>`
  );
}

function pickTau(d) {
  const v = pickScore(d, "y_tau");
  if (v !== "—") return v;
  const ds = d.direction_score;
  return ds != null && ds !== "" && Number.isFinite(Number(ds))
    ? `${Number(ds).toFixed(2)}%`
    : "—";
}

function fmtT0LegTime(v) {
  if (v == null || v === "") return "";
  const s = String(v).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return "";
  const m = s.match(/(\d{1,2}:\d{2})(?::\d{2})?/);
  if (m) return m[1];
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return s.length > 11 ? s.slice(11, 16) : "";
}

function fmtT0LegPrice(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n >= 100 ? n.toFixed(2) : n.toFixed(3);
}

function tradeAtSortKey(at) {
  if (at == null || at === "") return Number.MAX_SAFE_INTEGER;
  const d = new Date(String(at));
  return Number.isNaN(d.getTime()) ? Number.MAX_SAFE_INTEGER : d.getTime();
}

function legSideKind(side) {
  const s = String(side || "").toLowerCase();
  if (s.endsWith("sell")) return "sell";
  if (s.endsWith("buy")) return "buy";
  return "";
}

function normalizeTradeLeg(t) {
  const side = legSideKind(t?.side);
  const qty = Math.round(Number(t?.shares) || 0);
  const price = Number(t?.price);
  const time = fmtT0LegTime(t?.at);
  const note = String(t?.note || "");
  const legKind =
    String(t?.leg_kind || "").trim() ||
    (/收盘|强制/.test(note) ? "eod_cover" : "trigger");
  const eod = legKind === "eod_cover";
  return { side, qty, price, time, eod, legKind, note, at: t?.at };
}

const LEG_KIND_TAG = {
  eod_cover: { label: "收", cls: "is-eod" },
  pm_chase: { label: "追", cls: "is-stop" },
  pm_degrade: { label: "追", cls: "is-stop" }, // 旧账本兼容
};

function legKindTag(legKind) {
  return LEG_KIND_TAG[legKind] || null;
}

function collectLegRecords(d) {
  const trades = (Array.isArray(d?.trades) ? d.trades : [])
    .filter((t) => t && (t.side || t.shares || t.price))
    .slice()
    .sort((a, b) => tradeAtSortKey(a.at) - tradeAtSortKey(b.at));
  if (trades.length) {
    return trades.map(normalizeTradeLeg).filter((l) => l.side && l.qty > 0);
  }
  const legs = tradeLegCells(d);
  const qty = legQtyCells(d);
  const out = [];
  if (Number(qty.sellQty) > 0) {
    out.push({
      side: "sell",
      qty: Number(qty.sellQty),
      price: legs.sellPx,
      time: fmtT0LegTime(legs.sellAt),
      eod: false,
      note: "",
      at: legs.sellAt,
    });
  }
  if (Number(qty.buyQty) > 0) {
    out.push({
      side: "buy",
      qty: Number(qty.buyQty),
      price: legs.buyPx,
      time: fmtT0LegTime(legs.buyAt),
      eod: false,
      note: "",
      at: legs.buyAt,
    });
  }
  return out;
}

function legPlainText(l) {
  const px = fmtT0LegPrice(l.price);
  const side = l.side === "sell" ? "卖" : "买";
  const tag = legKindTag(l.legKind);
  const tagTxt = tag ? `[${tag.label}]` : "";
  if (l.time) return `${l.time}${tagTxt}${side}${l.qty}@${px}`;
  if (l.eod) return `收盘${side}${l.qty}@${px}`;
  return `${tagTxt}${side}${l.qty}@${px}`;
}

function legChipHtml(l, showTimes) {
  const sideLabel = l.side === "sell" ? "卖" : "买";
  const cls = l.side === "sell" ? "is-sell" : "is-buy";
  const tag = legKindTag(l.legKind);
  let timeHtml = "";
  if (showTimes && l.time) {
    timeHtml =
      `<span class="paper-t0-leg-time${tag ? ` ${tag.cls}` : ""}">` +
      `${escapeText(l.time)}` +
      (tag ? `<span class="paper-t0-leg-eod-tag">${escapeText(tag.label)}</span>` : "") +
      `</span>`;
  } else if (showTimes && l.eod) {
    timeHtml = `<span class="paper-t0-leg-time is-eod">收盘</span>`;
  } else if (showTimes && tag) {
    timeHtml =
      `<span class="paper-t0-leg-time ${tag.cls}">` +
      `<span class="paper-t0-leg-eod-tag">${escapeText(tag.label)}</span>` +
      `</span>`;
  }
  const title = l.note ? ` title="${escapeText(l.note)}"` : "";
  return (
    `<span class="paper-t0-leg-chip ${cls}"${title}>` +
    `<span class="paper-t0-leg-side">${sideLabel}</span>` +
    `<span class="paper-t0-leg-body">` +
    (timeHtml ? `${timeHtml}<span class="paper-t0-leg-dot" aria-hidden="true">·</span>` : "") +
    `<span class="paper-t0-leg-qty">${l.qty}</span>` +
    `<span class="paper-t0-leg-at">@</span>` +
    `<span class="paper-t0-leg-px">${escapeText(fmtT0LegPrice(l.price))}</span>` +
    `</span></span>`
  );
}

/** 单日做 T 收益率：(PnL+敞口) / 动仓名义。 */
function dayReturnPct(d) {
  if (d.day_return_pct != null && Number.isFinite(Number(d.day_return_pct))) {
    return Number(d.day_return_pct);
  }
  const net = Number(d.pnl || 0) + Number(d.exposure_pnl || 0);
  const rev = d.direction === "reverse_t";
  const qty = rev
    ? Number(d.bought_qty || d.buy_shares || 0)
    : Number(d.sold_qty || d.sell_shares || 0);
  const legs = tradeLegCells(d);
  const px = rev
    ? Number(legs.buyPx || d.buy_price || 0)
    : Number(legs.sellPx || d.sell_price || 0);
  const notional = qty * px;
  if (!(notional > 0)) return null;
  return (net / notional) * 100;
}

function fmtDayReturnPct(d) {
  const pct = dayReturnPct(d);
  if (pct == null || !Number.isFinite(pct)) {
    return { text: "—", tip: "缺动仓股数或成交价，无法算收益%" };
  }
  const sign = pct > 0 ? "+" : "";
  return {
    text: `${sign}${pct.toFixed(2)}%`,
    tip: `(PnL ${d.pnl ?? 0} + 敞口 ${d.exposure_pnl ?? 0}) / 动仓名义`,
    pct,
  };
}

/** 结构化过程列 HTML（卖/买 chip + 箭头链） */
export function legProcessFlowHtml(d) {
  const legs = collectLegRecords(d);
  if (!legs.length) return `<span class="paper-t0-leg-empty">—</span>`;
  const showTimes = !!d.minute_path;
  const flowCls = showTimes ? "is-minute" : "is-daily";
  const chips = legs
    .map(
      (l, i) =>
        `${i ? `<span class="paper-t0-leg-join" aria-hidden="true"></span>` : ""}${legChipHtml(l, showTimes)}`
    )
    .join("");
  return `<div class="paper-t0-leg-flow ${flowCls}">${chips}</div>`;
}

/** 按 trades 时间序还原完整做 T 链路；无 trades 时回退摘要字段。 */
export function fmtLegProcess(d) {
  const legs = collectLegRecords(d);
  return legs.length ? legs.map(legPlainText).join(" → ") : "—";
}

function legQtyCells(d) {
  const rev = d.direction === "reverse_t";
  if (rev) {
    return {
      sellQty: d.sell_shares ?? d.sold_back_qty ?? 0,
      buyQty: d.buy_shares ?? d.bought_qty ?? 0,
    };
  }
  return {
    sellQty: d.sell_shares ?? d.sold_qty ?? 0,
    buyQty: d.buy_shares ?? d.covered_qty ?? d.bought_qty ?? 0,
  };
}

/** 从 trades 腿 + touch_* / 后端摘要提取卖/买时间与价格。 */
export function tradeLegCells(d) {
  if (d.sell_price != null || d.buy_price != null || d.sell_at || d.buy_at) {
    return {
      sellAt: d.sell_at,
      sellPx: d.sell_price,
      buyAt: d.buy_at,
      buyPx: d.buy_price,
    };
  }
  const trades = Array.isArray(d?.trades) ? d.trades : [];
  const sellLegs = trades.filter((t) => /sell$/i.test(String(t.side || "")));
  const buyLegs = trades.filter((t) => /buy$/i.test(String(t.side || "")));
  const rev = d.direction === "reverse_t";
  if (rev) {
    return {
      sellAt: sellLegs[sellLegs.length - 1]?.at || d.touch_sell_at,
      sellPx: sellLegs[sellLegs.length - 1]?.price,
      buyAt: buyLegs[0]?.at || d.touch_buy_at,
      buyPx: buyLegs[0]?.price,
    };
  }
  return {
    sellAt: sellLegs[0]?.at || d.touch_sell_at,
    sellPx: sellLegs[0]?.price,
    buyAt: buyLegs[buyLegs.length - 1]?.at || d.touch_cover_at || d.touch_buy_at,
    buyPx: buyLegs[buyLegs.length - 1]?.price,
  };
}

export function daysHaveIntradayTime(days) {
  return (days || []).some((d) => !!d.minute_path || collectLegRecords(d).some((l) => !!l.time));
}

function tradeColgroup(showStock, showReason = false, showDelete = false) {
  let html = "<colgroup>";
  if (showStock) html += t0Col("paper-t0-col-stock", "stock");
  html +=
    t0Col("paper-t0-col-date", "date") +
    t0Col("paper-t0-col-eod", "eod") +
    t0Col("paper-t0-col-tau", "tau") +
    t0Col("paper-t0-col-trade", "trade") +
    t0Col("paper-t0-col-on", "on") +
    t0Col("paper-t0-col-nc", "nc") +
    t0Col("paper-t0-col-dir", "dir") +
    t0Col("paper-t0-col-process", "process") +
    t0Col("paper-t0-col-ret", "retPct") +
    t0Col("paper-t0-col-pnl", "pnl") +
    t0Col("paper-t0-col-exp", "exp");
  if (showReason) html += t0Col("paper-t0-col-reason", "reason");
  if (showDelete) html += t0Col("paper-t0-col-act", "act");
  return `${html}</colgroup>`;
}

export const T0_TRADE_TABLE_MAX_ROWS = 50;

/**
 * 成交明细表（纸面 / 量化回测共用）
 * @param {{ data?: object, days: object[], caption?: string, maxRows?: number, showReason?: boolean, preserveOrder?: boolean, showDelete?: boolean }} opts
 */
export function buildT0TradeTableHtml(opts) {
  const {
    data = {},
    days,
    caption = "",
    maxRows = T0_TRADE_TABLE_MAX_ROWS,
    showReason = false,
    preserveOrder = false,
    showDelete = false,
  } = opts || {};
  if (!days || !days.length) return caption || "";
  const showStock = shouldShowStockColumn(data, days);
  const showTime = daysHaveIntradayTime(days);
  const enter = yTauEnter(data);
  const tauMap = normalizeYTauMap(data.rules && data.rules.y_tau_map);
  const scoreTip = yTauMapScoreTip(tauMap, enter);
  const fallback = {
    stock_code: data.stock_code,
    stock_name: data.stock_name,
  };

  const head =
    (showStock ? `<th scope="col" class="paper-t0-col-stock">股票</th>` : "") +
    `<th scope="col" class="paper-t0-col-date">日</th>` +
    `<th scope="col" class="paper-t0-col-eod num paper-t0-col-y paper-t0-col-y-eod" title="y_eod 收盘先验">y_eod</th>` +
    `<th scope="col" class="paper-t0-col-tau num paper-t0-col-y paper-t0-col-y-tau" title="${escapeText(scoreTip)}">y_τ</th>` +
    `<th scope="col" class="paper-t0-col-trade num paper-t0-col-y paper-t0-col-y-trade" title="y_trade 可交易性">y_trade</th>` +
    `<th scope="col" class="paper-t0-col-on num paper-t0-col-y paper-t0-col-y-on" title="y_on 尾盘/隔夜">y_on</th>` +
    `<th scope="col" class="paper-t0-col-nc num paper-t0-col-y paper-t0-col-y-nc" title="y_nowcast · Kalman 权昨收">y_nc</th>` +
    `<th scope="col" class="paper-t0-col-dir">向</th>` +
    `<th scope="col" class="paper-t0-col-process" title="5m 第一触达时点；缺分钟日已跳过">过程</th>` +
    `<th scope="col" class="paper-t0-col-ret num" title="(PnL+敞口)/动仓名义">收益%</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `<th scope="col" class="paper-t0-col-exp num" title="${escapeText(exposureColTitle(data))}">敞口</th>` +
    (showReason ? `<th scope="col" class="paper-t0-col-reason">说明</th>` : "") +
    (showDelete ? `<th scope="col" class="paper-t0-col-act">操作</th>` : "");

  const rows = (preserveOrder ? days.slice(0, maxRows) : days.slice(-maxRows).reverse())
    .map((d) => {
      const skipped = !!d.skipped;
      const dir = d.direction === "reverse_t" ? "反" : d.direction === "long_t" ? "正" : "—";
      const tau = pickTau(d);
      const scoreDetailJson = escapeText(
        watchingScoreDetail(t0DayScoreItem(d, fallback))
      );
      const legs = tradeLegCells(d);
      const qty = legQtyCells(d);
      const process = fmtLegProcess(d);
      const expCell = fmtExposureCell(d);
      const retCell = fmtDayReturnPct(d);
      const sizingTip = adaptiveSizingDayTip(d, data.rules || {});
      const reason = String(d.reason || d.direction_reason || d.error || "").trim();
      const code = String(d.stock_code || fallback.stock_code || "").trim();
      const name = String(d.stock_name || fallback.stock_name || code).trim();
      const legTip =
        (sizingTip ? `${sizingTip} · ` : "") +
        (skipped && reason
          ? reason
          : process !== "—"
            ? process
            : `卖 ${qty.sellQty}股 @ ${fmtT0LegPrice(legs.sellPx)} · 买 ${qty.buyQty}股 @ ${fmtT0LegPrice(legs.buyPx)}` +
              (showTime ? "" : " · 缺触达时刻"));
      const tauTitle = d.direction_reason
        ? `${Y_TAU_TITLE} · ${d.direction_reason}`
        : Y_TAU_TITLE;
      const delBtn =
        showDelete && code && !skipped
          ? `<button type="button" class="paper-t0-ledger-del" data-code="${escapeText(
              code
            )}" data-name="${escapeText(name)}" title="删除并冲正账本">删除</button>`
          : showDelete
            ? `<span class="paper-t0-leg-empty">—</span>`
            : "";
      return (
        `<tr class="${skipped ? "is-skipped" : ""}" data-code="${escapeText(code)}">` +
        (showStock ? stockCellHtml(d, fallback) : "") +
        `<td class="paper-t0-col-date">${escapeText(d.date || "")}</td>` +
        t0YScoreCell("eod", pickScore(d, "y_eod"), scoreDetailJson, Y_EOD_TITLE) +
        t0YScoreCell("tau", tau, scoreDetailJson, tauTitle) +
        t0YScoreCell("trade", pickScore(d, "y_trade"), scoreDetailJson, TRADE_TITLE) +
        t0YScoreCell("on", pickScore(d, "y_on"), scoreDetailJson, Y_ON_TITLE) +
        t0YScoreCell("nowcast", pickScore(d, "y_nowcast"), scoreDetailJson, Y_NOWCAST_TITLE) +
        `<td class="paper-t0-col-dir">${dir}</td>` +
        `<td class="paper-t0-col-process" title="${escapeText(legTip)}">${
          skipped ? `<span class="paper-t0-leg-empty">—</span>` : legProcessFlowHtml(d)
        }</td>` +
        `<td class="num paper-t0-col-ret ${paperMetricClass(retCell.pct)}" title="${escapeText(
          retCell.tip
        )}">${escapeText(retCell.text)}</td>` +
        `<td class="num paper-t0-col-pnl ${paperMetricClass(d.pnl)}">${escapeText(String(d.pnl ?? 0))}</td>` +
        `<td class="num paper-t0-col-exp ${paperMetricClass(d.exposure_pnl)}" title="${escapeText(
          expCell.tip
        )}">${escapeText(expCell.text)}</td>` +
        (showReason
          ? `<td class="paper-t0-col-reason" title="${escapeText(reason)}">${escapeText(
              reason || (skipped ? "跳过" : "")
            )}</td>`
          : "") +
        (showDelete ? `<td class="paper-t0-col-act">${delBtn}</td>` : "") +
        `</tr>`
      );
    })
    .join("");

  const moreHint =
    days.length > maxRows
      ? `<p class="quant-trades-caption paper-t0-table-more">表内最近 ${maxRows} 笔 · 样本共 ${days.length} 笔 · 可滚动查看</p>`
      : "";

  return (
    `${caption}` +
    `<div class="quant-weight-table-wrap paper-t0-trades-wrap">` +
    `<table class="${TABLE_CLASS}">` +
    tradeColgroup(showStock, showReason, showDelete) +
    `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>` +
    `</div>` +
    moreHint
  );
}
