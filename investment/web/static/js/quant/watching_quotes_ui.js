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

/** 开盘相对昨收缺口 %。昨收由现价与涨跌反推。 */
export function gapPctFromQuote(q) {
  if (!q || typeof q !== "object") return null;
  const open = parseWatchingPx(q.open);
  const px = parseWatchingPx(q.price_raw != null && q.price_raw !== "" ? q.price_raw : q.price);
  const chg = Number(
    q.change_percent != null && q.change_percent !== ""
      ? q.change_percent
      : q.change_pct != null && q.change_pct !== ""
        ? q.change_pct
        : q.chgNum
  );
  if (!Number.isFinite(open) || !(open > 0) || !Number.isFinite(px) || !(px > 0) || !Number.isFinite(chg)) {
    return null;
  }
  const prev = px / (1 + chg / 100);
  if (!(prev > 0)) return null;
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

export const TRADE_TITLE =
  "ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ) · 现价对昨收（与涨跌同一口径）· 排序/卖门槛";

export const EOD_CAL_TITLE =
  "eod = g(ŷ_EOD) · 对涨跌的回归预估（现价对昨收）· 不含缺口 · 不进决策";

export const RESIDUAL_TITLE =
  "残差 = trade − 涨跌。trade 是 ŷ_trade（现价对昨收），与涨跌同一目标";

export const NOWCAST_TITLE =
  "ŷ_nowcast · Kalman 权昨收口径对照（与 ŷ_trade / 涨跌同一目标），不进决策";

/** 残差 = trade − 涨跌（百分点）。用 ŷ_trade，不用 g(ŷ_EOD)。 */
export function formatWatchingResidual(tradeCalOrTrade, chgNum) {
  if (
    tradeCalOrTrade == null ||
    !Number.isFinite(Number(tradeCalOrTrade)) ||
    chgNum == null ||
    !Number.isFinite(Number(chgNum))
  ) {
    return { residualNum: null, residual: "—", residualCls: "" };
  }
  const n = Number(tradeCalOrTrade) - Number(chgNum);
  return {
    residualNum: n,
    residual: `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`,
    residualCls: n === 0 ? "" : n > 0 ? "is-up" : "is-down",
  };
}

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
  const { residualNum, residual, residualCls } = formatWatchingResidual(
    prev.scoreNum,
    chgNum
  );
  const volNum = ok && typeof parseWatchingVolume === "function" ? parseWatchingVolume(it.volume) : NaN;
  return {
    price: ok && it.price != null ? String(it.price) : "—",
    open: ok && it.open != null && it.open !== "" ? String(it.open) : "—",
    chg: chgTxt,
    chgCls,
    chgNum,
    residual,
    residualCls,
    residualNum,
    residualTitle: RESIDUAL_TITLE,
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
