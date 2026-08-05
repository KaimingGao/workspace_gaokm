/**
 * promote 提示缓存（localStorage / sessionStorage）。
 */

export const PROMOTE_HINTS_KEY = "investment_promote_hints_v1";
export const PROMOTE_HARD_GATE_KEY = "investment_promote_hard_gate_v1";
export const PROMOTE_HINTS_TTL_HOURS_KEY = "investment_promote_hints_ttl_h_v1";
export const PROMOTE_EXPIRE_HARD_KEY = "investment_promote_expire_hard_v1";
export const PROMOTE_HINTS_TTL_HOURS_DEFAULT = 24;

export function readPromoteTtlHours() {
  const el = document.getElementById("quant-promote-ttl-hours");
  let h = PROMOTE_HINTS_TTL_HOURS_DEFAULT;
  if (el && el.value !== "") {
    const n = Number(el.value);
    if (Number.isFinite(n)) h = n;
  } else {
    try {
      const saved = Number(sessionStorage.getItem(PROMOTE_HINTS_TTL_HOURS_KEY));
      if (Number.isFinite(saved) && saved > 0) h = saved;
    } catch (_) {
      /* ignore */
    }
  }
  return Math.max(1, Math.min(168, Math.round(h)));
}

export function promoteHintsTtlMs() {
  return readPromoteTtlHours() * 60 * 60 * 1000;
}

export function persistPromoteTtlHours(h) {
  try {
    sessionStorage.setItem(PROMOTE_HINTS_TTL_HOURS_KEY, String(h));
  } catch (_) {
    /* ignore */
  }
}

export function readPromoteExpireHard() {
  const el = document.getElementById("strategy-promote-expire-hard");
  if (el) return !!el.checked;
  try {
    return sessionStorage.getItem(PROMOTE_EXPIRE_HARD_KEY) === "1";
  } catch (_) {
    return false;
  }
}

export function persistPromoteExpireHard(on) {
  try {
    sessionStorage.setItem(PROMOTE_EXPIRE_HARD_KEY, on ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
}

export function readPromoteHardGate() {
  const el = document.getElementById("quant-promote-hard-gate");
  if (el) return !!el.checked;
  try {
    return sessionStorage.getItem(PROMOTE_HARD_GATE_KEY) === "1";
  } catch (_) {
    return false;
  }
}

export function persistPromoteHardGate(on) {
  try {
    sessionStorage.setItem(PROMOTE_HARD_GATE_KEY, on ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
}

export function isPromoteHintsExpired(pack, now = Date.now()) {
  if (!pack || !pack.at) return true;
  const ttl =
    Number(pack.ttl_ms) > 0 ? Number(pack.ttl_ms) : promoteHintsTtlMs();
  return now - Number(pack.at) > ttl;
}

/** @returns {object|null} pack for localStorage */
export function buildPromoteHintsPack(data, { hardGateAtCache } = {}) {
  if (!data || !data.success) return null;
  const req = data.request || {};
  const ttlMs = promoteHintsTtlMs();
  return {
    at: Date.now(),
    ttl_ms: ttlMs,
    lookback: req.lookback,
    top_k: req.top_k,
    hints: Array.isArray(data.promote_hints) ? data.promote_hints : [],
    ic_equity_align: data.ic_equity_align || null,
    total_return_pct: (data.metrics || {}).total_return_pct,
    excess_pct: (data.benchmark || {}).excess_pct,
    hard_gate_at_cache:
      hardGateAtCache != null ? hardGateAtCache : readPromoteHardGate(),
  };
}

export function savePromoteHintsPack(pack) {
  if (!pack) return;
  try {
    localStorage.setItem(PROMOTE_HINTS_KEY, JSON.stringify(pack));
  } catch (_) {
    /* ignore */
  }
}

export function loadCachedPromoteHints() {
  try {
    const raw = localStorage.getItem(PROMOTE_HINTS_KEY);
    if (!raw) return null;
    const pack = JSON.parse(raw);
    if (isPromoteHintsExpired(pack)) {
      localStorage.removeItem(PROMOTE_HINTS_KEY);
      return {
        expired: true,
        at: pack.at,
        ttl_ms: pack.ttl_ms || promoteHintsTtlMs(),
      };
    }
    return pack;
  } catch (_) {
    return null;
  }
}

/** @param {object|null|undefined} result backtest result with optional promote_hints */
export function buildResearchPromoteMeta(result) {
  const cached = loadCachedPromoteHints();
  const liveHints = (result && result.promote_hints) || [];
  const ttlH = readPromoteTtlHours();
  return {
    hard_gate: readPromoteHardGate(),
    expire_hard_block: readPromoteExpireHard(),
    hints_ttl_hours: ttlH,
    hints_ttl_ms: ttlH * 3600000,
    live_promote_hints: liveHints,
    cached_promote_hints:
      cached && !cached.expired
        ? {
            at: cached.at,
            ttl_ms: cached.ttl_ms || ttlH * 3600000,
            lookback: cached.lookback,
            top_k: cached.top_k,
            hints: cached.hints || [],
            hard_gate_at_cache: cached.hard_gate_at_cache,
          }
        : cached && cached.expired
          ? { expired: true, at: cached.at, ttl_ms: cached.ttl_ms }
          : null,
    note: "hard_gate=回测应用最优；expire_hard_block=策略过期硬拦；TTL 可在回测页配置。",
  };
}
