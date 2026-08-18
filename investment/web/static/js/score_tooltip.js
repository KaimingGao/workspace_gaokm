/** 评分悬浮注释（交易执行持仓 / 数据中心观察表共用）。
 * 主叙事：收益分 ŷ + 因子系数 β（可正可负），非规则权重。
 */

import {
  escapeText,
  fuseOrthogonalTrade,
  resolveEodScore,
  resolveEodRemScore,
  resolveTradeScore,
} from "./paper/fmt.js?v=p1128";
import { renderYPathVizHtml } from "./y_path_viz.js?v=p1169";

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
  // 与表列同源：修 eod_next 塌成 EOD 的旧 blend；eod_next 不减缺口
  const trade = resolveTradeScore(raw);
  if (trade != null) return trade;
  const eodRem = resolveEodRemScore(raw);
  const yt = resolveTau(raw);
  const fused = fuseOrthogonalTrade(raw, eodRem, yt);
  if (fused != null) return fused;
  if (yt != null) return yt;
  if (eodRem != null) return eodRem;
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
      `<div class="score-hero-label">① ŷ_EOD · 已降级</div>` +
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
    `<div class="score-hero-label">① ŷ_EOD · 隔夜主轴</div>` +
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

/** ŷ_EOD_rem：与 ŷ_EOD 同级 · 剥掉昨收→τ 后的剩余。 */
export function formatEodRemScoreSection(raw) {
  const yEod = resolveEodScore(raw);
  const rem = resolveEodRemScore(raw);
  const realized = raw && raw.realized_t1_to_tau;
  const gap = raw && raw.gap_pct;
  const remN = rem == null ? null : Number(rem);
  const yN = yEod == null ? null : Number(yEod);
  const rN =
    realized != null && realized !== "" && Number.isFinite(Number(realized))
      ? Number(realized)
      : gap != null && gap !== "" && Number.isFinite(Number(gap))
        ? Number(gap)
        : null;
  const remTxt = remN == null || !Number.isFinite(remN) ? "—" : `${fmtSigned(remN, 3)}%`;
  const yTxt = yN == null || !Number.isFinite(yN) ? "—" : `${fmtSigned(yN, 3)}%`;
  const rTxt = rN == null || !Number.isFinite(rN) ? "—" : `${fmtSigned(rN, 3)}%`;
  const tau = String((raw && (raw.as_of_tau || raw.rem_tau)) || "open");
  const rows = [
    `<div class="score-layer-row"><span>ŷ_EOD</span><span class="num ${signCls(
      yN
    )}">${escapeText(yTxt)}</span></div>`,
    `<div class="score-layer-row"><span>昨收→τ 已实现</span><span class="num ${signCls(
      rN
    )}">${escapeText(rTxt)}</span></div>`,
    `<div class="score-layer-row score-layer-row-total"><span>ŷ_EOD_rem</span><span class="num ${signCls(
      remN
    )}">${escapeText(remTxt)}</span></div>`,
  ];
  return (
    `<div class="score-layer score-layer-eod-rem">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">② ŷ_EOD_rem · τ→收盘映射</div>` +
    `<div class="score-hero-value ${signCls(remN)}">${escapeText(remTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">(1+ŷ_EOD)/(1+已实现)−1 · τ=${escapeText(
      tau
    )} · 与 ŷ_τ 同目标（T 收/T 开）</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
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
    `<div class="score-hero-label">③ ŷ_τ · T收 / T开（rem 头）</div>` +
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
  return (
    resolveCalNum(raw, "predicted_score_cal") != null ||
    resolveCalNum(raw, "predicted_score_eod_rem_cal") != null ||
    resolveCalNum(raw, "predicted_score_tau_cal") != null ||
    resolveCalNum(raw, "predicted_score_blend_cal") != null
  );
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

/** 校准层 g(ŷ)：有 live knots 即对照。
 * @param {object} raw
 * @param {{ calOnly?: boolean }} [opts] calOnly=true 时只列 g(*)，不配 raw ŷ 行
 */
export function formatCalibrationSection(raw, opts = {}) {
  if (!calibrationActive(raw)) return "";
  const calOnly = !!opts.calOnly;
  const yEod = resolveEodScore(raw);
  const yTau = resolveTau(raw);
  const blend = resolveBlend(raw);
  const yEodCal = resolveCalNum(raw, "predicted_score_cal");
  const yTauCal = resolveCalNum(raw, "predicted_score_tau_cal");
  const blendCal = resolveCalNum(raw, "predicted_score_blend_cal");
  const hero =
    blendCal != null ? blendCal : yEodCal != null ? yEodCal : yTauCal;
  const heroTxt =
    hero == null ? "—" : `${fmtSigned(hero, 3)}%`;
  const rows = [];
  if (yEodCal != null) {
    if (!calOnly) {
      const yTxt =
        yEod == null || !Number.isFinite(Number(yEod))
          ? "—"
          : `${fmtSigned(Number(yEod), 3)}%`;
      rows.push(
        `<div class="score-layer-row"><span>ŷ_EOD</span><span class="num ${signCls(
          yEod
        )}">${escapeText(yTxt)}</span></div>`
      );
    }
    rows.push(calRowHtml("g(ŷ_EOD)", yEodCal));
    rows.push(calDeltaRowHtml(yEod, yEodCal));
  }
  const yEodRem = resolveEodRemScore(raw);
  const yEodRemCal = resolveCalNum(raw, "predicted_score_eod_rem_cal");
  if (yEodRemCal != null) {
    if (!calOnly) {
      const remTxt =
        yEodRem == null || !Number.isFinite(Number(yEodRem))
          ? "—"
          : `${fmtSigned(Number(yEodRem), 3)}%`;
      rows.push(
        `<div class="score-layer-row"><span>ŷ_EOD_rem</span><span class="num ${signCls(
          yEodRem
        )}">${escapeText(remTxt)}</span></div>`
      );
    }
    rows.push(calRowHtml("g(ŷ_EOD_rem)", yEodRemCal));
    rows.push(calDeltaRowHtml(yEodRem, yEodRemCal));
  }
  if (yTauCal != null) {
    if (!calOnly) {
      const tTxt =
        yTau == null || !Number.isFinite(Number(yTau))
          ? "—"
          : `${fmtSigned(Number(yTau), 3)}%`;
      rows.push(
        `<div class="score-layer-row"><span>ŷ_τ</span><span class="num ${signCls(
          yTau
        )}">${escapeText(tTxt)}</span></div>`
      );
    }
    rows.push(calRowHtml("g(ŷ_τ)", yTauCal));
    rows.push(calDeltaRowHtml(yTau, yTauCal));
  }
  if (blendCal != null) {
    if (!calOnly) {
      const bTxt =
        blend == null || !Number.isFinite(Number(blend))
          ? "—"
          : `${fmtSigned(Number(blend), 3)}%`;
      rows.push(
        `<div class="score-layer-row"><span>ŷ_trade</span><span class="num ${signCls(
          blend
        )}">${escapeText(bTxt)}</span></div>`
      );
    }
    rows.push(
      `<div class="score-layer-row score-layer-row-total"><span>g(ŷ_trade)</span><span class="num ${signCls(
        blendCal
      )}">${escapeText(fmtSigned(blendCal, 3))}%</span></div>`
    );
    rows.push(calDeltaRowHtml(blend, blendCal));
  }
  if (!rows.length) return "";
  const oorBits = [];
  if (raw && raw.score_calibration_eod_oor) oorBits.push("ŷ_EOD");
  if (raw && raw.score_calibration_eod_rem_oor) oorBits.push("ŷ_EOD_rem");
  if (raw && raw.score_calibration_tau_oor) oorBits.push("ŷ_τ");
  const oorHint = oorBits.length
    ? ` · ${oorBits.join("/")} 落在拟合域外（端点钳制，对照弱）`
    : "";
  const softNote =
    raw && raw.score_calibration_note
      ? ` · ${String(raw.score_calibration_note).slice(0, 80)}`
      : "";
  // live 仅有一侧 knots 时 blend 会混 raw：明确提示，避免当成全校准
  let partialHint = "";
  const partial = raw && raw.score_calibration_partial
    ? String(raw.score_calibration_partial)
    : "";
  if (partial.includes("tau_raw") && !partial.includes("oor")) {
    partialHint = " · g(ŷ_trade)=g(EOD)+raw ŷ_τ（尚无 τ 映射）";
  } else if (partial.includes("eod_raw") && !partial.includes("oor")) {
    partialHint = " · g(ŷ_trade)=raw EOD+g(ŷ_τ)（尚无 EOD 映射）";
  } else if (partial.includes("eod_rem_oor_raw")) {
    partialHint = " · ŷ_EOD_rem 域外：blend 用 raw rem + g(τ)，避免端点撞车";
  } else if (partial.includes("tau_oor_raw")) {
    partialHint = " · ŷ_τ 域外：blend 用 g(EOD)+raw τ，避免端点撞车";
  } else if (
    blendCal != null &&
    yTauCal == null &&
    (yEodCal != null || resolveCalNum(raw, "predicted_score_eod_rem_cal") != null) &&
    yTau != null &&
    Number.isFinite(Number(yTau))
  ) {
    partialHint = " · g(ŷ_trade)=g(EOD)+raw ŷ_τ（尚无 τ 映射）";
  }
  const title = calOnly ? "校准 g(ŷ)" : "⑤ 校准 g(ŷ)";
  const hint = calOnly
    ? `仅对照 · Δ=历史条件均值相对 raw 的修正 · 不进排序/闸/入簿${partialHint}${oorHint}${softNote}`
    : `单调映射 · Δ=g−ŷ · 不改 β · 排序/闸/入簿仍用原 ŷ${partialHint}${oorHint}${softNote}`;
  return (
    `<div class="score-layer score-layer-cal is-on">` +
    `<div class="score-layer-head">` +
    `<div class="score-hero-label">${escapeText(title)}</div>` +
    `<div class="score-hero-value ${signCls(hero)}">${escapeText(heroTxt)}</div>` +
    `</div>` +
    `<div class="score-hero-hint">${escapeText(hint)}</div>` +
    `<div class="score-layer-compose">${rows.join("")}</div>` +
    `</div>`
  );
}

/** 融合分：w·ŷ_EOD_rem + w·ŷ_τ。 */
export function formatBlendScoreSection(raw) {
  const blend = resolveBlend(raw);
  const eodRem = resolveEodRemScore(raw);
  const yt = resolveTau(raw);
  const { w_eod, w_tau } = resolveWeights(raw);
  const blendTxt = blend == null ? "—" : `${fmtSigned(blend, 3)}%`;
  const remN = eodRem == null || eodRem === "" ? null : Number(eodRem);
  const tN = yt == null || yt === "" ? null : Number(yt);
  const remTxt = remN == null || !Number.isFinite(remN) ? "—" : `${fmtSigned(remN, 3)}%`;
  const tTxt = tN == null || !Number.isFinite(tN) ? "—" : `${fmtSigned(tN, 3)}%`;
  const wTxt = `w_EOD=${Number(w_eod).toFixed(2)} · w_τ=${Number(w_tau).toFixed(2)}`;
  const wMode =
    raw && raw.dual_score_weights && raw.dual_score_weights.w_mode
      ? String(raw.dual_score_weights.w_mode)
      : "fixed";
  const window =
    (raw && (raw.dual_score_window || (raw.dual_score_weights && raw.dual_score_weights.window))) ||
    "intraday";
  const eodNext = String(window) === "eod_next";
  const hint = eodNext
    ? `${wTxt} · ${wMode} · 收盘后不减缺口 · ŷ_trade 仍正交加权`
    : `${wTxt} · ${wMode} · 同为 T收/T开 · 开盘排序键 · 表列主分`;
  const head =
    raw && raw.dual_score_head != null
      ? String(raw.dual_score_head)
      : raw && raw.dual_score_single_head
        ? "single"
        : "";
  let headHint = "";
  if (head === "single_eod") {
    headHint = " · 单头降级：仅 ŷ_EOD（缺 ŷ_τ）";
  } else if (head === "single_tau") {
    headHint = " · 单头降级：仅 ŷ_τ（缺 EOD rem）";
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
    `<div class="score-layer-row"><span>ŷ_EOD_rem</span><span class="num ${signCls(
      remN
    )}">${escapeText(remTxt)}</span></div>`,
    `<div class="score-layer-row"><span>ŷ_τ</span><span class="num ${signCls(
      tN
    )}">${escapeText(tTxt)}</span></div>`,
    `<div class="score-layer-row score-layer-row-total"><span>ŷ_trade</span><span class="num ${signCls(
      blend
    )}">${escapeText(blendTxt)}</span></div>`,
  ];
  const nowcast =
    raw && raw.predicted_score_nowcast != null
      ? Number(raw.predicted_score_nowcast)
      : null;
  if (nowcast != null && Number.isFinite(nowcast)) {
    const kTxt =
      raw.nowcast_K != null && Number.isFinite(Number(raw.nowcast_K))
        ? ` K=${Number(raw.nowcast_K).toFixed(2)}`
        : "";
    const qTxt =
      raw.nowcast_q != null && Number.isFinite(Number(raw.nowcast_q))
        ? ` q=${Number(raw.nowcast_q).toFixed(3)}`
        : "";
    const asOf = raw.nowcast_as_of ? ` @${raw.nowcast_as_of}` : "";
    rows.push(
      `<div class="score-layer-row"><span>ŷ_nowcast·影${escapeText(
        asOf
      )}${escapeText(kTxt)}${escapeText(qTxt)}</span><span class="num ${signCls(
        nowcast
      )}">${escapeText(`${fmtSigned(nowcast, 3)}%`)}</span></div>`
    );
  }
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
    `<div class="score-hero-label">④ ŷ_trade · 正交加权${
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
      : raw && (raw.formula_terms || raw.score_formula_terms);
  if (!expl || typeof expl !== "object") return "";
  const terms = Array.isArray(expl.terms) ? expl.terms : [];
  if (!terms.length && expl.intercept == null) return "";

  const title = key === "tau" ? "ŷ_τ 组成" : "ŷ_EOD 组成";
  const totalLabel = key === "tau" ? "合计 ŷ_τ" : "合计 ŷ_EOD";

  const rows = terms
    .map((t) => {
      const name = t.label || FACTOR_LABELS[t.key] || TAU_FEAT_LABELS[t.key] || t.key || "—";
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
      label: t.label || FACTOR_LABELS[t.key] || TAU_FEAT_LABELS[t.key] || t.key,
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
    if (score >= enter) decision = "正 T（先卖后买）";
    else if (score <= -enter) decision = "反 T（先买后卖）";
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
    if (tip === "trade" || tip === "score") return "trade";
    if (cell.classList.contains("watching-score-cal")) return "cal";
    return "trade";
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
    // 校准列 tip：只展示 g(*)，不混 ŷ_trade 全栈
    if (tipMode === "cal") {
      let calHtml = formatCalibrationSection(raw, { calOnly: true });
      if (!calHtml) {
        const soft =
          raw && raw.score_calibration_note
            ? String(raw.score_calibration_note).slice(0, 100)
            : "";
        const oorBits = [];
        if (raw && raw.score_calibration_eod_oor) oorBits.push("ŷ_EOD");
        if (raw && raw.score_calibration_eod_rem_oor) oorBits.push("ŷ_EOD_rem");
        if (raw && raw.score_calibration_tau_oor) oorBits.push("ŷ_τ");
        const hintParts = [];
        if (soft) hintParts.push(soft);
        if (oorBits.length) {
          hintParts.push(`${oorBits.join("/")} 落在拟合域外`);
        }
        if (!hintParts.length) {
          hintParts.push("暂无映射 · 研究节拟合并写入 live 后，校准列可读 g(ŷ)");
        }
        calHtml =
          `<div class="score-layer score-layer-cal is-shadow">` +
          `<div class="score-layer-head">` +
          `<div class="score-hero-label">校准 g(ŷ)</div>` +
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
    // ① EOD → ② EOD_rem → ③ τ → ④ ŷ_trade（校准 g 仅在校准列 tip）
    html += formatScoreHero(raw);
    html += formatFormulaTermsSection(raw);
    html += formatFactorWeightsSection(raw);
    if (formula && !hasTerms) {
      html += `<div class="score-formula-section">
        <div class="score-section-title">ŷ_EOD 公式</div>
        <div class="score-formula">${escapeText(formula)}</div>
      </div>`;
    }
    html += formatEodRemScoreSection(raw);
    html += formatRemScoreSection(raw);
    html += formatFormulaTermsSection(raw, { key: "tau" });
    html += formatFactorWeightsSection(raw, { key: "tau" });
    html += formatBlendScoreSection(raw);
    html += formatWeightSourceNote(raw);
    html += formatFeatureIsoSection(raw);
    html += formatSentimentGateSection(raw);
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
