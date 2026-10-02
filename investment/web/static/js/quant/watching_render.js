/**
 * 观察池 HTML 渲染 helpers（纯字符串 / 轻量 DOM 写入）。
 */
import { escapeHtml } from "../shared.js";
import { fmtTableScore, Y_OC_REBALANCE_TITLE, RANKING_REBALANCE_TITLE } from "../paper/fmt.js?v=p2544";
import { marketPriorDetailFields, tailAnomalyDetailFields, overheatDetailFields } from "../score_tooltip.js?v=p2544";
import { watchingNameSpanHtml } from "./names.js";
import { fitTierBadgeForCode } from "./fit_tier_ui.js";

export function describeWatchingSource(src, index) {
  const typ = String(src.type || "").toLowerCase() || "—";
  const label = `S${index + 1}`;
  const typLabel = typ === "static" ? "静态" : typ === "screen" ? "筛选" : typ;
  if (typ === "static") {
    const codes = (src.codes || []).map((c) => String(c));
    return {
      label,
      typ,
      typLabel,
      title: label,
      detail: codes.length ? codes.join("、") : "（空）",
      codes,
    };
  }
  if (typ === "screen") {
    const bits = [];
    if (src.sector) bits.push(String(src.sector));
    if (src.pe_min != null) bits.push(`PE≥${src.pe_min}`);
    if (src.pe_max != null) bits.push(`PE≤${src.pe_max}`);
    if (src.pb_max != null) bits.push(`PB≤${src.pb_max}`);
    if (src.change_min != null) bits.push(`涨跌≥${src.change_min}%`);
    if (src.change_max != null) bits.push(`涨跌≤${src.change_max}%`);
    if (src.limit != null) bits.push(`取${src.limit}`);
    return {
      label,
      typ,
      typLabel,
      title: label,
      detail: bits.join(" · ") || "筛选",
      codes: [],
    };
  }
  return {
    label,
    typ,
    typLabel: typ,
    title: label,
    detail: JSON.stringify(src),
    codes: [],
  };
}

export function shortOriginLabel(raw) {
  const text = String(raw || "").trim();
  if (!text) return "—";
  const m = text.match(/^S(\d+)/i);
  if (m) return `S${m[1]}`;
  if (text.includes("筛选") || text.includes("screen") || text.includes("合并")) {
    return "筛选";
  }
  return text;
}

export function matchWatchlistSource(code, sourceDescs) {
  const raw = String(code || "");
  for (const s of sourceDescs) {
    if (s.typ !== "static") continue;
    for (const c of s.codes) {
      if (c === raw || raw.includes(c) || c.includes(raw)) {
        return s.label;
      }
    }
  }
  return "筛选";
}

export function watchingScoreDetail(it) {
  // ŷ_oo / ŷ_oc / 缺口放前：data-score-detail 过长时避免被截掉
  const terms = slimFormulaTerms(
    (it && (it.score_formula_terms || it.formula_terms)) || null,
    10
  );
  const tauTerms = slimFormulaTerms(
    (it && (it.formula_terms_tau || it.score_formula_terms_tau)) || null,
    24
  );
  const coTerms = slimFormulaTerms(
    (it &&
      (it.formula_terms_co ||
        it.score_formula_terms_co ||
        it.formula_terms_on ||
        it.score_formula_terms_on)) ||
      null,
    12
  );
  const hasTerms =
    terms && Array.isArray(terms.terms) && terms.terms.length > 0;
  const hasTauTerms =
    tauTerms && Array.isArray(tauTerms.terms) && tauTerms.terms.length > 0;
  // 有分项拆解时不再塞整包系数，缩小属性体积、避免截断坏 JSON
  const coefs = hasTerms ? {} : (it && it.factor_coefficients) || {};
  const coefsTau = hasTauTerms
    ? null
    : (it && it.factor_coefficients_tau) || null;
  const sentInc =
    it && it.sentiment_include_in_score != null
      ? !!it.sentiment_include_in_score
      : null;
  const ep = (it && it.event_prior) || null;
  const eventPrior = ep
    ? {
        theme: !!ep.theme,
        warnings: Array.isArray(ep.warnings) ? ep.warnings.slice(0, 2) : [],
      }
    : null;
  const gapPct =
    it &&
    (it.features_tau && it.features_tau.gap_pct != null
      ? it.features_tau.gap_pct
      : it.gap_pct);
  // tip 头必须带 OC：缺 y_tau_oc 时用组成合计，避免落成剩余映射分
  let yTauOc =
    it &&
    (it.y_tau_oc != null
      ? it.y_tau_oc
      : it.predicted_score_tau_oc != null
        ? it.predicted_score_tau_oc
        : null);
  if (yTauOc == null && tauTerms && tauTerms.y_tau_raw != null) {
    yTauOc = tauTerms.y_tau_raw;
  }
  if (yTauOc == null && tauTerms && tauTerms.total != null) {
    yTauOc = tauTerms.total;
  }
  return JSON.stringify({
    stock_code: (it && (it.stock_code || it.code)) || null,
    // ranking 权/头靠前：data-score-detail 截断时 tip 仍与表列同式
    ranking: it && it.ranking,
    realized_ranking: it && it.realized_ranking,
    realized_oo: it && it.realized_oo,
    day_open: it && it.day_open,
    open: it && it.open,
    open_raw: it && it.open_raw,
    rebalance_px: it && it.rebalance_px,
    price_tau: it && it.price_tau,
    price_raw: it && it.price_raw,
    price: it && it.price,
    y_oo: it && it.y_oo,
    y_oc: it && it.y_oc,
    y_co: it && it.y_co,
    // 因子组成紧跟 ŷ 值：属性截断时 compact tip 仍能画出 β·z 表
    formula_terms: terms,
    score_formula_terms: terms,
    formula_terms_tau: tauTerms,
    score_formula_terms_tau: tauTerms,
    formula_terms_co: coTerms,
    fusion_w_oo: it && it.fusion_w_oo,
    fusion_w_oc: it && it.fusion_w_oc,
    fusion_w_co: it && it.fusion_w_co,
    // ŷ_oc OC 靠前，防止 data-score-detail 截断后 tip 退化成剩余映射分
    y_tau_oc: yTauOc,
    predicted_score_tau_oc: yTauOc,
    y_to: it && (it.y_to != null ? it.y_to : it.y_pc != null ? it.y_pc : it.y_r),
    y_pc: it && (it.y_pc != null ? it.y_pc : it.y_to),
    y_r: it && (it.y_r != null ? it.y_r : it.y_r_hat),
    predicted_score_r: it && (it.predicted_score_r != null ? it.predicted_score_r : it.y_r_hat),
    "y_τc":
      it &&
      (it["y_τc"] != null
        ? it["y_τc"]
        : it.predicted_score_τc != null
          ? it.predicted_score_τc
          : it.y_r != null
            ? it.y_r
            : it.y_r_hat),
    predicted_score_τc:
      it &&
      (it.predicted_score_τc != null
        ? it.predicted_score_τc
        : it["y_τc"] != null
          ? it["y_τc"]
          : it.predicted_score_r),
    y_spec_τc: (it && it.y_spec_τc) || null,
    y_spec_r: (it && it.y_spec_r) || null,
    formula_terms_r: slimFormulaTerms(
      (it && (it.formula_terms_r || it.score_formula_terms_r)) || null,
      12
    ),
    "y_τ30":
      it &&
      (it["y_τ30"] != null
        ? it["y_τ30"]
        : it.y_t30 != null
          ? it.y_t30
          : it.predicted_score_t30 != null
            ? it.predicted_score_t30
            : it.y_t30_hat),
    y_t30: it && (it.y_t30 != null ? it.y_t30 : it["y_τ30"]),
    predicted_score_t30:
      it &&
      (it.predicted_score_t30 != null
        ? it.predicted_score_t30
        : it.y_t30_hat != null
          ? it.y_t30_hat
          : it["y_τ30"]),
    y_t30_hat: it && (it.y_t30_hat != null ? it.y_t30_hat : it.predicted_score_t30),
    y_t30_realized: it && (it.y_t30_realized != null ? it.y_t30_realized : it.t30_realized),
    t30_realized: it && (it.t30_realized != null ? it.t30_realized : it.y_t30_realized),
    y_spec_τ30: (it && (it.y_spec_τ30 || it.y_spec_t30)) || null,
    y_spec_t30: (it && (it.y_spec_t30 || it.y_spec_τ30)) || null,
    formula_terms_t30: slimFormulaTerms(
      (it && (it.formula_terms_t30 || it.score_formula_terms_t30)) || null,
      12
    ),
    score_formula_terms_t30: slimFormulaTerms(
      (it && (it.score_formula_terms_t30 || it.formula_terms_t30)) || null,
      12
    ),
    "y_τ45":
      it &&
      (it["y_τ45"] != null
        ? it["y_τ45"]
        : it.y_t45 != null
          ? it.y_t45
          : it.predicted_score_t45 != null
            ? it.predicted_score_t45
            : it.y_t45_hat),
    y_t45: it && (it.y_t45 != null ? it.y_t45 : it["y_τ45"]),
    predicted_score_t45:
      it &&
      (it.predicted_score_t45 != null
        ? it.predicted_score_t45
        : it.y_t45_hat != null
          ? it.y_t45_hat
          : it["y_τ45"]),
    y_t45_hat: it && (it.y_t45_hat != null ? it.y_t45_hat : it.predicted_score_t45),
    y_t45_realized: it && (it.y_t45_realized != null ? it.y_t45_realized : it.t45_realized),
    t45_realized: it && (it.t45_realized != null ? it.t45_realized : it.y_t45_realized),
    y_spec_τ45: (it && (it.y_spec_τ45 || it.y_spec_t45)) || null,
    y_spec_t45: (it && (it.y_spec_t45 || it.y_spec_τ45)) || null,
    formula_terms_t45: slimFormulaTerms(
      (it && (it.formula_terms_t45 || it.score_formula_terms_t45)) || null,
      12
    ),
    score_formula_terms_t45: slimFormulaTerms(
      (it && (it.score_formula_terms_t45 || it.formula_terms_t45)) || null,
      12
    ),
    "y_τ60":
      it &&
      (it["y_τ60"] != null
        ? it["y_τ60"]
        : it.y_t60 != null
          ? it.y_t60
          : it.predicted_score_t60 != null
            ? it.predicted_score_t60
            : it.y_t60_hat),
    y_t60: it && (it.y_t60 != null ? it.y_t60 : it["y_τ60"]),
    predicted_score_t60:
      it &&
      (it.predicted_score_t60 != null
        ? it.predicted_score_t60
        : it.y_t60_hat != null
          ? it.y_t60_hat
          : it["y_τ60"]),
    y_t60_hat: it && (it.y_t60_hat != null ? it.y_t60_hat : it.predicted_score_t60),
    y_t60_realized: it && (it.y_t60_realized != null ? it.y_t60_realized : it.t60_realized),
    t60_realized: it && (it.t60_realized != null ? it.t60_realized : it.y_t60_realized),
    y_spec_τ60: (it && (it.y_spec_τ60 || it.y_spec_t60)) || null,
    y_spec_t60: (it && (it.y_spec_t60 || it.y_spec_τ60)) || null,
    formula_terms_t60: slimFormulaTerms(
      (it && (it.formula_terms_t60 || it.score_formula_terms_t60)) || null,
      12
    ),
    score_formula_terms_t60: slimFormulaTerms(
      (it && (it.score_formula_terms_t60 || it.formula_terms_t60)) || null,
      12
    ),
    "y_τ75":
      it &&
      (it["y_τ75"] != null
        ? it["y_τ75"]
        : it.y_t75 != null
          ? it.y_t75
          : it.predicted_score_t75 != null
            ? it.predicted_score_t75
            : it.y_t75_hat),
    y_t75: it && (it.y_t75 != null ? it.y_t75 : it["y_τ75"]),
    predicted_score_t75:
      it &&
      (it.predicted_score_t75 != null
        ? it.predicted_score_t75
        : it.y_t75_hat != null
          ? it.y_t75_hat
          : it["y_τ75"]),
    y_t75_hat: it && (it.y_t75_hat != null ? it.y_t75_hat : it.predicted_score_t75),
    y_t75_realized: it && (it.y_t75_realized != null ? it.y_t75_realized : it.t75_realized),
    t75_realized: it && (it.t75_realized != null ? it.t75_realized : it.y_t75_realized),
    y_spec_τ75: (it && (it.y_spec_τ75 || it.y_spec_t75)) || null,
    y_spec_t75: (it && (it.y_spec_t75 || it.y_spec_τ75)) || null,
    formula_terms_t75: slimFormulaTerms(
      (it && (it.formula_terms_t75 || it.score_formula_terms_t75)) || null,
      12
    ),
    score_formula_terms_t75: slimFormulaTerms(
      (it && (it.score_formula_terms_t75 || it.formula_terms_t75)) || null,
      12
    ),
    "y_τ90":
      it &&
      (it["y_τ90"] != null
        ? it["y_τ90"]
        : it.y_t90 != null
          ? it.y_t90
          : it.predicted_score_t90 != null
            ? it.predicted_score_t90
            : it.y_t90_hat),
    y_t90: it && (it.y_t90 != null ? it.y_t90 : it["y_τ90"]),
    predicted_score_t90:
      it &&
      (it.predicted_score_t90 != null
        ? it.predicted_score_t90
        : it.y_t90_hat != null
          ? it.y_t90_hat
          : it["y_τ90"]),
    y_t90_hat: it && (it.y_t90_hat != null ? it.y_t90_hat : it.predicted_score_t90),
    y_t90_realized: it && (it.y_t90_realized != null ? it.y_t90_realized : it.t90_realized),
    t90_realized: it && (it.t90_realized != null ? it.t90_realized : it.y_t90_realized),
    y_spec_τ90: (it && (it.y_spec_τ90 || it.y_spec_t90)) || null,
    y_spec_t90: (it && (it.y_spec_t90 || it.y_spec_τ90)) || null,
    formula_terms_t90: slimFormulaTerms(
      (it && (it.formula_terms_t90 || it.score_formula_terms_t90)) || null,
      12
    ),
    score_formula_terms_t90: slimFormulaTerms(
      (it && (it.score_formula_terms_t90 || it.formula_terms_t90)) || null,
      12
    ),
    ret_open_to_tau:
      it &&
      (it.features_tau && it.features_tau.ret_open_to_tau != null
        ? it.features_tau.ret_open_to_tau
        : it.ret_open_to_tau),
    r_hat:
      it &&
      (it.r_hat != null
        ? it.r_hat
        : it.remaining_oc != null
          ? it.remaining_oc
          : null),
    remaining_oc:
      it &&
      (it.remaining_oc != null
        ? it.remaining_oc
        : it.r_hat != null
          ? it.r_hat
          : null),
    r_realized:
      it &&
      (it.r_realized != null
        ? it.r_realized
        : it.y_r_realized != null
          ? it.y_r_realized
          : null),
    y_r_realized:
      it &&
      (it.y_r_realized != null
        ? it.y_r_realized
        : it.r_realized != null
          ? it.r_realized
          : null),
    predicted_score_tau:
      it &&
      (it.predicted_score_tau != null
        ? it.predicted_score_tau
        : it.score_rem != null
          ? it.score_rem
          : it.predicted_score_rem),
    gap_pct: gapPct,
    predicted_score_eod: it && it.predicted_score_eod,
    predicted_score_on: it && it.predicted_score_on,
    predicted_score: it && it.predicted_score != null ? it.predicted_score : it && it.score,
    score: it && it.score != null ? it.score : it && it.predicted_score,
    predicted_score_blend: it && it.predicted_score_blend,
    predicted_score_eod_rem: it && it.predicted_score_eod_rem,
    predicted_score_tau_delta: it && it.predicted_score_tau_delta,
    dual_score_window: (it && it.dual_score_window) || null,
    eod_feature_as_of: (it && it.eod_feature_as_of) || null,
    trade_day: (it && it.trade_day) || null,
    open_t: it && it.open_t,
    open_t_source: (it && it.open_t_source) || null,
    factor_anomaly: (it && it.factor_anomaly) || null,
    realized_t1_to_tau: it && it.realized_t1_to_tau,
    score_rem: it && (it.score_rem != null ? it.score_rem : it.predicted_score_rem),
    event_prior: eventPrior,
    as_of_tau: (it && (it.as_of_tau || it.rem_tau)) || null,
    rem_tau: (it && it.rem_tau) || null,
    y_spec_tau: (it && it.y_spec_tau) || null,
    features_tau: (it && it.features_tau) || null,
    factor_coefficients_tau: coefsTau,
    features_co: (it && (it.features_co || it.features_on)) || null,
    y_spec_co: (it && (it.y_spec_co || it.y_spec_on)) || null,
    dual_score_fusion: (it && it.dual_score_fusion) || null,
    dual_score_weights: (it && it.dual_score_weights) || null,
    dual_score_head: (it && it.dual_score_head) || null,
    dual_score_single_head: !!(it && it.dual_score_single_head),
    y_check: (it && it.y_check) || null,
    y_disagree: it && it.y_disagree,
    y_sigma: it && it.y_sigma,
    eod_trust: it && it.eod_trust,
    y_tau_to_close: it && it.y_tau_to_close,
    y_tau_to_close_src: it && it.y_tau_to_close_src,
    y_state: it && it.y_state,
    formula: hasTerms ? "" : (it && it.score_formula) || "",
    reasons: ((it && it.score_reasons) || []).slice(0, 5),
    hard_reject: !!(it && it.hard_reject),
    reject_reason: (it && it.reject_reason) || "",
    weight_source: (it && it.weight_source) || "",
    cluster_label: (it && it.cluster_label) || "",
    cluster_mode: (it && it.cluster_mode) || "",
    cluster_version: it && it.cluster_version,
    score_global: it && it.score_global,
    score_cluster: it && it.score_cluster,
    min_score: it && it.min_score,
    below_min_score: !!(it && it.below_min_score),
    return_model_source: (it && it.return_model_source) || "",
    score_scale: (it && it.score_scale) || "",
    heuristic_score: it && it.heuristic_score,
    factor_coefficients: coefs,
    sentiment_include_in_score: sentInc,
    sentiment_prior: (it && it.sentiment_prior) || null,
    alt_sentiment_beta: it && it.alt_sentiment_beta,
    alt_sentiment_in_yhat: !!(it && it.alt_sentiment_in_yhat),
    risk_hints: ((it && it.risk_hints) || []).slice(0, 3),
    warnings: ((it && it.warnings) || []).slice(0, 3),
    ...marketPriorDetailFields(it),
    ...tailAnomalyDetailFields(it),
    ...overheatDetailFields(it),
  });
}

function slimFormulaTerms(expl, maxTerms = 10) {
  if (!expl || typeof expl !== "object") return expl || null;
  const terms = Array.isArray(expl.terms) ? expl.terms : [];
  if (!terms.length) return expl;
  const pin = new Set([
    "amihud",
    "liquidity",
    "money_flow",
    "volume_price",
    "relative_strength",
    "size",
    "overheat",
    "gap_risk",
    "gap_pct",
    "theme_day",
    "gap_atr",
    "sector_gap_breadth",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
    "tau_lag1",
    "tau_ma5",
    "ret_open_to_tau",
    "tau_elapsed_min",
    "loc_hl",
    "ret_last_15m",
    "sector_ret_to_tau",
  ]);
  const sorted = [...terms].sort(
    (a, b) => Math.abs(Number(b?.contrib) || 0) - Math.abs(Number(a?.contrib) || 0)
  );
  // 在 limit 内用 pin 替换，不追加，避免 data-score-detail 过长截断
  const pinTerms = sorted.filter((t) => pin.has(String(t?.key || "")));
  const nonPin = sorted.filter((t) => !pin.has(String(t?.key || "")));
  const budget = Math.max(0, maxTerms - pinTerms.length);
  const kept = [...nonPin.slice(0, budget), ...pinTerms].sort(
    (a, b) => Math.abs(Number(b?.contrib) || 0) - Math.abs(Number(a?.contrib) || 0)
  );
  const out = {
    intercept: expl.intercept,
    total: expl.total,
    y_tau_raw: expl.y_tau_raw != null ? expl.y_tau_raw : null,
    terms: kept,
    head: expl.head,
    model_role: expl.model_role || null,
    head_kind: expl.head_kind || null,
    p_up: expl.p_up != null ? expl.p_up : null,
    logit: expl.logit != null ? expl.logit : null,
  };
  if (expl.missing_n != null) out.missing_n = expl.missing_n;
  if (Array.isArray(expl.missing_keys) && expl.missing_keys.length) {
    out.missing_keys = expl.missing_keys.slice(0, 8);
  }
  return out;
}

export function truncateText(s, n) {
  const t = String(s || "").trim();
  if (!t) return "";
  if (t.length <= n) return t;
  return `${t.slice(0, Math.max(0, n - 1))}…`;
}

export function sentimentLabelZh(label) {
  const m = {
    bullish: "多",
    bearish: "空",
    mixed: "杂",
    neutral: "中",
  };
  return m[label] || "中";
}

export function sentimentBadgeHtml(sent, code) {
  const s = sent || {};
  const label = String(s.label || "neutral");
  const cls =
    label === "bullish"
      ? "is-bull"
      : label === "bearish"
        ? "is-bear"
        : label === "mixed"
          ? "is-mixed"
          : "is-neutral";
  const hits = []
    .concat(s.hit_pos || [])
    .concat(s.hit_neg || [])
    .slice(0, 6);
  const scorePart =
    s.score != null && !Number.isNaN(Number(s.score))
      ? ` · 风险 ${Number(s.score).toFixed(2)}`
      : "";
  // 仅参考：不进 score/ŷ，不参与调仓
  const gateNote =
    s.role === "prior" || s.include_in_score !== true
      ? "仅参考 · 不参与 predicted_score / 调仓"
      : "遗留开闸进 ŷ（不推荐）";
  const title = [
    s.note || "规则关键词，非模型",
    gateNote,
    hits.length ? `命中：${hits.join("、")}` : "无关键词命中",
    scorePart.trim(),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    `<span class="watching-sent-badge ${cls}" data-code="${escapeHtml(code || "")}" title="${escapeHtml(title)}">` +
    `${sentimentLabelZh(label)}</span>`
  );
}

/**
 * @param {object} plan
 * @param {{
 *   bodyEl: HTMLElement|null,
 *   confirmBtnEl?: HTMLElement|null,
 *   mode: string,
 *   defaultVal: number,
 *   editableCodes: string[],
 *   amountByCode: Record<string, number>,
 *   sharesByCode: Record<string, number>,
 * }} opts
 */
export function renderWatchingBuildPlan(plan, opts) {
  const body = opts.bodyEl;
  const confirmBtn = opts.confirmBtnEl;
  if (!body) return;
  const items = (plan && plan.items) || [];
  const skipped = (plan && plan.skipped) || [];
  const defaultVal = opts.defaultVal;
  const fmtMoney = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  };
  const editableCodes = opts.editableCodes || [];
  const byCode = {};
  items.forEach((it) => {
    byCode[it.stock_code] = it;
  });
  skipped.forEach((s) => {
    if (!byCode[s.stock_code]) {
      byCode[s.stock_code] = {
        stock_code: s.stock_code,
        stock_name: s.stock_name,
        reason: s.reason,
      };
    }
  });
  const mode = opts.mode || "amount";
  const amountByCode = opts.amountByCode || {};
  const sharesByCode = opts.sharesByCode || {};
  const headCols =
    mode === "amount"
      ? `<th>股票</th><th>现价</th><th>金额</th><th>股数</th><th>花费</th>`
      : `<th>股票</th><th>现价</th><th>股数</th><th>花费</th>`;
  const table = editableCodes.length
    ? `<table class="quant-weight-table watching-build-table"><thead><tr>` +
      headCols +
      `</tr></thead><tbody>` +
      editableCodes
        .map((code) => {
          const it = byCode[code] || { stock_code: code };
          let midCells;
          if (mode === "amount") {
            const amt =
              amountByCode[code] != null
                ? amountByCode[code]
                : defaultVal;
            midCells =
              `<td class="num"><input type="number" class="watching-build-row-amount" data-code="${escapeHtml(
                code
              )}" min="100" step="100" value="${escapeHtml(String(amt))}" title="该只买入金额（元）" /></td>` +
              `<td class="num">${it.shares != null ? escapeHtml(String(it.shares)) : "—"}</td>`;
          } else if (mode === "shares") {
            const shares =
              sharesByCode[code] != null
                ? sharesByCode[code]
                : it.shares != null
                  ? it.shares
                  : Math.floor(defaultVal / 100) * 100;
            midCells =
              `<td class="num"><input type="number" class="watching-build-row-shares" data-code="${escapeHtml(
                code
              )}" min="100" step="100" value="${escapeHtml(String(shares))}" title="该只买入股数" /></td>`;
          } else {
            midCells =
              `<td class="num">${it.shares != null ? escapeHtml(String(it.shares)) : "—"}</td>`;
          }
          const amountCell =
            it.amount != null
              ? fmtMoney(it.amount)
              : it.reason
                ? `<span class="watching-build-skip-reason">${escapeHtml(it.reason)}</span>`
                : "—";
          return (
            `<tr data-code="${escapeHtml(code)}"><td class="watching-build-name">` +
            `<span class="watching-name-row">` +
            watchingNameSpanHtml(it.stock_name || code) +
            fitTierBadgeForCode(code) +
            `</span>` +
            `<span class="watching-code-sub">${escapeHtml(code)}</span></td>` +
            `<td class="num">${escapeHtml(String(it.price ?? "—"))}</td>` +
            midCells +
            `<td class="num">${amountCell}</td></tr>`
          );
        })
        .join("") +
      `</tbody></table>`
    : `<p class="watching-table-empty">按当前定量没有可建仓的股票</p>`;
  const costNote =
    plan && plan.cost_model === "zero"
      ? `<div class="watching-build-cost-chip">零成本假设</div>`
      : "";
  const totals =
    costNote +
    `<dl class="watching-build-totals">` +
    `<div><dt>买入</dt><dd>${items.length} 只</dd></div>` +
    `<div><dt>合计花费</dt><dd>${fmtMoney(plan && plan.total_amount)} 元</dd></div>` +
    `<div><dt>可用现金</dt><dd>${fmtMoney(plan && plan.cash)} 元</dd></div>` +
    `<div><dt>建仓后现金</dt><dd>${fmtMoney(plan && plan.cash_after)} 元</dd></div>` +
    `</dl>`;
  body.innerHTML = table + totals;
  if (confirmBtn) confirmBtn.disabled = !items.length;
}

/** CDN/React 不可用时的原生表回退 */
export function renderWatchingWatchTableFallback(rows, { onPickCountUpdate } = {}) {
  const watchTable = document.getElementById("watching-watchlist-table");
  if (!watchTable) return;
  const body = (rows || [])
    .map((d) => {
      const code = escapeHtml(d.code || "");
      const name = d.name || d.code || "—";
      const alertCls = d.isSentimentAlert ? " is-sentiment-alert" : "";
      const oosBadge = d.oosFailed
        ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
        : "";
      return (
        `<tr data-code="${code}" class="watching-watch-row${alertCls}${
          d.oosFailed ? " is-oos-failed" : ""
        }">` +
        `<td class="watching-pick-cell"><input type="checkbox" class="watching-pick" value="${code}" data-code="${code}" /></td>` +
        `<td class="watching-stock" title="${escapeHtml(name)} ${code}">` +
        `<span class="watching-name-row">` +
        watchingNameSpanHtml(name) +
        fitTierBadgeForCode(d.code) +
        oosBadge +
        `</span>` +
        `<span class="watching-code-sub">${code}<span class="watching-mkt"></span></span></td>` +
        `<td class="watching-paper-cell">` +
        (d.onPaper
          ? `<button type="button" class="watching-held-btn" data-code="${code}">${escapeHtml(d.paper || "已持")}</button>`
          : `<button type="button" class="watching-build-btn" data-code="${code}">建仓</button>`) +
        `</td>` +
        `<td class="watching-sent-cell">${d.sentHtml || "—"}</td>` +
        `<td class="num watching-col-num" data-q="prev_close">${escapeHtml(String(d.prev_close ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="open">${escapeHtml(String(d.open ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="price">${escapeHtml(String(d.price ?? "—"))}</td>` +
        `<td class="num watching-col-num watching-chg${d.chgCls ? " " + escapeHtml(d.chgCls) : ""}" data-q="chg">${escapeHtml(String(d.chg ?? "—"))}</td>` +
        `<td class="num watching-col-num watching-score-cell watching-score-eod paper-hold-score has-tip ${escapeHtml(
          d.scoreEodCls || ""
        )}" data-q="score_eod" data-score-tip="eod" data-score-detail="${escapeHtml(
          d.scoreDetail || ""
        )}" title="${escapeHtml(
          d.scoreEodTitle || "ŷ_oo"
        )}">${escapeHtml(String(d.scoreEod ?? "—"))}</td>` +
        `<td class="num watching-col-num watching-score-cell watching-score-tau paper-hold-score has-tip ${escapeHtml(
          d.scoreTauCls || ""
        )}" data-q="score_tau" data-score-tip="tau" data-score-detail="${escapeHtml(
          d.scoreDetail || ""
        )}" title="${escapeHtml(
          d.scoreTauTitle || "ŷ_oc"
        )}">${escapeHtml(String(d.scoreTau ?? "—"))}</td>` +
        `<td class="num watching-col-num watching-score-cell watching-score-on paper-hold-score has-tip ${escapeHtml(
          d.scoreOnCls || ""
        )}" data-q="score_on" data-score-tip="on" data-score-detail="${escapeHtml(
          d.scoreDetail || ""
        )}" title="${escapeHtml(
          d.scoreOnTitle || "ŷ_co"
        )}">${escapeHtml(String(d.scoreOn ?? "—"))}</td>` +
        (() => {
          const singleHead = !!d.scoreSingleHead;
          const head = d.dualScoreHead || "";
          const headTitle =
            head === "single_tau"
              ? "ranking 单头降级：仅 ŷ_oc（缺 ŷ_oo）· 与双头票不同量纲"
              : head === "single_oo"
                ? "ranking 单头降级：仅 ŷ_oo（缺 ŷ_oc）· 与双头票不同量纲"
                : "ranking 单头降级 · 与双头票不同量纲";
          const badge = singleHead
            ? `<span class="watching-single-head-badge" title="${escapeHtml(
                headTitle
              )}">单</span>`
            : "";
          return (
            `<td class="num watching-col-num watching-score-cell paper-hold-score has-tip ${escapeHtml(
              d.scoreCls || ""
            )}${singleHead ? " score-single-head" : ""}" data-q="score" data-score-tip="ranking" data-score-detail="${escapeHtml(
              d.scoreDetail || ""
            )}" title="${escapeHtml(d.scoreTitle || "ranking")}">${escapeHtml(
              String(d.score ?? "—")
            )}${badge}</td>`
          );
        })() +
        `<td class="num watching-col-num" data-q="excess">${escapeHtml(String(d.excess ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="vol">${escapeHtml(String(d.vol ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="volr">${escapeHtml(String(d.volr ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="pe">${escapeHtml(String(d.pe ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="pb">${escapeHtml(String(d.pb ?? "—"))}</td>` +
        `</tr>`
      );
    })
    .join("");
  watchTable.innerHTML =
    `<div class="watching-table-scroll"><table class="quant-weight-table watching-result-table"><thead><tr>` +
    `<th class="watching-pick-cell" title="勾选后可加入模拟持仓"><input type="checkbox" id="watching-select-all" /></th>` +
    `<th title="股票名称与代码">股票</th>` +
    `<th title="是否已在模拟持仓">仓位</th>` +
    `<th title="标题情绪摘要">情绪</th>` +
    `<th class="watching-col-num" title="上一交易日收盘价">昨收</th>` +
    `<th class="watching-col-num" title="今日开盘价">今开</th>` +
    `<th class="watching-col-num" title="最新成交价">现价</th>` +
    `<th class="watching-col-num" title="相对昨收的涨跌幅（与 Y 列同一口径）">涨跌</th>` +
    `<th class="watching-col-num" title="ŷ_oo · open[T]→open[T+1]">y_oo</th>` +
    `<th class="watching-col-num" title="${Y_OC_REBALANCE_TITLE}">y_τc</th>` +
    `<th class="watching-col-num" title="ŷ_co · close[T]→open[T+1]">y_co</th>` +
    `<th class="watching-col-num" title="${RANKING_REBALANCE_TITLE}">ranking</th>` +
    `<th class="watching-col-num" title="相对基准（指数）的超额收益">超额</th>` +
    `<th class="watching-col-num" title="成交量">量</th>` +
    `<th class="watching-col-num" title="近期成交量 / 均量">量比</th>` +
    `<th class="watching-col-num" title="市盈率">PE</th>` +
    `<th class="watching-col-num" title="市净率">PB</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>`;
  if (typeof onPickCountUpdate === "function") onPickCountUpdate();
}

/**
 * 观察池虚拟表初始行。
 * @param {string[]} wl
 * @param {string[]} names
 * @param {Map|Iterable} paperCodes
 * @param {Record<string, number>|null|undefined} scores
 * @param {{
 *   fmtScore: (n: number|null) => string,
 *   scoreCls: (n: number|null) => string,
 *   escapeHtml: (s: string) => string,
 *   alertCodes: Set<string>,
 *   bookCodes?: Set<string>,
 * }} deps
 */
export function buildWatchingWatchRows(wl, names, paperCodes, scores, deps) {
  const { fmtScore, scoreCls, escapeHtml, alertCodes, bookCodes } = deps;
  const inPaper =
    paperCodes instanceof Map
      ? paperCodes
      : new Map(Array.from(paperCodes || []).map((c) => [String(c), null]));
  const inBook = bookCodes instanceof Set ? bookCodes : new Set(bookCodes || []);
  const rowByCode = new Map();
  for (let i = 0; i < (wl || []).length; i++) {
    const code = String(wl[i] || "").trim();
    if (!code) continue;
    const name = (names[i] && String(names[i]).trim()) || "—";
    const scoreRaw = scores && scores[code];
    const rawN =
      scoreRaw == null || Number.isNaN(Number(scoreRaw)) ? null : Number(scoreRaw);
    const scoreNum = rawN != null && Math.abs(rawN) <= 20 ? rawN : null;
    const onPaper = inPaper.has(code);
    const heldShares = onPaper ? inPaper.get(code) : null;
    rowByCode.set(code, {
      code,
      name,
      picked: false,
      market: "",
      paper: onPaper ? (heldShares != null ? `${heldShares} 股` : "已持") : "建仓",
      onPaper,
      inBook: inBook.has(code),
      oosFailed: false,
      sentHtml: `<span class="watching-sent-badge is-neutral" data-code="${escapeHtml(
        code
      )}" title="加载中">…</span>`,
      price: "—",
      prev_close: "—",
      open: "—",
      openNum: null,
      chg: "—",
      chgCls: "",
      chgNum: null,
      score: scoreNum == null ? "…" : fmtTableScore(null, scoreNum),
      scoreNum,
      scoreCls: scoreCls(scoreNum),
      scoreEod: "…",
      scoreEodNum: null,
      scoreEodCls: "",
      scoreEodTitle: "暂无 ŷ_oo",
      scoreTau: "…",
      scoreTauNum: null,
      scoreTauCls: "",
      scoreTauTitle: "暂无 ŷ_oc",
      scoreOn: "…",
      scoreOnNum: null,
      scoreOnCls: "",
      scoreOnTitle: "暂无 ŷ_co",
      stance: "…",
      excess: "…",
      excessNum: null,
      vol: "…",
      volNum: null,
      volr: "…",
      pe: "…",
      pb: "…",
      isHardReject: false,
      isSentimentAlert: alertCodes.has(code),
    });
  }
  return Array.from(rowByCode.values());
}

export function buildWatchingNewsTitleHtml(name, code, sent, sentimentBadgeHtmlFn) {
  return (
    `资讯 · ${escapeHtml(name)}` +
    ` <span class="watching-code-sub">${escapeHtml(code)}</span> ` +
    sentimentBadgeHtmlFn(sent, code)
  );
}

export function buildWatchingNewsMetaText(data, sent) {
  const hitBits = []
    .concat(sent.hit_pos || [])
    .concat(sent.hit_neg || []);
  const n = (data.items || []).length;
  return [
    n ? `${n} 条` : data.error || "暂无资讯",
    sent.note || "规则关键词，非模型",
    hitBits.length ? `命中 ${hitBits.join("、")}` : "",
    data.updated_at ? `更新 ${data.updated_at}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function buildWatchingNewsListHtml(items, data) {
  if (!items.length) {
    return `<li class="watching-news-empty">${escapeHtml(
      data.error || "暂无资讯"
    )}</li>`;
  }
  return items
    .map((it) => {
      const t = escapeHtml(it.title || "");
      const meta = [it.time, it.source].filter(Boolean).join(" · ");
      const url = String(it.url || "").trim();
      const body = url
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${t}</a>`
        : t;
      return (
        `<li>` +
        `<div class="watching-news-title">${body}</div>` +
        (meta ? `<div class="watching-news-meta">${escapeHtml(meta)}</div>` : "") +
        `</li>`
      );
    })
    .join("");
}

export const WATCHING_NEWS_AI_LOADING_HTML = `
  <div class="watching-news-ai-loading">
    <div class="watching-news-ai-spinner"></div>
    <p>正在分析舆情数据…</p>
  </div>
`;

export function buildWatchingNewsAiAnalysisHtml(analysis, escapeHtml) {
  const esc = escapeHtml;
  let analysisHtml;
  if (typeof marked !== "undefined") {
    analysisHtml = marked.parse(analysis, { gfm: true, breaks: true });
  } else {
    analysisHtml = esc(analysis).replace(/\n/g, "<br/>");
  }
  return `<div class="watching-news-ai-text">${analysisHtml}</div>`;
}

export function buildWatchingNewsAiErrorHtml(message, escapeHtml) {
  return `<div class="watching-news-ai-error">${escapeHtml(message)}</div>`;
}
