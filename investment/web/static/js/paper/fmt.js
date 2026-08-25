/** Paper UI formatters. */

export function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeText(String(v));
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function fmtPriceUnit(v, unit, currency) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return currency === "CNY" ? `${n}${unit || "元"}` : `${unit || ""}${n}`;
}

export function escapeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function fmtPct(v, { signed = false } = {}) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeText(String(v));
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n}%`;
}

export function metricCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "up" : "down";
}

function _numField(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 正交加权 ŷ_trade；权重缺省 0.5/0.5。允许单头。 */
export function fuseOrthogonalTrade(it, left, right) {
  const a = _numField(left);
  const b = _numField(right);
  if (a == null && b == null) return null;
  const w = (it && it.dual_score_weights) || {};
  let we = Number(w.w_eod);
  let wt = Number(w.w_tau);
  if (!Number.isFinite(we)) we = 0.5;
  if (!Number.isFinite(wt)) wt = 0.5;
  const s = we + wt;
  if (a == null) return b;
  if (b == null) return a;
  if (Math.abs(s) < 1e-12) return 0.5 * a + 0.5 * b;
  return (we * a + wt * b) / s;
}

/** (1+a%)(1+b%)−1，百分点。 */
export function compoundPct(a, b) {
  const fa = Number(a);
  const fb = Number(b);
  if (!Number.isFinite(fa) || !Number.isFinite(fb)) return null;
  return ((1 + fa / 100) * (1 + fb / 100) - 1) * 100;
}

export function resolveGapPct(it) {
  if (!it || typeof it !== "object") return null;
  const g = _numField(it.gap_pct);
  if (g != null) return g;
  const ft = it.features_tau;
  if (ft && typeof ft === "object") return _numField(ft.gap_pct);
  return null;
}

/** ŷ_τ（开盘后）按缺口映到现价对昨收。 */
export function liftTauVsPrevClose(it, yTau) {
  const t = _numField(yTau);
  if (t == null) return null;
  const gap = resolveGapPct(it);
  if (gap == null) return t;
  const lifted = compoundPct(gap, t);
  return lifted != null && Number.isFinite(lifted) ? lifted : t;
}
export function expressTradeVsPrevClose(it, yOc) {
  if (yOc == null || !Number.isFinite(Number(yOc))) return null;
  const n = Number(yOc);
  if (!it || typeof it !== "object") return n;
  if (isHeuristicScoreScale(it)) return n;
  const vs = String(it.predicted_score_blend_vs || it.predicted_score_blend_cal_vs || "");
  if (vs === "prev_close") return n;
  if (String(it.dual_score_window || "") === "eod_next") return n;
  const gap = resolveGapPct(it);
  if (gap == null) return n;
  const lifted = compoundPct(gap, n);
  return lifted != null && Number.isFinite(lifted) ? lifted : n;
}

/** 是否为 0–100 启发式轨（表列仍只展示 ŷ%；heuristic 只在 tip）。 */
export function isHeuristicScoreScale(it) {
  if (!it || typeof it !== "object") return false;
  if (it.score_scale === "heuristic_0_100") return true;
  if (it.score_track === "heuristic") return true;
  if (it.return_model_source === "oos_failed_heuristic") return true;
  // 脏行：score≈heuristic 且量级像 0–100
  const hs = _numField(it.heuristic_score);
  const sc = _numField(it.score);
  if (hs != null && sc != null && Math.abs(hs) > 20 && Math.abs(hs - sc) < 1e-6) {
    return true;
  }
  return false;
}

/** ŷ 量级守卫：|v|>20 几乎不可能是隔夜收益百分点。 */
function _looksLikeYhatPct(n) {
  return n != null && Number.isFinite(n) && Math.abs(n) <= 20;
}

/** 表列主分：始终 ŷ_trade / 组ŷ%；绝不返回 heuristic 0–100。口径=现价对昨收。 */
export function resolveTradeScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) {
    for (const c of [
      it.score_cluster,
      it.score_global,
      it.predicted_score_blend,
      it.predicted_score_eod_rem,
      it.predicted_score_eod,
      it.predicted_score,
      // 洞察 API 已把 ŷ 写入 score；仅当像 ŷ% 时采用
      it.score,
    ]) {
      const n = _numField(c);
      if (_looksLikeYhatPct(n)) return n;
    }
    return null;
  }
  const eod = resolveEodScore(it);
  const tau = _numField(it.predicted_score_tau ?? it.score_rem);
  const blend = _numField(it.predicted_score_blend);
  const win = String(it.dual_score_window || "");
  const eodNext = win === "eod_next";
  const weights = it.dual_score_weights;
  // 收盘后 / 显式剥离 τ：blend≈EOD 且仍有 ŷ_τ 是契约，不是旧簿；禁止前端再融回双头
  const tauStripped =
    eodNext ||
    (weights && weights.tau_in_trade === false) ||
    (String(it.dual_score_head || "") === "single_eod" && tau != null);
  const stale =
    !tauStripped &&
    blend != null &&
    eod != null &&
    tau != null &&
    Math.abs(blend - eod) < 1e-9 &&
    Math.abs(tau - eod) > 1e-6;
  const needCc =
    !tauStripped &&
    resolveGapPct(it) != null &&
    String(it.predicted_score_blend_vs || "") !== "prev_close";
  if (!tauStripped && (blend == null || stale || needCc)) {
    const tauCc = liftTauVsPrevClose(it, tau);
    const fused = fuseOrthogonalTrade(it, eod, tauCc);
    if (_looksLikeYhatPct(fused)) return fused;
  }
  if (_looksLikeYhatPct(blend)) return expressTradeVsPrevClose(it, blend);
  const candidates = [
    it.decision_score,
    it.predicted_score,
    it.score_cluster,
    it.score, // 最后：且必须像 ŷ%
  ];
  for (const c of candidates) {
    const n = _numField(c);
    if (_numField(it.heuristic_score) != null && n != null && Math.abs(n - _numField(it.heuristic_score)) < 1e-6 && Math.abs(n) > 20) {
      continue;
    }
    if (_looksLikeYhatPct(n)) return expressTradeVsPrevClose(it, n);
  }
  return null;
}

/** eod 列：g(ŷ_EOD)，就是对涨跌（现价对昨收）的回归预估。不含缺口。 */
export function resolveCalTradeScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const eodCal = resolveCalEodScore(it);
  if (eodCal != null) return eodCal;
  return resolveEodScore(it);
}

/** g(ŷ_EOD)：与涨跌同口径（现价对昨收）；表列 eod，不进残差。 */
export function resolveCalEodScore(it) {
  if (!it || typeof it !== "object") return null;
  const n = Number(it.predicted_score_cal);
  return Number.isFinite(n) ? n : null;
}

/** ŷ_nowcast：Kalman 权对照，口径=现价对昨收（与 ŷ_trade 相同）。 */
export function resolveNowcastScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const n = _numField(it.predicted_score_nowcast);
  let stored = null;
  if (n != null && _looksLikeYhatPct(n)) {
    const vs = String(it.nowcast_vs || "");
    if (vs === "prev_close" || String(it.dual_score_window || "") === "eod_next") {
      stored = n;
    } else {
      const gap = resolveGapPct(it);
      if (gap == null) stored = n;
      else {
        const lifted = compoundPct(gap, n);
        stored = lifted != null && Number.isFinite(lifted) ? lifted : n;
      }
    }
  }
  const fused = reconstructNowcastPrevClose(it);
  const eod = resolveEodScore(it);
  // 收盘后曾把 nowcast 塌成 ŷ_EOD：有 K 与 ŷ_τ 时用公式现算
  if (
    fused != null &&
    (stored == null ||
      (eod != null &&
        Math.abs(stored - eod) < 1e-4 &&
        Math.abs(fused - eod) > 5e-4))
  ) {
    return fused;
  }
  return stored;
}

/** (1−K)·ŷ_EOD + K·(缺口∘ŷ_τ)。旧簿缺 K 时用 Kalman 默认增益。 */
export function reconstructNowcastPrevClose(it) {
  if (!it || typeof it !== "object") return null;
  const eod = resolveEodScore(it);
  const tauCc = liftTauVsPrevClose(
    it,
    it.predicted_score_tau != null ? it.predicted_score_tau : it.score_rem
  );
  let k = _numField(it.nowcast_K);
  if (k == null && it.dual_score_weights) {
    k = _numField(it.dual_score_weights.nowcast_K);
  }
  if (k == null) k = 1.05 / 2.05; // ve=1, q=0.05, R=1
  if (eod == null || tauCc == null) return null;
  if (k < 0 || k > 1) return null;
  const n = (1 - k) * eod + k * tauCc;
  return Number.isFinite(n) ? n : null;
}

export const Y_EOD_TITLE = "ŷ_EOD · 隔夜主轴 open[T]/open[T−1]−1（%）";
export const Y_TAU_TITLE = "ŷ_τ · T收/T开（拟合原值；τ 闸同源）";
export const Y_ON_TITLE = "隔夜 open · 旁路";
export const Y_NOWCAST_TITLE =
  "ŷ_nowcast · Kalman 权昨收口径对照（与 y_trade / 涨跌同一目标），不进决策";

/** ŷ_τ 表列 / tip：Ridge 拟合原值（T收/T开），与组成合计、τ 买入闸同口径。

  勿在此做缺口∘抬昨收——那只用于 ŷ_trade / nowcast 融合；抬完会与 tip 拆解对不上，
  且 |昨收口径| 很大时会误导成「强 τ」（实际闸门看的仍是开→收原值）。
  */
export function resolveTauScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const tau = _numField(it.predicted_score_tau ?? it.score_rem);
  return _looksLikeYhatPct(tau) ? tau : null;
}

/** 缺口∘ŷ_τ（昨收口径）；仅融合/对照用，不进 τ 表列。 */
export function resolveTauLiftedScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const tau = _numField(it.predicted_score_tau ?? it.score_rem);
  if (tau == null) return null;
  const lifted = liftTauVsPrevClose(it, tau);
  return _looksLikeYhatPct(lifted) ? lifted : null;
}

/** ŷ_ON：隔夜 open 链旁路头。 */
export function resolveOnScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const n = _numField(it.predicted_score_on);
  return _looksLikeYhatPct(n) ? n : null;
}

/** ŷ_EOD：隔夜主轴（买门槛用这一层）。 */
export function resolveEodScore(it) {
  if (!it || typeof it !== "object") return null;
  // OOS 失败降级：表列 score 是 0–100 启发式，不可当 ŷ_EOD%
  if (isHeuristicScoreScale(it)) {
    return null;
  }
  const candidates = [
    it.scoreEodNum,
    it.predicted_score_eod,
    it.predicted_score,
    typeof it.score === "number" ? it.score : null,
  ];
  for (const c of candidates) {
    if (c == null || c === "") continue;
    const n = Number(c);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/** (1+ŷ_EOD)/(1+已实现)−1；无已实现时退回 ŷ_EOD（尚未开盘）。 */
export function eodRemainingAtTau(yEod, realized) {
  if (yEod == null || yEod === "") return null;
  const ye = Number(yEod);
  if (!Number.isFinite(ye)) return null;
  if (realized == null || realized === "") return ye;
  const r = Number(realized);
  if (!Number.isFinite(r)) return ye;
  const denom = 1 + r / 100;
  if (Math.abs(denom) < 1e-12) return null;
  return ((1 + ye / 100) / denom - 1) * 100;
}

/** ŷ_EOD_rem：盘中有已实现/缺口时几何映射；``eod_next`` 不减缺口（信簿 rem / ŷ_EOD）。 */
export function resolveEodRemScore(it) {
  if (!it || typeof it !== "object") return null;
  const y = resolveEodScore(it);
  if (String(it.dual_score_window || "") === "eod_next") {
    for (const c of [it.scoreEodRemNum, it.predicted_score_eod_rem]) {
      const n = _numField(c);
      if (n != null) return n;
    }
    return y;
  }
  const realized = it.realized_t1_to_tau;
  const gap = it.gap_pct;
  const r =
    realized != null && realized !== "" && Number.isFinite(Number(realized))
      ? Number(realized)
      : gap != null && gap !== "" && Number.isFinite(Number(gap))
        ? Number(gap)
        : null;
  if (y != null && r != null) {
    return eodRemainingAtTau(y, r);
  }
  for (const c of [it.scoreEodRemNum, it.predicted_score_eod_rem]) {
    const n = _numField(c);
    if (n != null) return n;
  }
  if (y == null) return null;
  return eodRemainingAtTau(y, null);
}

/** 截面 μ / med / n（持仓统计等轻量场景）。 */
export function scoreSeriesStats(vals) {
  const xs = (vals || [])
    .filter((x) => x != null && x !== "")
    .map(Number)
    .filter((n) => Number.isFinite(n));
  if (!xs.length) return { n: 0, mean: null, median: null };
  const sorted = [...xs].sort((a, b) => a - b);
  const mean = xs.reduce((s, x) => s + x, 0) / xs.length;
  const pos = (sorted.length - 1) / 2;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const median =
    lo === hi ? sorted[lo] : sorted[lo] * (hi - pos) + sorted[hi] * (pos - lo);
  return { n: xs.length, mean, median };
}

/** 表格收益分：只格式化 ŷ%。|v|>20 视为脏 heuristic，显示 —（不把两种量纲混一列）。 */
export function fmtScore(v, { empty = "—", signed = false, heuristic = false, digits = 3 } = {}) {
  if (v === null || v === undefined || v === "") return empty;
  const n = Number(v);
  if (!Number.isFinite(n)) return empty;
  if (heuristic || Math.abs(n) > 20) return empty;
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

/** 表列 Y 轴：与涨跌同列展示，固定两位小数。 */
export function fmtTableScore(it, v, opts = {}) {
  void it;
  return fmtScore(v, { digits: 2, ...opts });
}

/** 收益分红绿：正 → score-up（红），负 → score-down（绿），零 → score-flat。 */
export function scoreCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "score-na";
  if (n > 0) return "score-up";
  if (n < 0) return "score-down";
  return "score-flat";
}

export function paperFmtPct(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return `${n}%`;
}

export function paperMetricClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "up" : "down";
}
