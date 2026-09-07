/** Paper · Execution / 做T 生效规则卡 + 编辑表单。 */

import { escapeText } from "./fmt.js";

export const Y_TAU_MAP_LABELS = {
  trend: "符号定方向",
  fixed_sell_then_buy: "固定反T",
  fixed_buy_then_sell: "固定正T",
};

/** 表单 / 规则卡选项文案（与 follow 下拉 value 一一对应）。 */
export const Y_TAU_MAP_SELECT_LABELS = {
  trend: "符号定方向",
  fixed_sell_then_buy: "固定反T",
  fixed_buy_then_sell: "固定正T",
};

/** 规则卡 / 摘要用短标签（表格列头等紧凑场景）。 */
export const Y_TAU_MAP_SHORT = {
  trend: "符号",
  fixed_sell_then_buy: "固定反T",
  fixed_buy_then_sell: "固定正T",
};

/** 与后端 normalize_y_tau_map 对齐（scalp 等别名 → trend）。 */
export function normalizeYTauMap(mode) {
  const k = String(mode || "trend").trim().toLowerCase();
  if (k === "scalp" || k === "follow" || k === "momentum" || k === "invert") {
    return "trend";
  }
  if (k in Y_TAU_MAP_SELECT_LABELS) return k;
  return "trend";
}

export function yTauMapLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_LABELS[k] || k;
}

export function yTauMapShortLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_SHORT[k] || yTauMapLabel(k);
}

/** τ先验：off | score。旧 skip/hard 并入 score。 */
function normalizeTauLeg1PriorMode(raw, priorBool) {
  if (raw == null || raw === "") {
    return priorBool === true ? "score" : "off";
  }
  const m = String(raw).trim().toLowerCase();
  if (m === "off" || m === "false" || m === "0" || m === "no") return "off";
  return "score";
}

export function yTauMapRuleLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_SELECT_LABELS[k] || k;
}

const PATH_MODE_LABELS = {
  first_touch: "5m 首触达",
};

/** 成交日行 hover：τ 出场裕度基准 → 当日有效值（回测明细仍可能携带旧字段）。 */
export function adaptiveSizingDayTip(day, rules = {}) {
  const sellM = Number(
    rules?.y_tau_exit_price_mult_buy_then_sell ??
      day?.y_tau_exit_price_mult_buy_then_sell ??
      2
  );
  const buyM = Number(
    rules?.y_tau_exit_price_mult_sell_then_buy ??
      day?.y_tau_exit_price_mult_sell_then_buy ??
      2
  );
  if (!Number.isFinite(sellM) || !Number.isFinite(buyM)) return "";
  const fmt = (n) => (Number.isFinite(n) ? Number(n).toFixed(2).replace(/\.?0+$/, "") : "—");
  return `τ出场裕度 正T卖×${fmt(sellM)} / 反T买×${fmt(buyM)}`;
}

/** v6 收盘带宽选腿说明（旧 y_tau_map 参数已忽略）。 */
export function yTauMapScoreTip(_mode, _enter = 0.25) {
  return "v6 收盘带宽：ĉ=ĉ_τ（该根前缀 ŷ_τ）→ /S 映分钟；r=(C/ĉ−1)% 破 ±δ 定正/反T；C=本根5m收价；τ先验把 ŷ_τ 当偏移加到 ±δ，整条带宽平移（宽仍 2δ）；|y_τ|/y_path 入场；|S−1| 超阈跳过。";
}

/** 回测/预演响应可能只有 rules 或残缺 execution；补齐 t0 供规则卡渲染。 */
export { nowcastOcPct } from "./fmt.js";

export function normalizeExecutionView(execution, rules) {
  if (!execution && !rules) return null;
  const base = execution ? { ...execution } : { ok: true };
  const t0 = { ...(rules || {}), ...(base.t0 || {}) };
  const hasDetail =
    t0.direction != null ||
    t0.path_mode != null ||
    t0.y_tau_exit_price_mult_buy_then_sell != null ||
    t0.t0_ratio != null;
  if (!hasDetail && !base.effective_hash) return execution || null;
  return {
    ...base,
    ok: base.ok !== false,
    t0,
    coupling: base.coupling || {},
  };
}

function specKpi(label, value, tip = "") {
  const title = tip ? ` title="${escapeText(tip)}"` : "";
  return (
    `<div class="paper-t0-spec-kpi"${title}>` +
    `<span class="paper-t0-spec-kpi-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-spec-kpi-v">${escapeText(String(value))}</span>` +
    `</div>`
  );
}

export function renderExecutionRulesHtml(execution) {
  if (!execution || execution.ok === false) {
    const err = (execution && execution.error) || "无法加载 ExecutionSpec";
    return `<p class="quant-sub">${escapeText(err)}</p>`;
  }
  const t0 = execution.t0 || {};
  const fillLbl = "trigger";
  const pathLbl =
    PATH_MODE_LABELS[String(t0.path_mode || "first_touch").toLowerCase()] || t0.path_mode || "—";

  const notes = (execution.notes || []).slice(0, 2);

  const enabledLbl = t0.enabled === false ? "关" : "开";

  return (
    `<div class="paper-t0-spec">` +
    `<header class="paper-t0-spec-head">` +
    `<h4 class="paper-t0-spec-head-title">生效规格</h4>` +
    `<div class="paper-t0-spec-kpi-strip" aria-label="核心参数">` +
    specKpi("启用", enabledLbl, "做 T overlay 总开关") +
    specKpi("策略", "close_band", "v6 收盘带宽选腿；每轮 40%；11:00 后不开 leg1") +
    specKpi(
      "选腿",
      (() => {
        const d =
          t0.t0_close_band_delta_pct != null ? Number(t0.t0_close_band_delta_pct) : 0.2;
        const rr =
          t0.t0_round_ratio != null ? Math.round(Number(t0.t0_round_ratio) * 100) : 40;
        const mr =
          t0.t0_slots_max_rounds != null ? Number(t0.t0_slots_max_rounds) : 5;
        const cap =
          t0.t0_max_position_pct != null ? Math.round(Number(t0.t0_max_position_pct) * 100) : 100;
        return `ĉ±${d}%·${rr}%×≤${mr}·≤${cap}%`;
      })(),
      "ĉ=ĉ_τ（该根前缀 ŷ_τ）；C=本根5m收价破 ĉ±δ 定正/反T；每轮比例×最多轮数×满仓上限"
    ) +
    specKpi(
      "门槛1",
      (() => {
        if (t0.y_enter_enabled === false) return "关";
        const tau = Number(t0.y_tau_enter);
        const path = Number(t0.y_path_enter);
        const r = Number(t0.r_tau_enter);
        const cx = Number(t0.y_complexity_max ?? t0.y_cx_max);
        const tpd = Number(t0.y_tpd_max);
        const tauLbl = Number.isFinite(tau) ? (tau > 0 ? `|τ|≥${tau}%` : "τ关") : "|τ|≥0.01%";
        const pathLblG = Number.isFinite(path) ? (path > 0 ? `|path|≥${path}%` : "path关") : "|path|≥0.01%";
        const rLbl = Number.isFinite(r) ? (r > 0 ? `|R|≥${r}%` : "R关") : "|R|≥0.1%";
        const cxLbl = Number.isFinite(cx) ? (cx >= 1 ? "cx关" : `cx≤${cx}`) : "cx关";
        const tpdLbl = Number.isFinite(tpd) ? (tpd >= 1 ? "tpd关" : `tpd≤${tpd}`) : "tpd≤0.4";
        return `${tauLbl}·${pathLblG}·${rLbl}·${cxLbl}·${tpdLbl}`;
      })(),
      "门槛1：启用时 |y_τ| / |y_path| / |R̂_τ| 入场 + complexity/tpd 风险"
    ) +
    specKpi(
      "门槛2",
      (() => {
        if (t0.y_enter_alt_enabled === false) return "关";
        const tau = Number(t0.y_tau_enter_alt);
        const path = Number(t0.y_path_enter_alt);
        const r = Number(t0.r_tau_enter_alt);
        const cx = Number(t0.y_complexity_max_alt);
        const tpd = Number(t0.y_tpd_max_alt);
        const tauLbl = Number.isFinite(tau) ? (tau > 0 ? `|τ|≥${tau}%` : "τ关") : "|τ|≥0.4%";
        const pathLblG = Number.isFinite(path) ? (path > 0 ? `|path|≥${path}%` : "path关") : "|path|≥0.4%";
        const rLbl = Number.isFinite(r) ? (r > 0 ? `|R|≥${r}%` : "R关") : "|R|≥0.4%";
        const cxLbl = Number.isFinite(cx) ? (cx >= 1 ? "cx关" : `cx≤${cx}`) : "cx关";
        const tpdLbl = Number.isFinite(tpd) ? (tpd >= 1 ? "tpd关" : `tpd≤${tpd}`) : "tpd关";
        return `${tauLbl}·${pathLblG}·${rLbl}·${cxLbl}·${tpdLbl}`;
      })(),
      "门槛2：启用时 |y_τ| / |y_path| / |R̂_τ| 入场 + complexity/tpd 风险"
    ) +
    specKpi("路径", pathLbl, "分钟触价路径") +
    specKpi("成交", fillLbl, "全量触价 trigger；表单不再提供 mid/optimistic") +
    specKpi(
      "止损",
      (() => {
        const bts = Number(t0.t0_stop_pct_buy_then_sell);
        const stb = Number(t0.t0_stop_pct_sell_then_buy);
        const arm = Number(t0.t0_stop_arm_bars);
        const armLbl = Number.isFinite(arm) ? `${Math.round(arm)}根` : "2根";
        const conf = t0.t0_stop_on_close === false ? "触价" : "收盘";
        const fmt = (n) => (Number.isFinite(n) && n > 0 ? `${n}%` : "关");
        return `正${fmt(bts)}/反${fmt(stb)}·${armLbl}·${conf}`;
      })(),
      "相对该轮第一腿成交价；正T跌破卖旧 / 反T涨破买回；多轮共用同一套%；延迟与收盘确认共用"
    ) +
    specKpi(
      "追价",
      (() => {
        const bts = String(t0.t0_pm_degrade_buy_then_sell ?? t0.t0_pm_degrade ?? "").trim();
        const stb = String(t0.t0_pm_degrade_sell_then_buy ?? "").trim();
        const off = (s) => !s || s === "0" || s === "off" || s === "none" || s === "-";
        const a = off(bts) ? "关" : bts;
        const b = off(stb) ? "关" : stb;
        return `正${a}/反${b}`;
      })(),
      "中点追价起算；空=关；止损优先于追价"
    ) +
    `</div></header>` +
    (notes.length
      ? `<ul class="paper-t0-rules-notes">${notes
          .map((n) => `<li>${escapeText(String(n))}</li>`)
          .join("")}</ul>`
      : "") +
    `</div>`
  );
}

/** 回测窗不进 ExecutionSpec；用 localStorage 跨刷新记住。 */
const T0_LOOKBACK_KEY = "paper.t0.lookback";
const T0_LOOKBACK_MIGRATE_KEY = "paper.t0.lookback.migrated_v3";
const T0_LOOKBACK_MIGRATE_V4_KEY = "paper.t0.lookback.migrated_v4";

function _clampLookback(n) {
  return Math.max(10, Math.min(Math.round(Number(n)), 500));
}

/** 读表单回测窗并写入 localStorage；无有效值时返回 null。 */
export function persistT0Lookback(root) {
  const el = root && root.querySelector('[name="lookback"]');
  if (!el || el.value === "") return null;
  const n = Number(el.value);
  if (!Number.isFinite(n)) return null;
  const lookback = _clampLookback(n);
  try {
    localStorage.setItem(T0_LOOKBACK_KEY, String(lookback));
  } catch (_) {
    /* ignore */
  }
  return lookback;
}

function _restoreT0Lookback(root) {
  try {
    // 旧默认曾误升到 90：只清一次
    if (!localStorage.getItem(T0_LOOKBACK_MIGRATE_KEY)) {
      const prev = localStorage.getItem(T0_LOOKBACK_KEY);
      if (prev === "90" || prev === "10") localStorage.removeItem(T0_LOOKBACK_KEY);
      localStorage.setItem(T0_LOOKBACK_MIGRATE_KEY, "1");
    }
    // 旧产品默认 20 → 现默认 10：只清一次误存的 20
    if (!localStorage.getItem(T0_LOOKBACK_MIGRATE_V4_KEY)) {
      if (localStorage.getItem(T0_LOOKBACK_KEY) === "20") {
        localStorage.removeItem(T0_LOOKBACK_KEY);
      }
      localStorage.setItem(T0_LOOKBACK_MIGRATE_V4_KEY, "1");
    }
    const saved = localStorage.getItem(T0_LOOKBACK_KEY);
    const n = saved != null ? Number(saved) : NaN;
    const el = root && root.querySelector('[name="lookback"]');
    if (!el) return;
    if (Number.isFinite(n)) el.value = String(_clampLookback(n));
    else if (el.value === "" || !Number.isFinite(Number(el.value))) el.value = "10";
  } catch (_) {
    /* ignore */
  }
}

/** 用生效 execution 填充表单控件。 */
export function fillExecutionForm(root, execution) {
  if (!root || !execution || !execution.t0) return;
  const t0 = execution.t0;
  const set = (name, val) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el) return;
    if (el.type === "checkbox") {
      el.checked = !!val;
      return;
    }
    if (val == null) return;
    el.value = String(val);
  };
  set("enabled", t0.enabled !== false);
  set("direction", "dual_y");
  set("path_mode", t0.path_mode || "first_touch");
  set("must_cover_same_day_sell_then_buy", t0.must_cover_same_day_sell_then_buy !== false);
  set(
    "must_cover_same_day_buy_then_sell",
    (t0.must_cover_same_day_buy_then_sell ?? t0.must_cover_same_day) !== false
  );
  // 表单写死独立；后端仍可读旧 coupling，保存时强制 independent
  set("t0_vs_stance", "independent");
  set("t0_close_band_delta_pct", t0.t0_close_band_delta_pct != null ? t0.t0_close_band_delta_pct : 0.2);
  set("t0_price_space_gate", t0.t0_price_space_gate !== false);
  set(
    "t0_price_space_max_dev_pct",
    t0.t0_price_space_max_dev_pct != null ? t0.t0_price_space_max_dev_pct : 5
  );
  set(
    "t0_price_space_prev_dev_pct",
    t0.t0_price_space_prev_dev_pct != null ? t0.t0_price_space_prev_dev_pct : 5
  );
  set("t0_round_ratio", t0.t0_round_ratio != null ? t0.t0_round_ratio : 0.4);
  set("t0_max_position_pct", t0.t0_max_position_pct != null ? t0.t0_max_position_pct : 1.0);
  set("t0_slots_max_rounds", t0.t0_slots_max_rounds != null ? t0.t0_slots_max_rounds : 5);
  set("y_enter_enabled", t0.y_enter_enabled !== false);
  set("y_enter_alt_enabled", t0.y_enter_alt_enabled !== false);
  set("y_tau_enter", (() => {
    const n = Number(t0.y_tau_enter);
    if (!Number.isFinite(n)) return 0.01;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_path_enter", (() => {
    const n = Number(t0.y_path_enter);
    if (!Number.isFinite(n)) return 0.01;
    return Math.max(0, Math.min(n, 100));
  })());
  set("r_tau_enter", (() => {
    const n = Number(t0.r_tau_enter);
    if (!Number.isFinite(n)) return 0.1;
    return Math.max(0, Math.min(n, 1));
  })());
  set("y_tau_enter_alt", (() => {
    const n = Number(t0.y_tau_enter_alt);
    if (!Number.isFinite(n)) return 0.4;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_path_enter_alt", (() => {
    const n = Number(t0.y_path_enter_alt);
    if (!Number.isFinite(n)) return 0.4;
    return Math.max(0, Math.min(n, 100));
  })());
  set("r_tau_enter_alt", (() => {
    const n = Number(t0.r_tau_enter_alt);
    if (!Number.isFinite(n)) return 0.4;
    return Math.max(0, Math.min(n, 1));
  })());
  set("y_use_path", t0.y_use_path !== false);
  set("y_path_strong", t0.y_path_strong != null ? t0.y_path_strong : 0.2);
  set(
    "y_complexity_max",
    (() => {
      const n = Number(t0.y_complexity_max ?? t0.y_cx_max);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })()
  );
  set(
    "y_tpd_max",
    (() => {
      const n = Number(t0.y_tpd_max);
      if (!Number.isFinite(n)) return 0.4;
      return Math.max(0, Math.min(n, 1));
    })()
  );
  set(
    "y_complexity_max_alt",
    (() => {
      const n = Number(t0.y_complexity_max_alt);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })()
  );
  set(
    "y_tpd_max_alt",
    (() => {
      const n = Number(t0.y_tpd_max_alt);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })()
  );
  set(
    "y_tau_leg1_prior_mode",
    normalizeTauLeg1PriorMode(t0.y_tau_leg1_prior_mode, t0.y_tau_leg1_prior)
  );
  set("y_tau_leg1_prior_risk", t0.y_tau_leg1_prior_risk != null ? t0.y_tau_leg1_prior_risk : 0.1);
  set(
    "y_tau_leg1_prior_shift_scale",
    (() => {
      const a =
        t0.y_tau_leg1_prior_shift_scale != null ? Number(t0.y_tau_leg1_prior_shift_scale) : 0.1;
      if (!Number.isFinite(a)) return 0.1;
      return Math.min(1.0, Math.max(0.1, a));
    })()
  );
  set(
    "t0_pm_degrade_sell_then_buy",
    t0.t0_pm_degrade_sell_then_buy != null ? t0.t0_pm_degrade_sell_then_buy : "13:00"
  );
  set(
    "t0_pm_degrade_buy_then_sell",
    t0.t0_pm_degrade_buy_then_sell ?? t0.t0_pm_degrade ?? "13:00"
  );
  set(
    "t0_pm_chase_interval_min_sell_then_buy",
    t0.t0_pm_chase_interval_min_sell_then_buy ??
      t0.t0_pm_chase_interval_min ??
      5
  );
  set(
    "t0_pm_chase_interval_min_buy_then_sell",
    t0.t0_pm_chase_interval_min_buy_then_sell ??
      t0.t0_pm_chase_interval_min ??
      5
  );
  set(
    "t0_stop_pct_buy_then_sell",
    t0.t0_stop_pct_buy_then_sell != null ? t0.t0_stop_pct_buy_then_sell : 1.2
  );
  set(
    "t0_stop_pct_sell_then_buy",
    t0.t0_stop_pct_sell_then_buy != null ? t0.t0_stop_pct_sell_then_buy : 1.2
  );
  set(
    "t0_stop_arm_bars",
    t0.t0_stop_arm_bars != null ? t0.t0_stop_arm_bars : 2
  );
  set("t0_stop_on_close", t0.t0_stop_on_close !== false);
  _restoreT0Lookback(root);
}

/** 从表单收集 PATCH body。 */
export function collectExecutionForm(root) {
  if (!root) return null;
  const num = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return fallback;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : fallback;
  };
  const str = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el && el.value !== "" ? el.value : fallback;
  };
  const chk = (name, fallback = true) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el ? !!el.checked : fallback;
  };
  const t0 = {
    enabled: chk("enabled", true),
    t0_ratio: 1.0,
    fill_mode_sell_then_buy: "trigger",
    fill_mode_buy_then_sell: "trigger",
    fill_mode: "trigger",
    direction: "dual_y",
    path_mode: str("path_mode", "first_touch"),
    must_cover_same_day_sell_then_buy: chk("must_cover_same_day_sell_then_buy", true),
    must_cover_same_day_buy_then_sell: chk("must_cover_same_day_buy_then_sell", true),
    must_cover_same_day: chk("must_cover_same_day_buy_then_sell", true),
    t0_close_band_delta_pct: Math.max(0, Math.min(num("t0_close_band_delta_pct", 0.2), 10)),
    t0_price_space_gate: chk("t0_price_space_gate", true),
    t0_price_space_max_dev_pct: Math.max(0, Math.min(num("t0_price_space_max_dev_pct", 5), 5)),
    t0_price_space_prev_dev_pct: Math.max(0, Math.min(num("t0_price_space_prev_dev_pct", 5), 5)),
    t0_round_ratio: Math.max(0.05, Math.min(num("t0_round_ratio", 0.4), 1)),
    t0_max_position_pct: Math.max(0.05, Math.min(num("t0_max_position_pct", 1), 1)),
    t0_slots_max_rounds: Math.max(0, Math.min(Math.round(num("t0_slots_max_rounds", 5)), 16)),
    y_enter_enabled: chk("y_enter_enabled", true),
    y_enter_alt_enabled: chk("y_enter_alt_enabled", true),
    y_tau_enter: Math.max(0, Math.min(num("y_tau_enter", 0.01), 100)),
    y_tau_enter_sell_then_buy: Math.max(0, Math.min(num("y_tau_enter", 0.01), 100)),
    y_tau_enter_buy_then_sell: Math.max(0, Math.min(num("y_tau_enter", 0.01), 100)),
    y_tau_enter_alt: Math.max(0, Math.min(num("y_tau_enter_alt", 0.4), 100)),
    y_path_enter: Math.max(0, Math.min(num("y_path_enter", 0.01), 100)),
    y_path_enter_sell_then_buy: Math.max(0, Math.min(num("y_path_enter", 0.01), 100)),
    y_path_enter_buy_then_sell: Math.max(0, Math.min(num("y_path_enter", 0.01), 100)),
    y_path_enter_alt: Math.max(0, Math.min(num("y_path_enter_alt", 0.4), 100)),
    r_tau_enter: Math.max(0, Math.min(num("r_tau_enter", 0.1), 1)),
    r_tau_enter_alt: Math.max(0, Math.min(num("r_tau_enter_alt", 0.4), 1)),
    y_use_path: chk("y_use_path", true),
    y_path_strong: Math.max(0, Math.min(num("y_path_strong", 0.2), 5)),
    y_complexity_max: (() => {
      const n = num("y_complexity_max", 1);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tpd_max: (() => {
      const n = num("y_tpd_max", 0.4);
      if (!Number.isFinite(n)) return 0.4;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_complexity_max_alt: (() => {
      const n = num("y_complexity_max_alt", 1);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tpd_max_alt: (() => {
      const n = num("y_tpd_max_alt", 1);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tau_leg1_prior_mode: normalizeTauLeg1PriorMode(
      str("y_tau_leg1_prior_mode", "off"),
      false
    ),
    y_tau_leg1_prior_risk: Math.max(0, Math.min(num("y_tau_leg1_prior_risk", 0.1), 100)),
    y_tau_leg1_prior_shift_scale: (() => {
      const a = num("y_tau_leg1_prior_shift_scale", 0.1);
      if (!Number.isFinite(a)) return 0.1;
      return Math.min(1.0, Math.max(0.1, a));
    })(),
    t0_pm_degrade_sell_then_buy: str("t0_pm_degrade_sell_then_buy", "13:00"),
    t0_pm_degrade_buy_then_sell: str("t0_pm_degrade_buy_then_sell", "13:00"),
    t0_pm_degrade: str("t0_pm_degrade_buy_then_sell", "13:00"),
    t0_pm_chase_interval_min_sell_then_buy: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min_sell_then_buy", 5)), 60)
    ),
    t0_pm_chase_interval_min_buy_then_sell: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min_buy_then_sell", 5)), 60)
    ),
    t0_pm_chase_interval_min: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min_buy_then_sell", 5)), 60)
    ),
    t0_stop_pct_buy_then_sell: Math.max(0, Math.min(num("t0_stop_pct_buy_then_sell", 1.2), 20)),
    t0_stop_pct_sell_then_buy: Math.max(0, Math.min(num("t0_stop_pct_sell_then_buy", 1.2), 20)),
    t0_stop_arm_bars: Math.max(0, Math.min(Math.round(num("t0_stop_arm_bars", 2)), 48)),
    t0_stop_on_close: chk("t0_stop_on_close", true),
  };
  t0.y_tau_leg1_prior = t0.y_tau_leg1_prior_mode !== "off";
  // 振幅下限已从表单与门禁下线（不再写入 / 强制 0.2）
  t0.min_range_pct_sell_then_buy = 0;
  t0.min_range_pct_buy_then_sell = 0;
  t0.min_range_pct = 0;
  return {
    lock: true,
    t0,
    coupling: {
      t0_vs_stance: "independent",
    },
  };
}

/** 同步 |y_path| λ 刻度文案、模式徽章与面板 viz 高亮。 */
function syncPathMatrixViz(root) {
  if (!root) return;
  const num = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return fallback;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : fallback;
  };
  const fmt = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return `${Number.isInteger(n) ? n : Number(n.toFixed(2))}%`;
  };
  const enterEl = document.getElementById("pm-viz-enter");
  const halfEl = document.getElementById("pm-viz-half");
  const fullEl = document.getElementById("pm-viz-full");
  if (enterEl) enterEl.textContent = fmt(num("pm_path_enter", 0.1));
  if (halfEl) halfEl.textContent = fmt(num("pm_path_half", 0.5));
  if (fullEl) fullEl.textContent = fmt(num("pm_path_full", 1));
  const badge = document.getElementById("paper-path-matrix-badge");
  if (badge) {
    badge.textContent = "路径择时";
    badge.classList.add("is-on");
    badge.classList.remove("is-linear");
  }
  const wt = num("pm_fusion_w_trade", 0.5);
  const wn = num("pm_fusion_w_nc", 0.5);
  const wSum = Math.max(1e-9, Math.max(0, wt) + Math.max(0, wn));
  const pt = Math.round((Math.max(0, wt) / wSum) * 100);
  const pn = Math.max(0, 100 - pt);
  const fuseTrade = document.getElementById("pm-viz-fuse-trade");
  const fuseNc = document.getElementById("pm-viz-fuse-nc");
  const fuseRatio = document.getElementById("pm-viz-fuse-ratio");
  if (fuseTrade) {
    fuseTrade.textContent = `trade ${pt}%`;
    fuseTrade.style.flex = String(Math.max(pt, 1));
    fuseTrade.classList.toggle("is-dim", pt <= 0);
  }
  if (fuseNc) {
    fuseNc.textContent = `nc ${pn}%`;
    fuseNc.style.flex = String(Math.max(pn, 1));
    fuseNc.classList.toggle("is-dim", pn <= 0);
  }
  if (fuseRatio) fuseRatio.textContent = `${pt} : ${pn}`;
  const gatesPanel = root.querySelector('.follow-path-matrix-panel[data-panel="gates"]');
  if (gatesPanel) {
    const poOn = !!root.querySelector('[name="pm_require_path_on"]')?.checked;
    const exitOn = !!root.querySelector('[name="pm_pending_exit"]')?.checked;
    const nc = gatesPanel.querySelector(".follow-path-matrix-viz-seg.is-nc");
    const exit = gatesPanel.querySelector(".follow-path-matrix-viz-seg.is-exit");
    if (nc) nc.classList.toggle("is-dim", !poOn);
    if (exit) exit.classList.toggle("is-dim", !exitOn);
  }
}

/** 从 execution.rebalance_timing.path_matrix 填调仓择时表单。 */
export function fillPathMatrixForm(root, execution) {
  if (!root) return;
  const timing =
    (execution && execution.rebalance_timing) ||
    (execution && execution.execution && execution.execution.rebalance_timing) ||
    {};
  const pm =
    (timing && timing.path_matrix && typeof timing.path_matrix === "object"
      ? timing.path_matrix
      : null) || {};
  const set = (name, val) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el) return;
    if (el.type === "checkbox") {
      el.checked = !!val;
      return;
    }
    if (val == null) return;
    el.value = String(val);
  };
  set("pm_path_enter", pm.path_enter != null ? pm.path_enter : 0.1);
  set("pm_path_half", pm.path_half != null ? pm.path_half : 0.5);
  set("pm_path_full", pm.path_full != null ? pm.path_full : 1);
  set("pm_y_on_allow", pm.y_on_allow != null ? pm.y_on_allow : 0.5);
  set("pm_y_on_half", pm.y_on_half != null ? pm.y_on_half : 0.1);
  set(
    "pm_fusion_w_trade",
    pm.fusion_w_trade != null ? pm.fusion_w_trade : 0.5
  );
  set(
    "pm_fusion_w_nc",
    pm.fusion_w_nowcast != null ? pm.fusion_w_nowcast : 0.5
  );
  // 同号闸：优先新键；仅当旧账只有 nc 闸且未写新键时回退
  let pathOn = true;
  if (pm.require_path_on_same_sign != null) {
    pathOn = !!pm.require_path_on_same_sign;
  } else if (
    pm.require_nowcast_for_open != null &&
    pm.fusion_w_trade == null &&
    pm.fusion_w_nowcast == null
  ) {
    pathOn = !!pm.require_nowcast_for_open;
  }
  set("pm_require_path_on", pathOn);
  set("pm_pending_exit", pm.allow_pending_exit_on_path_low !== false);
  syncPathMatrixViz(root);
  if (!root.dataset.pmVizBound) {
    root.dataset.pmVizBound = "1";
    root.addEventListener("input", () => syncPathMatrixViz(root));
    root.addEventListener("change", () => syncPathMatrixViz(root));
  }
}

/** 收集 path_matrix 保存体（只改 rebalance_timing，不动 t0）。 */
export function collectPathMatrixForm(root) {
  if (!root) return null;
  const num = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return fallback;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : fallback;
  };
  const chk = (name, fallback = true) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el ? !!el.checked : fallback;
  };
  const enter = Math.max(0.01, Math.min(num("pm_path_enter", 0.1), 5));
  let half = Math.max(0.01, Math.min(num("pm_path_half", 0.5), 5));
  let full = Math.max(0.01, Math.min(num("pm_path_full", 1), 10));
  if (half > full) half = full;
  let wTrade = Math.max(0, Math.min(num("pm_fusion_w_trade", 0.5), 1));
  let wNc = Math.max(0, Math.min(num("pm_fusion_w_nc", 0.5), 1));
  const wSum = wTrade + wNc;
  if (wSum <= 1e-12) {
    wTrade = 0.5;
    wNc = 0.5;
  } else {
    wTrade /= wSum;
    wNc /= wSum;
  }
  return {
    lock: true,
    rebalance_timing: {
      path_matrix: {
        enabled: true,
        mode: "path",
        path_enter: enter,
        path_half: half,
        path_full: full,
        y_on_allow: Math.max(0, Math.min(num("pm_y_on_allow", 0.5), 5)),
        y_on_half: Math.max(-5, Math.min(num("pm_y_on_half", 0.1), 5)),
        fusion_w_trade: Math.round(wTrade * 1000) / 1000,
        fusion_w_nowcast: Math.round(wNc * 1000) / 1000,
        require_path_on_same_sign: chk("pm_require_path_on", true),
        require_nowcast_for_open: false,
        allow_pending_exit_on_path_low: chk("pm_pending_exit", true),
      },
    },
  };
}

/** 做 T 区块根节点：复选框在 form 外，须从 section 查找。 */
function t0PanelRoot(formRoot) {
  if (formRoot) {
    const fromForm = formRoot.closest("#follow-section-t0, .follow-ops-t0");
    if (fromForm) return fromForm;
  }
  return (
    document.getElementById("follow-section-t0") ||
    document.querySelector(".follow-ops-t0") ||
    formRoot ||
    document
  );
}

/**
 * 做T回测范围：表单复选框 + Alt 快捷键。路径固定 5m first_touch（已删除日线模拟）。
 * @param {HTMLElement|null} root 通常为 #paper-t0-form；复选框在同 section 内 form 外
 * @param {{ altKey?: boolean, selectedCode?: string|null }} evt
 */
export function resolveT0BacktestScope(root, evt = {}) {
  const panel = t0PanelRoot(root);
  const selectedCode = String(evt.selectedCode || "").trim();
  const alt = !!evt.altKey;
  const onlyEl = panel.querySelector('[name="t0_bt_only_selected"]');
  const wantOnly = alt || !!(onlyEl && onlyEl.checked);
  if (wantOnly && !selectedCode) {
    return {
      onlySelected: false,
      useMinute: true,
      error: "请先点击持仓表选中一只股票，或取消「仅选中」",
    };
  }
  return {
    onlySelected: wantOnly && !!selectedCode,
    useMinute: true,
    error: null,
  };
}

/**
 * 做T回测请求体：直接读表单当前值（不必先点保存）。
 * @param {{ useMinute?: boolean, onlySelected?: boolean, selectedCode?: string }} opts
 */
export function collectT0BacktestBody(root, opts = {}) {
  const patch = collectExecutionForm(root) || { t0: {} };
  const t0 = patch.t0 || {};
  let lookback = persistT0Lookback(root) ?? 10;
  // 全持仓：回看自动封顶，否则 10 日×20+ 票易超前端超时
  if (!opts.onlySelected) {
    lookback = Math.min(lookback, 12);
  }
  const body = {
    from_paper: true,
    lookback,
    use_minute: true,
    compare_daily: false,
    t0_ratio: 1.0,
    fill_mode: "trigger",
    fill_mode_sell_then_buy: "trigger",
    fill_mode_buy_then_sell: "trigger",
    direction: "dual_y",
    path_mode: "first_touch",
    must_cover_same_day_sell_then_buy: t0.must_cover_same_day_sell_then_buy !== false,
    must_cover_same_day_buy_then_sell:
      (t0.must_cover_same_day_buy_then_sell ?? t0.must_cover_same_day) !== false,
    must_cover_same_day:
      (t0.must_cover_same_day_buy_then_sell ?? t0.must_cover_same_day) !== false,
    t0_close_band_delta_pct: t0.t0_close_band_delta_pct != null ? t0.t0_close_band_delta_pct : 0.2,
    t0_price_space_gate: t0.t0_price_space_gate !== false,
    t0_price_space_max_dev_pct:
      t0.t0_price_space_max_dev_pct != null ? t0.t0_price_space_max_dev_pct : 5,
    t0_price_space_prev_dev_pct:
      t0.t0_price_space_prev_dev_pct != null ? t0.t0_price_space_prev_dev_pct : 5,
    t0_round_ratio: t0.t0_round_ratio != null ? t0.t0_round_ratio : 0.4,
    t0_max_position_pct: t0.t0_max_position_pct != null ? t0.t0_max_position_pct : 1.0,
    t0_slots_max_rounds: t0.t0_slots_max_rounds != null ? t0.t0_slots_max_rounds : 5,
    y_enter_enabled: t0.y_enter_enabled !== false,
    y_enter_alt_enabled: t0.y_enter_alt_enabled !== false,
    y_tau_enter: (() => {
      const n = Number(t0.y_tau_enter);
      if (!Number.isFinite(n)) return 0.01;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tau_enter_sell_then_buy: (() => {
      const n = Number(t0.y_tau_enter_sell_then_buy ?? t0.y_tau_enter);
      if (!Number.isFinite(n)) return 0.01;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tau_enter_buy_then_sell: (() => {
      const n = Number(t0.y_tau_enter_buy_then_sell ?? t0.y_tau_enter);
      if (!Number.isFinite(n)) return 0.01;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tau_enter_alt: (() => {
      const n = Number(t0.y_tau_enter_alt);
      if (!Number.isFinite(n)) return 0.4;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter: (() => {
      const n = Number(t0.y_path_enter);
      if (!Number.isFinite(n)) return 0.01;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter_sell_then_buy: (() => {
      const n = Number(t0.y_path_enter_sell_then_buy ?? t0.y_path_enter);
      if (!Number.isFinite(n)) return 0.01;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter_buy_then_sell: (() => {
      const n = Number(t0.y_path_enter_buy_then_sell ?? t0.y_path_enter);
      if (!Number.isFinite(n)) return 0.01;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter_alt: (() => {
      const n = Number(t0.y_path_enter_alt);
      if (!Number.isFinite(n)) return 0.4;
      return Math.max(0, Math.min(n, 100));
    })(),
    r_tau_enter: (() => {
      const n = Number(t0.r_tau_enter);
      if (!Number.isFinite(n)) return 0.1;
      return Math.max(0, Math.min(n, 1));
    })(),
    r_tau_enter_alt: (() => {
      const n = Number(t0.r_tau_enter_alt);
      if (!Number.isFinite(n)) return 0.4;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_use_path: t0.y_use_path !== false,
    y_path_strong: t0.y_path_strong != null ? t0.y_path_strong : 0.2,
    y_complexity_max: (() => {
      const n = Number(t0.y_complexity_max ?? t0.y_cx_max);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tpd_max: (() => {
      const n = Number(t0.y_tpd_max);
      if (!Number.isFinite(n)) return 0.4;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_complexity_max_alt: (() => {
      const n = Number(t0.y_complexity_max_alt);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tpd_max_alt: (() => {
      const n = Number(t0.y_tpd_max_alt);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tau_leg1_prior_mode: normalizeTauLeg1PriorMode(
      t0.y_tau_leg1_prior_mode,
      t0.y_tau_leg1_prior
    ),
    y_tau_leg1_prior_risk: t0.y_tau_leg1_prior_risk != null ? t0.y_tau_leg1_prior_risk : 0.1,
    y_tau_leg1_prior_shift_scale:
      t0.y_tau_leg1_prior_shift_scale != null ? t0.y_tau_leg1_prior_shift_scale : 0.1,
    t0_pm_degrade_sell_then_buy: t0.t0_pm_degrade_sell_then_buy != null ? t0.t0_pm_degrade_sell_then_buy : "13:00",
    t0_pm_degrade_buy_then_sell:
      t0.t0_pm_degrade_buy_then_sell ?? t0.t0_pm_degrade ?? "13:00",
    t0_pm_degrade: t0.t0_pm_degrade_buy_then_sell ?? t0.t0_pm_degrade ?? "13:00",
    t0_pm_chase_interval_min_sell_then_buy:
      t0.t0_pm_chase_interval_min_sell_then_buy ??
      t0.t0_pm_chase_interval_min ??
      5,
    t0_pm_chase_interval_min_buy_then_sell:
      t0.t0_pm_chase_interval_min_buy_then_sell ??
      t0.t0_pm_chase_interval_min ??
      5,
    t0_pm_chase_interval_min:
      t0.t0_pm_chase_interval_min_buy_then_sell ??
      t0.t0_pm_chase_interval_min ??
      5,
    t0_stop_pct_buy_then_sell:
      t0.t0_stop_pct_buy_then_sell != null ? t0.t0_stop_pct_buy_then_sell : 1.2,
    t0_stop_pct_sell_then_buy:
      t0.t0_stop_pct_sell_then_buy != null ? t0.t0_stop_pct_sell_then_buy : 1.2,
    t0_stop_arm_bars: t0.t0_stop_arm_bars != null ? t0.t0_stop_arm_bars : 2,
    t0_stop_on_close: t0.t0_stop_on_close !== false,
  };
  if (t0.min_range_pct_sell_then_buy != null) body.min_range_pct_sell_then_buy = t0.min_range_pct_sell_then_buy;
  if (t0.min_range_pct_buy_then_sell != null) body.min_range_pct_buy_then_sell = t0.min_range_pct_buy_then_sell;
  if (t0.min_range_pct != null) body.min_range_pct = t0.min_range_pct;
  else body.min_range_pct = 0;
  if (opts.onlySelected && opts.selectedCode) body.code = opts.selectedCode;
  return body;
}

export function renderExecutionDiffHtml(diff) {
  if (!diff || !diff.ok) {
    return `<p class="quant-sub">${escapeText((diff && diff.error) || "无 diff")}</p>`;
  }
  if (!diff.changed) {
    return `<p class="quant-sub">与策略默认一致</p>`;
  }
  const rows = [...(diff.t0_changes || []), ...(diff.coupling_changes || [])].slice(0, 12);
  return (
    `<p class="quant-sub">相对 Spec 有 ${rows.length} 项差异</p>` +
    `<table class="quant-weight-table"><thead><tr><th>字段</th><th>Spec</th><th>纸面</th></tr></thead><tbody>` +
    rows
      .map(
        (r) =>
          `<tr><td>${escapeText(r.path)}</td>` +
          `<td>${escapeText(String(r.from))}</td>` +
          `<td>${escapeText(String(r.to))}</td></tr>`
      )
      .join("") +
    `</tbody></table>`
  );
}
