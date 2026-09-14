/** Paper · Execution / 做T 生效规则卡 + 编辑表单。 */

import { escapeText } from "./fmt.js";

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

/** v6 做 T 选腿说明（旧 y_tau_map / oc先验已忽略）。 */
export function yTauMapScoreTip(_mode, _enter = 0) {
  return "v6 做T：C_τ=O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)；upper/lower=C_τ×(1±δ/100)。C>upper 反T、C<lower 正T；leg2=C_τ。ŷ_τc 只对照、不参与。C=本根5m收价；|ŷ_oc|/y_hl 入场；|S−1| 超阈跳过。";
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
    specKpi("策略", "超额带宽", "C_τ=O×(1+clip(ŷ_oc×scale)/100)；C 破上带反T、破下带正T；leg2 冻结 C_τ；每轮默认 40%；11:00 后不开 leg1") +
    specKpi(
      "选腿",
      (() => {
        const d =
          t0.t0_close_band_delta_pct != null ? Number(t0.t0_close_band_delta_pct) : 3;
        const sc =
          t0.t0_y_oc_target_scale != null ? Number(t0.t0_y_oc_target_scale) : 10;
        const rr =
          t0.t0_round_ratio != null ? Math.round(Number(t0.t0_round_ratio) * 100) : 40;
        const mr =
          t0.t0_slots_max_rounds != null ? Number(t0.t0_slots_max_rounds) : 5;
        const cap =
          t0.t0_max_position_pct != null ? Math.round(Number(t0.t0_max_position_pct) * 100) : 100;
        return `C_τ±${d}%·×${sc}·${rr}%×≤${mr}·≤${cap}%`;
      })(),
      "C_τ=O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)；r=(C/C_τ−1)% 相对 ±δ 选向；leg2=C_τ；C=本根5m收价；每轮比例×最多轮数×满仓上限"
    ) +
    specKpi(
      "τc强",
      (() => {
        const s = Number(t0.y_tc_strong ?? t0.y_τc_strong);
        if (!Number.isFinite(s) || s >= 1) return "关";
        if (s <= 0) return "全顺带";
        return `|ŷ_τc|>${s}%逆带跳过`;
      })(),
      "破带后 ŷ_τc 须向 C_τ 回归；τc强%=0 任意有符号须同号，1=关。不改 C_τ。"
    ) +
    specKpi(
      "门槛1",
      (() => {
        if (t0.y_enter_enabled === false) return "关";
        const tau = Number(t0.y_tau_enter);
        const tc = Number(t0.y_tc_enter ?? t0.y_τc_enter);
        const path = Number(t0.y_path_enter);
        const cx = Number(t0.y_complexity_max ?? t0.y_cx_max);
        const tpd = Number(t0.y_tpd_max);
        const tauLbl = Number.isFinite(tau) ? (tau > 0 ? `|oc|≥${tau}%` : "oc关") : "oc关";
        const tcLbl = Number.isFinite(tc) ? (tc > 0 ? `|tc|≥${tc}%` : "tc关") : "tc关";
        const pathLblG = Number.isFinite(path) ? (path > 0 ? `|path|≥${path}%` : "path关") : "path关";
        const cxLbl = Number.isFinite(cx) ? (cx >= 1 ? "cx关" : `cx≤${cx}`) : "cx关";
        const tpdLbl = Number.isFinite(tpd) ? (tpd >= 1 ? "tpd关" : `tpd≤${tpd}`) : "tpd关";
        return `${tauLbl}·${tcLbl}·${pathLblG}·${cxLbl}·${tpdLbl}`;
      })(),
      "门槛1：启用时 |ŷ_oc| / |ŷ_τc| / |y_hl| 入场 + cx/tpd 风险"
    ) +
    specKpi(
      "门槛2",
      (() => {
        if (t0.y_enter_alt_enabled === false) return "关";
        const tau = Number(t0.y_tau_enter_alt);
        const tc = Number(t0.y_tc_enter_alt ?? t0.y_τc_enter_alt);
        const path = Number(t0.y_path_enter_alt);
        const cx = Number(t0.y_complexity_max_alt);
        const tpd = Number(t0.y_tpd_max_alt);
        const tauLbl = Number.isFinite(tau) ? (tau > 0 ? `|oc|≥${tau}%` : "oc关") : "oc关";
        const tcLbl = Number.isFinite(tc) ? (tc > 0 ? `|tc|≥${tc}%` : "tc关") : "tc关";
        const pathLblG = Number.isFinite(path) ? (path > 0 ? `|path|≥${path}%` : "path关") : "path关";
        const cxLbl = Number.isFinite(cx) ? (cx >= 1 ? "cx关" : `cx≤${cx}`) : "cx关";
        const tpdLbl = Number.isFinite(tpd) ? (tpd >= 1 ? "tpd关" : `tpd≤${tpd}`) : "tpd关";
        return `${tauLbl}·${tcLbl}·${pathLblG}·${cxLbl}·${tpdLbl}`;
      })(),
      "门槛2：启用时 |ŷ_oc| / |ŷ_τc| / |y_hl| 入场 + cx/tpd 风险"
    ) +
    specKpi("路径", pathLbl, "分钟触价路径") +
    specKpi("成交", fillLbl, "全量触价 trigger；表单不再提供 mid/optimistic") +
    specKpi(
      "止损",
      (() => {
        const bts = Number(t0.t0_stop_pct_buy_then_sell);
        const stb = Number(t0.t0_stop_pct_sell_then_buy);
        const arm = Number(t0.t0_stop_arm_bars);
        const armLbl = Number.isFinite(arm) ? `${Math.round(arm)}根` : "1根";
        const fmt = (n) => (Number.isFinite(n) && n > 0 ? `${n}%` : "关");
        return `正${fmt(bts)}/反${fmt(stb)}·${armLbl}·收盘`;
      })(),
      "相对该轮第一腿成交价；正T跌破卖旧 / 反T涨破买回；多轮共用同一套%；延迟共用；止损固定收盘破线确认"
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

/** 交易执行页只读：调仓规则摘要（改规则在历史回测）。 */
export function renderRebalanceRulesHtml(execution) {
  if (!execution || execution.ok === false) {
    const err = (execution && execution.error) || "无法加载调仓规则";
    return `<p class="quant-sub">${escapeText(err)}</p>`;
  }
  const timing = execution.rebalance_timing || {};
  const pm =
    (timing.rank_lots && typeof timing.rank_lots === "object" && timing.rank_lots) ||
    (timing.path_matrix && typeof timing.path_matrix === "object" && timing.path_matrix) ||
    {};
  const enter = rankScoreToPct(pm.rank_enter);
  const strong = rankScoreToPct(pm.rank_strong);
  const alpha =
    pm.fusion_w_co != null
      ? Number(pm.fusion_w_co)
      : pm.y_on_alpha != null
        ? Number(pm.y_on_alpha)
        : 0;
  const wt =
    pm.fusion_w_oo != null
      ? Number(pm.fusion_w_oo)
      : pm.fusion_w_trade != null
        ? Number(pm.fusion_w_trade)
        : 0.5;
  const wn =
    pm.fusion_w_oc != null
      ? Number(pm.fusion_w_oc)
      : pm.fusion_w_nowcast != null
        ? Number(pm.fusion_w_nowcast)
        : 0.5;
  const cap = pm.holdings_mv_cap != null ? Number(pm.holdings_mv_cap) : 150000;
  const fmtN = (n, d) => (Number.isFinite(n) ? n.toFixed(d) : "—");
  const capLbl = Number.isFinite(cap) && cap > 0 ? `${Math.round(cap / 10000)}万` : "不限";
  return (
    `<div class="paper-t0-spec">` +
    `<header class="paper-t0-spec-head">` +
    `<h4 class="paper-t0-spec-head-title">生效调仓</h4>` +
    `<div class="paper-t0-spec-kpi-strip" aria-label="调仓核心参数">` +
    specKpi("模式", "rank_lots", "09:30 开盘 · 现价 200/500 股") +
    specKpi("入场", `${fmtN(Number(enter), 1)}%`, "ranking 须大于此值才建仓/加仓") +
    specKpi("强买", `${fmtN(Number(strong), 1)}%`, "超过买 500 股，否则 200") +
    specKpi("w_co", fmtN(alpha, 1), "叠进 ŷ_oc 的隔夜系数；0=不叠") +
    specKpi("ranking", `${fmtN(wt, 2)}/${fmtN(wn, 2)}`, "w_oo / w_oc") +
    specKpi("市值上限", capLbl, "本笔将超则跳过该买") +
    `</div></header></div>`
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
  set("t0_close_band_delta_pct", t0.t0_close_band_delta_pct != null ? t0.t0_close_band_delta_pct : 3);
  set("t0_y_oc_target_scale", t0.t0_y_oc_target_scale != null ? t0.t0_y_oc_target_scale : 10);
  set("t0_y_oc_l", t0.t0_y_oc_l != null ? t0.t0_y_oc_l : -3);
  set("t0_y_oc_u", t0.t0_y_oc_u != null ? t0.t0_y_oc_u : 3);
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
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_path_enter", (() => {
    const n = Number(t0.y_path_enter);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_tau_enter_alt", (() => {
    const n = Number(t0.y_tau_enter_alt);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_path_enter_alt", (() => {
    const n = Number(t0.y_path_enter_alt);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_tc_enter", (() => {
    const n = Number(t0.y_tc_enter ?? t0.y_τc_enter);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_tc_enter_alt", (() => {
    const n = Number(t0.y_tc_enter_alt ?? t0.y_τc_enter_alt);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(n, 100));
  })());
  set("y_path_strong", t0.y_path_strong != null ? t0.y_path_strong : 5);
  set(
    "y_tc_strong",
    (() => {
      const n = Number(t0.y_tc_strong ?? t0.y_τc_strong);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })()
  );
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
      if (!Number.isFinite(n)) return 1;
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
    t0.t0_stop_arm_bars != null ? t0.t0_stop_arm_bars : 1
  );
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
    t0_close_band_delta_pct: Math.max(0, Math.min(num("t0_close_band_delta_pct", 3), 10)),
    t0_y_oc_target_scale: Math.max(0, Math.min(num("t0_y_oc_target_scale", 10), 100)),
    t0_y_oc_l: Math.max(-20, Math.min(num("t0_y_oc_l", -3), 20)),
    t0_y_oc_u: Math.max(-20, Math.min(num("t0_y_oc_u", 3), 20)),
    t0_price_space_gate: chk("t0_price_space_gate", true),
    t0_price_space_max_dev_pct: Math.max(0, Math.min(num("t0_price_space_max_dev_pct", 5), 5)),
    t0_price_space_prev_dev_pct: Math.max(0, Math.min(num("t0_price_space_prev_dev_pct", 5), 5)),
    t0_round_ratio: Math.max(0.05, Math.min(num("t0_round_ratio", 0.4), 1)),
    t0_max_position_pct: Math.max(0.05, Math.min(num("t0_max_position_pct", 1), 1)),
    t0_slots_max_rounds: Math.max(0, Math.min(Math.round(num("t0_slots_max_rounds", 5)), 16)),
    y_enter_enabled: chk("y_enter_enabled", true),
    y_enter_alt_enabled: chk("y_enter_alt_enabled", true),
    y_tau_enter: Math.max(0, Math.min(num("y_tau_enter", 0), 100)),
    y_tau_enter_sell_then_buy: Math.max(0, Math.min(num("y_tau_enter", 0), 100)),
    y_tau_enter_buy_then_sell: Math.max(0, Math.min(num("y_tau_enter", 0), 100)),
    y_tau_enter_alt: Math.max(0, Math.min(num("y_tau_enter_alt", 0), 100)),
    y_path_enter: Math.max(0, Math.min(num("y_path_enter", 0), 100)),
    y_path_enter_sell_then_buy: Math.max(0, Math.min(num("y_path_enter", 0), 100)),
    y_path_enter_buy_then_sell: Math.max(0, Math.min(num("y_path_enter", 0), 100)),
    y_path_enter_alt: Math.max(0, Math.min(num("y_path_enter_alt", 0), 100)),
    y_tc_enter: Math.max(0, Math.min(num("y_tc_enter", 0), 100)),
    y_τc_enter: Math.max(0, Math.min(num("y_tc_enter", 0), 100)),
    y_tc_enter_alt: Math.max(0, Math.min(num("y_tc_enter_alt", 0), 100)),
    y_τc_enter_alt: Math.max(0, Math.min(num("y_tc_enter_alt", 0), 100)),
    y_use_path: true,
    y_path_strong: Math.max(0, Math.min(num("y_path_strong", 5), 5)),
    y_tc_strong: Math.max(0, Math.min(num("y_tc_strong", 1), 1)),
    y_τc_strong: Math.max(0, Math.min(num("y_tc_strong", 1), 1)),
    y_complexity_max: (() => {
      const n = num("y_complexity_max", 1);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tpd_max: (() => {
      const n = num("y_tpd_max", 1);
      if (!Number.isFinite(n)) return 1;
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
    t0_stop_arm_bars: Math.max(0, Math.min(Math.round(num("t0_stop_arm_bars", 1)), 48)),
    t0_stop_on_close: true,
  };
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

/** 毛收益乘数 1.01/1.02、旧 0.20 → 净收益 0.01/0.02。 */
function coerceRankThreshold(raw, fallback) {
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  if (n >= 0.5) {
    if (Math.abs(n - 1) < 1e-9) return 0.01;
    if (Math.abs(n - 1.002) < 1e-6) return 0.02;
    return Math.max(0, n - 1);
  }
  if (Math.abs(n - 0.2) < 1e-6) return 0.02;
  return n;
}

const RANK_PCT_DEFAULT = 1.2;

/** 引擎净收益小数 → 表单百分数（0.012 → 1.2）。 */
function rankScoreToPct(raw, fallbackPct = RANK_PCT_DEFAULT) {
  const score = coerceRankThreshold(raw, fallbackPct / 100);
  const pct = Number(score) * 100;
  if (!Number.isFinite(pct)) return fallbackPct;
  return Math.round(Math.max(0, Math.min(10, pct)) * 10) / 10;
}

/** 表单百分数 → 引擎净收益小数（1.2 → 0.012）。 */
function rankPctToScore(raw, fallbackPct = RANK_PCT_DEFAULT) {
  let pct = fallbackPct;
  if (raw != null && raw !== "") {
    const n = Number(raw);
    if (Number.isFinite(n)) pct = n;
  }
  const score = pct / 100;
  return Math.round(Math.max(0, Math.min(0.1, score)) * 10000) / 10000;
}

/** 从 execution.rebalance_timing.path_matrix 填调仓表单。 */
export function fillPathMatrixForm(root, execution) {
  if (!root) return;
  const timing =
    (execution && execution.rebalance_timing) ||
    (execution && execution.execution && execution.execution.rebalance_timing) ||
    {};
  const pm =
    (timing && timing.rank_lots && typeof timing.rank_lots === "object"
      ? timing.rank_lots
      : timing && timing.path_matrix && typeof timing.path_matrix === "object"
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
  set("pm_rank_enter", rankScoreToPct(pm.rank_enter));
  set("pm_rank_strong", rankScoreToPct(pm.rank_strong));
  set(
    "pm_y_on_alpha",
    pm.fusion_w_co != null ? pm.fusion_w_co : pm.y_on_alpha != null ? pm.y_on_alpha : 0
  );
  set(
    "pm_holdings_mv_cap",
    pm.holdings_mv_cap != null ? pm.holdings_mv_cap : 150000
  );
  set(
    "pm_fusion_w_oo",
    pm.fusion_w_oo != null ? pm.fusion_w_oo : pm.fusion_w_trade != null ? pm.fusion_w_trade : 0.5
  );
  set(
    "pm_fusion_w_nc",
    pm.fusion_w_oc != null ? pm.fusion_w_oc : pm.fusion_w_nowcast != null ? pm.fusion_w_nowcast : 0.5
  );
}

function _normWeightPair(a, b) {
  let x = Math.max(0, Math.min(Number.isFinite(a) ? a : 0.5, 1));
  let y = Math.max(0, Math.min(Number.isFinite(b) ? b : 0.5, 1));
  const s = x + y;
  if (s <= 1e-12) return { a: 0.5, b: 0.5 };
  return { a: x / s, b: y / s };
}

/** 填 dual_score 一层权重（w_eod / w_tau；做 T ŷ_trade）。 */
export function fillDualScoreForm(root, ds) {
  if (!root || !ds) return;
  const set = (name, val) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || val == null) return;
    el.value = String(val);
  };
  set("pm_w_eod", ds.w_eod != null ? ds.w_eod : 0.5);
  set("pm_w_tau", ds.w_tau != null ? ds.w_tau : 0.5);
}

/** 收集 dual_score 保存体；不改 τ 闸。 */
export function collectDualScorePatch(root) {
  if (!root) return null;
  if (!root.querySelector('[name="pm_w_eod"]') && !root.querySelector('[name="pm_w_tau"]')) {
    return null;
  }
  const num = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return fallback;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : fallback;
  };
  const pair = _normWeightPair(num("pm_w_eod", 0.5), num("pm_w_tau", 0.5));
  return {
    fusion_mode: "blend",
    w_eod: Math.round(pair.a * 1000) / 1000,
    w_tau: Math.round(pair.b * 1000) / 1000,
    w_mode: "fixed",
    note: "follow rank_lots UI",
  };
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
  const numFirst = (names, fallback) => {
    for (const name of names) {
      const el = root.querySelector(`[name="${name}"]`);
      if (!el || el.value === "") continue;
      const n = Number(el.value);
      if (Number.isFinite(n)) return n;
    }
    return fallback;
  };
  let enter = rankPctToScore(num("pm_rank_enter", RANK_PCT_DEFAULT));
  let strong = rankPctToScore(num("pm_rank_strong", RANK_PCT_DEFAULT));
  if (strong < enter) strong = enter;
  const yOnAlpha = Math.max(0, Math.min(num("pm_y_on_alpha", 0), 10));
  const mvEl = root.querySelector('[name="pm_holdings_mv_cap"]');
  const mvCap =
    mvEl && mvEl.value !== ""
      ? Math.max(0, Math.min(num("pm_holdings_mv_cap", 150000), 1e8))
      : 150000;
  let wOo = Math.max(0, Math.min(numFirst(["pm_fusion_w_oo", "pm_fusion_w_trade"], 0.5), 1));
  let wOc = Math.max(0, Math.min(numFirst(["pm_fusion_w_nc", "pm_fusion_w_oc"], 0.5), 1));
  const wSum = wOo + wOc;
  if (wSum <= 1e-12) {
    wOo = 0.5;
    wOc = 0.5;
  } else {
    wOo /= wSum;
    wOc /= wSum;
  }
  return {
    lock: true,
    rebalance_timing: {
      rank_lots: {
        enabled: true,
        mode: "rank_lots",
        rank_enter: enter,
        rank_strong: strong,
        y_on_alpha: Math.round(yOnAlpha * 1000) / 1000,
        fusion_w_co: Math.round(yOnAlpha * 1000) / 1000,
        holdings_mv_cap: Math.round(mvCap),
        fusion_w_oo: Math.round(wOo * 1000) / 1000,
        fusion_w_oc: Math.round(wOc * 1000) / 1000,
        fusion_w_trade: Math.round(wOo * 1000) / 1000,
        fusion_w_nowcast: Math.round(wOc * 1000) / 1000,
      },
      path_matrix: {
        enabled: true,
        mode: "rank_lots",
        rank_enter: enter,
        rank_strong: strong,
        y_on_alpha: Math.round(yOnAlpha * 1000) / 1000,
        fusion_w_co: Math.round(yOnAlpha * 1000) / 1000,
        holdings_mv_cap: Math.round(mvCap),
        fusion_w_oo: Math.round(wOo * 1000) / 1000,
        fusion_w_oc: Math.round(wOc * 1000) / 1000,
        fusion_w_trade: Math.round(wOo * 1000) / 1000,
        fusion_w_nowcast: Math.round(wOc * 1000) / 1000,
      },
    },
  };
}

/** 做 T 区块根节点：复选框在 form 外，须从 section 查找。 */
function t0PanelRoot(formRoot) {
  if (formRoot) {
    const fromForm = formRoot.closest(
      "#follow-section-t0, .follow-ops-t0, #replay-section-t0, .replay-t0-section"
    );
    if (fromForm) return fromForm;
  }
  return (
    document.getElementById("replay-section-t0") ||
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
 * 做T回测本金 / 每票股数（表单专用，不写入 execution 规则）。
 * @param {HTMLElement|null} root
 */
export function readT0BtSizing(root) {
  const panel = t0PanelRoot(root);
  const num = (name, fallback) => {
    const el = panel && panel.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return fallback;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : fallback;
  };
  let shares = Math.round(num("t0_bt_shares", 1000) / 100) * 100;
  shares = Math.max(100, Math.min(shares, 100000));
  let cash = num("t0_bt_cash", 200000);
  cash = Math.max(10000, Math.min(cash, 1e8));
  return { shares, cash };
}

/**
 * 用上次回测结果回填本金 / 股数（有值才写，避免冲掉表单默认）。
 * @param {HTMLElement|null} root
 * @param {object} data
 */
export function fillT0BtSizing(root, data) {
  const panel = t0PanelRoot(root);
  if (!panel || !data) return;
  const set = (name, val) => {
    const el = panel.querySelector(`[name="${name}"]`);
    if (!el || val == null || !Number.isFinite(Number(val))) return;
    el.value = String(val);
  };
  const shares = data.virtual_shares ?? data.request?.initial_shares;
  const cash = data.virtual_cash ?? data.request?.initial_cash;
  if (shares != null) set("t0_bt_shares", shares);
  if (cash != null) set("t0_bt_cash", cash);
}

/**
 * 做T回测请求体：直接读表单当前值（不必先点保存）。
 * @param {{ useMinute?: boolean, onlySelected?: boolean, selectedCode?: string }} opts
 */
export function collectT0BacktestBody(root, opts = {}) {
  const patch = collectExecutionForm(root) || { t0: {} };
  const t0 = patch.t0 || {};
  const sizing = readT0BtSizing(root);
  let lookback = persistT0Lookback(root) ?? 10;
  // 全持仓：回看自动封顶，否则 10 日×20+ 票易超前端超时
  if (!opts.onlySelected) {
    lookback = Math.min(lookback, 12);
  }
  const body = {
    from_paper: true,
    lookback,
    use_minute: true,
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
    t0_close_band_delta_pct: t0.t0_close_band_delta_pct != null ? t0.t0_close_band_delta_pct : 3,
    t0_y_oc_target_scale: t0.t0_y_oc_target_scale != null ? t0.t0_y_oc_target_scale : 10,
    t0_y_oc_l: t0.t0_y_oc_l != null ? t0.t0_y_oc_l : -3,
    t0_y_oc_u: t0.t0_y_oc_u != null ? t0.t0_y_oc_u : 3,
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
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tau_enter_sell_then_buy: (() => {
      const n = Number(t0.y_tau_enter_sell_then_buy ?? t0.y_tau_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tau_enter_buy_then_sell: (() => {
      const n = Number(t0.y_tau_enter_buy_then_sell ?? t0.y_tau_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tau_enter_alt: (() => {
      const n = Number(t0.y_tau_enter_alt);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter: (() => {
      const n = Number(t0.y_path_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter_sell_then_buy: (() => {
      const n = Number(t0.y_path_enter_sell_then_buy ?? t0.y_path_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter_buy_then_sell: (() => {
      const n = Number(t0.y_path_enter_buy_then_sell ?? t0.y_path_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_path_enter_alt: (() => {
      const n = Number(t0.y_path_enter_alt);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tc_enter: (() => {
      const n = Number(t0.y_tc_enter ?? t0.y_τc_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_τc_enter: (() => {
      const n = Number(t0.y_tc_enter ?? t0.y_τc_enter);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_tc_enter_alt: (() => {
      const n = Number(t0.y_tc_enter_alt ?? t0.y_τc_enter_alt);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_τc_enter_alt: (() => {
      const n = Number(t0.y_tc_enter_alt ?? t0.y_τc_enter_alt);
      if (!Number.isFinite(n)) return 0;
      return Math.max(0, Math.min(n, 100));
    })(),
    y_use_path: true,
    y_path_strong: t0.y_path_strong != null ? t0.y_path_strong : 5,
    y_tc_strong: (() => {
      const n = Number(t0.y_tc_strong ?? t0.y_τc_strong);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_τc_strong: (() => {
      const n = Number(t0.y_tc_strong ?? t0.y_τc_strong);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_complexity_max: (() => {
      const n = Number(t0.y_complexity_max ?? t0.y_cx_max);
      if (!Number.isFinite(n)) return 1;
      return Math.max(0, Math.min(n, 1));
    })(),
    y_tpd_max: (() => {
      const n = Number(t0.y_tpd_max);
      if (!Number.isFinite(n)) return 1;
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
    t0_stop_arm_bars: t0.t0_stop_arm_bars != null ? t0.t0_stop_arm_bars : 1,
    t0_stop_on_close: true,
    initial_shares: sizing.shares,
    initial_cash: sizing.cash,
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
