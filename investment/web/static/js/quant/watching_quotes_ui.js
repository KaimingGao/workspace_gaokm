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
  return {
    price: ok && it.price != null ? String(it.price) : "—",
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
