/**
 * 研究台参数：持有期 / Ridge / 聚类 K / 观察池 Limit / Holdout / 分头训练窗 / 分头去均值。
 */

export const DEFAULT_HOLDOUT_TRADING_DAYS = 20;
export const HOLDOUT_DAYS_STORAGE_KEY = "quant_holdout_trading_days";

/** 各模型卡独立 Holdout / 训练窗 / 去均值。 */
export const FIT_PARAM_HEADS = [
  "oo",
  "tc",
  "co",
  "t30",
  "t45",
  "t60",
  "t75",
  "t90",
  "oo_tree",
  "tc_tree",
  "co_tree",
  "t30_tree",
  "t45_tree",
  "t60_tree",
  "t75_tree",
  "t90_tree",
  "oo_rank",
];

function _defaultLookbackForHead(head) {
  return head === "oo" || head === "co" || head === "oo_tree" || head === "co_tree"
    ? 600
    : 120;
}

/** 拟合用日线训练窗（交易日）；与回测「窗口」独立。 */
export const DEFAULT_FIT_LOOKBACK = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, _defaultLookbackForHead(h)])
);
export const FIT_LOOKBACK_STORAGE_KEYS = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, `quant_fit_lookback_${h}`])
);
export const HOLDOUT_DAYS_STORAGE_KEYS = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, `quant_holdout_trading_days_${h}`])
);
export const FIT_LOOKBACK_MIN = 40;
export const FIT_LOOKBACK_MAX = 700;

export const DEFAULT_LABEL_DEMEAN = false;
/** 旧页顶全局 key；hydrate 时迁移到分头 key。 */
export const LABEL_DEMEAN_STORAGE_KEY_LEGACY = "quant_label_demean";
export const LABEL_DEMEAN_STORAGE_KEYS = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, `quant_label_demean_${h}`])
);

export function clampHoldoutTradingDays(v, fallback = DEFAULT_HOLDOUT_TRADING_DAYS) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(60, Math.round(n)));
}

export function clampFitLookbackDays(v, fallback = 120) {
  const n = Number(v);
  const fb = Number(fallback);
  const base = Number.isFinite(fb) ? fb : 120;
  if (!Number.isFinite(n)) {
    return Math.max(FIT_LOOKBACK_MIN, Math.min(FIT_LOOKBACK_MAX, Math.round(base)));
  }
  return Math.max(FIT_LOOKBACK_MIN, Math.min(FIT_LOOKBACK_MAX, Math.round(n)));
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
export const BARS_WATCHING_LIMIT = 300;

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
 * @param {{ getHorizonEl?: () => HTMLElement|null, getRidgeEl?: () => HTMLElement|null, getClusterKEl?: () => HTMLElement|null, getWatchingLimitEl?: () => HTMLElement|null, getHoldoutEl?: (head: string) => HTMLElement|null, getFitLookbackEl?: (head: string) => HTMLElement|null, getLabelDemeanEl?: (head: string) => HTMLElement|null, initialHorizon?: number }} opts
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
    opts.getHoldoutEl ||
    ((head) => {
      const h = String(head || "oo").trim().toLowerCase() || "oo";
      if (h === "oo") return document.getElementById("quant-holdout-days");
      return document.getElementById(`quant-holdout-days-${h}`);
    });
  const getLabelDemeanEl =
    opts.getLabelDemeanEl ||
    ((head) => document.getElementById(`quant-label-demean-${head}`));
  const getFitLookbackEl =
    opts.getFitLookbackEl ||
    ((head) => document.getElementById(`quant-fit-lookback-${head}`));

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

  function _holdoutKey(head) {
    const h = String(head || "oo").trim().toLowerCase() || "oo";
    return HOLDOUT_DAYS_STORAGE_KEYS[h] || HOLDOUT_DAYS_STORAGE_KEYS.oo;
  }

  function readHoldoutTradingDays(head) {
    const h = String(head || "oo").trim().toLowerCase() || "oo";
    const el = getHoldoutEl(h);
    const key = _holdoutKey(h);
    let n;
    if (el && el.value !== "") {
      n = clampHoldoutTradingDays(el.value, DEFAULT_HOLDOUT_TRADING_DAYS);
    } else {
      try {
        const saved = localStorage.getItem(key);
        const legacy = localStorage.getItem(HOLDOUT_DAYS_STORAGE_KEY);
        n = clampHoldoutTradingDays(
          saved != null && saved !== "" ? saved : legacy,
          DEFAULT_HOLDOUT_TRADING_DAYS
        );
      } catch (_) {
        n = DEFAULT_HOLDOUT_TRADING_DAYS;
      }
    }
    if (el && String(el.value) !== String(n)) el.value = String(n);
    try {
      localStorage.setItem(key, String(n));
      if (h === "oo") localStorage.setItem(HOLDOUT_DAYS_STORAGE_KEY, String(n));
    } catch (_) {}
    return n;
  }

  function hydrateHoldoutTradingDays() {
    let legacy = null;
    try {
      const raw = localStorage.getItem(HOLDOUT_DAYS_STORAGE_KEY);
      if (raw != null && raw !== "") {
        legacy = clampHoldoutTradingDays(raw, DEFAULT_HOLDOUT_TRADING_DAYS);
      }
    } catch (_) {}
    for (const head of FIT_PARAM_HEADS) {
      const el = getHoldoutEl(head);
      if (!el) continue;
      const key = _holdoutKey(head);
      try {
        const saved = localStorage.getItem(key);
        if (saved != null && saved !== "") {
          el.value = String(clampHoldoutTradingDays(saved, DEFAULT_HOLDOUT_TRADING_DAYS));
        } else if (legacy != null) {
          el.value = String(legacy);
        } else if (!el.value) {
          el.value = String(DEFAULT_HOLDOUT_TRADING_DAYS);
        }
      } catch (_) {
        if (!el.value) el.value = String(DEFAULT_HOLDOUT_TRADING_DAYS);
      }
      el.addEventListener("change", () => readHoldoutTradingDays(head));
    }
  }

  function readFitLookbackDays(head) {
    const h = String(head || "").trim().toLowerCase();
    const key = FIT_LOOKBACK_STORAGE_KEYS[h];
    const fallback = DEFAULT_FIT_LOOKBACK[h] ?? 120;
    if (!key) return clampFitLookbackDays(fallback, fallback);
    const el = getFitLookbackEl(h);
    let n;
    if (el && el.value !== "") {
      n = clampFitLookbackDays(el.value, fallback);
    } else {
      try {
        n = clampFitLookbackDays(localStorage.getItem(key), fallback);
      } catch (_) {
        n = clampFitLookbackDays(fallback, fallback);
      }
    }
    if (el && String(el.value) !== String(n)) el.value = String(n);
    try {
      localStorage.setItem(key, String(n));
    } catch (_) {}
    return n;
  }

  function hydrateFitLookbackDays() {
    for (const head of FIT_PARAM_HEADS) {
      const el = getFitLookbackEl(head);
      if (!el) continue;
      const fallback = DEFAULT_FIT_LOOKBACK[head];
      const key = FIT_LOOKBACK_STORAGE_KEYS[head];
      try {
        const saved = localStorage.getItem(key);
        if (saved != null && saved !== "") {
          el.value = String(clampFitLookbackDays(saved, fallback));
        } else if (!el.value) {
          el.value = String(fallback);
        }
      } catch (_) {
        if (!el.value) el.value = String(fallback);
      }
      el.addEventListener("change", () => readFitLookbackDays(head));
    }
  }

  function _labelDemeanKey(head) {
    const h = String(head || "").trim().toLowerCase();
    return LABEL_DEMEAN_STORAGE_KEYS[h] || null;
  }

  function readLabelDemean(head) {
    const h = String(head || "oo").trim().toLowerCase();
    const key = _labelDemeanKey(h);
    const el = getLabelDemeanEl(h);
    let on;
    if (el) {
      on = !!el.checked;
    } else {
      try {
        const saved = key ? localStorage.getItem(key) : null;
        if (saved === "1" || saved === "0") {
          on = saved === "1";
        } else {
          on = localStorage.getItem(LABEL_DEMEAN_STORAGE_KEY_LEGACY) === "1";
        }
      } catch (_) {
        on = DEFAULT_LABEL_DEMEAN;
      }
    }
    if (key) {
      try {
        localStorage.setItem(key, on ? "1" : "0");
      } catch (_) {}
    }
    return on;
  }

  function hydrateLabelDemean() {
    let legacy = null;
    try {
      const raw = localStorage.getItem(LABEL_DEMEAN_STORAGE_KEY_LEGACY);
      if (raw === "1" || raw === "0") legacy = raw === "1";
    } catch (_) {}
    for (const head of FIT_PARAM_HEADS) {
      const el = getLabelDemeanEl(head);
      if (!el) continue;
      const key = _labelDemeanKey(head);
      try {
        const saved = key ? localStorage.getItem(key) : null;
        if (saved === "1" || saved === "0") {
          el.checked = saved === "1";
        } else if (legacy != null) {
          el.checked = legacy;
        } else {
          el.checked = DEFAULT_LABEL_DEMEAN;
        }
      } catch (_) {
        el.checked = DEFAULT_LABEL_DEMEAN;
      }
      el.addEventListener("change", () => readLabelDemean(head));
    }
  }

  return {
    clampHorizonDays,
    clampRidgeLambda,
    clampWatchingLimit,
    clampHoldoutTradingDays,
    clampFitLookbackDays,
    syncHorizonInputs,
    readHorizonDays,
    readRidgeLambda,
    readClusterK,
    readWatchingLimit,
    readHoldoutTradingDays,
    hydrateHoldoutTradingDays,
    readFitLookbackDays,
    hydrateFitLookbackDays,
    readLabelDemean,
    hydrateLabelDemean,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  };
}
