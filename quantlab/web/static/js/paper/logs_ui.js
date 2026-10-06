/** Paper operation/fund logs render helper. */

import { escapeText, fmtPct, fmtScore, metricCls } from "./fmt.js";

const TYPE_LABELS = {
  deposit: "注资",
  withdraw: "减资",
  reset: "回零",
  buy: "买入",
  sell: "卖出",
  rebalance: "调仓",
  cluster_pool_rebalance: "分池",
  watching_matrix_rebalance: "调仓",
  t0_batch: "做T",
  t0_void: "冲正",
  sync_paper: "建仓",
  init: "初始化",
};

function typeLabelOf(l) {
  const t = String(l.type || "").trim();
  const stored = String(l.type_label || "").trim();
  if (stored && stored !== t) return stored;
  return TYPE_LABELS[t] || stored || t || "";
}

function typeCls(t) {
  const m = {
    deposit: "is-deposit",
    withdraw: "is-withdraw",
    reset: "is-reset",
    buy: "is-buy",
    sell: "is-sell",
    rebalance: "is-rebalance",
    cluster_pool_rebalance: "is-cluster",
    watching_matrix_rebalance: "is-rebalance",
    t0_batch: "is-rebalance",
    sync_paper: "is-sync",
    init: "is-init",
  };
  return m[t] || "is-default";
}

function fmtTs(ts) {
  if (!ts) return { date: "—", time: "" };
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) {
      const raw = String(ts);
      return { date: raw.slice(0, 10) || "—", time: raw.slice(11, 16) || "" };
    }
    const pad = (n) => String(n).padStart(2, "0");
    const y = d.getFullYear();
    const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const today = new Date();
    const sameYear = y === today.getFullYear();
    return {
      date: sameYear ? md : `${y}-${md}`,
      time: hm,
      title: `${y}-${md} ${hm}`,
    };
  } catch (_) {
    const raw = String(ts);
    return { date: raw.slice(0, 10) || "—", time: raw.slice(11, 16) || "" };
  }
}

function fmtLogMoney(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs >= 10000) {
    const w = n / 10000;
    return `${w.toFixed(Math.abs(w) >= 100 || Number.isInteger(w * 10) ? 1 : 2)}万`;
  }
  return n.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
  });
}

function fmtLogPrice(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  return n.toLocaleString("zh-CN", {
    maximumFractionDigits: n >= 100 ? 2 : 3,
  });
}

function originLabelOf(l) {
  const meta = l.meta || {};
  const source = String(meta.source || "").trim();
  const origin = String(meta.origin || "").trim();
  if (origin === "t0" && source === "paper_t0_auto") return "做T·自动";
  if (origin === "manual") return "手动";
  if (origin === "t0") return "做T";
  if (origin === "strategy") return "策略";
  if (origin === "mixed") return "手动+策略";
  if (origin === "cluster" || origin === "research") return "研究枢纽";
  if (origin === "matrix") return source === "auto" ? "策略·自动" : "策略";
  const detail = String(l.detail || "");
  if (detail.includes("[调仓]")) return "策略";
  if (l.type === "sync_paper") return "观察建仓";
  if (l.type === "t0_batch") return source === "paper_t0_auto" ? "做T·自动" : "做T";
  if (l.type === "cluster_pool_rebalance") return "研究枢纽";
  if (l.type === "watching_matrix_rebalance") {
    return source === "auto" ? "策略·自动" : "策略";
  }
  if (l.type === "rebalance") return "策略汇总";
  if (meta.cluster_mode || meta.source === "research_hub") return "研究枢纽";
  if (meta.matrix_mode) return "策略";
  if (l.type === "buy" || l.type === "sell") return "手动";
  return "";
}

export function logMatchesTradingFilter(l, filter) {
  const f = String(filter || "all").trim() || "all";
  if (f === "all") return true;
  const meta = l.meta || {};
  const origin = String(meta.origin || "").trim();
  const source = String(meta.source || "").trim();
  const type = String(l.type || "");
  const detail = String(l.detail || "");
  if (f === "manual") {
    if (type === "sync_paper") return false;
    if (type === "rebalance" || type === "cluster_pool_rebalance" || type === "watching_matrix_rebalance" || type === "t0_batch") return false;
    if (origin === "t0" || origin === "strategy" || origin === "cluster" || origin === "matrix") return false;
    if (detail.includes("[调仓]") || detail.startsWith("做T")) return false;
    return type === "buy" || type === "sell" || origin === "manual" || !origin;
  }
  if (f === "strategy") {
    return (
      type === "rebalance" ||
      type === "cluster_pool_rebalance" ||
      type === "watching_matrix_rebalance" ||
      origin === "strategy" ||
      origin === "cluster" ||
      origin === "matrix" ||
      detail.includes("[调仓]")
    );
  }
  if (f === "t0") {
    return origin === "t0" || type === "t0_batch" || detail.includes("做T");
  }
  if (f === "sync") {
    return type === "sync_paper";
  }
  return true;
}

function renderLogItem(l, { tradeCols = false } = {}) {
  const meta = l.meta || {};
  const cls = typeCls(l.type);
  const label = escapeText(typeLabelOf(l));
  let code = String(meta.stock_code || "").trim();
  let name = String(meta.stock_name || "").trim();
  let shares = meta.shares != null && meta.shares !== "" ? Number(meta.shares) : NaN;
  let price = meta.price != null && meta.price !== "" ? Number(meta.price) : NaN;
  let amount =
    meta.amount != null && meta.amount !== ""
      ? Number(meta.amount)
      : meta.actual_cost != null
        ? Number(meta.actual_cost)
        : NaN;
  if (
    (l.type === "buy" || l.type === "sell" || l.type === "sync_paper") &&
    (!code || !Number.isFinite(shares) || !Number.isFinite(price))
  ) {
    const detail = String(l.detail || "");
    const m = detail.match(
      /(?:做T)?(?:买入|卖出|加入模拟)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*股(?:\s*@\s*(\d+(?:\.\d+)?))?/
    );
    if (m) {
      const token = String(m[1] || "").trim();
      if (!name && !code) {
        if (/^\d{6}(\.\w+)?$/.test(token)) code = token;
        else name = token;
      }
      if (!Number.isFinite(shares)) shares = Number(m[2]);
      if (!Number.isFinite(price) && m[3]) price = Number(m[3]);
    }
  }
  if (!Number.isFinite(amount) && Number.isFinite(shares) && Number.isFinite(price)) {
    amount = shares * price;
  }
  const origin = originLabelOf(l);
  const hasTradeBits =
    !!(name || code) ||
    Number.isFinite(shares) ||
    Number.isFinite(price) ||
    Number.isFinite(amount);

  let primary = "";
  let secondaryParts = [];
  if (l.type === "rebalance" || l.type === "cluster_pool_rebalance" || l.type === "watching_matrix_rebalance" || l.type === "t0_batch") {
    const buyN = meta.buy_count != null ? Number(meta.buy_count) : NaN;
    const sellN = meta.sell_count != null ? Number(meta.sell_count) : NaN;
    const tradeN = meta.trade_count != null ? Number(meta.trade_count) : NaN;
    const strategy = String(meta.strategy || "").trim();
    const ver =
      meta.cluster_version != null && meta.cluster_version !== ""
        ? `v${meta.cluster_version}`
        : "";
    if (l.type === "t0_batch") {
      primary = String(l.detail || "做T汇总");
    } else {
      const fallback =
        l.type === "cluster_pool_rebalance" ? "分池调仓" : "策略调仓";
      primary =
        Number.isFinite(buyN) || Number.isFinite(sellN)
          ? `买入 ${Number.isFinite(buyN) ? buyN : 0} 笔 · 卖出 ${Number.isFinite(sellN) ? sellN : 0} 笔`
          : String(l.detail || fallback);
    }
    if (Number.isFinite(tradeN) && l.type === "t0_batch") {
      secondaryParts.push(`${tradeN} 笔`);
    }
    if (ver) secondaryParts.push(ver);
    if (strategy) secondaryParts.push(strategy);
    if (origin) secondaryParts.push(origin);
  } else if ((l.type === "buy" || l.type === "sell" || l.type === "sync_paper") && hasTradeBits) {
    const bits = [];
    bits.push(name || code || "—");
    if (Number.isFinite(shares)) bits.push(`${shares}股`);
    if (Number.isFinite(amount)) bits.push(`${fmtLogMoney(amount)}元`);
    primary = bits.join(" · ");
    if (code && name) secondaryParts.push(code);
    if (origin) secondaryParts.push(origin);
    const feeBits = [];
    const commission =
      meta.commission != null && meta.commission !== "" ? Number(meta.commission) : NaN;
    const stamp =
      meta.stamp_duty != null && meta.stamp_duty !== ""
        ? Number(meta.stamp_duty)
        : meta.stamp_tax != null && meta.stamp_tax !== ""
          ? Number(meta.stamp_tax)
          : NaN;
    const fees = meta.fees != null && meta.fees !== "" ? Number(meta.fees) : NaN;
    const est = !!meta.fees_estimated;
    const feeSuffix = est ? "（估）" : "";
    if (Number.isFinite(commission)) {
      feeBits.push(`佣金 ${fmtLogMoney(commission)}${feeSuffix}`);
    }
    if (Number.isFinite(stamp)) {
      feeBits.push(`印花税 ${fmtLogMoney(stamp)}${feeSuffix}`);
    }
    if (
      Number.isFinite(fees) &&
      (!Number.isFinite(commission) || !Number.isFinite(stamp))
    ) {
      feeBits.push(`费用 ${fmtLogMoney(fees)}${feeSuffix}`);
    } else if (
      Number.isFinite(fees) &&
      Number.isFinite(commission) &&
      Number.isFinite(stamp) &&
      Math.abs(fees - (commission + stamp)) > 0.02
    ) {
      feeBits.push(`合计 ${fmtLogMoney(fees)}${feeSuffix}`);
    }
    if (feeBits.length) secondaryParts.push(feeBits.join(" · "));
    else if (String(meta.cost_model || "") === "zero") secondaryParts.push("零成本");
    if (meta.ranking != null && Number.isFinite(Number(meta.ranking))) {
      secondaryParts.push(`ranking ${fmtScore(Number(meta.ranking), { signed: true })}`);
    } else if (meta.score != null && Number.isFinite(Number(meta.score))) {
      secondaryParts.push(`评分 ${fmtScore(meta.score)}`);
    }
    let note = String(meta.note || "").trim();
    if (note && meta.ranking != null) {
      note = note
        .replace(/\s*ranking=[^\s·]+/g, "")
        .replace(/\s*·\s*·\s*/g, " · ")
        .replace(/^\s*·\s*|\s*·\s*$/g, "")
        .trim();
    }
    if (note) secondaryParts.push(note);
    if (meta.sentiment_prior) {
      secondaryParts.unshift("舆情参考 · 不调仓");
    }
    if (meta.market_prior) {
      secondaryParts.unshift("M prior · 不改 ŷ");
    }
  } else {
    primary = String(l.detail || "—");
    if (origin && (l.type === "buy" || l.type === "sell" || l.type === "sync_paper")) {
      secondaryParts.push(origin);
    }
  }
  let pnlHtml = "";
  if (l.type === "sell") {
    const pnlPct =
      meta.pnl_pct != null && meta.pnl_pct !== "" ? Number(meta.pnl_pct) : NaN;
    const pnlAbs = meta.pnl != null && meta.pnl !== "" ? Number(meta.pnl) : NaN;
    if (Number.isFinite(pnlPct)) {
      pnlHtml =
        `<span class="paper-log-pnl ${metricCls(pnlPct)}">` +
        `${escapeText(fmtPct(pnlPct, { signed: true }))}</span>`;
    } else if (Number.isFinite(pnlAbs)) {
      pnlHtml =
        `<span class="paper-log-pnl ${metricCls(pnlAbs)}">` +
        `${escapeText(fmtLogMoney(pnlAbs))}元</span>`;
    }
  }
  const secondary = secondaryParts.filter(Boolean).join(" · ");
  const when = fmtTs(l.ts);
  const priceHtml = Number.isFinite(price)
    ? `<span class="paper-log-price" title="成交价">${escapeText(fmtLogPrice(price))}</span>`
    : `<span class="paper-log-price is-empty" title="成交价">—</span>`;
  const timeHtml =
    `<span class="paper-log-ts">` +
    `<span class="paper-log-date">${escapeText(when.date)}</span>` +
    `<span class="paper-log-time">${escapeText(when.time || "—")}</span>` +
    `</span>`;
  const colCls = tradeCols ? " has-cols-4" : "";
  const priorBadges =
    (meta.market_prior
      ? `<span class="paper-market-prior-badge" title="M prior · 不改 ŷ">M</span> `
      : "") +
    (meta.sentiment_prior
      ? `<span class="watching-sent-badge is-bear" title="舆情参考 · 不调仓">S</span> `
      : "");
  return (
    `<div class="paper-log-item ${cls}${colCls}" title="${escapeText(when.title || `${when.date} ${when.time}`)}">` +
    `<span class="paper-log-type">${label}</span>` +
    `<div class="paper-log-body">` +
    `<div class="paper-log-primary">${priorBadges}${escapeText(primary)}` +
    (pnlHtml ? ` · ${pnlHtml}` : "") +
    `</div>` +
    (secondary ? `<div class="paper-log-secondary">${escapeText(secondary)}</div>` : "") +
    `</div>` +
    timeHtml +
    (tradeCols ? priceHtml : "") +
    `</div>`
  );
}

function renderTradingLogHead() {
  return (
    `<div class="paper-log-head" aria-hidden="true">` +
    `<span class="paper-log-head-type">类型</span>` +
    `<span class="paper-log-head-body">标的</span>` +
    `<span class="paper-log-head-time">时间</span>` +
    `<span class="paper-log-head-price">价格</span>` +
    `</div>`
  );
}

function dayKey(ts) {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "未知日期";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  } catch (_) {
    return "未知日期";
  }
}

function dayLabel(key, items) {
  let base = key;
  if (/^\d{4}-\d{2}-\d{2}$/.test(key)) {
    const today = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const todayKey = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
    const y = new Date(today);
    y.setDate(y.getDate() - 1);
    const yKey = `${y.getFullYear()}-${pad(y.getMonth() + 1)}-${pad(y.getDate())}`;
    if (key === todayKey) base = `今天 ${key.slice(5)}`;
    else if (key === yKey) base = `昨天 ${key.slice(5)}`;
    else base = key.slice(5);
  }
  if (!items || !items.length) return base;
  const buyN = items.filter((x) => x.type === "buy" || x.type === "sync_paper").length;
  const sellN = items.filter((x) => x.type === "sell").length;
  const clusterN = items.filter((x) => x.type === "cluster_pool_rebalance").length;
  const matrixN = items.filter((x) => x.type === "watching_matrix_rebalance").length;
  const parts = [];
  if (buyN) parts.push(`买${buyN}`);
  if (sellN) parts.push(`卖${sellN}`);
  if (clusterN) parts.push(`分池${clusterN}`);
  if (matrixN) parts.push(`调仓${matrixN}`);
  return parts.length ? `${base} · ${parts.join(" ")}` : base;
}

function renderLogs(list, kind, showAllState) {
  if (!list.length) return `<p class="watching-table-empty">暂无记录</p>`;
  const reversed = list.slice().reverse();
  const limit = 20;
  const showAll = !!(showAllState && showAllState[kind]);
  const visible = showAll ? reversed : reversed.slice(0, limit);
  const groups = [];
  let cur = null;
  visible.forEach((l) => {
    const key = dayKey(l.ts);
    if (!cur || cur.key !== key) {
      cur = { key, items: [] };
      groups.push(cur);
    }
    cur.items.push(l);
  });
  const body = groups
    .map((g) => {
      const items = g.items.map((l) => renderLogItem(l, { tradeCols: kind === "trading" })).join("");
      return (
        `<div class="paper-log-day">` +
        `<div class="paper-log-day-label">${escapeText(dayLabel(g.key, g.items))}</div>` +
        items +
        `</div>`
      );
    })
    .join("");
  const head = kind === "trading" ? renderTradingLogHead() : "";
  const more =
    !showAll && reversed.length > limit
      ? `<button type="button" class="dialog-btn secondary paper-log-more" data-log-kind="${escapeText(
          kind
        )}">展开全部 ${reversed.length} 笔</button>`
      : showAll && reversed.length > limit
        ? `<button type="button" class="dialog-btn secondary paper-log-more" data-log-kind="${escapeText(
            kind
          )}">只看近 ${limit} 笔</button>`
        : "";
  return head + body + more;
}

export function buildPaperLogsCsv(logs, { tradingOnly = true } = {}) {
  const tradingTypes = new Set([
    "buy",
    "sell",
    "rebalance",
    "cluster_pool_rebalance",
    "watching_matrix_rebalance",
    "sync_paper",
  ]);
  const rows = (logs || []).filter((l) => {
    if (l && l.voided) return false;
    return tradingOnly ? tradingTypes.has(l.type) : true;
  });
  const esc = (v) => {
    const s = v == null ? "" : String(v);
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };
  const header = [
    "ts",
    "type",
    "type_label",
    "code",
    "name",
    "shares",
    "price",
    "amount",
    "sentiment_prior",
    "market_prior",
    "origin",
    "note",
    "detail",
  ];
  const lines = [header.join(",")];
  for (const l of rows) {
    const meta = l.meta || {};
    lines.push(
      [
        l.ts,
        l.type,
        l.type_label,
        meta.stock_code,
        meta.stock_name,
        meta.shares,
        meta.price,
        meta.amount != null ? meta.amount : meta.actual_cost,
        meta.sentiment_prior ? 1 : 0,
        meta.market_prior ? 1 : 0,
        meta.origin,
        meta.note,
        l.detail,
      ]
        .map(esc)
        .join(",")
    );
  }
  return lines.join("\n");
}

export function buildPaperLogsView(data, showAllState, logFilter = "all") {
  const logs = (data && data.operation_log) || [];
  const tradingTypes = new Set([
    "buy",
    "sell",
    "rebalance",
    "cluster_pool_rebalance",
    "watching_matrix_rebalance",
    "t0_batch",
    "sync_paper",
  ]);
  const fundTypes = new Set(["init", "deposit", "withdraw", "reset"]);
  const tradingLogsAll = logs.filter(
    (l) => tradingTypes.has(l.type) && !l.voided
  );
  const tradingLogs = tradingLogsAll.filter((l) => logMatchesTradingFilter(l, logFilter));
  const fundLogs = logs.filter((l) => fundTypes.has(l.type));
  return {
    tradingHtml: renderLogs(tradingLogs, "trading", showAllState),
    fundHtml: renderLogs(fundLogs, "fund", showAllState),
    hasFundLogs: fundLogs.length > 0,
    tradingCount: tradingLogs.length,
    tradingCountAll: tradingLogsAll.length,
    tradingLogs,
    tradingLogsAll,
    allLogs: logs,
  };
}

