/** Paper · Execution / 做T 生效规则卡 + 编辑表单。 */

import { escapeText } from "./fmt.js";

export const Y_TAU_MAP_LABELS = {
  trend: "符号定方向",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 表单 / 规则卡选项文案（与 follow 下拉 value 一一对应）。 */
export const Y_TAU_MAP_SELECT_LABELS = {
  trend: "符号定方向",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 规则卡 / 摘要用短标签（表格列头等紧凑场景）。 */
export const Y_TAU_MAP_SHORT = {
  trend: "符号",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 与后端 normalize_y_tau_map 对齐（scalp 等别名 → trend）。 */
export function normalizeYTauMap(mode) {
  const k = String(mode || "trend").trim().toLowerCase();
  if (k === "scalp" || k === "follow" || k === "momentum" || k === "invert") {
    return "trend";
  }
  if (k === "long" || k === "always_long") return "fixed_long";
  if (k === "reverse" || k === "always_reverse") return "fixed_reverse";
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

export function yTauMapRuleLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_SELECT_LABELS[k] || k;
}

const FILL_MODE_LABELS = {
  trigger: "触价",
  mid: "触价·中点",
  optimistic: "乐观（高卖低买）",
};

const PATH_MODE_LABELS = {
  first_touch: "5m 首触达",
};

const COUPLING_LABELS = {
  independent: "独立",
  skip_if_avoid: "avoid 时跳过",
  only_if_hold: "仅 hold/watch",
};

function fmtThresholdPct(val, fallback) {
  const n = Number(val ?? fallback);
  if (!Number.isFinite(n)) return "—";
  const s = Math.abs(n) >= 1 ? n.toFixed(1) : n.toFixed(2);
  return `≥${s}%`;
}

function fmtTriggerPair(sell, buy) {
  const s = Number(sell);
  const b = Number(buy);
  if (!Number.isFinite(s) || !Number.isFinite(b)) return "—";
  return `卖 +${s}% · 买 −${b}%`;
}

/** dual_y 额度缩放区间（规则卡 gauge / 表注）。 */
export function adaptiveSizingBounds(t0 = {}) {
  const baseRatio = Number(t0.t0_ratio);
  const basePct = Number.isFinite(baseRatio) ? Math.round(baseRatio * 100) : 40;
  const boost = Number(t0.y_ratio_boost_cap ?? 1.25);
  const cut = Number(t0.y_ratio_cut ?? 0.75);
  const tauCap = Number(t0.y_ratio_tau_boost_cap ?? 1.15);
  const eodBoost = Number(t0.y_ratio_eod_align_boost ?? 1.1);
  const minPct = Math.max(5, Math.round(basePct * cut));
  const maxPct = Math.min(100, Math.round(basePct * boost * tauCap * eodBoost));
  return { basePct, minPct, maxPct };
}

/** dual_y 额度缩放只读摘要（规则卡 / 表注）。 */
export function adaptiveSizingRuleLabel(t0 = {}) {
  const { basePct, minPct, maxPct } = adaptiveSizingBounds(t0);
  return `基准 ${basePct}% · 有效约 ${minPct}–${maxPct}%`;
}

/** 成交日行 hover：基准动仓 → 当日有效动仓。 */
export function adaptiveSizingDayTip(day, rules = {}) {
  const eff = Number(day?.t0_ratio);
  const baseRaw = day?.t0_ratio_base ?? rules?.t0_ratio;
  const base = Number(baseRaw);
  if (!Number.isFinite(eff)) return "";
  const effPct = Math.round(eff * 100);
  if (!Number.isFinite(base)) return `有效动仓 ${effPct}%`;
  const basePct = Math.round(base * 100);
  if (Math.abs(eff - base) < 0.005) return `动仓 ${basePct}%（未缩放）`;
  return `动仓 ${basePct}% → ${effPct}%（ŷ 自适应）`;
}

/** dual_y 方向分说明（随 τ 映射变化）。enter 单位=收益百分点。 */
export function yTauMapScoreTip(mode, enter = 0.25) {
  const m = normalizeYTauMap(mode);
  const e = Number(enter);
  const thr = Number.isFinite(e) ? e : 0.25;
  const tail = "与 y_eod 冲突或 |y_trade| 不足则跳过。";
  if (m === "fixed_long") {
    return `dual_y[固定正T]：|y_τ|≥${thr}% 固定正T；${tail}`;
  }
  if (m === "fixed_reverse") {
    return `dual_y[固定反T]：|y_τ|≥${thr}% 固定反T；${tail}`;
  }
  return `dual_y：|y_τ|≥${thr}% 定方向（符号映射）；${tail}`;
}

/** 回测/预演响应可能只有 rules 或残缺 execution；补齐 t0 供规则卡渲染。 */
export function normalizeExecutionView(execution, rules) {
  if (!execution && !rules) return null;
  const base = execution ? { ...execution } : { ok: true };
  const t0 = { ...(rules || {}), ...(base.t0 || {}) };
  const hasDetail =
    t0.direction != null ||
    t0.path_mode != null ||
    t0.sell_trigger_pct != null ||
    t0.t0_ratio != null;
  if (!hasDetail && !base.effective_hash) return execution || null;
  return {
    ...base,
    ok: base.ok !== false,
    t0,
    coupling: base.coupling || {},
  };
}

function specMetric(label, value, tip = "") {
  const title = tip ? ` title="${escapeText(tip)}"` : "";
  return (
    `<div class="paper-t0-spec-metric"${title}>` +
    `<span class="paper-t0-spec-metric-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-spec-metric-v">${escapeText(String(value))}</span>` +
    `</div>`
  );
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

function specSubhead(label, value) {
  return (
    `<div class="paper-t0-spec-subhead">` +
    `<span class="paper-t0-spec-subhead-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-spec-subhead-v">${escapeText(String(value))}</span>` +
    `</div>`
  );
}

function specPanelTitle(kind, text) {
  return `<h5 class="paper-t0-spec-panel-title"><span class="paper-t0-spec-panel-mark is-${kind}" aria-hidden="true"></span>${escapeText(text)}</h5>`;
}

function buildTauMapVisualHtml(mode, enterRaw) {
  const k = normalizeYTauMap(mode);
  const gate = `|y_τ| ${fmtThresholdPct(enterRaw, 0.25)}`;

  function tauRow(polarity, sign, cond, action, actionKind, tip = "") {
    const title = tip ? ` title="${escapeText(tip)}"` : "";
    return (
      `<div class="paper-t0-spec-tau-row is-${polarity}"${title}>` +
      `<span class="paper-t0-spec-tau-sign">${sign}</span>` +
      `<span class="paper-t0-spec-tau-cond">${cond}</span>` +
      `<span class="paper-t0-spec-tau-arrow" aria-hidden="true">→</span>` +
      `<span class="paper-t0-spec-tau-act is-${actionKind}">${escapeText(action)}</span>` +
      `</div>`
    );
  }

  let rows = "";
  if (k === "fixed_long") {
    rows = tauRow("single", "±", `<span class="mono">|y_τ|</span> 达标`, "正T", "long", "固定正T");
  } else if (k === "fixed_reverse") {
    rows = tauRow("single", "±", `<span class="mono">|y_τ|</span> 达标`, "反T", "reverse", "固定反T");
  } else {
    rows =
      tauRow("up", "+", `<span class="mono">y_τ</span> &gt; 0`, "反T", "reverse", "卖高优先") +
      tauRow("down", "−", `<span class="mono">y_τ</span> &lt; 0`, "正T", "long", "买低优先");
  }

  return (
    `<div class="paper-t0-spec-panel-viz">` +
    specSubhead("门控", gate) +
    `<div class="paper-t0-spec-panel-body is-tau">` +
    `<div class="paper-t0-spec-tau-wrap">${rows}</div>` +
    `</div></div>`
  );
}

function buildSizingGaugeHtml(t0) {
  const { basePct, minPct, maxPct } = adaptiveSizingBounds(t0);
  const bandLeft = Math.max(0, minPct);
  const bandWidth = Math.max(2, maxPct - bandLeft);
  const tip = `基准 ${basePct}% × ŷ 自适应；弱信号缩至约 ${minPct}%，强信号可至 ${maxPct}%`;
  return (
    `<div class="paper-t0-spec-panel-viz">` +
    specSubhead("基准", `${basePct}%`) +
    `<div class="paper-t0-spec-panel-body is-gauge" title="${escapeText(tip)}">` +
    `<div class="paper-t0-spec-gauge-wrap">` +
    `<div class="paper-t0-spec-gauge-track" aria-hidden="true">` +
    `<span class="paper-t0-spec-gauge-band" style="left:${bandLeft}%;width:${bandWidth}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-min" style="left:${minPct}%" title="下限 ${minPct}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-base" style="left:${basePct}%" title="基准 ${basePct}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-max" style="left:${maxPct}%" title="上限 ${maxPct}%"></span>` +
    `</div>` +
    `<div class="paper-t0-spec-gauge-ticks" aria-hidden="true">` +
    `<span class="is-edge" style="left:0%">0</span>` +
    `<span class="is-min" style="left:${minPct}%">${minPct}</span>` +
    `<span class="is-base" style="left:${basePct}%">${basePct}</span>` +
    `<span class="is-max" style="left:${maxPct}%">${maxPct}</span>` +
    `<span class="is-edge" style="left:100%">100</span>` +
    `</div>` +
    `</div>` +
    `<div class="paper-t0-spec-gauge-legend">` +
    `<span class="paper-t0-spec-gauge-key"><i class="dot is-band"></i>有效区间</span>` +
    `<span class="paper-t0-spec-gauge-key"><i class="dot is-base"></i>基准</span>` +
    `<span class="paper-t0-spec-gauge-key is-range">${minPct}–${maxPct}%</span>` +
    `</div>` +
    `</div></div>`
  );
}

function buildDualYPipelineHtml(t0) {
  const pathLbl =
    PATH_MODE_LABELS[String(t0.path_mode || "first_touch").toLowerCase()] || "5m";
  const nodes = [
    { stage: "trade", role: "资格", label: "trade", sub: fmtThresholdPct(t0.y_trade_floor, 0.15), tip: "ŷ_trade 动仓门槛" },
    { stage: "tau", role: "方向", label: "τ", sub: fmtThresholdPct(t0.y_tau_enter, 0.25), tip: "ŷ_τ 定正/反 T" },
    { stage: "eod", role: "先验", label: "eod", sub: fmtThresholdPct(t0.y_eod_prior, 0.35), tip: "ŷ_eod 冲突过滤" },
    { stage: "path", role: "触价", label: "5m", sub: pathLbl, tip: "分钟路径首触达" },
    { stage: "on", role: "回补", label: "on", sub: fmtThresholdPct(t0.y_on_allow, 1.2), tip: "ŷ_on 收盘回补" },
  ];
  return nodes
    .map(
      (n, i) =>
        `<div class="paper-t0-spec-pipe-node" data-stage="${escapeText(n.stage)}" title="${escapeText(n.tip)}">` +
        `<span class="paper-t0-spec-pipe-step">${String(i + 1).padStart(2, "0")}</span>` +
        `<span class="paper-t0-spec-pipe-role">${escapeText(n.role)}</span>` +
        `<span class="paper-t0-spec-pipe-label">${escapeText(n.label)}</span>` +
        `<span class="paper-t0-spec-pipe-sub">${escapeText(n.sub)}</span>` +
        `</div>` +
        (i < nodes.length - 1 ? `<span class="paper-t0-spec-pipe-join" aria-hidden="true">›</span>` : "")
    )
    .join("");
}

export function renderExecutionRulesHtml(execution) {
  if (!execution || execution.ok === false) {
    const err = (execution && execution.error) || "无法加载 ExecutionSpec";
    return `<p class="quant-sub">${escapeText(err)}</p>`;
  }
  const t0 = execution.t0 || {};
  const coup = (execution.coupling && execution.coupling.t0_vs_stance) || "independent";
  const hash = execution.effective_hash || "";
  const { basePct } = adaptiveSizingBounds(t0);
  const rangeLbl =
    t0.min_range_pct != null && t0.min_range_pct !== ""
      ? fmtThresholdPct(t0.min_range_pct, null)
      : "自动";
  const fillLbl = FILL_MODE_LABELS[String(t0.fill_mode || "trigger").toLowerCase()] || t0.fill_mode || "—";
  const pathLbl =
    PATH_MODE_LABELS[String(t0.path_mode || "first_touch").toLowerCase()] || t0.path_mode || "—";
  const coupLbl = COUPLING_LABELS[String(coup).toLowerCase()] || coup;
  const triggerLbl = fmtTriggerPair(t0.sell_trigger_pct, t0.buy_trigger_pct);
  const coverLbl = t0.must_cover_same_day ? "收盘强制" : "y_on 策略";
  const atrLbl = t0.use_atr ? `开 · ${t0.atr_window ?? 14}日` : "关";

  const notes = (execution.notes || []).slice(0, 2);
  const hashShort = hash ? String(hash).slice(0, 10) : "";

  const headMeta = `dual_y · ${pathLbl} · ${fillLbl}`;

  return (
    `<div class="paper-t0-spec">` +
    `<header class="paper-t0-spec-head">` +
    `<div class="paper-t0-spec-head-main">` +
    `<h4 class="paper-t0-spec-head-title">生效规格</h4>` +
    `<p class="paper-t0-spec-head-meta">${escapeText(headMeta)}</p>` +
    `</div>` +
    `<div class="paper-t0-spec-kpi-strip" aria-label="核心参数">` +
    specKpi("策略", "dual_y", "多层 ŷ 门控") +
    specKpi("基准动仓", `${basePct}%`, adaptiveSizingRuleLabel(t0)) +
    specKpi("路径", pathLbl, "分钟触价路径") +
    specKpi("成交", fillLbl, "撮合假设") +
    `</div></header>` +
    `<section class="paper-t0-spec-block">` +
    `<h5 class="paper-t0-spec-block-title">决策链</h5>` +
    `<div class="paper-t0-spec-pipeline" aria-label="dual_y 决策链">` +
    buildDualYPipelineHtml(t0) +
    `</div></section>` +
    `<div class="paper-t0-spec-grid">` +
    `<section class="paper-t0-spec-panel is-direction">` +
    specPanelTitle("tau", "τ 方向映射") +
    buildTauMapVisualHtml(t0.y_tau_map, t0.y_tau_enter) +
    `<p class="paper-t0-spec-panel-foot">${escapeText(yTauMapRuleLabel(t0.y_tau_map))}</p>` +
    `</section>` +
    `<section class="paper-t0-spec-panel is-sizing">` +
    specPanelTitle("size", "动仓缩放") +
    buildSizingGaugeHtml(t0) +
    `<p class="paper-t0-spec-panel-foot">${escapeText(adaptiveSizingRuleLabel(t0))} · scale(ŷ_trade, ŷ_τ, eod)</p>` +
    `</section>` +
    `</div>` +
    `<section class="paper-t0-spec-block is-exec">` +
    `<h5 class="paper-t0-spec-block-title">执行参数</h5>` +
    `<div class="paper-t0-spec-metrics">` +
    specMetric("触发", triggerLbl, "卖高 / 买低阈值") +
    specMetric("振幅", rangeLbl, "日振幅下限") +
    specMetric("回补", coverLbl, "收盘强制或 y_on") +
    specMetric("ATR", atrLbl, "波动率自适应触发") +
    specMetric("stance", coupLbl, "与 stance 耦合") +
    `</div></section>` +
    (execution.summary || hashShort
      ? `<footer class="paper-t0-spec-foot">` +
        (execution.summary ? `<span class="paper-t0-spec-foot-summary">${escapeText(execution.summary)}</span>` : "") +
        (hashShort
          ? `<span class="paper-t0-rules-hash">hash ${escapeText(hashShort)}</span>`
          : "") +
        `</footer>`
      : "") +
    (notes.length
      ? `<ul class="paper-t0-rules-notes">${notes
          .map((n) => `<li>${escapeText(String(n))}</li>`)
          .join("")}</ul>`
      : "") +
    `</div>`
  );
}

/** 用生效 execution 填充表单控件。 */
export function fillExecutionForm(root, execution) {
  if (!root || !execution || !execution.t0) return;
  const t0 = execution.t0;
  const coup = (execution.coupling && execution.coupling.t0_vs_stance) || "independent";
  const set = (name, val) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || val == null) return;
    if (el.type === "checkbox") el.checked = !!val;
    else el.value = String(val);
  };
  set("enabled", t0.enabled !== false);
  set("t0_ratio", t0.t0_ratio != null ? Math.round(Number(t0.t0_ratio) * 100) : 40);
  set("sell_trigger_pct", t0.sell_trigger_pct);
  set("buy_trigger_pct", t0.buy_trigger_pct);
  if (t0.min_range_pct != null && t0.min_range_pct !== "") {
    set("min_range_pct", t0.min_range_pct);
  } else {
    set("min_range_pct", 0.5);
  }
  set("fill_mode", t0.fill_mode || "trigger");
  set("direction", "dual_y");
  set("y_tau_map", normalizeYTauMap(t0.y_tau_map));
  set("path_mode", t0.path_mode || "first_touch");
  set("use_atr", t0.use_atr !== false);
  set("must_cover_same_day", !!t0.must_cover_same_day);
  set("t0_vs_stance", coup);
  set("y_trade_floor", t0.y_trade_floor != null ? t0.y_trade_floor : 0.15);
  set("y_tau_enter", t0.y_tau_enter != null ? t0.y_tau_enter : 0.25);
  set("y_eod_prior", t0.y_eod_prior != null ? t0.y_eod_prior : 0.35);
  set("y_on_allow", t0.y_on_allow != null ? t0.y_on_allow : 1.2);
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
  const numOrNull = (name) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return null;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : null;
  };
  const str = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el && el.value !== "" ? el.value : fallback;
  };
  const chk = (name, fallback = true) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el ? !!el.checked : fallback;
  };
  const ratioPct = num("t0_ratio", 40);
  const t0 = {
    enabled: chk("enabled", true),
    t0_ratio: Math.max(0.05, Math.min(ratioPct / 100, 1)),
    sell_trigger_pct: num("sell_trigger_pct", 2),
    buy_trigger_pct: num("buy_trigger_pct", 1.5),
    fill_mode: str("fill_mode", "trigger"),
    direction: "dual_y",
    y_tau_map: normalizeYTauMap(str("y_tau_map", "trend")),
    path_mode: str("path_mode", "first_touch"),
    use_atr: chk("use_atr", true),
    must_cover_same_day: chk("must_cover_same_day", false),
    y_trade_floor: num("y_trade_floor", 0.15),
    y_tau_enter: Math.max(0.05, Math.min(num("y_tau_enter", 0.25), 5)),
    y_eod_prior: Math.max(0.05, Math.min(num("y_eod_prior", 0.35), 5)),
    y_on_allow: Math.max(0.1, Math.min(num("y_on_allow", 1.2), 10)),
  };
  const minRange = numOrNull("min_range_pct");
  if (minRange != null) t0.min_range_pct = Math.max(0.2, Math.min(minRange, 30));
  return {
    lock: true,
    t0,
    coupling: {
      t0_vs_stance: str("t0_vs_stance", "independent"),
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
  const lookbackEl = root && root.querySelector('[name="lookback"]');
  let lookback = 10;
  if (lookbackEl && lookbackEl.value !== "") {
    const n = Number(lookbackEl.value);
    if (Number.isFinite(n)) lookback = Math.max(10, Math.min(Math.round(n), 500));
  }
  const body = {
    from_paper: true,
    lookback,
    compare_optimistic: true,
    use_minute: true,
    compare_daily: false,
    t0_ratio: t0.t0_ratio != null ? t0.t0_ratio : 0.4,
    sell_trigger_pct: t0.sell_trigger_pct != null ? t0.sell_trigger_pct : 2,
    buy_trigger_pct: t0.buy_trigger_pct != null ? t0.buy_trigger_pct : 1.5,
    fill_mode: t0.fill_mode || "trigger",
    direction: "dual_y",
    y_tau_map: normalizeYTauMap(t0.y_tau_map),
    path_mode: "first_touch",
    must_cover_same_day: !!t0.must_cover_same_day,
    use_atr: t0.use_atr !== false,
    y_trade_floor: t0.y_trade_floor != null ? t0.y_trade_floor : 0.15,
    y_tau_enter: t0.y_tau_enter != null ? t0.y_tau_enter : 0.25,
    y_eod_prior: t0.y_eod_prior != null ? t0.y_eod_prior : 0.35,
    y_on_allow: t0.y_on_allow != null ? t0.y_on_allow : 1.2,
  };
  if (t0.min_range_pct != null) body.min_range_pct = t0.min_range_pct;
  else body.min_range_pct = 0.5;
  if (opts.onlySelected && opts.selectedCode) body.code = opts.selectedCode;
  return body;
}

export function renderExecutionDiffHtml(diff) {
  if (!diff || !diff.ok) {
    return `<p class="quant-sub">${escapeText((diff && diff.error) || "无 diff")}</p>`;
  }
  if (!diff.changed) {
    return `<p class="quant-sub">与策略默认一致 · hash ${escapeText(diff.paper_hash || "—")}</p>`;
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
