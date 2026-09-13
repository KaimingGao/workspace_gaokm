/**
 * 观察池行情列：涨跌/市场格式化与 grid patch（纯数据）。
 */

export function parseWatchingVolume(raw) {
  if (raw == null || raw === "") return NaN;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : NaN;
  let s = String(raw).trim().replace(/,/g, "").replace(/\s/g, "");
  if (!s || s === "—" || s === "…") return NaN;
  s = s.replace(/手$/u, "");
  let mult = 1;
  if (/亿$/u.test(s)) {
    mult = 1e8;
    s = s.replace(/亿$/u, "");
  } else if (/万$/u.test(s)) {
    mult = 1e4;
    s = s.replace(/万$/u, "");
  }
  const n = Number(s);
  return Number.isFinite(n) ? n * mult : NaN;
}

export function formatWatchingChg(changePercent) {
  const chgNum =
    changePercent != null && !Number.isNaN(Number(changePercent))
      ? Number(changePercent)
      : null;
  if (chgNum == null) return { chgNum: null, chgTxt: "—", chgCls: "" };
  return {
    chgNum,
    chgTxt: `${chgNum >= 0 ? "+" : ""}${chgNum.toFixed(2)}%`,
    chgCls: chgNum === 0 ? "" : chgNum > 0 ? "is-up" : "is-down",
  };
}

/** 从「1780.00元」一类展示串取出数值。 */
export function parseWatchingPx(raw) {
  if (raw == null || raw === "") return NaN;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : NaN;
  const m = String(raw).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  return m ? Number(m[0]) : NaN;
}

/** 昨收：显式字段或由现价+涨跌反推。 */
export function resolvePrevClose(q) {
  if (!q || typeof q !== "object") return null;
  for (const k of ["prev_close", "last_close", "yc", "pre_close", "yesterday_close"]) {
    const v = parseWatchingPx(q[k]);
    if (Number.isFinite(v) && v > 0) return v;
  }
  const px = parseWatchingPx(
    q.price_raw != null && q.price_raw !== "" ? q.price_raw : q.price
  );
  const chg = Number(
    q.change_percent != null && q.change_percent !== ""
      ? q.change_percent
      : q.change_pct != null && q.change_pct !== ""
        ? q.change_pct
        : q.chgNum
  );
  if (Number.isFinite(px) && px > 0 && Number.isFinite(chg)) {
    const prev = px / (1 + chg / 100);
    return prev > 0 ? prev : null;
  }
  return null;
}

/** 表列展示昨收（固定两位小数）。 */
export function formatPrevCloseDisplay(q, { unit, currency } = {}) {
  const n = resolvePrevClose(q);
  if (n == null || !Number.isFinite(n)) return "—";
  const shown = n.toFixed(2);
  const u = unit || q?.unit || "元";
  const c = currency || q?.currency || "CNY";
  return c === "CNY" || !c ? `${shown}${u}` : `${u}${shown}`;
}

/** 今开：open / open_raw。 */
export function resolveOpenPx(q) {
  if (!q || typeof q !== "object") return null;
  for (const k of ["open_raw", "open"]) {
    const v = parseWatchingPx(q[k]);
    if (Number.isFinite(v) && v > 0) return v;
  }
  return null;
}

/** 表列展示今开（固定两位小数）。 */
export function formatOpenDisplay(q, { unit, currency } = {}) {
  const n = resolveOpenPx(q);
  if (n == null || !Number.isFinite(n)) return "—";
  const shown = n.toFixed(2);
  const u = unit || q?.unit || "元";
  const c = currency || q?.currency || "CNY";
  return c === "CNY" || !c ? `${shown}${u}` : `${u}${shown}`;
}

/** 开盘相对昨收缺口 %。昨收由 resolvePrevClose 解析。 */
export function gapPctFromQuote(q) {
  if (!q || typeof q !== "object") return null;
  const open = parseWatchingPx(q.open);
  const prev = resolvePrevClose(q);
  if (!Number.isFinite(open) || !(open > 0) || prev == null || !(prev > 0)) {
    return null;
  }
  return (open / prev - 1) * 100;
}

/** 洞察行缺 gap 时用行情补上，并清 vs，逼 resolveTradeScore 按昨收重算。 */
export function withQuoteGap(it, quoteLike) {
  if (!it || typeof it !== "object") return it;
  const had = it.gap_pct;
  if (had != null && had !== "" && Number.isFinite(Number(had))) return it;
  const ft = it.features_tau;
  if (ft && typeof ft === "object" && ft.gap_pct != null && Number.isFinite(Number(ft.gap_pct))) {
    return it;
  }
  const g = gapPctFromQuote(quoteLike);
  if (g == null) return it;
  return { ...it, gap_pct: g, predicted_score_blend_vs: "", predicted_score_blend_cal_vs: "" };
}

export const TRADE_TITLE = "ranking · w·ŷ_oo + w·(ŷ_oc∘w_co·ŷ_co)";

export function formatWatchingMarketLabel(market) {
  const m = String(market || "").toUpperCase();
  if (m === "CN") return "A股";
  if (m === "HK") return "港股";
  if (m === "US") return "美股";
  return m || "";
}

/**
 * @param {object} it quote item
 * @param {object|null} row grid row / getData()
 * @param {(raw: unknown) => number} parseWatchingVolume
 */
export function buildWatchingQuoteGridPatch(it, row, parseWatchingVolume) {
  const prev = row && typeof row.getData === "function" ? row.getData() : row || {};
  const ok = !!(it && it.ok);
  const { chgNum, chgTxt, chgCls } = formatWatchingChg(ok ? it.change_percent : null);
  const volNum = ok && typeof parseWatchingVolume === "function" ? parseWatchingVolume(it.volume) : NaN;
  const prevClose = ok
    ? formatPrevCloseDisplay(it, {
        unit: it.unit,
        currency: it.currency,
      })
    : "—";
  const openPx = ok ? resolveOpenPx(it) : null;
  const openDisplay = ok
    ? formatOpenDisplay(it, {
        unit: it.unit,
        currency: it.currency,
      })
    : "—";
  return {
    price: ok && it.price != null ? String(it.price) : "—",
    prev_close: prevClose,
    open: openDisplay,
    openNum: openPx != null && Number.isFinite(openPx) ? openPx : null,
    chg: chgTxt,
    chgCls,
    chgNum,
    vol: ok && it.volume != null ? String(it.volume) : "—",
    volNum: Number.isFinite(volNum) ? volNum : null,
    market: formatWatchingMarketLabel(it.market),
    name: ok && it.stock_name ? String(it.stock_name) : prev.name,
  };
}

export function buildWatchingQuotesStatusText(okN, total) {
  return `行情已更新 · ${okN}/${total}`;
}

export function buildWatchingQuotesErrorStatus(err) {
  if (err && err.name === "AbortError") return "行情拉取超时";
  return String((err && err.message) || err);
}
