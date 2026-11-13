/**
 * 研究台参数：持有期 / Ridge / 聚类 K / 观察池 Limit / Holdout / 分头训练窗。
 */

const DEFAULT_HOLDOUT_TRADING_DAYS = 20;

/** 各模型卡独立 Holdout / 训练窗。 */
const FIT_PARAM_HEADS = [
  "oo",
  "tc",
  "t30",
  "t45",
  "t60",
  "t75",
  "t90",
  "oo_rank",
  "tc_rank",
];

function _defaultLookbackForHead(head) {
  if (head === "oo_rank" || head === "tc_rank") return 700;
  return head === "oo" ? 600 : 120;
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
/** 观察池日 K 写入窗（交易日）；与 core.data.policy.BARS_DAILY_LOOKBACK 对齐。 */
export const BARS_DAILY_LOOKBACK = 1060;
/** 拟合 / API lookback 上限；跟日 K 窗同一常量。 */
const FIT_LOOKBACK_MAX = BARS_DAILY_LOOKBACK;

/** ŷ_τc：09:30…11:00（含 0 前缀）；ŷ_τ*：09:35…11:00（方案 B，仅分钟）。 */
const TAU_END_TC_HEADS = ["tc", "tc_rank"];
const TAU_END_HORIZON_HEADS = ["t30", "t45", "t60", "t75", "t90"];
const TAU_END_FIT_HEADS = [...TAU_END_TC_HEADS, ...TAU_END_HORIZON_HEADS];
/** τc 默认 09:30；τ* 默认 11:00。拖动/拟合按组写入。 */
const TAU_END_STORAGE_KEY_TC = "quant_fit_tau_end_hm_tc_v1";
const TAU_END_STORAGE_KEY_HZ = "quant_fit_tau_end_hm_hz_v1";
const DEFAULT_TAU_END_HM_TC = "09:30";
const DEFAULT_TAU_END_HM_HORIZON = "11:00";
/** clamp / 做 T 回测等无组上下文时的回落（保持 11:00）。 */
const DEFAULT_TAU_END_HM = DEFAULT_TAU_END_HM_HORIZON;
/** ŷ_τc / 做 T 回测：含 09:30 开盘点。 */
export const TAU_END_CLOCKS = (() => {
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
/** ŷ_τ30…90：自 09:35 起，不读日线 open。 */
export const TAU_END_CLOCKS_HORIZON = TAU_END_CLOCKS.filter((c) => c !== "09:30");

function tauEndGroup(headOrGroup) {
  const h = String(headOrGroup || "")
    .trim()
    .toLowerCase();
  if (h === "tc" || h === "tau" || h === "τc" || h === "group_tc") return "tc";
  if (h === "horizon" || h === "group_horizon" || h === "hz") return "horizon";
  if (TAU_END_TC_HEADS.includes(h)) return "tc";
  return "horizon";
}

function tauEndHeads(group) {
  return group === "tc" ? TAU_END_TC_HEADS : TAU_END_HORIZON_HEADS;
}

function tauEndDefault(group) {
  return group === "tc" ? DEFAULT_TAU_END_HM_TC : DEFAULT_TAU_END_HM_HORIZON;
}

function tauEndStorageKey(group) {
  return group === "tc" ? TAU_END_STORAGE_KEY_TC : TAU_END_STORAGE_KEY_HZ;
}

function tauEndClocks(group) {
  return group === "tc" ? TAU_END_CLOCKS : TAU_END_CLOCKS_HORIZON;
}

function tauEndDefaultIdx(group) {
  const clocks = tauEndClocks(group);
  const hm = tauEndDefault(group);
  const i = clocks.indexOf(hm);
  return i >= 0 ? i : group === "tc" ? 0 : clocks.length - 1;
}

/** 规范前缀终点 HH:MM（须在 TAU_END_CLOCKS）；非法回落 fallback。做 T / τc 用。 */
export function clampTauEndHm(raw, fallback = DEFAULT_TAU_END_HM) {
  const s = String(raw || "")
    .trim()
    .replace("：", ":")
    .slice(0, 5);
  if (TAU_END_CLOCKS.includes(s)) return s;
  const fb = String(fallback || DEFAULT_TAU_END_HM).slice(0, 5);
  return TAU_END_CLOCKS.includes(fb) ? fb : DEFAULT_TAU_END_HM;
}

/** 窗头前缀：09:30/open 上抬 09:35。 */
function clampHorizonTauEndHm(raw, fallback = DEFAULT_TAU_END_HM_HORIZON) {
  let s = String(raw || "")
    .trim()
    .replace("：", ":")
    .slice(0, 5);
  if (s === "09:30" || s.toLowerCase() === "open") s = "09:35";
  if (TAU_END_CLOCKS_HORIZON.includes(s)) return s;
  const fb = String(fallback || DEFAULT_TAU_END_HM_HORIZON).slice(0, 5);
  const fb2 = fb === "09:30" ? "09:35" : fb;
  return TAU_END_CLOCKS_HORIZON.includes(fb2)
    ? fb2
    : DEFAULT_TAU_END_HM_HORIZON;
}

function clampTauEndHmForGroup(raw, group) {
  return group === "tc"
    ? clampTauEndHm(raw, DEFAULT_TAU_END_HM_TC)
    : clampHorizonTauEndHm(raw, DEFAULT_TAU_END_HM_HORIZON);
}

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

export const BARS_WATCHING_LIMIT = 1600;
/** ŷ_* 模型拟合截断硬顶（与 WATCHING_MAX_SIZE 对齐）。 */
export const MODEL_FIT_LIMIT = 1600;
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
        n =
          saved != null && saved !== ""
            ? clampHoldoutTradingDays(saved, DEFAULT_HOLDOUT_TRADING_DAYS)
            : DEFAULT_HOLDOUT_TRADING_DAYS;
      } catch (_) {
        n = DEFAULT_HOLDOUT_TRADING_DAYS;
      }
    }
    if (el && String(el.value) !== String(n)) el.value = String(n);
    try {
      localStorage.setItem(key, String(n));
    } catch (_) {}
    return n;
  }

  function hydrateHoldoutTradingDays() {
    try {
      localStorage.removeItem("quant_holdout_trading_days");
    } catch (_) {}
    for (const head of FIT_PARAM_HEADS) {
      const el = getHoldoutEl(head);
      if (!el) continue;
      const key = _holdoutKey(head);
      try {
        const saved = localStorage.getItem(key);
        if (saved != null && saved !== "") {
          el.value = String(clampHoldoutTradingDays(saved, DEFAULT_HOLDOUT_TRADING_DAYS));
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

  function readCrossSectionZscore(_head) {
    // 产品固定日截面 Z；训练窗全局 μ/σ 已下线
    return true;
  }

  /** 拟合请求体片段：日截面 Z（恒开；False 服务端拒）。 */
  function readCrossSectionFitParams(head) {
    return {
      cross_section_zscore: readCrossSectionZscore(head),
    };
  }

  /** 卡头：仅旧包仍带 train_universe / tiers 时提示；新训全池不标。 */
  function formatTrainTiersBit(data) {
    const src = data && typeof data === "object" ? data : {};
    const meta = src.meta && typeof src.meta === "object" ? src.meta : {};
    const rm =
      (src.return_model && typeof src.return_model === "object" && src.return_model) ||
      {};
    const allow = new Set(["A", "B", "C"]);
    const tiers = [];
    const seen = new Set();
    const push = (t) => {
      const u = String(t || "")
        .trim()
        .toUpperCase();
      if (allow.has(u) && !seen.has(u)) {
        seen.add(u);
        tiers.push(u);
      }
    };
    const raw =
      src.train_universe_tiers ||
      meta.train_universe_tiers ||
      rm.train_universe_tiers ||
      null;
    if (Array.isArray(raw)) raw.forEach(push);
    else if (typeof raw === "string") {
      String(raw)
        .split(/[,+|/\s]+/)
        .forEach(push);
    }
    if (!tiers.length) {
      const trainRaw =
        src.train_universe ||
        meta.train_universe ||
        rm.train_universe ||
        "";
      const uni = String(trainRaw || "").toLowerCase();
      if (uni === "tier_a" || uni === "a") tiers.push("A");
      else if (uni === "tier" || uni === "tiers") return "旧训练档";
    }
    if (tiers.length) return `旧训练${tiers.join("+")}`;
    return "";
  }

  /** 清已下线拟合控件的 localStorage（训练档 / 旧 z 宇宙勾选 / 旧共用 τ 钟）。 */
  function hydrateCrossSectionZParams() {
    for (const h of ["oo_rank", "tc_rank"]) {
      try {
        localStorage.removeItem(`quant_fit_train_tiers_${h}`);
        localStorage.removeItem(`quant_fit_z_univ_a_${h}`);
      } catch (_) {}
    }
    try {
      localStorage.removeItem("quant_fit_tau_end_hm_v3");
    } catch (_) {}
  }

  function tauEndIndex(hm, group = "horizon") {
    const clocks = tauEndClocks(group);
    const fb = tauEndDefault(group);
    const s = clampTauEndHmForGroup(hm, group);
    const i = clocks.indexOf(s);
    return i >= 0 ? i : clocks.indexOf(fb);
  }

  function _paintTauEndSlider(el, hm, group = "horizon") {
    if (!el) return;
    const clocks = tauEndClocks(group);
    const clock = clampTauEndHmForGroup(hm, group);
    const idx = tauEndIndex(clock, group);
    el.max = String(Math.max(0, clocks.length - 1));
    el.setAttribute("aria-valuemax", el.max);
    el.value = String(idx);
    el.setAttribute("aria-valuenow", String(idx));
    el.setAttribute("aria-valuetext", clock);
    const lab = el.closest(".quant-tau-end-slider");
    // 仅 τc 有 0 前缀；窗头自 09:35 起
    if (lab) lab.classList.toggle("is-zero-prefix", group === "tc" && clock === "09:30");
    const bound0 = lab && lab.querySelector(".quant-tau-end-bound");
    if (bound0 && group === "horizon") bound0.textContent = "09:35";
    const outId = el.id ? `${el.id}-val` : "";
    const out = outId ? document.getElementById(outId) : null;
    if (out) {
      out.value = clock;
      out.textContent = clock;
    }
  }

  function readStoredTauEndHm(group) {
    const g = tauEndGroup(group);
    const fb = tauEndDefault(g);
    try {
      const saved = localStorage.getItem(tauEndStorageKey(g));
      if (saved) return clampTauEndHmForGroup(saved, g);
    } catch (_) {}
    return fb;
  }

  /** @param {string} [headOrGroup] 头名 tc/t30… 或组名；默认 horizon */
  function readTauEndHm(headOrGroup) {
    const group = tauEndGroup(headOrGroup);
    const heads = tauEndHeads(group);
    const clocks = tauEndClocks(group);
    const fb = tauEndDefault(group);
    const fbIdx = tauEndDefaultIdx(group);
    let el = null;
    for (const head of heads) {
      el = document.getElementById(`quant-fit-tau-end-${head}`);
      if (el) break;
    }
    let hm = fb;
    if (el) {
      const raw = Number(el.value);
      const idx = Math.max(
        0,
        Math.min(
          clocks.length - 1,
          Math.round(Number.isFinite(raw) ? raw : fbIdx)
        )
      );
      hm = clocks[idx] || fb;
    } else {
      hm = readStoredTauEndHm(group);
    }
    hm = clampTauEndHmForGroup(hm, group);
    // 拟合读数只同步本组滑条，不写 storage（storage 仅用户拖动时写入）
    for (const head of heads) {
      _paintTauEndSlider(
        document.getElementById(`quant-fit-tau-end-${head}`),
        hm,
        group
      );
    }
    return hm;
  }

  /** 拟合请求体片段：按头读对应组前缀终点。 */
  function readTauEndFitParams(head) {
    return { tau_end_hm: readTauEndHm(head) };
  }

  /** 卡头摘要：非默认前缀才标「前缀HH:MM」；τc 默认 09:30 / 0 前缀不占卡头。 */
  function formatTauEndBit(data, opts) {
    const src = data && typeof data === "object" ? data : {};
    const ySpec = src.y_spec && typeof src.y_spec === "object" ? src.y_spec : {};
    const forHorizon = !!(opts && opts.forHorizon);
    let hm =
      src.tau_end_hm ||
      ySpec.tau_end_hm ||
      (Array.isArray(src.tau_grid) && src.tau_grid.length
        ? src.tau_grid[src.tau_grid.length - 1]
        : "");
    if (!hm && (src.zero_prefix || ySpec.zero_prefix)) hm = "09:30";
    if (!hm && (src.tau === "open" || ySpec.tau === "open")) hm = "09:30";
    hm = String(hm || "").trim().slice(0, 5);
    if (forHorizon) {
      if (hm === "09:30" || hm.toLowerCase() === "open") hm = "09:35";
      if (!hm || !TAU_END_CLOCKS_HORIZON.includes(hm)) return "";
      // τ* 默认 11:00 不占卡头
      if (hm === DEFAULT_TAU_END_HM_HORIZON) return "";
      return `前缀${hm}`;
    }
    if (!hm || !TAU_END_CLOCKS.includes(hm)) return "";
    // τc 默认 09:30（0 前缀·OC）不占卡头
    if (hm === "09:30" || hm === DEFAULT_TAU_END_HM_TC) return "";
    return `前缀${hm}`;
  }

  function applyTauEndHm(hm, opts) {
    const group = tauEndGroup((opts && (opts.head || opts.group)) || "horizon");
    const clock = clampTauEndHmForGroup(hm, group);
    const persist = !opts || opts.persist !== false;
    if (persist) {
      try {
        localStorage.setItem(tauEndStorageKey(group), clock);
      } catch (_) {}
    }
    for (const head of tauEndHeads(group)) {
      _paintTauEndSlider(
        document.getElementById(`quant-fit-tau-end-${head}`),
        clock,
        group
      );
    }
    return clock;
  }

  /** 从模型 / last_report 取训练前缀钟；无则 ""。 */
  function pickTauEndHmFromReport(data, headOrGroup) {
    const group = tauEndGroup(headOrGroup || "horizon");
    const clocks = tauEndClocks(group);
    const src = data && typeof data === "object" ? data : {};
    const last =
      src.last_report && typeof src.last_report === "object" ? src.last_report : null;
    const ySpec = src.y_spec && typeof src.y_spec === "object" ? src.y_spec : {};
    const rm =
      src.return_model && typeof src.return_model === "object" ? src.return_model : {};
    const rmYs = rm.y_spec && typeof rm.y_spec === "object" ? rm.y_spec : {};
    const lastYs =
      last && last.y_spec && typeof last.y_spec === "object" ? last.y_spec : {};
    const candidates = [
      src.tau_end_hm,
      ySpec.tau_end_hm,
      rmYs.tau_end_hm,
      last && last.tau_end_hm,
      lastYs.tau_end_hm,
    ];
    for (const c of candidates) {
      let s = String(c || "")
        .trim()
        .replace("：", ":")
        .slice(0, 5);
      if (group === "horizon" && (s === "09:30" || s.toLowerCase() === "open")) {
        s = "09:35";
      }
      if (clocks.includes(s)) return s;
    }
    return "";
  }

  function pickTauEndStamp(data) {
    const src = data && typeof data === "object" ? data : {};
    const last =
      src.last_report && typeof src.last_report === "object" ? src.last_report : {};
    for (const k of ["fitted_at", "saved_at", "promoted_at", "updated_at"]) {
      for (const bag of [src, last]) {
        const t = Date.parse(String((bag && bag[k]) || ""));
        if (Number.isFinite(t)) return t;
      }
    }
    return 0;
  }

  /** 组内多头预填竞态：只采纳该组 fitted_at 更新的那次。 */
  const _tauEndRestoreBestTs = { tc: -1, horizon: -1 };

  function restoreTauEndFromFitReport(data, head) {
    const group = tauEndGroup(head);
    const hm = pickTauEndHmFromReport(data, head);
    if (!hm) return null;
    const ts = pickTauEndStamp(data);
    if (ts < _tauEndRestoreBestTs[group]) return null;
    _tauEndRestoreBestTs[group] = ts;
    return applyTauEndHm(hm, { persist: true, group });
  }

  function hydrateTauEndParams() {
    // 先画各组 localStorage / 默认；各头 /model 预填后再按组内最新拟合覆盖
    for (const group of ["tc", "horizon"]) {
      const hm = readStoredTauEndHm(group);
      const clocks = tauEndClocks(group);
      for (const head of tauEndHeads(group)) {
        const el = document.getElementById(`quant-fit-tau-end-${head}`);
        if (!el) continue;
        _paintTauEndSlider(el, hm, group);
        if (el.dataset.wired === "1") continue;
        el.dataset.wired = "1";
        const sync = () => {
          const fb = tauEndDefault(group);
          const fbIdx = tauEndDefaultIdx(group);
          const raw = Number(el.value);
          const idx = Math.max(
            0,
            Math.min(
              clocks.length - 1,
              Math.round(Number.isFinite(raw) ? raw : fbIdx)
            )
          );
          applyTauEndHm(clocks[idx] || fb, { persist: true, group });
        };
        el.addEventListener("input", sync);
        el.addEventListener("change", sync);
      }
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
    formatTrainTiersBit,
    hydrateCrossSectionZParams,
    readTauEndHm,
    readTauEndFitParams,
    formatTauEndBit,
    applyTauEndHm,
    pickTauEndHmFromReport,
    restoreTauEndFromFitReport,
    hydrateTauEndParams,
    setPrefsHorizonDays,
    getPrefsHorizonDays,
  };
}
