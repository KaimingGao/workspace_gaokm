/**
 * 研究台参数：持有期 / Ridge / 聚类 K / 观察池 Limit / Holdout。
 */

export const DEFAULT_HOLDOUT_TRADING_DAYS = 10;
export const HOLDOUT_DAYS_STORAGE_KEY = "quant_holdout_trading_days";

export function clampHoldoutTradingDays(v, fallback = DEFAULT_HOLDOUT_TRADING_DAYS) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(60, Math.round(n)));
}

export function clampHorizonDays(v, fallback = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(10, Math.round(n)));
}

export function clampRidgeLambda(v, fallback = 0) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.min(100, n);
}

/** 观察池截断上限：对齐 WATCHING_MAX_SIZE。分组 Limit / 日K/5m / ŷ_* 拟合共用。 */
export const BARS_WATCHING_LIMIT = 200;

export function clampWatchingLimit(v, fallback = BARS_WATCHING_LIMIT) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(3, Math.min(BARS_WATCHING_LIMIT, Math.round(n)));
}

export function clampBarsWatchingLimit(v, fallback = BARS_WATCHING_LIMIT) {
  return clampWatchingLimit(v, fallback);
}

/** @returns {number|null} 空=自动 */
export function readClusterKFromEl(el) {
  if (!el || el.value === "" || el.value == null) return null;
  const n = Number(el.value);
  if (!Number.isFinite(n)) return null;
  return Math.max(2, Math.min(100, Math.round(n)));
}

/**
 * @param {{ getHorizonEl?: () => HTMLElement|null, getRidgeEl?: () => HTMLElement|null, getClusterKEl?: () => HTMLElement|null, getWatchingLimitEl?: () => HTMLElement|null, getHoldoutEl?: () => HTMLElement|null, initialHorizon?: number }} opts
 */
export function createResearchParams(opts = {}) {
  let prefsHorizonDays = clampHorizonDays(opts.initialHorizon ?? 3, 3);
  const getHorizonEl =
    opts.getHorizonEl || (() => document.getElementById("quant-horizon"));
  const getRidgeEl =
    opts.getRidgeEl || (() => document.getElementById("quant-ridge-lambda"));
  const getClusterKEl =
    opts.getClusterKEl || (() => document.getElementById("quant-cluster-k"));
  const getWatchingLimitEl =
    opts.getWatchingLimitEl ||
    (() => document.getElementById("quant-watching-limit"));
  const getHoldoutEl =
    opts.getHoldoutEl || (() => document.getElementById("quant-holdout-days"));

  function syncHorizonInputs(h) {
    const v = String(clampHorizonDays(h, prefsHorizonDays));
    document.querySelectorAll("#quant-horizon").forEach((el) => {
      el.value = v;
    });
  }

  function readHorizonDays() {
    const el = getHorizonEl();
    if (el && el.value !== "") {
      const n = clampHorizonDays(el.value, prefsHorizonDays);
      prefsHorizonDays = n;
      return n;
    }
    return prefsHorizonDays;
  }

  function readRidgeLambda() {
    const el = getRidgeEl();
    if (el && el.value !== "") return clampRidgeLambda(el.value, 1.0);
    return 1.0;
  }

  function readClusterK() {
    return readClusterKFromEl(getClusterKEl());
  }

  function readWatchingLimit() {
    const el = getWatchingLimitEl();
    if (el && el.value !== "") return clampWatchingLimit(el.value, BARS_WATCHING_LIMIT);
    return BARS_WATCHING_LIMIT;
  }

  function setPrefsHorizonDays(h) {
    prefsHorizonDays = clampHorizonDays(h, prefsHorizonDays);
    return prefsHorizonDays;
  }

  function getPrefsHorizonDays() {
    return prefsHorizonDays;
  }

  function readHoldoutTradingDays() {
    const el = getHoldoutEl();
    let n;
    if (el && el.value !== "") {
      n = clampHoldoutTradingDays(el.value, DEFAULT_HOLDOUT_TRADING_DAYS);
    } else {
      try {
        n = clampHoldoutTradingDays(
          localStorage.getItem(HOLDOUT_DAYS_STORAGE_KEY),
          DEFAULT_HOLDOUT_TRADING_DAYS
        );
      } catch (_) {
        n = DEFAULT_HOLDOUT_TRADING_DAYS;
      }
    }
    if (el && String(el.value) !== String(n)) el.value = String(n);
    try {
      localStorage.setItem(HOLDOUT_DAYS_STORAGE_KEY, String(n));
    } catch (_) {}
    return n;
  }

  function hydrateHoldoutTradingDays() {
    const el = getHoldoutEl();
    if (!el) return;
    try {
      const saved = localStorage.getItem(HOLDOUT_DAYS_STORAGE_KEY);
      if (saved != null && saved !== "") {
        el.value = String(clampHoldoutTradingDays(saved, DEFAULT_HOLDOUT_TRADING_DAYS));
      }
    } catch (_) {}
    el.addEventListener("change", () => readHoldoutTradingDays());
  }

  return {
    clampHorizonDays,
    clampRidgeLambda,
    clampWatchingLimit,
    clampHoldoutTradingDays,
    syncHorizonInputs,
    readHorizonDays,
    readRidgeLambda,
    readClusterK,
    readWatchingLimit,
    readHoldoutTradingDays,
    hydrateHoldoutTradingDays,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  };
}
