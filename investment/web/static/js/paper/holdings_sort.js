/** Holdings sort helpers extracted from paper.js (W0.1). */

import { resolveTradeScore, resolveCalTradeScore, resolveNowcastScore } from "./fmt.js?v=p1227";

export function loadHoldingsSort() {
  let key = "market_value";
  let dir = "desc";
  try {
    const saved = JSON.parse(localStorage.getItem("paper_holdings_sort") || "null");
    if (
      saved &&
      (saved.key === "code" ||
        saved.key === "market_value" ||
        saved.key === "score" ||
        saved.key === "score_cal" ||
        saved.key === "score_nowcast" ||
        saved.key === "residual" ||
        saved.key === "pnl" ||
        saved.key === "chg")
    ) {
      key = saved.key;
      dir = saved.dir === "asc" ? "asc" : "desc";
    }
  } catch (_) {
    /* ignore */
  }
  return { key, dir };
}

export function persistHoldingsSort(key, dir) {
  try {
    localStorage.setItem(
      "paper_holdings_sort",
      JSON.stringify({ key, dir: dir === "asc" ? "asc" : "desc" })
    );
  } catch (_) {
    /* ignore */
  }
}

export function sortHoldings(list, key, dir) {
  const arr = Array.isArray(list) ? list.slice() : [];
  const k = key || "market_value";
  const asc = dir === "asc";
  arr.sort((a, b) => {
    let av;
    let bv;
    if (k === "code") {
      av = String(a.stock_code || "");
      bv = String(b.stock_code || "");
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    }
    if (k === "score") {
      av = resolveTradeScore(a);
      bv = resolveTradeScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
    } else if (k === "score_cal") {
      av = resolveCalTradeScore(a);
      bv = resolveCalTradeScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
    } else if (k === "score_nowcast") {
      av = resolveNowcastScore(a);
      bv = resolveNowcastScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
    } else if (k === "residual") {
      const ac = resolveCalTradeScore(a);
      const bc = resolveCalTradeScore(b);
      const achg = Number(a.change_pct);
      const bchg = Number(b.change_pct);
      av =
        ac != null && Number.isFinite(Number(ac)) && Number.isFinite(achg)
          ? Number(ac) - achg
          : NaN;
      bv =
        bc != null && Number.isFinite(Number(bc)) && Number.isFinite(bchg)
          ? Number(bc) - bchg
          : NaN;
    } else if (k === "pnl") {
      av = Number(a.pnl_pct);
      bv = Number(b.pnl_pct);
    } else if (k === "chg") {
      av = Number(a.change_pct);
      bv = Number(b.change_pct);
    } else {
      av = Number(a.market_value ?? a.market_value_approx);
      bv = Number(b.market_value ?? b.market_value_approx);
    }
    if (!Number.isFinite(av)) av = asc ? Infinity : -Infinity;
    if (!Number.isFinite(bv)) bv = asc ? Infinity : -Infinity;
    return asc ? av - bv : bv - av;
  });
  return arr;
}

export function sortThHtml(label, key, activeKey, activeDir) {
  const on = activeKey === key;
  const arrow = on ? (activeDir === "asc" ? " ↑" : " ↓") : "";
  const next = on && activeDir === "desc" ? "asc" : "desc";
  return (
    `<th class="sortable${on ? " is-sorted" : ""}" data-sort="${key}" ` +
    `data-next="${next}" role="button" tabindex="0">${label}${arrow}</th>`
  );
}
