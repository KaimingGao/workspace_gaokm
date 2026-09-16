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

/** (1+a%)(1+b%)−1，百分点。 */
export function compoundPct(a, b) {
  const fa = Number(a);
  const fb = Number(b);
  if (!Number.isFinite(fa) || !Number.isFinite(fb)) return null;
  return ((1 + fa / 100) * (1 + fb / 100) - 1) * 100;
}

/** nowcast oc：nc（昨收口径）→ open→close，与 y_τ 同窗口。 */
export function nowcastOcPct(yNowcastCc, gapPct) {
  const cc = Number(yNowcastCc);
  const gap = Number(gapPct);
  if (!Number.isFinite(cc) || !Number.isFinite(gap)) return null;
  const denom = 1 + gap / 100;
  if (Math.abs(denom) < 1e-9) return null;
  return ((1 + cc / 100) / denom - 1) * 100;
}

export function resolveGapPct(it) {
  if (!it || typeof it !== "object") return null;
  // 优先 τ 特征包内缺口（与组成 / 打分瞬间同口径）；勿被 live 顶层 gap 漂移带偏
  const ft = it.features_tau;
  if (ft && typeof ft === "object") {
    const fg = _numField(ft.gap_pct);
    if (fg != null) return fg;
  }
  return _numField(it.gap_pct);
}

/** ŷ_oc（开盘后）按缺口映到现价对昨收。 */
export function liftTauVsPrevClose(it, yTau) {
  const t = _numField(yTau);
  if (t == null) return null;
  const gap = resolveGapPct(it);
  if (gap == null) return t;
  const lifted = compoundPct(gap, t);
  return lifted != null && Number.isFinite(lifted) ? lifted : t;
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

/** rank_lots 权：fusion_w_oo / fusion_w_oc；旧键 fusion_w_trade / fusion_w_nowcast。 */
export function fusionWeightsFromItem(it) {
  const d = it && typeof it === "object" ? it : {};
  let wOo = _numField(d.fusion_w_oo);
  if (wOo == null) wOo = _numField(d.fusion_w_trade);
  let wOc = _numField(d.fusion_w_oc);
  if (wOc == null) wOc = _numField(d.fusion_w_nowcast);
  if (wOo == null) wOo = 0.5;
  if (wOc == null) wOc = 0.5;
  wOo = Math.max(0, Math.min(1, wOo));
  wOc = Math.max(0, Math.min(1, wOc));
  const s = wOo + wOc;
  if (s <= 1e-12) return { wOo: 0.5, wOc: 0.5 };
  return { wOo: wOo / s, wOc: wOc / s };
}

/** 隔夜叠入 ŷ_oc 的系数；默认 0。 */
export function fusionWCoFromItem(it) {
  const d = it && typeof it === "object" ? it : {};
  let w = _numField(d.fusion_w_co);
  if (w == null) w = _numField(d.y_on_alpha);
  if (w == null) return 0;
  return Math.max(0, Math.min(10, w));
}

function fusePct(left, right, wLeft, wRight) {
  const a = _numField(left);
  const b = _numField(right);
  if (a == null && b == null) return null;
  if (a == null) return b;
  if (b == null) return a;
  let wl = Number(wLeft);
  let wr = Number(wRight);
  if (!Number.isFinite(wl)) wl = 0.5;
  if (!Number.isFinite(wr)) wr = 0.5;
  wl = Math.max(0, wl);
  wr = Math.max(0, wr);
  const s = wl + wr;
  if (s <= 1e-12) return 0.5 * a + 0.5 * b;
  return (wl * a + wr * b) / s;
}

/** ((1+ŷ_oc)(1+w_co·ŷ_co)−1)×100。w_co=0 或缺 ŷ_co 则 ŷ_oc。 */
export function ocWithCoPct(yOc, yCo, wCo) {
  const oc = _numField(yOc);
  if (oc == null) return null;
  const co = _numField(yCo);
  let wc = Number(wCo);
  if (!Number.isFinite(wc)) wc = 0;
  if (co == null || wc <= 1e-12) return oc;
  return compoundPct(oc, wc * co);
}

/** 拟合 ŷ_oc（open→close）；勿用 remaining 映射后的 predicted_score_tau。 */
export function pickYOcFitted(it) {
  if (!it || typeof it !== "object") return null;
  for (const c of [it.y_oc, it.predicted_score_oc, it.y_tau_oc, it.predicted_score_tau_oc]) {
    const n = _numField(c);
    if (_looksLikeYhatPct(n)) return n;
  }
  return resolveTauScore(it);
}

function hasFusionWeights(it) {
  if (!it || typeof it !== "object") return false;
  return (
    _numField(it.fusion_w_oo) != null ||
    _numField(it.fusion_w_trade) != null ||
    _numField(it.fusion_w_oc) != null ||
    _numField(it.fusion_w_nowcast) != null
  );
}

/** 表列 ranking = w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)。不用 dual_score 缺口抬升。 */
export function rankingPct(it) {
  if (!it || typeof it !== "object") return null;
  const oo = resolveEodScore(it);
  const oc = pickYOcFitted(it);
  const co = resolveOnScore(it);
  const { wOo, wOc } = fusionWeightsFromItem(it);
  const wCo = fusionWCoFromItem(it);
  const fused = fusePct(oo, ocWithCoPct(oc, co, wCo), wOo, wOc);
  return _looksLikeYhatPct(fused) ? fused : null;
}

/** 表列主分：ranking = w_oo·ŷ_oo + w_oc·(ŷ_oc ∘ w_co·ŷ_co)。不做 T ŷ_trade。 */
export function resolveRankingScore(it) {
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
  const ranked = rankingPct(it);
  const stamped = _numField(it.ranking);
  // 有 fusion_w 才现算；否则缺权会按 0.5/0.5 把 tip 算歪，信落盘 ranking
  if (hasFusionWeights(it) && _looksLikeYhatPct(ranked)) return ranked;
  if (_looksLikeYhatPct(stamped)) return stamped;
  if (_looksLikeYhatPct(ranked)) return ranked;
  return null;
}

/** @deprecated 表列 ranking；请用 resolveRankingScore。做 T 用 resolveYTradeScore。 */
export const resolveTradeScore = resolveRankingScore;

/** 做 T 主分 ŷ_trade（blend），不是调仓 ranking。 */
export function resolveYTradeScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  for (const c of [it.y_trade, it.decision_score, it.predicted_score_blend]) {
    const n = _numField(c);
    if (_looksLikeYhatPct(n)) return n;
  }
  return null;
}

/** leftover nowcast 簿字段（仅 fallback；刷簿不再写入）。 */
function _nowcastFromBookField(it) {
  const n = _numField(it.predicted_score_nowcast);
  if (n == null || !_looksLikeYhatPct(n)) return null;
  return n;
}

/** nowcast = nc：仅读 leftover，不再 Kalman 重算。 */
export function resolveNowcastCcScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const persisted = _numField(it.y_nc);
  if (persisted != null && _looksLikeYhatPct(persisted)) return persisted;
  const yn = _numField(it.y_nowcast ?? it.y_nc);
  if (yn != null && _looksLikeYhatPct(yn)) return yn;
  return _nowcastFromBookField(it);
}

/** @deprecated 与 resolveNowcastCcScore 同源（nowcast = nc leftover）。 */
export function resolveNowcastScore(it) {
  return resolveNowcastCcScore(it);
}

export const Y_OO_TITLE = "ŷ_oo · open[T]→open[T+1]（%）";
export const Y_EOD_TITLE = Y_OO_TITLE;
export const Y_OC_TITLE = "ŷ_oc · open[T]→close[T]（拟合原值；τ 闸同源）";
export const Y_TAU_TITLE = Y_OC_TITLE;
/** 数据中心观察池 / 持仓表：ŷ_oc 用 09:30–10:00 调仓因果前缀 */
export const Y_OC_REBALANCE_TITLE =
  "ŷ_oc · 09:30–10:00 因果前缀 open[T]→close[T]";
export const RANKING_REBALANCE_TITLE =
  "ranking · 09:30–10:00 ŷ · w·ŷ_oo + w·(ŷ_oc∘w_co·ŷ_co)";
export const EOD_REALIZED_TITLE =
  "oo实 · open[T+1]/open[T]−1（与 ŷ_oo 同标签）";
export const TAU_REALIZED_TITLE =
  "oc实 · close[T]/open[T]−1（与 ŷ_oc 同标签）";
export const Y_CO_TITLE = "ŷ_co · close[T]→open[T+1]（对照；不进 ranking）";
export const Y_ON_TITLE = Y_CO_TITLE;
/** leftover nowcast = nc：旧簿对照昨收，不进主决策。 */
export const Y_NC_TITLE =
  "nowcast（nc）· leftover 对照昨收，不进决策";
/** leftover nowcast oc：把 nc 映到 open→close（与 y_oc 同窗口）。 */
export const Y_NC_OC_TITLE =
  "nowcast oc · leftover nc 映到 open→close（异号闸 OC 开时对照 y_oc）";
/** @deprecated 用 Y_NC_TITLE */
export const Y_NOWCAST_TITLE = Y_NC_TITLE;

/** ŷ_hl：极值序 signed range%（path_ridge；多 τ 训 / live 单钟）；dual_y 与 y_τ 联合选向。 */
export const Y_HL_TITLE = "ŷ_hl · 极值序 signed (H−L)/ref%";
/** @deprecated 用 Y_HL_TITLE */
export const Y_PATH_TITLE = Y_HL_TITLE;
export const PATH_REALIZED_TITLE =
  "HL实 · 先 low→high 为正、先 high→low 为负（与 ŷ_hl 同标签）";
export const Y_CX_TITLE =
  "ŷ_cx · 本轮前缀 ŷ（全日 1−D/L ∈[0,1]，表内×100%）· 0=直线 · 1=最折 · ŷ_cx>门槛则跳过";
/** @deprecated 用 Y_CX_TITLE */
export const Y_COMPLEXITY_TITLE = Y_CX_TITLE;
export const Y_TPD_TITLE =
  "y_tpd · 本轮前缀 ŷ（全日转折点密度 ∈[0,1]，表内×100%）· 0=无反转 · 1=每根都反转 · ŷ_tpd>门槛则跳过";
export const Y_τc_TITLE =
  "ŷ_τc · Ridge 预估 close[T]/price(τ)−1 · 模型 price→close";
export const Y_TC_TITLE = Y_τc_TITLE;
export const Y_R_TITLE = Y_τc_TITLE;
export const Y_T30_TITLE =
  "ŷ_τ30 · Ridge 预估 price(τ⊕30m)/price(τ)−1 · 做 T 旁路，不进 C_τ";
export const T30_REALIZED_TITLE =
  "τ30实 · price(τ⊕30m)/price(τ)−1（与 ŷ_τ30 同标签）";
export const Y_T60_TITLE =
  "ŷ_τ60 · Ridge 预估 price(τ⊕60m)/price(τ)−1 · 做 T 旁路，不进 C_τ";
export const T60_REALIZED_TITLE =
  "τ60实 · price(τ⊕60m)/price(τ)−1（与 ŷ_τ60 同标签）";
export const Y_T90_TITLE =
  "ŷ_τ90 · Ridge 预估 price(τ⊕90m)/price(τ)−1 · 做 T 旁路，不进 C_τ";
export const T90_REALIZED_TITLE =
  "τ90实 · price(τ⊕90m)/price(τ)−1（与 ŷ_τ90 同标签）";
/** τ 后窗口符号和：ŷ_τw = f(ŷ_τ30)+f(ŷ_τ60)+f(ŷ_τ90)；f(x)=1 if x>0 else −1 */
export const Y_TW_TITLE =
  "ŷ_τw · τ后窗口符号和 f(ŷ_τ30)+f(ŷ_τ60)+f(ŷ_τ90)；f(x)=1 if x>0 else −1 · 旁路，不进 C_τ";
export const TW_REALIZED_TITLE =
  "τw实 · f(τ30实)+f(τ60实)+f(τ90实)；f(x)=1 if x>0 else −1（缺头不计）";
export const R_HAT_TITLE =
  "R̂_τ · Ĉ_τ/price(τ)−1 · remaining(clip(ŷ_oc×scale), price) · 与 Ĉ_τ 同目标 · 不参与选腿 · 预估(真实)";
export const R_REALIZED_TITLE =
  "τc实 · close[T]/price(τ)−1（与 ŷ_τc / R̂_τ 同标签）";

/** OC 头原始 ŷ（映射前）：与组成合计 / tip 大标题同口径。 */
function _tauOcRaw(it) {
  if (!it || typeof it !== "object") return null;
  const ft = it.formula_terms_tau || it.score_formula_terms_tau;
  for (const c of [
    it.y_tau_oc,
    it.predicted_score_tau_oc,
    ft && typeof ft === "object" ? ft.y_tau_raw : null,
    ft && typeof ft === "object" ? ft.total : null,
  ]) {
    const n = _numField(c);
    if (_looksLikeYhatPct(n)) return n;
  }
  return null;
}

/** 分钟时钟剩余映射后的 ŷ_oc（决策/融合用）；无映射时与 OC 相同。 */
export function resolveTauMappedScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const tau = _numField(it.predicted_score_tau ?? it.score_rem);
  return _looksLikeYhatPct(tau) ? tau : null;
}

/** ŷ_oc 表列 / tip：Ridge 拟合原值（T收/T开），与组成合计同口径。

  优先 y_tau_oc（映射前）；勿把 remaining 映射后的 predicted_score_tau 当成拟合原值，
  否则 tip 大标题会与「合计 ŷ_oc」对不上（做T明细常见）。
  勿在此做缺口∘抬昨收——那只用于 ŷ_trade 融合。
  */
export function resolveTauScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const oc = _tauOcRaw(it);
  if (oc != null) return oc;
  return resolveTauMappedScore(it);
}

/** 缺口∘ŷ_oc（昨收口径）；对照用拟合原值，不进 τ 表列。 */
export function resolveTauLiftedScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const tau = resolveTauScore(it);
  if (tau == null) return null;
  const lifted = liftTauVsPrevClose(it, tau);
  return _looksLikeYhatPct(lifted) ? lifted : null;
}

/** ŷ_hl：极值序 signed range%（path_ridge）。 */
export function resolvePathScore(it) {
  if (!it || typeof it !== "object") return null;
  for (const c of [it.predicted_score_hl, it.y_hl, it.predicted_score_path, it.y_path]) {
    const n = _numField(c);
    if (n != null) return n;
  }
  return null;
}

/** 表列 y_hl：signed range %；|v|<1 用两位小数避免 0.02% 显示成 +0.0。 */
export function fmtPathScore(v, opts = {}) {
  const empty = opts.empty ?? "—";
  // null/"" 不能走 Number()：Number(null)===0，会把「未打分」显示成 0
  if (v == null || v === "") return empty;
  const n = Number(v);
  if (!Number.isFinite(n)) return empty;
  const digits = opts.digits ?? (Math.abs(n) < 1 ? 2 : 1);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
}

/** ŷ_co：隔夜缺口对照头。 */
export function resolveOnScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  for (const c of [it.y_co, it.predicted_score_co, it.predicted_score_on, it.y_on]) {
    const n = _numField(c);
    if (_looksLikeYhatPct(n)) return n;
  }
  return null;
}

function _pcFormulaIsLegacy(formula) {
  const s = String(formula || "")
    .replace(/\s/g, "")
    .replace(/（/g, "(")
    .replace(/）/g, ")");
  if (!s) return false;
  const low = s.toLowerCase();
  if (!low.includes("price") || !low.includes("close")) return false;
  return low.indexOf("price") < low.indexOf("close");
}

function _pcFormulaOf(it) {
  if (!it || typeof it !== "object") return "";
  for (const key of ["y_spec_τc", "y_spec_tc", "y_spec_to", "y_spec_pc", "y_spec_r", "y_spec"]) {
    const spec = it[key];
    if (spec && typeof spec === "object" && spec.formula) return String(spec.formula);
    if (spec && typeof spec !== "object") return String(spec);
  }
  const rm = it.return_model;
  if (rm && typeof rm === "object") {
    const nested = _pcFormulaOf(rm);
    if (nested) return nested;
  }
  const target = String(it.horizon_mode || it.target || "")
    .replace(/-/g, "_")
    .toLowerCase();
  if (target.includes("close_over_price")) return "close[T]/price[τ]-1";
  if (target.includes("price_over_close")) return "price[τ]/close[T]-1";
  return "";
}

/** ŷ_τc：Ridge 模型预估 price(τ)→close(T)。旧簿若把 remaining 盖进 y_τc，改读 Ridge。 */
export function resolveYτcScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  const rem = _numField(it.remaining_oc ?? it.r_hat ?? it.residual);
  const src = String(it.y_τc_source || "");
  const ridge = _numField(it.y_τc_ridge);

  const fromYr = () => {
    const yr = _numField(it.y_r ?? it.y_r_hat ?? it.predicted_score_r);
    if (yr == null || !_looksLikeYhatPct(yr)) return null;
    const formula = _pcFormulaOf(it);
    if (formula && !_pcFormulaIsLegacy(formula)) return yr;
    const d = 1 + yr / 100;
    if (Math.abs(d) < 1e-12) return null;
    const tc = (1 / d - 1) * 100;
    return Number.isFinite(tc) ? tc : null;
  };

  if (src === "remaining_oc") {
    if (ridge != null && _looksLikeYhatPct(ridge)) return ridge;
    return fromYr();
  }
  for (const c of [
    it["y_τc"],
    it["predicted_score_τc"],
    it.y_tc,
    it.predicted_score_tc,
    it.y_to,
    it.predicted_score_to,
    it.y_pc,
    it.predicted_score_pc,
  ]) {
    const n = _numField(c);
    if (!_looksLikeYhatPct(n)) continue;
    if (rem != null && Math.abs(n - rem) < 1e-3) {
      const model = ridge != null && _looksLikeYhatPct(ridge) ? ridge : fromYr();
      if (model != null && Math.abs(model - rem) > 1e-3) return model;
    }
    return n;
  }
  if (ridge != null && _looksLikeYhatPct(ridge)) return ridge;
  return fromYr();
}

/** @deprecated 用 resolveYτcScore */
export function resolveTcScore(it) {
  return resolveYτcScore(it);
}

/** ŷ_τ30：Ridge 预估 price(τ⊕30m)/price(τ)−1。 */
export function resolveYT30Score(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  for (const c of [it["y_τ30"], it.y_t30, it.predicted_score_t30, it.y_t30_hat]) {
    const n = _numField(c);
    if (n != null && _looksLikeYhatPct(n)) return n;
  }
  return null;
}

/** ŷ_τ60：Ridge 预估 price(τ⊕60m)/price(τ)−1。 */
export function resolveYT60Score(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  for (const c of [it["y_τ60"], it.y_t60, it.predicted_score_t60, it.y_t60_hat]) {
    const n = _numField(c);
    if (n != null && _looksLikeYhatPct(n)) return n;
  }
  return null;
}
/** ŷ_τ90：Ridge 预估 price(τ⊕90m)/price(τ)−1。 */
export function resolveYT90Score(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  for (const c of [it["y_τ90"], it.y_t90, it.predicted_score_t90, it.y_t90_hat]) {
    const n = _numField(c);
    if (n != null && _looksLikeYhatPct(n)) return n;
  }
  return null;
}

/** f(x)=1 if x>0 else −1；缺分不计。 */
export function yTwSign(x) {
  const n = _numField(x);
  if (n == null) return null;
  return n > 0 ? 1 : -1;
}

/** ŷ_τw = f(ŷ_τ30)+f(ŷ_τ60)+f(ŷ_τ90)；三头齐时 ∈ {−3,−1,+1,+3}。 */
export function blendYtw(y30, y60, y90) {
  let sum = 0;
  let n = 0;
  for (const s of [yTwSign(y30), yTwSign(y60), yTwSign(y90)]) {
    if (s == null) continue;
    sum += s;
    n += 1;
  }
  if (n === 0) return null;
  return sum;
}

/** ŷ_τw 表列：符号和，非百分比。 */
export function fmtYtwVote(n) {
  const v = _numField(n);
  if (v == null) return "—";
  const k = Math.round(v);
  if (k > 0) return `+${k}`;
  return String(k);
}

/** ŷ_τw：由 ŷ_τ30/60/90 现算符号和；不读旧加权落盘。 */
export function resolveYTWScore(it) {
  if (!it || typeof it !== "object") return null;
  if (isHeuristicScoreScale(it)) return null;
  return blendYtw(resolveYT30Score(it), resolveYT60Score(it), resolveYT90Score(it));
}

/** ŷ_oo：open[T]→open[T+1]（调仓 ranking 输入）。 */
export function resolveEodScore(it) {
  if (!it || typeof it !== "object") return null;
  // OOS 失败降级：表列 score 是 0–100 启发式，不可当 ŷ_oo%
  if (isHeuristicScoreScale(it)) {
    return null;
  }
  for (const c of [it.scoreEodNum, it.y_oo, it.predicted_score_oo, it.predicted_score_eod, it.predicted_score]) {
    const n = _numField(c);
    if (n != null) return n;
  }
  // 双头 align 后 score=ŷ_trade，不可当 EOD；仅旧单头无 blend/decision 时用 score
  const sc = _numField(it.score);
  if (sc != null) {
    const tradeProbe = _numField(it.decision_score) ?? _numField(it.predicted_score_blend);
    if (tradeProbe == null) return sc;
  }
  return null;
}

/** (1+ŷ_oo)/(1+已实现)−1；无已实现时退回 ŷ_oo（尚未开盘）。 */
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

/** ŷ_oo_rem：盘中有已实现/缺口时几何映射；``eod_next`` 不减缺口（信簿 rem / ŷ_oo）。 */
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
