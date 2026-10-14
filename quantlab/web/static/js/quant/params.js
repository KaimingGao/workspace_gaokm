/**
 * 研究台参数：持有期 / Ridge / 聚类 K / 观察池 Limit / Holdout / 分头训练窗。
 */

const DEFAULT_HOLDOUT_TRADING_DAYS = 20;
const HOLDOUT_DAYS_STORAGE_KEY = "quant_holdout_trading_days";

/** 各模型卡独立 Holdout / 训练窗。 */
const FIT_PARAM_HEADS = [
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
  "tc_rank",
];

/** 支持「只训 A 档」的拟合头（ŷ_oo_rank / ŷ_τc_rank）。 */
const TIER_A_FIT_HEADS = ["oo_rank", "tc_rank"];

function _defaultLookbackForHead(head) {
  if (head === "oo_rank" || head === "tc_rank") return 700;
  return head === "oo" || head === "co" || head === "oo_tree" || head === "co_tree"
    ? 600
    : 120;
}

/** 拟合用日线训练窗（交易日）；与回测「窗口」独立。 */
const DEFAULT_FIT_LOOKBACK = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, _defaultLookbackForHead(h)])
);
const FIT_LOOKBACK_STORAGE_KEYS = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, `quant_fit_lookback_${h}`])
);
const HOLDOUT_DAYS_STORAGE_KEYS = Object.fromEntries(
  FIT_PARAM_HEADS.map((h) => [h, `quant_holdout_trading_days_${h}`])
);
const FIT_LOOKBACK_MIN = 100;
const FIT_LOOKBACK_MAX = 1000;

/** 支持日截面 Z 勾选的拟合头。 */
const CS_Z_FIT_HEADS = [
  "oo",
  "tc",
  "co",
  "t30",
  "t45",
  "t60",
  "t75",
  "t90",
  "oo_rank",
  "tc_rank",
  "oo_tree",
  "tc_tree",
  "co_tree",
];
const CS_ZSCORE_STORAGE_KEYS = Object.fromEntries(
  CS_Z_FIT_HEADS.map((h) => [h, `quant_fit_cs_z_${h}`])
);

/** ŷ_τc / ŷ_τc_tree 共用：训练前缀终点 09:30…11:00（含 0 前缀）。 */
const TAU_END_FIT_HEADS = ["tc", "tc_tree"];
const TAU_END_STORAGE_KEY = "quant_fit_tau_end_hm";
const DEFAULT_TAU_END_HM = "11:00";
const TAU_END_CLOCKS = (() => {
  const out = [];
  let total = 9 * 60 + 30;
  const end = 11 * 60;
  while (total <= end) {
    const h = Math.floor(total / 60);
    const m = total % 60;
    out.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
    total += 5;
  }
  return out;
})();

/** oo/tc/co_tree：训练日滑窗 + 步长（LightGBM init_model 增量）。 */
const TREE_SLIDE_HEADS = ["oo_tree", "tc_tree", "co_tree"];
const DEFAULT_TREE_WINDOW_DAYS = 60;
const DEFAULT_TREE_STEP_DAYS = 20;
const TREE_WINDOW_STORAGE_KEYS = Object.fromEntries(
  TREE_SLIDE_HEADS.map((h) => [h, `quant_fit_window_${h}`])
);
const TREE_STEP_STORAGE_KEYS = Object.fromEntries(
  TREE_SLIDE_HEADS.map((h) => [h, `quant_fit_step_${h}`])
);

function clampHoldoutTradingDays(v, fallback = DEFAULT_HOLDOUT_TRADING_DAYS) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(60, Math.round(n)));
}

function clampFitLookbackDays(v, fallback = 120) {
  const n = Number(v);
  const fb = Number(fallback);
  const base = Number.isFinite(fb) ? fb : 120;
  if (!Number.isFinite(n)) {
    return Math.max(FIT_LOOKBACK_MIN, Math.min(FIT_LOOKBACK_MAX, Math.round(base)));
  }
  return Math.max(FIT_LOOKBACK_MIN, Math.min(FIT_LOOKBACK_MAX, Math.round(n)));
}

function clampTreeWindowDays(v, fallback = DEFAULT_TREE_WINDOW_DAYS) {
  const n = Number(v);
  const fb = Number(fallback);
  const base = Number.isFinite(fb) ? fb : DEFAULT_TREE_WINDOW_DAYS;
  if (!Number.isFinite(n)) return Math.max(0, Math.min(500, Math.round(base)));
  // 0 = 关闭滑窗（全样本一次训）
  return Math.max(0, Math.min(500, Math.round(n)));
}

function clampTreeStepDays(v, fallback = DEFAULT_TREE_STEP_DAYS) {
  const n = Number(v);
  const fb = Number(fallback);
  const base = Number.isFinite(fb) ? fb : DEFAULT_TREE_STEP_DAYS;
  if (!Number.isFinite(n)) return Math.max(1, Math.min(200, Math.round(base)));
  return Math.max(1, Math.min(200, Math.round(n)));
}

export function clampHorizonDays(v, fallback = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(10, Math.round(n)));
}

function clampRidgeLambda(v, fallback = 0) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.min(100, n);
}

export const BARS_WATCHING_LIMIT = 1000;
/** 观察池日 K 写入窗（交易日）；与 core.data.policy.BARS_DAILY_LOOKBACK 对齐。 */
export const BARS_DAILY_LOOKBACK = 1000;
/** ŷ_* 模型拟合截断硬顶（与 WATCHING_MAX_SIZE 对齐）。 */
export const MODEL_FIT_LIMIT = 1000;
export const WATCHING_POOL_DEFAULT = 300;
/** 滑块 hydrate / 保存后广播，日线·5m Coverage 据此重拉。 */
export const WATCHING_POOL_LIMIT_CHANGED = "quant-watching-pool-limit-changed";

function clampWatchingPoolLimit(v, fallback = WATCHING_POOL_DEFAULT) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(200, Math.min(MODEL_FIT_LIMIT, Math.round(n)));
}

let _poolLimitReadyResolve = null;
let _poolLimitReadySettled = false;
/** 首次从 watching.json hydrate 滑块后再 resolve；日线/5m 首刷须 await。 */
export const watchingPoolLimitReady = new Promise((resolve) => {
  _poolLimitReadyResolve = resolve;
});

/** 标记观察池只数已就绪（幂等）；供日线/5m 首刷等待。 */
export function markWatchingPoolLimitReady(limit) {
  if (_poolLimitReadySettled) return;
  _poolLimitReadySettled = true;
  const n = clampWatchingPoolLimit(limit, WATCHING_POOL_DEFAULT);
  const resolve = _poolLimitReadyResolve;
  _poolLimitReadyResolve = null;
  if (typeof resolve === "function") resolve(n);
}

/** hydrate 完成后或用户改滑块保存后：就绪 + 通知 Coverage 重拉。 */
export function notifyWatchingPoolLimitChanged(limit) {
  const n = clampWatchingPoolLimit(limit, WATCHING_POOL_DEFAULT);
  markWatchingPoolLimitReady(n);
  if (typeof document === "undefined" || typeof document.dispatchEvent !== "function") {
    return n;
  }
  try {
    document.dispatchEvent(
      new CustomEvent(WATCHING_POOL_LIMIT_CHANGED, { detail: { limit: n } })
    );
  } catch (_) {
    /* ignore */
  }
  return n;
}

/**
 * 等待观察池只数 hydrate；超时则用当前滑块/默认值放行，避免 Coverage 首刷挂死。
 * @param {number} [timeoutMs]
 * @returns {Promise<number>}
 */
export function awaitWatchingPoolLimitReady(timeoutMs = 8000) {
  const ms = Math.max(0, Number(timeoutMs) || 0);
  if (!ms) return watchingPoolLimitReady;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (n) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(n);
    };
    const timer = setTimeout(() => {
      const n = readWatchingPoolLimit();
      markWatchingPoolLimitReady(n);
      finish(n);
    }, ms);
    watchingPoolLimitReady.then((n) => finish(n), () => finish(readWatchingPoolLimit()));
  });
}

function clampWatchingLimit(v, fallback = BARS_WATCHING_LIMIT) {
  return clampWatchingPoolLimit(v, fallback);
}

function clampModelFitLimit(v, fallback = MODEL_FIT_LIMIT) {
  return clampWatchingPoolLimit(v, fallback);
}

export function clampBarsWatchingLimit(v, fallback = BARS_WATCHING_LIMIT) {
  return clampWatchingPoolLimit(v, fallback);
}

/** 观察池只数：研究枢纽配置，拟合 / 日K / 5m / 分档共用。 */
export function readWatchingPoolLimit() {
  const el =
    (typeof document !== "undefined" &&
      (document.getElementById("quant-watching-max-size") ||
        document.getElementById("quant-watching-limit"))) ||
    null;
  if (el && el.value !== "") return clampWatchingPoolLimit(el.value);
  return WATCHING_POOL_DEFAULT;
}

/** @returns {number|null} 空=自动 */
function readClusterKFromEl(el) {
  if (!el || el.value === "" || el.value == null) return null;
  const n = Number(el.value);
  if (!Number.isFinite(n)) return null;
  return Math.max(2, Math.min(100, Math.round(n)));
}

/**
 * @param {{ getHorizonEl?: () => HTMLElement|null, getRidgeEl?: () => HTMLElement|null, getClusterKEl?: () => HTMLElement|null, getWatchingLimitEl?: () => HTMLElement|null, getHoldoutEl?: (head: string) => HTMLElement|null, getFitLookbackEl?: (head: string) => HTMLElement|null, initialHorizon?: number }} opts
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
    (() =>
      document.getElementById("quant-watching-max-size") ||
      document.getElementById("quant-watching-limit"));
  const getHoldoutEl =
    opts.getHoldoutEl ||
    ((head) => {
      const h = String(head || "oo").trim().toLowerCase() || "oo";
      if (h === "oo") return document.getElementById("quant-holdout-days");
      return document.getElementById(`quant-holdout-days-${h}`);
    });
  const getFitLookbackEl =
    opts.getFitLookbackEl ||
    ((head) => document.getElementById(`quant-fit-lookback-${head}`));
  const getFitCsZEl =
    opts.getFitCsZEl ||
    ((head) => document.getElementById(`quant-fit-cs-z-${head}`));
  const getFitWindowEl =
    opts.getFitWindowEl ||
    ((head) => document.getElementById(`quant-fit-window-${head}`));
  const getFitStepEl =
    opts.getFitStepEl ||
    ((head) => document.getElementById(`quant-fit-step-${head}`));

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
    if (el && el.value !== "") return clampWatchingPoolLimit(el.value);
    return readWatchingPoolLimit();
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

  function _readStoredNum(el, key, clampFn, fallback) {
    let n;
    if (el && el.value !== "") {
      n = clampFn(el.value, fallback);
    } else {
      try {
        n = clampFn(localStorage.getItem(key), fallback);
      } catch (_) {
        n = clampFn(fallback, fallback);
      }
    }
    if (el && String(el.value) !== String(n)) el.value = String(n);
    try {
      localStorage.setItem(key, String(n));
    } catch (_) {}
    return n;
  }

  function readCrossSectionZscore(head) {
    const h = String(head || "").trim().toLowerCase();
    if (!CS_Z_FIT_HEADS.includes(h)) return true;
    const el = getFitCsZEl(h);
    const key = CS_ZSCORE_STORAGE_KEYS[h];
    let on = true;
    if (el) {
      on = !!el.checked;
    } else {
      try {
        const saved = localStorage.getItem(key);
        if (saved === "0") on = false;
        else if (saved === "1") on = true;
      } catch (_) {}
    }
    try {
      localStorage.setItem(key, on ? "1" : "0");
    } catch (_) {}
    if (el) el.checked = on;
    return on;
  }

  /** 拟合请求体片段：日截面 Z。 */
  function readCrossSectionFitParams(head) {
    return {
      cross_section_zscore: readCrossSectionZscore(head),
    };
  }

  /** 卡头摘要：日截面 / 全局 Z（Ridge return_model · Tree tree_return_model）。 */
  function formatCrossSectionZBit(data) {
    const src = data && typeof data === "object" ? data : {};
    const meta = src.meta && typeof src.meta === "object" ? src.meta : {};
    let cs;
    if ("cross_section_zscore" in src) cs = src.cross_section_zscore !== false;
    else if ("cross_section_zscore" in meta) cs = meta.cross_section_zscore !== false;
    else {
      const rm =
        (src.tree_return_model &&
          typeof src.tree_return_model === "object" &&
          src.tree_return_model) ||
        (src.return_model && typeof src.return_model === "object" && src.return_model) ||
        (src.model && typeof src.model === "object" && src.model) ||
        {};
      const hp = rm.hyperparams && typeof rm.hyperparams === "object" ? rm.hyperparams : {};
      if ("cross_section_zscore" in hp) cs = hp.cross_section_zscore !== false;
      else {
        const scope = String(rm.zscore_scope || hp.zscore_scope || "");
        if (scope === "cross_section") cs = true;
        else if (
          rm.zscore_means &&
          typeof rm.zscore_means === "object" &&
          Object.keys(rm.zscore_means).length > 0
        ) {
          cs = false;
        } else if (
          hp.zscore_means &&
          typeof hp.zscore_means === "object" &&
          Object.keys(hp.zscore_means).length > 0
        ) {
          cs = false;
        }
      }
    }
    if (cs === undefined) return "";
    return cs ? "日截面Z" : "全局Z";
  }

  function hydrateCrossSectionZParams() {
    for (const head of CS_Z_FIT_HEADS) {
      const csEl = getFitCsZEl(head);
      if (!csEl) continue;
      try {
        const saved = localStorage.getItem(CS_ZSCORE_STORAGE_KEYS[head]);
        if (saved === "0") csEl.checked = false;
        else if (saved === "1") csEl.checked = true;
      } catch (_) {}
      csEl.addEventListener("change", () => readCrossSectionZscore(head));
    }
  }

  function tauEndIndex(hm) {
    const s = String(hm || "").trim().slice(0, 5);
    const i = TAU_END_CLOCKS.indexOf(s);
    return i >= 0 ? i : TAU_END_CLOCKS.indexOf(DEFAULT_TAU_END_HM);
  }

  function clampTauEndHm(raw, fallback = DEFAULT_TAU_END_HM) {
    const s = String(raw || "")
      .trim()
      .replace("：", ":")
      .slice(0, 5);
    if (TAU_END_CLOCKS.includes(s)) return s;
    const fb = String(fallback || DEFAULT_TAU_END_HM).slice(0, 5);
    return TAU_END_CLOCKS.includes(fb) ? fb : DEFAULT_TAU_END_HM;
  }

  function _paintTauEndSlider(el, hm) {
    if (!el) return;
    const clock = clampTauEndHm(hm);
    const idx = tauEndIndex(clock);
    el.value = String(idx);
    el.setAttribute("aria-valuenow", String(idx));
    el.setAttribute("aria-valuetext", clock);
    const lab = el.closest(".quant-tau-end-slider");
    if (lab) lab.classList.toggle("is-zero-prefix", clock === "09:30");
    const outId = el.id ? `${el.id}-val` : "";
    const out = outId ? document.getElementById(outId) : null;
    if (out) {
      out.value = clock;
      out.textContent = clock;
    }
  }

  function readTauEndHm() {
    const el =
      document.getElementById("quant-fit-tau-end-tc") ||
      document.getElementById("quant-fit-tau-end-tc_tree");
    let hm = DEFAULT_TAU_END_HM;
    if (el) {
      const idx = Math.max(
        0,
        Math.min(TAU_END_CLOCKS.length - 1, Math.round(Number(el.value) || 0))
      );
      hm = TAU_END_CLOCKS[idx] || DEFAULT_TAU_END_HM;
    } else {
      try {
        const saved = localStorage.getItem(TAU_END_STORAGE_KEY);
        if (saved) hm = clampTauEndHm(saved);
      } catch (_) {}
    }
    hm = clampTauEndHm(hm);
    try {
      localStorage.setItem(TAU_END_STORAGE_KEY, hm);
    } catch (_) {}
    for (const head of TAU_END_FIT_HEADS) {
      _paintTauEndSlider(document.getElementById(`quant-fit-tau-end-${head}`), hm);
    }
    return hm;
  }

  /** 拟合请求体片段：ŷ_τc 前缀终点。 */
  function readTauEndFitParams() {
    return { tau_end_hm: readTauEndHm() };
  }

  /** 卡头摘要：前缀11:00 / 0前缀·OC。 */
  function formatTauEndBit(data) {
    const src = data && typeof data === "object" ? data : {};
    const ySpec = src.y_spec && typeof src.y_spec === "object" ? src.y_spec : {};
    let hm =
      src.tau_end_hm ||
      ySpec.tau_end_hm ||
      (Array.isArray(src.tau_grid) && src.tau_grid.length
        ? src.tau_grid[src.tau_grid.length - 1]
        : "");
    if (!hm && (src.zero_prefix || ySpec.zero_prefix)) hm = "09:30";
    if (!hm && (src.tau === "open" || ySpec.tau === "open")) hm = "09:30";
    hm = String(hm || "").trim().slice(0, 5);
    if (!hm || !TAU_END_CLOCKS.includes(hm)) return "";
    if (hm === "09:30") return "0前缀·OC";
    return `前缀${hm}`;
  }

  function applyTauEndHm(hm) {
    const clock = clampTauEndHm(hm);
    try {
      localStorage.setItem(TAU_END_STORAGE_KEY, clock);
    } catch (_) {}
    for (const head of TAU_END_FIT_HEADS) {
      _paintTauEndSlider(document.getElementById(`quant-fit-tau-end-${head}`), clock);
    }
    return clock;
  }

  function hydrateTauEndParams() {
    let hm = DEFAULT_TAU_END_HM;
    try {
      const saved = localStorage.getItem(TAU_END_STORAGE_KEY);
      if (saved) hm = clampTauEndHm(saved);
    } catch (_) {}
    for (const head of TAU_END_FIT_HEADS) {
      const el = document.getElementById(`quant-fit-tau-end-${head}`);
      if (!el) continue;
      _paintTauEndSlider(el, hm);
      if (el.dataset.wired === "1") continue;
      el.dataset.wired = "1";
      const sync = () => {
        const idx = Math.max(
          0,
          Math.min(TAU_END_CLOCKS.length - 1, Math.round(Number(el.value) || 0))
        );
        applyTauEndHm(TAU_END_CLOCKS[idx] || DEFAULT_TAU_END_HM);
      };
      el.addEventListener("input", sync);
      el.addEventListener("change", sync);
    }
  }

  function readTreeWindowDays(head) {
    const h = String(head || "").trim().toLowerCase();
    if (!TREE_SLIDE_HEADS.includes(h)) return DEFAULT_TREE_WINDOW_DAYS;
    return _readStoredNum(
      getFitWindowEl(h),
      TREE_WINDOW_STORAGE_KEYS[h],
      clampTreeWindowDays,
      DEFAULT_TREE_WINDOW_DAYS
    );
  }

  function readTreeStepDays(head) {
    const h = String(head || "").trim().toLowerCase();
    if (!TREE_SLIDE_HEADS.includes(h)) return DEFAULT_TREE_STEP_DAYS;
    return _readStoredNum(
      getFitStepEl(h),
      TREE_STEP_STORAGE_KEYS[h],
      clampTreeStepDays,
      DEFAULT_TREE_STEP_DAYS
    );
  }

  function readTreeSlideParams(head) {
    return {
      window_days: readTreeWindowDays(head),
      step_days: readTreeStepDays(head),
    };
  }

  /** 卡头摘要：滑窗60/20 或空（全样本）。 */
  function formatTreeSlideBit(data) {
    const src = data && typeof data === "object" ? data : {};
    const hp =
      (src.hyperparams && typeof src.hyperparams === "object" && src.hyperparams) ||
      (src.tree_return_model &&
        src.tree_return_model.hyperparams &&
        typeof src.tree_return_model.hyperparams === "object" &&
        src.tree_return_model.hyperparams) ||
      {};
    const w = Number(hp.window_days != null ? hp.window_days : src.window_days);
    const s = Number(hp.step_days != null ? hp.step_days : src.step_days);
    if (!Number.isFinite(w) || w <= 0) return "";
    const step = Number.isFinite(s) && s > 0 ? s : DEFAULT_TREE_STEP_DAYS;
    const nWin = Number(hp.n_windows);
    const trees = Number(hp.total_trees);
    let bit = `滑窗${Math.round(w)}/${Math.round(step)}`;
    if (Number.isFinite(nWin) && nWin > 0) bit += `·${Math.round(nWin)}窗`;
    if (Number.isFinite(trees) && trees > 0) bit += `·${Math.round(trees)}树`;
    return bit;
  }

  function hydrateTreeSlideParams() {
    for (const head of TREE_SLIDE_HEADS) {
      const wEl = getFitWindowEl(head);
      const sEl = getFitStepEl(head);
      if (wEl) {
        try {
          const saved = localStorage.getItem(TREE_WINDOW_STORAGE_KEYS[head]);
          if (saved != null && saved !== "") {
            wEl.value = String(clampTreeWindowDays(saved, DEFAULT_TREE_WINDOW_DAYS));
          } else if (!wEl.value) {
            wEl.value = String(DEFAULT_TREE_WINDOW_DAYS);
          }
        } catch (_) {
          if (!wEl.value) wEl.value = String(DEFAULT_TREE_WINDOW_DAYS);
        }
        wEl.addEventListener("change", () => readTreeWindowDays(head));
      }
      if (sEl) {
        try {
          const saved = localStorage.getItem(TREE_STEP_STORAGE_KEYS[head]);
          if (saved != null && saved !== "") {
            sEl.value = String(clampTreeStepDays(saved, DEFAULT_TREE_STEP_DAYS));
          } else if (!sEl.value) {
            sEl.value = String(DEFAULT_TREE_STEP_DAYS);
          }
        } catch (_) {
          if (!sEl.value) sEl.value = String(DEFAULT_TREE_STEP_DAYS);
        }
        sEl.addEventListener("change", () => readTreeStepDays(head));
      }
    }
  }

  function tierACheckboxId(head) {
    const h = String(head || "").trim().toLowerCase();
    if (h === "oo_rank") return "quant-oo-rank-tier-a";
    if (h === "tc_rank") return "quant-tc-rank-tier-a";
    return `quant-tier-a-${h}`;
  }

  function tierAStorageKey(head) {
    const h = String(head || "").trim().toLowerCase();
    return `quant.${h}.watching_tier_a_only`;
  }

  function readWatchingTierAOnly(head) {
    const el = document.getElementById(tierACheckboxId(head));
    return !!(el && el.checked);
  }

  function hydrateWatchingTierAOnly() {
    for (const head of TIER_A_FIT_HEADS) {
      const el = document.getElementById(tierACheckboxId(head));
      if (!el) continue;
      try {
        el.checked = localStorage.getItem(tierAStorageKey(head)) === "1";
      } catch (_) {
        /* ignore */
      }
      if (el.dataset.wired === "1") continue;
      el.dataset.wired = "1";
      el.addEventListener("change", () => {
        try {
          localStorage.setItem(tierAStorageKey(head), el.checked ? "1" : "0");
        } catch (_) {
          /* ignore */
        }
      });
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
    readCrossSectionZscore,
    readCrossSectionFitParams,
    formatCrossSectionZBit,
    hydrateCrossSectionZParams,
    readTauEndHm,
    readTauEndFitParams,
    formatTauEndBit,
    applyTauEndHm,
    hydrateTauEndParams,
    readTreeWindowDays,
    readTreeStepDays,
    readTreeSlideParams,
    formatTreeSlideBit,
    hydrateTreeSlideParams,
    readWatchingTierAOnly,
    hydrateWatchingTierAOnly,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  };
}
