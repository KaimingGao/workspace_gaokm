/** 评分悬浮注释（交易执行持仓 / 数据中心观察表共用）。
 * 主叙事：收益分 ŷ + 因子系数 β（可正可负），非规则权重。
 */

import {
  escapeText,
  fuseOrthogonalTrade,
  liftTauVsPrevClose,
  resolveEodScore,
  resolveNowcastScore,
  resolveOnScore,
  resolveTauScore,
  resolveTradeScore,
} from "./paper/fmt.js?v=p1226";
import { renderYPathVizHtml } from "./y_path_viz.js?v=p1169";
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
  size: "规模",
  earnings_yield: "盈利收益率",
  growth: "成长",
  dividend: "股息",
  money_flow: "资金流",
  amihud: "Amihud",
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

function resolveWeights(raw) {
  const w = (raw && raw.dual_score_weights) || {};
  let we = Number(w.w_eod);
  let wt = Number(w.w_tau);
  if (!Number.isFinite(we)) we = 0.5;
  if (!Number.isFinite(wt)) wt = 0.5;
  return { w_eod: we, w_tau: wt };
}

function resolveBlend(raw) {
  // 与表列同源：ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)
  const trade = resolveTradeScore(raw);
  if (trade != null) return trade;
  const eod = resolveEodScore(raw);
  const yt = resolveTau(raw);
  const fused = fuseOrthogonalTrade(raw, eod, liftTauVsPrevClose(raw, yt));
  if (fused != null) return fused;
  if (eod != null) return eod;
  return resolveYhat(raw);
}

const TAU_FEAT_LABELS = {
  gap_pct: "跳空 %",
  open_gap: "开盘缺口",
  sector_gap_breadth: "同业缺口广度",
  theme_day: "主题日",
  gap_atr: "缺口 / ATR",
  gap_vs_sector: "行业相对缺口",
  ret_open_to_tau: "开盘→τ 收益 %",
};

/** ŷ_EOD：表列 score · 含因子组成。 */
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
      `<div class="score-hero-label">ŷ_EOD · 隔夜主轴</div>` +
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
    gate = `<div class="score-hero-gate${below ? " is-warn" : ""}">买入门槛 ŷ_EOD ≥ ${escapeText(
      Number.isFinite(floor) ? `${floor}%` : String(floor)
    )}${below ? " · 当前低于门槛" : ""}</div>`;
  }
  return (
    `<div class="score-layer score-layer-eod">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_EOD · 隔夜主轴</div>` +
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
  if (t && t.label) return t.label;
  const k = t && t.key;
  if (key === "on" && k && ON_FEAT_META[k] && ON_FEAT_META[k].label) {
    return ON_FEAT_META[k].label;
  }
  return FACTOR_LABELS[k] || TAU_FEAT_LABELS[k] || k || "—";
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
    gate = `<div class="score-hero-gate${below ? " is-warn" : ""}">买入门槛 ŷ_EOD ≥ ${escapeText(
      Number.isFinite(floor) ? `${floor}%` : String(floor)
    )}${below ? " · 当前低于门槛" : ""}</div>`;
  }
  return (
    `<div class="score-layer score-layer-eod">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">y_eod</div>` +
    `<div class="score-hero-value ${signCls(y)}">${escapeText(val)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">ŷ_EOD · T−1 因子 · α+Σβ·z</div>` +
    gate +
    `</div>`
  );
}

/** y_τ 列：只展示头值与口径，不展开全栈。 */
function formatCompactTauTip(raw) {
  const shown = resolveTauScore(raw);
  const val = shown == null ? "—" : `${fmtSigned(shown, 2)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  return (
    `<div class="score-layer score-layer-tau">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">y_τ</div>` +
    `<div class="score-hero-value ${signCls(shown)}">${escapeText(val)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">τ=${escapeText(tau)} · τ→收盘 · 昨收口径</div>` +
    `</div>`
  );
}

/** y_on 列：旁路 open 链头。 */
function formatCompactOnTip(raw) {
  const on = resolveOnScore(raw);
  const val = on == null ? "—" : `${fmtSigned(on, 2)}%`;
  const ySpec =
    (raw && raw.y_spec_on && raw.y_spec_on.formula) ||
    (raw && raw.on_y_spec) ||
    "open[T+1]/open[T]−1";
  return (
    `<div class="score-layer score-layer-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">y_on</div>` +
    `<div class="score-hero-value ${signCls(on)}">${escapeText(val)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(String(ySpec))} · 旁路 · 不进排序</div>` +
    `</div>`
  );
}

/** ŷ_τ：独立预估 T 收相对 T 开。 */
export function formatRemScoreSection(raw) {
  const rem = resolveTau(raw);
  const gap = raw && raw.gap_pct;
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
  const body = !hasRem
    ? `<div class="score-hero-hint">未产出（需 rem 模型）</div>`
    : hasTauTerms
      ? `<div class="score-hero-hint">组成见表「ŷ_τ 组成」</div>`
      : featRows.length
        ? `<div class="score-layer-compose">${featRows.join("")}</div>`
        : `<div class="score-hero-hint">τ=${escapeText(tau)} · y=${escapeText(String(ySpec))}</div>`;

  return (
    `<div class="score-layer score-layer-tau">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_τ · T收 / T开（rem 头）</div>` +
    `<div class="score-hero-value ${signCls(rem)}">${escapeText(remTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">τ=${escapeText(tau)} · ${escapeText(String(ySpec))} · 买入闸</div>` +
    body +
    (warn ? `<div class="score-hero-hint">${escapeText(warn)}</div>` : "") +
    `</div>`
  );
}

function resolveCalNum(raw, key) {
  if (!raw || raw[key] == null || raw[key] === "") return null;
  const n = Number(raw[key]);
  return Number.isFinite(n) ? n : null;
}

function calibrationActive(raw) {
  if (raw && raw.score_calibration_applied) return true;
  return resolveCalNum(raw, "predicted_score_cal") != null;
}

function calRowHtml(label, value) {
  if (value == null || !Number.isFinite(Number(value))) return "";
  const n = Number(value);
  return `<div class="score-layer-row"><span>${escapeText(
    label
  )}</span><span class="num ${signCls(n)}">${escapeText(
    fmtSigned(n, 3)
  )}%</span></div>`;
}

/** Δ = g − ŷ：正=历史上实现偏高（模型偏保守），负=幅度常被夸大。 */
function calDeltaRowHtml(raw, cal) {
  const a = Number(raw);
  const b = Number(cal);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return "";
  const d = b - a;
  const t =
    Math.abs(d) < 1e-9
      ? "0"
      : d > 0
        ? `+${Math.abs(d).toFixed(3)}`
        : `−${Math.abs(d).toFixed(3)}`;
  return `<div class="score-layer-row score-layer-row-delta"><span>Δ(g−ŷ)</span><span class="num ${signCls(
    d
  )}">${escapeText(t)}%</span></div>`;
}

/** eod 列 tip：g(ŷ_EOD) 对涨跌，不含缺口 / τ；对照列出原 ŷ_EOD。
 * @param {object} raw
 * @param {{ calOnly?: boolean }} [opts]
 */
export function formatCalibrationSection(raw, opts = {}) {
  if (!calibrationActive(raw)) return "";
  const calOnly = !!opts.calOnly;
  const yEod = resolveEodScore(raw);
  const yEodCal = resolveCalNum(raw, "predicted_score_cal");
  if (yEodCal == null) return "";
  const heroTxt = `${fmtSigned(yEodCal, 3)}%`;
  const rows = [];
  const yTxt =
    yEod == null || !Number.isFinite(Number(yEod))
      ? "—"
      : `${fmtSigned(Number(yEod), 3)}%`;
  rows.push(
    `<div class="score-layer-row"><span>原 ŷ_EOD</span><span class="num ${signCls(
      yEod
    )}">${escapeText(yTxt)}</span></div>`
  );
  rows.push(calRowHtml("g(ŷ_EOD)", yEodCal));
  rows.push(calDeltaRowHtml(yEod, yEodCal));
  const oorHint =
    raw && raw.score_calibration_eod_oor
      ? " · ŷ_EOD 落在拟合域外（端点钳制，对照弱）"
      : "";
  const softNote =
    raw && raw.score_calibration_note
      ? ` · ${String(raw.score_calibration_note).slice(0, 80)}`
      : "";
  const title = calOnly ? "eod · g(ŷ_EOD)" : "g(ŷ_EOD)";
  const hint = calOnly
    ? `对涨跌（现价对昨收）· 不含缺口 · 不进排序/闸/入簿 · Δ=g−原ŷ${oorHint}${softNote}`
    : `单调映射 · Δ=g−原ŷ_EOD · 不含缺口 · 排序/闸/入簿仍用原 ŷ${oorHint}${softNote}`;
  return (
    `<div class="score-layer score-layer-cal is-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">${escapeText(title)}</div>` +
    `<div class="score-hero-value ${signCls(yEodCal)}">${escapeText(heroTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(hint)}</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
    `</div>`
  );
}

/** nowcast 列 tip：Kalman 权昨收口径，对照 ŷ_trade，不进决策。 */
export function formatNowcastSection(raw) {
  const ncN = resolveNowcastScore(raw);
  if (ncN == null || !Number.isFinite(Number(ncN))) return "";
  const yEod = resolveEodScore(raw);
  const yt = resolveTau(raw);
  const ytCc = liftTauVsPrevClose(raw, yt);
  const k = raw && raw.nowcast_K != null ? Number(raw.nowcast_K) : null;
  const q = raw && raw.nowcast_q != null ? Number(raw.nowcast_q) : null;
  const asOf = raw && raw.nowcast_as_of ? String(raw.nowcast_as_of) : "";
  const blend = resolveBlend(raw);
  const rows = [];
  const eodN = yEod == null || yEod === "" ? null : Number(yEod);
  const priorN =
    raw && raw.nowcast_x_prior != null && Number.isFinite(Number(raw.nowcast_x_prior))
      ? Number(raw.nowcast_x_prior)
      : eodN;
  const tCcN = ytCc == null || ytCc === "" ? null : Number(ytCc);
  if (priorN != null && Number.isFinite(priorN)) {
    rows.push(
      `<div class="score-layer-row"><span>先验 ŷ_EOD</span><span class="num ${signCls(
        priorN
      )}">${escapeText(`${fmtSigned(priorN, 2)}%`)}</span></div>`
    );
  }
  if (tCcN != null && Number.isFinite(tCcN)) {
    rows.push(
      `<div class="score-layer-row"><span>ŷ_τ 昨收</span><span class="num ${signCls(
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
  if (blend != null && Number.isFinite(Number(blend))) {
    rows.push(
      `<div class="score-layer-row"><span>ŷ_trade</span><span class="num ${signCls(
        blend
      )}">${escapeText(`${fmtSigned(Number(blend), 2)}%`)}</span></div>`
    );
  }
  rows.push(
    `<div class="score-layer-row score-layer-row-total"><span>ŷ_nowcast</span><span class="num ${signCls(
      ncN
    )}">${escapeText(`${fmtSigned(ncN, 2)}%`)}</span></div>`
  );
  const bits = ["Kalman 权 · 现价对昨收 · 不进排序/闸/入簿"];
  if (asOf) bits.push(`@${asOf}`);
  if (q != null && Number.isFinite(q)) bits.push(`q=${q.toFixed(3)}`);
  if (
    priorN != null &&
    Number.isFinite(priorN) &&
    Math.abs(Number(ncN) - priorN) < 1e-4
  ) {
    bits.push("后验≈先验（未观测 ŷ_τ 或 K≈0）");
  }
  return (
    `<div class="score-layer score-layer-nowcast is-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">nowcast 对照</div>` +
    `<div class="score-hero-value ${signCls(ncN)}">${escapeText(
      `${fmtSigned(ncN, 2)}%`
    )}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(bits.join(" · "))}</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
    `</div>`
  );
}

/** 融合分：w·ŷ_EOD + w·(缺口∘ŷ_τ)，同为现价对昨收。 */
export function formatBlendScoreSection(raw) {
  const blend = resolveBlend(raw);
  const yEod = resolveEodScore(raw);
  const yt = resolveTau(raw);
  const ytCc = liftTauVsPrevClose(raw, yt);
  const { w_eod, w_tau } = resolveWeights(raw);
  const blendTxt = blend == null ? "—" : `${fmtSigned(blend, 2)}%`;
  const eodN = yEod == null || yEod === "" ? null : Number(yEod);
  const tN = yt == null || yt === "" ? null : Number(yt);
  const tCcN = ytCc == null || ytCc === "" ? null : Number(ytCc);
  const eodTxt = eodN == null || !Number.isFinite(eodN) ? "—" : `${fmtSigned(eodN, 2)}%`;
  const tTxt = tN == null || !Number.isFinite(tN) ? "—" : `${fmtSigned(tN, 2)}%`;
  const tCcTxt = tCcN == null || !Number.isFinite(tCcN) ? "—" : `${fmtSigned(tCcN, 2)}%`;
  const wTxt = `w_EOD=${Number(w_eod).toFixed(2)} · w_τ=${Number(w_tau).toFixed(2)}`;
  const wMode =
    raw && raw.dual_score_weights && raw.dual_score_weights.w_mode
      ? String(raw.dual_score_weights.w_mode)
      : "fixed";
  const hint = `${wTxt} · ${wMode} · 现价对昨收 · 表列 trade`;
  const head =
    raw && raw.dual_score_head != null
      ? String(raw.dual_score_head)
      : raw && raw.dual_score_single_head
        ? "single"
        : "";
  let headHint = "";
  const win = raw && raw.dual_score_window != null ? String(raw.dual_score_window) : "";
  const tauInTrade =
    raw && raw.dual_score_weights && raw.dual_score_weights.tau_in_trade;
  if (head === "single_eod" && tN != null && Number.isFinite(tN)) {
    headHint =
      win === "eod_next" || tauInTrade === false
        ? " · 收盘后 trade=ŷ_EOD（τ 仍对照，不进融合）"
        : " · 单头：trade=ŷ_EOD（τ 未进融合）";
  } else if (head === "single_eod") {
    headHint = " · 单头降级：仅 ŷ_EOD（缺 ŷ_τ）";
  } else if (head === "single_tau") {
    headHint = " · 单头降级：仅 ŷ_τ（缺 ŷ_EOD）";
  } else if (head === "blend") {
    headHint = " · 双头融合";
  }
  const cascade =
    raw && raw.predicted_score_tau_cascade != null
      ? Number(raw.predicted_score_tau_cascade)
      : null;
  const cascadeTxt =
    cascade == null || !Number.isFinite(cascade)
      ? null
      : `${fmtSigned(cascade, 3)}%`;
  const rows = [
    `<div class="score-layer-row"><span>ŷ_EOD</span><span class="num ${signCls(
      eodN
    )}">${escapeText(eodTxt)}</span></div>`,
    `<div class="score-layer-row"><span>ŷ_τ</span><span class="num ${signCls(
      tN
    )}">${escapeText(tTxt)}</span></div>`,
    `<div class="score-layer-row"><span>ŷ_τ 昨收</span><span class="num ${signCls(
      tCcN
    )}">${escapeText(tCcTxt)}</span></div>`,
    `<div class="score-layer-row score-layer-row-total"><span>ŷ_trade</span><span class="num ${signCls(
      blend
    )}">${escapeText(blendTxt)}</span></div>`,
  ];
  if (cascadeTxt) {
    rows.push(
      `<div class="score-layer-row"><span>ŷ_cascade·影</span><span class="num ${signCls(
        cascade
      )}">${escapeText(cascadeTxt)}</span></div>`
    );
  }
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
  const yPathHtml = renderYPathVizHtml(raw);
  return (
    `<div class="score-layer score-layer-blend${
      head.startsWith("single") || (yCheck && yCheck !== "ok") ? " is-warn" : ""
    }">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">ŷ_trade · 正交加权${
      head.startsWith("single") ? " · 单头" : ""
    }${yCheck && yCheck !== "ok" ? " · Y校验" : ""}</div>` +
    `<div class="score-hero-value ${signCls(blend)}">${escapeText(blendTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(hint + headHint)}</div>` +
    (yPathHtml ? yPathHtml : "") +
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
      : key === "on"
        ? raw && (raw.formula_terms_on || raw.score_formula_terms_on)
        : raw && (raw.formula_terms || raw.score_formula_terms);
  if (!expl || typeof expl !== "object") return "";
  const terms = Array.isArray(expl.terms) ? expl.terms : [];
  if (!terms.length && expl.intercept == null) return "";

  const title =
    key === "tau" ? "ŷ_τ 组成" : key === "on" ? "ŷ_ON 组成" : "ŷ_EOD 组成";
  const totalLabel =
    key === "tau" ? "合计 ŷ_τ" : key === "on" ? "合计 ŷ_ON" : "合计 ŷ_EOD";

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
    `<td class="score-ft-name">截距 α</td>` +
    `<td class="num">—</td>` +
    `<td class="num">—</td>` +
    `<td class="num ${signCls(expl.intercept)}">${escapeText(alpha)}</td>` +
    `</tr>` +
    rows +
    `<tr class="score-ft-total">` +
    `<td class="score-ft-name">${escapeText(totalLabel)}</td>` +
    `<td class="num">—</td>` +
    `<td class="num">—</td>` +
    `<td class="num ${signCls(expl.total)}">${escapeText(total)}%</td>` +
    `</tr>` +
    `</tbody></table>` +
    `<div class="score-formula-caption">β×z = 贡献；条长∝|贡献|</div>` +
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

/** 舆情先验（ŷ 外）旁路提示。 */
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
    `<div class="score-section-title">舆情先验</div>` +
    `<div class="score-sentiment-lead">先验旁路 · 不进 ŷ · 非因子</div>` +
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

/** 仅系数表（无分项拆解时回退）。opts.key=tau → ŷ_τ β。 */
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
      `<div class="score-section-title">ŷ_τ 因子系数 β</div>` +
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

/** 做 T 开盘方向分悬浮（≠ 选股 ŷ）。 */
export function formatT0DirectionDetail(raw) {
  const score = Number(raw && raw.direction_score);
  const scoreTxt = Number.isFinite(score) ? fmtSigned(score, 2) : "—";
  const dir = String((raw && raw.direction) || "");
  const dirLabel =
    dir === "long_t" ? "正 T" : dir === "reverse_t" ? "反 T" : dir || "—";
  const enter =
    raw && raw.dir_enter != null && Number.isFinite(Number(raw.dir_enter))
      ? Number(raw.dir_enter)
      : 0.35;
  let decision = "低置信跳过";
  if (Number.isFinite(score)) {
    if (score >= enter) decision = "正 T（卖旧仓再买回）";
    else if (score <= -enter) decision = "反 T（买新仓再卖旧仓）";
  } else if (dir === "long_t" || dir === "reverse_t") {
    decision = dirLabel;
  }
  let html = '<div class="score-detail">';
  html +=
    `<div class="score-hero">` +
    `<div class="score-hero-label">做 T 方向分（表格「分」）</div>` +
    `<div class="score-hero-value ${signCls(score)}">${escapeText(scoreTxt)}</div>` +
    `<div class="score-hero-hint">约 -1～+1 · 开盘可用特征 · 无前视</div>` +
    `<div class="score-hero-semantics">` +
    `语义：决定当天正 T / 反 T / 跳过 · <strong>不是</strong> 选股 predicted_score（ŷ）` +
    `</div>` +
    `<div class="score-hero-gate">门槛 ±${escapeText(String(enter))} · 判定 ${escapeText(
      decision
    )}</div>` +
    `</div>`;

  const feats = (raw && raw.features) || (raw && raw.direction_features) || {};
  const featKeys = ["gap_pct", "yclose_loc", "mom3_pct", "gap_atr", "atr_pct"];
  const featRows = featKeys
    .map((k) => {
      const v = feats[k];
      if (v == null || v === "") return "";
      const n = Number(v);
      const txt = Number.isFinite(n) ? fmtSigned(n, 2) : String(v);
      return (
        `<tr>` +
        `<td class="score-fw-name">${escapeText(T0_FEAT_LABELS[k] || k)}</td>` +
        `<td class="num score-fw-val ${signCls(n)}">${escapeText(txt)}</td>` +
        `</tr>`
      );
    })
    .filter(Boolean);
  if (featRows.length) {
    html +=
      `<div class="score-factors-section">` +
      `<div class="score-section-title">开盘特征</div>` +
      `<table class="score-factor-weights"><tbody>${featRows.join("")}</tbody></table>` +
      `<div class="score-hero-hint" style="margin-top:6px">权重默认 跳空0.45 · 昨位0.20 · mom3 0.20 · gap/ATR 0.15</div>` +
      `</div>`;
  }

  const reason = String((raw && (raw.direction_reason || raw.reason)) || "").trim();
  if (reason) {
    html +=
      `<div class="score-reasons-section">` +
      `<div class="score-section-title">选向说明</div>` +
      `<ul class="score-reasons"><li class="neutral">${escapeText(reason)}</li></ul>` +
      `</div>`;
  }

  if (raw && raw.stock_code) {
    html +=
      `<div class="score-hero-hint">` +
      `${escapeText(raw.stock_code)}` +
      (raw.date ? ` · ${escapeText(raw.date)}` : "") +
      (dirLabel !== "—" ? ` · ${escapeText(dirLabel)}` : "") +
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

  function showHtml(anchor, html, { className = "score-tooltip" } = {}) {
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
  }

  function bindAttrTip(
    host,
    {
      selector = "[data-tip-html]",
      htmlAttr = "data-tip-html",
      className = "score-tooltip plain-hover-tip",
      buildHtml = null,
    } = {}
  ) {
    if (!host || host.dataset.attrTipWired === "1") return;
    host.dataset.attrTipWired = "1";
    host.addEventListener("mouseover", (e) => {
      const el = e.target.closest(selector);
      if (!el || !host.contains(el)) return;
      if (tipAnchor === el && tipEl) return;
      let html = "";
      if (typeof buildHtml === "function") {
        html = buildHtml(el) || "";
      } else {
        html = el.getAttribute(htmlAttr) || "";
      }
      if (!html) return;
      showHtml(el, html, { className });
    });
    host.addEventListener("mouseout", (e) => {
      const from = e.target.closest(selector);
      if (!from) return;
      const to = e.relatedTarget;
      if (to && from.contains(to)) return;
      if (tipEl && to && tipEl.contains(to)) return;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      hide();
    });
  }

  function resolveScoreTipMode(cell) {
    if (!cell) return "trade";
    const tip = String(cell.dataset.scoreTip || "").trim().toLowerCase();
    if (tip === "cal" || tip === "calibration") return "cal";
    if (tip === "eod") return "eod";
    if (tip === "nowcast" || tip === "nc") return "nowcast";
    if (tip === "tau" || tip === "rem") return "tau";
    if (tip === "on") return "on";
    if (tip === "trade" || tip === "score") return "trade";
    if (cell.classList.contains("watching-score-eod")) return "eod";
    if (cell.classList.contains("watching-score-cal")) return "cal";
    if (cell.classList.contains("watching-score-nowcast")) return "nowcast";
    if (cell.classList.contains("watching-score-tau")) return "tau";
    if (cell.classList.contains("watching-score-on")) return "on";
    return "trade";
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

    // 校准列 tip：只展示 g(*)，不混 ŷ_trade 全栈
    if (tipMode === "cal") {
      let calHtml = formatCalibrationSection(raw, { calOnly: true });
      if (!calHtml) {
        const soft =
          raw && raw.score_calibration_note
            ? String(raw.score_calibration_note).slice(0, 100)
            : "";
        const hintParts = [];
        if (soft) hintParts.push(soft);
        if (raw && raw.score_calibration_eod_oor) {
          hintParts.push("ŷ_EOD 落在拟合域外");
        }
        if (!hintParts.length) {
          hintParts.push("暂无映射 · 研究节拟合并写入 live 后，eod 列可读 g(ŷ_EOD)");
        }
        calHtml =
          `<div class="score-layer score-layer-cal is-shadow">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">eod · g(ŷ_EOD)</div>` +
          `<div class="score-hero-value score-flat">—</div>` +
          `</div>` +
          `<div class="score-hero-hint">${escapeText(hintParts.join(" · "))}</div>` +
          `</div>`;
      }
      const html = `<div class="score-detail">${calHtml}</div>`;
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

    if (tipMode === "nowcast") {
      let ncHtml = formatNowcastSection(raw);
      if (!ncHtml) {
        ncHtml =
          `<div class="score-layer score-layer-nowcast is-shadow">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">y_nc</div>` +
          `<div class="score-hero-value score-flat">—</div>` +
          `</div>` +
          `<div class="score-hero-hint">Kalman 合成 · 昨收口径</div>` +
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

    if (tipMode === "tau") {
      const parts = [
        formatCompactTauTip(raw),
        formatFormulaTermsSection(raw, { key: "tau" }),
        formatFactorWeightsSection(raw, { key: "tau" }),
      ].filter(Boolean);
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

    if (tipMode === "trade") {
      let blendHtml = formatBlendScoreSection(raw);
      if (!blendHtml) {
        const trade = resolveTradeScore(raw);
        const val = trade == null ? "—" : `${fmtSigned(trade, 2)}%`;
        blendHtml =
          `<div class="score-layer score-layer-blend">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">y_trade</div>` +
          `<div class="score-hero-value ${signCls(trade)}">${escapeText(val)}</div>` +
          `</div>` +
          `<div class="score-hero-hint">双头融合 · 排序/卖门槛</div>` +
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
      !!(raw.dual_score_fusion && String(raw.dual_score_fusion).trim());

    // 空对象（JSON 解析失败）才跳过；未打分也展示 ŷ_EOD/ŷ_τ 占位，避免悬停无反应
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
    // trade 列：先 ŷ_trade 组成，再拆 ŷ_τ / ŷ_EOD（g 只在 eod 列，nowcast 只在 nowcast 列）
    html += formatBlendScoreSection(raw);
    html += formatRemScoreSection(raw);
    html += formatFormulaTermsSection(raw, { key: "tau" });
    html += formatFactorWeightsSection(raw, { key: "tau" });
    html += formatScoreHero(raw);
    html += formatFormulaTermsSection(raw);
    html += formatFactorWeightsSection(raw);
    if (formula && !hasTerms) {
      html += `<div class="score-formula-section">
        <div class="score-section-title">ŷ_EOD 公式</div>
        <div class="score-formula">${escapeText(formula)}</div>
      </div>`;
    }
    html += formatWeightSourceNote(raw);
    html += formatFeatureIsoSection(raw);
    html += formatSentimentGateSection(raw);
    html += formatMarketPriorSection(raw);
    html += formatTailAnomalySection(raw);
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
