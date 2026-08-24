/** 做 T 回测表格：统一列定义，避免表头/数据错位。 */

import { escapeText, paperMetricClass } from "./fmt.js";

export const SKIP_CAT_LABEL = {
  missing_scores: "缺ŷ快照",
  y_tau_flat: "y_τ横盘",
  y_trade_weak: "y_trade不足",
  conflict: "先验冲突",
  amplitude: "振幅不足",
  lot_size: "手数不足",
  path: "路径否决",
  trigger_miss: "未触达",
  other: "其它",
};

const TABLE_CLASS = "quant-weight-table paper-t0-table";

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
  pnl: "72px",
  exp: "72px",
};

function t0Col(cls, key) {
  const w = T0_TRADE_COL_W[key];
  return `<col class="${cls}" style="width:${w}" />`;
}

/** 股票名 + 代码 */
export function stockCellHtml(row, fallback = {}) {
  const code = String((row && row.stock_code) || fallback.stock_code || "").trim();
  const name = String((row && row.stock_name) || fallback.stock_name || "").trim();
  const title = name && code && name !== code ? `${name} ${code}` : name || code;
  if (!name && !code) return `<td class="rebalance-stock paper-t0-col-stock">—</td>`;
  if (!name || name === code) {
    return (
      `<td class="rebalance-stock paper-t0-col-stock" title="${escapeText(title)}">` +
      `<span class="rebalance-stock-name">${escapeText(code || "—")}</span></td>`
    );
  }
  return (
    `<td class="rebalance-stock paper-t0-col-stock" title="${escapeText(title)}">` +
    `<span class="rebalance-stock-main">` +
    `<span class="rebalance-stock-name">` +
    `<span class="rebalance-stock-name-text">${escapeText(name)}</span>` +
    `</span>` +
    `<span class="rebalance-stock-code">${escapeText(code)}</span>` +
    `</span></td>`
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

function pickScore(d, key) {
  const feats = d.direction_features || d.features || {};
  const scores = d.scores || {};
  const v = feats[key] ?? scores[key];
  return v != null && v !== "" && Number.isFinite(Number(v)) ? Number(v).toFixed(2) : "—";
}

function pickTau(d) {
  const v = pickScore(d, "y_tau");
  if (v !== "—") return v;
  const ds = d.direction_score;
  return ds != null && ds !== "" && Number.isFinite(Number(ds)) ? Number(ds).toFixed(2) : "—";
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
  const legKind = String(t?.leg_kind || "").trim() || (/收盘|强制/.test(note) ? "eod_cover" : "trigger");
  const eod = legKind === "eod_cover";
  return { side, qty, price, time, eod, legKind, note, at: t?.at };
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
  if (l.time) return `${l.time}${side}${l.qty}@${px}`;
  if (l.eod) return `收盘${side}${l.qty}@${px}`;
  return `${side}${l.qty}@${px}`;
}

function legChipHtml(l, showTimes) {
  const sideLabel = l.side === "sell" ? "卖" : "买";
  const cls = l.side === "sell" ? "is-sell" : "is-buy";
  let timeHtml = "";
  if (showTimes && l.time) {
    timeHtml =
      `<span class="paper-t0-leg-time${l.eod ? " is-eod" : ""}">` +
      `${escapeText(l.time)}` +
      (l.eod ? `<span class="paper-t0-leg-eod-tag">收</span>` : "") +
      `</span>`;
  } else if (showTimes && l.eod) {
    timeHtml = `<span class="paper-t0-leg-time is-eod">收盘</span>`;
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

function tradeColgroup(showStock) {
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
    t0Col("paper-t0-col-pnl", "pnl") +
    t0Col("paper-t0-col-exp", "exp");
  return `${html}</colgroup>`;
}

function skipColgroup() {
  return (
    `<colgroup>` +
    `<col class="paper-t0-col-stock" />` +
    `<col class="paper-t0-col-date" />` +
    `<col class="paper-t0-col-cat" />` +
    `<col class="paper-t0-col-tau" />` +
    `<col class="paper-t0-col-reason" />` +
    `</colgroup>`
  );
}

export const T0_TRADE_TABLE_MAX_ROWS = 50;

/**
 * 成交明细表（纸面 / 量化回测共用）
 * @param {{ data?: object, days: object[], caption?: string, maxRows?: number }} opts
 */
export function buildT0TradeTableHtml(opts) {
  const { data = {}, days, caption = "", maxRows = T0_TRADE_TABLE_MAX_ROWS } = opts || {};
  if (!days || !days.length) return caption || "";
  const showStock = shouldShowStockColumn(data, days);
  const showTime = daysHaveIntradayTime(days);
  const enter = yTauEnter(data);
  const scoreTip =
    `dual_y 方向分≈y_τ（%点）：≥+${enter} 正T，≤-${enter} 反T；与 y_eod 冲突或 y_trade 不足则跳过。`;
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
    `<th scope="col" class="paper-t0-col-process" title="5m：第一触达时点；日线：仅价量代理、无具体时刻">过程</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `<th scope="col" class="paper-t0-col-exp num">敞口</th>`;

  const rows = days
    .slice(-maxRows)
    .reverse()
    .map((d) => {
      const dir = d.direction === "reverse_t" ? "反" : d.direction === "long_t" ? "正" : "—";
      const tau = pickTau(d);
      const code = d.stock_code || fallback.stock_code || "";
      const name = d.stock_name || fallback.stock_name || "";
      const detail = JSON.stringify({
        kind: "t0_direction",
        direction_score: d.direction_score,
        direction_reason: d.direction_reason || "",
        direction: d.direction || "",
        features: d.direction_features || d.features || null,
        direction_features: d.direction_features || d.features || null,
        stock_code: code,
        stock_name: name,
        date: d.date || "",
        dir_enter: enter,
      });
      const legs = tradeLegCells(d);
      const qty = legQtyCells(d);
      const process = fmtLegProcess(d);
      const legTip =
        process !== "—"
          ? process
          : `卖 ${qty.sellQty}股 @ ${fmtT0LegPrice(legs.sellPx)} · 买 ${qty.buyQty}股 @ ${fmtT0LegPrice(legs.buyPx)}` +
            (showTime ? "" : " · 日线代理无日内时点");
      return (
        `<tr>` +
        (showStock ? stockCellHtml(d, fallback) : "") +
        `<td class="paper-t0-col-date">${escapeText(d.date || "")}</td>` +
        `<td class="num paper-t0-col-eod paper-t0-col-y paper-t0-col-y-eod">${pickScore(d, "y_eod")}</td>` +
        `<td class="num paper-t0-col-tau paper-t0-col-y paper-t0-col-y-tau paper-t0-dir-score has-tip" data-score-detail="${escapeText(
          detail
        )}" title="悬停查看方向分详情">${tau}</td>` +
        `<td class="num paper-t0-col-trade paper-t0-col-y paper-t0-col-y-trade">${pickScore(d, "y_trade")}</td>` +
        `<td class="num paper-t0-col-on paper-t0-col-y paper-t0-col-y-on">${pickScore(d, "y_on")}</td>` +
        `<td class="num paper-t0-col-nc paper-t0-col-y paper-t0-col-y-nc">${pickScore(d, "y_nowcast")}</td>` +
        `<td class="paper-t0-col-dir">${dir}</td>` +
        `<td class="paper-t0-col-process" title="${escapeText(legTip)}">${legProcessFlowHtml(d)}</td>` +
        `<td class="num paper-t0-col-pnl ${paperMetricClass(d.pnl)}">${escapeText(String(d.pnl ?? 0))}</td>` +
        `<td class="num paper-t0-col-exp ${paperMetricClass(d.exposure_pnl)}">${escapeText(
          String(d.exposure_pnl ?? 0)
        )}</td></tr>`
      );
    })
    .join("");

  const moreHint =
    days.length > maxRows
      ? `<p class="quant-trades-caption paper-t0-table-more">表内最近 ${maxRows} 笔 · 样本共 ${days.length} 笔 · 可滚动查看</p>`
      : "";

  const timeHint = showTime
    ? `<p class="quant-trades-caption paper-t0-table-more">5m 路径：过程列时间为第一触达时点（收盘回补取末根 K 线）</p>`
    : `<p class="quant-trades-caption paper-t0-table-more">日线代理：仅还原卖买价量，无日内时点；勾「5分钟」可回溯触达时刻</p>`;

  return (
    `${caption}` +
    `<table class="${TABLE_CLASS}">` +
    tradeColgroup(showStock) +
    `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>` +
    timeHint +
    moreHint
  );
}

/** 跳过样例表 */
export function buildT0SkipTableHtml(skips, fallback = {}) {
  if (!skips || !skips.length) return "";
  const rows = skips
    .slice(0, 10)
    .map((d) => {
      const cat = SKIP_CAT_LABEL[d.skip_category] || d.skip_category || "—";
      const feats = d.direction_features || {};
      const scores = d.scores || {};
      const tauRaw = d.direction_score ?? feats.y_tau ?? scores.y_tau;
      const tau =
        tauRaw != null && tauRaw !== "" && Number.isFinite(Number(tauRaw))
          ? Number(tauRaw).toFixed(2)
          : "—";
      return (
        `<tr>` +
        `${stockCellHtml(d, fallback)}` +
        `<td class="paper-t0-col-date">${escapeText(d.date || "")}</td>` +
        `<td class="paper-t0-col-cat">${escapeText(cat)}</td>` +
        `<td class="num paper-t0-col-tau">${tau}</td>` +
        `<td class="paper-t0-col-reason">${escapeText(d.reason || "跳过")}</td></tr>`
      );
    })
    .join("");

  return (
    `<table class="${TABLE_CLASS} paper-t0-skip-table">` +
    skipColgroup() +
    `<thead><tr>` +
    `<th scope="col" class="paper-t0-col-stock">股票</th>` +
    `<th scope="col" class="paper-t0-col-date">日</th>` +
    `<th scope="col" class="paper-t0-col-cat">类别</th>` +
    `<th scope="col" class="paper-t0-col-tau num">τ</th>` +
    `<th scope="col" class="paper-t0-col-reason">原因</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`
  );
}
