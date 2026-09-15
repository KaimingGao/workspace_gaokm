/** 评分悬浮注释（交易执行持仓 / 数据中心观察表共用）。
 * 主叙事：收益分 ŷ + 因子系数 β（可正可负），非规则权重。
 */

import {
  escapeText,
  fusionWeightsFromItem,
  fusionWCoFromItem,
  liftTauVsPrevClose,
  nowcastOcPct,
  ocWithCoPct,
  pickYOcFitted,
  resolveEodScore,
  resolveGapPct,
  resolveNowcastScore,
  resolveNowcastCcScore,
  resolveOnScore,
  resolveTauLiftedScore,
  resolveTauScore,
  resolveRankingScore,
  resolveYτcScore,
  resolveYT30Score,
  resolveYT60Score,
  resolveYT90Score,
  blendYtw,
  yTwSign,
  fmtYtwVote,
  resolvePathScore,
  compoundPct,
  Y_NC_TITLE,
  Y_NC_OC_TITLE,
  Y_HL_TITLE,
} from "./paper/fmt.js?v=p2426";
import { hydrateTailAnomalyCharts } from "./tail_anomaly_chart.js";
import { ON_FEAT_META } from "./quant/factor_meta.js?v=p1226";

const FACTOR_LABELS = {
  momentum: "动量",
  volume_price: "量价",
  relative_strength: "相对强弱",
  volatility: "波动",
  reversal: "反转",
  liquidity: "流动性",
  value: "估值",
  quality: "质量",
  ma_slope: "均线斜率",
  technical_pattern: "技术形态",
  weekly_confirm: "周线确认",
  gap_risk: "跳空风险",
  overheat: "短期过热",
  size: "规模",
  earnings_yield: "盈利收益率",
  growth: "成长",
  dividend: "股息",
  money_flow: "资金流",
  amihud: "非流动性",
  idio_momentum: "特异动量",
  alt_sentiment: "舆情",
  tail_anomaly: "尾盘异常",
};

function fmtSigned(v, digits = 3) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const t = n.toFixed(digits);
  return n > 0 ? `+${t}` : t;
}

function signCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}

function resolveYhat(raw) {
  if (
    raw &&
    (raw.score_scale === "heuristic_0_100" ||
      raw.return_model_source === "oos_failed_heuristic")
  ) {
    return null;
  }
  const candidates = [raw && raw.predicted_score, raw && raw.predicted_score_eod];
  if (raw && raw.formula_terms && raw.formula_terms.total != null) {
    candidates.unshift(raw.formula_terms.total);
  }
  for (const c of candidates) {
    // null/"" 不能走 Number()：Number(null)===0，会把「未打分」显示成 0.000%
    if (c == null || c === "") continue;
    const n = Number(c);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function resolveTau(raw) {
  const candidates = [
    raw && raw.predicted_score_tau,
    raw && raw.score_rem,
    raw && raw.predicted_score_rem,
  ];
  for (const c of candidates) {
    if (c == null || c === "") continue;
    const n = Number(c);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

const TAU_FEAT_LABELS = {
  gap_pct: "跳空 %",
  open_gap: "开盘缺口",
  sector_gap_breadth: "同业缺口广度",
  theme_day: "主题日",
  gap_atr: "缺口 / ATR",
  gap_vs_sector: "行业相对缺口",
  yclose_loc: "昨收位置",
  mom3_pct: "近3日动量 %",
  tau_lag1: "昨真实开→收 %",
  tau_ma5: "近5日真实开→收均 %",
  ret_open_to_tau: "开盘→τ 收益 %",
  ret_prev_to_tau: "昨收→τ 收益 %",
  range_pct: "前缀振幅 %",
  loc_hl: "HL 位置 0–1",
  up_extent: "相对开盘上探 %",
  down_extent: "相对开盘下探 %",
  path_sign: "路径符号(+先低后高)",
  pullback_from_high: "自高回撤 %",
  bounce_from_low: "自低反弹 %",
  ret_last_15m: "近15m 收益 %",
  ret_last_5m: "近5m 收益 %",
  ret_last_30m: "近30交易分钟收益 %",
  session_elapsed: "已过交易分钟（09:30=0）",
  session_remain: "距收盘剩余交易分钟",
  crosses_lunch: "未来30m是否跨午休",
  session_vwap_dev: "τ价相对会话VWAP %",
  vol_last_30m_vs_avg: "近30m量/前缀均量",
  sector_ret_last_30m: "板块中位近30m %",
  ret_last_30m_vs_sector: "近30m相对板块 %",
  t30_lag1: "昨同钟真实 τ⊕30m %",
  t30_ma5: "近5日同钟真实 τ⊕30m 均 %",
  ret_last_60m: "近60交易分钟收益 %",
  crosses_lunch_60: "未来60m是否跨午休",
  vol_last_60m_vs_avg: "近60m量/前缀均量",
  sector_ret_last_60m: "板块中位近60m %",
  ret_last_60m_vs_sector: "近60m相对板块 %",
  t60_lag1: "昨同钟真实 τ⊕60m %",
  t60_ma5: "近5日同钟真实 τ⊕60m 均 %",
  ret_last_90m: "近90交易分钟收益 %",
  crosses_lunch_90: "未来90m是否跨午休",
  vol_last_90m_vs_avg: "近90m量/前缀均量",
  sector_ret_last_90m: "板块中位近90m %",
  ret_last_90m_vs_sector: "近90m相对板块 %",
  t90_lag1: "昨同钟真实 τ⊕90m %",
  t90_ma5: "近5日同钟真实 τ⊕90m 均 %",
  realized_vol: "前缀已实现波动 %",
  vol_last3_vs_avg: "近3根量/均量",
  tau_elapsed_min: "τ距开盘分钟",
  sector_ret_to_tau: "板块中位开→τ %",
  ret_vs_sector: "开→τ 相对板块 %",
  t_hi_frac: "最高点相对前缀进度",
  t_lo_frac: "最低点相对前缀进度",
};

/** ŷ_oo：表列 score · 含因子组成。 */
export function formatScoreHero(raw) {
  const heuristic =
    raw &&
    (raw.score_scale === "heuristic_0_100" ||
      raw.return_model_source === "oos_failed_heuristic");
  if (heuristic) {
    const hs =
      raw.heuristic_score != null && raw.heuristic_score !== ""
        ? Number(raw.heuristic_score)
        : raw.score != null && raw.score !== ""
          ? Number(raw.score)
          : null;
    const hsTxt =
      hs == null || !Number.isFinite(hs) ? "—" : fmtSigned(hs, 1);
    const cluster =
      raw.score_cluster != null && Number.isFinite(Number(raw.score_cluster))
        ? `${fmtSigned(Number(raw.score_cluster), 3)}%`
        : "—";
    return (
      `<div class="score-layer score-layer-eod">` +
      `<div class="score-layer-head">` +
      `<div class="score-hero-label">ŷ_oo · 隔夜主轴</div>` +
      `<div class="score-hero-value">—</div>` +
      `</div>` +
      `<div class="score-hero-hint">OOS 失败组 · 主分=启发式 ${escapeText(
        hsTxt
      )}（0–100）· 组 ŷ 对照 ${escapeText(cluster)} · 不当作收益%</div>` +
      `</div>`
    );
  }
  const y = resolveYhat(raw);
  const yTxt = y == null ? "—" : `${fmtSigned(y, 3)}%`;
  const below = !!raw.below_min_score;
  const floor =
    raw && raw.min_score != null && Number.isFinite(Number(raw.min_score))
      ? Number(raw.min_score)
      : null;
  let gate = "";
  if (floor != null) {
    gate = `<div class="score-hero-gate${below ? " is-warn" : ""}">买入门槛 ŷ_oo ≥ ${escapeText(
      Number.isFinite(floor) ? `${floor}%` : String(floor)
    )}${below ? " · 当前低于门槛" : ""}</div>`;
  }
  return (
    `<div class="score-layer score-layer-eod">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_oo · 隔夜主轴</div>` +
    `<div class="score-hero-value ${signCls(y)}">${escapeText(yTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">ŷ = α + Σ β·z（百分点）· T−1 因子 · 买入门槛` +
    (raw &&
    raw.heuristic_score != null &&
    Number.isFinite(Number(raw.heuristic_score))
      ? ` · heuristic对照 ${escapeText(fmtSigned(Number(raw.heuristic_score), 1))}`
      : "") +
    `</div>` +
    gate +
    `</div>`
  );
}

function termFeatLabel(t, key) {
  const k = t && t.key;
  if (key === "on" && k && ON_FEAT_META[k] && ON_FEAT_META[k].label) {
    return ON_FEAT_META[k].label;
  }
  if ((key === "tau" || key === "r" || key === "t30" || key === "t60" || key === "t90" || key === "path" || key === "hl") && k && TAU_FEAT_LABELS[k]) {
    return TAU_FEAT_LABELS[k];
  }
  if (t && t.label && String(t.label) !== String(k || "")) {
    return t.label;
  }
  return (
    FACTOR_LABELS[k] ||
    TAU_FEAT_LABELS[k] ||
    (t && t.label) ||
    k ||
    "—"
  );
}

/** y_eod 列：头值 + 因子表（不含 trade 全栈）。 */
function formatCompactEodTip(raw) {
  const y = resolveEodScore(raw);
  const val = y == null ? "—" : `${fmtSigned(y, 2)}%`;
  const below = !!raw.below_min_score;
  const floor =
    raw && raw.min_score != null && Number.isFinite(Number(raw.min_score))
      ? Number(raw.min_score)
      : null;
  let gate = "";
  if (floor != null) {
    gate = `<div class="score-hero-gate${below ? " is-warn" : ""}">买入门槛 ŷ_oo ≥ ${escapeText(
      Number.isFinite(floor) ? `${floor}%` : String(floor)
    )}${below ? " · 当前低于门槛" : ""}</div>`;
  }
  return (
    `<div class="score-layer score-layer-eod">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">y_oo</div>` +
    `<div class="score-hero-value ${signCls(y)}">${escapeText(val)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">ŷ_oo · T−1 因子 · α+Σβ·z</div>` +
    gate +
    `</div>`
  );
}

function pickTauToPct(raw) {
  if (!raw || typeof raw !== "object") return null;
  for (const c of [
    raw.y_to,
    raw.y_pc,
    raw["y_τc"],
    raw.predicted_score_to,
    raw.predicted_score_r,
    raw.y_r,
    raw.y_r_hat,
  ]) {
    if (c == null || c === "") continue;
    const n = Number(c);
    if (Number.isFinite(n) && Math.abs(n) <= 20) return n;
  }
  return null;
}

function remainingAtTauPct(yOc, rot) {
  const y = Number(yOc);
  const r = Number(rot);
  if (!Number.isFinite(y) || !Number.isFinite(r)) return null;
  const d = 1 + r / 100;
  if (Math.abs(d) < 1e-12) return null;
  const out = ((1 + y / 100) / d - 1) * 100;
  return Number.isFinite(out) ? out : null;
}

function hasTauFormulaTerms(raw) {
  return !!(
    raw &&
    ((raw.formula_terms_tau && (raw.formula_terms_tau.terms || []).length) ||
      (raw.score_formula_terms_tau && (raw.score_formula_terms_tau.terms || []).length))
  );
}

/** y_τ 列 tip：头值=拟合原值（=组成合计=表列）；昨收口径仅作对照。 */
function formatCompactTauTip(raw) {
  const fit = resolveTauScore(raw);
  const lifted = resolveTauLiftedScore(raw);
  const fitTxt = fit == null ? "—" : `${fmtSigned(fit, 3)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const hasTerms = hasTauFormulaTerms(raw);
  const feat = (raw && raw.features_tau) || {};
  const rot =
    feat.ret_open_to_tau != null && Number.isFinite(Number(feat.ret_open_to_tau))
      ? Number(feat.ret_open_to_tau)
      : raw && raw.ret_open_to_tau != null && Number.isFinite(Number(raw.ret_open_to_tau))
        ? Number(raw.ret_open_to_tau)
        : null;
  let yTo = pickTauToPct(raw);
  if (yTo == null && fit != null && rot != null) yTo = remainingAtTauPct(fit, rot);
  const restored = yTo != null && rot != null ? compoundPct(rot, yTo) : null;
  const rows = [];
  if (
    lifted != null &&
    fit != null &&
    Number.isFinite(lifted) &&
    Math.abs(lifted - fit) > 1e-4
  ) {
    rows.push(
      `<div class="score-layer-row"><span>昨收对照（缺口∘ŷ_oc）</span>` +
        `<span class="num ${signCls(lifted)}">${escapeText(
          fmtSigned(lifted, 3)
        )}%</span></div>`
    );
  }
  const gap = raw && resolveGapPct(raw);
  if (gap != null && Number.isFinite(Number(gap))) {
    rows.push(
      `<div class="score-layer-row"><span>跳空缺口</span>` +
        `<span class="num ${signCls(gap)}">${escapeText(
          fmtSigned(Number(gap), 2)
        )}%</span></div>`
    );
  }
  if (!hasTerms && (yTo != null || rot != null)) {
    if (yTo != null) {
      rows.push(
        `<div class="score-layer-row"><span>ŷ_τc</span>` +
          `<span class="num ${signCls(yTo)}">${escapeText(fmtSigned(yTo, 3))}%</span></div>`
      );
    }
    if (rot != null) {
      rows.push(
        `<div class="score-layer-row"><span>开盘→τ 收益 %</span>` +
          `<span class="num ${signCls(rot)}">${escapeText(fmtSigned(rot, 3))}%</span></div>`
      );
    }
    if (
      restored != null &&
      fit != null &&
      Number.isFinite(restored) &&
      Math.abs(restored - Number(fit)) > 1e-3
    ) {
      rows.push(
        `<div class="score-layer-row"><span>还原 ŷ_oc</span>` +
          `<span class="num ${signCls(restored)}">${escapeText(
            fmtSigned(restored, 3)
          )}%</span></div>`
      );
    }
  }
  if (!hasTerms) {
    for (const [k, label] of Object.entries(TAU_FEAT_LABELS)) {
      if (k === "gap_pct" || k === "ret_open_to_tau") continue;
      const v = feat[k];
      if (v == null || v === "") continue;
      if (typeof v === "boolean") {
        rows.push(
          `<div class="score-layer-row"><span>${escapeText(label)}</span><span>${
            v ? "是" : "否"
          }</span></div>`
        );
        continue;
      }
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const pct =
        k === "open_gap" ||
        k === "gap_vs_sector" ||
        k === "ret_prev_to_tau" ||
        k === "range_pct" ||
        k === "up_extent" ||
        k === "down_extent" ||
        k === "pullback_from_high" ||
        k === "bounce_from_low";
      rows.push(
        `<div class="score-layer-row"><span>${escapeText(label)}</span><span class="num ${
          Number.isFinite(n) ? signCls(n) : ""
        }">${escapeText(txt)}${Number.isFinite(n) && pct ? "%" : ""}</span></div>`
      );
    }
  }
  const hint = hasTerms
    ? `τ=${escapeText(tau)} · 与表列 / 组成合计同口径`
    : yTo != null || rot != null
      ? `τ=${escapeText(tau)} · 本槽未跑 OC Ridge · ŷ_oc=(1+开盘→τ)(1+ŷ_τc)−1`
      : `τ=${escapeText(tau)} · 与表列同口径`;
  return (
    `<div class="score-layer score-layer-tau">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_oc · T收/T开（拟合）</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}

function hasRFormulaTerms(raw) {
  return !!(
    raw &&
    ((raw.formula_terms_r && (raw.formula_terms_r.terms || []).length) ||
      (raw.score_formula_terms_r && (raw.score_formula_terms_r.terms || []).length))
  );
}

function hasT30FormulaTerms(raw) {
  return !!(
    raw &&
    ((raw.formula_terms_t30 && (raw.formula_terms_t30.terms || []).length) ||
      (raw.score_formula_terms_t30 && (raw.score_formula_terms_t30.terms || []).length))
  );
}

function hasPathFormulaTerms(raw) {
  return !!(
    raw &&
    ((raw.formula_terms_path && (raw.formula_terms_path.terms || []).length) ||
      (raw.score_formula_terms_path && (raw.score_formula_terms_path.terms || []).length))
  );
}

function hasT60FormulaTerms(raw) {
  return !!(
    raw &&
    ((raw.formula_terms_t60 && (raw.formula_terms_t60.terms || []).length) ||
      (raw.score_formula_terms_t60 && (raw.score_formula_terms_t60.terms || []).length))
  );
}
function hasT90FormulaTerms(raw) {
  return !!(
    raw &&
    ((raw.formula_terms_t90 && (raw.formula_terms_t90.terms || []).length) ||
      (raw.score_formula_terms_t90 && (raw.score_formula_terms_t90.terms || []).length))
  );
}

/** y_τc 列 tip：表列=Ridge 模型预估 price→close；remaining(ŷ_oc) 仅未 clip 对照。 */
function formatCompactRTip(raw) {
  const fit = resolveYτcScore(raw);
  const fitTxt = fit == null ? "—" : `${fmtSigned(fit, 3)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const hasTerms = hasRFormulaTerms(raw);
  const feat = (raw && raw.features_tau) || {};
  const rot =
    feat.ret_open_to_tau != null && Number.isFinite(Number(feat.ret_open_to_tau))
      ? Number(feat.ret_open_to_tau)
      : raw && raw.ret_open_to_tau != null && Number.isFinite(Number(raw.ret_open_to_tau))
        ? Number(raw.ret_open_to_tau)
        : null;
  const yOc = resolveTauScore(raw);
  const remOc =
    raw && raw.remaining_oc != null && Number.isFinite(Number(raw.remaining_oc))
      ? Number(raw.remaining_oc)
      : yOc != null && rot != null
        ? remainingAtTauPct(yOc, rot)
        : null;
  const ridgeRaw = raw && (raw.y_τc_ridge ?? raw.y_r ?? raw.y_r_hat ?? raw.predicted_score_r);
  const ridge =
    ridgeRaw != null && Number.isFinite(Number(ridgeRaw)) ? Number(ridgeRaw) : null;
  const ySpec =
    (raw && raw.y_spec_τc && raw.y_spec_τc.formula) ||
    (raw && raw.y_spec_r && raw.y_spec_r.formula) ||
    "close[T]/price(τ)−1";
  const rows = [];
  if (remOc != null && Number.isFinite(remOc) && (fit == null || Math.abs(remOc - fit) > 1e-3)) {
    rows.push(
      `<div class="score-layer-row"><span>remaining(ŷ_oc) 未clip</span>` +
        `<span class="num ${signCls(remOc)}">${escapeText(
          fmtSigned(remOc, 3)
        )}%</span></div>`
    );
  } else if (
    ridge != null &&
    fit != null &&
    Number.isFinite(ridge) &&
    Math.abs(ridge - fit) > 1e-3
  ) {
    rows.push(
      `<div class="score-layer-row"><span>Ridge 对照</span>` +
        `<span class="num ${signCls(ridge)}">${escapeText(
          fmtSigned(ridge, 3)
        )}%</span></div>`
    );
  }
  const gap = raw && resolveGapPct(raw);
  if (gap != null && Number.isFinite(Number(gap))) {
    rows.push(
      `<div class="score-layer-row"><span>跳空缺口</span>` +
        `<span class="num ${signCls(gap)}">${escapeText(
          fmtSigned(Number(gap), 2)
        )}%</span></div>`
    );
  }
  if (rot != null) {
    rows.push(
      `<div class="score-layer-row"><span>开盘→τ 收益 %</span>` +
        `<span class="num ${signCls(rot)}">${escapeText(fmtSigned(rot, 3))}%</span></div>`
    );
  }
  if (!hasTerms) {
    for (const [k, label] of Object.entries(TAU_FEAT_LABELS)) {
      if (k === "gap_pct" || k === "ret_open_to_tau") continue;
      const v = feat[k];
      if (v == null || v === "") continue;
      if (typeof v === "boolean") {
        rows.push(
          `<div class="score-layer-row"><span>${escapeText(label)}</span><span>${
            v ? "是" : "否"
          }</span></div>`
        );
        continue;
      }
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const pct =
        k === "open_gap" ||
        k === "gap_vs_sector" ||
        k === "ret_prev_to_tau" ||
        k === "range_pct" ||
        k === "up_extent" ||
        k === "down_extent" ||
        k === "pullback_from_high" ||
        k === "bounce_from_low";
      rows.push(
        `<div class="score-layer-row"><span>${escapeText(label)}</span><span class="num ${
          Number.isFinite(n) ? signCls(n) : ""
        }">${escapeText(txt)}${Number.isFinite(n) && pct ? "%" : ""}</span></div>`
      );
    }
  }
  const hint = hasTerms
    ? `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · Ridge 模型预估 price→close`
    : `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · 与 ŷ_oc 同因子 · 模型预估`;
  return (
    `<div class="score-layer score-layer-r">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_τc · Ridge</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}

/** y_τ30 列 tip：Ridge 预估 price(τ⊕30m)/price(τ)−1。 */
function formatCompactT30Tip(raw) {
  const fit = resolveYT30Score(raw);
  const fitTxt = fit == null ? "—" : `${fmtSigned(fit, 3)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const hasTerms = hasT30FormulaTerms(raw);
  const feat = (raw && raw.features_tau) || {};
  const realRaw = raw && (raw.y_t30_realized ?? raw.t30_realized);
  const real =
    realRaw != null && Number.isFinite(Number(realRaw)) ? Number(realRaw) : null;
  const ySpec =
    (raw && raw.y_spec_τ30 && raw.y_spec_τ30.formula) ||
    (raw && raw.y_spec_t30 && raw.y_spec_t30.formula) ||
    "price[τ+30m]/price[τ]−1";
  const rows = [];
  if (real != null) {
    const agree =
      fit != null && Math.abs(fit) > 1e-12 && real != null && Math.abs(real) > 1e-12
        ? fit * real > 0
          ? "同号"
          : "异号"
        : "";
    rows.push(
      `<div class="score-layer-row"><span>真实 τ⊕30m</span>` +
        `<span class="num ${signCls(real)}">${escapeText(fmtSigned(real, 3))}%${
          agree ? ` · ${agree}` : ""
        }</span></div>`
    );
  }
  const gap = raw && resolveGapPct(raw);
  if (gap != null && Number.isFinite(Number(gap))) {
    rows.push(
      `<div class="score-layer-row"><span>跳空缺口</span>` +
        `<span class="num ${signCls(gap)}">${escapeText(
          fmtSigned(Number(gap), 2)
        )}%</span></div>`
    );
  }
  if (!hasTerms) {
    for (const [k, label] of Object.entries(TAU_FEAT_LABELS)) {
      if (k === "gap_pct") continue;
      const v = feat[k];
      if (v == null || v === "") continue;
      if (typeof v === "boolean") {
        rows.push(
          `<div class="score-layer-row"><span>${escapeText(label)}</span><span>${
            v ? "是" : "否"
          }</span></div>`
        );
        continue;
      }
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const pct =
        k === "open_gap" ||
        k === "gap_vs_sector" ||
        k === "ret_open_to_tau" ||
        k === "ret_last_5m" ||
        k === "ret_last_30m" ||
        k === "ret_last_15m" ||
        k === "session_vwap_dev" ||
        k === "sector_ret_last_30m" ||
        k === "ret_last_30m_vs_sector" ||
        k === "t30_lag1" ||
        k === "t30_ma5" ||
        k === "ret_last_60m" ||
        k === "sector_ret_last_60m" ||
        k === "ret_last_60m_vs_sector" ||
        k === "t60_lag1" ||
        k === "t60_ma5" ||
        k === "ret_last_90m" ||
        k === "sector_ret_last_90m" ||
        k === "ret_last_90m_vs_sector" ||
        k === "t90_lag1" ||
        k === "t90_ma5";
      rows.push(
        `<div class="score-layer-row"><span>${escapeText(label)}</span><span class="num ${
          Number.isFinite(n) ? signCls(n) : ""
        }">${escapeText(txt)}${Number.isFinite(n) && pct ? "%" : ""}</span></div>`
      );
    }
  }
  const hint = hasTerms
    ? `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · Ridge 模型预估 · 做 T 旁路`
    : `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · 做 T 旁路，不进 C_τ`;
  return (
    `<div class="score-layer score-layer-t30">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_τ30 · Ridge</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}

function formatCompactHlTip(raw) {
  const fit = resolvePathScore(raw);
  const fitTxt = fit == null ? "—" : `${fmtSigned(fit, 3)}%`;
  const hasTerms = hasPathFormulaTerms(raw);
  const realRaw = raw && (raw.path_realized ?? raw.y_path_realized ?? raw.hl_realized);
  const real =
    realRaw != null && Number.isFinite(Number(realRaw)) ? Number(realRaw) : null;
  const rows = [];
  if (real != null) {
    const agree =
      fit != null && Math.abs(fit) > 1e-12 && Math.abs(real) > 1e-12
        ? fit * real > 0
          ? "同号"
          : "异号"
        : "";
    rows.push(
      `<div class="score-layer-row"><span>真实极值序</span>` +
        `<span class="num ${signCls(real)}">${escapeText(fmtSigned(real, 3))}%${
          agree ? ` · ${agree}` : ""
        }</span></div>`
    );
  }
  const hint = hasTerms
    ? `${escapeText(Y_HL_TITLE)} · Ridge path`
    : escapeText(Y_HL_TITLE);
  return (
    `<div class="score-layer score-layer-hl">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_hl · 极值序</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length ? `<div class="score-layer-compose">${rows.join("")}</div>` : "") +
    `</div>`
  );
}

/** y_τ60 列 tip：Ridge 预估 price(τ⊕60m)/price(τ)−1。 */
function formatCompactT60Tip(raw) {
  const fit = resolveYT60Score(raw);
  const fitTxt = fit == null ? "—" : `${fmtSigned(fit, 3)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const hasTerms = hasT60FormulaTerms(raw);
  const feat = (raw && raw.features_tau) || {};
  const realRaw = raw && (raw.y_t60_realized ?? raw.t60_realized);
  const real =
    realRaw != null && Number.isFinite(Number(realRaw)) ? Number(realRaw) : null;
  const ySpec =
    (raw && raw.y_spec_τ60 && raw.y_spec_τ60.formula) ||
    (raw && raw.y_spec_t60 && raw.y_spec_t60.formula) ||
    "price[τ+60m]/price[τ]−1";
  const rows = [];
  if (real != null) {
    const agree =
      fit != null && Math.abs(fit) > 1e-12 && real != null && Math.abs(real) > 1e-12
        ? fit * real > 0
          ? "同号"
          : "异号"
        : "";
    rows.push(
      `<div class="score-layer-row"><span>真实 τ⊕60m</span>` +
        `<span class="num ${signCls(real)}">${escapeText(fmtSigned(real, 3))}%${
          agree ? ` · ${agree}` : ""
        }</span></div>`
    );
  }
  const gap = raw && resolveGapPct(raw);
  if (gap != null && Number.isFinite(Number(gap))) {
    rows.push(
      `<div class="score-layer-row"><span>跳空缺口</span>` +
        `<span class="num ${signCls(gap)}">${escapeText(
          fmtSigned(Number(gap), 2)
        )}%</span></div>`
    );
  }
  if (!hasTerms) {
    for (const [k, label] of Object.entries(TAU_FEAT_LABELS)) {
      if (k === "gap_pct") continue;
      const v = feat[k];
      if (v == null || v === "") continue;
      if (typeof v === "boolean") {
        rows.push(
          `<div class="score-layer-row"><span>${escapeText(label)}</span><span>${
            v ? "是" : "否"
          }</span></div>`
        );
        continue;
      }
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const pct =
        k === "open_gap" ||
        k === "gap_vs_sector" ||
        k === "ret_open_to_tau" ||
        k === "ret_last_5m" ||
        k === "ret_last_30m" ||
        k === "ret_last_60m" ||
        k === "ret_last_15m" ||
        k === "session_vwap_dev" ||
        k === "sector_ret_last_30m" ||
        k === "ret_last_30m_vs_sector" ||
        k === "t30_lag1" ||
        k === "t30_ma5" ||
        k === "sector_ret_last_60m" ||
        k === "ret_last_60m_vs_sector" ||
        k === "t60_lag1" ||
        k === "t60_ma5" ||
        k === "ret_last_90m" ||
        k === "sector_ret_last_90m" ||
        k === "ret_last_90m_vs_sector" ||
        k === "t90_lag1" ||
        k === "t90_ma5";
      rows.push(
        `<div class="score-layer-row"><span>${escapeText(label)}</span><span class="num ${
          Number.isFinite(n) ? signCls(n) : ""
        }">${escapeText(txt)}${Number.isFinite(n) && pct ? "%" : ""}</span></div>`
      );
    }
  }
  const hint = hasTerms
    ? `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · Ridge 模型预估 · 做 T 旁路`
    : `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · 做 T 旁路，不进 C_τ`;
  return (
    `<div class="score-layer score-layer-t60">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_τ60 · Ridge</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}
/** y_τ90 列 tip：Ridge 预估 price(τ⊕90m)/price(τ)−1。 */
function formatCompactT90Tip(raw) {
  const fit = resolveYT90Score(raw);
  const fitTxt = fit == null ? "—" : `${fmtSigned(fit, 3)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const hasTerms = hasT90FormulaTerms(raw);
  const feat = (raw && raw.features_tau) || {};
  const realRaw = raw && (raw.y_t90_realized ?? raw.t90_realized);
  const real =
    realRaw != null && Number.isFinite(Number(realRaw)) ? Number(realRaw) : null;
  const ySpec =
    (raw && raw.y_spec_τ90 && raw.y_spec_τ90.formula) ||
    (raw && raw.y_spec_t90 && raw.y_spec_t90.formula) ||
    "price[τ+90m]/price[τ]−1";
  const rows = [];
  if (real != null) {
    const agree =
      fit != null && Math.abs(fit) > 1e-12 && real != null && Math.abs(real) > 1e-12
        ? fit * real > 0
          ? "同号"
          : "异号"
        : "";
    rows.push(
      `<div class="score-layer-row"><span>真实 τ⊕90m</span>` +
        `<span class="num ${signCls(real)}">${escapeText(fmtSigned(real, 3))}%${
          agree ? ` · ${agree}` : ""
        }</span></div>`
    );
  }
  const gap = raw && resolveGapPct(raw);
  if (gap != null && Number.isFinite(Number(gap))) {
    rows.push(
      `<div class="score-layer-row"><span>跳空缺口</span>` +
        `<span class="num ${signCls(gap)}">${escapeText(
          fmtSigned(Number(gap), 2)
        )}%</span></div>`
    );
  }
  if (!hasTerms) {
    for (const [k, label] of Object.entries(TAU_FEAT_LABELS)) {
      if (k === "gap_pct") continue;
      const v = feat[k];
      if (v == null || v === "") continue;
      if (typeof v === "boolean") {
        rows.push(
          `<div class="score-layer-row"><span>${escapeText(label)}</span><span>${
            v ? "是" : "否"
          }</span></div>`
        );
        continue;
      }
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const pct =
        k === "open_gap" ||
        k === "gap_vs_sector" ||
        k === "ret_open_to_tau" ||
        k === "ret_last_5m" ||
        k === "ret_last_30m" ||
        k === "ret_last_90m" ||
        k === "ret_last_15m" ||
        k === "session_vwap_dev" ||
        k === "sector_ret_last_30m" ||
        k === "ret_last_30m_vs_sector" ||
        k === "t30_lag1" ||
        k === "t30_ma5" ||
        k === "sector_ret_last_90m" ||
        k === "ret_last_90m_vs_sector" ||
        k === "t90_lag1" ||
        k === "t90_ma5";
      rows.push(
        `<div class="score-layer-row"><span>${escapeText(label)}</span><span class="num ${
          Number.isFinite(n) ? signCls(n) : ""
        }">${escapeText(txt)}${Number.isFinite(n) && pct ? "%" : ""}</span></div>`
      );
    }
  }
  const hint = hasTerms
    ? `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · Ridge 模型预估 · 做 T 旁路`
    : `τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · 做 T 旁路，不进 C_τ`;
  return (
    `<div class="score-layer score-layer-t90">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_τ90 · Ridge</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}

/** y_τw 列 tip：符号和 f(ŷ_τ30)+f(ŷ_τ60)+f(ŷ_τ90)；f(x)=1 if x>0 else −1。 */
function formatCompactTWTip(raw) {
  const y30 = resolveYT30Score(raw);
  const y60 = resolveYT60Score(raw);
  const y90 = resolveYT90Score(raw);
  const fit = blendYtw(y30, y60, y90);
  const fitTxt = fit == null ? "—" : fmtYtwVote(fit);
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const r30Raw = raw && (raw.y_t30_realized ?? raw.t30_realized);
  const r60Raw = raw && (raw.y_t60_realized ?? raw.t60_realized);
  const r90Raw = raw && (raw.y_t90_realized ?? raw.t90_realized);
  const r30 = r30Raw != null && Number.isFinite(Number(r30Raw)) ? Number(r30Raw) : null;
  const r60 = r60Raw != null && Number.isFinite(Number(r60Raw)) ? Number(r60Raw) : null;
  const r90 = r90Raw != null && Number.isFinite(Number(r90Raw)) ? Number(r90Raw) : null;
  const real = blendYtw(r30, r60, r90);
  const rows = [];
  const pushHead = (label, hat, realN) => {
    const fHat = yTwSign(hat);
    const fReal = yTwSign(realN);
    const hatTxt =
      hat == null
        ? "—"
        : `${fHat == null ? "—" : fmtYtwVote(fHat)} · ${fmtSigned(hat, 3)}%`;
    const realTxt =
      realN == null
        ? ""
        : ` · 实 ${fReal == null ? "—" : fmtYtwVote(fReal)} (${escapeText(fmtSigned(realN, 3))}%)`;
    rows.push(
      `<div class="score-layer-row"><span>${escapeText(label)}</span>` +
        `<span class="num ${signCls(hat)}">${escapeText(hatTxt)}${realTxt}</span></div>`
    );
  };
  pushHead("ŷ_τ30", y30, r30);
  pushHead("ŷ_τ60", y60, r60);
  pushHead("ŷ_τ90", y90, r90);
  if (real != null) {
    const agree =
      fit != null && Math.abs(fit) > 1e-12 && Math.abs(real) > 1e-12
        ? fit * real > 0
          ? "同号"
          : "异号"
        : "";
    rows.push(
      `<div class="score-layer-row"><span>真实 τ后窗口</span>` +
        `<span class="num ${signCls(real)}">${escapeText(fmtYtwVote(real))}${
          agree ? ` · ${agree}` : ""
        }</span></div>`
    );
  }
  const missing = [];
  if (y30 == null) missing.push("ŷ_τ30");
  if (y60 == null) missing.push("ŷ_τ60");
  if (y90 == null) missing.push("ŷ_τ90");
  const hint =
    `τ=${escapeText(tau)} · ŷ_τw=f(ŷ_τ30)+f(ŷ_τ60)+f(ŷ_τ90) · f(x)=1 if x>0 else −1` +
    (missing.length ? ` · 缺 ${missing.join("、")}，不计` : "");
  return (
    `<div class="score-layer score-layer-tw">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_τw · 窗口</div>` +
    `<div class="score-hero-value ${signCls(fit)}">${escapeText(fitTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${hint}</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}

/** R_τ 列 tip：Ĉ_τ/price(τ)−1，与价带同目标。 */
function formatCompactRtauTip(raw) {
  const feat = (raw && raw.features_tau) || {};
  const rot =
    feat.ret_open_to_tau != null && Number.isFinite(Number(feat.ret_open_to_tau))
      ? Number(feat.ret_open_to_tau)
      : raw && raw.ret_open_to_tau != null && Number.isFinite(Number(raw.ret_open_to_tau))
        ? Number(raw.ret_open_to_tau)
        : null;
  const yOc = resolveTauScore(raw);
  const cTau =
    raw && raw.c_tau != null && Number.isFinite(Number(raw.c_tau)) ? Number(raw.c_tau) : null;
  const barC =
    raw && raw.bar_c != null && Number.isFinite(Number(raw.bar_c)) && Number(raw.bar_c) > 0
      ? Number(raw.bar_c)
      : null;
  const remBand = cTau != null && barC != null ? (cTau / barC - 1) * 100 : null;
  const remStored =
    raw && raw.r_hat != null && Number.isFinite(Number(raw.r_hat))
      ? Number(raw.r_hat)
      : raw && raw.residual != null && Number.isFinite(Number(raw.residual))
        ? Number(raw.residual)
        : null;
  const remCalc = yOc != null && rot != null ? remainingAtTauPct(yOc, rot) : null;
  const rem = remBand != null ? remBand : remStored != null ? remStored : remCalc;
  const remTxt = rem == null ? "—" : `${fmtSigned(rem, 3)}%`;
  const rows = [];
  if (yOc != null) {
    rows.push(
      `<div class="score-layer-row"><span>ŷ_oc open→close</span>` +
        `<span class="num ${signCls(yOc)}">${escapeText(fmtSigned(yOc, 3))}%</span></div>`
    );
  }
  const yTarget =
    raw && raw.y_oc_target != null && Number.isFinite(Number(raw.y_oc_target))
      ? Number(raw.y_oc_target)
      : null;
  if (yTarget != null) {
    rows.push(
      `<div class="score-layer-row"><span>clip(ŷ_oc×scale) 目标</span>` +
        `<span class="num ${signCls(yTarget)}">${escapeText(fmtSigned(yTarget, 3))}%</span></div>`
    );
  }
  if (cTau != null && barC != null) {
    rows.push(
      `<div class="score-layer-row"><span>Ĉ_τ / C</span>` +
        `<span class="num">${escapeText(`${cTau.toFixed(3)} / ${barC.toFixed(3)}`)}</span></div>`
    );
  }
  if (rot != null) {
    rows.push(
      `<div class="score-layer-row"><span>开盘→τ 已实现</span>` +
        `<span class="num ${signCls(rot)}">${escapeText(fmtSigned(rot, 3))}%</span></div>`
    );
  }
  const ytc = resolveYτcScore(raw);
  if (ytc != null && (rem == null || Math.abs(ytc - rem) > 1e-3)) {
    rows.push(
      `<div class="score-layer-row"><span>ŷ_τc Ridge 对照</span>` +
        `<span class="num ${signCls(ytc)}">${escapeText(fmtSigned(ytc, 3))}%</span></div>`
    );
  }
  return (
    `<div class="score-layer score-layer-rtau">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">R̂_τ · Ĉ_τ/price(τ)−1</div>` +
    `<div class="score-hero-value ${signCls(rem)}">${escapeText(remTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">与 Ĉ_τ 同目标 · remaining(clip(ŷ_oc×scale), price)</div>` +
    (rows.length
      ? `<div class="score-layer-compose">${rows.join("")}</div>`
      : "") +
    `</div>`
  );
}

/** y_on 列：开盘决策口径旁路（勿与含当日 close 的 y_on_path 混读）。 */
function formatCompactOnTip(raw) {
  const on = resolveOnScore(raw);
  const val = on == null ? "—" : `${fmtSigned(on, 2)}%`;
  const ySpec =
    (raw && raw.y_spec_on && raw.y_spec_on.formula) ||
    (raw && raw.on_y_spec) ||
    "open[T+1]/close[T]−1";
  const pathOn =
    raw && raw.y_on_path != null && Number.isFinite(Number(raw.y_on_path))
      ? Number(raw.y_on_path)
      : raw &&
          raw.predicted_score_on_path != null &&
          Number.isFinite(Number(raw.predicted_score_on_path))
        ? Number(raw.predicted_score_on_path)
        : null;
  const pathLine =
    pathOn == null
      ? ""
      : `<div class="score-hero-hint">复盘路径价 y_on_path=${escapeText(
          `${fmtSigned(pathOn, 2)}%`
        )}（可含当日 close · 不定向）</div>`;
  return (
    `<div class="score-layer score-layer-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">y_co</div>` +
    `<div class="score-hero-value ${signCls(on)}">${escapeText(val)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(String(ySpec))} · 开盘决策 · 旁路 · 不进排序</div>` +
    pathLine +
    `</div>`
  );
}

/** ŷ_oc：独立预估 T 收相对 T 开（与表列 / 组成合计同口径）。 */
export function formatRemScoreSection(raw) {
  const rem = resolveTauScore(raw);
  const lifted = resolveTauLiftedScore(raw);
  const gap = raw && resolveGapPct(raw);
  const ep = raw && raw.event_prior;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const hasRem = rem != null;
  const hasGap = gap != null && Number.isFinite(Number(gap));
  const theme = !!(ep && ep.theme);
  const remTxt = hasRem ? `${fmtSigned(Number(rem), 3)}%` : "—";
  const ySpec =
    (raw && raw.y_spec_tau && raw.y_spec_tau.formula) ||
    (raw && raw.rem_y_spec) ||
    "close[T]/open[T]−1";

  const feat = (raw && raw.features_tau) || {};
  const hasTauTerms = !!(
    raw &&
    ((raw.formula_terms_tau && (raw.formula_terms_tau.terms || []).length) ||
      (raw.score_formula_terms_tau && (raw.score_formula_terms_tau.terms || []).length))
  );
  const featRows = [];
  if (
    hasRem &&
    lifted != null &&
    Number.isFinite(lifted) &&
    Math.abs(lifted - Number(rem)) > 1e-4
  ) {
    featRows.push(
      `<div class="score-layer-row"><span>昨收对照（缺口∘ŷ_oc）</span>` +
        `<span class="num ${signCls(lifted)}">${escapeText(
          fmtSigned(lifted, 3)
        )}%</span></div>`
    );
  }
  // 有 β·z 组成表时不再堆特征原值行（组成表更完整）
  if (!hasTauTerms) {
    if (hasGap || feat.gap_pct != null) {
      const g = hasGap ? Number(gap) : Number(feat.gap_pct);
      if (Number.isFinite(g)) {
        featRows.push(
          `<div class="score-layer-row"><span>跳空缺口</span><span class="num ${signCls(
            g
          )}">${escapeText(fmtSigned(g, 2))}%</span></div>`
        );
      }
    }
    for (const [k, label] of Object.entries(TAU_FEAT_LABELS)) {
      if (k === "gap_pct") continue;
      const v = feat[k];
      if (v == null || v === "") continue;
      if (typeof v === "boolean") {
        featRows.push(
          `<div class="score-layer-row"><span>${escapeText(label)}</span><span>${
            v ? "是" : "否"
          }</span></div>`
        );
        continue;
      }
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const pct =
        k === "gap_pct" ||
        k === "open_gap" ||
        k === "gap_vs_sector" ||
        k === "ret_open_to_tau";
      featRows.push(
        `<div class="score-layer-row"><span>${escapeText(label)}</span><span class="num ${
          Number.isFinite(n) ? signCls(n) : ""
        }">${escapeText(txt)}${Number.isFinite(n) && pct ? "%" : ""}</span></div>`
      );
    }
    if (theme) {
      featRows.push(
        `<div class="score-layer-row"><span>事件先验</span><span>主题日</span></div>`
      );
    }
  }
  const warn = Array.isArray(ep && ep.warnings) ? ep.warnings.slice(0, 2).join(" · ") : "";
  const compose = featRows.length
    ? `<div class="score-layer-compose">${featRows.join("")}</div>`
    : "";
  const body = !hasRem
    ? `<div class="score-hero-hint">未产出（需 ŷ_oc 模型）</div>`
    : hasTauTerms
      ? `${compose}<div class="score-hero-hint">组成见表「ŷ_oc 组成」· 与表列 / 合计同口径</div>`
      : compose ||
        `<div class="score-hero-hint">τ=${escapeText(tau)} · y=${escapeText(
          String(ySpec)
        )}</div>`;

  return (
    `<div class="score-layer score-layer-tau">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_oc · T收 / T开（拟合）</div>` +
    `<div class="score-hero-value ${signCls(rem)}">${escapeText(remTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · 拟合原值</div>` +
    body +
    (warn ? `<div class="score-hero-hint">${escapeText(warn)}</div>` : "") +
    `</div>`
  );
}

/** nowcast（nc）列 tip：对照昨收，不进决策。 */
export function formatNowcastSection(raw) {
  const ncN = resolveNowcastCcScore(raw);
  if (ncN == null || !Number.isFinite(Number(ncN))) return "";
  const yEod = resolveEodScore(raw);
  const yt = resolveTau(raw);
  const ytCc = liftTauVsPrevClose(raw, yt);
  const k = raw && raw.nowcast_K != null ? Number(raw.nowcast_K) : null;
  const q = raw && raw.nowcast_q != null ? Number(raw.nowcast_q) : null;
  const asOf = raw && raw.nowcast_as_of ? String(raw.nowcast_as_of) : "";
  const rows = [];
  const eodN = yEod == null || yEod === "" ? null : Number(yEod);
  const priorN =
    raw && raw.nowcast_x_prior != null && Number.isFinite(Number(raw.nowcast_x_prior))
      ? Number(raw.nowcast_x_prior)
      : eodN;
  const tCcN = ytCc == null || ytCc === "" ? null : Number(ytCc);
  if (priorN != null && Number.isFinite(priorN)) {
    rows.push(
      `<div class="score-layer-row"><span>先验 ŷ_oo</span><span class="num ${signCls(
        priorN
      )}">${escapeText(`${fmtSigned(priorN, 2)}%`)}</span></div>`
    );
  }
  if (tCcN != null && Number.isFinite(tCcN)) {
    rows.push(
      `<div class="score-layer-row"><span>ŷ_oc 昨收</span><span class="num ${signCls(
        tCcN
      )}">${escapeText(`${fmtSigned(tCcN, 2)}%`)}</span></div>`
    );
  }
  if (k != null && Number.isFinite(k)) {
    rows.push(
      `<div class="score-layer-row"><span>K</span><span class="num">${escapeText(
        k.toFixed(3)
      )}</span></div>`
    );
  }
  rows.push(
    `<div class="score-layer-row score-layer-row-total"><span>ŷ_nowcast</span><span class="num ${signCls(
      ncN
    )}">${escapeText(`${fmtSigned(ncN, 2)}%`)}</span></div>`
  );
  const bits = ["nowcast = nc · 对照昨收 · 不进排序/闸/入簿"];
  if (asOf) bits.push(`@${asOf}`);
  if (q != null && Number.isFinite(q)) bits.push(`q=${q.toFixed(3)}`);
  if (
    priorN != null &&
    Number.isFinite(priorN) &&
    Math.abs(Number(ncN) - priorN) < 1e-4
  ) {
    bits.push("后验≈先验（未观测 ŷ_oc 或 K≈0）");
  }
  return (
    `<div class="score-layer score-layer-nowcast is-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">nowcast（nc）· 昨收</div>` +
    `<div class="score-hero-value ${signCls(ncN)}">${escapeText(
      `${fmtSigned(ncN, 2)}%`
    )}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(bits.join(" · "))}</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
    `</div>`
  );
}

function resolveNcOcGate(raw) {
  if (!raw || typeof raw !== "object") return false;
  if (raw.y_nowcast_oc_gate != null) return raw.y_nowcast_oc_gate === true;
  const rules = raw.rules && typeof raw.rules === "object" ? raw.rules : {};
  return rules.y_nowcast_oc_gate === true;
}

function resolveNcOcEnter(raw) {
  if (!raw || typeof raw !== "object") return null;
  const rules = raw.rules && typeof raw.rules === "object" ? raw.rules : {};
  const pick = (k) => {
    const v = raw[k] != null && raw[k] !== "" ? raw[k] : rules[k];
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  return pick("y_nc_strong") ?? pick("y_nowcast_enter");
}

function resolveNcOcValues(raw) {
  const yNc = resolveNowcastCcScore(raw);
  const gap = resolveGapPct(raw);
  let oc =
    raw && raw.y_nc_oc != null && Number.isFinite(Number(raw.y_nc_oc))
      ? Number(raw.y_nc_oc)
      : raw && raw.y_nowcast_oc != null && Number.isFinite(Number(raw.y_nowcast_oc))
        ? Number(raw.y_nowcast_oc)
        : null;
  if (oc == null && yNc != null && gap != null) oc = nowcastOcPct(yNc, gap);
  return { yNc, gap, oc };
}

/** nowcast oc 列：nc 映到 open→close。 */
export function formatNcOcSection(raw) {
  const { yNc, gap, oc } = resolveNcOcValues(raw);
  if (oc == null && (yNc == null || gap == null)) return "";
  const ocTxt = oc == null ? "—" : `${fmtSigned(oc, 2)}%`;
  const ocGate = resolveNcOcGate(raw);
  const compareLabel =
    raw && raw.nowcast_compare_label
      ? String(raw.nowcast_compare_label)
      : ocGate
        ? "y_nc_oc"
        : "y_nc";
  const yt = resolveTau(raw);
  const ncEnter = resolveNcOcEnter(raw);
  const rows = [];
  if (yNc != null && Number.isFinite(yNc)) {
    rows.push(
      `<div class="score-layer-row"><span>nc（昨收）</span><span class="num ${signCls(
        yNc
      )}">${escapeText(`${fmtSigned(yNc, 2)}%`)}</span></div>`
    );
  }
  if (gap != null && Number.isFinite(gap)) {
    rows.push(
      `<div class="score-layer-row"><span>gap（开盘缺口）</span><span class="num ${signCls(
        gap
      )}">${escapeText(`${fmtSigned(gap, 2)}%`)}</span></div>`
    );
  }
  if (yNc != null && gap != null) {
    rows.push(
      `<div class="score-layer-row"><span>换算</span><span>(1+nc)/(1+gap)−1</span></div>`
    );
  }
  rows.push(
    `<div class="score-layer-row score-layer-row-total"><span>nowcast oc</span><span class="num ${signCls(
      oc
    )}">${escapeText(ocTxt)}</span></div>`
  );
  if (yt != null && Number.isFinite(Number(yt))) {
    rows.push(
      `<div class="score-layer-row"><span>y_τ（对照）</span><span class="num ${signCls(
        yt
      )}">${escapeText(`${fmtSigned(Number(yt), 2)}%`)}</span></div>`
    );
  }
  const gateBits = [
    ocGate ? "异号闸 OC=ON · 比 nowcast oc" : "异号闸 OC=OFF · 比 nc（oc 仅对照）",
    "open→close",
  ];
  if (ncEnter != null) gateBits.push(`|nowcast|≥${ncEnter}% 才拦异号`);
  if (yt != null && oc != null && ncEnter != null) {
    const signConflict =
      Math.abs(Number(yt)) >= 1e-9 &&
      Math.abs(oc) >= ncEnter &&
      (Number(yt) > 0) !== (oc > 0);
    if (signConflict && ocGate) {
      gateBits.push(`y_τ 与 ${compareLabel} 异号 → 跳过`);
    } else if (signConflict && !ocGate) {
      gateBits.push(`y_τ 与 nowcast oc 异号（闸仍看 nc）`);
    } else if (ocGate) {
      gateBits.push(`y_τ 与 ${compareLabel} 同号或未达门槛`);
    }
  }
  return (
    `<div class="score-layer score-layer-nowcast is-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">nowcast oc · open→close</div>` +
    `<div class="score-hero-value ${signCls(oc)}">${escapeText(ocTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(gateBits.join(" · "))}</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
    `</div>`
  );
}

/** 融合分：ranking = w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)。与表列同源。 */
export function formatBlendScoreSection(raw) {
  const blend = resolveRankingScore(raw);
  const yEod = resolveEodScore(raw);
  const yt = pickYOcFitted(raw);
  const yCo = resolveOnScore(raw);
  const { wOo, wOc } = fusionWeightsFromItem(raw);
  const wCo = fusionWCoFromItem(raw);
  const ocRight = ocWithCoPct(yt, yCo, wCo);
  const blendTxt = blend == null ? "—" : `${fmtSigned(blend, 2)}%`;
  const eodN = yEod == null || yEod === "" ? null : Number(yEod);
  const tN = yt == null || yt === "" ? null : Number(yt);
  const coN = yCo == null || yCo === "" ? null : Number(yCo);
  const rightN = ocRight == null || ocRight === "" ? null : Number(ocRight);
  const eodTxt = eodN == null || !Number.isFinite(eodN) ? "—" : `${fmtSigned(eodN, 2)}%`;
  const tTxt = tN == null || !Number.isFinite(tN) ? "—" : `${fmtSigned(tN, 2)}%`;
  const coTxt = coN == null || !Number.isFinite(coN) ? "—" : `${fmtSigned(coN, 2)}%`;
  const wTxt = `w_oo=${Number(wOo).toFixed(2)} · w_oc=${Number(wOc).toFixed(2)}`;
  const arith =
    eodN != null && Number.isFinite(eodN) && rightN != null && Number.isFinite(rightN)
      ? ` · ${Number(wOo).toFixed(2)}×${fmtSigned(eodN, 2)}+${Number(wOc).toFixed(2)}×${fmtSigned(rightN, 2)}`
      : "";
  const hint = `${wTxt}${arith} · 与表列 ranking 同式`;
  const head =
    raw && raw.dual_score_head != null
      ? String(raw.dual_score_head)
      : raw && raw.dual_score_single_head
        ? "single"
        : "";
  let headHint = "";
  if (head === "single_eod" && tN != null && Number.isFinite(tN)) {
    headHint = " · 单头：ranking=ŷ_oo（缺 ŷ_oc 权）";
  } else if (head === "single_eod") {
    headHint = " · 单头降级：仅 ŷ_oo（缺 ŷ_oc）";
  } else if (head === "single_tau") {
    headHint = " · 单头降级：仅 ŷ_oc（缺 ŷ_oo）";
  } else if (head === "blend") {
    headHint = " · 双头融合";
  }
  const rows = [
    `<div class="score-layer-row"><span>ŷ_oo ×${Number(wOo).toFixed(2)}</span><span class="num ${signCls(
      eodN
    )}">${escapeText(eodTxt)}</span></div>`,
    `<div class="score-layer-row"><span>ŷ_oc ×${Number(wOc).toFixed(2)}</span><span class="num ${signCls(
      tN
    )}">${escapeText(tTxt)}</span></div>`,
    `<div class="score-layer-row"><span>ŷ_co</span><span class="num ${signCls(
      coN
    )}">${escapeText(coTxt)}${wCo > 1e-12 ? ` · ×${Number(wCo).toFixed(2)}` : " · 不进"}</span></div>`,
    `<div class="score-layer-row score-layer-row-total"><span>ranking</span><span class="num ${signCls(
      blend
    )}">${escapeText(blendTxt)}</span></div>`,
  ];
  const yCheck = raw && raw.y_check != null ? String(raw.y_check) : "";
  const yDisagree =
    raw && raw.y_disagree != null && Number.isFinite(Number(raw.y_disagree))
      ? Number(raw.y_disagree)
      : null;
  const ySigma =
    raw && raw.y_sigma != null && Number.isFinite(Number(raw.y_sigma))
      ? Number(raw.y_sigma)
      : null;
  const yTrust =
    raw && raw.eod_trust != null && Number.isFinite(Number(raw.eod_trust))
      ? Number(raw.eod_trust)
      : null;
  const checkLabel = {
    ok: "校验通过",
    conflict: "双头分歧",
    low_conf: "低置信",
    missing_tau: "缺 τ",
    single_head: "单头",
  };
  if (yCheck) {
    const warn = yCheck !== "ok";
    rows.push(
      `<div class="score-layer-row${warn ? " is-warn" : ""}"><span>Y·EOD校验</span><span>${escapeText(
        checkLabel[yCheck] || yCheck
      )}${
        yDisagree != null ? ` · |Δ|=${yDisagree.toFixed(2)}` : ""
      }${ySigma != null ? ` · σ≈${ySigma.toFixed(2)}` : ""}${
        yTrust != null ? ` · trust=${yTrust.toFixed(2)}` : ""
      }</span></div>`
    );
  }
  return (
    `<div class="score-layer score-layer-blend${
      head.startsWith("single") || (yCheck && yCheck !== "ok") ? " is-warn" : ""
    }">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ranking · w·ŷ_oo + w·(ŷ_oc∘w_co·ŷ_co)${
      head.startsWith("single") ? " · 单头" : ""
    }${yCheck && yCheck !== "ok" ? " · Y校验" : ""}</div>` +
    `<div class="score-hero-value ${signCls(blend)}">${escapeText(blendTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(hint + headHint)}</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
    `</div>`
  );
}

export function formatWeightSourceNote(raw) {
  const src = String((raw && raw.weight_source) || "").trim();
  const mode = String((raw && raw.cluster_mode) || "").trim();
  const rms = String((raw && raw.return_model_source) || "").trim();
  const label = raw && raw.cluster_label ? String(raw.cluster_label) : "";
  const ver =
    raw && raw.cluster_version != null && raw.cluster_version !== ""
      ? `v${raw.cluster_version}`
      : "";
  let line = "全局收益分模型";
  if (rms === "oos_failed_global" || rms === "oos_failed_heuristic") {
    line =
      rms === "oos_failed_heuristic"
        ? "OOS 失败组 · 表列=启发式(0–100) · 组 ŷ% 仅对照"
        : "OOS 失败组 · 主分=全局 ŷ% · 组 ŷ% 仅对照";
    if (label) line += ` · ${label}`;
    if (ver) line += ` · ${ver}`;
    const hs =
      raw && raw.heuristic_score != null && Number.isFinite(Number(raw.heuristic_score))
        ? Number(raw.heuristic_score)
        : null;
    const sc =
      raw && raw.score_cluster != null && Number.isFinite(Number(raw.score_cluster))
        ? Number(raw.score_cluster)
        : null;
    const sg =
      raw && raw.score_global != null && Number.isFinite(Number(raw.score_global))
        ? Number(raw.score_global)
        : null;
    const bitsDual = [];
    if (hs != null) bitsDual.push(`heuristic ${fmtSigned(hs, 1)}`);
    if (sc != null) bitsDual.push(`组ŷ ${fmtSigned(sc, 3)}%`);
    if (sg != null) bitsDual.push(`全局ŷ ${fmtSigned(sg, 3)}%`);
    if (bitsDual.length) line += ` · ${bitsDual.join(" · ")}`;
  } else if (rms === "cluster_group_beta" || src.startsWith("cluster:")) {
    line = `分组因子系数 · ${
      src.startsWith("cluster:") ? src.slice("cluster:".length) : label || "组"
    }`;
    if (ver) line += ` · ${ver}`;
  } else if (src === "oos_failed_degrade") {
    line = "OOS 失败组 · 主分已降级";
    if (label) line += `（${label}）`;
  } else if (src === "global+shadow") {
    line = "主分=全局 ŷ · 影子已算组 ŷ";
    if (label) line += `（${label}）`;
  } else if (src === "global_fallback") {
    line = "全局收益分回退（未映射到分组）";
  } else if (rms === "global" || src === "global" || !src) {
    if (mode && mode !== "off") line = `全局收益分 · mode=${mode}`;
  } else if (src) {
    line = src;
  }

  const bits = [
    `<div class="score-section-title">模型来源</div>`,
    `<div class="score-weight-source">${escapeText(line)}</div>`,
  ];
  return `<div class="score-weight-section">${bits.join("")}</div>`;
}

/** 分项拆解表：因子 / β / z / 贡献。有 terms 时优先于纯系数表。 */
export function formatFormulaTermsSection(raw, opts = {}) {
  const key = opts.key || "eod";
  const expl =
    key === "tau"
      ? raw && (raw.formula_terms_tau || raw.score_formula_terms_tau)
      : key === "r"
        ? raw && (raw.formula_terms_r || raw.score_formula_terms_r)
        : key === "t30"
          ? raw && (raw.formula_terms_t30 || raw.score_formula_terms_t30)
          : key === "t60"
            ? raw && (raw.formula_terms_t60 || raw.score_formula_terms_t60)
            : key === "t90"
            ? raw && (raw.formula_terms_t90 || raw.score_formula_terms_t90)
            : key === "path" || key === "hl"
              ? raw && (raw.formula_terms_path || raw.score_formula_terms_path)
            : key === "on"
          ? raw && (raw.formula_terms_on || raw.score_formula_terms_on)
            : raw && (raw.formula_terms || raw.score_formula_terms);
  if (!expl || typeof expl !== "object") return "";
  const terms = Array.isArray(expl.terms) ? expl.terms : [];
  if (!terms.length && expl.intercept == null) return "";

  const title =
    key === "tau"
      ? "ŷ_oc 组成"
      : key === "r"
        ? "ŷ_τc 组成"
        : key === "t30"
          ? "ŷ_τ30 组成"
          : key === "t60"
            ? "ŷ_τ60 组成"
          : key === "t90"
            ? "ŷ_τ90 组成"
          : key === "path" || key === "hl"
            ? "ŷ_hl 组成"
          : key === "on"
          ? "ŷ_co 组成"
            : "ŷ_oo 组成";
  const totalLabel =
    key === "tau"
      ? "合计 ŷ_oc（T收/T开）"
      : key === "r"
        ? "合计 ŷ_τc（T收/τ价）"
        : key === "t30"
          ? "合计 ŷ_τ30（τ⊕30m/τ价）"
          : key === "t60"
            ? "合计 ŷ_τ60（τ⊕60m/τ价）"
          : key === "t90"
            ? "合计 ŷ_τ90（τ⊕90m/τ价）"
          : key === "path" || key === "hl"
            ? "合计 ŷ_hl（极值序）"
          : key === "on"
          ? "合计 ŷ_co"
            : "合计 ŷ_oo";

  const rows = terms
    .map((t) => {
      const name = termFeatLabel(t, key);
      const gated = !!t.gated;
      const nameExtra = gated ? "（闸关）" : t.note ? `（${t.note}）` : "";
      const beta = fmtSigned(t.beta, 3);
      const z = gated ? "—" : fmtSigned(t.z, 2);
      const contrib = gated ? "0" : fmtSigned(t.contrib, 3);
      return (
        `<tr${gated ? ' class="score-ft-gated"' : ""}>` +
        `<td class="score-ft-name" title="${escapeText(t.key || "")}">${escapeText(
          name + nameExtra
        )}</td>` +
        `<td class="num ${signCls(t.beta)}">${escapeText(beta)}</td>` +
        `<td class="num ${signCls(t.z)}">${escapeText(z)}</td>` +
        `<td class="num ${signCls(t.contrib)}">${escapeText(contrib)}</td>` +
        `</tr>`
      );
    })
    .join("");

  const alpha = fmtSigned(expl.intercept, 3);
  const total = fmtSigned(expl.total, 3);

  // 贡献条：用已有 contrib，不依赖异步模块（tooltip 内联）
  const barTerms = terms
    .filter((t) => t && !t.gated && Number.isFinite(Number(t.contrib)))
    .map((t) => ({
      key: t.key,
      label: termFeatLabel(t, key),
      contrib: Number(t.contrib),
    }));
  const peak = Math.max(...barTerms.map((t) => Math.abs(t.contrib)), 1e-9);
  const barsHtml = barTerms.length
    ? `<div class="yhat-contrib-bars" aria-label="因子贡献">` +
      barTerms
        .slice(0, 10)
        .map((r) => {
          const pct = Math.min(100, (Math.abs(r.contrib) / peak) * 100);
          const side = r.contrib >= 0 ? "pos" : "neg";
          const sign = r.contrib > 0 ? "+" : "";
          return (
            `<div class="yhat-contrib-row" title="${escapeText(r.key || "")}">` +
            `<span class="yhat-contrib-name">${escapeText(r.label)}</span>` +
            `<span class="yhat-contrib-track">` +
            `<span class="yhat-contrib-bar ${side}" style="width:${pct.toFixed(1)}%"></span>` +
            `</span>` +
            `<span class="yhat-contrib-val ${side}">${escapeText(
              `${sign}${r.contrib.toFixed(3)}`
            )}</span>` +
            `</div>`
          );
        })
        .join("") +
      `</div>`
    : "";

  const caption =
    key === "tau"
      ? "β×z = 贡献；合计=Ridge 拟合原值（T收/T开），与表列 ŷ_oc / τ 闸同口径。"
      : key === "r"
        ? "β×z = 贡献；合计=Ridge 拟合原值（T收/τ价）。做 T 回测走研究套截距（Holdout 训练）；研究枢纽系数表默认展示执行套全样本截距。"
        : key === "t30"
          ? "β×z = 贡献；合计=Ridge 拟合原值（price(τ⊕30m)/price(τ)−1）。做 T 旁路，不进 C_τ。"
          : key === "t60"
            ? "β×z = 贡献；合计=Ridge 拟合原值（price(τ⊕60m)/price(τ)−1）。做 T 旁路，不进 C_τ。"
          : key === "t90"
            ? "β×z = 贡献；合计=Ridge 拟合原值（price(τ⊕90m)/price(τ)−1）。做 T 旁路，不进 C_τ。"
          : key === "path" || key === "hl"
            ? "β×z = 贡献；合计=Ridge 拟合原值（极值序 signed (H−L)/ref%）。做 T 同号闸 / 入场。"
          : "β×z = 贡献；条长∝|贡献|";
  const role = String((expl && expl.model_role) || "").toLowerCase();
  const alphaName =
    key === "r" && role === "research"
      ? "截距 α（研究套）"
      : key === "r" && role === "live"
        ? "截距 α（执行套）"
        : "截距 α";
  return (
    `<div class="score-formula-section">` +
    `<div class="score-section-title">${escapeText(title)}</div>` +
    barsHtml +
    `<table class="score-formula-table">` +
    `<thead><tr>` +
    `<th>因子</th><th>β</th><th>z</th><th>贡献</th>` +
    `</tr></thead>` +
    `<tbody>` +
    `<tr class="score-ft-alpha">` +
    `<td class="score-ft-name">${escapeText(alphaName)}</td>` +
    `<td class="num">—</td>` +
    `<td class="num">—</td>` +
    `<td class="num ${signCls(expl.intercept)}">${escapeText(alpha)}</td>` +
    `</tr>` +
    rows +
    `<tr class="score-ft-total">` +
    `<td class="score-ft-name">${escapeText(totalLabel)}</td>` +
    `<td class="num">—</td>` +
    `<td class="num">—</td>` +
    `<td class="num ${signCls(expl.total)}">${escapeText(`${total}%`)}</td>` +
    `</tr>` +
    `</tbody></table>` +
    `<div class="score-formula-caption">${escapeText(caption)}</div>` +
    `</div>`
  );
}

/** 特征同构（X 轨）：财务 PIT / 指数 / 深度边界。 */
export function formatFeatureIsoSection(raw) {
  if (!raw) return "";
  const bits = [];
  const pit = raw.fundamentals_pit || {};
  if (pit && (pit.mode || pit.as_of || pit.decision_as_of)) {
    const mode = String(pit.mode || "—");
    const asOf = String(pit.as_of || pit.decision_as_of || "—");
    bits.push(`财务 PIT · mode=${mode} · as_of=${asOf}`);
    if (pit.ann_missing) bits.push("ann_missing（可用日缺公告日）");
    if (pit.non_pit) bits.push("非严格 PIT 快照");
    if (pit.ok === false) bits.push("财务点缺失/失败");
  }
  const idx = raw.index_meta || {};
  if (idx && (idx.benchmark || idx.reason || idx.ok != null)) {
    if (idx.ok) {
      bits.push(
        `指数 ${String(idx.benchmark || "")} · ${Number(idx.bar_count) || "?"} 根`
      );
    } else {
      bits.push(`指数不可用 · ${String(idx.reason || "no_index")}`);
    }
  }
  const depth = String(raw.fundamentals_depth || "").trim();
  if (depth) {
    const depthNote =
      (raw.fundamentals_depth_meta && raw.fundamentals_depth_meta.note) || "";
    bits.push(
      depth === "cn_full"
        ? "财务深度 · A 股完整（需 ingest）"
        : depth === "hk_shallow"
          ? "财务深度 · 港股浅"
          : depth === "us_shallow"
            ? "财务深度 · 美股/其他浅"
            : `财务深度 · ${depth}`
    );
    if (depthNote && depth !== "cn_full") bits.push(String(depthNote));
  }
  if (!bits.length) return "";
  return (
    `<div class="score-feature-iso-section">` +
    `<div class="score-section-title">特征同构</div>` +
    bits.map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`).join("") +
    `</div>`
  );
}

/** 个股舆情徽章（ŷ 外）：仅参考，不调仓。 */
export function formatSentimentGateSection(raw) {
  if (!raw) return "";
  const meta = [];
  const notes = [];
  const prior = raw.sentiment_prior || {};
  const mode = String(prior.mode || "").trim();
  if (mode) {
    const active = prior.active ? "触发" : "未触发";
    meta.push({
      chip: active,
      chipCls: prior.active ? "is-on" : "is-off",
      code: `prior.mode=${mode}`,
    });
  } else if (
    raw.sentiment_include_in_score === false ||
    raw.sentiment_include_in_score === true
  ) {
    if (raw.sentiment_include_in_score) {
      meta.push({
        chip: "遗留",
        chipCls: "is-warn",
        code: "include_in_score=true",
        note: "不推荐；应走 prior.mode",
      });
    } else {
      meta.push({
        chip: "硬闸",
        chipCls: "is-gate",
        code: "include_in_score=false",
      });
    }
  }
  const hints = Array.isArray(raw.risk_hints) ? raw.risk_hints : [];
  for (const h of hints.slice(0, 2)) {
    if (h && h.note) notes.push(String(h.note));
  }
  const warns = Array.isArray(raw.warnings) ? raw.warnings : [];
  for (const w of warns.slice(0, 3)) {
    if (w) notes.push(String(w));
  }

  const metaHtml = meta
    .map((m) => {
      const note = m.note
        ? `<span class="score-sentiment-note">${escapeText(m.note)}</span>`
        : "";
      return (
        `<div class="score-sentiment-meta">` +
        `<span class="score-sentiment-chip ${m.chipCls}">${escapeText(m.chip)}</span>` +
        `<span class="score-sentiment-code">${escapeText(m.code)}</span>` +
        note +
        `</div>`
      );
    })
    .join("");
  const notesHtml = notes
    .map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`)
    .join("");

  if (!metaHtml && !notesHtml) return "";

  return (
    `<div class="score-sentiment-section">` +
    `<div class="score-section-title">舆情</div>` +
    `<div class="score-sentiment-lead">仅参考徽章 · 不进 ŷ · 不调仓</div>` +
    metaHtml +
    notesHtml +
    `</div>`
  );
}

function _compactPriorPack(p) {
  if (!p || typeof p !== "object" || !p.active) return null;
  return {
    active: true,
    mode: p.mode || null,
    role: p.role || null,
    warnings: Array.isArray(p.warnings) ? p.warnings.slice(0, 3) : [],
  };
}

/** 供 data-score-detail 嵌入的 M 层 prior 字段（控制体积）。 */
export function marketPriorDetailFields(it) {
  if (!it || typeof it !== "object") return {};
  const active = !!it.market_prior_active;
  const warns = Array.isArray(it.market_prior_warnings)
    ? it.market_prior_warnings.slice(0, 4)
    : [];
  if (
    !active &&
    !warns.length &&
    !it.cross_market_prior &&
    !it.market_sentiment_prior &&
    !it.regulatory_prior &&
    !it.ipo_drain_prior
  ) {
    return {};
  }
  return {
    market_prior_active: active,
    market_prior_warnings: warns,
    cross_market_prior: _compactPriorPack(it.cross_market_prior),
    market_sentiment_prior: _compactPriorPack(it.market_sentiment_prior),
    regulatory_prior: _compactPriorPack(it.regulatory_prior),
    ipo_drain_prior: _compactPriorPack(it.ipo_drain_prior),
  };
}

/** 市场级 prior（M 层）旁路提示。 */
export function formatMarketPriorSection(raw) {
  if (!raw) return "";
  const active = !!raw.market_prior_active;
  const packs = [
    ["跨市场", raw.cross_market_prior],
    ["情绪周期", raw.market_sentiment_prior],
    ["监管", raw.regulatory_prior],
    ["IPO", raw.ipo_drain_prior],
  ];
  const meta = [];
  for (const [label, p] of packs) {
    if (!p || !p.active) continue;
    meta.push({
      chip: label,
      chipCls: "is-on",
      code: p.mode ? `mode=${p.mode}` : "gate",
    });
  }
  if (!meta.length && !active && !(raw.market_prior_warnings || []).length) {
    return "";
  }
  if (!meta.length && active) {
    meta.push({ chip: "M prior", chipCls: "is-on", code: "active" });
  }
  const notes = [];
  for (const w of (raw.market_prior_warnings || []).slice(0, 5)) {
    if (w) notes.push(String(w));
  }
  for (const [, p] of packs) {
    if (!p || !p.active) continue;
    for (const w of (p.warnings || []).slice(0, 2)) {
      if (w && !notes.includes(String(w))) notes.push(String(w));
    }
  }
  const metaHtml = meta
    .map(
      (m) =>
        `<div class="score-sentiment-meta">` +
        `<span class="score-sentiment-chip ${m.chipCls}">${escapeText(m.chip)}</span>` +
        `<span class="score-sentiment-code">${escapeText(m.code)}</span>` +
        `</div>`
    )
    .join("");
  const notesHtml = notes
    .map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`)
    .join("");
  if (!metaHtml && !notesHtml) return "";
  return (
    `<div class="score-sentiment-section score-market-prior-section">` +
    `<div class="score-section-title">市场 prior（M）</div>` +
    `<div class="score-sentiment-lead">盘前上下文 · 不进 ŷ · 调仓缩放</div>` +
    metaHtml +
    notesHtml +
    `</div>`
  );
}

/** 供 data-score-detail 嵌入的 tail_anomaly 字段（控制体积）。 */
export function tailAnomalyDetailFields(it) {
  if (!it || typeof it !== "object") return {};
  const pack = it.tail_anomaly;
  const fac = it.factors && typeof it.factors === "object" ? it.factors : {};
  const subs =
    it.sub_scores && typeof it.sub_scores === "object" ? it.sub_scores : {};
  const tvr =
    (pack && pack.tail_volume_ratio != null
      ? pack.tail_volume_ratio
      : fac.tail_volume_ratio) ?? null;
  const slope =
    (pack && pack.tail_price_slope_pct != null
      ? pack.tail_price_slope_pct
      : fac.tail_price_slope_pct) ?? null;
  const sub =
    (pack && pack.sub_score != null ? pack.sub_score : subs.tail_anomaly) ?? null;
  if (tvr == null && slope == null && sub == null) return {};
  return {
    tail_anomaly: {
      sub_score: sub,
      tail_volume_ratio: tvr,
      tail_price_slope_pct: slope,
    },
  };
}

/** 供 data-score-detail 嵌入的 overheat 字段（控制体积）。 */
export function overheatDetailFields(it) {
  if (!it || typeof it !== "object") return {};
  const pack = it.overheat;
  const fac = it.factors && typeof it.factors === "object" ? it.factors : {};
  const subs =
    it.sub_scores && typeof it.sub_scores === "object" ? it.sub_scores : {};
  const sub =
    (pack && pack.score != null ? pack.score : subs.overheat) ?? null;
  const rawPct =
    (pack && pack.raw_pct != null ? pack.raw_pct : fac.overheat_raw_pct) ?? null;
  const scale =
    (pack && pack.scale != null
      ? pack.scale
      : it.overheat_scale != null
        ? it.overheat_scale
        : fac.overheat_scale) ?? null;
  const level = (pack && pack.level) || null;
  const paperHr = it.paper_hard_reject;
  const paperReason = it.paper_reject_reason || (pack && pack.reason) || null;
  const mom5 = fac.momentum_5d ?? (pack && pack.mom5) ?? null;
  const mom3 = fac.momentum_3d ?? (pack && pack.mom3) ?? null;
  if (
    sub == null &&
    rawPct == null &&
    scale == null &&
    !paperHr &&
    mom5 == null
  ) {
    return {};
  }
  return {
    overheat: {
      sub_score: sub,
      raw_pct: rawPct,
      scale,
      level,
      momentum_5d: mom5,
      momentum_3d: mom3,
      paper_hard_reject: paperHr || false,
      paper_reject_reason: paperReason,
      predicted_score_overheat_scaled: it.predicted_score_overheat_scaled ?? null,
    },
  };
}

/** overheat：短期过热（heuristic 子分 + 纸面闸 tip）。 */
export function formatOverheatSection(raw) {
  const pack = raw && raw.overheat;
  if (!pack || typeof pack !== "object") return "";
  const lines = [];
  if (pack.sub_score != null && Number.isFinite(Number(pack.sub_score))) {
    lines.push(`子分 ${Number(pack.sub_score).toFixed(1)}（权重约 0.03 · 越低越热）`);
  }
  if (pack.raw_pct != null && Number.isFinite(Number(pack.raw_pct))) {
    lines.push(`过热幅度 ${Number(pack.raw_pct).toFixed(2)}%`);
  }
  if (pack.momentum_5d != null && Number.isFinite(Number(pack.momentum_5d))) {
    lines.push(`mom5 ${Number(pack.momentum_5d).toFixed(2)}%`);
  }
  if (pack.momentum_3d != null && Number.isFinite(Number(pack.momentum_3d))) {
    lines.push(`mom3 ${Number(pack.momentum_3d).toFixed(2)}%`);
  }
  if (pack.scale != null && Number.isFinite(Number(pack.scale))) {
    lines.push(`软折扣 ×${Number(pack.scale).toFixed(2)}`);
  }
  if (
    pack.predicted_score_overheat_scaled != null &&
    Number.isFinite(Number(pack.predicted_score_overheat_scaled))
  ) {
    lines.push(
      `ŷ×过热 ${Number(pack.predicted_score_overheat_scaled).toFixed(3)}%（对照列）`
    );
  }
  if (pack.paper_hard_reject) {
    lines.push(
      `纸面禁买：${pack.paper_reject_reason || pack.level || "过热追高"}`
    );
  } else if (pack.level && pack.level !== "none") {
    lines.push(`等级 ${pack.level}`);
  }
  if (!lines.length) return "";
  return (
    `<div class="score-sentiment-section">` +
    `<div class="score-section-title">短期过热 · overheat</div>` +
    lines
      .map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`)
      .join("") +
    `</div>`
  );
}

/** tail_anomaly 因子：尾盘放量 + 价格斜率（进 ŷ，权重低）。 */
export function formatTailAnomalySection(raw) {
  const pack = raw && raw.tail_anomaly;
  if (!pack || typeof pack !== "object") return "";
  const sub = pack.sub_score;
  const tvr = pack.tail_volume_ratio;
  const slope = pack.tail_price_slope_pct;
  if (sub == null && tvr == null && slope == null) return "";
  const lines = [];
  if (sub != null && Number.isFinite(Number(sub))) {
    lines.push(`子分 ${Number(sub).toFixed(1)}（权重 0.02）`);
  }
  if (tvr != null && Number.isFinite(Number(tvr))) {
    const pct = (Number(tvr) * 100).toFixed(1);
    lines.push(`尾盘量比 ${pct}%`);
  }
  if (slope != null && Number.isFinite(Number(slope))) {
    const s = Number(slope);
    lines.push(`尾盘斜率 ${s > 0 ? "+" : ""}${s.toFixed(2)}%`);
  }
  if (!lines.length) return "";
  const code = String(raw.stock_code || raw.code || "").trim();
  const chartHtml = code
    ? `<div class="score-tail-chart-wrap">` +
      `<canvas class="score-tail-chart" data-tail-code="${escapeText(code)}" width="280" height="72" aria-label="尾盘分钟线"></canvas>` +
      `<span class="score-tail-chart-hint">加载分钟线…</span>` +
      `</div>`
    : "";
  return (
    `<div class="score-sentiment-section score-tail-anomaly-section">` +
    `<div class="score-section-title">尾盘异常 · tail_anomaly</div>` +
    `<div class="score-sentiment-lead">分钟微观 · 进 ŷ · 非 prior</div>` +
    lines.map((b) => `<div class="score-sentiment-line">${escapeText(b)}</div>`).join("") +
    chartHtml +
    `</div>`
  );
}

/** 仅系数表（无分项拆解时回退）。opts.key=tau → ŷ_oc β。 */
export function formatFactorWeightsSection(raw, opts = {}) {
  const key = opts.key || "eod";
  if (key === "tau") {
    const expl = raw && (raw.formula_terms_tau || raw.score_formula_terms_tau);
    if (expl && Array.isArray(expl.terms) && expl.terms.length) return "";
    const coefs = raw && raw.factor_coefficients_tau;
    if (!coefs || typeof coefs !== "object") return "";
    const keys = Object.keys(coefs)
      .filter((k) => coefs[k] != null && Number.isFinite(Number(coefs[k])))
      .sort(
        (a, b) =>
          Math.abs(Number(coefs[b] || 0)) - Math.abs(Number(coefs[a] || 0)) ||
          String(a).localeCompare(String(b))
      );
    if (!keys.length) return "";
    const rows = keys
      .slice(0, 14)
      .map((k) => {
        const v = Number(coefs[k]);
        const name = FACTOR_LABELS[k] || TAU_FEAT_LABELS[k] || k;
        return (
          `<tr><td class="score-fw-name" title="${escapeText(k)}">${escapeText(name)}</td>` +
          `<td class="score-fw-val num ${signCls(v)}">${escapeText(fmtSigned(v, 4))}</td></tr>`
        );
      })
      .join("");
    return (
      `<div class="score-factors-section">` +
      `<div class="score-section-title">ŷ_oc 因子系数 β</div>` +
      `<table class="score-factor-weights"><tbody>${rows}</tbody></table>` +
      `</div>`
    );
  }
  if (raw && (raw.formula_terms || raw.score_formula_terms)) {
    const expl = raw.formula_terms || raw.score_formula_terms;
    if (expl && Array.isArray(expl.terms) && expl.terms.length) return "";
  }
  const coefs = raw && (raw.factor_coefficients || raw.coefficients);
  const fw = raw && raw.factor_weights;
  const useCoefs = coefs && typeof coefs === "object" && Object.keys(coefs).length > 0;
  const map = useCoefs ? coefs : fw;
  if (!map || typeof map !== "object") return "";
  const keys = Object.keys(map).sort(
    (a, b) =>
      Math.abs(Number(map[b] || 0)) - Math.abs(Number(map[a] || 0)) ||
      String(a).localeCompare(String(b))
  );
  if (!keys.length) return "";
  const src = String((raw && raw.weight_source) || "").trim();
  const label = raw && raw.cluster_label ? String(raw.cluster_label) : "";
  const title = useCoefs
    ? src.startsWith("cluster:") || label
      ? `因子系数 β${label ? ` · ${label}` : ""}`
      : "因子系数 β"
    : src.startsWith("cluster:") || label
      ? `展示权(|β|)${label ? ` · ${label}` : ""}`
      : "展示权(|β|)";
  const rows = keys
    .slice(0, 12)
    .map((k) => {
      const v = Number(map[k]);
      const txt = Number.isFinite(v)
        ? useCoefs
          ? fmtSigned(v, 4)
          : v.toFixed(2)
        : String(map[k] ?? "—");
      const name = FACTOR_LABELS[k] || k;
      return (
        `<tr><td class="score-fw-name" title="${escapeText(k)}">${escapeText(name)}</td>` +
        `<td class="score-fw-val num ${signCls(v)}">${escapeText(txt)}</td></tr>`
      );
    })
    .join("");
  return (
    `<div class="score-factors-section">` +
    `<div class="score-section-title">${escapeText(title)}</div>` +
    `<table class="score-factor-weights"><tbody>${rows}</tbody></table>` +
    `</div>`
  );
}

function formatReasonsSection(reasons) {
  const list = Array.isArray(reasons) ? reasons.slice(0, 5) : [];
  if (!list.length) return "";
  let html =
    `<div class="score-reasons-section">` +
    `<div class="score-section-title">评分理由</div>` +
    `<ul class="score-reasons">`;
  list.forEach((rsn) => {
    let cls = "neutral";
    if (/强于|高于|上升|增加|优秀|良好|高/.test(rsn)) cls = "pos";
    else if (/弱于|低于|下降|减少|较差|低/.test(rsn)) cls = "neg";
    html += `<li class="${cls}">${escapeText(rsn)}</li>`;
  });
  if (Array.isArray(reasons) && reasons.length > 5) {
    html += `<li class="neutral score-reasons-more">另有 ${reasons.length - 5} 条…</li>`;
  }
  html += "</ul></div>";
  return html;
}

const T0_FEAT_LABELS = {
  gap_pct: "跳空 %",
  yclose_loc: "昨收位置",
  mom3_pct: "近3日动量 %",
  gap_atr: "gap / ATR",
  atr_pct: "ATR %",
};

/** 做 T dual_y 方向分悬浮（ŷ 百分点，≠ 旧 signal ±1 分）。 */
export function formatT0DirectionDetail(raw) {
  const score = Number(raw && raw.direction_score);
  const scoreTxt = Number.isFinite(score) ? `${fmtSigned(score, 2)}%` : "—";
  const dir = String((raw && raw.direction) || "");
  const dirLabel =
    dir === "sell_then_buy" ? "反 T" : dir === "buy_then_sell" ? "正 T" : dir || "—";
  const enterRaw =
    raw && raw.y_tau_enter != null
      ? Number(raw.y_tau_enter)
      : raw && raw.dir_enter != null
        ? Number(raw.dir_enter)
        : 0;
  const enter = Number.isFinite(enterRaw) ? enterRaw : 0;
  const reason = String((raw && raw.direction_reason) || "").trim();
  let decision = "未入场 / 跳过";
  if (reason) decision = reason;
  else if (Number.isFinite(score)) {
    if (Math.abs(score) < enter) decision = `|y_oc|<${enter}% 横盘跳过`;
    else if (dir === "sell_then_buy" || dir === "buy_then_sell") decision = dirLabel;
    else if (score >= enter) decision = "正 T（依 τ 映射）";
    else if (score <= -enter) decision = "反 T（依 τ 映射）";
  } else if (dir === "sell_then_buy" || dir === "buy_then_sell") {
    decision = dirLabel;
  }
  let html = '<div class="score-detail">';
  html +=
    `<div class="score-hero">` +
    `<div class="score-hero-label">做 T · y_oc（开→收）</div>` +
    `<div class="score-hero-value ${signCls(score)}">${escapeText(scoreTxt)}</div>` +
    `<div class="score-hero-hint">收益百分点 · 与表列 ŷ% 同口径 · dual_y 主方向</div>` +
    `<div class="score-hero-semantics">` +
    `语义：τ 定正/反 T；ŷ_oc / ŷ_τ30 / ŷ_τ60 / ŷ_τ90 过入场；选腿仅收盘带宽` +
    `</div>` +
    `<div class="score-hero-gate">门槛 ±${escapeText(String(enter))}% · ${escapeText(
      decision
    )}</div>` +
    `</div>`;

  const feats = (raw && raw.features) || (raw && raw.direction_features) || {};
  const scores = (raw && raw.scores) || {};
  const yKeys = [
    ["y_eod", "y_oo"],
    ["y_tau", "y_oc"],
    ["y_trade", "y_trade"],
    ["y_on", "y_co"],
    ["y_nowcast", "nowcast"],
  ];
  const yRows = yKeys
    .map(([k, label]) => {
      const v = feats[k] ?? scores[k];
      if (v == null || v === "") return "";
      const n = Number(v);
      const txt = Number.isFinite(n) ? `${fmtSigned(n, 3)}%` : String(v);
      return (
        `<div class="score-row">` +
        `<span class="k">${escapeText(label)}</span>` +
        `<span class="v ${signCls(n)}">${escapeText(txt)}</span></div>`
      );
    })
    .filter(Boolean)
    .join("");
  if (yRows) {
    html +=
      `<div class="score-section"><div class="score-section-title">dual_y ŷ</div>${yRows}</div>`;
  }

  const featKeys = ["gap_pct", "yclose_loc", "mom3_pct", "gap_atr", "atr_pct"];
  const featRows = featKeys
    .map((k) => {
      const v = feats[k];
      if (v == null || v === "") return "";
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      const unit = k.endsWith("_pct") || k === "atr_pct" ? "%" : "";
      return (
        `<div class="score-row"><span class="k">${escapeText(k)}</span>` +
        `<span class="v">${escapeText(txt)}${unit}</span></div>`
      );
    })
    .filter(Boolean)
    .join("");
  if (featRows) {
    html +=
      `<div class="score-section"><div class="score-section-title">开盘特征（对照）</div>${featRows}</div>`;
  }

  const code = (raw && (raw.stock_code || raw.code)) || "";
  const name = (raw && raw.stock_name) || "";
  const date = (raw && raw.date) || "";
  if (code || name || date) {
    html +=
      `<div class="score-section"><div class="score-section-title">样本</div>` +
      `<div class="score-row"><span class="k">标的</span><span class="v">${escapeText(
        [name, code].filter(Boolean).join(" ")
      )}</span></div>` +
      (date
        ? `<div class="score-row"><span class="k">日</span><span class="v">${escapeText(
            date
          )}</span></div>`
        : "") +
      `</div>`;
  }
  html += "</div>";
  return html;
}

export function createScoreTooltipController() {
  let tipEl = null;
  let tipAnchor = null;
  let docClickClose = null;
  let docKeyClose = null;
  let hideTimer = null;

  function cancelHide() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHide(delayMs = 160) {
    cancelHide();
    hideTimer = setTimeout(() => {
      hideTimer = null;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      hide();
    }, delayMs);
  }

  function clearDocClosers() {
    if (docClickClose) {
      document.removeEventListener("click", docClickClose, true);
      docClickClose = null;
    }
    if (docKeyClose) {
      document.removeEventListener("keydown", docKeyClose, true);
      docKeyClose = null;
    }
  }

  function hide() {
    cancelHide();
    clearDocClosers();
    if (tipEl) {
      tipEl.remove();
      tipEl = null;
    }
    tipAnchor = null;
  }

  function attachDismiss(tip, cell, { sticky = false } = {}) {
    tip.addEventListener("pointerenter", () => {
      cancelHide();
    });
    tip.addEventListener("pointerleave", (e) => {
      if (tip.dataset.sticky === "1") return;
      const to = e.relatedTarget;
      if (to && cell && cell.contains(to)) return;
      scheduleHide();
    });

    if (!sticky) return;

    tip.dataset.sticky = "1";
    tip.classList.add("is-sticky");
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "score-tooltip-close";
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      hide();
    });
    tip.prepend(closeBtn);

    docClickClose = (ev) => {
      if (!tipEl || tipEl !== tip) return;
      if (tip.contains(ev.target)) return;
      // 同单元格点击由 bindHost / 业务侧 toggle，这里不抢
      if (cell && cell.contains(ev.target)) return;
      hide();
    };
    docKeyClose = (ev) => {
      if (ev.key === "Escape") hide();
    };
    setTimeout(() => {
      document.addEventListener("click", docClickClose, true);
      document.addEventListener("keydown", docKeyClose, true);
    }, 0);
  }

  function showPlain(anchor, text) {
    const msg = String(text || "").trim();
    if (!anchor || !msg) return;
    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip plain-hover-tip";
    tip.innerHTML = `<div class="plain-hover-tip-body">${escapeText(msg)}</div>`;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = anchor;
    place(tip, anchor);
  }

  function showHtml(anchor, html, { className = "score-tooltip", onShow = null } = {}) {
    const body = String(html || "").trim();
    if (!anchor || !body) return;
    hide();
    const tip = document.createElement("div");
    tip.className = className;
    tip.innerHTML = body;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = anchor;
    place(tip, anchor);
    attachDismiss(tip, anchor, { sticky: false });
    if (typeof onShow === "function") {
      try {
        onShow(anchor, tip);
      } catch (_) {
        /* tip chart mount optional */
      }
    }
  }

  function bindAttrTip(
    host,
    {
      selector = "[data-tip-html]",
      htmlAttr = "data-tip-html",
      className = "score-tooltip plain-hover-tip",
      buildHtml = null,
      onShow = null,
      wireKey = "",
    } = {}
  ) {
    if (!host) return;
    const key = String(wireKey || selector || "default");
    if (!host.__attrTipKeys) host.__attrTipKeys = new Set();
    if (host.__attrTipKeys.has(key)) return;
    host.__attrTipKeys.add(key);
    // 兼容旧「单次绑定」检测
    host.dataset.attrTipWired = "1";
    host.addEventListener("pointerover", (e) => {
      const el = e.target.closest(selector);
      if (!el || !host.contains(el)) return;
      cancelHide();
      if (tipAnchor === el && tipEl) return;
      let html = "";
      if (typeof buildHtml === "function") {
        html = buildHtml(el) || "";
      } else {
        html = el.getAttribute(htmlAttr) || "";
      }
      if (!html) return;
      showHtml(el, html, { className, onShow });
    });
    host.addEventListener("pointerout", (e) => {
      const from = e.target.closest(selector);
      if (!from) return;
      const to = e.relatedTarget;
      if (to && from.contains(to)) return;
      if (tipEl && to && tipEl.contains(to)) return;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      scheduleHide();
    });
  }

  function resolveScoreTipMode(cell) {
    if (!cell) return "ranking";
    const tip = String(cell.dataset.scoreTip || "").trim().toLowerCase();
    if (tip === "eod") return "eod";
    if (tip === "nowcast" || tip === "nc") return "nowcast";
    if (tip === "nc_oc") return "nc_oc";
    if (tip === "tau" || tip === "rem") return "tau";
    if (tip === "r" || tip === "τc" || tip === "tc" || tip === "pc") return "r";
    if (tip === "t30" || tip === "τ30" || tip === "yt30") return "t30";
    if (tip === "t60" || tip === "τ60" || tip === "yt60") return "t60";
    if (tip === "t90" || tip === "τ90" || tip === "yt90") return "t90";
    if (tip === "hl" || tip === "path" || tip === "yhl") return "hl";
    if (tip === "tw" || tip === "τw" || tip === "ytw") return "tw";
    if (tip === "rtau" || tip === "r_tau" || tip === "rhat") return "rtau";
    if (tip === "on") return "on";
    if (tip === "ranking" || tip === "trade" || tip === "score") return "ranking";
    if (cell.classList.contains("watching-score-eod")) return "eod";
    if (cell.classList.contains("watching-score-nowcast")) return "nowcast";
    if (cell.classList.contains("watching-score-tau")) return "tau";
    if (
      cell.classList.contains("paper-t0-col-y-rtau") ||
      cell.classList.contains("paper-t0-col-rtau")
    )
      return "rtau";
    if (
      cell.classList.contains("paper-t0-col-y-t30") ||
      cell.classList.contains("paper-t0-col-yt30")
    )
      return "t30";
    if (
      cell.classList.contains("paper-t0-col-y-t60") ||
      cell.classList.contains("paper-t0-col-yt60")
    )
      return "t60";
    if (
      cell.classList.contains("paper-t0-col-y-t90") ||
      cell.classList.contains("paper-t0-col-yt90")
    )
      return "t90";
    if (
      cell.classList.contains("paper-t0-col-y-hl") ||
      cell.classList.contains("paper-t0-col-yhl")
    )
      return "hl";
    if (
      cell.classList.contains("paper-t0-col-y-tw") ||
      cell.classList.contains("paper-t0-col-ytw")
    )
      return "tw";
    if (cell.classList.contains("watching-score-on")) return "on";
    return "ranking";
  }

  function showCompactScoreTip(cell, html, { sticky = false } = {}) {
    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip";
    tip.innerHTML = `<div class="score-detail">${html}</div>`;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = cell;
    place(tip, cell);
    attachDismiss(tip, cell, { sticky });
  }

  function show(cell, { sticky = false } = {}) {
    let raw;
    try {
      raw = JSON.parse(cell.dataset.scoreDetail || "{}");
    } catch (_) {
      raw = {};
    }
    if (raw && raw.kind === "t0_direction") {
      const html = formatT0DirectionDetail(raw);
      hide();
      const tip = document.createElement("div");
      tip.className = "score-tooltip";
      tip.innerHTML = html;
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
      tipEl = tip;
      tipAnchor = cell;
      place(tip, cell);
      attachDismiss(tip, cell, { sticky });
      return;
    }
    // 兼容旧字段名
    if (!raw.formula_terms && raw.score_formula_terms) {
      raw.formula_terms = raw.score_formula_terms;
    }

    const tipMode = resolveScoreTipMode(cell);

    if (tipMode === "eod") {
      const parts = [
        formatCompactEodTip(raw),
        formatFormulaTermsSection(raw),
        formatFactorWeightsSection(raw),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "nowcast") {
      let ncHtml = formatNowcastSection(raw);
      if (!ncHtml) {
        ncHtml =
          `<div class="score-layer score-layer-nowcast is-shadow">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">nowcast（nc）</div>` +
          `<div class="score-hero-value score-flat">—</div>` +
          `</div>` +
          `<div class="score-hero-hint">${escapeText(Y_NC_TITLE)}</div>` +
          `</div>`;
      }
      const html = `<div class="score-detail">${ncHtml}</div>`;
      hide();
      const tip = document.createElement("div");
      tip.className = "score-tooltip";
      tip.innerHTML = html;
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
      tipEl = tip;
      tipAnchor = cell;
      place(tip, cell);
      attachDismiss(tip, cell, { sticky });
      return;
    }

    if (tipMode === "nc_oc") {
      let ncOcHtml = formatNcOcSection(raw);
      if (!ncOcHtml) {
        ncOcHtml =
          `<div class="score-layer score-layer-nowcast is-shadow">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">nowcast oc</div>` +
          `<div class="score-hero-value score-flat">—</div>` +
          `</div>` +
          `<div class="score-hero-hint">${escapeText(Y_NC_OC_TITLE)} · 需 nc 与 gap</div>` +
          `</div>`;
      }
      const html = `<div class="score-detail">${ncOcHtml}</div>`;
      hide();
      const tip = document.createElement("div");
      tip.className = "score-tooltip";
      tip.innerHTML = html;
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
      tipEl = tip;
      tipAnchor = cell;
      place(tip, cell);
      attachDismiss(tip, cell, { sticky });
      return;
    }

    if (tipMode === "tau") {
      const parts = [
        formatCompactTauTip(raw),
        formatFormulaTermsSection(raw, { key: "tau" }),
        formatFactorWeightsSection(raw, { key: "tau" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "r") {
      const parts = [
        formatCompactRTip(raw),
        formatFormulaTermsSection(raw, { key: "r" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "t30") {
      const parts = [
        formatCompactT30Tip(raw),
        formatFormulaTermsSection(raw, { key: "t30" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "t60") {
      const parts = [
        formatCompactT60Tip(raw),
        formatFormulaTermsSection(raw, { key: "t60" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "t90") {
      const parts = [
        formatCompactT90Tip(raw),
        formatFormulaTermsSection(raw, { key: "t90" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "hl") {
      const parts = [
        formatCompactHlTip(raw),
        formatFormulaTermsSection(raw, { key: "path" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "tw") {
      const parts = [formatCompactTWTip(raw)].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "rtau") {
      const parts = [formatCompactRtauTip(raw)].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "on") {
      const parts = [
        formatCompactOnTip(raw),
        formatFormulaTermsSection(raw, { key: "on" }),
      ].filter(Boolean);
      showCompactScoreTip(cell, parts.join(""), { sticky });
      return;
    }

    if (tipMode === "ranking") {
      let blendHtml = formatBlendScoreSection(raw);
      if (!blendHtml) {
        const ranked = resolveRankingScore(raw);
        const val = ranked == null ? "—" : `${fmtSigned(ranked, 2)}%`;
        blendHtml =
          `<div class="score-layer score-layer-blend">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">ranking</div>` +
          `<div class="score-hero-value ${signCls(ranked)}">${escapeText(val)}</div>` +
          `</div>` +
          `<div class="score-hero-hint">w·ŷ_oo + w·(ŷ_oc∘w_co·ŷ_co) · 排序/卖门槛</div>` +
          `</div>`;
      }
      showCompactScoreTip(cell, blendHtml, { sticky });
      return;
    }

    const formula = raw.formula || "";
    const reasons = raw.reasons || [];
    const hardReject = raw.hard_reject;
    const rejectReason = raw.reject_reason || "";
    const hasWeight = !!(
      raw.weight_source ||
      raw.cluster_mode ||
      raw.cluster_label ||
      raw.return_model_source
    );
    const hasTerms =
      raw.formula_terms &&
      Array.isArray(raw.formula_terms.terms) &&
      raw.formula_terms.terms.length > 0;
    const hasCoefs =
      (raw.factor_coefficients &&
        typeof raw.factor_coefficients === "object" &&
        Object.keys(raw.factor_coefficients).length > 0) ||
      (raw.coefficients &&
        typeof raw.coefficients === "object" &&
        Object.keys(raw.coefficients).length > 0);
    const hasFactorWeights =
      raw.factor_weights &&
      typeof raw.factor_weights === "object" &&
      Object.keys(raw.factor_weights).length > 0;
    const hasTau =
      (raw.predicted_score_tau != null &&
        Number.isFinite(Number(raw.predicted_score_tau))) ||
      (raw.score_rem != null && Number.isFinite(Number(raw.score_rem))) ||
      (raw.predicted_score_rem != null &&
        Number.isFinite(Number(raw.predicted_score_rem))) ||
      (raw.gap_pct != null && Number.isFinite(Number(raw.gap_pct))) ||
      !!(raw.event_prior && typeof raw.event_prior === "object") ||
      !!raw.market_prior_active ||
      !!(raw.market_prior_warnings && raw.market_prior_warnings.length) ||
      !!(raw.tail_anomaly && typeof raw.tail_anomaly === "object") ||
      !!(raw.overheat && typeof raw.overheat === "object") ||
      !!raw.paper_hard_reject ||
      !!(raw.dual_score_fusion && String(raw.dual_score_fusion).trim());

    // 空对象（JSON 解析失败）才跳过；未打分也展示 ŷ_oo/ŷ_oc 占位，避免悬停无反应
    if (
      !formula &&
      !reasons.length &&
      !hardReject &&
      !hasWeight &&
      !hasTerms &&
      !hasCoefs &&
      !hasFactorWeights &&
      !hasTau &&
      resolveYhat(raw) == null &&
      raw.sentiment_include_in_score == null &&
      !(Array.isArray(raw.risk_hints) && raw.risk_hints.length) &&
      !(Array.isArray(raw.warnings) && raw.warnings.length) &&
      !raw.fundamentals_pit &&
      !raw.index_meta &&
      !raw.fundamentals_depth &&
      !(raw && Object.keys(raw).length)
    )
      return;

    let html = '<div class="score-detail">';
    // ranking 列：先融合组成，再拆 ŷ_oc / ŷ_oo（g 只在 eod 列，nowcast 只在 nowcast 列）
    html += formatBlendScoreSection(raw);
    html += formatRemScoreSection(raw);
    html += formatFormulaTermsSection(raw, { key: "tau" });
    html += formatFactorWeightsSection(raw, { key: "tau" });
    html += formatScoreHero(raw);
    html += formatFormulaTermsSection(raw);
    html += formatFactorWeightsSection(raw);
    if (formula && !hasTerms) {
      html += `<div class="score-formula-section">
        <div class="score-section-title">ŷ_oo 公式</div>
        <div class="score-formula">${escapeText(formula)}</div>
      </div>`;
    }
    html += formatWeightSourceNote(raw);
    html += formatFeatureIsoSection(raw);
    html += formatSentimentGateSection(raw);
    html += formatMarketPriorSection(raw);
    html += formatTailAnomalySection(raw);
    html += formatOverheatSection(raw);
    if (hardReject && rejectReason) {
      html += `<div class="score-detail-reject">
        <span class="score-detail-reject-icon">⚠</span>
        <span class="score-detail-reject-text">${escapeText(rejectReason)}</span>
      </div>`;
    }
    html += formatReasonsSection(reasons);
    html += "</div>";

    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip";
    tip.innerHTML = html;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = cell;
    place(tip, cell);
    attachDismiss(tip, cell, { sticky });
    afterTipMount(tip);
  }

  function afterTipMount(tip) {
    hydrateTailAnomalyCharts(tip).catch(() => {});
  }

  function place(tip, anchor) {
    const pad = 8;
    const maxH = Math.max(120, window.innerHeight - pad * 2);
    tip.style.maxHeight = `${maxH}px`;

    const rect = anchor.getBoundingClientRect();
    let tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(pad, Math.min(left, window.innerWidth - tipRect.width - pad));

    // Prefer below; flip above if it fits; else pin to top and let tip scroll.
    let top = rect.bottom + 6;
    tipRect = tip.getBoundingClientRect();
    if (top + tipRect.height > window.innerHeight - pad) {
      const above = rect.top - tipRect.height - 6;
      top = above >= pad ? above : pad;
    }
    const h = tip.offsetHeight || tipRect.height;
    top = Math.max(pad, Math.min(top, window.innerHeight - h - pad));

    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }

  function bindHost(host, { scoreSelector = "[data-score-detail]" } = {}) {
    if (!host || host.dataset.scoreTipWired === "1") return;
    host.dataset.scoreTipWired = "1";

    host.addEventListener("click", (e) => {
      const cell = e.target.closest(scoreSelector);
      if (!cell || !host.contains(cell)) return;
      e.preventDefault();
      e.stopPropagation();
      cancelHide();
      if (tipAnchor === cell && tipEl && tipEl.dataset.sticky === "1") {
        hide();
        return;
      }
      show(cell, { sticky: true });
    });
    host.addEventListener("pointerover", (e) => {
      const cell = e.target.closest(scoreSelector);
      if (!cell || !host.contains(cell)) return;
      cancelHide();
      if (tipEl && tipEl.dataset.sticky === "1") return;
      if (tipAnchor === cell && tipEl) return;
      show(cell, { sticky: false });
    });
    host.addEventListener("pointerout", (e) => {
      const from = e.target.closest(scoreSelector);
      if (!from) return;
      const to = e.relatedTarget;
      if (to && from.contains(to)) return;
      if (tipEl && to && tipEl.contains(to)) return;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      scheduleHide();
    });
  }

  return {
    show,
    showPlain,
    showHtml,
    hide,
    bindHost,
    bindAttrTip,
    get tipEl() {
      return tipEl;
    },
    get tipAnchor() {
      return tipAnchor;
    },
  };
}
