/** Holdings sort helpers extracted from paper.js (W0.1). */

import { resolveRankingScore, resolveEodScore, resolveTauScore, resolveOnScore } from "./fmt.js?v=p2544";
import { getPredTier, predTierRank } from "../quant/pred_tier_ui.js?v=p2749";

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
        saved.key === "score_eod" ||
        saved.key === "score_tau" ||
        saved.key === "score_on" ||
        saved.key === "pnl" ||
        saved.key === "chg" ||
        saved.key === "tier")
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
    if (k === "tier") {
      const ar = predTierRank((getPredTier(a.stock_code) || {}).tier);
      const br = predTierRank((getPredTier(b.stock_code) || {}).tier);
      if (ar !== br) return asc ? ar - br : br - ar;
      const as = resolveRankingScore(a);
      const bs = resolveRankingScore(b);
      const aOk = as != null && Number.isFinite(Number(as));
      const bOk = bs != null && Number.isFinite(Number(bs));
      if (aOk && bOk && Number(as) !== Number(bs)) return Number(bs) - Number(as);
      if (aOk && !bOk) return -1;
      if (!aOk && bOk) return 1;
      return String(a.stock_code || "").localeCompare(String(b.stock_code || ""), "zh-CN", {
        numeric: true,
      });
    }
    if (k === "score") {
      av = resolveRankingScore(a);
      bv = resolveRankingScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
    } else if (k === "score_eod") {
      av = resolveEodScore(a);
      bv = resolveEodScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
    } else if (k === "score_tau") {
      av = resolveTauScore(a);
      bv = resolveTauScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
    } else if (k === "score_on") {
      av = resolveOnScore(a);
      bv = resolveOnScore(b);
      av = av == null ? NaN : av;
      bv = bv == null ? NaN : bv;
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
