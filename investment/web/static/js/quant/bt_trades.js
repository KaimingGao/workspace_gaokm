/**
 * Top-K 回测模拟成交表：列定义与纯变换。
 */

export const BT_SIM_TRADE_COLS_BASE = [
  { id: "signal", label: "信号日", widthPct: 9 },
  { id: "entry", label: "买入日", widthPct: 9 },
  { id: "exit", label: "卖出日", widthPct: 9 },
  { id: "name", label: "股票", flex: true },
  {
    id: "score",
    label: "score",
    widthPct: 7,
    num: true,
    sortable: true,
    title: "悬停查看评分公式与分组因子权重",
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
  { id: "ret", label: "收益", widthPct: 7, num: true, sortable: true },
  { id: "status", label: "状态", widthPct: 8 },
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
        score_weight_source: l.score_weight_source,
        factor_weights: l.factor_weights,
        factor_weights_note: l.factor_weights_note,
        score_formula: l.score_formula,
        score_reasons: l.score_reasons,
        score_raw: l.score_raw,
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
    const name = nameByCode[code] || "";
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
    if (Array.isArray(dataOrTrades.trades_sample)) {
      return flattenTradesToSimLegs(dataOrTrades.trades_sample);
    }
    if (Array.isArray(dataOrTrades.signal_fill_sample)) {
      return dataOrTrades.signal_fill_sample;
    }
  }
  return [];
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
  const fullName = nameByCode[code] || code;
  const ret = r.return_pct;
  const st = r.status || "filled";
  const reasons = Array.isArray(r.score_reasons) ? r.score_reasons.slice(0, 8) : [];
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
  const scoreDetail = JSON.stringify({
    formula: r.score_formula || "",
    reasons,
    hard_reject: false,
    reject_reason: "",
    weight_source: r.score_weight_source || r.weight_source || "",
    cluster_label: r.cluster_label || "",
    cluster_mode: r.cluster_mode || "",
    cluster_version: r.cluster_version,
    score_global: r.score_global,
    score_cluster: r.score_cluster,
    factor_weights: r.factor_weights || {},
    factor_coefficients: r.factor_coefficients || {},
    return_model_source: r.return_model_source || "",
    formula_terms: r.score_formula_terms || null,
    predicted_score:
      r.predicted_score != null ? r.predicted_score : r.score != null ? r.score : null,
    score: r.score,
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
    scoreNum: Number.isFinite(Number(r.score)) ? Number(r.score) : null,
    scoreText: fmtScore(r.score),
    scoreCls: scoreCls(r.score),
    scoreDetail,
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
    `）· 按买入日新→旧 · 信号日→次日买入` +
    (showIntent ? "" : " · 意图价=买入价已省略") +
    `<button type="button" id="quant-bt-trades-csv" class="dialog-btn secondary quant-bt-trades-csv">下载 CSV</button>` +
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
};

/** Virtual-table compare callback for sim trades. */
export function btTradesNumCompare(id, a, b) {
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

/**
 * Virtual-table cellHtml for sim trades.
 * @param {{ escapeHtml: (s: string) => string, watchingNameSpanHtml: (name: string) => string }} deps
 */
export function btTradesCellHtml(col, d, deps) {
  const { escapeHtml, watchingNameSpanHtml } = deps;
  if (col.id === "name") {
    return (
      `<div class="watching-stock" title="${escapeHtml(d.name + " " + d.stock_code)}">` +
      watchingNameSpanHtml(d.name) +
      `<span class="watching-code-sub">${escapeHtml(d.stock_code)}</span></div>`
    );
  }
  if (col.id === "ret") {
    return `<span class="bt-trade-ret ${d.retCls || ""}">${escapeHtml(d.retText)}</span>`;
  }
  if (col.id === "score") {
    return (
      `<span class="bt-trade-score paper-hold-score has-tip ${escapeHtml(
        d.scoreCls || ""
      )}" ` +
      `data-score-detail="${escapeHtml(d.scoreDetail)}" ` +
      `title="悬停查看收益分与因子系数">${escapeHtml(d.scoreText)}</span>`
    );
  }
  if (col.id === "intent") return escapeHtml(d.intentText);
  if (col.id === "buy") return escapeHtml(d.buyText);
  if (col.id === "sell") return escapeHtml(d.sellText);
  return escapeHtml(d[col.id] ?? "—");
}
